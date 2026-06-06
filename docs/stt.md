# Распознавание речи (GigaAM STT)

Голосовой ввод в чат — **опционален** и доступен **только при `ui_locale: ru`**. Модель — **GigaAM v3 e2e RNNT** в формате ONNX ([istupakov/gigaam-v3-onnx](https://huggingface.co/istupakov/gigaam-v3-onnx) на Hugging Face).

В отличие от TTS (Silero), веса STT **не нужно класть вручную**: при первом запуске с русской локалью Lira в фоне ставит Python-зависимости и скачивает ONNX-файлы (~850 MB) в `data/models/gigaam-v3-e2e-rnnt/`.

## Поведение Lira

| Условие | Результат |
|---------|-----------|
| `ui_locale: ru`, слот чата (не художница / image-edit) | Кнопка 🎤 в поле ввода; bootstrap при старте, если нет deps или весов |
| `ui_locale: en` | STT скрыт и отключён |
| Слот `text-to-image` или `image-edit` | STT не используется (bootstrap не запускается) |
| Веса ещё качаются | Кнопка неактивна; после загрузки UI обновится сам |

**Push-to-talk:** нажать 🎤 — начать запись (TTS при этом останавливается), нажать ⏹ — остановить и распознать. Текст уходит в чат как обычное сообщение пользователя.

Ограничения записи: моно **16 kHz**, до **25 с** на одну фразу; нужен рабочий микрофон (через **sounddevice**).

## Автоустановка (bootstrap)

При `delayed_init()` и при смене локали на `ru` в сайдбаре (если активен чат-слот) в фоновом потоке:

1. `pip install onnx-asr>=0.11 huggingface_hub` — если пакетов нет в venv;
2. скачивание недостающих файлов из `istupakov/gigaam-v3-onnx` в `data/models/gigaam-v3-e2e-rnnt/`.

Логи: префикс `[STT]` в stderr / `logs/lira.log`. После успеха UI вызывает `refreshSttEnabled()` и включает кнопку микрофона.

Ручная установка не требуется. Для офлайн-машины можно один раз скачать каталог с другой машины и положить в `data/models/gigaam-v3-e2e-rnnt/` (см. список файлов ниже).

## Какие файлы нужны

Каталог: `data/models/gigaam-v3-e2e-rnnt/`

| Файл | Назначение |
|------|------------|
| `config.json` | Конфиг ONNX-модели |
| `v3_e2e_rnnt_encoder.onnx` | Encoder |
| `v3_e2e_rnnt_decoder.onnx` | Decoder |
| `v3_e2e_rnnt_joint.onnx` | Joint network |
| `v3_e2e_rnnt_vocab.txt` | Словарь |
| `v3_e2e_rnnt.yaml` | Метаданные (скачивается вместе с весами) |

Проверка:

```bash
test -f data/models/gigaam-v3-e2e-rnnt/v3_e2e_rnnt_encoder.onnx \
  && echo "STT: OK" || echo "STT: нет весов (дождитесь bootstrap или положите файлы вручную)"
```

## Inference и железо

- Движок: **onnx-asr**, провайдер **`CPUExecutionProvider`** — сознательно без GPU, чтобы не забирать VRAM у LLM.
- Типичная задержка на CPU: порядка **1–2 с** на короткую фразу (зависит от CPU и длины аудио).
- Запись и воспроизведение TTS используют **sounddevice** (уже в `requirements.txt`).

Пакеты `onnx-asr` и `huggingface_hub` ставятся bootstrap’ом при первом ru-старте, в базовый `install-deps.sh` не входят.

## Код (для разработчиков)

| Компонент | Путь |
|-----------|------|
| Bootstrap (deps + download) | `core/scripts/chat/infrastructure/stt/bootstrap.py` |
| ONNX inference | `infrastructure/stt/engine.py` |
| Запись с микрофона | `infrastructure/stt/recorder.py` |
| Push-to-talk, worker | `core/scripts/chat/app/stt_controller.py` |
| WebChannel API | `core/scripts/chat/ui/bridge.py` (`startSttRecording`, `is_stt_enabled`) |
| Кнопка 🎤 в UI | `core/web/app.js` |

STT изолирован от `ChatController`: распознанный текст передаётся через тот же `sendMessage`, что и обычный ввод.

## Устранение неполадок

| Симптом | Действие |
|---------|----------|
| Нет кнопки 🎤 | Проверьте `ui_locale: ru` и что активен чат-слот, не SD/image-edit |
| «Распознавание речи недоступно» сразу после старта | Дождитесь bootstrap (~850 MB); смотрите `[STT]` в логе |
| Ошибка pip / Hugging Face | VPN или прокси для HF; либо скопируйте каталог весов вручную |
| «Could not recognize speech» | Слишком тихо / коротко; говорите ближе к микрофону |
| Нет устройства записи | Проверьте микрофон в системе; права PulseAudio/PipeWire |
| Долго «Распознаём речь…» | Нормально на слабом CPU; первая загрузка ONNX тоже дольше |

См. также [configuration.md](configuration.md) (`ui_locale`), [tts.md](tts.md) (озвучка ответов), [i18n-ui.md](i18n-ui.md).
