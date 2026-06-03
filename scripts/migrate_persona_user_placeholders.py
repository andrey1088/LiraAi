#!/usr/bin/env python3
"""Replace hardcoded owner name in persona JSON with {user_name*} placeholders."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "data" / "personas"

# Phrases where the hardcoded name is NOT the owner (before generic replaces).
SPECIAL_PHRASES = [
    ("Не зови его Андреем", "Не называй собеседника именем владельца"),
]

# Order matters: longer inflected forms first.
REPLACEMENTS = [
    ("Андреем", "{user_name_instrumental}"),
    ("Андрею", "{user_name_dative}"),
    ("Андрея", "{user_name_genitive}"),
    ("Андрей", "{user_name}"),
    ("Andrey's", "{user_name}'s"),
    ("Andrey", "{user_name}"),
    # Model / app names → placeholders (filled from config at runtime).
    ("«Лира, бот владельца", "«{model_name}, бот владельца"),
    ("«Lira, bot", "«{model_name}, bot"),
    ("ты Лира, голос", "ты {model_name}, голос"),
    ("as Lira, the bot", "as {model_name}, the bot"),
    ("as Lira, the", "as {model_name}, the"),
    ("Reply briefly as Lira", "Reply briefly as {model_name}"),
    ("нейтрально как Лира", "нейтрально как {model_name}"),
    ("briefly as Lira", "briefly as {model_name}"),
    ("владелец Лиры", "владелец ({user_name})"),
    ("owner of Lira", "owner ({user_name})"),
    ("main Lira chat", "main {app_name} desktop chat"),
]


def _replace_in_str(s: str) -> str:
    for old, new in SPECIAL_PHRASES:
        s = s.replace(old, new)
    for old, new in REPLACEMENTS:
        s = s.replace(old, new)
    return s


def _walk(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = _walk(v)
        return out
    if isinstance(obj, list):
        return [_walk(x) for x in obj]
    if isinstance(obj, str):
        return _replace_in_str(obj)
    return obj


def main() -> int:
    n = 0
    for path in sorted(PERSONAS.glob("*.json")):
        if path.parent.name == "_template":
            continue
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
        new = _walk(raw)
        if new != raw:
            with path.open("w", encoding="utf-8") as f:
                json.dump(new, f, ensure_ascii=False, indent=2)
            print(f"updated: {path.name}")
            n += 1
        else:
            print(f"unchanged: {path.name}")

    tpl = PERSONAS / "_template" / "persona.json"
    if tpl.is_file():
        with tpl.open(encoding="utf-8") as f:
            raw = json.load(f)
        new = _walk(raw)
        with tpl.open("w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, indent=2)
        print(f"template: {tpl.name}")

    print(f"done, {n} persona file(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
