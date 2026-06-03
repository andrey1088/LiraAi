"""
Local emotion classifier for Russian text (Aniemore/rubert-tiny2-russian-emotion-detection).
Used to probe model replies.

Model files (place under data/models/rubert-tiny2-russian-emotion-detection/):
  config.json
  model.safetensors   (or pytorch_model.bin)
  tokenizer.json
  tokenizer_config.json
  vocab.txt
  special_tokens_map.json

Download with:
  huggingface-cli download Aniemore/rubert-tiny2-russian-emotion-detection \\
    --local-dir ~/Lira2/data/models/rubert-tiny2-russian-emotion-detection
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import torch

logger = logging.getLogger(__name__)

from infrastructure.paths import lira_data

DEFAULT_MODEL_DIR = str(lira_data("models", "rubert-tiny2-russian-emotion-detection"))

EMOTION_LABELS = (
    "neutral",
    "happiness",
    "sadness",
    "enthusiasm",
    "fear",
    "anger",
    "disgust",
)


class EmotionDetector:
    """Lazy-load BERT classifier; inference on CPU only."""

    def __init__(self, model_dir: Optional[str] = None):
        self._model_dir = os.path.expanduser(model_dir or DEFAULT_MODEL_DIR)
        self._tokenizer = None
        self._model = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None

    @property
    def model_dir(self) -> str:
        return self._model_dir

    def is_available(self) -> bool:
        return os.path.isfile(os.path.join(self._model_dir, "config.json"))

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if self._load_error is not None:
            raise RuntimeError(self._load_error)
        with self._lock:
            if self._model is not None:
                return
            if not self.is_available():
                from infrastructure.locale.variables import var_get

                self._load_error = str(var_get("emotion.model_missing", "en") or "").format(dir=self._model_dir)
                raise RuntimeError(self._load_error)
            try:
                from transformers import AutoTokenizer, BertForSequenceClassification
            except ImportError as e:
                from infrastructure.locale.variables import var_get

                self._load_error = str(var_get("emotion.need_transformers", "en") or "")
                raise RuntimeError(self._load_error) from e

            logger.info("EmotionDetector: loading from %s", self._model_dir)
            self._tokenizer = AutoTokenizer.from_pretrained(
                self._model_dir,
                local_files_only=True,
            )
            self._model = BertForSequenceClassification.from_pretrained(
                self._model_dir,
                local_files_only=True,
            )
            self._model.eval()
            self._model.to("cpu")

    @torch.no_grad()
    def predict_top(self, text: str) -> str:
        probs = self.predict_probs(text)
        return max(probs, key=probs.get)

    @torch.no_grad()
    def predict_probs(self, text: str) -> dict[str, float]:
        text = (text or "").strip()
        if not text:
            return {label: 0.0 for label in EMOTION_LABELS}

        self._ensure_loaded()
        inputs = self._tokenizer(
            text,
            max_length=512,
            padding=True,
            truncation=True,
            return_tensors="pt",
        )
        outputs = self._model(**inputs)
        probs = torch.nn.functional.softmax(outputs.logits, dim=1)[0]
        return {label: float(probs[i].item()) for i, label in enumerate(EMOTION_LABELS)}

    def analyze_text(self, text: str) -> dict:
        """For logging: top emotion + all probabilities."""
        try:
            probs = self.predict_probs(text)
            top = max(probs, key=probs.get)
            return {"ok": True, "top": top, "probs": probs}
        except Exception as e:
            return {"ok": False, "error": str(e)}
