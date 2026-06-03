"""Locale-specific data: intent needles, stopwords, persona defaults, limbic wording (JSON per locale)."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from infrastructure.locale.loader import DEFAULT_LOCALE, LOCALE_ROOT, normalize_locale

_VARIABLES_DIR = LOCALE_ROOT / "variables"


@lru_cache(maxsize=8)
def load_variables(locale: str | None = None) -> dict[str, Any]:
    loc = normalize_locale(locale)
    path = _VARIABLES_DIR / f"{loc}.json"
    if not path.is_file() and loc != DEFAULT_LOCALE:
        path = _VARIABLES_DIR / f"{DEFAULT_LOCALE}.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _walk(data: dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def var_get(dotted: str, locale: str | None = None, *, default: Any = None) -> Any:
    value = _walk(load_variables(locale), dotted)
    return default if value is None else value


def var_list(dotted: str, locale: str | None = None) -> tuple[str, ...]:
    value = var_get(dotted, locale)
    if isinstance(value, list):
        return tuple(str(x).strip() for x in value if str(x).strip())
    if isinstance(value, str) and value.strip():
        return tuple(x.strip() for x in value.split("|") if x.strip())
    return ()


def var_dict(dotted: str, locale: str | None = None) -> dict[str, str]:
    value = var_get(dotted, locale)
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v is not None}


def var_frozenset(dotted: str, locale: str | None = None) -> frozenset[str]:
    return frozenset(var_list(dotted, locale))


def clear_variables_cache() -> None:
    load_variables.cache_clear()
