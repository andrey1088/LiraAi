import json
import os
import threading
from pathlib import Path

from app.chat_controller import ChatController
from app.model_controller import ModelController
from app.session_controller import SessionController
from app.stt_controller import SttController
from app.voice_controller import VoiceController
from maintenance import sleep_mode
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QSizeGrip,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from ui.bridge import LiraBridge
from ui.search_proxy import install_search_proxy_router
from ui.search_view import SearchView

# Internal imports (paths via sys.path in gui.py)
from infrastructure.config.repo import ConfigRepository
from infrastructure.paths import config_path, lira_data, resolve_path
from infrastructure.lifecycle.activity_gate import UserActivityGate
from infrastructure.lifecycle.perception_daemon import get_perception_daemon
from infrastructure.locale.i18n import tr
from infrastructure.memory.repo import ChatRepository
from infrastructure.runtime.service_manager import ServiceManager

# Project root relative to this file
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent


class LiraWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config_repo = ConfigRepository(str(config_path()))
        self.service_manager = ServiceManager(BASE_DIR)
        self.model_controller = ModelController(self)
        self.session_controller = SessionController(self)
        self.voice_controller = VoiceController(self)
        self.stt_controller = SttController(self)
        self.chat_controller = ChatController(self)
        self.activity_gate = UserActivityGate()
        self._web_service_warmup_started = False
        self._stt_bootstrap_running = False
        self._shutdown_in_progress = False
        self._shutdown_complete = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Lira 2.0")
        self.setMinimumSize(1400, 900)
        self.drag_pos = None

        self._search_proxy_router = install_search_proxy_router(self.service_manager)
        self.chat_browser = self.init_browser()
        self.browser = self.chat_browser
        self.search_view = SearchView(self)
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self.chat_browser)
        self._content_stack.addWidget(self.search_view)
        self._surface_mode = "chat"
        self.pending_switch = None
        self.pending_switch_kind = None

        self.central_widget = QWidget()
        self.central_widget.setObjectName("centralWidget")
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.sizegrip = QSizeGrip(self.central_widget)
        self.layout.addWidget(self._content_stack, stretch=1)
        self.layout.setContentsMargins(2, 2, 2, 16)
        self.layout.setSpacing(0)

        self.bridge = LiraBridge(self)
        self.channel = QWebChannel()
        self.channel.registerObject("backend", self.bridge)
        self.browser.page().setWebChannel(self.channel)
        self.browser.setObjectName("browser")

        self.header = QWidget()
        self.header.setObjectName("headerWidget")
        self.header.setFixedHeight(30)
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(10, 0, 10, 0)
        self.header_layout.addStretch()

        self.max_btn = QPushButton("▢")  # maximize icon
        self.max_btn.setFixedSize(30, 30)
        self.max_btn.clicked.connect(self.toggle_maximize)
        self.max_btn.setObjectName("maxBtn")
        self.header_layout.addWidget(self.max_btn)

        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.clicked.connect(self.close)
        self.close_btn.setObjectName("closeBtn")
        self.header_layout.addWidget(self.close_btn)

        self.layout.insertWidget(0, self.header)

        self.sizegrip.setParent(self.central_widget)
        self.sizegrip.setFixedSize(16, 16)
        self.sizegrip.raise_()

        self.bridge.imageSelected.connect(self.handle_image_selected)
        self.bridge.gallery_data_signal.connect(self.handle_gallery_ui_update)
        self.bridge.gallery_describe_event.connect(self._on_gallery_describe_event)

        m_info = self.config_repo.get_active_model_info()
        db_path = self.config_repo.get_or_create_db_path()
        self.repository = ChatRepository(db_path)
        self.gallery_repo = ChatRepository(str(lira_data("db", "gallery.db")))

        sp = self.config_repo.get_tts_speaker_for_locale(m_info, self.config_repo.get_ui_locale())
        self.voice_controller.set_speaker(sp)
        self.model_controller.current_icon_path = resolve_path(m_info.icon_path or str(lira_data("icons", "lira.png")))

        with open(BASE_DIR / "core/styles/chat.qss", "r") as f:
            self.setStyleSheet(f.read())

        self.model_controller.prepare_icons()
        self.sync_limbic_from_db()

        # Defer heavy work via timer so window shows instantly
        QTimer.singleShot(100, self.delayed_init)

    def _apply_normal_chrome(self) -> None:
        self.max_btn.setText("▢")
        self.central_widget.setStyleSheet("#centralWidget { border-radius: 10px; }")

    def _apply_maximized_chrome(self) -> None:
        self.max_btn.setText("❐")
        self.central_widget.setStyleSheet("#centralWidget { border-radius: 0px; }")

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self._apply_normal_chrome()
        else:
            self.showMaximized()
            self._apply_maximized_chrome()

    def delayed_init(self):
        self.chat_controller.semantic_engine.warmup()
        sleep_mode(self.config_repo, self.chat_controller.semantic_engine)
        self.start_stt_bootstrap_if_needed()
        # Then start model load
        self.init_model_brain()

    def start_stt_bootstrap_if_needed(self) -> None:
        from infrastructure.stt.bootstrap import stt_bootstrap_needed

        m_info = self.config_repo.get_active_model_info()
        if self._stt_bootstrap_running or not stt_bootstrap_needed(
            self.config_repo.get_ui_locale(),
            model_class=m_info.model_class,
        ):
            return
        self._stt_bootstrap_running = True
        threading.Thread(
            target=self._bootstrap_stt_background,
            name="stt-bootstrap",
            daemon=True,
        ).start()

    def _bootstrap_stt_background(self) -> None:
        try:
            from infrastructure.stt.bootstrap import ensure_stt_ready

            if ensure_stt_ready():
                QTimer.singleShot(0, self._notify_stt_ui_ready)
        finally:
            self._stt_bootstrap_running = False

    def _notify_stt_ui_ready(self) -> None:
        self.browser.page().runJavaScript(
            "if(window.liraApp) liraApp.refreshSttEnabled().then(() => liraApp.syncSttButton());"
        )

    def init_model_brain(self):
        self.model_controller.init_model_brain()

    def on_model_ready(self):
        m_info = self.config_repo.get_active_model_info()
        if m_info.model_class in ("text-to-image", "image-edit"):
            self.browser.page().runJavaScript(
                """
                    if(window.liraApp) {
                        liraApp.updateUI().then(() => {
                            liraApp.view.hideBlockingLoader(); liraApp.view.createImageEditor();
                        });
                    }
                """
            )
            from infrastructure.locale.i18n import tr

            loc = self.config_repo.get_ui_locale()
            welcome = (
                tr(
                    "Qwen Image Edit ready: main photo required, second optional for composition; then describe the edit.",
                    loc,
                )
                + " 🖼️"
                if m_info.model_class == "image-edit"
                else tr("Artist mode ready 🎨", loc)
            )
            self.browser.page().runJavaScript(
                f"if(window.liraApp) liraApp.view.renderSystemMessage({json.dumps(welcome)});"
            )
            return

        sc = self.session_controller
        last_id = self.repository.get_last_session_id()
        sc.current_session_id = last_id
        sc.history = self.repository.get_session_messages(last_id) if last_id else []
        self.sync_limbic_from_db()
        get_perception_daemon(self).start()

        sp = self.config_repo.get_tts_speaker_for_locale(m_info, self.config_repo.get_ui_locale())
        self.voice_controller.set_speaker(sp)
        self.model_controller.current_icon_path = os.path.expanduser(m_info.icon_path or "")
        self.model_controller.prepare_icons()

        target_id = last_id if last_id else "null"

        script = f"""
                if(window.liraApp) {{
                    liraApp.updateUI().then(() => {{
                        liraApp.view.hideBlockingLoader();
                        if ({target_id} !== null) {{
                            liraApp.loadChatSession({target_id});
                        }} else {{
                            document.getElementById('chat').innerHTML = '';
                        }}
                        liraApp.checkGalleryDescriptionsPending();
                    }});
                }}
            """
        self.browser.page().runJavaScript(script)

        # Warm searxng once after first text model load.
        # In a background thread to avoid chat latency.
        if not self._web_service_warmup_started:
            self._web_service_warmup_started = True
            threading.Thread(
                target=self.ensure_web_search_service,
                name="searxng-warmup",
                daemon=True,
            ).start()

    def _finish_pending_switch(self):
        if self.pending_switch_kind == "chat":
            self.session_controller.finish_pending_chat_switch()
        elif self.pending_switch_kind == "model":
            model_id = self.pending_switch["model_id"]
            self.init_model_switch(model_id)
        self.pending_switch = None
        self.pending_switch_kind = None

    def finalize_answer(self, full_response):
        self.chat_controller.finalize_answer(full_response)

    def interrupt_voice(self):
        self.voice_controller.stop_all_audio("window_interrupt")

    def reset_voice_for_model(self, speaker_name):
        self.voice_controller.reset_voice_for_model(speaker_name)

    def update_chat(self, token):
        self.voice_controller.update_chat(token)

    def _limbic_applies_to_active_model(self) -> bool:
        from infrastructure.limbic.assets import model_limbic_enabled

        m_info = self.config_repo.get_active_model_info()
        if m_info.model_class in ("text-to-image", "image-edit"):
            return False
        return model_limbic_enabled(m_info)

    def sync_limbic_from_db(self) -> None:
        """One mood per model (DB file), not per chat."""
        if not self._limbic_applies_to_active_model():
            return
        cc = self.chat_controller
        if not hasattr(cc, "limbic_state"):
            return
        saved = self.repository.get_limbic_state()
        if saved:
            cc.limbic_state.load_snapshot(saved)
        else:
            cc.limbic_state.reset()
            self.repository.save_limbic_state(cc.limbic_state.snapshot())
        self.notify_limbic_emotion()

    def sync_limbic_to_db(self) -> None:
        if not self._limbic_applies_to_active_model():
            return
        cc = self.chat_controller
        if not hasattr(cc, "limbic_state"):
            return
        self.repository.save_limbic_state(cc.limbic_state.snapshot())

    def notify_limbic_emotion(self) -> None:
        if not self._limbic_applies_to_active_model():
            return
        cc = self.chat_controller
        if not hasattr(cc, "limbic_state"):
            return
        from infrastructure.limbic.assets import limbic_images_base_url

        m_info = self.config_repo.get_active_model_info()
        base_url = limbic_images_base_url(m_info)
        if not base_url:
            return
        emotion = cc.limbic_state.top_label()
        payload = json.dumps({"emotion": emotion, "baseUrl": base_url})
        script = f"if(window.liraApp) window.liraApp.setLimbicEmotion({payload});"
        self.browser.page().runJavaScript(script)

    def request_chat_switch(self, session_id):
        return self.session_controller.request_chat_switch(session_id)

    def ensure_web_search_service(self, route_mode="default"):
        return self.service_manager.ensure_searxng_running(route_mode=route_mode)

    def get_web_search_url(self, route_mode="default"):
        return self.service_manager.get_searxng_url(route_mode=route_mode)

    def get_proxy_url(self, route_mode="default"):
        return self.service_manager.get_proxy_url(route_mode=route_mode)

    def search_route_mode(self) -> str:
        if self.service_manager.get_proxy_url("ru"):
            return "ru"
        return "default"

    def show_chat_surface(self) -> None:
        if self._surface_mode == "chat":
            return
        self._surface_mode = "chat"
        self._search_proxy_router.set_active(False)
        self._content_stack.setCurrentWidget(self.chat_browser)
        self.chat_browser.page().runJavaScript("if(window.liraApp) liraApp.onLeftSearchMode();")

    def show_search_mode(self) -> None:
        if self._surface_mode == "search":
            self.search_view.ensure_visible()
            return
        self._surface_mode = "search"
        self._search_proxy_router.set_active(True)
        self._content_stack.setCurrentWidget(self.search_view)
        self.search_view.ensure_visible()

    def get_all_generations(self, model_filter="all", sort_order="DESC"):
        data = self.gallery_repo.get_all_generations(model_filter, sort_order)
        return json.dumps(data)

    def delete_generation_entry(self, gen_id):
        return self.gallery_repo.delete_generation_entry(gen_id)

    def handle_image_selected(self, base64_data):
        # Forward to controller
        self.chat_controller.handle_pending_image(base64_data)

    def request_model_switch(self, model_id):
        from infrastructure.locale.i18n import tr

        self._show_blocking_loader(tr("Switching model…", self.config_repo.get_ui_locale()))
        QTimer.singleShot(0, lambda: self._request_model_switch_continue(model_id))

    def _request_model_switch_continue(self, model_id) -> None:
        self.interrupt_voice()
        cc = self.chat_controller
        worker_running = cc.worker is not None and cc.worker.isRunning()
        if not worker_running:
            self.pending_switch_kind = "model"
            self.pending_switch = {"model_id": str(model_id)}
            self._finish_pending_switch()
            return

        self.pending_switch_kind = "model"
        self.pending_switch = {"model_id": str(model_id)}

    def _show_blocking_loader(self, text: str) -> None:
        payload = json.dumps(text, ensure_ascii=False)
        self._run_blocking_loader_js(f"if(window.liraApp) liraApp.view.showBlockingLoader({payload});")
        self._pump_ui()

    def init_browser(self):
        browser = QWebEngineView()
        browser.page().setBackgroundColor(Qt.GlobalColor.transparent)
        # Allow text selection and copy context menu
        browser.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        web_dir = BASE_DIR / "core" / "web"
        html_path = web_dir / "chat.html"
        browser.setUrl(QUrl.fromLocalFile(str(html_path.absolute())))
        return browser

    def inject_message(self, role, text, images=None, *, is_stream=False):
        import json

        icon = self.model_controller.user_icon_64 if role == "user" else self.model_controller.model_icon_64

        # Ensure images is a list for the frontend
        if isinstance(images, str):
            images = [images]
        elif images is None:
            images = []

        payload = json.dumps(
            {
                "role": role,
                "text": text,
                "icon": icon,
                "images": images,
                "isStream": is_stream,
            },
            ensure_ascii=False,
        )

        script = f"if(window.liraApp) window.liraApp.addMessageFromJson({payload});"
        self.browser.page().runJavaScript(script)

    def show_thinking_indicator(self, text: str):
        t = json.dumps(text, ensure_ascii=False)
        self.browser.page().runJavaScript(f"if(window.liraApp) window.liraApp.showThinkingIndicator({t});")
        self._pump_ui()

    def hide_thinking_indicator(self):
        self.browser.page().runJavaScript("if(window.liraApp) window.liraApp.hideThinkingIndicator();")

    def display_art_on_canvas(self, url, prompt):
        safe_prompt = prompt.replace("`", "\\`").replace("'", "\\'")

        self.browser.page().runJavaScript(f"liraApp.renderArtCanvas('{url}', `{safe_prompt}`)")

    def init_model_switch(self, model_id):
        self.model_controller.init_model_switch(model_id)

    def mousePressEvent(self, event):
        # Drag only via header
        if event.button() == Qt.MouseButton.LeftButton and self.header.underMouse():
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Move only when header drag started
        if self.drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sizegrip.move(self.width() - 22, self.height() - 22)

    def _pump_ui(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _run_blocking_loader_js(self, script: str) -> None:
        self.browser.page().runJavaScript(script)

    def _show_shutdown_loader(self, text: str) -> None:
        payload = json.dumps(text, ensure_ascii=False)
        self._run_blocking_loader_js(f"if(window.liraApp) liraApp.view.showBlockingLoader({payload});")

    def _update_shutdown_loader(self, text: str) -> None:
        payload = json.dumps(text, ensure_ascii=False)
        self._run_blocking_loader_js(f"if(window.liraApp) liraApp.view.updateBlockingLoader({payload});")

    def _begin_graceful_shutdown(self) -> None:
        loc = self.config_repo.get_ui_locale()
        self._show_shutdown_loader(tr("Closing application…", loc))
        self._pump_ui()
        QTimer.singleShot(80, self._execute_shutdown_sequence)

    def _execute_shutdown_sequence(self) -> None:
        loc = self.config_repo.get_ui_locale()
        try:
            self._update_shutdown_loader(tr("Stopping chat tasks…", loc))
            self._pump_ui()
            self.chat_controller.shutdown_for_close()
            self.model_controller.shutdown_for_close()

            self._update_shutdown_loader(tr("Stopping perception…", loc))
            self._pump_ui()
            get_perception_daemon(self).stop()

            self._update_shutdown_loader(tr("Stopping speech…", loc))
            self._pump_ui()
            self.voice_controller.stop_all_audio("app_close")
            self.stt_controller.shutdown()

            self._update_shutdown_loader(tr("Stopping services…", loc))
            self._pump_ui()
            self.service_manager.shutdown_managed_services()
            self._pump_ui()
        finally:
            self._shutdown_complete = True
            self._shutdown_in_progress = False
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._shutdown_complete:
            super().closeEvent(event)
            return
        if self._shutdown_in_progress:
            event.ignore()
            return
        event.ignore()
        self._shutdown_in_progress = True
        self._begin_graceful_shutdown()

    def _on_gallery_describe_event(self, payload: str) -> None:
        import json

        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False)
        self.browser.page().runJavaScript(f"if(window.liraApp) window.liraApp.onGalleryDescribeEvent({payload});")

    def handle_gallery_ui_update(self, data):
        # 1. Clean JSON string for JS
        if isinstance(data, dict):
            json_payload = json.dumps(data, ensure_ascii=False)
        else:
            json_payload = data  # already a string from the signal

        # 2. Call JS with the whole object as one string
        # Wrap JSON in single quotes,
        # json.dumps escapes the rest.
        script = f"if(window.liraApp) liraApp.renderGallerySearch({json_payload});"

        self.browser.page().runJavaScript(script)
        pass
