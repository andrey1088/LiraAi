"""Detect whether the installed stable-diffusion.cpp wheel was built with CUDA."""

from __future__ import annotations

import subprocess
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def stable_diffusion_lib_path() -> Path | None:
    try:
        import stable_diffusion_cpp.stable_diffusion_cpp as c
    except ImportError:
        return None
    lib = Path(c.__file__).resolve().parent / "lib" / "libstable-diffusion.so"
    return lib if lib.is_file() else None


@lru_cache(maxsize=1)
def stable_diffusion_linked_cuda() -> bool:
    lib = stable_diffusion_lib_path()
    if lib is None:
        return False
    try:
        out = subprocess.run(
            ["ldd", str(lib)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    text = (out.stdout or "").lower()
    return "libcudart" in text or "libcublas" in text


def log_sd_backend_hint() -> None:
    lib = stable_diffusion_lib_path()
    if lib is None:
        print("[SD] stable_diffusion_cpp не установлен", file=sys.stderr, flush=True)
        return
    if stable_diffusion_linked_cuda():
        print(f"[SD] CUDA: да (lib {lib})", file=sys.stderr, flush=True)
    else:
        print(
            "[SD] CUDA: нет — сборка CPU-only. Переустановите:\n"
            "  CMAKE_ARGS='-DSD_CUDA=ON' pip install --force-reinstall --no-cache-dir "
            "stable-diffusion-cpp-python\n"
            "  или: ./scripts/install-deps.sh (пересборка SD_CUDA при NVIDIA)",
            file=sys.stderr,
            flush=True,
        )
