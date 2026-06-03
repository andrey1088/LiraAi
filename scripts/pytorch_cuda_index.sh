#!/usr/bin/env bash
# Выбрать тег wheel PyTorch (cu128, cu126, …, cpu).
# Печатает тег в stdout; пояснение — в stderr.
# Переопределение: LIRA_TORCH_CUDA=cu128|cu126|cu124|cu121|cu118|cpu
set -euo pipefail

if [[ -n "${LIRA_TORCH_CUDA:-}" ]]; then
  echo "${LIRA_TORCH_CUDA}" >&2
  echo "PyTorch: LIRA_TORCH_CUDA=${LIRA_TORCH_CUDA}"
  exit 0
fi

source_kind=""
cuda_ver=""

if command -v nvidia-smi &>/dev/null; then
  if nvidia_smi_out="$(nvidia-smi 2>/dev/null)"; then
    cuda_ver="$(sed -n 's/.*CUDA Version: \([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' <<<"$nvidia_smi_out" | head -1)"
    if [[ -n "$cuda_ver" ]]; then
      source_kind="nvidia-smi (драйвер)"
    fi
  fi
fi

if [[ -z "$cuda_ver" ]] && command -v nvcc &>/dev/null; then
  if nvcc_out="$(nvcc --version 2>/dev/null)"; then
    cuda_ver="$(sed -n 's/.*release \([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' <<<"$nvcc_out" | head -1)"
    if [[ -n "$cuda_ver" ]]; then
      source_kind="nvcc (CUDA toolkit)"
    fi
  fi
fi

if [[ -z "$cuda_ver" ]]; then
  echo "PyTorch: GPU/CUDA не найдены (nvidia-smi / nvcc) → cpu" >&2
  echo "cpu"
  exit 0
fi

major="${cuda_ver%%.*}"
minor="${cuda_ver#*.}"
minor="${minor%%.*}"
ver_num=$((major * 100 + minor))

# Самый новый wheel, совместимый с обнаруженной версией (порог — типичный минимум драйвера).
# CUDA 13.x toolkit обычно совместим с cu128 wheels PyTorch.
tag="cpu"
if (( ver_num >= 1280 )); then
  tag="cu128"
elif (( ver_num >= 1260 )); then
  tag="cu126"
elif (( ver_num >= 1240 )); then
  tag="cu124"
elif (( ver_num >= 1210 )); then
  tag="cu121"
elif (( ver_num >= 1180 )); then
  tag="cu118"
else
  echo "PyTorch: CUDA ${cuda_ver} слишком старая для готовых wheels → cpu" >&2
  echo "cpu"
  exit 0
fi

echo "PyTorch: ${source_kind}, CUDA ${cuda_ver} → index ${tag}" >&2
echo "$tag"
