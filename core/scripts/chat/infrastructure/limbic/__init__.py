"""Limbic system: state, emotions, prompt for chat template."""

from infrastructure.limbic.assets import (
    limbic_images_base_url,
    model_limbic_enabled,
    model_limbic_prompt_enabled,
    model_perception_daemon_enabled,
    resolve_limbic_images_dir,
)
from infrastructure.limbic.prompt import render_limbic_prompt, render_limbic_prompt_from_snapshot
from infrastructure.limbic.state import EMOTION_LABELS, LimbicState, format_emotion_vector

__all__ = [
    "EMOTION_LABELS",
    "LimbicState",
    "format_emotion_vector",
    "limbic_images_base_url",
    "model_limbic_enabled",
    "model_limbic_prompt_enabled",
    "model_perception_daemon_enabled",
    "render_limbic_prompt",
    "render_limbic_prompt_from_snapshot",
    "resolve_limbic_images_dir",
]
