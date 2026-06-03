"""Shared paths for model tasks (subprocess, maintenance scripts)."""

from __future__ import annotations

import sys
from pathlib import Path


def lira_project_root() -> Path:
    # infrastructure/model_tasks/paths.py → …/Lira2
    return Path(__file__).resolve().parents[5]


def gallery_describe_subprocess_script() -> Path:
    return lira_project_root() / "core/scripts/maintenance/gallery_describe_subprocess.py"


def python_executable() -> str:
    root = lira_project_root()
    venv_py = root / "venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable
