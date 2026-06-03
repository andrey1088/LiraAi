"""Chat tool names → callables (implementations in core.scripts.chat.tools)."""

from __future__ import annotations

from core.scripts.chat.tools.camera_capture import camera_capture_tool
from core.scripts.chat.tools.gallery_search import gallery_tool
from core.scripts.chat.tools.memory_search import memory_tool
from core.scripts.chat.tools.web_fetch_url import web_fetch_url
from core.scripts.chat.tools.web_search import web_search
from core.scripts.chat.tools.web_search_saved import web_search_saved


def chat_tool_implementations() -> dict[str, object]:
    return {
        "memory_search": memory_tool,
        "gallery_search": gallery_tool,
        "camera_capture": camera_capture_tool,
        "web_search": web_search,
        "web_search_saved": web_search_saved,
        "web_fetch_url": web_fetch_url,
    }
