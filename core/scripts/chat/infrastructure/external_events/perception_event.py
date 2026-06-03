"""Normalized event for WorldState / rules / proactive."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class PerceptionEvent:
    key: str
    value: Any
    source: str
    priority: str = "normal"  # background | normal | urgent
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def ts_iso(self) -> str:
        return self.ts.astimezone(timezone.utc).replace(microsecond=0).isoformat()
