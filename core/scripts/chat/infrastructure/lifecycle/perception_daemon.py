"""
Background Lira “life”: limbic decay, Telegram, proactive chat.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from PyQt6.QtCore import QTimer

from infrastructure.external_events.perception_event import PerceptionEvent
from infrastructure.external_events.perception_rules import (
    PerceptionRuleEngine,
    ProactiveJob,
    parse_telegram_id,
    telegram_reply_chat_id_from_event,
)
from infrastructure.external_events.telegram_bot_thread import TelegramBotThread
from infrastructure.external_events.telegram_config import load_telegram_config
from infrastructure.external_events.world_state import TELEGRAM_LAST_MESSAGE, get_world_state
from infrastructure.lifecycle.activity_gate import get_activity_gate
from infrastructure.limbic.assets import model_perception_daemon_enabled
from infrastructure.locale.variables import var_get, var_list

# One step toward baseline every 20 minutes (while Lira active or catch-up on start)
DECAY_STEP_INTERVAL_SEC = 20 * 60
DECAY_STEP_INTERVAL_MS = DECAY_STEP_INTERVAL_SEC * 1000
_PROACTIVE_TICK_MS = 2500
_EVAL_LOG = "[PERCEPTION-EVAL]"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_utc_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        text = raw.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def intervals_for_elapsed(elapsed_sec: float) -> int:
    if elapsed_sec < DECAY_STEP_INTERVAL_SEC:
        return 0
    return int(elapsed_sec // DECAY_STEP_INTERVAL_SEC)


def format_elapsed(elapsed_sec: float, locale: str = "ru") -> str:
    from infrastructure.locale.variables import var_get

    total_min = max(0, int(elapsed_sec // 60))
    hours, minutes = divmod(total_min, 60)
    loc = str(locale or "ru")
    if hours and minutes:
        return str(var_get("perception.duration_hours_min", loc) or "").format(hours=hours, minutes=minutes)
    if hours:
        return str(var_get("perception.duration_hours", loc) or "").format(hours=hours)
    if minutes:
        return str(var_get("perception.duration_minutes", loc) or "").format(minutes=minutes)
    return str(var_get("perception.duration_under_minute", loc) or "")


def format_absence_summary(elapsed_sec: float, locale: str = "ru") -> str:
    from infrastructure.locale.variables import var_get

    if elapsed_sec < DECAY_STEP_INTERVAL_SEC:
        return ""
    dur = format_elapsed(elapsed_sec, locale)
    return str(var_get("perception.idle_since", locale) or "").format(dur=dur)


def _log(msg: str) -> None:
    print(f"[PERCEPTION] {msg}", file=sys.stderr, flush=True)


def _proactive_log(msg: str) -> None:
    print(f"[PROACTIVE] {msg}", file=sys.stderr, flush=True)


class PerceptionDaemon:
    def __init__(self, window):
        self.window = window
        self._running = False
        self.absence_summary: str | None = None
        self._decay_timer = QTimer(window)
        self._decay_timer.setInterval(DECAY_STEP_INTERVAL_MS)
        self._decay_timer.timeout.connect(self._on_decay_tick)
        self._proactive_timer = QTimer(window)
        self._proactive_timer.setInterval(_PROACTIVE_TICK_MS)
        self._proactive_timer.timeout.connect(self._try_dispatch_proactive)
        self._eval_timer = QTimer(window)
        self._eval_timer.timeout.connect(self._on_eval_tick)
        self._telegram_thread: TelegramBotThread | None = None
        self._rule_engine = PerceptionRuleEngine()
        self._pending_proactive: list[ProactiveJob] = []
        self._active_proactive_job: ProactiveJob | None = None

    def is_running(self) -> bool:
        return self._running

    def _limbic(self):
        cc = self.window.chat_controller
        if not hasattr(cc, "limbic_state"):
            return None
        return cc.limbic_state

    def _apply_one_decay_step(self, reason: str) -> bool:
        ls = self._limbic()
        if ls is None:
            return False
        if ls.is_at_baseline():
            return False
        before = ls.top_label()
        ls.step_toward_baseline()
        self.window.sync_limbic_to_db()
        self.window.notify_limbic_emotion()
        after = ls.top_label()
        _log(f"decay ({reason}): {before} -> {after}")
        return True

    def is_user_active(self) -> bool:
        return get_activity_gate(self.window).is_user_active()

    def _on_decay_tick(self) -> None:
        if not self._running:
            return
        if self.is_user_active():
            return
        self._apply_one_decay_step("timer")
        self._try_dispatch_proactive()

    def _catch_up_after_stop(self) -> tuple[float, int]:
        ls = self._limbic()
        if ls is None:
            return 0.0, 0

        stopped_raw = self.window.repository.get_perception_stopped_at()
        stopped_at = _parse_utc_iso(stopped_raw)
        if stopped_at is None:
            return 0.0, 0

        now = datetime.now(timezone.utc)
        elapsed_sec = max(0.0, (now - stopped_at).total_seconds())
        max_steps = intervals_for_elapsed(elapsed_sec)
        if max_steps <= 0:
            return elapsed_sec, 0

        self.window.sync_limbic_from_db()
        applied = ls.decay_until_baseline(max_steps)
        if applied > 0:
            self.window.sync_limbic_to_db()
            self.window.notify_limbic_emotion()
        return elapsed_sec, applied

    def _enqueue_proactive(self, job) -> None:
        self._pending_proactive = [j for j in self._pending_proactive if j.rule_id != job.rule_id]
        self._pending_proactive.append(job)
        _proactive_log(f"queued rule={job.rule_id} queue_len={len(self._pending_proactive)}")

    def _refresh_pending_job(self, job):
        if job.rule_id == "telegram_incoming":
            event = get_world_state(self.window).get(TELEGRAM_LAST_MESSAGE)
            job = self._rule_engine.refresh_job(job, event)
        return job

    def _on_perception_event(self, event) -> None:
        get_world_state(self.window).publish(event)
        if event.source == "telegram" and self._rule_engine.telegram_world_state_only():
            uid = preview = ""
            if isinstance(event.value, dict):
                uid = event.value.get("from_user_id", "")
                preview = (event.value.get("text_preview") or "")[:60]
            _log(f"world_state telegram user_id={uid} preview={preview!r} (eval only, no immediate reply)")
            return
        job = self._rule_engine.evaluate(event)
        if job:
            if job.rule_id == "telegram_incoming":
                self.window.interrupt_voice()
                uid = preview = ""
                if isinstance(event.value, dict):
                    uid = event.value.get("from_user_id", "")
                    preview = (event.value.get("text_preview") or "")[:60]
                _log(f"telegram user_id={uid} preview={preview!r} → proactive (immediate)")
            self._enqueue_proactive(job)
        self._try_dispatch_proactive()

    def world_state_needs_evaluation(self) -> bool:
        return get_world_state(self.window).has_unevaluated_changes()

    def _eval_tick_ms(self) -> int:
        return int(self._rule_engine._perception_eval_tick_sec) * 1000

    def _run_evaluation_if_ready(self) -> bool:
        if not self._running:
            return False
        if not self.world_state_needs_evaluation():
            return False
        gate = get_activity_gate(self.window)
        if gate.is_dialog_busy():
            return False
        cc = self.window.chat_controller
        if cc._llm_inference_busy():
            return False
        if hasattr(cc, "run_perception_evaluation") and cc.run_perception_evaluation():
            _log("eval started (life tick)")
            return True
        reason = cc.perception_eval_block_reason() if hasattr(cc, "perception_eval_block_reason") else "busy"
        print(f"{_EVAL_LOG} tick skipped ({reason})", file=sys.stderr, flush=True)
        return False

    def _on_eval_tick(self) -> None:
        if not self._running:
            return
        self._run_evaluation_if_ready()

    def apply_perception_verdict(self, verdict) -> None:
        from infrastructure.external_events.world_state import get_world_state
        from infrastructure.lifecycle.perception_eval import PerceptionVerdict

        if not isinstance(verdict, PerceptionVerdict):
            return
        ws = get_world_state(self.window)
        print(
            f"{_EVAL_LOG} verdict={verdict.verdict} reason={verdict.reason!r} notify={bool(verdict.notify_andrey)}",
            file=sys.stderr,
            flush=True,
        )
        if verdict.verdict in ("ignore", "note"):
            ws.mark_evaluated()
            return
        if verdict.notify_andrey:
            self._notify_andrey_in_chat(verdict.notify_andrey)
        ws.mark_evaluated()

    def send_telegram_message(self, chat_id: int, text: str) -> None:
        thread = self._telegram_thread
        body = (text or "").strip()
        if thread is None or not body:
            return
        thread.send_reply.emit(int(chat_id), body)
        _proactive_log(f"telegram reply chat_id={chat_id} len={len(body)}")

    def _notify_andrey_in_chat(self, text: str) -> None:
        from core.scripts.chat.domain.message import Message

        body = (text or "").strip()
        if not body:
            return
        for prefix in var_list("perception.external_event_prefixes", "ru") + var_list(
            "perception.external_event_prefixes", "en"
        ):
            if body.startswith(prefix):
                body = body[len(prefix) :].strip()
                break
        sc = self.window.session_controller
        repo = self.window.repository
        sc.ensure_session()
        line = str(var_get("perception.external_event_line", "ru") or "").format(body=body)
        self.window.inject_message("model", line)
        repo.add_chat_message(sc.current_session_id, "model", line)
        sc.history.append(Message(role="assistant", content=line))
        self.window.voice_controller.speak_message(line)

    def _try_dispatch_proactive(self) -> None:
        if not self._running or not self._pending_proactive:
            return
        cc = self.window.chat_controller
        if not hasattr(cc, "run_proactive"):
            return
        if not cc.can_run_proactive():
            reason = cc.proactive_block_reason() if hasattr(cc, "proactive_block_reason") else "busy"
            _proactive_log(f"waiting ({reason}) queue_len={len(self._pending_proactive)}")
            return
        job = self._refresh_pending_job(self._pending_proactive[0])
        self._pending_proactive[0] = job
        if not self._rule_engine.can_dispatch(job.rule_id, telegram_message_id=job.telegram_message_id):
            wait = int(self._rule_engine.cooldown_wait_sec(job.rule_id) + 0.5)
            if wait > 0:
                _proactive_log(
                    f"waiting (cooldown ~{wait}s) rule={job.rule_id} queue_len={len(self._pending_proactive)}"
                )
            return
        job = self._pending_proactive.pop(0)
        if cc.run_proactive(job):
            self._active_proactive_job = job
            self._rule_engine.notify_dispatched(job.rule_id, telegram_message_id=job.telegram_message_id)
            _proactive_log(f"started rule={job.rule_id}")
        else:
            self._pending_proactive.insert(0, job)

    def _telegram_reply_chat_id(self) -> int | None:
        event = get_world_state(self.window).get(TELEGRAM_LAST_MESSAGE)
        if event is not None:
            cid = telegram_reply_chat_id_from_event(event)
            if cid is not None:
                return cid
        job = self._active_proactive_job
        if job and job.telegram_chat_id:
            return int(job.telegram_chat_id)
        return None

    def deliver_telegram_reply(self, text: str) -> None:
        from infrastructure.external_events.perception_rules import (
            PROACTIVE_USER_PROMPT_PREFIX,
            is_proactive_internal_user_text,
        )

        job = self._active_proactive_job
        if job is None:
            return
        chat_id = self._telegram_reply_chat_id()
        if not chat_id:
            _proactive_log("telegram reply skipped: no chat_id")
            return
        event = get_world_state(self.window).get(TELEGRAM_LAST_MESSAGE)
        if event and isinstance(event.value, dict):
            uid = parse_telegram_id(event.value.get("from_user_id"))
            if uid is not None and uid != chat_id:
                _proactive_log(f"telegram reply: chat_id={chat_id} from_user_id={uid} (private chats should match)")
        job_cid = job.telegram_chat_id
        if job_cid and int(job_cid) != int(chat_id):
            _proactive_log(f"telegram reply: using chat_id={chat_id} (job had {job_cid})")
        thread = self._telegram_thread
        if thread is None:
            return
        body = (text or "").strip()
        if is_proactive_internal_user_text(body) or body.startswith(PROACTIVE_USER_PROMPT_PREFIX):
            body = str(var_get("perception.telegram_notice", "ru") or "")
        if not body:
            return
        thread.send_reply.emit(chat_id, body)
        _proactive_log(f"telegram reply chat_id={chat_id} len={len(body)}")
        self._record_telegram_exchange(body)

    def _record_telegram_exchange(self, reply_text: str) -> None:
        ws = get_world_state(self.window)
        event = ws.get(TELEGRAM_LAST_MESSAGE)
        if event is None or not isinstance(event.value, dict):
            return
        body = (reply_text or "").strip()
        if not body:
            return
        v = dict(event.value)
        v["reply_preview"] = body.replace("\n", " ")[:500]
        ws.publish(
            PerceptionEvent(
                key=TELEGRAM_LAST_MESSAGE,
                value=v,
                source="telegram",
                priority=event.priority,
            )
        )
        _log(
            f"world_state exchange user_id={v.get('from_user_id')!r} "
            f"q={((v.get('text_preview') or '')[:40])!r} → pending life-eval"
        )

    def clear_active_proactive_job(self) -> None:
        self._active_proactive_job = None

    def start(self) -> None:
        m_info = self.window.model_controller.get_active_model_info()
        if not model_perception_daemon_enabled(m_info):
            return
        if self._running:
            return
        if self._limbic() is None:
            return

        elapsed_sec, applied = self._catch_up_after_stop()
        summary = format_absence_summary(elapsed_sec)
        self.absence_summary = summary or None

        self._running = True
        self._decay_timer.start()
        self._proactive_timer.start()
        self._eval_timer.setInterval(self._eval_tick_ms())
        self._eval_timer.start()
        _log(f"life eval timer {self._rule_engine._perception_eval_tick_sec}s")
        self._start_telegram()
        _log(
            f"start model={m_info.name!r} elapsed_sec={elapsed_sec:.0f} "
            f"catchup_steps={applied} timer_interval_sec={DECAY_STEP_INTERVAL_SEC} "
            f"absence={bool(summary)}"
        )

    def _start_telegram(self) -> None:
        self._stop_telegram()
        cfg = load_telegram_config()
        if not cfg.is_runnable:
            if cfg.enabled and not cfg.bot_token:
                _log("telegram: no token (set TELEGRAM_BOT_TOKEN in $LIRA_ROOT/.env)")
            return
        if cfg.allowed_user_ids_misconfigured:
            _log(
                "telegram: TELEGRAM_ALLOWED_USER_IDS set but ids not parsed (no brackets: 6032363684, not [6032363684]) — all messages rejected"
            )
        elif not cfg.allowed_user_ids:
            _log(
                "telegram: allowed_user_ids empty — accepting all; for a private bot set TELEGRAM_ALLOWED_USER_IDS=your_user_id"
            )
        else:
            _log(f"telegram: allowed_user_ids={sorted(cfg.allowed_user_ids)}")
        self._telegram_thread = TelegramBotThread(cfg, parent=self.window)
        self._telegram_thread.event_received.connect(self._on_perception_event)
        self._telegram_thread.start()

    def _stop_telegram(self) -> None:
        thread = self._telegram_thread
        self._telegram_thread = None
        if thread is None:
            return
        try:
            thread.event_received.disconnect(self._on_perception_event)
        except (TypeError, RuntimeError):
            pass
        thread.request_stop()
        if thread.isRunning():
            thread.wait(8000)
        get_world_state(self.window).clear_source("telegram")

    def stop(self) -> None:
        self._decay_timer.stop()
        self._proactive_timer.stop()
        self._eval_timer.stop()
        self._pending_proactive.clear()
        self._active_proactive_job = None
        self._stop_telegram()
        cc = self.window.chat_controller
        self._active_proactive_job = None
        if hasattr(cc, "cancel_proactive"):
            cc.cancel_proactive()
        if hasattr(cc, "cancel_perception_evaluation"):
            cc.cancel_perception_evaluation()
        m_info = self.window.model_controller.get_active_model_info()
        enabled = model_perception_daemon_enabled(m_info)
        if enabled:
            try:
                self.window.repository.set_perception_stopped_at(_utc_now_iso())
            except Exception as e:
                _log(f"stop: failed to save timestamp: {e}")
        if self._running or enabled:
            _log(f"stop model={m_info.name!r} saved_ts={enabled}")
        self._running = False
        self.absence_summary = None

    def consume_absence_summary(self) -> str | None:
        text = (self.absence_summary or "").strip()
        self.absence_summary = None
        return text or None


def get_perception_daemon(window) -> PerceptionDaemon:
    daemon = getattr(window, "_perception_daemon", None)
    if daemon is None:
        daemon = PerceptionDaemon(window)
        window._perception_daemon = daemon
    return daemon
