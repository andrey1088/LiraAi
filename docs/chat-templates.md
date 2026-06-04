# Chat templates (Jinja)

## Назначение

- Форматирование истории для **llama.cpp** (`tokenizer.chat_template` или файл `template_path` у слота).
- Роли служебных сообщений в промпте:
  - **[sens](sens.md)** — время, дата, GPU/CPU;
  - **[limbic](limbic.md)** — внутреннее настроение (если включено для слота).

Примеры шаблонов: `data/models/<семейство>/chat_template.jinja`.

## Где задаётся

В `config.json` у слота: `"template_path": "~/Lira/data/models/.../chat_template.jinja"` (или под ваш `$LIRA_ROOT`).

Тексты персоны и инструкции — **не** в jinja, а в [personas.md](personas.md).  
Класс модели и handler: [models.md](models.md).

## Tools в промпте

Блок `<tools>` / function calling зависит от handler (Gemma4, Qwen3-VL, Qwen3.5). Список инструментов — [tools.md](tools.md).

## Vision и Jinja

`template_path` применяется к **текстовому** чату слота (история, sens, limbic, tools).

Для **vision** (mmproj, картинка в `content`) Lira вызывает **Llava15ChatHandler** из llama-cpp-python: промпт собирается через встроенный multimodal-стек **llama.cpp**, а не через ваш `chat_template.jinja`. Для Gemma-4 в коде подставляется упрощённый turn-формат (`Gemma4ChatHandler.CHAT_FORMAT`), но это всё ещё не полноценный Gemma-шаблон из GGUF.

**Следствие:** дефолтный vision-шаблон библиотеки **слабо согласован с семейством Gemma**; возможны артефакты при описании картинок (пустой ответ, циклы, остатки `thought` / channel). В репозитории есть постобработка ответа, однако она не гарантирует стабильность — ждём обновления **llama.cpp** / **llama-cpp-python**.

Подробнее: [models.md — Vision, mmproj и chat template](models.md#vision-mmproj-и-chat-template-ограничения-gemma).

## Отладка

- Логи `[SENS]`, `[LIMBIC]`, `[TOOLS]`, `[CHAT]` в `logs/lira.log`
- Smoke vision вне Lira: локальные утилиты/CLI (в репозитории не хранятся)
