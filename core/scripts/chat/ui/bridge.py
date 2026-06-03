import base64
import copy
import json
import os

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication


class LiraBridge(QObject):
    imageSelected = pyqtSignal(str)
    gallery_data_signal = pyqtSignal(str)
    gallery_describe_event = pyqtSignal(str)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.web_ready = False

    @staticmethod
    def _model_supports_gallery_describe(m_info) -> bool:
        from infrastructure.model_tasks.gallery.capabilities import (
            model_supports_gallery_describe,
        )

        return model_supports_gallery_describe(m_info)

    @pyqtSlot()
    def on_web_ready(self):
        self.web_ready = True

    @pyqtSlot()
    def enterSearchMode(self):
        self.window.show_search_mode()

    @pyqtSlot()
    def leaveSearchMode(self):
        self.window.show_chat_surface()

    @pyqtSlot(result=str)
    def openDownloadsFolder(self):
        from ui.search_downloads import DOWNLOADS_DIR, open_downloads_folder_in_os

        ok, path = open_downloads_folder_in_os()
        return json.dumps(
            {"ok": bool(ok), "path": path, "dir": DOWNLOADS_DIR},
            ensure_ascii=False,
        )

    @pyqtSlot(result=str)
    def get_active_model_icon(self):
        return "file://" + self.window.model_controller.current_icon_path

    @pyqtSlot(bool)
    def set_user_typing(self, active: bool):
        self.window.activity_gate.set_user_typing(active)

    @pyqtSlot(str, result=int)
    def sendMessage(self, text):
        session_id = self.window.session_controller.ensure_session()
        self.window.activity_gate.touch_user_message()
        self.window.activity_gate.set_user_typing(False)
        self.window.chat_controller.process_web_message(text)
        # image-edit / text-to-image: ensure_session() may not create chat → None breaks WebChannel (result=int).
        return int(session_id) if session_id is not None else 0

    @pyqtSlot()
    def saveExperience(self):
        self.window.chat_controller.save_experience()

    @pyqtSlot()
    def markMemoryVerified(self):
        self.window.chat_controller.mark_last_interaction_for_memory()

    @pyqtSlot(result=str)
    def get_active_model_info(self):
        from infrastructure.limbic.assets import (
            limbic_images_base_url,
            model_limbic_enabled,
        )

        m_info = self.window.config_repo.get_active_model_info()
        limbic_emotion = None
        limbic_base_url = None
        cc = getattr(self.window, "chat_controller", None)
        if model_limbic_enabled(m_info) and cc is not None and hasattr(cc, "limbic_state"):
            limbic_emotion = cc.limbic_state.top_label()
            limbic_base_url = limbic_images_base_url(m_info)

        has_gallery_vision = self._model_supports_gallery_describe(m_info)

        return json.dumps(
            {
                "id": m_info.id,
                "name": m_info.name,
                "model_class": m_info.model_class,
                "icon": self.window.model_controller.model_icon_64,
                "user_icon": self.window.model_controller.user_icon_64,
                "model_type": m_info.model_type,
                "clip_model_path": getattr(m_info, "clip_model_path", None) or "",
                "current_session_id": self.window.session_controller.current_session_id,
                "settings": m_info.settings or {},
                "limbic_dominant_emotion": limbic_emotion,
                "limbic_images_base_url": limbic_base_url,
                "has_gallery_vision": has_gallery_vision,
            }
        )

    @pyqtSlot(result=str)
    def get_ui_locale(self):
        return json.dumps({"locale": self.window.config_repo.get_ui_locale()})

    @pyqtSlot(str, result=str)
    def set_ui_locale(self, locale: str):
        repo = self.window.config_repo
        m_info = repo.get_active_model_info()
        if m_info.model_class not in ("text-to-image", "image-edit"):
            self.window.voice_controller.apply_ui_locale(locale)
            self.window.voice_controller.persist_tts_to_config(locale)
            loc = repo.get_ui_locale()
            cc = getattr(self.window, "chat_controller", None)
            if cc and hasattr(cc, "rebuild_tools_for_locale"):
                cc.rebuild_tools_for_locale(loc)
        else:
            loc = repo.save_ui_locale(locale)
        return json.dumps({"ok": True, "locale": loc, "tts": repo.get_tts_block()})

    @pyqtSlot(result=str)
    def get_full_config(self):
        config_to_send = copy.deepcopy(self.window.config_repo.config)

        def to_b64(path):
            full_path = os.path.expanduser(path)
            try:
                with open(full_path, "rb") as f:
                    return f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
            except OSError:
                # Icon file missing or unreadable — return empty string instead of crashing.
                return ""

        for model in config_to_send.get("models", []):
            if "icon_path" in model:
                model["icon_base64"] = to_b64(model["icon_path"])

        config_to_send["user_icon_base_64"] = self.window.model_controller.user_icon_64
        config_to_send["active_session_id"] = self.window.session_controller.current_session_id

        return json.dumps(config_to_send)

    @pyqtSlot(str)
    def switch_model(self, model_id):
        self.window.request_model_switch(model_id)

    @pyqtSlot(str, str)
    def update_model_settings(self, model_id, settings_json):
        try:
            new_settings = json.loads(settings_json)

            # --- TYPE COERCION ---
            # Volume always float
            if "volume" in new_settings:
                new_settings["volume"] = float(new_settings["volume"])
                self.window.voice_controller.set_volume(new_settings["volume"])

            # LLM parameters
            if "temperature" in new_settings:
                new_settings["temperature"] = float(new_settings["temperature"])
            if "top_p" in new_settings:
                new_settings["top_p"] = float(new_settings["top_p"])

            # Artist (SD) parameters
            if "steps" in new_settings:
                new_settings["steps"] = int(new_settings["steps"])
            if "cfg_scale" in new_settings:
                new_settings["cfg_scale"] = float(new_settings["cfg_scale"])
            if "width" in new_settings:
                new_settings["width"] = int(new_settings["width"])
            if "height" in new_settings:
                new_settings["height"] = int(new_settings["height"])
            if "true_cfg_scale" in new_settings:
                new_settings["true_cfg_scale"] = float(new_settings["true_cfg_scale"])
            if "guidance_scale" in new_settings:
                new_settings["guidance_scale"] = float(new_settings["guidance_scale"])
            if "n_gpu_layers" in new_settings:
                new_settings["n_gpu_layers"] = int(new_settings["n_gpu_layers"])
            if "context_budget_tokens" in new_settings:
                new_settings["context_budget_tokens"] = int(new_settings["context_budget_tokens"])
            if "context_reserve_tokens" in new_settings:
                new_settings["context_reserve_tokens"] = int(new_settings["context_reserve_tokens"])
            if "context_template_slack_tokens" in new_settings:
                new_settings["context_template_slack_tokens"] = int(new_settings["context_template_slack_tokens"])
            if "text_encoder_gpu" in new_settings:
                v = new_settings["text_encoder_gpu"]
                new_settings["text_encoder_gpu"] = v in (True, "true", "1", "on", 1)

            self.window.config_repo.update_settings(model_id, new_settings)
            self.window.model_controller.update_active_settings(new_settings)

            pass
        except Exception:
            pass

    @pyqtSlot(result=str)
    def get_chat_history_list(self):
        import json

        sessions = self.window.repository.get_all_sessions()
        # Convert each ChatSession to dict
        return json.dumps([s.to_dict() for s in sessions])

    @pyqtSlot(int, result=str)
    def load_session(self, session_id):
        import json

        result = self.window.request_chat_switch(session_id)
        pass

        if isinstance(result, dict) and "messages" in result:
            processed_messages = []
            for m in result["messages"]:
                # Convert Message to dict
                msg_dict = m.to_dict() if hasattr(m, "to_dict") else m

                image_val = msg_dict.get("image_url")

                if image_val:
                    # CASE 1: list of paths
                    if isinstance(image_val, list):
                        processed_urls = []
                        for path in image_val:
                            if (
                                isinstance(path, str)
                                and not path.startswith("data:")
                                and not path.startswith("file://")
                            ):
                                processed_urls.append("file://" + path)
                            else:
                                processed_urls.append(path)
                        msg_dict["image_url"] = processed_urls

                    # CASE 2: single path string
                    elif (
                        isinstance(image_val, str)
                        and not image_val.startswith("data:")
                        and not image_val.startswith("file://")
                    ):
                        msg_dict["image_url"] = "file://" + image_val

                processed_messages.append(msg_dict)

            result["messages"] = processed_messages

        return json.dumps(result)

    @pyqtSlot(result=int)  # returns int
    def create_new_session(self):
        # Delegate session creation to controller
        return self.window.session_controller.create_new_session()

    @pyqtSlot(int)
    def delete_chat(self, session_id):
        self.window.interrupt_voice()
        # 1. Active model info for folder name
        m_info = self.window.config_repo.get_active_model_info()

        # 2. Pass session id and model info to delete media folder
        self.window.repository.delete_session(session_id=session_id, model_type=m_info.model_type, model_id=m_info.id)

        pass

        if self.window.session_controller.current_session_id == session_id:
            pass
            self.create_new_session()

    @pyqtSlot(int, str)
    def rename_chat(self, session_id, title):
        self.window.repository.rename_session(session_id, title)

    @pyqtSlot(float)
    def set_volume(self, val):
        # 1. Apply immediately to voice controller
        self.window.voice_controller.set_volume(val)

        # 2. Persist in config for reload
        m_info = self.window.config_repo.get_active_model_info()
        self.window.config_repo.update_settings(m_info.id, {"volume": val})
        pass

    @pyqtSlot(result=bool)
    def toggle_mute(self):
        """Toggle mute and return new state for icon"""
        return self.window.voice_controller.toggle_mute()

    @pyqtSlot(float)
    def apply_volume_live(self, val):
        self.window.voice_controller.set_volume(val)

    @pyqtSlot(int, result=str)
    def open_camera_for_attachment(self, session_id: int = 0) -> str:
        """Modal camera capture → pending_attachments; JSON {id, preview}."""
        m_info = self.window.config_repo.get_active_model_info()
        if getattr(m_info, "model_class", None) in ("text-to-image", "image-edit"):
            return ""
        if session_id and int(session_id) > 0:
            self.window.session_controller.current_session_id = int(session_id)
        return self.window.chat_controller.capture_camera_for_user_attachment()

    @pyqtSlot(str, result=str)
    def register_image_attachment(self, base64_data: str) -> str:
        if not base64_data:
            print("[Attachment] bridge register_image_attachment: empty input", flush=True)
            return ""
        result = self.window.chat_controller.register_image_attachment(base64_data)
        if not result:
            print("[Attachment] bridge register_image_attachment: no result", flush=True)
        return json.dumps(result, ensure_ascii=False) if result else ""

    @pyqtSlot(str, result=str)
    def register_image_attachment_from_path(self, file_path: str) -> str:
        if not file_path:
            print("[Attachment] bridge register_image_attachment_from_path: empty path", flush=True)
            return ""
        result = self.window.chat_controller.register_image_attachment_from_path(file_path)
        if result.get("error"):
            print(f"[Attachment] bridge gallery error: {result['error']}", flush=True)
        return json.dumps(result, ensure_ascii=False) if result else ""

    @pyqtSlot(str, str, result=str)
    def register_document_attachment(self, filename: str, file_b64: str) -> str:
        if not file_b64:
            return ""
        result = self.window.chat_controller.register_document_attachment(filename, file_b64)
        return json.dumps(result, ensure_ascii=False)

    @pyqtSlot(str, int, result=str)
    def begin_document_upload(self, filename: str, total_size: int) -> str:
        return self.window.chat_controller.begin_document_upload(filename, total_size)

    @pyqtSlot(str, str)
    def document_upload_chunk(self, upload_id: str, chunk_b64: str) -> None:
        self.window.chat_controller.document_upload_chunk(upload_id, chunk_b64)

    @pyqtSlot(str, result=str)
    def finish_document_upload(self, upload_id: str) -> str:
        result = self.window.chat_controller.finish_document_upload(upload_id)
        return json.dumps(result, ensure_ascii=False)

    @pyqtSlot(result=str)
    def get_pending_attachments_preview(self) -> str:
        items = self.window.chat_controller.pending_attachments_for_ui()
        return json.dumps(items, ensure_ascii=False)

    @pyqtSlot(str, result=bool)
    def remove_pending_attachment(self, attachment_id: str) -> bool:
        if not attachment_id:
            return False
        return self.window.chat_controller.remove_pending_attachment(attachment_id)

    @pyqtSlot()
    def clear_pending_attachments(self) -> None:
        self.window.chat_controller.clear_pending_attachments()

    @pyqtSlot(str)
    def onImageSelected(self, base64_data):
        """Compatibility: single call = register_image_attachment."""
        self.register_image_attachment(base64_data)

    @pyqtSlot(str)
    def onImageEditPrimarySelected(self, base64_data: str):
        if not base64_data:
            return
        file_path, clean_b64 = self.window.chat_controller.process_incoming_image(base64_data)
        if file_path and clean_b64:
            self.window.chat_controller.set_image_edit_primary(clean_b64, file_path)

    @pyqtSlot(str)
    def onImageEditSecondarySelected(self, base64_data: str):
        if not base64_data:
            return
        file_path, clean_b64 = self.window.chat_controller.process_incoming_image(base64_data)
        if file_path and clean_b64:
            self.window.chat_controller.set_image_edit_secondary(clean_b64, file_path)

    @pyqtSlot()
    def clearImageEditPrimarySlot(self) -> None:
        self.window.chat_controller.clear_image_edit_primary()

    @pyqtSlot()
    def clearImageEditSecondarySlot(self) -> None:
        self.window.chat_controller.clear_image_edit_secondary()

    @pyqtSlot(str, str, str)
    def process_image_request(self, prompt, negative, ratio):
        """Handle generation request from JS."""
        # Forward to ChatController
        self.window.chat_controller.process_image_generation(prompt=prompt, negative=negative, ratio=ratio)

    @pyqtSlot(str, str, result=str)  # filter and sort
    def get_all_generations(self, model_filter, sort_order):
        try:
            return self.window.get_all_generations(model_filter, sort_order)
        except Exception:
            pass
            return "[]"

    @pyqtSlot(str, result=str)  # accepts str
    def delete_generation_entry(self, image_id_str):
        try:
            image_id = int(image_id_str)
            success = self.window.delete_generation_entry(image_id)

            import json

            return json.dumps({"status": "success" if success else "error"})
        except (ValueError, TypeError):
            pass

    @pyqtSlot(str)
    def copy_to_clipboard(self, text):
        try:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            pass
        except Exception:
            pass

    @pyqtSlot(result=str)
    def get_lora_list(self):
        try:
            # Path from config or temporary hardcode
            from infrastructure.paths import lira_data

            lora_dir = str(lira_data("models", "lora"))
            if not os.path.exists(lora_dir):
                return "[]"

            # Collect .safetensors files (basename for display)
            loras = [f.replace(".safetensors", "") for f in os.listdir(lora_dir) if f.endswith(".safetensors")]
            return json.dumps(sorted(loras))
        except Exception:
            pass
            return "[]"

    @pyqtSlot(str)
    def send_gallery_to_ui(self, data):
        # Must be self (signal on Bridge)
        self.gallery_data_signal.emit(data)

    @pyqtSlot(result=str)
    def get_sidebar_model_tasks(self):
        from infrastructure.model_tasks.registry import sidebar_tasks_for_ui

        m_info = self.window.config_repo.get_active_model_info()
        loc = self.window.config_repo.get_ui_locale()
        return json.dumps(sidebar_tasks_for_ui(m_info, loc), ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def run_sidebar_model_task(self, task_id: str):
        from infrastructure.locale.i18n import tr
        from infrastructure.model_tasks.registry import (
            task_by_id,
            task_params_dict,
            tasks_for_model,
        )

        m_info = self.window.config_repo.get_active_model_info()
        loc = self.window.config_repo.get_ui_locale()
        task = task_by_id(task_id)
        if not task or task not in tasks_for_model(m_info):
            return json.dumps(
                {
                    "ok": False,
                    "error": tr("Task is not available for this model", loc),
                },
                ensure_ascii=False,
            )
        if task.bridge_method == "start_gallery_description_refresh":
            mode = task_params_dict(task).get("mode", "missing")
            return self.start_gallery_description_refresh(mode)
        return json.dumps(
            {"ok": False, "error": tr("Not implemented", loc)},
            ensure_ascii=False,
        )

    @pyqtSlot(str, result=str)
    def start_gallery_description_refresh(self, mode="missing"):
        mode_key = (mode or "missing").strip().lower()
        QTimer.singleShot(0, lambda: self._start_gallery_description_refresh_impl(mode_key))
        return json.dumps({"ok": True, "accepted": True}, ensure_ascii=False)

    def _start_gallery_description_refresh_impl(self, mode: str) -> None:
        cc = self.window.chat_controller
        try:
            res = cc.start_gallery_description_refresh(mode)
        except Exception as e:
            res = {"ok": False, "error": str(e)}
        if res.get("ok") and int(res.get("total") or 0) > 0:
            return
        from infrastructure.locale.i18n import tr

        loc = self.window.config_repo.get_ui_locale()
        msg = res.get("error") or res.get("message") or tr("Failed to start description refresh", loc)
        cc._emit_gallery_describe_event({"type": "rejected", "error": msg, "message": msg, "mode": mode})

    @pyqtSlot(result=str)
    def count_gallery_description_repair(self):
        try:
            repo = self.window.gallery_repo
            return json.dumps(
                {
                    "missing": repo.count_generations_missing_description(),
                    "bad": repo.count_generations_bad_description(),
                    "repair": repo.count_generations_for_description_repair(),
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"missing": 0, "bad": 0, "repair": 0, "error": str(e)})

    @pyqtSlot()
    def cancel_gallery_description_refresh(self):
        self.window.chat_controller.cancel_gallery_description_refresh()

    @pyqtSlot(str, str, result=str)
    def save_generation_description(self, gen_id_str, description):
        try:
            gen_id = int(gen_id_str)
            result = self.window.chat_controller.save_generation_description(gen_id, description)
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
