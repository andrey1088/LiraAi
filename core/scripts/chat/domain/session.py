from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from core.scripts.chat.domain.message import Message
from infrastructure.locale.variables import var_get


def _default_chat_title() -> str:
    return str(var_get("memory.default_chat_title", "en") or "New chat")


@dataclass
class ChatSession:
    id: int
    title: str = field(default_factory=_default_chat_title)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    display_date: Optional[str] = None
    messages: List[Message] = field(default_factory=list)

    def to_dict(self):
        """Serialize for the UI."""
        return {"id": self.id, "title": self.title, "date": self.display_date or ""}
