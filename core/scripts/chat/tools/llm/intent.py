"""user_intent substrings; loaded from infrastructure/locale/variables/{locale}.json."""

from __future__ import annotations

from infrastructure.locale.i18n import normalize_locale
from infrastructure.locale.variables import var_list


def _merge_needles(*locales: str, var_key: str) -> tuple[str, ...]:
    """Union of needles for ui_locale + the other language (users often mix RU/EN text)."""
    seen: set[str] = set()
    out: list[str] = []
    for loc in locales:
        for needle in var_list(var_key, loc):
            key = needle.casefold()
            if key not in seen:
                seen.add(key)
                out.append(needle)
    return tuple(out)


def gallery_intent_fallback(locale: str | None = None) -> tuple[str, ...]:
    loc = normalize_locale(locale)
    other = "en" if loc == "ru" else "ru"
    return _merge_needles(loc, other, var_key="intent.gallery_message_substrings")


def camera_intent_fallback(locale: str | None = None) -> tuple[str, ...]:
    loc = normalize_locale(locale)
    other = "en" if loc == "ru" else "ru"
    return _merge_needles(loc, other, var_key="intent.camera_message_substrings")


def web_intent_fallback(locale: str | None = None) -> tuple[str, ...]:
    loc = normalize_locale(locale)
    other = "en" if loc == "ru" else "ru"
    return _merge_needles(loc, other, var_key="intent.web_message_substrings")


def gallery_memory_block_markers(locale: str | None = None) -> tuple[str, ...]:
    """Substrings in memory_search query that mean «use gallery_search, not memory»."""
    loc = normalize_locale(locale)
    other = "en" if loc == "ru" else "ru"
    return _merge_needles(loc, other, var_key="intent.gallery_memory_block_markers")
