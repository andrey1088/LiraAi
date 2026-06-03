import os


class SessionController:
    def __init__(self, window):
        self.window = window
        self.current_session_id = None
        self.history = []
        self.pending_session_id = None
        self._camera_consent_sessions: set[int] = set()

    def normalized_session_id(self) -> int | None:
        if self.current_session_id is None:
            return None
        return int(self.current_session_id)

    def has_camera_consent(self, session_id: int | None = None) -> bool:
        sid = int(session_id) if session_id is not None else self.normalized_session_id()
        if sid is None:
            return False
        return sid in self._camera_consent_sessions

    def grant_camera_consent(self, session_id: int | None = None) -> None:
        sid = int(session_id) if session_id is not None else self.normalized_session_id()
        if sid is not None:
            self._camera_consent_sessions.add(sid)

    def ensure_session(self):
        m_info = self.window.model_controller.get_active_model_info()

        if m_info.model_class in ("text-to-image", "image-edit"):
            return

        if self.current_session_id is None:
            # Try restoring the last session from the current DB
            last_id = self.window.repository.get_last_session_id()
            if last_id:
                self.current_session_id = last_id
                self.history = self.window.repository.get_session_messages(last_id)
            else:
                # Only if the DB is empty — create a new session
                self.current_session_id = self.window.repository.start_session()
        return self.current_session_id

    def create_new_session(self):
        self.window.interrupt_voice()
        new_id = self.window.repository.start_session()
        self.current_session_id = new_id
        self.history = []
        return new_id

    def request_chat_switch(self, session_id):
        self.window.interrupt_voice()

        cc = self.window.chat_controller
        worker_running = cc.worker is not None and cc.worker.isRunning()

        if not worker_running:
            self.current_session_id = session_id
            self.history = self.window.repository.get_session_messages(session_id)
            return {"pending": False, "session_id": session_id, "messages": self.history}

        self.window.pending_switch_kind = "chat"
        self.pending_session_id = session_id
        self.window.pending_switch = {"session_id": session_id}  # gui.py compatibility
        pass
        return {"pending": True, "session_id": session_id, "messages": []}

    def finish_pending_chat_switch(self):
        self.current_session_id = self.pending_session_id
        self.history = self.window.repository.get_session_messages(self.current_session_id)

        script = f"""
            if (window.liraApp) {{
                liraApp.updateUI().then(() => {{
                    liraApp.loadChatSession({self.current_session_id});
                }});
            }}
        """
        self.window.browser.page().runJavaScript(script)
        self.pending_session_id = None

    def get_session_media_dir(self):
        from infrastructure.paths import lira_data

        m_info = self.window.model_controller.get_active_model_info()

        if m_info.model_class in ("text-to-image", "image-edit"):
            # Artist / Qwen Edit — shared gallery folder, not per session
            path = str(lira_data("media", "gallery"))
        else:
            # Legacy path (data/media/session_number)
            path = str(lira_data("media", self.current_session_id))

        os.makedirs(path, exist_ok=True)
        return path
