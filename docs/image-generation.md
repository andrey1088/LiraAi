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
| `keep_vae_on_cpu` | **`true`** (если не задано) | **декод latent → картинка на CPU** — на ~16 GB без этого часто OOM на этапе `decoding latents` (буфер VAE в логе ~7+ GB VRAM) |
| `keep_clip_on_cpu` | `false` | CLIP на CPU |
| `diffusion_flash_attn` | `false` | flash attention (меньше VRAM) |
| `sd_verbose` | `true` | лог stable-diffusion.cpp в stderr |
| `width`, `height` | `768` | база для 1:1, если в `sd_aspect_sizes` нет ключа `"1:1"` |
| `sd_aspect_sizes` | см. `config.example.json` | **ширина×высота** для каждого соотношения из UI |

## Разрешение (aspect ratio)

Выбор в UI: **1:1**, **16:9**, **9:16** (`core/web/app.js` → `ratioSelect` → `process_image_generation` → `aspect_ratio`).

Размеры задаются в **`config.json`**, слот `text-to-image` → **`settings.sd_aspect_sizes`** (внутри блока `settings`, не рядом с ним). Массив `[ширина, высота]`:

```json
"width": 768,
"height": 768,
"sd_aspect_sizes": {
  "1:1": [768, 768],
  "16:9": [1024, 576],
  "9:16": [576, 1024]
}
```

В `config.example.json` для базы 768×768: альбом/портрет **~та же площадь**, что квадрат (~590K px), не SDXL **1216×832** (~1M px, частый OOM).

Если `sd_aspect_sizes` **не задан** или в нём нет какого-то ключа — недостающие размеры считаются из `width`/`height` (16:9 и 9:16 — та же площадь, что у квадрата).

При нехватке VRAM: уменьшите все три пары в `sd_aspect_sizes` (и при желании `width`/`height`) или включите `sd_offload_to_cpu` / `keep_vae_on_cpu`.

### OOM на `decoding latents` / `vae compute buffer`

Типичный лог при вылете:

```text
generating 1 latent images completed, taking …s
decoding 1 latents
vae compute buffer size: 7680.25 MB(VRAM)
CUDA error: out of memory
```

Сэмплинг (UNet) уже прошёл на GPU; падает **VAE decode**, когда под картинку резервируется ещё несколько гигабайт VRAM поверх весов диффузии. Решение:

1. **`"keep_vae_on_cpu": true`** в `settings` слота художницы (в Lira по умолчанию включено, если ключ не задан).
2. **Перезагрузить слот** после смены флага (переключить модель туда-обратно или перезапуск Lira) — флаг читается при загрузке checkpoint.
3. При необходимости ещё **`sd_offload_to_cpu": true`** или меньшие размеры в `sd_aspect_sizes`.

### Сероый прямоугольник и «долго крутит»

Чаще всего это **не «модель не умеет 16:9»**, а **слишком много пикселей** для VRAM (~16 GB): сэмплинг идёт долго, latent/VAE ломаются, на диск попадает пустая или серая картинка без явной ошибки в UI.

Сравнение (пикселей):

| Размер | Пикселей | Комментарий |
|--------|----------|-------------|
| 1024×1024 | ~1.05M | нормальная база для SDXL / Pony |
| 1024×576 (16:9) | ~0.59M | легче квадрата, обычно стабильно |
| 1216×832 / 1344×768 | ~1.0M | типичные SDXL-бакеты, на 16 GB может OOM |
| **1600×900** | **~1.44M** | тяжелее квадрата — частый кандидат на серый кадр |

Чекпоинты **Pony / Illustrious (SDXL)** обучены на фиксированных **бакетах** (кратность 8, часто 1024 по длинной стороне). Произвольное **1600×900** не «запрещено», но и не эталон: качество и стабильность обычно лучше у **1024×576**, **1216×832** или **1344×768** — подбирайте по `nvidia-smi`, не выше того, что держит 1:1.

См. также [getting-started.md](getting-started.md), [configuration.md](configuration.md).
