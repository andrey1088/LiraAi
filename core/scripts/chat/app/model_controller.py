import base64
import gc
import os
import sys
import traceback

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from infrastructure.lifecycle.perception_daemon import get_perception_daemon
from infrastructure.memory.repo import ChatRepository
from infrastructure.model_backends.loader import ModelLoader
from infrastructure.runtime.llm_cuda_hygiene import _empty_torch_cuda_cache


def _switch_log(msg: str) -> None:
    line = f"[ModelSwitch] {msg}"
    print(line, file=sys.stderr, flush=True)


class ModelController:
    def __init__(self, window):
        self.window = window
        self.loader = None
        self.llm = None
        self.model_icon_64 = None
        self.user_icon_64 = None
        self.current_icon_path = ""
        self._model_load_generation = 0
        self._switch_active = False
        self._switch_target_id: str | None = None
        self._handoff_reload_callback = None
        # Next gallery describe — subprocess (see on_model_ready), not in-process on GUI.
        self._gallery_describe_subprocess_guard = False

    def get_active_model_info(self):
        return self.window.config_repo.get_active_model_info()

    def _pump_events(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _hide_blocking_loader(self) -> None:
        self.window.browser.page().runJavaScript("if(window.liraApp) liraApp.view.hideBlockingLoader();")

    def _abort_switch(self, message: str) -> None:
        _switch_log(f"abort: {message}")
        self._switch_active = False
        self._hide_blocking_loader()
        self.window.inject_message("model", f"⚠️ Model switch: {message}")

    def _free_llm_on_gui_thread(self) -> None:
        """llama.cpp must be freed on the thread that created the context (GUI)."""
        from infrastructure.runtime.llm_cuda_hygiene import release_llm_cuda_cache

        llm = self.llm
        self.llm = None
        if llm is None:
            return
        _switch_log(f"free llm: {type(llm).__name__}")
        try:
            if hasattr(llm, "sd"):
                del llm.sd
        except Exception:
            pass
        release_llm_cuda_cache(llm, deep=True)
        try:
            if hasattr(llm, "close") and callable(llm.close):
                llm.close()
        except Exception:
            pass
        try:
            del llm
        except Exception:
            pass
        gc.collect()
        _empty_torch_cuda_cache()
        self._pump_events()

    def reload_after_gallery_handoff(self, when_ready) -> None:
        """Single LLM reload after batch gallery describe on GPU."""
        self._handoff_reload_callback = when_ready
        self._stop_loader_thread()
        self._model_load_generation += 1
        _switch_log("reload_after_gallery_handoff")
        self.init_model_brain()

    def _stop_loader_thread(self) -> None:
        loader = self.loader
        self.loader = None
        if loader is None:
            return
        try:
            loader.loaded.disconnect(self.on_model_ready)
        except (TypeError, RuntimeError):
            pass
        try:
            loader.load_failed.disconnect(self._on_model_load_failed)
        except (TypeError, RuntimeError):
            pass
        if loader.isRunning():
            _switch_log("stop ModelLoader thread")
            loader.requestInterruption()
            loader.terminate()
            loader.wait(3000)

    def init_model_brain(self):
        m_info = self.get_active_model_info()
        gen = self._model_load_generation
        self.loader = ModelLoader(m_info)
        self.loader._lira_load_generation = gen
        self.loader.loaded.connect(self.on_model_ready)
        self.loader.load_failed.connect(self._on_model_load_failed)
        _switch_log(f"start ModelLoader gen={gen} class={m_info.model_class}")
        self.loader.start()

    def prepare_icons(self):
        def to_b64_from_path(full_path):
            try:
                with open(full_path, "rb") as f:
                    return f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
            except Exception:
                return ""

        from infrastructure.paths import lira_data

        user_path = str(lira_data("icons", "user.png"))
        self.user_icon_64 = to_b64_from_path(user_path)
        self.model_icon_64 = to_b64_from_path(self.current_icon_path)

    def on_model_ready(self, llm):
        loader = self.loader
        if loader is None:
            _switch_log("on_model_ready: stale (loader is None)")
            return
        if getattr(loader, "_lira_load_generation", -1) != self._model_load_generation:
            _switch_log("on_model_ready: stale generation")
            return

        m_info = self.get_active_model_info()
        model_label = getattr(m_info, "name", None) or getattr(m_info, "model_type", None) or "?"
        _switch_log(f"on_model_ready: ok {model_label!r} (backend={type(llm).__name__})")

        if hasattr(llm, "finish_load_on_main_thread") and callable(llm.finish_load_on_main_thread):
            self.llm = llm
            QTimer.singleShot(0, self._deferred_finish_qwen_image_edit)
            return
        self.llm = llm
        self._arm_gallery_describe_subprocess_guard()
        self.window.on_model_ready()
        cb = self._handoff_reload_callback
        if cb is not None:
            self._handoff_reload_callback = None
            QTimer.singleShot(0, cb)

    def _on_model_load_failed(self, message: str) -> None:
        _switch_log(f"load_failed: {message}")
        handoff_cb = self._handoff_reload_callback
        if handoff_cb is not None:
            self._handoff_reload_callback = None
            self.window.chat_controller.on_gallery_handoff_reload_failed(message)
            return
        self._abort_switch(message)

    def _deferred_finish_qwen_image_edit(self) -> None:
        llm = self.llm
        try:
            if llm is not None and hasattr(llm, "finish_load_on_main_thread"):
                llm.finish_load_on_main_thread()
        except BaseException:
            try:
                from infrastructure.model_backends.image_qwen.diag_log import (
                    qwen_diag_append,
                )

                qwen_diag_append("model_controller._deferred_finish_qwen_image_edit:\n" + traceback.format_exc())
            except Exception:
                pass
            self._free_llm_on_gui_thread()
            self._abort_switch("Qwen Image Edit load failed")
            raise
        self._arm_gallery_describe_subprocess_guard()
        self.window.on_model_ready()

    def _arm_gallery_describe_subprocess_guard(self) -> None:
        m_info = self.get_active_model_info()
        cc = self.window.chat_controller
        if m_info is None or self.llm is None:
            return
        if not cc._model_has_vision(self, m_info):
            self._gallery_describe_subprocess_guard = False
            return
        self._gallery_describe_subprocess_guard = True
        _switch_log(f"gallery describe guard: next run subprocess+handoff ({m_info.name!r})")

    def clear_gallery_describe_subprocess_guard(self) -> None:
        self._gallery_describe_subprocess_guard = False

    def init_model_switch(self, model_id):
        current_id = str(self.window.config_repo.config.get("active_model_id"))
        if current_id == str(model_id):
            return
        if self._switch_active:
            self._switch_target_id = str(model_id)
            _switch_log(f"queue switch -> {model_id}")
            return

        self._switch_active = True
        self._switch_target_id = str(model_id)
        self._model_load_generation += 1
        _switch_log(f"begin switch {current_id} -> {model_id} gen={self._model_load_generation}")
        self.window.chat_controller.cancel_pending_qwen_edit()
        QTimer.singleShot(0, self._switch_step_wait_idle)

    def _switch_step_wait_idle(self) -> None:
        cc = self.window.chat_controller
        if cc._image_gen_busy() or cc._qwen_edit_main_busy:
            QTimer.singleShot(40, self._switch_step_wait_idle)
            return
        QTimer.singleShot(0, self._switch_step_unload_and_load)

    def _switch_step_unload_and_load(self) -> None:
        try:
            get_perception_daemon(self.window).stop()
            self._stop_loader_thread()
            self._free_llm_on_gui_thread()
            self._switch_step_configure()
        except Exception as e:
            self._abort_switch(str(e))

    def _switch_step_configure(self) -> None:
        model_id = self._switch_target_id
        if model_id is None:
            self._switch_active = False
            return

        self.window.config_repo.save_active_model(model_id)
        m_info = self.get_active_model_info()
        _switch_log(f"configure active={m_info.name} class={m_info.model_class}")

        if m_info.model_class in ("text-to-image", "image-edit"):
            from infrastructure.paths import lira_data

            db_path = str(lira_data("db", "gallery.db"))
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            self.window.repository = ChatRepository(db_path)
            self.window.repository._init_gallery_db()
        else:
            vc = self.window.voice_controller
            repo = self.window.config_repo
            loc = repo.get_ui_locale()
            vc.apply_ui_locale(loc)
            if loc == "ru":
                vc.voice.set_speaker(m_info.voice or "kseniya")
            vc.persist_tts_to_config(loc)
            try:
                new_db_path = self.window.config_repo.get_or_create_db_path()
                self.window.repository = ChatRepository(new_db_path)
            except Exception:
                pass

        sc = self.window.session_controller
        if m_info.model_class not in ("text-to-image", "image-edit"):
            sc.current_session_id = None
            sc.ensure_session()
            self.window.sync_limbic_from_db()
        else:
            sc.current_session_id = None
            sc.history = []

        self.init_model_brain()
        self._switch_active = False
        sv = getattr(self.window, "search_view", None)
        if sv is not None:
            sv.reset_for_model_switch()
        if getattr(self.window, "_surface_mode", "chat") == "search":
            self.window.show_chat_surface()

    def update_active_settings(self, new_settings):
        if hasattr(self, "llm_settings"):
            self.llm_settings.update(new_settings)

    def shutdown_for_close(self) -> None:
        self._stop_loader_thread()

    def unload_active_model(self):
        get_perception_daemon(self.window).stop()
        self.window.chat_controller.cancel_gallery_description_refresh()
        self.window.chat_controller.cancel_pending_qwen_edit()
        self.window.chat_controller.wait_image_gen_idle()
        self._stop_loader_thread()
        self._free_llm_on_gui_thread()
