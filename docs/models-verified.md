# Проверенные модели и стек

Классы слотов и поля конфига: [models.md](models.md). Установка окружения: [getting-started.md](getting-started.md).

## Продакшен (автор)

| Слот | Имя | Модель | Роль |
|------|-----|--------|------|
| 1 | Лира | Gemma-4-26B + mmproj | основной голос, tools, чат |
| 2 | Ава | Gemma-3-12b-null-space | второй multimodal |

`active_model` / `active_model_id` в `config.json` — **последний выбор в UI**, в git не фиксируются.

## Экспериментально / не рекомендуется

| Модель | Статус |
|--------|--------|
| Qwen3-VL-30B-A3B | слот 3, тяжёлый, не prod |
| Qwen3.5 hybrid, Pixtral, InternVL3 8B | см. заметки в корневом [Readme.md](../Readme.md) |

## Python-стек (эталон, 2026-06)

| Компонент | Версия / источник |
|-----------|-------------------|
| Python | 3.12+ |
| PyTorch | 2.11.x + **cu128** (`install-deps.sh`) |
| **llama-cpp-python** | **0.3.23** (CUDA), git @ `5dd9b1ce` — [requirements-llama.txt](../requirements-llama.txt) |
| llama.cpp (нативный, mtmd smoke) | commit **f47a246** |
| PyQt6 | 6.10.x |
| sentence-transformers | 5.3.x (MiniLM / e5 для RAG и галереи) |
| transformers | 5.5.x (лимбика / emotion BERT) |

Сборка **llama-cpp-python** вручную (если не используете git-pin из `requirements-llama.txt`):

```bash
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python==0.3.23
```

## Железо (ориентир)

- **RTX 5070 Ti 16 GB** — эталон VRAM автора; для Gemma-4 26B смотрите `n_gpu_layers` и квантизацию в слоте.

## Веса и данные (не в git)

- GGUF + `mmproj` в `data/models/<slot-dir>/`
- Embedder: `paraphrase-multilingual-MiniLM-L12-v2` или `multilingual-e5-small` (см. `gallery_search` в config)
- TTS: Silero `v5_5_ru.pt` (путь в `config.json` → `tts.model_path`)
- Emotion (лимбика): `rubert-tiny2-russian-emotion-detection` в `data/models/`
