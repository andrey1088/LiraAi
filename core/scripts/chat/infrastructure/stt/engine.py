from __future__ import annotations

import os
import tempfile
import wave
from pathlib import Path

import numpy as np

from infrastructure.stt.availability import is_stt_model_available, log_stt_unavailable_once
from infrastructure.stt.paths import MODEL_DIR, ONNX_MODEL_ID
from infrastructure.stt.recorder import SAMPLE_RATE

_model = None


def _apply_nvidia_lib_path() -> None:
    try:
        import nvidia
    except ImportError:
        return
    root = Path(nvidia.__file__).resolve().parent
    extra = sorted({str(p) for p in root.glob("*/lib") if p.is_dir()})
    if not extra:
        return
    prefix = ":".join(extra)
    current = os.environ.get("LD_LIBRARY_PATH", "")
    if not current.startswith(prefix):
        os.environ["LD_LIBRARY_PATH"] = f"{prefix}:{current}" if current else prefix


def _get_model():
    global _model
    if _model is not None:
        return _model
    if not is_stt_model_available():
        log_stt_unavailable_once()
        raise RuntimeError("stt_model_missing")

    import onnx_asr

    _apply_nvidia_lib_path()
    _model = onnx_asr.load_model(
        ONNX_MODEL_ID,
        path=MODEL_DIR,
        providers=["CPUExecutionProvider"],
    )
    return _model


def save_pcm_wav(audio: np.ndarray, path: str | Path, *, sample_rate: int = SAMPLE_RATE) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def transcribe_audio(audio: np.ndarray) -> str:
    if audio.size == 0:
        return ""
    model = _get_model()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        save_pcm_wav(audio, tmp_path)
        text = model.recognize(tmp_path)
        return (text or "").strip()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
