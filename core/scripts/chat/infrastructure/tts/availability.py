"""Silero TTS: optional; no auto-download (weights are not in git)."""

from __future__ import annotations

import os
import sys

from infrastructure.config.defaults import TTS_PROFILES
from infrastructure.paths import resolve_path


def tts_model_path(locale: str = "ru", *, model_path: str | None = None) -> str:
    loc = locale if locale in TTS_PROFILES else "ru"
    raw = (model_path or "").strip() or TTS_PROFILES[loc]["model_path"]
    return resolve_path(raw)


def is_tts_model_available(locale: str = "ru", *, model_path: str | None = None) -> bool:
    path = tts_model_path(locale, model_path=model_path)
    return os.path.isfile(path)


def log_tts_unavailable(locale: str, path: str) -> None:
    print(
        f"[TTS] Silero отключена: нет файла {path!r} (locale={locale}). "
        f"См. docs/tts.md и https://github.com/snakers4/silero-models",
        file=sys.stderr,
        flush=True,
    )
