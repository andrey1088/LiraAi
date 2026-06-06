from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from infrastructure.stt.availability import is_stt_model_available, log_stt_unavailable_once
from infrastructure.stt.engine import transcribe_audio
from infrastructure.stt.recorder import SAMPLE_RATE, SttRecorder

MAX_RECORD_SECONDS = 25.0


class _SttTranscribeWorker(QThread):
    finished_ok = pyqtSignal(str)
    finished_err = pyqtSignal(str)

    def __init__(self, audio: np.ndarray) -> None:
        super().__init__()
        self._audio = audio

    def run(self) -> None:
        try:
            text = transcribe_audio(self._audio)
            if not text.strip():
                self.finished_err.emit("empty")
                return
            self.finished_ok.emit(text.strip())
        except Exception as exc:
            msg = str(exc)
            if msg == "stt_model_missing":
                self.finished_err.emit("stt_model_missing")
            else:
                self.finished_err.emit(msg)


class SttController(QObject):
    """Push-to-talk STT (ru locale only); independent from ChatController."""

    state_changed = pyqtSignal(bool)
    transcribe_started = pyqtSignal()
    transcribe_finished = pyqtSignal(str)
    transcribe_failed = pyqtSignal(str)

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self._recorder = SttRecorder()
        self._worker: _SttTranscribeWorker | None = None

    def is_enabled_for_ui(self) -> bool:
        if self.window.config_repo.get_ui_locale() != "ru":
            return False
        return is_stt_model_available()

    @property
    def is_recording(self) -> bool:
        return self._recorder.is_recording

    def start_recording(self) -> bool:
        if not self.is_enabled_for_ui():
            log_stt_unavailable_once()
            return False
        if self._worker is not None and self._worker.isRunning():
            return False
        if self._recorder.is_recording:
            return True
        self.window.voice_controller.stop_all_audio("stt_record")
        try:
            self._recorder.start()
        except Exception as exc:
            self.transcribe_failed.emit(str(exc))
            return False
        self.state_changed.emit(True)
        return True

    def stop_recording_and_transcribe(self) -> None:
        if not self._recorder.is_recording:
            return
        audio = self._recorder.stop()
        self.state_changed.emit(False)
        duration = float(audio.size) / SAMPLE_RATE if audio.size else 0.0
        if duration <= 0.05:
            self.transcribe_failed.emit("empty")
            return
        if duration > MAX_RECORD_SECONDS:
            max_samples = int(MAX_RECORD_SECONDS * SAMPLE_RATE)
            audio = audio[:max_samples]

        self.transcribe_started.emit()
        self._worker = _SttTranscribeWorker(audio)
        self._worker.finished_ok.connect(self._on_transcribe_ok)
        self._worker.finished_err.connect(self._on_transcribe_err)
        self._worker.finished.connect(self._clear_worker)
        self._worker.start()

    def cancel_recording(self) -> None:
        if self._recorder.is_recording:
            self._recorder.stop()
            self.state_changed.emit(False)

    def _clear_worker(self) -> None:
        self._worker = None

    def _on_transcribe_ok(self, text: str) -> None:
        self.transcribe_finished.emit(text)

    def _on_transcribe_err(self, reason: str) -> None:
        self.transcribe_failed.emit(reason)

    def shutdown(self) -> None:
        self.cancel_recording()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)
