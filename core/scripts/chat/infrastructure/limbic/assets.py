"""Emotion portrait paths with shared fallback in data/icons/limbic."""

from __future__ import annotations

import os
from pathlib import Path

from infrastructure.limbic.state import EMOTION_LABELS


def _shared_limbic_dir() -> Path | None:
    from infrastructure.paths import lira_data

    candidate = lira_data("icons", "limbic").resolve()
    if not candidate.is_dir():
        return None
    if not (candidate / "neutral.png").is_file():
        return None
    return candidate


def resolve_limbic_images_dir(m_info) -> Path | None:
    raw = getattr(m_info, "limbic_images_path", None) or ""
    if not raw:
        return _shared_limbic_dir()
    directory = Path(os.path.expanduser(str(raw))).resolve()
    if not directory.is_dir():
        return _shared_limbic_dir()
    if not (directory / "neutral.png").is_file():
        return _shared_limbic_dir()
    return directory


def limbic_images_base_url(m_info) -> str | None:
    """file:// catalog URL with trailing slash for <img src>."""
    directory = resolve_limbic_images_dir(m_info)
    if directory is None:
        return None
    return directory.as_uri().rstrip("/") + "/"


def model_limbic_enabled(m_info) -> bool:
    return resolve_limbic_images_dir(m_info) is not None


def model_perception_daemon_enabled(m_info) -> bool:
    """Background perception (decay on start/stop) — models with flag in config only."""
    return bool(getattr(m_info, "perception_daemon", False)) and model_limbic_enabled(m_info)


def model_limbic_prompt_enabled(m_info) -> bool:
    if not model_limbic_enabled(m_info):
        return False
    template = getattr(m_info, "template_path", None) or ""
    if not template:
        return False
    return Path(os.path.expanduser(template)).is_file()


def emotion_image_path(m_info, emotion: str) -> Path | None:
    directory = resolve_limbic_images_dir(m_info)
    if directory is None:
        return None
    key = emotion if emotion in EMOTION_LABELS else "neutral"
    path = directory / f"{key}.png"
    return path if path.is_file() else None
