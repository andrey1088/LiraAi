"""Default config fields and normalization (ui_locale, tts, active_model)."""

from __future__ import annotations

import os

from infrastructure.paths import lira_data

UI_LOCALES = frozenset({"ru", "en"})
DEFAULT_UI_LOCALE = "ru"
DEFAULT_ACTIVE_MODEL_ID = "1"

_TTS_ROOT = str(lira_data("models"))

# Shared with tts_engine.LiraVoice (paths and default speakers).
TTS_PROFILES = {
    "ru": {
        "model_path": os.path.join(_TTS_ROOT, "v5_5_ru.pt"),
        "default_speaker": "kseniya",
        "sample_rate": 48000,
    },
    "en": {
        "model_path": os.path.join(_TTS_ROOT, "v3_en.pt"),
        "default_speaker": "en_21",
        "sample_rate": 48000,
    },
}


def config_path_tilde(path: str) -> str:
    """Store under home as ~/… when possible."""
    p = os.path.expanduser(path or "")
    home = os.path.expanduser("~")
    if p.startswith(home + os.sep):
        return "~" + p[len(home) :]
    return path


def default_tts_block(locale: str = DEFAULT_UI_LOCALE, *, speaker: str | None = None) -> dict:
    loc = locale if locale in TTS_PROFILES else DEFAULT_UI_LOCALE
    prof = TTS_PROFILES[loc]
    sp = (speaker or "").strip() or prof["default_speaker"]
    block = {
        "locale": loc,
        "model_path": config_path_tilde(prof["model_path"]),
        "speaker": sp,
        "sample_rate": int(prof.get("sample_rate", 48000)),
    }
    if loc == "en":
        block["en_speaker"] = sp
    else:
        block["en_speaker"] = TTS_PROFILES["en"]["default_speaker"]
    return block


def ensure_config_defaults(cfg: dict) -> bool:
    """Fill missing root keys; return True if cfg was modified."""
    changed = False

    loc = str(cfg.get("ui_locale") or DEFAULT_UI_LOCALE).strip().lower()
    if loc not in UI_LOCALES:
        loc = DEFAULT_UI_LOCALE
        changed = True
    if cfg.get("ui_locale") != loc:
        cfg["ui_locale"] = loc
        changed = True

    if not cfg.get("active_model_id"):
        cfg["active_model_id"] = DEFAULT_ACTIVE_MODEL_ID
        changed = True

    models = cfg.get("models")
    if not isinstance(models, list):
        cfg["models"] = []
        changed = True
        models = cfg["models"]

    aid = str(cfg.get("active_model_id"))
    active_name = cfg.get("active_model")
    for m in models:
        if str(m.get("id")) == aid:
            name = m.get("name") or ""
            if active_name != name:
                cfg["active_model"] = name
                changed = True
            break
    else:
        if models and active_name != models[0].get("name"):
            cfg["active_model"] = models[0].get("name", "")
            cfg["active_model_id"] = str(models[0].get("id", DEFAULT_ACTIVE_MODEL_ID))
            changed = True

    tts = cfg.get("tts")
    if not isinstance(tts, dict):
        cfg["tts"] = default_tts_block(loc)
        return True

    prof = TTS_PROFILES.get(loc, TTS_PROFILES[DEFAULT_UI_LOCALE])
    expected_path = config_path_tilde(prof["model_path"])
    if tts.get("locale") != loc:
        tts["locale"] = loc
        tts["model_path"] = expected_path
        if loc == "en":
            tts["speaker"] = str(tts.get("en_speaker") or prof["default_speaker"]).strip()
        changed = True

    for key, val in (
        ("model_path", expected_path),
        ("sample_rate", int(prof.get("sample_rate", 48000))),
    ):
        if not tts.get(key):
            tts[key] = val
            changed = True

    if not tts.get("speaker"):
        tts["speaker"] = prof["default_speaker"]
        changed = True

    if not tts.get("en_speaker"):
        tts["en_speaker"] = TTS_PROFILES["en"]["default_speaker"]
        changed = True

    return changed
