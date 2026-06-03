import os
import threading
import time

import sounddevice as sd
import torch
from PyQt6.QtCore import QThread

from infrastructure.config.defaults import TTS_PROFILES
from infrastructure.tts.availability import is_tts_model_available, log_tts_unavailable, tts_model_path


class LiraVoice:
    def __init__(
        self,
        locale: str = "ru",
        speaker: str | None = None,
        model_path: str | None = None,
    ):
        self.volume = 0.0
        self.device = torch.device("cpu")
        self.locale = "ru"
        self.model_path = ""
        self.model = None
        self.sample_rate = 48000
        self.current_speaker = "kseniya"
        self.available = False
        self._playback_abort = threading.Event()
        self.set_locale(locale, speaker, model_path=model_path)

    def _profile(self, locale: str) -> dict:
        loc = locale if locale in TTS_PROFILES else "ru"
        return TTS_PROFILES[loc]

    def _load_model(self, locale: str, model_path: str | None = None) -> bool:
        profile = self._profile(locale)
        path = tts_model_path(locale, model_path=model_path or profile["model_path"])
        if not os.path.isfile(path):
            self.model = None
            self.model_path = path
            return False
        self.model_path = path
        self.sample_rate = int(profile.get("sample_rate", 48000))
        self.model = torch.package.PackageImporter(path).load_pickle("tts_models", "model")
        self.model.to(self.device)
        self.locale = locale if locale in TTS_PROFILES else "ru"
        return True

    def set_locale(
        self,
        locale: str,
        speaker: str | None = None,
        model_path: str | None = None,
    ) -> None:
        loc = locale if locale in TTS_PROFILES else "ru"
        profile = self._profile(loc)
        path_arg = (model_path or "").strip() or None
        reload = loc != self.locale or self.model is None
        resolved = tts_model_path(loc, model_path=path_arg)
        if resolved != os.path.expanduser(self.model_path or ""):
            reload = True
        if reload:
            ok = self._load_model(loc, path_arg)
            self.available = ok
            if not ok:
                log_tts_unavailable(loc, resolved)
        sp = (speaker or "").strip() or profile["default_speaker"]
        self.current_speaker = sp

    def set_speaker(self, speaker_name: str) -> None:
        self.current_speaker = speaker_name

    def reset_for_model(self, speaker_name: str) -> None:
        self.request_stop_playback()
        self.set_speaker(speaker_name)

    def set_volume(self, volume: float) -> None:
        prev_volume = self.volume
        self.volume = float(volume)
        if self.volume <= 0 and prev_volume > 0:
            self.request_stop_playback()

    def speak(self, text: str) -> None:
        clean_text = text.strip()
        if not self.available or not clean_text or len(clean_text) < 2 or self.model is None:
            return
        if self.volume <= 0:
            return

        self._playback_abort.clear()

        try:
            audio = self.model.apply_tts(
                text=clean_text,
                speaker=self.current_speaker,
                sample_rate=self.sample_rate,
            )

            if self._playback_abort.is_set():
                return

            audio_data = audio.cpu().numpy() * self.volume

            if self.volume > 0:
                sd.play(audio_data, self.sample_rate)
                self._wait_playback_worker_only()
        except Exception:
            pass

    def request_stop_playback(self) -> None:
        self._playback_abort.set()

    def stop(self) -> None:
        self.request_stop_playback()

    def _wait_playback_worker_only(self) -> None:
        try:
            while True:
                if self._playback_abort.is_set():
                    try:
                        sd.stop()
                    except Exception:
                        pass
                    return
                try:
                    stream = sd.get_stream()
                except RuntimeError:
                    break
                if stream is None or not stream.active:
                    break
                time.sleep(0.02)
            try:
                sd.wait(ignore_errors=True)
            except Exception:
                pass
        except Exception:
            pass


class VoiceWorker(QThread):
    def __init__(self, voice_engine, text):
        super().__init__()
        self.voice = voice_engine
        self.text = text

    def run(self):
        if self.isInterruptionRequested():
            return
        self.voice.speak(self.text)
