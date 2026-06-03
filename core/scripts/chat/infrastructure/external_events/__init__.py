"""External events: Telegram, WorldState, rules."""

from infrastructure.external_events.perception_event import PerceptionEvent
from infrastructure.external_events.perception_rules import (
    PROACTIVE_USER_PROMPT_PREFIX,
    PerceptionRuleEngine,
    ProactiveJob,
    is_proactive_internal_user_text,
    is_telegram_channel_user_content,
    parse_telegram_id,
    telegram_bot_reply_system_prompt,
    telegram_external_context_block,
    telegram_message_id_from_event,
    telegram_reply_chat_id_from_event,
    telegram_sender_label,
)
from infrastructure.external_events.telegram_config import (
    TelegramPerceptionConfig,
    load_telegram_config,
)
from infrastructure.external_events.world_state import (
    TELEGRAM_LAST_MESSAGE,
    WorldState,
    get_world_state,
    telegram_exchange_ready,
)

__all__ = [
    "PROACTIVE_USER_PROMPT_PREFIX",
    "PerceptionEvent",
    "PerceptionRuleEngine",
    "ProactiveJob",
    "TELEGRAM_LAST_MESSAGE",
    "TelegramPerceptionConfig",
    "WorldState",
    "get_world_state",
    "is_proactive_internal_user_text",
    "is_telegram_channel_user_content",
    "load_telegram_config",
    "parse_telegram_id",
    "telegram_bot_reply_system_prompt",
    "telegram_exchange_ready",
    "telegram_external_context_block",
    "telegram_message_id_from_event",
    "telegram_reply_chat_id_from_event",
    "telegram_sender_label",
]
