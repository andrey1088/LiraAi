# Документация Lira

Локальный GUI-ассистент (llama.cpp, vision, tools, память). Обзор проекта — [README](../README.md) в корне.

## Содержание

### Старт и конфигурация

| Документ | О чём |
|----------|--------|
| [getting-started.md](getting-started.md) | Установка, первый запуск, `setup.sh` |
| [tts.md](tts.md) | Озвучка Silero (опционально, без auto-download) |
| [stt.md](stt.md) | Голосовой ввод GigaAM (ru, auto-download весов при старте) |
| [image-generation.md](image-generation.md) | Художница / SD, сборка с CUDA |
| [configuration.md](configuration.md) | `config.json`, слоты, `user`, gallery |
| [personas.md](personas.md) | Персоны, промпты, `{model_name}`, `{user_name}` |
| [i18n-ui.md](i18n-ui.md) | Локализация: `infrastructure/locale/`, `tr()` / `t()` |

### Модели

| Документ | О чём |
|----------|--------|
| [models.md](models.md) | Текстовые, мультимодальные, text-to-image, image-edit |
| [models-verified.md](models-verified.md) | Проверенные GGUF, версии стека |
| [chat-templates.md](chat-templates.md) | Jinja-шаблоны чата |

### Поведение и данные

| Документ | О чём |
|----------|--------|
| [tools.md](tools.md) | Инструменты чата, политики, добавление новых |
| [memory-databases.md](memory-databases.md) | SQLite: чат, память, галерея, research |
| [sens.md](sens.md) | Блок sens (время, железо) |
| [limbic.md](limbic.md) | Эмоциональное состояние и UI-портрет |
| [external-events.md](external-events.md) | Perception, проактив, Telegram |
| [telegram.md](telegram.md) | Настройка бота (кратко) |

### Разработка

| Документ | О чём |
|----------|--------|
| [architecture.md](architecture.md) | Слои кода, поток сообщения |
