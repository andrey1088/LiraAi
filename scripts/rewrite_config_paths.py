#!/usr/bin/env python3
"""Rewrite install paths in config.json to the current LIRA_ROOT."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts" / "chat"))

os.environ.setdefault("LIRA_ROOT", str(ROOT))

from infrastructure.paths import config_path, lira_root, path_for_config, resolve_path  # noqa: E402


def _is_path_like(value: str) -> bool:
    v = value.strip()
    if not v:
        return False
    if v.startswith("data/") or "/data/" in v:
        return True
    if v.startswith("~/") or v.startswith("~\\"):
        return True
    if v.startswith("/") and "/data/" in v:
        return True
    return "Lira2" in v or "LiraAi" in v


def _rewrite_str(value: str) -> str:
    if not _is_path_like(value):
        return value
    resolved = resolve_path(value)
    return path_for_config(resolved)


def _walk(obj):
    if isinstance(obj, dict):
        return {k: _walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v) for v in obj]
    if isinstance(obj, str):
        return _rewrite_str(obj)
    return obj


def main() -> int:
    root = lira_root()
    cfg_path = config_path()
    if not cfg_path.is_file():
print(f"Config file not found: {cfg_path}", file=sys.stderr)
        return 1

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    out = _walk(data)
    changed = out != data

    cfg_path.write_text(json.dumps(out, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    if changed:
        print(f"Paths updated in {cfg_path}")
    else:
        print(f"No changes (already matches {root})")
    print(f"Install root: {root}")
    print(f"Example model_path: {out.get('models', [{}])[0].get('model_path', '—') if out.get('models') else '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
