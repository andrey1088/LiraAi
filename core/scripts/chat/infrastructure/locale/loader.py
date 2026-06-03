"""Translations: infrastructure/locale/<domain>/<locale>.csv — key column = msgid (English source)."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

LOCALE_ROOT = Path(__file__).resolve().parent

DEFAULT_LOCALE = "ru"
SUPPORTED_LOCALES = frozenset({"ru", "en"})


def normalize_locale(locale: str | None) -> str:
    loc = str(locale or DEFAULT_LOCALE).strip().lower()
    return loc if loc in SUPPORTED_LOCALES else DEFAULT_LOCALE


def domain_csv_path(domain: str, locale: str) -> Path:
    loc = normalize_locale(locale)
    return LOCALE_ROOT / domain / f"{loc}.csv"


@lru_cache(maxsize=16)
def _load_domain_table(domain: str, locale: str) -> dict[str, str]:
    """msgid (key) -> translated string for the given locale file."""
    loc = normalize_locale(locale)
    path = domain_csv_path(domain, loc)
    table: dict[str, str] = {}
    if not path.is_file():
        return table
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        col = loc if loc in (reader.fieldnames or []) else loc
        for row in reader:
            msgid = (row.get("key") or "").strip()
            if not msgid:
                continue
            val = (row.get(col) or row.get(loc) or "").strip()
            if val:
                table[msgid] = val
    return table


def tr(
    domain: str,
    msgid: str,
    locale: str = DEFAULT_LOCALE,
    *,
    fallback: str | None = None,
) -> str:
    text = (msgid or "").strip()
    if not text:
        return fallback or ""
    loc = normalize_locale(locale)
    if loc == "en":
        return text
    translated = _load_domain_table(domain, loc).get(text)
    if translated:
        return translated
    return fallback if fallback is not None else text


def tr_ui(msgid: str, locale: str = DEFAULT_LOCALE, *, fallback: str | None = None) -> str:
    return tr("ui", msgid, locale, fallback=fallback)


def tr_tools(key: str, locale: str = DEFAULT_LOCALE, *, fallback: str | None = None) -> str:
    """Tools/policies: stable keys tools.* / policies.* — always read tools/{locale}.csv."""
    text = (key or "").strip()
    if not text:
        return fallback or ""
    loc = normalize_locale(locale)
    translated = _load_domain_table("tools", loc).get(text)
    if translated:
        return translated
    if loc != "en":
        translated = _load_domain_table("tools", "en").get(text)
        if translated:
            return translated
    return fallback if fallback is not None else text


def clear_cache() -> None:
    _load_domain_table.cache_clear()
