# Первый запуск

Локальный GUI-ассистент Lira: **llama.cpp** (текст + vision), tools, память, опционально TTS и генерация картинок.

## Требования

| Компонент | Минимум |
|-----------|---------|
| ОС | Linux (эталон: **Ubuntu 24.04**) |
| GPU | NVIDIA + драйвер (`nvidia-smi` без mismatch) |
| Python | **3.12+** |
| Диск | Место под GGUF в `data/models/` (десятки ГБ — не в git) |
| Нативный стек | **llama.cpp** (CUDA), **llama-cpp-python** — см. [models-verified.md](models-verified.md) |
| TTS (опционально) | Silero — вручную, см. [tts.md](tts.md) |
| Веб-поиск (опционально) | Docker + `infra/docker-compose.services.yml` (SearXNG) |

## Установка (репозиторий уже клонирован)

```bash
cd ~/Lira2

# 1. venv + pip (PyTorch CUDA, llama-cpp CUDA, SD CUDA при NVIDIA — см. install-deps.sh)
chmod +x scripts/install-deps.sh scripts/smoke_imports.sh
./scripts/install-deps.sh

# Уже есть venv с llama-cpp-python? Пропустить пересборку:
# LIRA_SKIP_LLAMA=1 ./scripts/install-deps.sh

# Опционально: diffusers для Qwen Image Edit (слот image-edit)
# LIRA_INSTALL_OPTIONAL=1 ./scripts/install-deps.sh

# 2. Конфиг и имя владельца ({user_name} в промптах)
./scripts/setup.sh

# 3. Секреты и сервисы (по желанию)
cp .env.example .env
# отредактируйте TELEGRAM_*, LIRA_SEARXNG_* …

# 4. Веса и слоты моделей
# Положите GGUF/mmproj в data/models/, пропишите слоты в config.json
# или скопируйте config.example.json → config.json и заполните models[]
# Если config скопирован со старого ~/Lira2:
#   LIRA_ROOT="$PWD" python3 scripts/rewrite_config_paths.py

# 5. Проверка импортов (без GUI и без весов)
./scripts/smoke_imports.sh

# 6. Запуск (при отсутствии venv создаст его и вызовет install-deps.sh)
./scripts/lira_start.sh
# только venv без pip: LIRA_START_SKIP_INSTALL=1 ./scripts/lira_start.sh

# 7. Ярлык в меню приложений (Linux, опционально)
# см. раздел «Ярлык в меню» ниже
```

Переменные окружения:

| Переменная | Назначение |
|------------|------------|
| `LIRA_ROOT` | Корень клона (выставляет `lira_start.sh`; без него пути могут уехать в `~/Lira2`) |
| `LIRA_VENV` | Путь к venv (по умолчанию `$LIRA_ROOT/venv`) |
| `LIRA_CONFIG` | Путь к `config.json` (по умолчанию `$LIRA_ROOT/config.json`) |
| `LIRA_SKIP_LLAMA` | `1` — не ставить/пересобирать llama-cpp-python |
| `LIRA_INSTALL_OPTIONAL` | `1` — `requirements-optional.txt` |
| `LIRA_INSTALL_DEV` | `1` — ruff, pytest |
| `LIRA_INSTALL_SD_CPU` | `1` — не пересобирать stable-diffusion-cpp с CUDA (машина без GPU) |

**Важно:** для тестовой установки в другом каталоге запускайте **только** `./scripts/lira_start.sh` из этого каталога. Ярлык `.desktop` должен указывать на **тот же** каталог, что и `config.json`.

## Ярлык в меню (Linux)

Иконка приложения: `data/icon.png` в корне репозитория (не путать с `data/icons/` — аватары слотов моделей).

1. Скопируйте шаблон и подставьте путь к клону (`INSTALL_ROOT` → например `~/LiraAi`):

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

`lira.desktop` с вашими путями в git не коммитьте (локальный файл; в репозитории только `lira.desktop.example`). `Exec` и `Path` должны вести в каталог, где лежат `scripts/lira_start.sh` и `config.json`.

## Другой каталог на том же ПК (уже прогоняли)

Если нужно снова проверить пути `LIRA_ROOT` — достаточно клона в другой папке и `./scripts/lira_start.sh` (без повторного полного `install-deps`, если venv общий или скопирован). Обязательный второй прогон для релиза **не нужен** — см. [RELEASE_PLAN.md](RELEASE_PLAN.md) § 4.1.1.

Другая машина без NVIDIA: установка возможна на CPU / Intel GPU, но чат и vision будут медленными; это не часть чеклиста релиза.

## Файлы зависимостей

| Файл | Содержимое |
|------|------------|
| [requirements.txt](../requirements.txt) | GUI, tools, RAG, PDF, sqlite-vec, stable-diffusion-cpp |
| [requirements-llama.txt](../requirements-llama.txt) | Закреплённый git-коммит llama-cpp-python |
| [requirements-optional.txt](../requirements-optional.txt) | Qwen Image Edit (по желанию) |
| [requirements-dev.txt](../requirements-dev.txt) | ruff, pytest |

**PyTorch** — `install-deps.sh` сначала смотрит **CUDA Version** в `nvidia-smi`, иначе версию **nvcc**, и выбирает wheel (`cu128`, `cu126`, `cu124`, … или `cpu`). Принудительно: `LIRA_TORCH_CUDA=cu128 ./scripts/install-deps.sh`.

**llama-cpp-python** на эталонной машине: версия **0.3.23**, CUDA (`GGML_CUDA=on`). Подробности и коммит llama.cpp: [models-verified.md](models-verified.md).

## Данные вне git

| Путь | Назначение |
|------|------------|
| `config.json` | Слоты моделей, `user`, gallery, TTS |
| `data/personas/` | Персоны и промпты |
| `data/memory/*.db` | История и память |
| `data/models/*` | GGUF, mmproj, embedder, Silero `.pt` |
| `.env` | Telegram, SearXNG (см. `.env.example`) |

Шаблон конфига: [config.example.json](../config.example.json). Документация полей: [configuration.md](configuration.md).

## Модели по умолчанию (автор)

Слот **1** — Gemma-4 (Лира), **2** — Gemma-3 (Ава), **3** — экспериментальный Qwen3-VL. Таблица и версии стека: [models-verified.md](models-verified.md).

## Чеклист «у себя всё завелось»

- [ ] `./scripts/install-deps.sh` без ошибок
- [ ] `./scripts/smoke_imports.sh` → `Smoke OK`
- [ ] `./scripts/setup.sh` — `user.display_name` в config
- [ ] В `config.json` есть хотя бы один слот с существующими путями к GGUF
- [ ] `./scripts/lira_start.sh` — окно Lira, ответ в чате на verified-модели
- [ ] Прикрепление изображения в чат (файл или кнопка 📎 в галерее) — превью над полем ввода
- [ ] (опционально) `data/models/paraphrase-multilingual-MiniLM-L12-v2/` — semantic / memory_search
- [ ] (опционально) `.env` + Docker — `web_search` через SearXNG
- [ ] (опционально) [озвучка Silero](tts.md) — `data/models/v5_5_ru.pt` или без TTS

## Художница (stable-diffusion.cpp)

При **NVIDIA** `install-deps.sh` сам пересобирает пакет с `SD_CUDA=ON` (pip-wheel из `requirements.txt` — CPU-only). `smoke_imports` падает, если GPU есть, а lib без CUDA. Подробнее: [image-generation.md](image-generation.md).

## Озвучка (Silero)

Не обязательна: без файла `.pt` Lira **запускается без озвучки**. Веса не качаются при `install-deps` — только вручную, см. [tts.md](tts.md) и [snakers4/silero-models](https://github.com/snakers4/silero-models).

## Устранение неполадок

- **`import llama_cpp` fails** — пересоберите с CUDA: `CMAKE_ARGS="-DGGML_CUDA=on" pip install -r requirements-llama.txt`
- **Открывается «чужая» data / старый чат** — проверьте `LIRA_ROOT` (запуск через `./scripts/lira_start.sh` из нужного клона, не старый `.desktop` на `~/Lira2`)
- **Пути в config указывают на другой каталог** — `LIRA_ROOT="$PWD" python3 scripts/rewrite_config_paths.py`
- **Галерея есть, прикрепить в чат нельзя** — в терминале строки `[Attachment]`; частые причины: файл не найден (`resolve_path` / пути в `gallery.db`), раньше — `TypeError: not 'int'` в `lira_data` (исправлено: сегменты пути приводятся к `str`)
- **«Файл изображения не найден» из галереи** — в БД пути вида `~/Lira2/data/...`; нужен запуск с правильным `LIRA_ROOT` или общая `data/` + `rewrite_config_paths.py`
- **Qt WebEngine / OpenGL** — не запускайте под SSH без `DISPLAY`; на Wayland/X11 локально обычно достаточно `AA_ShareOpenGLContexts` (уже в `gui.py`)
- **PDF в vision** — `pip install pymupdf`; только текст из PDF — достаточно `pypdf` (уже в requirements)
- **Память с векторами** — `pip install sqlite-vec`; без расширения поиск по embedding отключён
- **Нет озвучки** — нормально без `data/models/v5_5_ru.pt`; см. [tts.md](tts.md)
- **Художница на CPU при NVIDIA** — снова `./scripts/install-deps.sh` (должен допересобрать SD); см. [image-generation.md](image-generation.md)

Дальше: [configuration.md](configuration.md), [personas.md](personas.md), [tools.md](tools.md), [tts.md](tts.md).
