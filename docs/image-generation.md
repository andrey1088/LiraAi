# Генерация картинок (художница, stable-diffusion.cpp)

Слоты `text-to-image` в `config.json` используют [stable-diffusion-cpp-python](https://github.com/william-murray1204/stable-diffusion-cpp-python) (обёртка над [leejet/stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)).

## GPU vs CPU

Пакет из PyPI **по умолчанию может собраться без CUDA** — тогда вся генерация на **CPU** (`nvidia-smi` пустой во время работы).

Проверка установки:

```bash
./venv/bin/python3 -c "
from infrastructure.model_backends.image_sd.cuda_probe import stable_diffusion_linked_cuda, stable_diffusion_lib_path
print('lib:', stable_diffusion_lib_path())
print('CUDA-linked:', stable_diffusion_linked_cuda())
"
```

Или: `ldd venv/lib/python3.12/site-packages/stable_diffusion_cpp/lib/libstable-diffusion.so | grep -i cuda`

## Установка с CUDA

**`./scripts/install-deps.sh`** после `requirements.txt` сам пересобирает пакет с `SD_CUDA=ON`, если есть NVIDIA, а wheel — CPU-only (как при тестовом клоне / новом venv).

Ручная переустановка (если нужно):

```bash
CMAKE_ARGS="-DSD_CUDA=ON" pip install --force-reinstall --no-cache-dir stable-diffusion-cpp-python
```

Без GPU: `LIRA_INSTALL_SD_CPU=1 ./scripts/install-deps.sh`.

Нужны: драйвер NVIDIA (`nvidia-smi`), toolkit CUDA (`nvcc --version`), обычно ≥ 4 GB VRAM.

## Поведение Lira

- При переключении на художницу **LLM выгружается** с GPU (`release_llm_cuda_cache`), чтобы освободить VRAM.
- В логе при загрузке слота: `[SD] CUDA: да` или предупреждение о CPU-only.
- Во время **сэмплинга** (не при чтении файлов с диска) смотрите `nvidia-smi` — там должен расти `GPU-Util`.

## Настройки слота (`settings`)

| Поле | По умолчанию | Смысл |
|------|--------------|--------|
| `sd_offload_to_cpu` | `false` | `offload_params_to_cpu` в библиотеке (экономия VRAM, медленнее) |
| `keep_vae_on_cpu` | `false` | VAE на CPU |
| `keep_clip_on_cpu` | `false` | CLIP на CPU |
| `diffusion_flash_attn` | `false` | flash attention (меньше VRAM) |
| `sd_verbose` | `true` | лог stable-diffusion.cpp в stderr |

См. также [getting-started.md](getting-started.md), [configuration.md](configuration.md).
