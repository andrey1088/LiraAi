from infrastructure.locale.i18n import tr_tools


def tool_history_trunc_marker(locale: str = "ru") -> str:
    return tr_tools(
        "chat.tool_history_trunc_marker",
        locale,
        fallback="\n\n[… tool output truncated for context length …]",
    )
