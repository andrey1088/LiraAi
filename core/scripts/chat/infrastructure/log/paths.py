"""Lira log directory under install root."""

from __future__ import annotations

from pathlib import Path

from infrastructure.paths import lira_root

_LOG_DIR: Path | None = None


def logs_dir() -> Path:
    global _LOG_DIR
    if _LOG_DIR is None:
        _LOG_DIR = lira_root() / "logs"
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def lira_session_log_path() -> Path:
    """Main session log (print, stderr, traceback). Truncated on gui startup."""
    return logs_dir() / "lira.log"


def qwen_image_edit_log_path() -> Path:
    return logs_dir() / "qwen_image_edit.log"


def camera_capture_log_path() -> Path:
    return logs_dir() / "camera_capture.log"
