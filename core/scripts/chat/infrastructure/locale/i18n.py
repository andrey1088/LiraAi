"""UI/tools translations (English msgid for UI; keys for tools)."""

from __future__ import annotations

from infrastructure.locale.loader import (
    clear_cache as _clear_loader_cache,
    normalize_locale as _normalize_locale,
    tr_tools as _tr_tools,
    tr_ui as _tr_ui,
)


def tr(msgid: str, locale: str = "ru", *, fallback: str | None = None) -> str:
    return _tr_ui(msgid, locale, fallback=fallback)


def tr_ui_format(
    msgid: str,
    locale: str = "ru",
    *,
    fallback: str | None = None,
    **fmt: str,
) -> str:
    text = tr(msgid, locale, fallback=fallback)
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, ValueError):
        return text


def tr_tools(key: str, locale: str = "ru", *, fallback: str | None = None) -> str:
    return _tr_tools(key, locale, fallback=fallback)


def normalize_locale(locale: str | None) -> str:
    """Compatibility re-export for modules importing from i18n."""
    return _normalize_locale(locale)


def tr_tools_format(
    key: str,
    locale: str = "ru",
    *,
    fallback: str | None = None,
    **fmt: str,
) -> str:
    text = tr_tools(key, locale, fallback=fallback)
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, ValueError):
        return text


def clear_cache() -> None:
    from infrastructure.locale.variables import clear_variables_cache

    _clear_loader_cache()
    clear_variables_cache()
