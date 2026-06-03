# Модели

Слоты моделей задаются в `config.json` → `models[]`. Тип поведения определяет поле **`model_class`**; конкретный стек (Gemma, Qwen, SD) — **`model_type`**, пути к весам и handler в коде.

Список проверенных GGUF и коммитов llama.cpp: [models-verified.md](models-verified.md).  
Пути, `n_ctx`, GPU: [configuration.md](configuration.md).

## Текстовые модели

**`model_class`: `text`** (значение по умолчанию, если поле не задано).

- Инференс: **llama.cpp** через `llama-cpp-python` (`infrastructure/model_backends/text_llama.py`).
- Один GGUF в `model_path`, опционально **Jinja**-шаблон в `template_path`.
- Чат, инструменты, память, сжатие контекста, TTS — полный цикл `ChatController` → `ModelWorker`.
- Без `clip_model_path` модель **не видит** картинки в чате (только текст вложений/PDF-текст, если есть).

Типичные поля слота:

| Поле | Назначение |
|------|------------|
| `model_path` | GGUF |
| `template_path` | `chat_template.jinja` |
| `persona_file` | персона и промпты |
| `db_path` | SQLite памяти слота |
| `chat_format` | подсказка для loader |
| `settings` | `temperature`, `n_ctx`, `n_gpu_layers`, бюджет контекста |

## Мультимодальные модели

Отдельного `model_class: multimodal` нет: это **тот же `text`**, но с **`clip_model_path`** (mmproj) и vision-**chat_handler** (Gemma3/4, Qwen3-VL, Qwen3.5 и т.д.).

- Картинки в сообщении пользователя уходят в модель как vision-контент.
- Нужны инструменты **`camera_capture`** и осмысленный **`gallery_search`** (vision-подвызов по найденным кадрам).
- Промпты vision/gallery — ключи в [personas.md](personas.md) (`vision_system`, `gallery_describe_*`).
- Для UI-аватара эмоций и фона «жизни» — `limbic_images_path`, `perception_daemon` (см. [limbic.md](limbic.md), [external-events.md](external-events.md)).

Проверка «есть vision»: непустой `clip_model_path` (`gallery/capabilities.py`).

### Vision, mmproj и chat template (ограничения Gemma)

Описание картинок в чате, галерее и vision-подвызовах `gallery_search` идёт через **Llava15ChatHandler** (llama-cpp-python → llama.cpp **mtmd** / vision projector), а не через Jinja из `template_path`.

| Режим | Какой шаблон |
|--------|----------------|
| Текстовый чат, tools | `template_path` или `tokenizer.chat_template` из GGUF (для Gemma-4 — свой jinja в `data/models/…`) |
| Vision (картинка в сообщении) | Упрощённый turn-формат в коде (`Gemma4ChatHandler.CHAT_FORMAT` и аналоги), по сути опора на **дефолтный multimodal-стек llama.cpp**, который **не заточен под семейство Gemma** так же, как родной chat template модели |

То есть для vision используется не тот же шаблон, что для обычного диалога; качество промпта и декодирования на стороне библиотеки может отставать от текстового режима.

В Lira добавлена **постобработка** vision-ответов (см. `infrastructure/templates/gemma4_vision.py`, `workers/model_worker.py`): снятие остатков `thought` / `<|channel|>`, обрезка зацикливаний, ужесточённые `repeat_penalty` / `temperature` / `max_tokens`. **Несмотря на это**, при описании изображений возможны глюки: пустой или обрезанный ответ, повторы токенов, служебный мусор в начале строки.

**Ожидание:** улучшения в upstream **llama.cpp** и **llama-cpp-python** (корректный multimodal chat template для Gemma и смежных семейств). После обновления зависимостей — пересмотреть handler и прогнать vision-smoke локальными утилитами (вне репозитория). Версии стека: [models-verified.md](models-verified.md).

Подробнее про разделение jinja и vision: [chat-templates.md](chat-templates.md#vision-и-jinja).

## text-to-image модели

**`model_class`: `text-to-image`**.

- Бэкенд: **Stable Diffusion** (`infrastructure/model_backends/image_sd/generator.py`).
- Слот хранит пути к чекпоинту/VAE, `settings` (`steps`, `guidance_scale`, …).
- Ответ в UI — **картинка на canvas**, не поток токенов чата.
- `persona_file` / `db_path` могут быть заданы для единообразия конфига; чатовый tool-цикл для этого класса **не используется**.
- Сгенерированные файлы попадают в **галерею** (`data/gallery/`, БД `gallery.db`) — см. [memory-databases.md](memory-databases.md).

## image-edit модели

**`model_class`: `image-edit`**.

- Бэкенд: **Qwen Image Edit** (`infrastructure/model_backends/image_qwen/generator.py`).
- Редактирование по промпту и исходному изображению (веса HF, `settings.hf_repo_id` и др.).
- Как и text-to-image: отдельный UI-поток, без tool-цепочки основного чата.
- Результаты также могут индексироваться в галерее.

## Выбор handler и шаблона

| Условие | Handler / примечание |
|---------|----------------------|
| `model_type` Gemma-4-26B + mmproj | `Gemma4ChatHandler` |
| Qwen3-VL | `Qwen3VLChatHandler` |
| Qwen3.5 (hybrid) | `Qwen35ChatHandler` |
| прочий Qwen + mmproj | `Gemma3ChatHandler` (legacy path) |
| только GGUF | стандартный `Llama` без vision |

При добавлении новой семьи обычно нужны: handler в `infrastructure/templates/`, jinja в `data/models/<…>/`, запись в [models-verified.md](models-verified.md).

## Связанные документы

- [personas.md](personas.md) — характер и промпты слота  
- [chat-templates.md](chat-templates.md) — jinja, роли `sens` / `limbic`  
- [tools.md](tools.md) — какие инструменты доступны текстовым/vision-слотам  
