"""Lightweight Qwen Image Edit log (stdlib only — no torch/diffusers at import)."""

from __future__ import annotations

from datetime import datetime

from infrastructure.log.paths import qwen_image_edit_log_path


def qwen_diag_log_path() -> str:
    return str(qwen_image_edit_log_path())


def qwen_diag_append(msg: str) -> None:
    """Append line to ~/Lira2/logs/qwen_image_edit.log."""
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n"
    try:
        with open(qwen_image_edit_log_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
