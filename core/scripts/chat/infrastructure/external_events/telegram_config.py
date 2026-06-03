"""Load Telegram bot settings from install-root .env (not in git)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from infrastructure.paths import dotenv_path

_dotenv_applied = False


def _apply_project_dotenv() -> None:
    """Load .env from LIRA_ROOT into os.environ when the key is unset."""
    global _dotenv_applied
    if _dotenv_applied:
        return
    _dotenv_applied = True
    path = dotenv_path()
    if not path.is_file():
        return
    try:
        data: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, val = raw.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                data[key] = val
        for _ in range(3):
            for key, val in list(data.items()):
                if "${" in val:
                    for ref_k, ref_v in data.items():
                        val = val.replace("${" + ref_k + "}", ref_v)
                    data[key] = val
        for key, val in data.items():
            if key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


@dataclass
class TelegramPerceptionConfig:
    enabled: bool
    bot_token: str
    allowed_user_ids: frozenset[int]
    # TELEGRAM_ALLOWED_USER_IDS set in .env but no id parsed (often due to [brackets]).
    allowed_user_ids_misconfigured: bool = False

    @property
    def is_runnable(self) -> bool:
        return self.enabled and bool(self.bot_token.strip())


def _parse_user_ids(raw: str) -> frozenset[int]:
    text = (raw or "").strip()
    if not text:
        return frozenset()
    # Allow JSON paste: [6032363684] or [1, 2]
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    items = text.replace(";", ",").split(",")
    out: set[int] = set()
    for item in items:
        s = str(item).strip().strip("[]")
        if not s:
            continue
        try:
            out.add(int(s))
        except ValueError:
            continue
    return frozenset(out)


def load_telegram_config() -> TelegramPerceptionConfig:
    _apply_project_dotenv()

    enabled = True
    if os.environ.get("TELEGRAM_PERCEPTION_ENABLED", "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        enabled = False

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    allowed_raw = (os.environ.get("TELEGRAM_ALLOWED_USER_IDS") or "").strip()
    allowed = _parse_user_ids(allowed_raw)
    misconfigured = bool(allowed_raw) and not allowed

    return TelegramPerceptionConfig(
        enabled=enabled,
        bot_token=token,
        allowed_user_ids=allowed,
        allowed_user_ids_misconfigured=misconfigured,
    )
