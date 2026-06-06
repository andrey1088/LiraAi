"""STT bootstrap: optional deps + GigaAM weights (ru locale, background on startup)."""

from __future__ import annotations

import logging
import subprocess
import sys

from infrastructure.stt.availability import is_stt_model_available, missing_model_files
from infrastructure.stt.paths import HF_MODEL_REPO, MODEL_DIR, MODEL_DOWNLOAD_FILES, REQUIRED_MODEL_FILES

logger = logging.getLogger(__name__)

_PIP_PACKAGES = ("onnx-asr>=0.11", "onnxruntime", "huggingface_hub")


def _stt_import_ok() -> bool:
    try:
        import huggingface_hub  # noqa: F401
        import onnxruntime  # noqa: F401
        import onnx_asr  # noqa: F401
        return True
    except ImportError:
        return False


def _pip_install(*specs: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", *specs],
        check=True,
        capture_output=True,
        text=True,
    )


def ensure_stt_dependencies() -> bool:
    """Install onnx-asr, onnxruntime, huggingface_hub when missing."""
    if _stt_import_ok():
        return True
    try:
        logger.info("[STT] Installing Python packages: %s", ", ".join(_PIP_PACKAGES))
        _pip_install(*_PIP_PACKAGES)
        if _stt_import_ok():
            return True
        logger.warning("[STT] Packages installed but STT imports still fail")
        return False
    except Exception as exc:
        logger.warning("[STT] Failed to install dependencies: %s", exc)
        return False


def download_model_weights() -> bool:
    if is_stt_model_available():
        return True
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        logger.warning("[STT] huggingface_hub not available for model download")
        return False

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    missing = missing_model_files()
    logger.info("[STT] Downloading GigaAM ONNX to %s (missing: %s)", MODEL_DIR, ", ".join(missing))
    try:
        for filename in MODEL_DOWNLOAD_FILES:
            dest = MODEL_DIR / filename
            if dest.is_file():
                continue
            hf_hub_download(HF_MODEL_REPO, filename, local_dir=str(MODEL_DIR))
    except Exception as exc:
        logger.warning("[STT] Model download failed: %s", exc)
        return False

    if is_stt_model_available():
        logger.info("[STT] GigaAM model ready")
        return True
    logger.warning("[STT] Model download incomplete: %s", ", ".join(missing_model_files()))
    return False


def ensure_stt_ready() -> bool:
    """Dependencies + weights; safe to call from a background thread."""
    if not ensure_stt_dependencies():
        return False
    return download_model_weights()


def stt_bootstrap_needed(locale: str, *, model_class: str | None = None) -> bool:
    if (locale or "").strip().lower() != "ru":
        return False
    if model_class in ("text-to-image", "image-edit"):
        return False
    if is_stt_model_available() and _stt_import_ok():
        return False
    return True
