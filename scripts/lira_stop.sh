#!/usr/bin/env bash
# Stop Lira and free VRAM (frozen window / gio “already running”).
set -euo pipefail

PATTERN='/home/dev01/Lira2/core/scripts/chat/gui.py'
LOCK=~/Lira2/lira.lock

pids=$(pgrep -f "$PATTERN" || true)
if [[ -z "$pids" ]]; then
  echo "[lira_stop] процесс Lira не найден"
  rm -f "$LOCK" 2>/dev/null || true
else
  echo "[lira_stop] завершаю: $pids"
  kill -TERM $pids 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    sleep 1
    pgrep -f "$PATTERN" >/dev/null 2>&1 || break
  done
  pids=$(pgrep -f "$PATTERN" || true)
  if [[ -n "$pids" ]]; then
    echo "[lira_stop] SIGKILL: $pids"
    kill -KILL $pids 2>/dev/null || true
    sleep 1
  fi
  rm -f "$LOCK" 2>/dev/null || true
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "[lira_stop] GPU:"
  nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv,noheader 2>/dev/null \
    | rg -i 'lira|python' || echo "  (нет python/lira на GPU)"
fi
