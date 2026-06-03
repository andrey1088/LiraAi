# Telegram (опционально)

Бот — пример **внешнего канала** в контуре perception. Полный поток: [external-events.md](external-events.md).

## Идея

- **Посторонние** пишут боту; это не основной чат Lira с владельцем.
- Ответ в Telegram — отдельный вызов LLM (`telegram_bot_reply` в [personas.md](personas.md)).
- Уведомление владельца в десктоп-чате — только через оценку `telegram_life_eval` ([tools.md](tools.md)).
- Имя владельца — `{user_name}` из `config.user`, не хардкод.

## Включение

1. Слот с `perception_daemon: true` и `limbic_images_path` ([limbic.md](limbic.md)).
2. Конфиг Telegram: `infrastructure/external_events/telegram_config.py`, переменные окружения.
3. Правила проактивов: `config.perception_rules.json` в корне проекта.

## Связанные файлы

| Путь | Роль |
|------|------|
| `external_events/telegram_bot_thread.py` | polling / входящие |
| `lifecycle/perception_daemon.py` | оркестрация |
| `tools/notify_andrey.py` | schema `telegram_life_eval` |

## Безопасность

Не коммитить токены бота. Использовать `.env` (см. будущий `.env.example`).

> **TODO:** пошаговый чеклист первого запуска бота (фаза 4).
