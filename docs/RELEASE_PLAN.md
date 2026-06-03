# План подготовки к публичному релизу (Lira → LiraAi)

Версия плана: **2026-06-02** (обновлено после первого прогона установки).

## Принцип

1. Сначала **доделать код** в рабочей копии (Lira2).
2. Документацию **вести по ходу** фаз 1–2.
3. Затем **одноразовый** перенос в **LiraAi без истории git** (export — bootstrap, не синхронизация).
4. Решения по лицензии, имени и дисклеймеру — **перед** demo и public.
5. Публичный push — не раньше фазы 8.

Ориентир по срокам: **7–12 рабочих дней** при частичной занятости.

---

## Цели релиза

| Цель | Критерий готовности |
|------|---------------------|
| Чужой человек клонирует repo | README → установка → `config.example.json` → запуск GUI (свои веса) |
| Не стыдно показать | Demo-скрины, нет личных данных в git |
| Юридически ясно | `LICENSE` (MIT) + дисклеймер в README |
| Поддержка не обещается | `CONTRIBUTING.md` / раздел Expectations |
| Удобно развивать дальше | Промпты в persona (ru/en), код читаемый |

**Вне scope v0.1:** идеальный слот 3, полная i18n всех строк, CI, en Silero (можно v0.2).

---

## Фаза 1 — Промпты, персоны, локализация

### 1.1 Персоны и промпты

- [x] Схема persona v2: `system_persona`, `additional_instructions`, `prompts.*` с **`ru` / `en`** (`persona_store.py`).
- [x] `data/personas/_template/persona.json` — при создании новой модели.
- [x] Динамические промпты из persona (vision, gallery, telegram, perception, context compress).
- [x] Миграция: `scripts/migrate_personas.py` (живые `data/personas/*.json` обновлены).
- [x] API: `get_persona_prompt`, `get_persona_text(model, key, locale=…)`.
- [x] Имя владельца в `config.user` + плейсхолдеры `{user_name*}`; миграция `migrate_persona_user_placeholders.py`.
- [x] Осталось: tool-hints в jinja / отдельные ключи.

### 1.2 Локализация UI

- [x] `ui_locale` в config (`ru` \| `en`), переключатель в сайдбаре, `infrastructure/locale/ui/{en,ru}.csv`.
- [x] Silero: `v5_5_ru.pt` / `v3_en.pt` по локали; persona `en` при `ui_locale=en`.
- [x] Дальше: остальные строки UI (настройки, галерея, лоадеры модели), Qt-ошибки.

**Definition of done:** переключение UI ru/en; промпты не размазаны по handlers; новая модель получает шаблон persona.

**Мини-решения внутри фазы (не откладывать на фазу 5):** формат JSON persona, имена ключей `prompts.*`.

---

## Фаза 2 — Код и архитектура

- [x] `pyproject.toml` / ruff (или black + isort).
- [x] Прогон по `core/scripts/chat/`: форматирование, удаление шумных комментариев, **комментарии на EN**.
- [x] `docs/architecture.md` — UI → controllers → workers → infrastructure → handlers.
- [x] Убрать мёртвый/экспериментальный код.
- [x] Согласовать логику `active_model` / `active_model_id` для example-конфига.
- [x] `__version__` в одном месте.

**Definition of done:** код читается посторонним; в main-ветке Lira2 нет хвостов вчерашних экспериментов.

---

## Фаза 3 — Документация (живая, параллельно)

- [x] Каркас `docs/` + оглавление [docs/README.md](README.md).
- [ ] Наполнять по мере фаз 1–2 и 4 (секции **TODO** в файлах).

| Файл | Когда заполнять |
|------|------------------|
| `docs/architecture.md` | Фаза 2 |
| `docs/configuration.md`, `docs/personas.md`, `docs/tools.md`, `docs/models.md`, `docs/memory-databases.md`, `docs/sens.md`, `docs/limbic.md`, `docs/external-events.md`, `docs/chat-templates.md` | Фаза 1 (черновики) |
| `docs/getting-started.md`, `docs/models-verified.md` | Фаза 4 — черновики готовы, второй прогон установки |
| `docs/telegram.md` | При настройке Telegram |

Публичный README — **финальная сборка** перед фазой 8.

---

## Фаза 4 — Установка → LiraAi (чистый repo) → гигиена

### 4.1 Зависимости и установка (в Lira2)

- [x] `requirements.txt`, `requirements-llama.txt`, `requirements-optional.txt`, `requirements-dev.txt`.
- [x] `scripts/install-deps.sh` (venv, PyTorch по `nvidia-smi` / `nvcc`, llama-cpp-python, опции SD CUDA / optional / dev).
- [x] `scripts/lira_start.sh` — `LIRA_ROOT`, `LIRA_CONFIG`, автосоздание venv при первом запуске.
- [x] `scripts/setup.sh`, `scripts/smoke_imports.sh`, `scripts/rewrite_config_paths.py`.
- [x] `infrastructure/paths.py` — `LIRA_ROOT` / `LIRA_CONFIG`, `resolve_path()` (клон не привязан к `~/Lira2`).
- [x] Документация: [getting-started.md](getting-started.md), [models-verified.md](models-verified.md), [tts.md](tts.md), [image-generation.md](image-generation.md), `.env.example`.
- [x] **Прогон установки** — один раз на рабочем ПК (клон в отдельном каталоге, GUI + GPU). Повторять / облако (RunPod, Colab) **не требуется** — hobby-проект, без SLA.

#### 4.1.1 Прогон установки (2026-06-02, закрыт)

Тестовый каталог (клон + `data/` с рабочей машины). Цель — сценарий «не ~/Lira2 по умолчанию».

| Проверено | Результат |
|-----------|-----------|
| `install-deps.sh`, `smoke_imports.sh`, `lira_start.sh` | OK |
| Запуск с `LIRA_ROOT` ≠ `~/Lira2` | OK после `paths.py` и `rewrite_config_paths.py` |
| Чат, память, semantic, gallery search | OK при корректных путях в `config.json` |
| Прикрепление изображения в чат (галерея, 📎, файл) | OK после фикса `lira_data(..., str(session_id))` |
| GPU: embedder / Lira без `CUDA_VISIBLE_DEVICES=""` | OK |
| TTS без `.pt` | OK (озвучка отключена, без падения) |
| Художница SD на GPU | `install-deps.sh` пересобирает SD с CUDA при NVIDIA; smoke ловит CPU-only — [image-generation.md](image-generation.md) |
| `.desktop` со старым путём `~/Lira2` | Путаница с данными; для теста — только `./scripts/lira_start.sh` из каталога клона |

**Исправления в коде (уже в `master` Lira2):**

- `lira_data()` — все сегменты пути через `str(...)` (иначе `current_session_id` int ломал `process_incoming_image`).
- `register_image_attachment_from_path` — `resolve_path()` для путей галереи из другого клона (`~/Lira2/...` → активный `LIRA_ROOT`).
- `gallery_vectors` при инициализации `gallery.db`.
- Убран принудительный CPU для semantic/gallery embedder.

**Не делать в тестовом прогоне:** считать ярлык `.desktop` эталоном; коммитить личный `config.json`.

**Вне scope 4.1 (осознанно):** второй физический ПК (RTX 3090 не поднялась, сервис недоступен); платное облако + SSH + качание GGUF без GUI; обязательный «чужой компьютер».

**Опционально у автора:** другой ПК на Intel iGPU / CPU — любопытство, не критерий релиза.

**Следующий шаг по плану:** фаза **4.2** — export в LiraAi + `git init` (без повторного install-deps).

### 4.2 Два `master`: Lira2 (sandbox) и **~/LiraAi** (релиз)

**Инвариант:** два согласованных снимка — `master` в Lira2 и `master` в LiraAi (GitHub). Связь не через второй `remote` в Lira2, а через **export** (`git ls-files` − `.export-ignore` → копия в папку).

| Где | Роль |
|-----|------|
| **Lira2** | Работа в **ветках ≠ master**; `master` = готово к публикации. Private git, полная история. **Без** `remote` на LiraAi. |
| **~/LiraAi** | Релизный git: demo-картинки, PR в свой `master`, push на GitHub (сначала private). Не ежедневный sandbox. |
| **export-lirai.sh** | Фильтрованное копирование; обратно: `LIRA_EXPORT_SRC=~/LiraAi LIRA_IMPORT=1` (см. ниже). |

#### A. Обычный релиз (в LiraAi не было своих коммитов)

1. Lira2: merge `feature/*` → **`master`**
2. `LIRA_EXPORT_DELETE=1 ./scripts/export-lirai.sh ~/LiraAi` (на ветке релиза в LiraAi: wipe + снимок)
3. LiraAi: PR `release/…` → **`master`**, локальный прогон, push GitHub

#### B. В LiraAi уже были изменения (PR на GitHub и т.п.)

Сначала выровнять Lira2:

1. Lira2: ветка от **`master`**, `git rm -rf --cached .` (только tracked)
2. `LIRA_EXPORT_SRC=~/LiraAi LIRA_IMPORT=1 ./scripts/export-lirai.sh ~/Lira2`
3. Lira2: PR → **`master`**, проверить diff

Затем **A**.

#### Bootstrap (первый раз)

```bash
./scripts/export-lirai.sh ~/LiraAi
cd ~/LiraAi && git init && git add -A && git status   # без config.json
# demo, setup, remote, push private
```

Прогон до public: повторить **A** на private GitHub без смены модели.

- [ ] Папка `~/LiraAi`, первый export + `git init`.
- [x] `export-lirai.sh`, `.export-ignore`.
- [ ] Private GitHub; репетиция **A**; при необходимости **B**.
- [ ] В git LiraAi нет `config.json`; есть `config.example.json`, `personas/_template/`.

### 4.3 Гигиена перед push

Чеклист на снимке в LiraAi после export; подготовка tracked-файлов — в Lira2.

- [ ] Аудит секретов в LiraAi перед push.
- [ ] `.gitignore`: `config.json`, `data/personas/`, `data/memory/`, `data/models/*`, `.env`, `logs/`.
- [x] `config.example.json` + `scripts/setup.sh` (имя владельца).
- [x] `.env.example`.
- [ ] В git: `personas/_template/`, `LICENSE`, `docs/`, скрипты; **не в git:** веса, личные персоны, production config.

**Definition of done:** clone LiraAi → setup → example config → GUI стартует (веса — локально у пользователя).

**Чужие PR в LiraAi:** без **B** следующий export из Lira2 их затрёт. Либо **B** → merge в Lira2, либо issues. В Lira2 не вешать remote на GitHub.

---

## Фаза 5 — Решения перед публикацией

- [ ] Имя проекта и repo: **LiraAi**.
- [ ] **MIT** + copyright → `LICENSE`.
- [ ] Таблица **verified models** (Gemma-4 слот 1 = production; Qwen3-VL и др. = experimental / не рекомендуется).
- [ ] README: hobby, без гарантии поддержки, PR welcome, некоммерческий **замысел** автора (юридически — MIT).
- [ ] Раздел «What is NOT included» (weights, private personas).
- [ ] `CONTRIBUTING.md` (issues без SLA).

---

## Фаза 7 — Demo

- [ ] Скрины на demo-config, нейтральный текст в чате.
- [ ] `docs/images/01-main.png` … (3–6 кадров).
- [ ] Сжатие PNG; без Telegram, имён, hostname, личных путей.
- [ ] Блок **Demo** в README (после фазы 5).

---

## Фаза 8 — Pre-release → public

- [ ] `LICENSE`, `config.example.json`, install doc актуальны.
- [ ] Один сценарий: чат + один tool на verified-модели.
- [ ] README финальный, ссылки на `docs/` не битые.
- [ ] GitHub: description, topics.
- [ ] **Public** LiraAi.

---

## Дорожная карта (кратко)

```
Фаза 1  Промпты + persona ru/en + i18n UI
    ↓
Фаза 2  Код-стайл, architecture.md, чистка
    ↓  (параллельно)
Фаза 3  docs/ + черновик README
    ↓
Фаза 4  requirements + install test → LiraAi (fresh git) → audit + example config
    ↓
Фаза 5  MIT, имя, verified models, дисклеймер, CONTRIBUTING
    ↓
Фаза 7  Demo
    ↓
Фаза 8  Public
```

---

## Судьба Lira2

Единственная среда для IDE и экспериментов. **~/LiraAi** — релизный репозиторий; синхронизация с `master` Lira2 только через export (**A** / **B**). Публичной истории Lira2 на GitHub нет.

---

## Риски

| Риск | Митигация |
|------|-----------|
| Установка только у автора | Фаза 4.1 закрыта одним прогоном на рабочем ПК; чужие машины — на усмотрение пользователя |
| Пути `~/Lira2` в config и gallery.db | `LIRA_ROOT` + `rewrite_config_paths.py`; `resolve_path` в attach |
| `session_id` int в `Path.joinpath` | `lira_data()` приводит сегменты к `str` |
| Слот 3 портит впечатление | Не в demo; experimental в `models-verified.md` |
| Секреты в старом git | Новый repo без history (фаза 4.2) |
| Расхождение master Lira2 / LiraAi | **A** или сначала **B** (§ 4.2) |
| Второй `remote` на LiraAi в Lira2 | **Не вешать** |
| PR в LiraAi без **B** | Затираются следующим export из Lira2 |
| Форки продают код на MIT | Ожидания в README; имя LiraAi — не товарный знак без отдельной политики |
| Запросы поддержки | CONTRIBUTING: без SLA |

---

## Связанные артефакты (создать по ходу)

```
LICENSE
README.md
CONTRIBUTING.md
config.example.json
.env.example
requirements.txt
requirements-optional.txt
personas/_template/
docs/
  architecture.md
  configuration.md
  tools.md
  chat-templates.md
  telegram.md
  getting-started.md
  models-verified.md
  images/
scripts/setup.sh
scripts/lira_start.sh
scripts/lira_stop.sh
.export-ignore
scripts/export-lirai.sh
```
