#!/usr/bin/env bash
# Start Lira (.desktop and terminal). Creates venv on first run if missing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${LIRA_VENV:-$ROOT/venv}"
PYTHON="${PYTHON:-python3}"
PY="$VENV/bin/python3"

if [[ ! -d "$VENV" ]]; then
  echo "Creating virtual environment: $VENV"
  "$PYTHON" -m venv "$VENV"
  NEW_VENV=1
fi

if [[ "${NEW_VENV:-}" == "1" ]] || ! "$PY" -c "import PyQt6" 2>/dev/null; then
  if [[ "${LIRA_START_SKIP_INSTALL:-}" == "1" ]]; then
    echo "Dependencies missing in venv. Run:" >&2
    echo "  $ROOT/scripts/install-deps.sh" >&2
    exit 1
  fi
  echo "First run: installing dependencies…"
  LIRA_VENV="$VENV" "$ROOT/scripts/install-deps.sh"
fi

export LIRA_ROOT="$ROOT"
export LIRA_CONFIG="${LIRA_CONFIG:-$ROOT/config.json}"

cd "$ROOT"
exec "$PY" "$ROOT/core/scripts/chat/gui.py" "$@"
