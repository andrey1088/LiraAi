"""Load persona JSON: system prompt + keyed dynamic prompts (ru/en)."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from infrastructure.persona.defaults import (
    DEFAULT_LOCALE,
    PERSONA_SCHEMA_VERSION,
    default_additional_instructions,
    default_prompts_bilingual,
    default_system_persona,
)


def _locale_dict(ru: str, en: str | None = None) -> dict[str, str]:
    return {"ru": ru, "en": en if en is not None else ru}


def _locale_list(ru: list[str], en: list[str] | None = None) -> dict[str, list[str]]:
    return {"ru": list(ru), "en": list(en if en is not None else ru)}


def default_persona_document() -> dict[str, Any]:
    return {
        "schema_version": PERSONA_SCHEMA_VERSION,
        "locale_default": DEFAULT_LOCALE,
        "system_persona": _locale_dict(
            default_system_persona("ru"),
            default_system_persona("en"),
        ),
        "additional_instructions": _locale_list(
            default_additional_instructions("ru"),
            default_additional_instructions("en"),
        ),
        "prompts": deepcopy(default_prompts_bilingual()),
    }


def pick_locale(value: Any, locale: str, *, fallback: str = DEFAULT_LOCALE) -> Any:
    """String passthrough; dict uses locale keys with ru fallback."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if locale in value and value[locale] not in (None, ""):
            return value[locale]
        if fallback in value and value[fallback] not in (None, ""):
            return value[fallback]
        for v in value.values():
            if v not in (None, ""):
                return v
    return value


def migrate_legacy_persona(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert pre-v2 flat persona to schema_version 2."""
    out = default_persona_document()

    sp = raw.get("system_persona")
    if isinstance(sp, str) and sp.strip():
        out["system_persona"] = _locale_dict(sp.strip(), default_system_persona("en"))
    elif isinstance(sp, dict):
        out["system_persona"] = sp

    instr = raw.get("additional_instructions")
    if isinstance(instr, list):
        out["additional_instructions"] = _locale_list(instr, default_additional_instructions("en"))
    elif isinstance(instr, dict):
        out["additional_instructions"] = instr

    legacy_prompts = raw.get("prompts")
    if isinstance(legacy_prompts, dict):
        merged = deepcopy(default_prompts_bilingual())
        for key, val in legacy_prompts.items():
            if isinstance(val, str):
                merged[key] = _locale_dict(val)
            elif isinstance(val, dict):
                merged[key] = val
        out["prompts"] = merged

    if raw.get("locale_default") in ("ru", "en"):
        out["locale_default"] = raw["locale_default"]

    return out


def normalize_persona(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return default_persona_document()
    if int(raw.get("schema_version") or 0) >= PERSONA_SCHEMA_VERSION:
        doc = default_persona_document()
        # merge missing prompt keys from defaults
        prompts = deepcopy(default_prompts_bilingual())
        prompts.update(raw.get("prompts") or {})
        doc.update(raw)
        doc["prompts"] = prompts
        doc["schema_version"] = PERSONA_SCHEMA_VERSION
        return doc
    return migrate_legacy_persona(raw)


class PersonaStore:
    @staticmethod
    @lru_cache(maxsize=1)
    def load_user_format_vars(config_path: str | None = None) -> dict[str, str]:
        """From config.json `user` block; used when handlers lack ConfigRepository."""
        from infrastructure.paths import config_path as default_config_path

        path = Path(os.path.expanduser(config_path or os.environ.get("LIRA_CONFIG", str(default_config_path()))))
        from infrastructure.locale.runtime_vars import user_format_vars_from_config

        if not path.is_file():
            return user_format_vars_from_config(None, locale="en")
        with path.open(encoding="utf-8") as f:
            cfg = json.load(f)
        return user_format_vars_from_config(cfg, locale="en")

    @staticmethod
    def load_path(persona_path: str | Path) -> dict[str, Any]:
        path = Path(persona_path).expanduser()
        if not path.is_file():
            return default_persona_document()
        with path.open(encoding="utf-8") as f:
            return normalize_persona(json.load(f))

    @staticmethod
    def save_path(persona_path: str | Path, doc: dict[str, Any]) -> None:
        path = Path(persona_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)

    @staticmethod
    def load_and_upgrade(persona_path: str | Path) -> dict[str, Any]:
        path = Path(persona_path).expanduser()
        if not path.is_file():
            doc = default_persona_document()
            PersonaStore.save_path(path, doc)
            return doc
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
        doc = normalize_persona(raw)
        if raw.get("schema_version") != PERSONA_SCHEMA_VERSION:
            PersonaStore.save_path(path, doc)
        return doc

    @staticmethod
    def format_text(text: str, **kwargs: Any) -> str:
        if not text:
            return ""
        out = text
        for key, val in kwargs.items():
            out = out.replace("{" + key + "}", str(val))
        return out

    @classmethod
    def build_system_prompt(
        cls,
        doc: dict[str, Any],
        locale: str | None = None,
        **format_kwargs: Any,
    ) -> str:
        loc = locale or doc.get("locale_default") or DEFAULT_LOCALE
        sp = pick_locale(doc.get("system_persona"), loc) or ""
        sp = cls.format_text(str(sp), **format_kwargs)
        instr_raw = pick_locale(doc.get("additional_instructions"), loc) or []
        parts = [sp.strip()] if sp.strip() else []
        if isinstance(instr_raw, list):
            for line in instr_raw:
                text = cls.format_text(str(line).strip(), **format_kwargs)
                if text:
                    parts.append(text)
        return "\n".join(parts)

    @classmethod
    def get_prompt(
        cls,
        doc: dict[str, Any],
        key: str,
        locale: str | None = None,
        **format_kwargs: Any,
    ) -> str:
        loc = locale or doc.get("locale_default") or DEFAULT_LOCALE
        prompts = doc.get("prompts") or {}
        val = pick_locale(prompts.get(key), loc)
        if val is None:
            val = pick_locale((default_prompts_bilingual().get(key) or {}), loc)
        text = cls.format_text(str(val or ""), **format_kwargs)
        return text

    @classmethod
    def get_prompt_from_file(
        cls,
        persona_path: str | Path | None,
        key: str,
        locale: str | None = None,
        **format_kwargs: Any,
    ) -> str:
        if not persona_path:
            doc = default_persona_document()
        else:
            doc = cls.load_path(persona_path)
        merged = {**cls.load_user_format_vars(), **format_kwargs}
        return cls.get_prompt(doc, key, locale=locale, **merged)
