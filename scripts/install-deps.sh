#!/usr/bin/env bash
# Create venv (if needed), install PyTorch (CUDA) and Python deps for Lira2.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${LIRA_VENV:-$ROOT/venv}"
PYTHON="${PYTHON:-python3}"

if [[ ! -d "$VENV" ]]; then
  echo "Создаю venv: $VENV"
  "$PYTHON" -m venv "$VENV"
fi

# shellcheck source=/dev/null
source "$VENV/bin/activate"

pip install -U pip wheel

install_pytorch() {
  local tag index_url
  tag="$("$ROOT/scripts/pytorch_cuda_index.sh")"
  if [[ "$tag" == "cpu" ]]; then
    index_url="https://download.pytorch.org/whl/cpu"
  else
    index_url="https://download.pytorch.org/whl/${tag}"
  fi
  echo "Устанавливаю PyTorch (${tag})…"
  pip install torch torchvision torchaudio --index-url "$index_url"
}

if ! python -c "import torch" 2>/dev/null; then
  install_pytorch
else
  echo "PyTorch уже установлен: $(python -c 'import torch; print(torch.__version__)')"
  echo "  (переустановка: удалите torch из venv или новый venv; тег: LIRA_TORCH_CUDA=cu128)"
fi

echo "Устанавливаю requirements.txt…"
pip install -r "$ROOT/requirements.txt"

if [[ -n "${LIRA_SKIP_LLAMA:-}" ]]; then
  echo "LIRA_SKIP_LLAMA=1 — пропуск llama-cpp-python"
else
  if python -c "import llama_cpp" 2>/dev/null; then
    echo "llama-cpp-python уже установлен: $(python -c 'import llama_cpp; print(getattr(llama_cpp, "__version__", "?"))')"
  else
    echo "Устанавливаю llama-cpp-python (CUDA)…"
    CMAKE_ARGS="-DGGML_CUDA=on" pip install -r "$ROOT/requirements-llama.txt"
  fi
fi

sd_cuda_linked() {
  PYTHONPATH="$ROOT/core/scripts/chat" python -c "
from infrastructure.model_backends.image_sd.cuda_probe import stable_diffusion_linked_cuda
import sys
sys.exit(0 if stable_diffusion_linked_cuda() else 1)
"
}

ensure_sd_cuda() {
  if [[ "${LIRA_INSTALL_SD_CPU:-}" == "1" ]]; then
    echo "LIRA_INSTALL_SD_CPU=1 — stable-diffusion-cpp остаётся CPU-only."
    return 0
  fi
  if ! python -c "import stable_diffusion_cpp" 2>/dev/null; then
    return 0
  fi
  if sd_cuda_linked 2>/dev/null; then
    echo "stable-diffusion-cpp: CUDA-сборка уже есть."
    return 0
  fi
  local torch_tag
  torch_tag="$("$ROOT/scripts/pytorch_cuda_index.sh")"
  if [[ "$torch_tag" == "cpu" ]]; then
    echo "stable-diffusion-cpp: CPU-only (NVIDIA/CUDA для PyTorch не найдены)."
    return 0
  fi
  echo "stable-diffusion-cpp: pip-wheel без CUDA — пересборка SD_CUDA=ON (как llama-cpp)…"
  CMAKE_ARGS="-DSD_CUDA=ON" pip install --force-reinstall --no-cache-dir stable-diffusion-cpp-python
  if ! sd_cuda_linked; then
    echo "ОШИБКА: stable-diffusion-cpp собран без CUDA. См. docs/image-generation.md" >&2
    return 1
  fi
  echo "stable-diffusion-cpp: CUDA ok"
}

ensure_sd_cuda

if [[ "${LIRA_INSTALL_OPTIONAL:-}" == "1" ]]; then
  echo "Опциональные пакеты…"
  pip install -r "$ROOT/requirements-optional.txt"
fi

if [[ "${LIRA_INSTALL_DEV:-}" == "1" ]]; then
  pip install -r "$ROOT/requirements-dev.txt"
fi

echo ""
echo "Готово. Проверка импортов:"
"$ROOT/scripts/smoke_imports.sh"
