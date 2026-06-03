#!/usr/bin/env python3
"""Filter git ls-files -z (stdin) by .export-ignore → NUL-separated paths on stdout."""
from __future__ import annotations

import fnmatch
import os
import sys


def load_rules(path: str) -> tuple[list[str], list[str]]:
    deny: list[str] = []
    allow: list[str] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("!"):
                allow.append(line[1:])
            else:
                deny.append(line)
    return deny, allow


def matches(path: str, rules: list[str]) -> bool:
    for rule in rules:
        if rule.endswith("/**"):
            base = rule[:-3]
            if path == base or path.startswith(base + "/"):
                return True
            continue
        if rule.endswith("/"):
            base = rule[:-1]
            if path == base or path.startswith(base + "/"):
                return True
            continue
        if "*" in rule or "?" in rule or "[" in rule:
            if fnmatch.fnmatch(path, rule):
                return True
            continue
        if path == rule:
            return True
    return False


def main() -> int:
    ignore = os.environ.get("EXPORT_IGNORE")
    if not ignore or not os.path.isfile(ignore):
        print("EXPORT_IGNORE must point to .export-ignore", file=sys.stderr)
        return 1

    deny, allow = load_rules(ignore)
    buf = sys.stdin.buffer.read()
    for chunk in buf.split(b"\0"):
        if not chunk:
            continue
        path = chunk.decode()
        if matches(path, allow) or not matches(path, deny):
            sys.stdout.buffer.write(chunk + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
