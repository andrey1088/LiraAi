"""Life-eval: model decides whether to notify owner in desktop chat."""

from __future__ import annotations

TELEGRAM_LIFE_EVAL_TOOL = "telegram_life_eval"


def telegram_life_eval_schema(
    locale: str = "ru",
    *,
    format_vars: dict[str, str] | None = None,
) -> dict:
    from infrastructure.locale.i18n import tr_tools_format

    loc = str(locale or "ru")
    fmt = format_vars or {}

    def _t(key: str) -> str:
        return tr_tools_format(key, loc, **fmt)

    return {
        "type": "function",
        "function": {
            "name": TELEGRAM_LIFE_EVAL_TOOL,
            "description": _t("notify_andrey.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "should_notify_andrey": {
                        "type": "boolean",
                        "description": _t("notify_andrey.param.should_notify"),
                    },
                    "message": {
                        "type": "string",
                        "description": _t("notify_andrey.param.message"),
                    },
                },
                "required": ["should_notify_andrey"],
            },
        },
    }


# Backward-compatible module-level schema (Russian UI default).
TELEGRAM_LIFE_EVAL_SCHEMA = telegram_life_eval_schema("ru")
