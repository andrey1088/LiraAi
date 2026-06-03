#!/usr/bin/env python3
"""Upgrade all data/personas/*.json to schema_version 2 (prompts ru/en)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT = ROOT / "core" / "scripts" / "chat"
sys.path.insert(0, str(CHAT))

from infrastructure.persona.defaults import PERSONA_SCHEMA_VERSION  # noqa: E402
from infrastructure.persona.store import PersonaStore, normalize_persona  # noqa: E402


def main() -> int:
    personas_dir = ROOT / "data" / "personas"
    if not personas_dir.is_dir():
        print(f"Missing {personas_dir}")
        return 1

    updated = 0
    for path in sorted(personas_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
        doc = normalize_persona(raw)
        if raw.get("schema_version") == PERSONA_SCHEMA_VERSION and raw.get("prompts"):
            print(f"skip (already v2): {path.name}")
            continue
        PersonaStore.save_path(path, doc)
        print(f"updated: {path.name}")
        updated += 1

    template = personas_dir / "_template" / "persona.json"
    if template.is_file():
        print(f"template ok: {template}")
    else:
        print(f"warn: no template at {template}")

    print(f"done, updated {updated} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
