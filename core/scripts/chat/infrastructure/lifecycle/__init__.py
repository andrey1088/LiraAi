"""Lira lifecycle: decay, eval, proactive, activity gate."""

from infrastructure.lifecycle.activity_gate import UserActivityGate, get_activity_gate

__all__ = [
    "UserActivityGate",
    "get_activity_gate",
]
