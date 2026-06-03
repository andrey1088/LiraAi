#!/usr/bin/env python3
"""Replace {user_name_genitive,dative,instrumental} with neutral phrasing or {user_name}."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "data" / "personas"

# Longer phrases first.
PHRASE_FIXES = [
    (
        "персональный ИИ-ассистент {user_name_genitive}",
        "персональный ИИ-ассистент владельца ({user_name})",
    ),
    (
        "Это НЕ чат Лиры с {user_name_instrumental}. {user_name} сейчас не пишет тебе.",
        "Это НЕ основной чат с владельцем ({user_name}). Владелец сейчас не пишет тебе.",
    ),
    (
        "интимный/домашний тон как с {user_name_instrumental}.",
        "интимный/домашний тон как с владельцем в основном чате.",
    ),
    ("бот {user_name_genitive}", "бот владельца ({user_name})"),
    ("с машины {user_name_genitive}", "с машины владельца"),
    ("Не выводи {user_name_dative} технический", "Не выводи пользователю технический"),
    ("передать {user_name_dative}", "передать владельцу"),
    ("notify только при явной просьбе передать {user_name_dative}", "notify только при явной просьбе передать владельцу"),
]

FALLBACK = {
    "{user_name_genitive}": "{user_name}",
    "{user_name_dative}": "владельцу",
    "{user_name_instrumental}": "владельцем",
}


def _fix_str(s: str) -> str:
    for old, new in PHRASE_FIXES:
        s = s.replace(old, new)
    for old, new in FALLBACK.items():
        s = s.replace(old, new)
    return s


def _walk(obj):
    if isinstance(obj, dict):
        return {k: _walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(x) for x in obj]
    if isinstance(obj, str):
        return _fix_str(obj)
    return obj


def _process(path: Path) -> bool:
    raw = json.loads(path.read_text(encoding="utf-8"))
    new = _walk(raw)
    if new == raw:
        return False
    path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> int:
    n = 0
    for path in sorted(PERSONAS.glob("*.json")):
        if _process(path):
            print(f"updated: {path.name}")
            n += 1
        else:
            print(f"unchanged: {path.name}")
    tpl = PERSONAS / "_template" / "persona.json"
    if tpl.is_file():
        _process(tpl)
        print("template: persona.json")
    print(f"done, {n} persona file(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
