#!/usr/bin/env bash
# Initial setup: config.json and owner display name (user block).
# Python-зависимости: ./scripts/install-deps.sh (см. docs/getting-started.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${LIRA_CONFIG:-$ROOT/config.json}"
EXAMPLE="$ROOT/config.example.json"

if [[ ! -f "$EXAMPLE" ]]; then
  echo "Не найден config.example.json в $ROOT" >&2
  exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
  cp "$EXAMPLE" "$CONFIG"
  echo "Создан $CONFIG из примера."
fi

echo ""
echo "Имя владельца ({user_name}) подставляется в промпты."
echo "Позже можно изменить в config.json → user.display_name;"
echo "в истории чата могут остаться старые обращения — возможна путаница у модели."
echo ""

read -r -p "Как вас зовут? " DISPLAY_NAME
DISPLAY_NAME="$(echo "$DISPLAY_NAME" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
if [[ -z "$DISPLAY_NAME" ]]; then
  echo "Имя не задано, выход." >&2
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

print(f"\nГотово. user.display_name записан в {path}")
PY

echo "Дальше: добавьте модели в config.json или через UI, положите GGUF в data/models/."
echo "Запуск: $ROOT/scripts/lira_start.sh"
