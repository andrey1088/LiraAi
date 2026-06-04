# Персоны и промпты

Каждый текстовый слот модели имеет файл персоны: `data/personas/<model_type>-<id>.json` (путь в `config.json` → `persona_file`).

## Схема v2

```json
{
  "schema_version": 2,
  "locale_default": "ru",
  "system_persona": { "ru": "…", "en": "…" },
  "additional_instructions": { "ru": ["…"], "en": ["…"] },
  "prompts": {
    "vision_system": { "ru": "…", "en": "…" }
  }
}
```

- **system_persona** + **additional_instructions** → основной system prompt чата.
- **prompts** → vision, gallery, Telegram, сжатие контекста и т.д. (см. таблицу в коде `persona_defaults.py`).

Шаблон для новой модели: `data/personas/_template/persona.json`.

Миграция старых файлов: `scripts/migrate_personas.py`.

## Плейсхолдеры

Подставляются при сборке текста (`PersonaStore.format_text`):

| Плейсхолдер | Источник |
|-------------|----------|
| `{model_name}` | `models[].name` в config |
| `{user_name}` | `config.user.display_name` |
| `{n_images}` | только в отдельных вызовах (vision batch) |

**Правило:** не пишите имя владельца в persona буквально — только плейсхолдеры. Имя задаётся в глобальном `config.json` → `user` (при установке — `scripts/setup.sh`). См. [configuration.md](configuration.md#блок-user-имя-владельца).

Пример:

```json
"system_persona": {
  "ru": "Ты — {model_name}, персональный ИИ-ассистент владельца ({user_name}). Общайся на «ты».",
  "en": "You are {model_name}, personal assistant for {user_name}."
}
```

## API в коде

- `config_repo.get_persona_prompt(model)` — system + instructions
- `config_repo.get_persona_text(model, "vision_system", …)` — ключ из `prompts`

## Миграция persona

```bash
~/Lira2/venv/bin/python3 ~/Lira2/scripts/migrate_persona_user_placeholders.py   # имя → {user_name}
~/Lira2/venv/bin/python3 ~/Lira2/scripts/simplify_persona_declensions.py      # убрать {user_name_*}
```

В Telegram-промптах: «владелец», `{user_name}` в именительном; не называть собеседника именем владельца.

Если правите persona вручную, сверяйтесь с `persona_defaults.py` и `_template/persona.json`. Редактирование persona в UI пока только через JSON на диске.
