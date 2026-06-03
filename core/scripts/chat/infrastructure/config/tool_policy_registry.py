"""
Tool policy registry: load from JSON and merge into base schema.

Default path: infrastructure/config/tool_policies.json
Override: LIRA_TOOL_POLICIES_JSON env var (absolute or ~ path to file).
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

# Match legacy ChatController names — stripped before API (see _strip_tool_schema_meta).
TOOL_SCHEMA_META_PREFIX = "x_lira_"
TOOL_FOLLOWUP_TOOLS_KEY = "x_lira_followup_tools"
TOOL_ONLY_AT_CHAIN_STEPS_KEY = "x_lira_only_at_chain_steps"
TOOL_FORBIDDEN_IF_LAST_TOOL_KEY = "x_lira_forbidden_if_last_tool"
TOOL_FORBIDDEN_IF_LAST_HINT_KEY = "x_lira_forbidden_if_last_tool_hint"

_JSON_TO_SCHEMA_META = {
    "followup_tools": TOOL_FOLLOWUP_TOOLS_KEY,
    "only_at_chain_steps": TOOL_ONLY_AT_CHAIN_STEPS_KEY,
    "forbidden_if_last_tool": TOOL_FORBIDDEN_IF_LAST_TOOL_KEY,
    "forbidden_if_last_hint": TOOL_FORBIDDEN_IF_LAST_HINT_KEY,
}

_DEFAULT_POLICY_PATH = Path(__file__).resolve().parent / "tool_policies.json"


def _policy_tools_text(key: str, locale: str) -> str:
    """Tool-policy denial/hint text for model and UI (never hardcode locale in Python)."""
    from infrastructure.locale.i18n import normalize_locale, tr_tools

    loc = normalize_locale(locale)
    text = tr_tools(key, loc)
    if text and text != key:
        return text
    if loc != "en":
        text = tr_tools(key, "en")
        if text and text != key:
            return text
    return key


def resolve_tool_policies_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    env = (os.environ.get("LIRA_TOOL_POLICIES_JSON") or "").strip()
    if env:
        p = Path(os.path.expanduser(env))
        if p.is_file():
            return p
        print(f"[tool_policy] LIRA_TOOL_POLICIES_JSON={p!s} not found — using built-in path")
    return _DEFAULT_POLICY_PATH


def load_tool_policy_registry(path: Path | None = None) -> dict:
    p = resolve_tool_policies_path(path)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("tool_policies.json: root must be an object")
    return data


def merge_policies_into_tool_schema(base_schema: list[dict], registry: dict) -> list:
    """Deep copy base_schema + x_lira_* fields from registry['tools']."""
    out = copy.deepcopy(base_schema)
    by_name = {e["function"]["name"]: e for e in out}
    tools_pol = registry.get("tools") or {}
    for name, pol in tools_pol.items():
        if not isinstance(pol, dict):
            continue
        entry = by_name.get(name)
        if entry is None:
            print(f"[tool_policy] unknown tool {name!r} in JSON — skip")
            continue
        for jkey, meta_key in _JSON_TO_SCHEMA_META.items():
            if jkey not in pol or pol[jkey] is None:
                continue
            val = pol[jkey]
            if jkey == "only_at_chain_steps":
                entry[meta_key] = tuple(int(x) for x in val) if isinstance(val, list) else ()
            elif jkey == "forbidden_if_last_hint":
                entry[meta_key] = val if isinstance(val, str) else str(val)
            elif isinstance(val, list):
                entry[meta_key] = tuple(val)
            else:
                entry[meta_key] = val
        extras = pol.get("extras")
        if isinstance(extras, dict):
            for ek, ev in extras.items():
                entry[f"{TOOL_SCHEMA_META_PREFIX}{ek}"] = ev
    return out


def chain_limits(registry: dict) -> dict:
    return registry.get("chain_limits") or {}


def system_policy_append_texts(registry: dict) -> tuple[str, ...]:
    blocks = registry.get("system_policy_appends") or []
    out: list[str] = []
    for b in blocks:
        if isinstance(b, dict):
            t = (b.get("text") or "").strip()
            if t:
                out.append(t)
        elif isinstance(b, str) and b.strip():
            out.append(b.strip())
    return tuple(out)


def gallery_intent_substrings(registry: dict) -> tuple[str, ...]:
    ui = registry.get("user_intent") or {}
    raw = ui.get("gallery_message_substrings")
    if isinstance(raw, list):
        return tuple(str(x) for x in raw if str(x).strip())
    return ()


def camera_intent_substrings(registry: dict) -> tuple[str, ...]:
    ui = registry.get("user_intent") or {}
    raw = ui.get("camera_message_substrings")
    if isinstance(raw, list):
        return tuple(str(x) for x in raw if str(x).strip())
    return ()


def web_intent_substrings(registry: dict) -> tuple[str, ...]:
    ui = registry.get("user_intent") or {}
    raw = ui.get("web_message_substrings")
    if isinstance(raw, list):
        return tuple(str(x) for x in raw if str(x).strip())
    return ()


def orphan_web_fetch_refusal(
    schema_entry: dict | None,
    web_research_touched: bool,
    *,
    locale: str,
) -> str | None:
    """When web_fetch_url schema sets extras.requires_web_research_touched_this_turn."""
    if not schema_entry:
        return None
    if not schema_entry.get(f"{TOOL_SCHEMA_META_PREFIX}requires_web_research_touched_this_turn"):
        return None
    if web_research_touched:
        return None
    return _policy_tools_text(
        "policies.tools.web_fetch_url.extras.orphan_refusal_message",
        locale,
    )


def memory_search_gallery_redirect_refusal(
    *,
    has_gallery_intent: bool,
    query: str,
    locale: str,
) -> str | None:
    """Block memory_search when the user or query clearly targets saved images."""
    if not has_gallery_intent:
        from tools.llm.intent import gallery_memory_block_markers

        q = (query or "").casefold()
        if not any(m.casefold() in q for m in gallery_memory_block_markers(locale)):
            return None
    return _policy_tools_text(
        "policies.tools.memory_search.extras.gallery_redirect_refusal_message",
        locale,
    )


def gallery_search_user_intent_refusal(
    schema_entry: dict | None,
    *,
    has_gallery_intent: bool,
    locale: str,
) -> str | None:
    """
    Block gallery_search when extras requires_user_gallery_intent is set
    but the current user message has no explicit gallery/image request (see user_intent in JSON).
    """
    if has_gallery_intent:
        return None
    if not schema_entry:
        return None
    if not schema_entry.get(f"{TOOL_SCHEMA_META_PREFIX}requires_user_gallery_intent"):
        return None
    return _policy_tools_text(
        "policies.tools.gallery_search.extras.no_gallery_intent_refusal_message",
        locale,
    )


def web_search_user_intent_refusal(
    schema_entry: dict | None,
    *,
    has_web_intent: bool,
    locale: str,
) -> str | None:
    """
    Block web_search when extras requires_user_web_intent is set
    but the current user message has no explicit web/network request (see user_intent in JSON).
    """
    if has_web_intent:
        return None
    if not schema_entry:
        return None
    if not schema_entry.get(f"{TOOL_SCHEMA_META_PREFIX}requires_user_web_intent"):
        return None
    return _policy_tools_text(
        "policies.tools.web_search.extras.no_web_intent_refusal_message",
        locale,
    )


def gallery_web_suppress_config(
    schema_entry: dict | None,
    *,
    locale: str,
) -> tuple[bool, str | None]:
    """(enabled, denial text) to suppress gallery after web without user intent."""
    return _web_suppress_without_visual_intent(
        schema_entry,
        flag_key="suppress_if_web_without_user_gallery_intent",
        refusal_key="policies.tools.gallery_search.extras.suppress_refusal_message",
        locale=locale,
    )


def camera_capture_user_intent_refusal(
    schema_entry: dict | None,
    *,
    has_camera_intent: bool,
    locale: str,
) -> str | None:
    if has_camera_intent:
        return None
    if not schema_entry:
        return None
    if not schema_entry.get(f"{TOOL_SCHEMA_META_PREFIX}requires_user_camera_intent"):
        return None
    return _policy_tools_text(
        "policies.tools.camera_capture.extras.no_camera_intent_refusal_message",
        locale,
    )


def camera_web_suppress_config(
    schema_entry: dict | None,
    *,
    locale: str,
) -> tuple[bool, str | None]:
    """Suppress camera_capture after web without explicit camera request."""
    return _web_suppress_without_visual_intent(
        schema_entry,
        flag_key="suppress_if_web_without_user_camera_intent",
        refusal_key="policies.tools.camera_capture.extras.suppress_refusal_message",
        locale=locale,
    )


def _web_suppress_without_visual_intent(
    schema_entry: dict | None,
    *,
    flag_key: str,
    refusal_key: str,
    locale: str,
) -> tuple[bool, str | None]:
    if not schema_entry:
        return False, None
    if not schema_entry.get(f"{TOOL_SCHEMA_META_PREFIX}{flag_key}"):
        return False, None
    return True, _policy_tools_text(refusal_key, locale)


def tool_forbidden_if_last_hint(fn_name: str, locale: str) -> str:
    """Localized hint when a tool is blocked because of the previous tool in the chain."""
    return _policy_tools_text(f"policies.tools.{fn_name}.forbidden_if_last_hint", locale)


def strip_tool_schema_meta(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if not k.startswith(TOOL_SCHEMA_META_PREFIX)}
