"""
Gallery-only embedder (separate from memory_search / MiniLM).
Model and format: ~/Lira2/config.json → gallery_search.
After model change: python3 ~/Lira2/core/scripts/maintenance/index_gallery.py

Local copy (optional, like rubert for emotions):
  huggingface-cli download intfloat/multilingual-e5-small \\
    --local-dir ~/Lira2/data/models/multilingual-e5-small
If the directory exists — load from disk; else Hugging Face (~/.cache/huggingface/).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

from infrastructure.paths import config_path, lira_data

_CONFIG_PATH = config_path()
_DEFAULT_MODEL = "intfloat/multilingual-e5-small"
_DEFAULT_FORMAT = "e5"
_DEFAULT_LOCAL_DIR = lira_data("models", "multilingual-e5-small")

_gallery_embedder: "GalleryEmbedder | None" = None
_gallery_lock = threading.Lock()


def _load_gallery_search_config() -> tuple[str, str, Path | None]:
    path = Path(os.path.expanduser(_CONFIG_PATH))
    if not path.is_file():
        return _DEFAULT_MODEL, _DEFAULT_FORMAT, None
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return _DEFAULT_MODEL, _DEFAULT_FORMAT, None
    gs = data.get("gallery_search")
    if not isinstance(gs, dict):
        return _DEFAULT_MODEL, _DEFAULT_FORMAT, None
    model = (gs.get("embedding_model") or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    fmt = (gs.get("embedding_format") or _DEFAULT_FORMAT).strip().lower() or _DEFAULT_FORMAT
    local_raw = (gs.get("embedding_model_path") or "").strip()
    local = Path(os.path.expanduser(local_raw)) if local_raw else None
    return model, fmt, local


class GalleryEmbedder:
    def __init__(self, model_name: str | None = None, embedding_format: str | None = None):
        cfg_model, cfg_fmt, cfg_local = _load_gallery_search_config()
        self.model_name = model_name or cfg_model
        self.embedding_format = (embedding_format or cfg_fmt).lower()
        self._local_path = cfg_local
        self._model = None
        self._lock = threading.Lock()

    def _resolve_load_path(self) -> str:
        if self._local_path is not None and self._local_path.is_dir():
            return str(self._local_path)
        default_local = Path(os.path.expanduser(_DEFAULT_LOCAL_DIR))
        if default_local.is_dir():
            return str(default_local)
        return self.model_name

    def _ensure(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            from sentence_transformers import SentenceTransformer

            load_path = self._resolve_load_path()
            logger.info(
                "GalleryEmbedder: loading %r (format=%s)…",
                load_path,
                self.embedding_format,
            )
            print(
                f"[GALLERY] embedder load path={load_path!r} format={self.embedding_format!r}",
                flush=True,
            )
            self._model = SentenceTransformer(load_path, device="cpu")

    def _wrap_query(self, text: str) -> str:
        t = (text or "").strip()
        if self.embedding_format == "e5":
            return f"query: {t}"
        return t

    def _wrap_document(self, text: str) -> str:
        t = (text or "").strip()
        if self.embedding_format == "e5":
            return f"passage: {t}"
        return t

    def encode_query(self, text: str) -> np.ndarray:
        self._ensure()
        return self._model.encode(
            self._wrap_query(text),
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

    def encode_document(self, text: str) -> np.ndarray:
        self._ensure()
        return self._model.encode(
            self._wrap_document(text),
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

    def encode_document_bytes(self, text: str) -> bytes:
        return self.encode_document(text).astype(np.float32).tobytes()


def get_gallery_embedder() -> GalleryEmbedder:
    global _gallery_embedder
    if _gallery_embedder is None:
        with _gallery_lock:
            if _gallery_embedder is None:
                _gallery_embedder = GalleryEmbedder()
    return _gallery_embedder
