# Конфигурация

Файл: `~/Lira2/config.json` (локальный, в git не коммитить с личными данными).

## Корневые поля

| Поле | Назначение |
|------|------------|
| `active_model_id` | id слота модели в UI |
| `active_model` | отображаемое имя (legacy) |
| `ui_locale` | Язык UI и промптов persona: `ru` \| `en` (сохраняется при смене в сайдбаре) |
| `tts` | Активный Silero: `locale`, `model_path`, `speaker`, `sample_rate`, `en_speaker` |
| `active_model` | Имя слота (синхронизируется с `active_model_id` при переключении модели) |

Блок **`tts`** обновляется вместе с `ui_locale` (как `active_model_id` + `active_model`):

```json
"tts": {
  "locale": "ru",
  "model_path": "~/Lira2/data/models/v5_5_ru.pt",
  "speaker": "kseniya",
  "sample_rate": 48000,
  "en_speaker": "en_21"
}
```

При `ui_locale: en` подставляется `v3_en.pt` и `speaker` / `en_speaker`. Для `ru` голос по умолчанию из `models[].voice`.

## Локализация интерфейса

Переводы UI: [`infrastructure/locale/ui/en.csv`](../core/scripts/chat/infrastructure/locale/ui/en.csv) и [`ru.csv`](../core/scripts/chat/infrastructure/locale/ui/ru.csv). **В коде пишете английский текст** (как msgid в gettext); в `ru.csv` колонка `key` — тот же английский, `ru` — перевод.

**Добавить перевод UI:**

1. Добавьте строку в `en.csv` и `ru.csv` с одинаковым `key` (английская фраза). При запятых — кавычки в CSV.
2. **HTML**: `data-i18n="Save"` и видимый текст на английском.
3. **JS**: `this.t('Save')` или `this.t('Done: {done} of {total}', { done, total })`.
4. **Python**: `tr("Interface language", config_repo.get_ui_locale())`.
5. Перезапуск для подхвата CSV; смена `ui_locale` в UI пересобирает tools без перезапуска.

Смена языка в сайдбаре сохраняет `ui_locale` и блок `tts` в `config.json`. Подробнее и сравнение с библиотеками: [i18n-ui.md](i18n-ui.md).

Переводы **persona** (`data/personas/*.json`) — отдельно от `infrastructure/locale/`; на них влияет тот же `ui_locale`, но файл персоны не редактируется через CSV.

**Инструменты чата (memory, gallery, web, camera):** переводы — [`infrastructure/locale/tools/en.csv`](../core/scripts/chat/infrastructure/locale/tools/en.csv), [`ru.csv`](../core/scripts/chat/infrastructure/locale/tools/ru.csv); схема и handlers — [`core/scripts/chat/tools/`](../core/scripts/chat/tools/) (`llm/` + `memory_search.py`, …). Подробнее: [i18n-ui.md](i18n-ui.md).

**Описания галереи:** при `ui_locale: ru` в БД принимаются русские описания (порог кириллицы, redescribe при англ. начале). При `en` — зеркально (латиница, redescribe при кириллическом начале). Уже сохранённые описания при «Исправить описания» проверяются по **языку текста**, а не только по текущему `ui_locale`, чтобы не помечать все старые RU-записи битыми после смены языка UI.

| `user` | **владелец** Lira — имя для подстановки в персоны |
| `models[]` | слоты моделей |
| `gallery_search` | эмбеддинги и лимиты галереи |
| `gallery_describe` | пакетное описание кадров |

## Блок `user` (имя владельца)

Имя **глобальное** для всего приложения: один владелец, все слоты моделей. В persona — плейсхолдер `{user_name}`; в текстах по возможности «владелец», без склонений в конфиге.

**Первый запуск:** `scripts/setup.sh` (скопирует `config.example.json` → `config.json`, если нужно, и спросит имя).

**Позже:** правка `user.display_name` вручную. Можно изменить, но в SQLite уже могут быть реплики со старым именем — модель иногда путается.

```json
{
  "app": {
    "product_name": "Лира"
  },
  "user": {
    "display_name": "Андрей",
    "display_name_genitive": "Андрея",
    "display_name_dative": "Андрею",
    "display_name_instrumental": "Андреем"
  }
}
```

| Плейсхолдер | Источник |
|-------------|----------|
| `{user_name}` | `user.display_name` |
| `{user_name_genitive}` | `user.display_name_genitive` (fallback: `display_name`) |
| `{user_name_dative}` | `user.display_name_dative` |
| `{user_name_instrumental}` | `user.display_name_instrumental` |
| `{model_name}` | `models[].name` активного слота |
| `{app_name}` | `app.product_name` или `variables/*/app.product_name` |

Имена моделей в слотах (`models[].name`: «Лира», «Ава», …) — это `{model_name}`, не путать с `{app_name}` (бренд приложения в UI).

Подробнее: [personas.md](personas.md).

## Слот модели (`models[]`)

| Поле | Назначение |
|------|------------|
| `id`, `name` | идентификатор и имя в UI |
| `model_class` | `text`, `text-to-image`, `image-edit` |
| `model_type` | тип для выбора handler |
| `model_path`, `clip_model_path` | GGUF / mmproj |
| `template_path` | jinja chat template |
| `persona_file` | `~/Lira2/data/personas/…json` |
| `db_path` | SQLite памяти |
| `settings` | `temperature`, `n_ctx`, `n_gpu_layers`, … |

При первом обращении к модели создаются `persona_file` и `db_path`, если не заданы.

Шаблон без личных данных: `config.example.json` (в git) — **три слота** (text/multimodal, `text-to-image`, `image-edit`), заглушки путей к весам, без `persona_file` / `db_path` (создаются при первом использовании). Локальный `config.json` — не коммитить; дополнительные слоты — копией и правкой id.
