"""Default persona texts loaded from infrastructure/locale/variables/{locale}.json."""

from __future__ import annotations

from infrastructure.locale.variables import var_get

PERSONA_SCHEMA_VERSION = 2
DEFAULT_LOCALE = "ru"


def default_system_persona(locale: str = DEFAULT_LOCALE) -> str:
    return str(var_get("persona.system_persona", locale) or var_get("persona.system_persona", "en") or "")


def default_additional_instructions(locale: str = DEFAULT_LOCALE) -> list[str]:
    raw = var_get("persona.additional_instructions", locale) or var_get("persona.additional_instructions", "en")
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def default_prompts_bilingual() -> dict[str, dict[str, str]]:
    """All prompt keys with ru/en for new persona documents."""
    ru = var_get("persona.prompts", "ru") or {}
    en = var_get("persona.prompts", "en") or {}
    if not isinstance(ru, dict):
        ru = {}
    if not isinstance(en, dict):
        en = {}
    keys = set(ru) | set(en)
    return {k: {"ru": str(ru.get(k, en.get(k, ""))), "en": str(en.get(k, ""))} for k in keys}
