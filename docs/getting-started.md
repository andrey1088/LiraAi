# Первый запуск

Локальный GUI-ассистент Lira: **llama.cpp** (текст + vision), tools, память, опционально TTS, **STT (ru)** и генерация картинок.

## Требования

| Компонент | Минимум |
|-----------|---------|
| ОС | Linux (эталон: **Ubuntu 24.04**) |
| GPU | NVIDIA + драйвер (`nvidia-smi` без mismatch) |
| Python | **3.12+** |
| Диск | Место под GGUF в `data/models/` (десятки ГБ — не в git) |
| Нативный стек | **llama.cpp** (CUDA), **llama-cpp-python** — см. [models-verified.md](models-verified.md) |
| TTS (опционально) | Silero — вручную, см. [tts.md](tts.md) |
| STT (опционально) | GigaAM — только `ui_locale: ru`, веса качаются при старте, см. [stt.md](stt.md) |
| Веб-поиск (опционально) | Docker + `infra/docker-compose.services.yml` (SearXNG) |

## Установка (репозиторий уже клонирован)

```bash
cd ~/Lira

# 1. venv + pip (PyTorch CUDA, llama-cpp CUDA, SD CUDA, diffusers on NVIDIA)
chmod +x scripts/install-deps.sh scripts/smoke_imports.sh
./scripts/install-deps.sh

# LIRA_SKIP_LLAMA=1 ./scripts/install-deps.sh

# 2. config + owner name ({user_name} in prompts)
./scripts/setup.sh

# 3. Secrets and services (optional)
cp .env.example .env
# TELEGRAM_*, LIRA_SEARXNG_*, HTTP_PROXY for search in WebEngine

# 4. Weights and model slots
# Place GGUF/mmproj under data/models/, edit config.json
# Or: cp config.example.json config.json and fill models[]
# Paths from another clone: LIRA_ROOT="$PWD" python3 scripts/rewrite_config_paths.py

# 5. Import smoke (no GUI, no weights)
./scripts/smoke_imports.sh

# 6. Launch
./scripts/lira_start.sh
# LIRA_START_SKIP_INSTALL=1 ./scripts/lira_start.sh

# 7. Desktop shortcut (optional) — see below
```

Переменные окружения:

| Переменная | Назначение |
|------------|------------|
| `LIRA_ROOT` | Install root (`lira_start.sh`; default: repo directory) |
| `LIRA_VENV` | Путь к venv (по умолчанию `$LIRA_ROOT/venv`) |
| `LIRA_CONFIG` | Путь к `config.json` (по умолчанию `$LIRA_ROOT/config.json`) |
| `LIRA_SKIP_LLAMA` | `1` — не ставить/пересобирать llama-cpp-python |
| `LIRA_INSTALL_OPTIONAL` | `1` — diffusers на CPU-only (иначе при NVIDIA ставится автоматически) |
| `LIRA_INSTALL_QWEN_CPU` | `1` — не ставить diffusers (слот image-edit недоступен) |
| `LIRA_INSTALL_DEV` | `1` — ruff, pytest |
| `LIRA_INSTALL_SD_CPU` | `1` — не пересобирать stable-diffusion-cpp с CUDA (машина без GPU) |

**Важно:** для тестовой установки в другом каталоге запускайте **только** `./scripts/lira_start.sh` из этого каталога. Ярлык `.desktop` должен указывать на **тот же** каталог, что и `config.json`.

## Ярлык в меню (Linux)

Иконка приложения: `data/icon.png` в корне репозитория (не путать с `data/icons/` — аватары слотов моделей).

1. Скопируйте шаблон и подставьте путь к клону (`INSTALL_ROOT` → например `~/Lira`):

```bash
cd "$LIRA_ROOT"   # каталог, откуда вы запускаете Lira
ROOT="$(pwd)"
sed "s|INSTALL_ROOT|$ROOT|g" lira.desktop.example > lira.desktop
chmod +x scripts/lira_start.sh
```

2. Установите файл в меню:

```bash
mkdir -p ~/.local/share/applications
cp lira.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

3. Запуск: пункт **Lira AI** в меню приложений или:

```bash
gio launch ~/.local/share/applications/lira.desktop
```

`lira.desktop` с вашими путями в git не коммитьте. `Exec` и `Path` должны указывать на каталог с `scripts/lira_start.sh` и `config.json`.

Другая машина без NVIDIA: установка на CPU возможна, но чат и vision будут медленными.

## Файлы зависимостей

| Файл | Содержимое |
|------|------------|
| [requirements.txt](../requirements.txt) | GUI, tools, RAG, PDF, sqlite-vec, stable-diffusion-cpp |
| [requirements-llama.txt](../requirements-llama.txt) | Закреплённый git-коммит llama-cpp-python |
| [requirements-optional.txt](../requirements-optional.txt) | Qwen Image Edit (diffusers, при NVIDIA — автоматически) |
| [requirements-dev.txt](../requirements-dev.txt) | ruff, pytest |

**PyTorch** — `install-deps.sh` сначала смотрит **CUDA Version** в `nvidia-smi`, иначе версию **nvcc**, и выбирает wheel (`cu128`, `cu126`, `cu124`, … или `cpu`). Принудительно: `LIRA_TORCH_CUDA=cu128 ./scripts/install-deps.sh`.

**llama-cpp-python** на эталонной машине: версия **0.3.23**, CUDA (`GGML_CUDA=on`). Подробности и коммит llama.cpp: [models-verified.md](models-verified.md).

## Данные вне git

| Путь | Назначение |
|------|------------|
| `config.json` | Слоты моделей, `user`, gallery, TTS |
| `data/personas/` | Персоны и промпты |
| `data/memory/*.db` | История и память |
| `data/models/*` | GGUF, mmproj, embedder, Silero `.pt`, GigaAM ONNX (STT) |
| `.env` | Telegram, SearXNG (см. `.env.example`) |

Шаблон конфига: [config.example.json](../config.example.json). Документация полей: [configuration.md](configuration.md).

## Модели

Основные и экспериментальные GGUF, версии стека: [models-verified.md](models-verified.md). В `config.example.json` — три слота (чат / SD / image-edit).

## Проверка после установки

1. **Окружение** — `./scripts/install-deps.sh` без ошибок; `./scripts/smoke_imports.sh` → `Smoke OK`.
2. **Конфиг** — `./scripts/setup.sh` записал `user.display_name`; в `config.json` есть слот с существующими путями к GGUF.
3. **Запуск** — `./scripts/lira_start.sh` открывает окно; ответ в чате на модели из [основного списка](models-verified.md).
4. **Vision** — картинка из файла или 📎 в галерее: превью над полем ввода, модель видит вложение.
5. **Опционально** — `data/models/paraphrase-multilingual-MiniLM-L12-v2/` для semantic / `memory_search`; `.env` + Docker + SearXNG для `web_search`; Silero `v5_5_ru.pt` для TTS (без `.pt` приложение тоже стартует); при `ui_locale: ru` — голосовой ввод (GigaAM, см. [stt.md](stt.md)).

## Художница (stable-diffusion.cpp)

При **NVIDIA** `install-deps.sh` сам пересобирает пакет с `SD_CUDA=ON` (pip-wheel из `requirements.txt` — CPU-only). `smoke_imports` падает, если GPU есть, а lib без CUDA. Подробнее: [image-generation.md](image-generation.md).

## Озвучка (Silero)

Не обязательна: без файла `.pt` Lira **запускается без озвучки**. Веса не качаются при `install-deps` — только вручную, см. [tts.md](tts.md) и [snakers4/silero-models](https://github.com/snakers4/silero-models).

## Распознавание речи (GigaAM)

Только при **`ui_locale: ru`** и чат-слоте. Веса (~850 MB) и пакеты `onnx-asr` / `huggingface_hub` подтягиваются **в фоне при первом запуске**; без интернета можно положить ONNX в `data/models/gigaam-v3-e2e-rnnt/` вручную. Подробнее: [stt.md](stt.md).

## Устранение неполадок

- **`import llama_cpp` fails** — пересоберите с CUDA: `CMAKE_ARGS="-DGGML_CUDA=on" pip install -r requirements-llama.txt`
- **Wrong data / old chat** — check `LIRA_ROOT` (run `./scripts/lira_start.sh` from the intended clone; `.desktop` must match)
- **Config paths point elsewhere** — `LIRA_ROOT="$PWD" python3 scripts/rewrite_config_paths.py`
- **Gallery attach fails** — paths in `gallery.db` may reference another install; use `rewrite_config_paths.py` or correct `LIRA_ROOT`
- **Qt WebEngine / OpenGL** — не запускайте под SSH без `DISPLAY`; на Wayland/X11 локально обычно достаточно `AA_ShareOpenGLContexts` (уже в `gui.py`)
- **PDF в vision** — `pip install pymupdf`; только текст из PDF — достаточно `pypdf` (уже в requirements)
- **Память с векторами** — `pip install sqlite-vec`; без расширения поиск по embedding отключён
- **Нет озвучки** — нормально без `data/models/v5_5_ru.pt`; см. [tts.md](tts.md)
- **Художница на CPU при NVIDIA** — снова `./scripts/install-deps.sh` (должен допересобрать SD); см. [image-generation.md](image-generation.md)

Дальше: [configuration.md](configuration.md), [personas.md](personas.md), [tools.md](tools.md), [tts.md](tts.md).
