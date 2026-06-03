import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from infrastructure.tts.engine import LiraVoice, VoiceWorker


class VoiceController:
    def __init__(self, window):
        self.window = window
        repo = window.config_repo
        locale = repo.get_ui_locale()
        m_info = repo.get_active_model_info()
        speaker = repo.get_tts_speaker_for_locale(m_info, locale)
        model_path = repo.get_tts_model_path(locale)
        self.voice = LiraVoice(locale=locale, speaker=speaker, model_path=model_path)
        self.speech_queue = []
        self.voice_buffer = ""
        self.is_speaking = False
        self.voice_thread = None
        self._voice_generation = 0

        # 1. Load volume from nested settings dict
        m_info = self.window.config_repo.get_active_model_info()
        # In ConfigRepository settings live in m_info.settings
        settings = getattr(m_info, "settings", {})
        self.volume = settings.get("volume", 0.7)
        self.is_muted = False

        # 2. Apply to the engine
        self.update_voice_params()

    def update_voice_params(self):
        """Update engine volume. 0 when muted."""
        current_vol = 0.0 if self.is_muted else self.volume
        self.voice.set_volume(current_vol)

    def set_volume(self, val):
        """Live volume change (not persisted to file)"""
        self.volume = float(val)
        self.update_voice_params()

    def toggle_mute(self):
        """Toggle mute (via Bridge)"""
        self.is_muted = not self.is_muted
        self.update_voice_params()
        return self.is_muted

    def _clean_text_for_speech(self, text):
        import re

        # Raw r'' string for correct special-char handling
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"[*#_`~\[\]\(\)<>|\\/^]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _wait_voice_thread_finish(self, timeout_ms=2500):
        """Wait for VoiceWorker without blocking the UI forever."""
        if not self.voice_thread or not self.voice_thread.isRunning():
            return
        self.voice.request_stop_playback()
        deadline = time.time() + timeout_ms / 1000.0
        while self.voice_thread.isRunning() and time.time() < deadline:
            QApplication.processEvents()
            self.voice_thread.wait(40)

    def stop_all_audio(self, reason="manual"):
        # Mark all active/stale threads as obsolete.
        self._voice_generation += 1
        self.voice.request_stop_playback()
        self.speech_queue.clear()
        self.voice_buffer = ""
        self.is_speaking = False

        if self.voice_thread and self.voice_thread.isRunning():
            try:
                self.voice_thread.requestInterruption()
                self.voice_thread.finished.disconnect()
            except Exception:
                # Best-effort: voice thread already finished or signals disconnected.
                pass
            self._wait_voice_thread_finish()
        self.voice_thread = None

    def interrupt_voice(self):
        # Back-compat for legacy callers.
        self.stop_all_audio("interrupt")

    def set_speaker(self, speaker_name):
        self.voice.set_speaker(speaker_name)

    def reset_voice_for_model(self, speaker_name):
        self.stop_all_audio("model_reset")
        self.voice.reset_for_model(speaker_name)

    def apply_ui_locale(self, locale: str | None = None) -> None:
        """Switch Silero; caller should save_ui_locale with runtime speaker/path after."""
        repo = self.window.config_repo
        loc = locale or repo.get_ui_locale()
        m_info = repo.get_active_model_info()
        speaker = repo.get_tts_speaker_for_locale(m_info, loc)
        model_path = repo.get_tts_model_path(loc)
        self.stop_all_audio("locale_change")
        self.voice.set_locale(loc, speaker, model_path=model_path)

    def persist_tts_to_config(self, locale: str | None = None) -> None:
        repo = self.window.config_repo
        loc = locale or repo.get_ui_locale()
        repo.save_ui_locale(
            loc,
            speaker=self.voice.current_speaker,
            model_path=self.voice.model_path,
        )

    def update_chat(self, token):
        # If a switch is pending, skip UI and TTS
        if self.window.pending_switch is not None:
            return

        cc = self.window.chat_controller

        if cc.is_first_model_token:
            self.window.hide_thinking_indicator()
            self.window.inject_message("model", token)
            cc.is_first_model_token = False
        else:
            self.window.inject_message("model", token, is_stream=True)

        if self.is_muted:
            self.voice_buffer = ""
            return

        # Split logic for long tokens
        if len(token) > 200:
            import re

            sentences = re.split(r"(?<=[.!?,\n])\s+", token)
            for sentence in sentences:
                chunk = self._clean_text_for_speech(sentence.strip())
                if any(c.isalnum() for c in chunk):
                    self.speech_queue.append(chunk)

            if not self.is_speaking and self.speech_queue:
                self.start_speaking_queue()
        else:
            # Standard logic for short tokens
            self.voice_buffer += token
            if any(punct in token for punct in ".!?,\n"):
                raw_chunk = self.voice_buffer.strip()
                self.voice_buffer = ""
                chunk = self._clean_text_for_speech(raw_chunk)
                if any(c.isalnum() for c in chunk):
                    self.speech_queue.append(chunk)
                    if not self.is_speaking:
                        self.start_speaking_queue()

    def process_voice_tail(self):
        """Speak remaining buffered text after generation ends."""
        tail = self.voice_buffer.strip()
        if tail and any(c.isalnum() for c in tail):
            self.speech_queue.append(tail)
            self.voice_buffer = ""
            if not self.is_speaking:
                self.start_speaking_queue()
        self.voice_buffer = ""

    def speak_message(self, text: str) -> None:
        """Speak a finished utterance (no chat streaming)."""
        if self.window.pending_switch is not None:
            return
        if self.is_muted:
            return
        chunk = self._clean_text_for_speech((text or "").strip())
        if not chunk or not any(c.isalnum() for c in chunk):
            return
        self.speech_queue.append(chunk)
        if not self.is_speaking:
            self.start_speaking_queue()

    def start_speaking_queue(self):
        if not self.speech_queue:
            self.is_speaking = False
            return
        if not self.voice.available:
            self.speech_queue.clear()
            self.is_speaking = False
            return

        self.is_speaking = True
        text_to_speak = self.speech_queue.pop(0)
        generation = self._voice_generation

        if self.voice_thread and self.voice_thread.isRunning():
            try:
                self.voice_thread.requestInterruption()
                self.voice_thread.finished.disconnect()
            except Exception:
                # Thread may have already finished or signals disconnected; continue cleanup.
                pass
            self._wait_voice_thread_finish()
            self.voice_thread = None

        self.voice_thread = VoiceWorker(self.voice, text_to_speak)
        self.voice_thread.finished.connect(lambda g=generation: self.wait_and_speak_next(g))
        self.voice_thread.start()

    def wait_and_speak_next(self, generation):
        # Stale thread finished after stop/reset — ignore its callback.
        if generation != self._voice_generation:
            return
        self.is_speaking = False
        QTimer.singleShot(200, self.start_speaking_queue)
