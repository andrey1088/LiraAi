# Локализация интерфейса

Переводы: **`core/scripts/chat/infrastructure/locale/`** — отдельный CSV на домен и локаль.

| Домен | Файлы | В коде |
|-------|--------|--------|
| UI | `locale/ui/en.csv`, `locale/ui/ru.csv` | **Английский текст = msgid** |
| LLM tools | `locale/tools/en.csv`, `locale/tools/ru.csv` | Ключи `tools.*`, `policies.*` |

```csv
key,ru
Interface language,Язык интерфейса
Save,Сохранить
```

## API

| Слой | Вызов |
|------|--------|
| HTML | `<span data-i18n="Interface language">Interface language</span>` |
| JS | `this.t('Save')` |
| Python UI | `tr("Interface language", locale)` — [`infrastructure/locale/i18n.py`](../core/scripts/chat/infrastructure/locale/i18n.py) |
| Python tools | `tr_tools("tools.memory_search.description", locale)` — keys from `tools/*.csv`, not English msgids (`en` reads `tools/en.csv`) |

Загрузчик: [`locale/loader.py`](../core/scripts/chat/infrastructure/locale/loader.py).

## Инструменты чата

Handlers и схема: [`core/scripts/chat/tools/`](../core/scripts/chat/tools/).

## Variables (`locale/variables/`)

Data that is **not** UI labels: intent needles (`intent.*`), gallery stopwords, limbic mood text, default persona lines, context-compression prompts, perception/Telegram copy, semantic RAG tags, template junk markers.

| Files | API |
|-------|-----|
| `variables/en.json`, `variables/ru.json` | [`variables.py`](../core/scripts/chat/infrastructure/locale/variables.py): `var_get`, `var_list`, `var_dict`, `var_frozenset` |

```python
from infrastructure.locale.variables import var_get, var_list

needles = var_list("intent.web_message_substrings", locale)
role_hdr = var_get("chat.system_role_header", locale)
```

Main sections: `intent`, `gallery_query`, `limbic`, `memory`, `detection`, `persona`, `chat`, `semantic`, `perception`, `telegram`, `templates`, `gallery`, `emotion`, `world_state`, `recall_knowledge`, `user`.

`tool_policies.json` holds English source text for `localize_tool_policy_registry`; runtime denials use `tr_tools` via [`tool_policy_registry._policy_tools_text`](../core/scripts/chat/infrastructure/config/tool_policy_registry.py) (no hardcoded locale in Python). Default persona seeds: [`persona/defaults.py`](../core/scripts/chat/infrastructure/persona/defaults.py).

## Persona vs UI

`ui_locale` does not replace file-based overrides in `data/personas/`.

## Code

Comments and docstrings in `core/scripts/chat/`, `core/web/`, and related scripts are **English**.  
User-visible text uses CSV / `tr()` / `tr_tools()` / `variables` — no Cyrillic in `.py`, `.js`, or `.html` (except under `locale/**` CSV and `variables/*.json`).
