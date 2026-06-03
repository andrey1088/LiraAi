"""OpenAI-style tool schema for chat (descriptions for the LLM)."""

from __future__ import annotations

from infrastructure.locale.i18n import normalize_locale, tr_tools


def _t(key: str, locale: str) -> str:
    return tr_tools(key, locale)


def _fmt_tool(text: str, format_vars: dict[str, str] | None) -> str:
    if not text or not format_vars:
        return text
    try:
        return text.format(**format_vars)
    except (KeyError, ValueError):
        return text


def build_chat_tool_schema(
    locale: str = "ru",
    *,
    format_vars: dict[str, str] | None = None,
) -> list[dict]:
    loc = normalize_locale(locale)

    def t(key: str) -> str:
        return _fmt_tool(_t(key, loc), format_vars)

    return [
        {
            "type": "function",
            "function": {
                "name": "memory_search",
                "description": t("tools.memory_search.description"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": t("tools.memory_search.param.query"),
                        }
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gallery_search",
                "description": t("tools.gallery_search.description"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": t("tools.gallery_search.param.query"),
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "camera_capture",
                "description": t("tools.camera_capture.description"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": t("tools.camera_capture.param.reason"),
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": t("tools.web_search.description"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": t("tools.web_search.param.query"),
                        },
                        "limit": {
                            "type": "integer",
                            "description": t("tools.web_search.param.limit"),
                        },
                        "language": {
                            "type": "string",
                            "description": t("tools.web_search.param.language"),
                        },
                        "route_mode": {
                            "type": "string",
                            "description": t("tools.web_search.param.route_mode"),
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search_saved",
                "description": t("tools.web_search_saved.description"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "integer",
                            "description": t("tools.web_search_saved.param.run_id"),
                        }
                    },
                    "required": ["run_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch_url",
                "description": t("tools.web_fetch_url.description"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": t("tools.web_fetch_url.param.url"),
                        },
                        "run_id": {
                            "type": "integer",
                            "description": t("tools.web_fetch_url.param.run_id"),
                        },
                        "route_mode": {
                            "type": "string",
                            "description": t("tools.web_fetch_url.param.route_mode"),
                        },
                    },
                    "required": ["url"],
                },
            },
        },
    ]
