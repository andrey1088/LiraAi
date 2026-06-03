Проект Lira2

Инициализация локального окружения

python3 -m venv venv

pip install --upgrade pip

----------------------------------

Старт и остановка локального окружения

source venv/bin/activate - start env

deactivate - stop env

-----------------------------------

gio launch ~/.local/share/applications/lira.desktop

Запуск файла под локальным окружением

./venv/bin/python3 core/scripts/index_gallery.py


Name: llama_cpp_python
Version: 0.3.19

Слежение за файлом

tail -f data/train_data.jsonl 

------------------------------------

Проверка памяти CPU

htop

Проверка GPU

nvidia-smi

------------------------------------

Обновить иконку

gtk-update-icon-cache -f -t ~/.local/share/icons

------------------------------------

Репозиторий голосовой модели

https://github.com/snakers4/silero-models


-------------------------------------


Убить процесс дебаггера в хромиум

pkill -9 QtWebEngineProcess



Рейтинг моделей

1. Gemma-3-12B - самая умная
2. Qwen2-VL-7B
3. Saiga - та же Джемма, но обученная немного Сбером
4. Loki-v2-75b-8b - плоховато с русским, нет мультимодальности, глючит
5. Llama-3-2 Плохой русский. Постоянно выдает английский текст и валит озвучку
6. ruGPT-3.5 - выдает какие-то странные символы, невозможно разговаривать, весит 7.1 Гб

### Lira2 / multimodal и Qwen (2026-05-20 — 2026-05-21)

**Стек:** `llama-cpp-python` 0.3.23 (CUDA), RTX 5070 Ti 16 GB, `n_gpu_layers=32`. Handlers: `Gemma4ChatHandler`, `Qwen35ChatHandler` (hybrid 3.5/3.6), `Qwen3VLChatHandler` (нативный VL). Эталон vision вне Lira: `llama-mtmd-cli` @ `llama.cpp` **f47a246**.

**Слоты `config.json` (три текстовых multimodal):**

| id | Имя | Модель | Статус |
|----|-----|--------|--------|
| **1** | Лира | Gemma-4-26B | **Продакшен** (основной голос) |
| **2** | Ава | Gemma-3-12b-null-space | Второй слот Google |
| **3** | Лиза | Qwen3-VL-30B-A3B (веса на диске) | **Занят временно** — сменим, когда найдём multimodal **лучше по качеству**; в прод не идёт |

`active_model` / `active_model_id` в конфиге — **динамические** (последний выбор в UI), в git не фиксируем как «какая модель должна быть активна».

---

#### Результаты экспериментов (нумеровано)

1. **Gemma-4-26B** (слот 1, Лира) — **единственная рабочая прод-модель.** Текст и tools с оговорками (свой jinja, `Gemma4ChatHandler`, мусор тегами, циклы). Vision в Lira слабый (не родной pipeline Gemma). Сравнение: эталон по русскому и связности в проекте.

2. **Gemma-3-12b-null-space** (слот 2, Ава) — референс «слабее Gemma 4, но живая». Используется как нижняя планка качества для кандидатов в слот 3.

3. **Qwen3.5-27B hybrid** + `mmproj` (ранний слот 3) — vision в Lira **не работает** (та же болезнь, что п.4): `find_slot`, ~50–60 `prompt_tokens` при ~576 image tokens, галлюцинации.

4. **Qwen3.6-27B abliterated hybrid** + `mmproj-f16` — текст в Lira грузится; vision в Lira **не работает** (image кодируется, в KV для генерации не попадает). Layout/base64 в чате не виноваты (`process_incoming_image` уже даёт `data:image/jpeg;base64`). Batch 3072/1024 не помог; `pip @ main` не починил.

5. **Qwen3.6-27B** (те же GGUF) через **`llama-mtmd-cli`** — vision **работает** (~622 prompt tokens, адекватное описание тестовой картинки). Вывод: **веса не мусор**, ломается связка **hybrid + `llama-cpp-python`**, не «слепая модель».

6. **Qwen3-VL-30B-A3B-Instruct abliterated** (`qwen3vlmoe`, слот 3) — отдельная ветка, не откат на 2.5:
   - **Vision в Lira:** после `Qwen3VLChatHandler` — **работает** (картинка видна; `usage.prompt_tokens` в pip по-прежнему занижен, на качество смотреть по смыслу ответа).
   - **Текст / русский:** **провал** — заметно **хуже Gemma 3 12B** и несравнимо с Gemma 4; стиль «кривой перевод», обрывки, слабая связность. Ощущение промежуточного EN/китайского alignment, не «родной» русский чат.
   - **Tools:** заработали после jinja (`sens`/`limbic`), парсера `<tool_call>` (в т.ч. без закрывающего тега), `{model_name}` в vision-промптах вместо захардкоженной «Лира».
   - **Кванты на 16 GB:** IQ3_XXS, Q3_K_S, Q3_K_M — **Q3_K_M хуже IQ3_XXS**; финальный прогон **IQ3_M** — качество не спасает.
   - **Gallery vision:** до 10 кадров, пакеты по 1; для MoE нужен сброс KV между вызовами (`failed to find a memory slot` при 10 картинках в одном контексте без сброса).
   - **Вердикт:** не для продакшена по **качеству** (текст/русский/MoE), не из‑за abliterated. Для слота 3 **vendor-Instruct с цензурой не подходит** — модель не должна быть зажата вендором; **abliterated / community uncensored GGUF допустимы**, поведение задаём **persona + свой датасет/DPO**, не RLHF вендора.

7. **Отклонено без полноценного слота:** Qwen 2.5 / text-only — не цель (нужны три multimodal, без отката поколения). `llama-server`, вторая 27B только под vision — отклонены (процесс / VRAM).

---

#### Итог

- **Прод:** Gemma 4 (слот 1).
- **Слот 3:** остаётся за **Лизой / Qwen3-VL** до появления замены; замена — когда smoke даст **текст и vision не хуже Gemma 3 12B** (abliterated ок, если снимает вендорский зажим; **не** «чистый» Instruct с отказами/морализаторством).
- **Техдолг, полезный для любой следующей multimodal:** `qwen3vl.py`, доработки `qwen35_vl.py`, vision/tool paths в `chat_controller.py`, `Message.tool_function_name`, smoke-скрипты в `tuning/`.

**Дымовые тесты:** локальные скрипты/CLI вне репозитория.


Модели, которые не получилось развернуть

1. RWKV6-7B


pip install sqlite-vec  - расширение для векторных бд

SELECT load_extension('/home/dev01/Lira2/venv/lib/python3.12/site-packages/sqlite_vec/vec0')


search_memory какой мой номер дома?

valid formats: [
'llama-2',
'llama-3',
'alpaca',
'qwen',
'vicuna',
'oasst_llama',
'baichuan-2',
'baichuan',
'openbuddy',
'redpajama-incite',
'snoozy',
'phind',
'intel',
'open-orca',
'mistrallite',
'zephyr',
'pygmalion',
'chatml',
'mistral-instruct',
'chatglm3', - не
'openchat',
'saiga',
'gemma',
'functionary',
'functionary-v2', - не
'functionary-v1',
'chatml-function-calling']


Backlog:

- PDF формат
- загрузка приложения
- распознавание голоса
- полноэкранный режим

-----------------------------------

Видео

https://huggingface.co/Lightricks/LTX-Video/tree/main

https://huggingface.co/Kijai/CogVideoX-comfy/tree/main

это для лучшего из использованного - wan 2.1

- технология сырая, очень тяжелая и плохо настраиваемая

- номинальное видео для таких моделей - 2 секунды

- промпт не понимает, нужно искать лору под конкретный пример

- чтобы получить приемлемое качество, нужно многократно обрабатывать видео после генерации






Ава (применение лоры и  создание gguf) - блокнот на гугл диске