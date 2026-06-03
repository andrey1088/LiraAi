#!/usr/bin/env bash
# Quick import smoke (no GUI, no model weights).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${LIRA_PYTHON:-$ROOT/venv/bin/python3}"

export LIRA_CONFIG="${LIRA_CONFIG:-$ROOT/config.json}"
export LIRA_ROOT="$ROOT"

"$PY" <<'PY'
import os
import sys
from pathlib import Path

root = Path(os.environ["LIRA_ROOT"])
chat = root / "core" / "scripts" / "chat"
sys.path.insert(0, str(chat))

mods = [
    "PyQt6.QtCore",
    "PyQt6.QtWebEngineWidgets",
    "llama_cpp",
    "PIL",
    "torch",
    "sentence_transformers",
    "transformers",
    "trafilatura",
    "pypdf",
    "fitz",
    "sqlite_vec",
    "sounddevice",
    "stable_diffusion_cpp",
]

failed = []
for m in mods:
    try:
        __import__(m)
        print(f"  ok  {m}")
    except Exception as e:
        print(f"  FAIL {m}: {e}")
        failed.append(m)

# App modules (no QApplication)
from infrastructure.config.defaults import ensure_config_defaults
from infrastructure.semantic.engine import SemanticEngine

print("  ok  infrastructure.config.defaults")
print("  ok  infrastructure.semantic.engine")

if "stable_diffusion_cpp" not in failed:
    import subprocess
    from infrastructure.model_backends.image_sd.cuda_probe import stable_diffusion_linked_cuda

    if stable_diffusion_linked_cuda():
        print("  ok  stable_diffusion_cpp (CUDA)")
    elif subprocess.run(["nvidia-smi"], capture_output=True, timeout=5).returncode == 0:
        print(
            "  FAIL stable_diffusion_cpp: CPU-only при NVIDIA — ./scripts/install-deps.sh",
            file=sys.stderr,
        )
        failed.append("stable_diffusion_cpp.cuda")
    else:
        print("  ok  stable_diffusion_cpp (CPU, GPU нет)")

if failed:
    print(f"\nSmoke FAILED: {', '.join(failed)}", file=sys.stderr)
    sys.exit(1)
print("\nSmoke OK")
PY
