"""Perception rules: event → proactive reply in Lira chat."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from infrastructure.external_events.perception_event import PerceptionEvent
from infrastructure.external_events.world_state import TELEGRAM_LAST_MESSAGE
from infrastructure.locale.variables import var_get

from infrastructure.paths import lira_root

_RULES_PATH = lira_root() / "config.perception_rules.json"


def proactive_user_prompt_prefix(locale: str = "en") -> str:
    return str(var_get("perception.proactive_prefix", locale) or "[PROACTIVE")


PROACTIVE_USER_PROMPT_PREFIX = proactive_user_prompt_prefix("en")


def is_proactive_internal_user_text(content: str | None) -> bool:
    if not content or not isinstance(content, str):
        return False
    text = content.strip()
    return any(text.startswith(proactive_user_prompt_prefix(loc)) for loc in ("en", "ru"))


def is_telegram_channel_user_content(content: str | None) -> bool:
    """Telegram-channel service user lines — hide from chat and Lira history."""
    if not content or not isinstance(content, str):
        return False
    text = content.strip()
    if is_proactive_internal_user_text(text):
        return True
    if text.startswith("📱 Telegram"):
        return True
    return False


def parse_telegram_id(raw) -> int | None:
    """Telegram chat/user id from JSON (int, str, whole float)."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw == int(raw):
        return int(raw)
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    return None


def telegram_reply_chat_id_from_event(event: PerceptionEvent) -> int | None:
    """chat_id for sendMessage — only message.chat.id from the event."""
    v = event.value if isinstance(event.value, dict) else {}
    return parse_telegram_id(v.get("chat_id"))


def telegram_message_id_from_event(event: PerceptionEvent) -> int | None:
    v = event.value if isinstance(event.value, dict) else {}
    return parse_telegram_id(v.get("message_id"))


def telegram_sender_label(event: PerceptionEvent, locale: str = "ru") -> str:
    """Who messaged the bot — external user_id, not the owner in Lira desktop."""
    v = event.value if isinstance(event.value, dict) else {}
    uid = v.get("from_user_id")
    return str(var_get("perception.stranger_label", locale) or "").format(uid=uid)


def telegram_external_context_block(event: PerceptionEvent, locale: str = "ru") -> str:
    """LLM text: channel and that this is not the owner in desktop chat."""
    v = event.value if isinstance(event.value, dict) else {}
    preview = (v.get("text_preview") or "").strip()
    uid = v.get("from_user_id")
    loc = str(locale or "ru")
    lines = [
        str(var_get("perception.telegram_channel_warning", loc) or ""),
        str(var_get("perception.telegram_stranger_line", loc) or "").format(uid=uid),
        str(var_get("perception.telegram_voice_line", loc) or ""),
    ]
    if preview:
        lines.append(str(var_get("perception.telegram_message_line", loc) or "").format(preview=preview))
    lines.append(str(var_get("perception.telegram_reply_hint", loc) or ""))
    return "\n".join(lines)


def telegram_bot_reply_system_prompt() -> str:
    """Fallback; chat uses persona key telegram_bot_reply from active model."""
    from infrastructure.persona.store import PersonaStore

    return PersonaStore.get_prompt_from_file(None, "telegram_bot_reply")


@dataclass(frozen=True)
class ProactiveJob:
    rule_id: str
    user_prompt: str
    priority: str = "normal"
    telegram_chat_id: int | None = None
    telegram_message_id: int | None = None


@dataclass
class _Rule:
    id: str
    match_source: str | None
    match_key: str | None
    action: str
    cooldown_sec: int
    enabled: bool
    exclude_prefixes: tuple[str, ...]


class PerceptionRuleEngine:
    def __init__(self, rules_path: Path | None = None):
        self._path = Path(os.path.expanduser(rules_path or _RULES_PATH))
        self._rules: list[_Rule] = []
        self._global_cooldown_sec = 90
        self._global_last: datetime | None = None
        self._rule_last: dict[str, datetime] = {}
        self._last_dispatched_telegram_message_id: int | None = None
        self._telegram_dispatch = "legacy_proactive"
        self._perception_eval_tick_sec = 5
        self._load()

    def telegram_world_state_only(self) -> bool:
        """WorldState + eval only, no instant Telegram reply (legacy mode)."""
        return self._telegram_dispatch in ("world_state_only", "phase2")

    def telegram_immediate_reply(self) -> bool:
        """Bot message → immediate proactive; WorldState gets Q&A pair."""
        return self._telegram_dispatch in (
            "immediate_reply",
            "legacy_proactive",
            "immediate",
        )

    def _load(self) -> None:
        data: dict = {}
        if self._path.is_file():
            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f) or {}
            except (json.JSONDecodeError, OSError):
                data = {}
        self._telegram_dispatch = str(data.get("telegram_dispatch", "legacy_proactive")).strip().lower()
        self._perception_eval_tick_sec = max(3, int(data.get("perception_eval_tick_sec", 5)))
        self._global_cooldown_sec = int(data.get("global_cooldown_sec", 90))
        raw_rules = data.get("rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            self._rules = [self._default_telegram_rule()]
            return
        parsed: list[_Rule] = []
        for item in raw_rules:
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            match = item.get("match") or {}
            ex = item.get("exclude_text_prefixes") or []
            if isinstance(ex, str):
                ex = [ex]
            parsed.append(
                _Rule(
                    id=str(item.get("id", "rule")),
                    match_source=match.get("source"),
                    match_key=match.get("key"),
                    action=str(item.get("action", "proactive_chat")),
                    cooldown_sec=int(item.get("cooldown_sec", 120)),
                    enabled=True,
                    exclude_prefixes=tuple(str(x) for x in ex),
                )
            )
        self._rules = parsed or [self._default_telegram_rule()]

    @staticmethod
    def _default_telegram_rule() -> _Rule:
        return _Rule(
            id="telegram_incoming",
            match_source="telegram",
            match_key=TELEGRAM_LAST_MESSAGE,
            action="proactive_chat",
            cooldown_sec=120,
            enabled=True,
            exclude_prefixes=("/",),
        )

    def _cooldown_ok(self, rule: _Rule, now: datetime) -> bool:
        if self._global_last is not None:
            if (now - self._global_last).total_seconds() < self._global_cooldown_sec:
                return False
        last = self._rule_last.get(rule.id)
        if last is not None and (now - last).total_seconds() < rule.cooldown_sec:
            return False
        return True

    def _mark_fired(self, rule: _Rule, now: datetime) -> None:
        self._global_last = now
        self._rule_last[rule.id] = now

    @staticmethod
    def _rule_matches(rule: _Rule, event: PerceptionEvent) -> bool:
        if rule.match_source and event.source != rule.match_source:
            return False
        if rule.match_key and event.key != rule.match_key:
            return False
        if rule.action != "proactive_chat":
            return False
        if isinstance(event.value, dict):
            text = (event.value.get("text_preview") or "").strip()
            for prefix in rule.exclude_prefixes:
                if prefix and text.startswith(prefix):
                    return False
        return True

    def build_user_prompt(self, event: PerceptionEvent, locale: str = "ru") -> str:
        loc = str(locale or "ru")
        ctx = telegram_external_context_block(event, loc)
        return str(var_get("perception.proactive_telegram_prompt", loc) or "").format(
            prefix=proactive_user_prompt_prefix(loc),
            body=ctx,
        )

    @staticmethod
    def _telegram_chat_id(event: PerceptionEvent) -> int | None:
        return telegram_reply_chat_id_from_event(event)

    def make_job(self, rule: _Rule, event: PerceptionEvent) -> ProactiveJob:
        return ProactiveJob(
            rule_id=rule.id,
            user_prompt=self.build_user_prompt(event),
            priority=event.priority,
            telegram_chat_id=self._telegram_chat_id(event),
            telegram_message_id=telegram_message_id_from_event(event),
        )

    def refresh_job(self, job: ProactiveJob, event: PerceptionEvent | None) -> ProactiveJob:
        if event is None or job.rule_id != "telegram_incoming":
            return job
        fresh_cid = self._telegram_chat_id(event)
        fresh_mid = telegram_message_id_from_event(event)
        return ProactiveJob(
            rule_id=job.rule_id,
            user_prompt=self.build_user_prompt(event),
            priority=job.priority,
            telegram_chat_id=fresh_cid if fresh_cid is not None else job.telegram_chat_id,
            telegram_message_id=(fresh_mid if fresh_mid is not None else job.telegram_message_id),
        )

    def can_dispatch(self, rule_id: str, telegram_message_id: int | None = None) -> bool:
        if (
            rule_id == "telegram_incoming"
            and telegram_message_id is not None
            and telegram_message_id != self._last_dispatched_telegram_message_id
        ):
            return True
        now = datetime.now(timezone.utc)
        for rule in self._rules:
            if rule.id == rule_id:
                return self._cooldown_ok(rule, now)
        return True

    def cooldown_wait_sec(self, rule_id: str) -> float:
        now = datetime.now(timezone.utc)
        waits: list[float] = []
        if self._global_last is not None:
            w = self._global_cooldown_sec - (now - self._global_last).total_seconds()
            if w > 0:
                waits.append(w)
        for rule in self._rules:
            if rule.id != rule_id:
                continue
            last = self._rule_last.get(rule.id)
            if last is not None:
                w = rule.cooldown_sec - (now - last).total_seconds()
                if w > 0:
                    waits.append(w)
            break
        return max(waits) if waits else 0.0

    def notify_dispatched(self, rule_id: str, telegram_message_id: int | None = None) -> None:
        now = datetime.now(timezone.utc)
        if rule_id == "telegram_incoming" and telegram_message_id is not None:
            self._last_dispatched_telegram_message_id = telegram_message_id
        for rule in self._rules:
            if rule.id == rule_id:
                self._mark_fired(rule, now)
                return

    def evaluate(self, event: PerceptionEvent) -> ProactiveJob | None:
        for rule in self._rules:
            if not self._rule_matches(rule, event):
                continue
            return self.make_job(rule, event)
        return None
