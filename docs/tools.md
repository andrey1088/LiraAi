# Инструменты (tools)

Модель вызывает инструменты в формате function calling; сервер выполняет Python-функцию и возвращает результат в историю (`role=tool`), после чего возможен следующий шаг цепочки.

**Когда звать tool** — в первую очередь из **persona** (`additional_instructions`) и политик; код лишь **разрешает или отклоняет** вызов по контексту сообщения.

## Каталог инструментов

| Имя | Назначение | Ограничения |
|-----|------------|-------------|
| `memory_search` | Семантический поиск по **долговременной памяти** и релевантным фрагментам переписки слота | Только текст; не для картинок; не два подряд за один ответ |
| `gallery_search` | Поиск по **локальной галерее** (описания + эмбеддинги в `gallery.db`) | Только при явном запросе визуала/галереи; vision-слот; обычно первый tool в цепочке |
| `camera_capture` | Один кадр с **веб-камеры** для vision-ответа | Явный запрос камеры; нужен mmproj; первый tool в цепочке |
| `web_search` | Поиск в интернете (**SearXNG**) | Явный запрос «из сети»; не для времени/GPU из sens |
| `web_search_saved` | Повторная выдача **уже сохранённого** SERP по `research_run_id` | После `web_search`, без нового запроса в сеть |
| `web_fetch_url` | Загрузка и разбор **страницы по URL** | Только после `web_search` в том же ответе; один URL за раз |

Отдельно (не в основном меню чата):

| Имя | Назначение |
|-----|------------|
| `telegram_life_eval` | Оценка обмена в Telegram-боте: нужно ли **прервать владельца** в десктоп-чате (`should_notify_andrey`) |

Схема `recall_knowledge` в старых шаблонах — устаревшее имя; в коде зарегистрирован **`memory_search`**.

## Sens и инструменты

Данные **sens** (время, дата, GPU/CPU) — только из блока [sens](sens.md) в промпте. Для «который час» и нагрузки железа **не** используют `web_search`, `gallery_search`, `web_fetch_url` (см. persona).

## Telegram

В ответах **посторонним** в боте набор tools **отключён** — см. [telegram.md](telegram.md), [external-events.md](external-events.md).

## Политики вызова

Файл `core/scripts/chat/infrastructure/config/tool_policies.json` задаёт метаданные `x_lira_*` (мержатся в schema в рантайме):

- подстроки в сообщении пользователя для **web** / **gallery** / **camera**;
- запрет повторного tool подряд (`forbidden_if_last_tool`);
- допустимые **шаги** цепочки (`only_at_chain_steps`);
- порядок follow-up (`followup_tools`).

Отказ сервера возвращается модели текстом; она должна ответить без tool.

## Цепочка tools

- Лимит шагов: `MAX_TOOL_CHAIN_STEPS` (по умолчанию 10) в `ChatController`.
- Длинные ответы tool **обрезаются** при записи в историю (`_TOOL_HISTORY_MAX_CHARS`).
- Спец-логика: `web_fetch_url` без предшествующего `web_search` в том же turn — отказ.

## Как добавить новый инструмент

1. **Реализация** — модуль в `core/scripts/chat/tools/`, сигнатура вида:
   ```python
   def my_tool(arg1: str, *, repository, semantic_engine=None, window=None, **kwargs) -> str:
       ...
   ```
   Возвращайте **строку** для модели (без сырого JSON пользователю, если не нужно).

2. **Schema для LLM** — блок в `_chat_tool_schema_base()` в `app/chat_controller.py` (`type: function`, `name`, `description`, `parameters`).

3. **Регистрация** — словарь `self.available_tools` в `ChatController.__init__`:
   ```python
   "my_tool": my_tool,
   ```

4. **Политики (опционально)** — правила в `infrastructure/config/tool_policies.json` и ключи `x_lira_*` (см. `infrastructure/config/tool_policy_registry.py`).

5. **Persona** — когда модель *должна* / *не должна* вызывать tool (русские формулировки в `additional_instructions`).

6. **Особые ветки** — если tool нужен нестандартный UI (как `gallery_search` / `camera_capture`), смотрите `ChatController` после `TOOL_CALL|…` и обработку в `on_worker_finished`.

7. **Память исследований** — для web-подобных сценариев может понадобиться запись в `research_runs` / `artifacts` ([memory-databases.md](memory-databases.md)).

Публичный набор tools для релиза лучше держать **маленьким и стабильным**; экспериментальные — за флагом или отдельной веткой.

## Файлы

| Путь | Роль |
|------|------|
| `app/chat_controller.py` | schema, `call_tool`, цепочка |
| `tools/*.py` | реализации |
| `infrastructure/config/tool_policies.json` | intent и ограничения |
| `infrastructure/config/tool_policy_registry.py` | merge policy → schema |
