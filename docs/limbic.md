# Limbic

**Limbic** — внутреннее «настроение» ассистента: вектор эмоций влияет на тон ответа, но **не показывается** пользователю как отдельный блок (persona запрещает цитировать `[LIMBIC_STATE]`).

## Модель данных

Код: `infrastructure/limbic/state.py`, `EMOTION_LABELS` (neutral, happiness, sadness, enthusiasm, fear, anger, disgust).

- **baseline** — спокойное состояние по умолчанию;
- **current** — текущий вектор (0…1 по осям);
- шаги `blend_signal`, `step_toward_baseline`, `decay_until_baseline` — плавные переходы.

Сохранение в SQLite слота: таблица `model_limbic_state` ([memory-databases.md](memory-databases.md)).

## Включение для слота

В `config.json` у модели:

| Поле | Назначение |
|------|------------|
| `limbic_images_path` | Каталог PNG по эмоциям (`neutral.png` обязателен) |
| `perception_daemon` | Фоновый decay / Telegram / проактив (только вместе с limbic) |
| `template_path` | Jinja с ролью `limbic` |

Проверки: `infrastructure/limbic/assets.py` (`model_limbic_enabled`, `model_limbic_prompt_enabled`).

Без каталога картинок limbic в промпт **не** подмешивается (UI-аватар эмоций тоже отключён).

## Текст для LLM

`infrastructure/limbic/prompt.py` → `render_limbic_prompt()`:

- сравнивает `current` с `baseline`;
- если отличий мало — возвращает `None` (блок не вставляется);
- иначе короткая русская фраза («сейчас ты …», поведенческая подсказка) + запрет цитировать блок.

В `ModelWorker` это сообщение с ролью **`limbic`** (после `sens`, если оба есть). В логах: `[LIMBIC]`.

## Откуда меняется состояние

- **BERT / детектор** по тексту пользователя (`infrastructure/limbic/emotion_detector.py`) — сдвиг `current` после реплик;
- **Perception daemon** — периодическое затухание к baseline, когда Lira неактивна ([external-events.md](external-events.md));
- восстановление из БД при старте сессии.

## UI

Эмоция может отображаться **портретом** модели (file URL из `limbic_images_path`), без вывода шкал в чат.

## Persona

В `additional_instructions` обычно есть пункт: окрашивать ответ настроением, **не** цитировать limbic пользователю. Детали тона — в persona слота, не в коде limbic.

## Связанные документы

- [chat-templates.md](chat-templates.md) — роли в jinja  
- [sens.md](sens.md) — соседний служебный блок в промпте  
- [external-events.md](external-events.md) — daemon и проактив  
