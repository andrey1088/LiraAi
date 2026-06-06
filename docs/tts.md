# Озвучка (Silero TTS)

Озвучка ответов в чате — **опциональна**. Веса **не входят** в репозиторий и **не скачиваются** при `./scripts/install-deps.sh` (доступ к Hugging Face / hub у многих только через VPN).

Источник моделей: [snakers4/silero-models](https://github.com/snakers4/silero-models).

## Поведение Lira

- При старте проверяется файл из `config.json` → блок **`tts`** (`model_path`).
- Если файла **нет** — озвучка **отключена**, приложение работает без TTS; в stderr один раз: `[TTS] Silero отключена: нет файла …`.
- Скачивание при установке **не выполняется** — только ручное размещение файла.

## Какие файлы нужны

| Локаль UI | Файл по умолчанию | Голос (speaker) |
|-----------|-------------------|-----------------|
| `ru` | `data/models/v5_5_ru.pt` | `kseniya` (и др. из Silero v5) |
| `en` | `data/models/v3_en.pt` | `en_21` |

Пути задаются в `config.json`:

```json
"tts": {
    "locale": "ru",
    "model_path": "data/models/v5_5_ru.pt",
    "speaker": "kseniya",
    "sample_rate": 48000
}
```

После `setup.sh` можно указать свой путь (относительно корня установки или `~/…`).

## Как получить `.pt`

1. Откройте [Silero Models](https://github.com/snakers4/silero-models) — раздел **Text-To-Speech**, примеры для `torch.hub`.
2. Скачайте или соберите **torch.package**-файл в формате, который ожидает Lira (как `v5_5_ru.pt` / `v3_en.pt` в рабочей установке).
3. Положите в `data/models/` каталога установки (рядом с GGUF).

Пример проверки из корня репозитория:

```bash
test -f data/models/v5_5_ru.pt && echo "TTS ru: OK" || echo "TTS ru: нет файла"
```

## Зависимости Python

Уже в `requirements.txt`: **PyTorch** (CPU достаточно для Silero), **sounddevice** (вывод в колонки).

## Устранение неполадок

| Симптом | Действие |
|---------|----------|
| Нет звука, в логе `[TTS] Silero отключена` | Положите `.pt` по пути из `config.json` → `tts.model_path` |
| Ошибка при смене языка UI | Для `en` нужен отдельный файл (`v3_en.pt` по умолчанию) |
| Звук есть, но тихо | Ползунок громкости в UI / `settings.volume` активной модели |

См. также [getting-started.md](getting-started.md), [configuration.md](configuration.md), [stt.md](stt.md) (голосовой ввод).
