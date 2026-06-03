# Структура памяти и баз данных

Lira хранит данные **локально** в SQLite (и файлах на диске). Пути задаются в `config.json`; в git не коммитятся.

## Обзор файлов

| Хранилище | Типичный путь | Назначение |
|-----------|---------------|------------|
| Память слота модели | `data/memory/<model_type>-<id>.db` | Чаты, долгая память, limbic, web-research |
| Галерея | `data/db/gallery.db` (или путь из config) | Генерации SD/Qwen, описания, векторы поиска |
| Медиа сессии | `data/memory/media/<model_type>-<id>/<session_id>/` | Вложения чата (картинки, PDF-страницы) |
| Веса / кэши | `data/models/`, `data/gallery/` | Вне БД |

У каждого **текстового слота** свой `db_path` и свой `persona_file` — память диалогов не смешивается между моделями.

## База слота модели (`db_path`)

Класс `ChatRepository` в `infrastructure/memory/repo.py` создаёт схему при первом открытии.

### Чат

| Таблица | Содержимое |
|---------|------------|
| `chat_sessions` | Сессии UI (заголовок, даты) |
| `chat_messages` | Сообщения: `role`, `content`, опционально `image_path` (JSON-массив путей) |
| `chat_context_summary` | Сжатое резюме старых сообщений сессии (rolling summary) |

Сообщения с текстом канала Telegram **не пишутся** в UI-историю (`is_telegram_channel_user_content`) — они живут в контуре perception.

### Долгосрочная память

| Таблица | Содержимое |
|---------|------------|
| `long_term_memory` | Пары user/assistant для RAG и `memory_search` |
| `long_term_vec` | Векторы (sqlite-vec, 384 dim) к записям памяти |

Поиск: `memory_search` + `SemanticEngine` / embedder.

### Состояние модели

| Таблица | Содержимое |
|---------|------------|
| `model_limbic_state` | JSON вектора эмоций (одна строка на БД слота) |
| `model_perception_meta` | Метаданные perception (например `perception_stopped_at`) |

### Исследовательский контур (web)

Используется цепочкой `web_search` → `web_fetch_url`:

| Таблица | Содержимое |
|---------|------------|
| `research_runs` | Запрос пользователя, статус, `research_run_id` |
| `research_steps` | Шаги (search, fetch, …) |
| `sources` | Источники (домен, доверие) |
| `artifacts` | URL, заголовки, хеши, срок хранения |
| `artifact_payloads` | Текст страницы, summary |
| `artifact_files` | Файлы на диске |
| `run_artifacts` | Связь run ↔ artifact |
| `research_reports` | Отчёты (markdown/json) |
| `tool_events` | Лог вызовов tools в run |

### Legacy

| Таблица | Содержимое |
|---------|------------|
| `history` | Контекст между чатами (рабочая история диалогов) |

## База галереи (`gallery.db`)

Отдельный файл; `ChatRepository` с путём, содержащим `gallery.db`, вызывает `_init_gallery_db()`.

| Таблица | Содержимое |
|---------|------------|
| `generations` | `prompt`, `file_path`, `model_name`, `description` (русское описание для поиска), `settings_json`, … |
| `gallery_vectors` | Эмбеддинги описаний (поиск `gallery_search`) |

Настройки эмбеддера: `config.json` → `gallery_search`, `gallery_describe` ([configuration.md](configuration.md)).

Пакетное описание кадров: subprocess / очередь в `ChatController` (промпты `gallery_describe_*` в persona).

## Жизненный цикл

- **Новый слот модели** — при первом обращении `config_repo` создаёт `persona_file` и `db_path`, если не заданы.
- **Удаление сессии** — сообщения в БД + каталог медиа сессии (`delete_session`).
- **Смена `user.display_name`** — старый текст в `chat_messages` / `long_term_memory` **не переписывается** (см. [configuration.md](configuration.md)).

## Связанные документы

- [tools.md](tools.md) — `memory_search`, `gallery_search`, web-tools  
- [personas.md](personas.md) — что модель должна помнить/искать  
- [external-events.md](external-events.md) — события вне UI-чата  
