"""Install root and data paths (multi-directory installs; not hardcoded ~/Lira2)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def lira_root() -> Path:
    """Repository root: LIRA_ROOT, parent of LIRA_CONFIG, or inferred from package layout."""
    env = (os.environ.get("LIRA_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    cfg = (os.environ.get("LIRA_CONFIG") or "").strip()
    if cfg:
        return Path(cfg).expanduser().resolve().parent
    # …/core/scripts/chat/infrastructure/paths.py → repo root
    return Path(__file__).resolve().parents[4]


def config_path() -> Path:
    env = (os.environ.get("LIRA_CONFIG") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return lira_root() / "config.json"


def dotenv_path() -> Path:
    return lira_root() / ".env"


def lira_data(*parts: str) -> Path:
    return lira_root().joinpath("data", *[str(p) for p in parts])


@lru_cache(maxsize=1)
def _legacy_lira2_resolved() -> Path:
    return (Path.home() / "Lira2").resolve()


def _data_relative_under_home(p: Path) -> Path | None:
    """Return path relative to home starting at data/ (e.g. data/models/x.gguf)."""
    try:
        rel = p.resolve().relative_to(Path.home().resolve())
    except ValueError:
        return None
    if "data" not in rel.parts:
        return None
    i = rel.parts.index("data")
    return Path(*rel.parts[i:])


def resolve_path(path: str | None) -> str:
    """
    Expand ~ and map paths under another clone (~/Lira2, ~/LiraAi, …/data/…) to lira_root().
    Relative paths (data/models/…) resolve under lira_root().
    """
    if not path:
        return ""
    raw = str(path).strip()
    if not raw:
        return ""
    p = Path(os.path.expanduser(raw))
    root = lira_root().resolve()

    try:
        p.resolve().relative_to(root)
        return str(p.resolve())
    except ValueError:
        pass

    try:
        rel = p.resolve().relative_to(_legacy_lira2_resolved())
        return str(root / rel)
    except ValueError:
        pass

    data_rel = _data_relative_under_home(p)
    if data_rel is not None:
        return str((root / data_rel).resolve())

    if not p.is_absolute():
        return str((root / p).resolve())

    return str(p.resolve())


def path_for_config(path: str | Path) -> str:
    """Serialize path as ~/… under home (supports e.g. ~/Disk D/LiraAi/data/…)."""
    p = Path(path).resolve()
    root = lira_root().resolve()
    home = Path.home().resolve()
    try:
        rel = p.relative_to(root)
        install = root.relative_to(home)
        return "~/" + (install / rel).as_posix()
    except ValueError:
        pass
    try:
        rel = p.relative_to(home)
        return "~/" + rel.as_posix()
    except ValueError:
        return str(p)
