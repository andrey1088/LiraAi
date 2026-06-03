class SessionManager:
    def __init__(self, db_instance):
        self.db = db_instance
        self.current_id = None
        self.history = []

    def load_initial(self):
        """Load on startup or model switch"""
        last_id = self.db.get_last_session_id()
        if last_id:
            self.current_id = last_id
            self.history = self.db.get_session_messages(last_id)
        else:
            self.current_id = None
            self.history = []
        return self.current_id

    def switch_to(self, session_id):
        """Switch chat via sidebar"""
        self.current_id = session_id
        self.history = self.db.get_session_messages(session_id)
        self.db.update_session_time(session_id)
        return self.history

    def reset(self):
        """For New chat button"""
        self.current_id = None
        self.history = []

    def ensure_session(self):
        """Lazy create before first message"""
        if self.current_id is None:
            self.current_id = self.db.start_session()
        return self.current_id

    def add_assistant_reply(self, user_text, assistant_text):
        if self.current_id:
            self.db.add_chat_message(self.current_id, "user", user_text)
            self.db.add_chat_message(self.current_id, "assistant", assistant_text)

        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": assistant_text})
