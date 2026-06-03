# Semantics: separate GalleryEmbedder (see config.json → gallery_search).
import json
import os
import sys

from core.scripts.chat.tools.gallery_search_tokens import query_tokens

HYBRID_POOL = 150
TEXT_HITS_LIMIT = 12

_DEFAULT_MIN_SIMILARITY = 0.55
_RELAXED_MIN_SIMILARITY = 0.48
_DEFAULT_MAX_RESULTS = 10
_DEFAULT_MAX_VISION_IMAGES = 10
_DEFAULT_VISION_BATCH_SIZE = 4


def _load_gallery_search_dict() -> dict:
    from infrastructure.paths import config_path

    path = str(config_path())
    try:
        if os.path.isfile(path):
            gs = (json.loads(open(path, encoding="utf-8").read()) or {}).get("gallery_search")
            if isinstance(gs, dict):
                return gs
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return {}


def _gallery_min_similarity() -> tuple[float, float]:
    gs = _load_gallery_search_dict()
    if gs.get("min_similarity") is not None:
        try:
            base = float(gs["min_similarity"])
            return base, max(0.35, base - 0.07)
        except (TypeError, ValueError):
            pass
    return _DEFAULT_MIN_SIMILARITY, _RELAXED_MIN_SIMILARITY


def _gallery_int_setting(key: str, default: int, *, min_v: int = 1, max_v: int = 20) -> int:
    gs = _load_gallery_search_dict()
    if key in gs and gs[key] is not None:
        try:
            return max(min_v, min(max_v, int(gs[key])))
        except (TypeError, ValueError):
            pass
    return default


def gallery_max_results() -> int:
    """Frames to return in UI and tool (config: gallery_search.max_results)."""
    return _gallery_int_setting("max_results", _DEFAULT_MAX_RESULTS)


def gallery_max_vision_images() -> int:
    """Frames to run through vision (config: gallery_search.max_vision_images)."""
    return _gallery_int_setting("max_vision_images", _DEFAULT_MAX_VISION_IMAGES)


def gallery_vision_batch_size() -> int:
    """Frames per Gemma vision call (config: gallery_search.vision_batch_size)."""
    return _gallery_int_setting("vision_batch_size", _DEFAULT_VISION_BATCH_SIZE)


def _caption(prompt: str, description: str) -> str:
    d = (description or "").strip()
    if d:
        return d
    return (prompt or "").strip()


def _append_hit(image_data, final_ids, gen_id, prompt, path, description: str = ""):
    if gen_id in final_ids:
        return False
    desc = (description or "").strip()
    image_data.append(
        {
            "id": gen_id,
            "prompt": prompt,
            "path": path,
            "description": desc,
            "caption": _caption(prompt, desc),
        }
    )
    final_ids.append(gen_id)
    return True


def _parse_hybrid_row(res) -> tuple:
    if len(res) >= 5:
        return res[0], res[1], res[2], res[4]
    if len(res) >= 4:
        return res[0], res[1], res[2], ""
    return res[0], res[1], res[2], ""


def _log_search(
    query: str,
    hits: list,
    *,
    relaxed: bool = False,
    hybrid_rows: list | None = None,
    embedder_model: str = "",
) -> None:
    model_bit = f" model={embedder_model!r}" if embedder_model else ""
    if not hits and not hybrid_rows:
        print(
            f"[GALLERY] search q={query!r} hits=0 relaxed={relaxed}{model_bit}",
            file=sys.stderr,
            flush=True,
        )
        return
    if hybrid_rows and len(hybrid_rows[0]) >= 4:
        brief = [f"{int(r[0])}@{float(r[3]):.2f}" for r in hybrid_rows[:5]]
        print(
            f"[GALLERY] search q={query!r} hits={len(hits)} relaxed={relaxed} top={brief}{model_bit}",
            file=sys.stderr,
            flush=True,
        )
        return
    top = [h.get("id") if isinstance(h, dict) else h[0] for h in hits[:5]]
    print(
        f"[GALLERY] search q={query!r} hits={len(hits)} relaxed={relaxed} top_ids={top}{model_bit}",
        file=sys.stderr,
        flush=True,
    )


def gallery_tool(query, repository, semantic_engine, **kwargs):
    q = (query or "").strip()
    if not q:
        return []

    from infrastructure.model_tasks.gallery.embedder import get_gallery_embedder

    ge = get_gallery_embedder()
    query_vector = ge.encode_query(q).tolist()

    final_ids: list = []
    image_data: list = []
    tokens = query_tokens(q, kwargs.get("locale"))
    min_sim, min_relaxed = _gallery_min_similarity()

    def _run_hybrid(threshold: float) -> list:
        try:
            if hasattr(repository, "search_gallery_hybrid"):
                return repository.search_gallery_hybrid(
                    q,
                    query_vector,
                    limit=gallery_max_results(),
                    pool=HYBRID_POOL,
                    min_score=threshold,
                )
            return repository.search_gallery_semantic(
                query_vector,
                limit=HYBRID_POOL,
                described_only=True,
            )
        except Exception as e:
            print(f"[GALLERY] hybrid search failed: {e}", file=sys.stderr, flush=True)
            return []

    relaxed = False
    hybrid = _run_hybrid(min_sim)
    if not hybrid:
        relaxed = True
        hybrid = _run_hybrid(min_relaxed)

    for res in hybrid:
        v_id, prompt, path, desc = _parse_hybrid_row(res)
        _append_hit(image_data, final_ids, v_id, prompt, path, desc)
        if len(image_data) >= gallery_max_results():
            _log_search(
                q,
                image_data,
                relaxed=relaxed,
                hybrid_rows=hybrid,
                embedder_model=ge.model_name,
            )
            return image_data

    if len(image_data) < 3 and tokens and hasattr(repository, "search_gallery_by_tokens"):
        for res in repository.search_gallery_by_tokens(tokens, limit=TEXT_HITS_LIMIT):
            desc = res[4] if len(res) > 4 else ""
            _append_hit(image_data, final_ids, res[0], res[1], res[2], desc)
            if len(image_data) >= gallery_max_results():
                break

    max_results = gallery_max_results()
    if len(image_data) < max_results:
        for res in repository.search_gallery_by_text(q, limit=TEXT_HITS_LIMIT):
            desc = res[4] if len(res) > 4 else ""
            _append_hit(image_data, final_ids, res[0], res[1], res[2], desc)
            if len(image_data) >= max_results:
                break

    _log_search(
        q,
        image_data,
        relaxed=relaxed,
        hybrid_rows=hybrid,
        embedder_model=ge.model_name,
    )
    return image_data
