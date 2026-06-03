"""In-memory snapshot of external perception (Telegram, etc.) for sens / eval."""

from __future__ import annotations

import json
from typing import Any

from infrastructure.external_events.perception_event import PerceptionEvent
from infrastructure.locale.variables import var_get

TELEGRAM_LAST_MESSAGE = "telegram.last_message"


def _value_snapshot(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def telegram_exchange_ready(event: PerceptionEvent | None) -> bool:
    """Lifecycle eval needs a pair: Telegram question and bot reply."""
    if event is None or not isinstance(event.value, dict):
        return False
    v = event.value
    question = (v.get("text_preview") or "").strip()
    reply = (v.get("reply_preview") or "").strip()
    return bool(question and reply)


class WorldState:
    def __init__(self) -> None:
        self._entries: dict[str, PerceptionEvent] = {}
        self._evaluated_snapshot: dict[str, str] = {}

    def publish(self, event: PerceptionEvent) -> None:
        self._entries[event.key] = event

    def get(self, key: str) -> PerceptionEvent | None:
        return self._entries.get(key)

    def clear_source(self, source: str) -> None:
        drop = [k for k, e in self._entries.items() if e.source == source]
        for k in drop:
            del self._entries[k]
            self._evaluated_snapshot.pop(k, None)

    def _entry_ready_for_eval(self, key: str, event: PerceptionEvent) -> bool:
        if key == TELEGRAM_LAST_MESSAGE:
            return telegram_exchange_ready(event)
        return True

    def has_unevaluated_changes(self) -> bool:
        for key, event in self._entries.items():
            if not self._entry_ready_for_eval(key, event):
                continue
            snap = _value_snapshot(event.value)
            if self._evaluated_snapshot.get(key) != snap:
                return True
        return False

    def mark_evaluated(self) -> None:
        self._evaluated_snapshot = {
            key: _value_snapshot(event.value)
            for key, event in self._entries.items()
            if self._entry_ready_for_eval(key, event)
        }

    def format_for_sens(self) -> str:
        parts: list[str] = []
        msg = self._entries.get(TELEGRAM_LAST_MESSAGE)
        if msg and isinstance(msg.value, dict):
            v = msg.value
            uid = v.get("from_user_id")
            question = (v.get("text_preview") or "").strip()
            reply = (v.get("reply_preview") or "").strip()
            at = (v.get("at_local") or "").strip()
            when = f" ({at})" if at else ""
            if question and reply:
                q = question if len(question) <= 80 else question[:77] + "…"
                r = reply if len(reply) <= 80 else reply[:77] + "…"
                parts.append(str(var_get("world_state.telegram_pair", "ru") or "").format(uid=uid, when=when, q=q, r=r))
            elif question or uid is not None:
                tail = question if len(question) <= 120 else question[:117] + "…"
                parts.append(
                    str(var_get("world_state.telegram_message", "ru") or "").format(uid=uid, when=when, tail=tail)
                )
        return " ".join(parts)


def get_world_state(window) -> WorldState:
    ws = getattr(window, "_world_state", None)
    if ws is None:
        ws = WorldState()
        window._world_state = ws
    return ws
