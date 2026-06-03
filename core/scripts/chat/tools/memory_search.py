import re

from infrastructure.locale.i18n import tr_tools

_DEFAULT_MAX_MEMORY_DISTANCE = 0.20


def _max_memory_distance(kwargs: dict) -> float:
    """Configurable relevance gate for memory hits (lower distance is better)."""
    try:
        window = kwargs.get("window")
        cfg = getattr(getattr(window, "config_repo", None), "config", None)
        if isinstance(cfg, dict):
            ms = cfg.get("memory_search")
            if isinstance(ms, dict) and ms.get("max_distance") is not None:
                val = float(ms.get("max_distance"))
                return max(0.01, min(val, 1.50))
    except Exception:
        pass
    return _DEFAULT_MAX_MEMORY_DISTANCE


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"\w+", (text or "").casefold()) if len(t) >= 3}


def _is_relevant_hit(query: str, user_msg: str, distance: float, max_distance: float) -> bool:
    # 1) strict vector gate
    if distance <= max_distance:
        return True
    # 2) lexical overlap gate (prevents missing clear matches like "день рождения")
    q = _tokenize(query)
    u = _tokenize(user_msg)
    if not q or not u:
        return False
    overlap = q & u
    return len(overlap) >= 2


def memory_tool(query, repository, semantic_engine, locale="ru", **kwargs):
    """Fetch facts from long-term vector memory."""
    loc = str(locale or "ru")
    try:
        # Run two semantic probes: plain question and the same in "Q: ... A:" form.
        qv_plain = semantic_engine.embedder.encode(query).tolist()
        qv_pair = semantic_engine.embedder.encode(f"Q: {query} A:").tolist()
        results_plain = repository.search_long_term(qv_plain, limit=8) or []
        results_pair = repository.search_long_term(qv_pair, limit=8) or []
        if not results_plain and not results_pair:
            return tr_tools("tools.memory_search.no_hit", loc)

        merged: dict[tuple[str, str], float] = {}
        for u, a, d in [*results_plain, *results_pair]:
            if d is None:
                continue
            key = (str(u or ""), str(a or ""))
            dist = float(d)
            prev = merged.get(key)
            if prev is None or dist < prev:
                merged[key] = dist

        max_distance = _max_memory_distance(kwargs)
        ranked = sorted(
            ((u, a, d) for (u, a), d in merged.items()),
            key=lambda x: x[2],
        )
        filtered = [(u, a, d) for (u, a, d) in ranked if _is_relevant_hit(query, u, d, max_distance)][:3]
        if not filtered:
            return tr_tools("tools.memory_search.no_hit", loc)

        header = tr_tools("tools.memory_search.context_header", loc)
        formatted = header + "\n"
        for _u_msg, a_reply, _distance in filtered:
            formatted += tr_tools("tools.memory_search.fact_line", loc).format(reply=a_reply) + "\n"
        return formatted
    except Exception as e:
        return tr_tools("tools.memory_search.error", loc).format(e=e)
