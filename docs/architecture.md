# Архитектура (Lira → ChatController → Tools)

## Слои и ответственность

- `core/scripts/chat/gui.py`: запуск Qt приложения и обвязка UI.
- `core/scripts/chat/app/chat_controller.py`: оркестратор диалога:
  - строит `system` блок (persona + RAG-память + sens/limbic);
  - запускает `ModelWorker`;
  - исполняет tool-calls и поднимает follow-up pass после получения результата инструмента.
- `core/scripts/chat/app/model_controller.py`: загрузка/выгрузка LLM и GPU/llama.cpp hygiene (в т.ч. корректное освобождение контекста).
- `core/scripts/chat/app/context_manager.py`: сборка “финального набора сообщений” для llama.cpp:
  - trim/summarization по токен-бюджету;
  - flattening контента разных типов (текст/картинки/вложения);
  - подготовка системы под конкретные чаты-шаблоны моделей.
- `core/scripts/chat/workers/model_worker.py`: фактический вызов llama.cpp и постобработка результата (включая tool_calls).
- `core/scripts/chat/infrastructure/...`: инфраструктура:
  - `config_repo`, `persona_store`;
  - `semantic_engine` + memory репозитории для RAG;
  - `infrastructure/locale` (UI и tool-подсказки локализуются отдельно);
  - `lifecycle/perception_daemon` (проактивные внешние события: Telegram и др.).
- `core/scripts/chat/infrastructure/templates/...` и `data/models/*/chat_template.jinja`:
  - chat handlers и Jinja-шаблоны, которые гарантируют правильную сериализацию ролей (system/sens/limbic/tool) для конкретной модели.
- `core/scripts/chat/tools/...`:
  - сами инструменты (web/gallery/memory/camera);
  - tool schema и политики последовательности/запретов вызовов.

## Поток сообщения (основной диалог)

1. UI добавляет user-сообщение в `session history`.
2. `ChatController` формирует `system` блок:
   - persona из `persona_file`;
   - (опционально) RAG из long-term памяти на основе `semantic_engine.search(...)`.
3. `ContextManager.build_context(...)` превращает history в “сообщения для модели” с учетом бюджета.
4. `ModelWorker` вызывает llama.cpp через chat handler/шаблон.
5. Если модель вернула tool-call:
   - `ChatController.call_tool(...)` выполняет инструмент и добавляет результат в `history` как role=`tool`;
   - запускается follow-up worker с ограниченным меню и follow-up подсказкой.

## Конфигурация vs persona

| Что | Где |
|-----|-----|
| Пути к GGUF, GPU, n_ctx, параметры слота | `config.json` → `models[i]` |
| Инструкции/“характер” + dynamic prompts (vision/gallery/telegram/perception/context compress) | `data/personas/*.json` |
| Имя владельца и плейсхолдеры (`{user_name*}`) | `config.json` → `user` |

См. `docs/personas.md`, `docs/configuration.md`, `docs/models.md`, `docs/tools.md`, `docs/memory-databases.md`, `docs/external-events.md`.
