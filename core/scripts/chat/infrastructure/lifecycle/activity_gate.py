"""
User activity signals for perception.

is_dialog_busy — typing or chat reply in progress (WorldState / Telegram eval waits on this).
is_user_active — same + 5 min after last message (legacy proactive “do not wake owner”).
"""

from __future__ import annotations

from datetime import datetime, timezone

# Still “in dialog” for N seconds after the last user message.
IDLE_AFTER_LAST_MESSAGE_SEC = 300


class UserActivityGate:
    def __init__(self, idle_after_message_sec: float = IDLE_AFTER_LAST_MESSAGE_SEC):
        self.idle_after_message_sec = float(idle_after_message_sec)
        self._user_typing = False
        self._worker_busy = False
        self._last_user_message_at: datetime | None = None

    def touch_user_message(self) -> None:
        self._last_user_message_at = datetime.now(timezone.utc)

    def set_user_typing(self, active: bool) -> None:
        self._user_typing = bool(active)

    def set_worker_busy(self, busy: bool) -> None:
        self._worker_busy = bool(busy)

    def is_dialog_busy(self) -> bool:
        """Dialog active: typing or model replying in chat."""
        return self._user_typing or self._worker_busy

    def is_user_active(self) -> bool:
        """Legacy proactive: do not wake while user is “in dialog” (+ 5 min after last reply)."""
        if self.is_dialog_busy():
            return True
        if self._last_user_message_at is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self._last_user_message_at).total_seconds()
        return elapsed < self.idle_after_message_sec

    def status_snapshot(self) -> dict:
        """For logs / debugging."""
        elapsed = None
        if self._last_user_message_at is not None:
            elapsed = (datetime.now(timezone.utc) - self._last_user_message_at).total_seconds()
        return {
            "active": self.is_user_active(),
            "typing": self._user_typing,
            "worker_busy": self._worker_busy,
            "sec_since_last_message": elapsed,
        }


def get_activity_gate(window) -> UserActivityGate:
    gate = getattr(window, "activity_gate", None)
    if gate is None:
        gate = UserActivityGate()
        window.activity_gate = gate
    return gate
