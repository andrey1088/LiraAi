# Lira

> **English (brief).** Local PyQt6 assistant: llama.cpp chat, vision, tools, memory, optional TTS and image generation/editing. **All documentation is in Russian** — start with [docs/getting-started.md](docs/getting-started.md) if you read Cyrillic; otherwise use this README and `config.example.json` as a map. **Linux** (Ubuntu 24.04 tested), **Python 3.12+**, **NVIDIA + CUDA** recommended. Model weights are **not** included. Hobby project, no support guarantee; issues and PRs welcome.

---

Локальный GUI-ассистент на **PyQt6**: чат с LLM через **llama.cpp**, vision, инструменты, долговременная память, опционально TTS и генерация/редактирование изображений. Веса моделей не входят в репозиторий — вы кладёте GGUF и прописываете пути в `config.json`.

**Платформа:** Linux (эталон — Ubuntu 24.04). **GPU:** NVIDIA + CUDA рекомендуются для комфортной работы.

## Демо

![Интерфейс чата и настройки слота](docs/assets/demo/01-chat.webp)

<p align="center">
  <img src="docs/assets/demo/02-gallery-search.webp" width="24%" alt="Поиск по галерее (gallery_search)" />
  <img src="docs/assets/demo/03-gallery.webp" width="24%" alt="Галерея работ" />
  <img src="docs/assets/demo/04-image-edit.webp" width="24%" alt="Qwen Image Edit" />
  <img src="docs/assets/demo/05-web-search.webp" width="24%" alt="Веб-поиск (SearXNG)" />
</p>

## Возможности

| Область | Что умеет |
|---------|-----------|
| **Чат** | Несколько слотов моделей, persona, сжатие контекста, tool-calling |
| **Vision** | Картинки в сообщениях (mmproj + llama.cpp mtmd) |
| **Память** | SQLite, семантический поиск по истории и long-term memory |
| **Галерея** | Индексация кадров, поиск по смыслу, vision-описания |
| **Инструменты** | Веб (SearXNG), память, камера, галерея — см. [docs/tools.md](docs/tools.md) |
| **Limbic** | Эмоциональное состояние в промпте и UI-аватар (опционально) |
| **TTS** | Silero (ручная установка весов) |
| **Картинки** | Stable Diffusion (text-to-image), Qwen Image Edit (image-edit) |
| **Интеграции** | Telegram, perception daemon (опционально) |

Шаблон конфига на **три слота**: multimodal-чат, text-to-image, image-edit — [config.example.json](config.example.json).

## Ограничения

- **Веса сами:** GGUF, mmproj, SD-checkpoint, Silero `.pt`, embedder — в `data/models/` (десятки ГБ, не в git).
- **Железо:** полноценный multimodal-чат рассчитан на дискретную NVIDIA; на CPU возможно, но медленно.
- **Vision:** для части семейств (например Gemma) multimodal-стек llama.cpp может отставать от текстового режима — см. [docs/models.md](docs/models.md).
- **ОС:** разработка и установка ориентированы на Linux; Windows/macOS не в фокусе.
- **Голосовой ввод, видеогенерация** — не реализованы.
- **Hobby-проект:** без гарантии поддержки; [MIT](LICENSE), issues и PR — см. [CONTRIBUTING.md](CONTRIBUTING.md).

## Быстрый старт

```bash
git clone <repo-url> ~/Lira
cd ~/Lira

chmod +x scripts/install-deps.sh scripts/smoke_imports.sh
./scripts/install-deps.sh          # venv, PyTorch CUDA, llama-cpp, SD, diffusers при NVIDIA
./scripts/setup.sh                 # config.json из config.example.json
cp .env.example .env               # по желанию: Telegram, SearXNG

# Положите веса в data/models/, поправьте пути в config.json
./scripts/smoke_imports.sh
./scripts/lira_start.sh
```

Подробно: [docs/getting-started.md](docs/getting-started.md).

## Структура репозитория

```
├── core/scripts/chat/     # GUI, ChatController, workers, tools, backends
├── data/                  # personas, icons; models/ и *.db — локально, не в git
├── docs/                  # документация (оглавление: docs/README.md)
├── docs/assets/demo/      # скриншоты для README (см. раздел «Демо»)
├── infra/                 # docker-compose для SearXNG и сервисов
├── scripts/               # install-deps, setup, lira_start
├── config.example.json    # шаблон слотов и путей
└── requirements*.txt      # Python-зависимости
```

## Документация

| Раздел | Файл |
|--------|------|
| Оглавление | [docs/README.md](docs/README.md) |
| Установка | [docs/getting-started.md](docs/getting-started.md) |
| Конфигурация | [docs/configuration.md](docs/configuration.md) |
| Модели и слоты | [docs/models.md](docs/models.md) |
| Проверенный стек | [docs/models-verified.md](docs/models-verified.md) |
| Генерация картинок | [docs/image-generation.md](docs/image-generation.md) |
| Архитектура | [docs/architecture.md](docs/architecture.md) |
| Инструменты | [docs/tools.md](docs/tools.md) |
| Память и БД | [docs/memory-databases.md](docs/memory-databases.md) |

## Основные модели (слоты)

Задаются в `config.json` → `models[]`. В репозитории только пример путей; скачивание и лицензии — на стороне пользователя.

| Слот (пример) | Класс | Типичный стек |
|---------------|-------|----------------|
| Lira | text + vision | Gemma-4-26B + mmproj ([models-verified.md](docs/models-verified.md)) |
| Artist | text-to-image | SD checkpoint + LoRA |
| Image Edit | image-edit | Qwen Image Edit GGUF + [Qwen/Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511) |

## Вспомогательные модели (не LLM-слоты)

Скачиваются отдельно в `data/models/`:

| Назначение | Модель | Документация |
|------------|--------|--------------|
| RAG / `memory_search` | [paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) | [memory-databases.md](docs/memory-databases.md) |
| Поиск по галерее | [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small) | [configuration.md](docs/configuration.md) → `gallery_search` |
| Эмоции (limbic) | [rubert-tiny2-russian-emotion-detection](https://huggingface.co/Aniemore/rubert-tiny2-russian-emotion-detection) | [limbic.md](docs/limbic.md) |
| TTS | [Silero v5 ru](https://github.com/snakers4/silero-models) | [docs/tts.md](docs/tts.md) |
