"""Gallery caption quality: filter junk; language check by ui_locale."""

from __future__ import annotations

import json
import os
import re
from collections import Counter

from workers.model_worker import (
    strip_degenerate_token_runs,
    strip_leading_channel_thought_preamble,
)

from infrastructure.config.defaults import DEFAULT_UI_LOCALE, UI_LOCALES
from infrastructure.locale.variables import var_get

_TECH_MARKERS = (
    "close to end of turn",
    "token id 0 is not available",
    "not available for this model",
    "<start_of_turn",
    "<end_of_turn",
    "<|",
    "ggml",
    "cuda error",
    "tool call",
    "functions.",
    "channel|>",
    "redacted_im_end",
)

_JUNK_PHRASE_MARKERS = (
    "certain way",
    "message:",
    "thought",
)

_MIN_SCRIPT_RATIO = 0.12
_DOMINANT_SCRIPT_RATIO = 0.35


def normalize_gallery_locale(locale: str | None) -> str:
    loc = str(locale or DEFAULT_UI_LOCALE).strip().lower()
    return loc if loc in UI_LOCALES else DEFAULT_UI_LOCALE


def resolve_gallery_description_locale(locale: str | None = None) -> str:
    """ui_locale from arg or config.json (repo/subprocess without ConfigRepository)."""
    if locale is not None:
        return normalize_gallery_locale(locale)
    from infrastructure.paths import config_path

    path = str(config_path())
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return normalize_gallery_locale(data.get("ui_locale"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return DEFAULT_UI_LOCALE


def _cyrillic_letter_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04ff")
    return cyr / len(letters)


def _latin_letter_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    lat = sum(1 for c in letters if ("A" <= c <= "Z") or ("a" <= c <= "z"))
    return lat / len(letters)


def infer_description_locale(text: str | None) -> str:
    """
    Language of an already saved description (repair without current ui_locale).
    Dominant Cyrillic/Latin script; else ui_locale from config.
    """
    t = sanitize_gallery_description(text)
    if len(t) < 8:
        return resolve_gallery_description_locale()
    cyr = _cyrillic_letter_ratio(t)
    lat = _latin_letter_ratio(t)
    if cyr >= _DOMINANT_SCRIPT_RATIO and cyr >= lat:
        return "ru"
    if lat >= _DOMINANT_SCRIPT_RATIO and lat > cyr:
        return "en"
    return resolve_gallery_description_locale()


def _starts_with_template_junk(raw: str) -> bool:
    if raw.startswith("<") or raw.startswith("[{") or raw.startswith("{'"):
        return True
    t = sanitize_gallery_description(raw)
    if not t:
        return False
    return t.lstrip().startswith("<")


def starts_with_english_letters_or_tags(text: str | None) -> bool:
    """
    Description starts with template tags/junk or two Latin letters in a row
    (typical English reply when Russian was expected).
    """
    raw = (text or "").strip()
    if _starts_with_template_junk(raw):
        return True
    t = sanitize_gallery_description(text)
    if not t:
        return False
    return bool(re.match(r"^[A-Za-z]{2}", t.lstrip()))


def starts_with_cyrillic_letters_or_tags(text: str | None) -> bool:
    """
    Starts with tags or two Cyrillic letters (Russian reply when EN was expected).
    """
    raw = (text or "").strip()
    if _starts_with_template_junk(raw):
        return True
    t = sanitize_gallery_description(text)
    if not t:
        return False
    return bool(re.match(r"^[\u0400-\u04FF]{2}", t.lstrip()))


def starts_with_wrong_locale_lead(text: str | None, locale: str) -> bool:
    loc = normalize_gallery_locale(locale)
    if loc == "en":
        return starts_with_cyrillic_letters_or_tags(text)
    return starts_with_english_letters_or_tags(text)


def should_redescribe_gallery_lead(text: str | None, locale: str) -> bool:
    """Reset and regen when description language prefix mismatches ui_locale."""
    return starts_with_wrong_locale_lead(text, locale)


def gallery_describe_retry_intro(locale: str) -> str:
    loc = normalize_gallery_locale(locale)
    if loc == "en":
        return (
            "Describe the image in English for gallery search: who or what is shown, "
            "setting, style. Facts only, 1–3 sentences."
        )
    return str(var_get("gallery.quality_prompt", loc, default="Describe the image briefly."))


def sanitize_gallery_description(text: str | None) -> str:
    if not text:
        return ""
    t = str(text).strip()
    t = re.sub(r"<\|[^|]*\|>", "", t)
    t = re.sub(r"<start_of_turn>|<end_of_turn>", "", t, flags=re.IGNORECASE)
    t = strip_leading_channel_thought_preamble(t)
    t = strip_degenerate_token_runs(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _fails_script_ratio(t: str, locale: str) -> bool:
    if len(t) < 12:
        return False
    loc = normalize_gallery_locale(locale)
    if loc == "en":
        return _latin_letter_ratio(t) < _MIN_SCRIPT_RATIO
    return _cyrillic_letter_ratio(t) < _MIN_SCRIPT_RATIO


def is_bad_gallery_description(text: str | None, locale: str | None = None) -> bool:
    """
    locale: ui_locale when saving a new description.
    None — infer language from text (repair existing DB rows).
    """
    t = sanitize_gallery_description(text)
    loc = infer_description_locale(text) if locale is None else normalize_gallery_locale(locale)
    if starts_with_wrong_locale_lead(text, loc):
        return True
    if len(t) < 8:
        return True
    low = t.lower()
    for marker in _TECH_MARKERS:
        if marker in low:
            return True
    for marker in _JUNK_PHRASE_MARKERS:
        if marker in low and _has_excessive_repetition(t):
            return True
        if marker in low and len(t) < 80:
            return True
    if _has_excessive_repetition(t):
        return True
    if _fails_script_ratio(t, loc):
        return True
    return False


def _has_excessive_repetition(text: str) -> bool:
    low = text.lower()
    words = re.findall(r"[\w']+", low, flags=re.UNICODE)
    if len(words) >= 4:
        _word, cnt = Counter(words).most_common(1)[0]
        if cnt >= 4 and cnt / len(words) >= 0.4:
            return True
    parts = [p.strip() for p in re.split(r"[.!?]+", text) if len(p.strip()) >= 3]
    if len(parts) >= 3:
        _phrase, n = Counter(parts).most_common(1)[0]
        if n >= 3:
            return True
    if re.search(r"(.{3,40})(?:\s*[.,]\s*\1){2,}", text, flags=re.IGNORECASE):
        return True
    stripped = strip_degenerate_token_runs(text)
    if stripped != text and len(stripped) < max(12, len(text) // 3):
        return True
    return False
