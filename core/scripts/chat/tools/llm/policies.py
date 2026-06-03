"""Localize strings in tool_policies.json (denials, system append, hints)."""

from __future__ import annotations

import copy

from infrastructure.locale.i18n import normalize_locale, tr_tools
from tools.llm.intent import (
    camera_intent_fallback,
    gallery_intent_fallback,
    web_intent_fallback,
)


def _maybe_tr(key: str, locale: str, original: str) -> str:
    translated = tr_tools(key, locale, fallback=None)
    if translated and translated != key:
        return translated
    return original


def _apply_user_intent_from_variables(reg: dict, locale: str) -> None:
    ui = reg.setdefault("user_intent", {})
    loaders = (
        ("gallery_message_substrings", gallery_intent_fallback),
        ("camera_message_substrings", camera_intent_fallback),
        ("web_message_substrings", web_intent_fallback),
    )
    for key, loader in loaders:
        needles = loader(locale)
        if needles:
            ui[key] = list(needles)


def localize_tool_policy_registry(registry: dict, locale: str) -> dict:
    """Deep copy of registry with texts substituted from infrastructure/locale/tools/."""
    loc = normalize_locale(locale)
    reg = copy.deepcopy(registry)
    _apply_user_intent_from_variables(reg, loc)

    for block in reg.get("system_policy_appends") or []:
        if not isinstance(block, dict):
            continue
        bid = str(block.get("id") or "unknown")
        orig = str(block.get("text") or "")
        block["text"] = _maybe_tr(f"policies.system.{bid}.text", loc, orig)

    tools_pol = reg.get("tools") or {}
    if not isinstance(tools_pol, dict):
        return reg

    for name, pol in tools_pol.items():
        if not isinstance(pol, dict):
            continue
        prefix = f"policies.tools.{name}"
        if isinstance(pol.get("forbidden_if_last_hint"), str):
            pol["forbidden_if_last_hint"] = _maybe_tr(
                f"{prefix}.forbidden_if_last_hint",
                loc,
                pol["forbidden_if_last_hint"],
            )
        extras = pol.get("extras")
        if not isinstance(extras, dict):
            continue
        for ek, ev in list(extras.items()):
            if isinstance(ev, str) and ev.strip():
                extras[ek] = _maybe_tr(f"{prefix}.extras.{ek}", loc, ev)
    return reg
