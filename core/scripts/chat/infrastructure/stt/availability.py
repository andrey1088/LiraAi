"""GigaAM STT: optional; weights live under data/models/."""

from __future__ import annotations

import logging

from infrastructure.stt.paths import MODEL_DIR, REQUIRED_MODEL_FILES

logger = logging.getLogger(__name__)
_warned = False


def missing_model_files() -> list[str]:
    return [name for name in REQUIRED_MODEL_FILES if not (MODEL_DIR / name).is_file()]


def is_stt_model_available() -> bool:
    return not missing_model_files()


def log_stt_unavailable_once() -> None:
    global _warned
    if _warned or is_stt_model_available():
        return
    _warned = True
    missing = ", ".join(missing_model_files())
    logger.warning(
        "[STT] GigaAM disabled: missing in %s: %s (will retry on next ru startup)",
        MODEL_DIR,
        missing,
    )
