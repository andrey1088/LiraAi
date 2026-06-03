"""gallery_describe settings from config.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULTS = {
    "max_side": 384,
    "max_tokens": 220,
    "subprocess_n_ctx": 4096,
    "subprocess_chunk_size": 1,
    "cuda_deep_every": 1,
    "gpu_handoff": True,
    "use_subprocess": False,
    # First gallery describe after vision model load/switch — subprocess+handoff (not in-process).
    "force_subprocess_after_switch": True,
}


def load_gallery_describe_settings() -> dict:
    settings = dict(_DEFAULTS)
    try:
        from infrastructure.paths import config_path

        path = config_path()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            gd = data.get("gallery_describe")
            if isinstance(gd, dict):
                settings.update(gd)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return settings
