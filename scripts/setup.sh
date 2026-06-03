#!/usr/bin/env bash
# Initial setup: config.json and owner display name (user block).
# Python deps: ./scripts/install-deps.sh (see docs/getting-started.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${LIRA_CONFIG:-$ROOT/config.json}"
EXAMPLE="$ROOT/config.example.json"

if [[ ! -f "$EXAMPLE" ]]; then
  echo "config.example.json not found in $ROOT" >&2
  exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
  cp "$EXAMPLE" "$CONFIG"
  echo "Created $CONFIG from example."
fi

echo ""
echo "Owner name ({user_name}) is injected into prompts."
echo "You can change it later in config.json → user.display_name;"
echo "old chat history may still use the previous name."
echo ""

read -r -p "Your display name: " DISPLAY_NAME
DISPLAY_NAME="$(echo "$DISPLAY_NAME" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
if [[ -z "$DISPLAY_NAME" ]]; then
  echo "Empty name, aborting." >&2
  exit 1
fi

export CONFIG DISPLAY_NAME ROOT LIRA_ROOT="$ROOT" LIRA_CONFIG="$CONFIG"
python3 <<'PY'
import json
import os
import sys

path = os.path.expanduser(os.environ["CONFIG"])
root = os.environ["ROOT"]
sys.path.insert(0, os.path.join(root, "core", "scripts", "chat"))

from infrastructure.config.defaults import ensure_config_defaults

with open(path, encoding="utf-8") as f:
    cfg = json.load(f)

cfg["user"] = {"display_name": os.environ["DISPLAY_NAME"]}
ensure_config_defaults(cfg)

with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=4)
    f.write("\n")

print(f"\nDone. user.display_name saved to {path}")
PY

echo "Next: add model slots in config.json or UI; place GGUF weights under data/models/."
echo "Start: $ROOT/scripts/lira_start.sh"
