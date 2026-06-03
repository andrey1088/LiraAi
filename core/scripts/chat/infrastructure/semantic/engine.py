import logging
import os
import sys
import threading

import torch

from infrastructure.locale.variables import var_get, var_list

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
from infrastructure.paths import lira_data

DEFAULT_MODEL_DIR = str(lira_data("models", "paraphrase-multilingual-MiniLM-L12-v2"))


class SemanticEngine:
    """
    RAG over history and memory_search (384-d MiniLM).
    Files: ~/Lira2/data/models/paraphrase-multilingual-MiniLM-L12-v2/

    Download if missing:
      huggingface-cli download sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \\
        --local-dir ~/Lira2/data/models/paraphrase-multilingual-MiniLM-L12-v2
    """

    def __init__(self, model_dir: str | None = None):
        self._model_dir = os.path.expanduser(model_dir or DEFAULT_MODEL_DIR)
        self._model_id = DEFAULT_MODEL_ID
        self._embedder = None
        self._embedder_lock = threading.Lock()
        self._warmup_done = False
        self.stop_words = list(
            dict.fromkeys(var_list("semantic.stop_phrases", "en") + var_list("semantic.stop_phrases", "ru"))
        )

    @property
    def model_dir(self) -> str:
        return self._model_dir

    def is_available(self) -> bool:
        return os.path.isfile(os.path.join(self._model_dir, "config.json")) and (
            os.path.isfile(os.path.join(self._model_dir, "model.safetensors"))
            or os.path.isfile(os.path.join(self._model_dir, "pytorch_model.bin"))
        )

    def _resolve_load_path(self) -> str:
        if self.is_available():
            return self._model_dir
        return self._model_id

    def _ensure_embedder(self) -> None:
        if self._embedder is not None:
            return
        with self._embedder_lock:
            if self._embedder is not None:
                return
            from sentence_transformers import SentenceTransformer

            # CPU only for this model; do not set CUDA_VISIBLE_DEVICES (breaks llama.cpp / SD in-process).
            load_path = self._resolve_load_path()
            local = load_path == self._model_dir
            logger.info("SemanticEngine: loading SentenceTransformer(%r)…", load_path)
            print(
                f"[SEMANTIC] embedder load path={load_path!r} local={local}",
                file=sys.stderr,
                flush=True,
            )
            self._embedder = SentenceTransformer(load_path, device="cpu")
            if local:
                print("[SEMANTIC] embedder ready (local)", file=sys.stderr, flush=True)
            else:
                print(
                    f"[SEMANTIC] embedder ready (hub/cache; place model in {DEFAULT_MODEL_DIR})",
                    file=sys.stderr,
                    flush=True,
                )

    def warmup(self) -> None:
        """Load into RAM on app start (before sleep_mode / memory_search)."""
        if self._warmup_done and self._embedder is not None:
            return
        if not self.is_available():
            print(
                f"[SEMANTIC] local model not found at {self._model_dir!r}; will try Hugging Face ({self._model_id})",
                file=sys.stderr,
                flush=True,
            )
        self._ensure_embedder()
        self._warmup_done = True

    @property
    def embedder(self):
        """Embedder access (sleep_mode, scripts) — triggers lazy load."""
        self._ensure_embedder()
        return self._embedder

    def search(self, user_query, raw_history, top_k=3, threshold=0.65, silent=False):
        if not raw_history:
            return ""

        clean_history = [(u, a, v) for u, a, v in raw_history if not any(sw in a.lower() for sw in self.stop_words)]

        if not clean_history:
            return ""

        search_corpus = [f"Q: {u} A: {a}" for u, a, v in clean_history]

        self._ensure_embedder()
        from sentence_transformers import util

        query_emb = self._embedder.encode(user_query, convert_to_tensor=True)
        hist_embs = self._embedder.encode(search_corpus, convert_to_tensor=True)

        cos_scores = util.cos_sim(query_emb, hist_embs)[0]
        top_results = torch.topk(cos_scores, k=min(top_k, len(search_corpus)))

        res_blocks = []

        for score, idx in zip(top_results.values, top_results.indices, strict=True):
            s_val = score.item()

            if s_val < threshold:
                continue

            u_old, a_old, verified = clean_history[idx.item()]

            loc = "en"
            if verified == 1:
                tag = var_get("semantic.tag_confirmed", loc)
                directive = var_get("semantic.directive_confirmed", loc)
            elif verified == 2:
                tag = var_get("semantic.tag_error", loc)
                directive = var_get("semantic.directive_error", loc)
            else:
                tag = var_get("semantic.tag_context", loc)
                directive = var_get("semantic.directive_context", loc)

            fragment = f"{tag}\n{directive}\n" + var_get("semantic.memory_pair_template", loc).format(
                question=u_old, answer=a_old
            )

            if s_val > 0.70:
                return self._wrap_result(fragment)

            res_blocks.append(fragment)

        if not res_blocks:
            return ""

        return self._wrap_result("\n".join(res_blocks))

    def _wrap_result(self, content):
        loc = "en"
        return (
            var_get("semantic.persona_facts_header", loc)
            + f"{content}\n"
            + var_get("semantic.persona_facts_footer", loc)
        )
