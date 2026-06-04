# Проверенные модели и стек

Классы слотов и поля конфига: [models.md](models.md). Установка: [getting-started.md](getting-started.md).

`active_model` / `active_model_id` в `config.json` — последний выбор в UI, в git не фиксируются.

## Основные (сейчас в работе)

| Модель | Назначение |
|--------|------------|
| **Gemma-4-26B** + mmproj | основной чат, tools, vision (слот 1) |
| **Gemma-3-12b-null-space** + mmproj | второй multimodal-слот |
| **Stable Diffusion** checkpoint + LoRA | text-to-image («художница») |
| **Qwen Image Edit** (GGUF + [Qwen/Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)) | image-edit |

Для чата и vision в авторской установке брались **community GGUF (abliterated / uncensored)**, не vendor Instruct — поведение через persona и `config.json`.

## Запускались / эксперименты

| Модель | Заметка |
|--------|---------|
| Qwen3-VL-30B-A3B-Instruct | abliterated GGUF; vision в Lira работает, текст/русский слабее Gemma 3 |
| Qwen3.5-27B hybrid + mmproj | vision в Lira нестабилен; через `llama-mtmd-cli` — ок |
| Qwen3.6-27B hybrid + mmproj | то же |
| Pixtral | пробовали как кандидат multimodal |
| InternVL3 8B | пробовали как кандидат multimodal |
| Qwen 2.5 / text-only | не цель (нужен multimodal) |

Подробные заметки по сравнению — в локальном `Project-notes.md` (не в public repo).

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

Сборка **llama-cpp-python** вручную:

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
