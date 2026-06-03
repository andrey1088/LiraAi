"""
Telegram Bot API long polling in a background QThread.
Publishes incoming messages to WorldState (while Lira / perception is ON).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from PyQt6.QtCore import Qt, QThread, pyqtSignal

from infrastructure.external_events.perception_event import PerceptionEvent
from infrastructure.external_events.perception_rules import parse_telegram_id
from infrastructure.external_events.telegram_config import TelegramPerceptionConfig
from infrastructure.external_events.world_state import TELEGRAM_LAST_MESSAGE
from infrastructure.locale.variables import var_get

_POLL_TIMEOUT_SEC = 25

# Bot API — direct, no HTTP_PROXY from .env (often ${PROXY_*} for docker/searxng).
_TELEGRAM_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _log(msg: str) -> None:
    print(f"[TELEGRAM] {msg}", file=sys.stderr, flush=True)


class TelegramBotThread(QThread):
    event_received = pyqtSignal(object)
    # object, not int: Telegram chat_id > 2^31-1; pyqtSignal(int) truncates (6032363684 → 1737396388).
    send_reply = pyqtSignal(object, str)

    def __init__(
        self,
        config: TelegramPerceptionConfig,
        parent=None,
    ):
        super().__init__(parent)
        self._config = config
        self._stop_requested = False
        self._offset: int | None = None
        self.send_reply.connect(self._on_send_reply, Qt.ConnectionType.QueuedConnection)

    def _on_send_reply(self, chat_id, text: str) -> None:
        cid = parse_telegram_id(chat_id)
        if cid and (text or "").strip():
            self._send_message(cid, (text or "").strip()[:4096])

    def request_stop(self) -> None:
        self._stop_requested = True

    def _api(self, method: str, **params) -> dict | None:
        token = self._config.bot_token
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"https://api.telegram.org/bot{token}/{method}"
        if query:
            url = f"{url}?{query}"
        req = urllib.request.Request(url, method="GET")
        try:
            with _TELEGRAM_OPENER.open(req, timeout=_POLL_TIMEOUT_SEC + 10) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            _log(f"api {method} HTTP {e.code}: {err_body or e.reason}")
            return None
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            _log(f"api {method} error: {e}")
            return None
        if not data.get("ok"):
            _log(f"api {method} not ok: {data!r}")
            return None
        return data.get("result")

    def _api_post_json(self, method: str, payload: dict) -> dict | None:
        token = self._config.bot_token
        url = f"https://api.telegram.org/bot{token}/{method}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with _TELEGRAM_OPENER.open(req, timeout=_POLL_TIMEOUT_SEC + 10) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            _log(f"api {method} HTTP {e.code}: {err_body or e.reason}")
            return None
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            _log(f"api {method} error: {e}")
            return None
        if not data.get("ok"):
            _log(f"api {method} not ok: {data!r}")
            return None
        return data.get("result")

    def _send_message(self, chat_id: int, text: str) -> None:
        result = self._api_post_json(
            "sendMessage",
            {"chat_id": int(chat_id), "text": text},
        )
        if result is None:
            _log(f"sendMessage failed chat_id={chat_id} len={len(text)}")

    def _user_allowed(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        if self._config.allowed_user_ids_misconfigured:
            return False
        allowed = self._config.allowed_user_ids
        if not allowed:
            return True
        return user_id in allowed

    def _handle_message(self, message: dict) -> None:
        from_user = message.get("from") or {}
        user_id = parse_telegram_id(from_user.get("id"))
        chat = message.get("chat") or {}
        chat_id = parse_telegram_id(chat.get("id"))
        if chat_id is None and chat.get("type") == "private":
            chat_id = user_id
        if not self._user_allowed(user_id):
            _log(f"ignored message from user_id={user_id!r} (not in allowed_user_ids)")
            if chat_id and user_id:
                self._send_message(
                    chat_id,
                    str(var_get("telegram.access_denied", "ru") or "").format(user_id=user_id),
                )
            return

        text = (message.get("text") or message.get("caption") or "").strip()
        if not text and message.get("entities"):
            text = str(var_get("telegram.attachment_placeholder", "ru") or "[attachment]")
        if not text:
            text = str(var_get("telegram.empty_message_placeholder", "ru") or "[empty message]")

        msg_date = message.get("date")
        if isinstance(msg_date, (int, float)):
            at_local = datetime.fromtimestamp(msg_date).strftime("%H:%M %d.%m.%Y")
        else:
            at_local = ""

        preview = text.replace("\n", " ")[:200]
        event = PerceptionEvent(
            key=TELEGRAM_LAST_MESSAGE,
            value={
                "text_preview": preview,
                "from_user_id": user_id,
                "chat_id": chat_id,
                "chat_type": chat.get("type"),
                "message_id": message.get("message_id"),
                "at_local": at_local,
            },
            source="telegram",
            priority="normal",
        )
        self.event_received.emit(event)
        _log(f"message user_id={user_id} chat_id={chat_id!r}: {preview[:80]!r}")

    def run(self) -> None:
        if not self._config.is_runnable:
            _log("not started: missing token or disabled")
            return
        _log("polling started")
        try:
            me = self._api("getMe")
            if me:
                _log(f"bot=@{me.get('username')} id={me.get('id')}")
            else:
                _log("getMe failed — check TELEGRAM_BOT_TOKEN and api.telegram.org access")
        except Exception as e:
            _log(f"getMe exception: {e!r}")
            return

        while not self._stop_requested:
            try:
                params = {
                    "timeout": _POLL_TIMEOUT_SEC,
                    "allowed_updates": json.dumps(["message"]),
                }
                if self._offset is not None:
                    params["offset"] = self._offset
                result = self._api("getUpdates", **params)
            except Exception as e:
                _log(f"poll loop error: {e!r}")
                self.msleep(5000)
                continue
            if self._stop_requested:
                break
            if result is None:
                self.msleep(3000)
                continue
            for upd in result:
                upd_id = upd.get("update_id")
                if isinstance(upd_id, int):
                    self._offset = upd_id + 1
                msg = upd.get("message")
                if not msg:
                    continue
                if (msg.get("text") or "").strip().startswith("/start"):
                    uid = parse_telegram_id((msg.get("from") or {}).get("id"))
                    chat_id = parse_telegram_id((msg.get("chat") or {}).get("id"))
                    if chat_id and uid:
                        self._send_message(
                            chat_id,
                            str(var_get("telegram.greeting", "ru") or "").format(uid=uid),
                        )
                self._handle_message(msg)
        _log("polling stopped")
