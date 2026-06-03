"""Evaluate Telegram exchange: model sets should_notify_andrey via tool."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from infrastructure.external_events.perception_event import PerceptionEvent
from infrastructure.external_events.world_state import (
    TELEGRAM_LAST_MESSAGE,
    telegram_exchange_ready,
)
from infrastructure.locale.variables import var_get, var_list
from tools.notify_andrey import TELEGRAM_LIFE_EVAL_TOOL, telegram_life_eval_schema


def perception_life_tools(
    locale: str = "ru",
    *,
    format_vars: dict[str, str] | None = None,
) -> list[dict]:
    return [telegram_life_eval_schema(locale, format_vars=format_vars)]


PERCEPTION_LIFE_TOOLS = perception_life_tools("ru")


@dataclass(frozen=True)
class PerceptionVerdict:
    verdict: str  # ignore | note | act
    telegram_reply: str | None
    notify_andrey: str | None
    reason: str


def build_evaluation_user_prompt(
    event: PerceptionEvent | None,
    locale: str = "ru",
    *,
    format_vars: dict[str, str] | None = None,
) -> str:
    loc = str(locale or "ru")
    if event is None or not isinstance(event.value, dict):
        return str(var_get("perception.world_state_unchanged", loc) or "")

    v = event.value
    if event.key != TELEGRAM_LAST_MESSAGE:
        return str(var_get("perception.event_line", loc) or "").format(key=event.key, value=v)

    if not telegram_exchange_ready(event):
        return str(var_get("perception.telegram_incomplete", loc) or "")

    uid = v.get("from_user_id")
    question = (v.get("text_preview") or "").strip()
    reply = (v.get("reply_preview") or "").strip()
    at = (v.get("at_local") or "").strip()
    when = f" ({at})" if at else ""

    merged = dict(format_vars or {})
    merged.update(
        uid=uid,
        when=when,
        question=question,
        reply=reply,
        tool=TELEGRAM_LIFE_EVAL_TOOL,
    )
    return str(var_get("perception.telegram_exchange", loc) or "").format(**merged)


def build_evaluation_system_prompt(locale: str = "ru") -> str:
    """Fallback when config_repo is unavailable; prefer persona key perception_eval_system."""
    from infrastructure.persona.store import PersonaStore

    loc = str(locale or "ru")
    base = PersonaStore.get_prompt_from_file(None, "perception_eval_system")
    footer = str(var_get("perception.telegram_eval_footer", loc) or "").format(tool=TELEGRAM_LIFE_EVAL_TOOL)
    return f"{base}\n{footer}"


def _parse_bool(value, locale: str = "ru") -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        true_vals = {x.lower() for x in var_list("perception.boolean_true", locale)}
        false_vals = {x.lower() for x in var_list("perception.boolean_false", locale)}
        if low in true_vals:
            return True
        if low in false_vals:
            return False
    return None


def verdict_from_telegram_life_eval_args(args_json: str, locale: str = "ru") -> PerceptionVerdict:
    try:
        data = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        return PerceptionVerdict("ignore", None, None, "bad_json")
    if not isinstance(data, dict):
        return PerceptionVerdict("ignore", None, None, "args_not_object")

    should = _parse_bool(data.get("should_notify_andrey"), locale)
    if should is None:
        return PerceptionVerdict("ignore", None, None, "no_should_flag")

    if not should:
        return PerceptionVerdict("ignore", None, None, "model_no_notify")

    msg = (data.get("message") or data.get("text") or "").strip()
    if not msg:
        # Some models set should_notify_andrey=true but leave message empty.
        # Treat it as notify with a safe fallback instead of dropping the event.
        fallback = (
            "В Telegram попросили передать сообщение владельцу."
            if str(locale or "ru").lower().startswith("ru")
            else "A Telegram user asked to pass a message to the owner."
        )
        return PerceptionVerdict("act", None, fallback, "model_notify_empty_message")

    return PerceptionVerdict("act", None, msg, "model_notify")


def verdict_from_model_answer(full_response: str, locale: str = "ru") -> PerceptionVerdict:
    if full_response.startswith("TOOL_CALL|"):
        parts = full_response.split("|", 3)
        fn_name = parts[1] if len(parts) > 1 else ""
        args_json = parts[2] if len(parts) > 2 else "{}"
        if fn_name in (TELEGRAM_LIFE_EVAL_TOOL, "notify_andrey"):
            return verdict_from_telegram_life_eval_args(args_json, locale)
        return PerceptionVerdict("ignore", None, None, f"unexpected_tool:{fn_name}")

    text = (full_response or "").strip()
    text = re.sub(r"<\|.*?\|>", "", text).strip()
    if not text:
        return PerceptionVerdict("ignore", None, None, "no_tool_call")
    return PerceptionVerdict("ignore", None, None, "no_tool_call")
