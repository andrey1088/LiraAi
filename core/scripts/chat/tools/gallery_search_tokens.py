"""Parse gallery_search query (no synonym dict — semantics in GalleryEmbedder)."""

from __future__ import annotations

import re

from infrastructure.config.defaults import DEFAULT_UI_LOCALE
from infrastructure.locale.variables import var_frozenset


def query_tokens(query: str, locale: str | None = None) -> list[str]:
    loc = locale or DEFAULT_UI_LOCALE
    stop = var_frozenset("gallery_query.stopwords", loc)
    normalized = (query or "").lower().replace("-", " ").replace("\u2014", " ")
    words = re.findall(r"[\w\d]+", normalized, flags=re.UNICODE)
    out: list[str] = []
    seen: set[str] = set()
    for w in words:
        if len(w) < 2 or w in stop:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out
