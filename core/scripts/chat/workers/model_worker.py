import datetime
import json
import re

from PyQt6.QtCore import QThread, pyqtSignal

from infrastructure.locale.i18n import tr_tools


def strip_leading_tool_results_echo(text: str) -> str:
    """
    Strip leading junk like «tool call results(fetch): [ {...}, ... ]» —
    the model sometimes echoes pseudo tool results in visible text.
    """
    if not text or not text.lstrip():
        return text
    t = text.lstrip()
    m = re.match(r"(?is)^tool\s+call\s+results\s*(?:\([^)]*\))?\s*:\s*", t)
    if not m:
        return text
    tail = t[m.end() :].lstrip()
    if not tail.startswith("["):
        return text
    try:
        _obj, end = json.JSONDecoder().raw_decode(tail)
    except json.JSONDecodeError:
        return text
    remainder = tail[end:].lstrip()
    return remainder if remainder else text


_LEADING_CHANNEL_JUNK_RE = re.compile(
    r"^(?:"
    r"[\s\u200b\ufeff]*"
    r"(?:"
    r"(?:<\|?channel\|?>|channel\s*>\s*)"
    r"|(?:<\|[^|>]+\|>\s*)"
    r"|(?:<[^>]{0,48}>\s*)"
    r"))+",
    re.IGNORECASE,
)

_LEADING_THOUGHT_LINE_RE = re.compile(
    r"^(?:"
    r"(?://+\s*)?(?:thought|thinking|assistant)\b\s*(?::\s*)?"
    r"|//+\s*(?:thought|thinking)\b\s*"
    r")(?:\n+|\s+|$)",
    re.IGNORECASE,
)


def strip_leading_channel_thought_preamble(text: str) -> str:
    """
    Strip Gemma service-channel preamble at the start of a reply
    (thought\\n…, <|channel|>thought, channel|>…), without touching words mid-text.
    """
    if not text or not str(text).strip():
        return text or ""

    t = str(text).lstrip("\ufeff\u200b").lstrip()
    low = t.lower()
    if "channel|>" in low:
        idx = low.rfind("channel|>")
        t = t[idx + len("channel|>") :].lstrip()

    for _ in range(8):
        prev = t
        t = _LEADING_CHANNEL_JUNK_RE.sub("", t, count=1).lstrip()
        m = _LEADING_THOUGHT_LINE_RE.match(t)
        if m:
            t = t[m.end() :].lstrip()
        if t == prev:
            break

    # Gemma sometimes leaks «//thought» (comment-style channel marker) before the answer.
    t = re.sub(
        r"^(?://+\s*)?(?:thought|thinking)\b\s*(?::\s*)?",
        "",
        t,
        count=1,
        flags=re.IGNORECASE,
    ).lstrip()
    t = re.sub(r"^[|>/\s]+", "", t).lstrip()
    return t


def _messages_include_user_images(messages: list) -> bool:
    for msg in reversed(messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role != "user":
            continue
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if isinstance(content, list):
            return any(isinstance(p, dict) and p.get("type") == "image_url" for p in content)
        return False
    return False


_GEMMA_TURN_FRAGMENT_RE = re.compile(
    r"\b(?:start_of_|end_of_)?turn\b",
    re.IGNORECASE,
)


def strip_gemma_template_turn_leaks(text: str) -> str:
    """
    Remove leaked Gemma chat-template fragments (<start_of_turn> → bare «turn» spam).
    """
    if not text or not str(text).strip():
        return text or ""

    words = re.findall(r"\b\w+\b", str(text).lower())
    if len(words) >= 5:
        turn_n = words.count("turn")
        if turn_n / len(words) >= 0.28:
            scrubbed = _GEMMA_TURN_FRAGMENT_RE.sub(" ", text)
            scrubbed = re.sub(
                r"\b(?:of|correct|model|user|assistant)\b",
                " ",
                scrubbed,
                flags=re.IGNORECASE,
            )
            scrubbed = re.sub(
                r"\b(?:start_of|end_of|tur)(?:_\w+)?\b",
                " ",
                scrubbed,
                flags=re.IGNORECASE,
            )
            scrubbed = re.sub(r"\s+", " ", scrubbed).strip()
            remain = re.findall(r"\b\w+\b", scrubbed.lower())
            if not remain or remain.count("turn") / max(len(remain), 1) > 0.12 or len(scrubbed) < 12:
                return ""
            return scrubbed

    collapsed = re.sub(
        r"(?:\b(?:start_of_|end_of_)?turn\b\s*){3,}",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", collapsed).strip()


def clean_vision_assistant_text(raw: str) -> str:
    """Post-process vision model text (Gemma/Qwen leaks, loops, empty-safe)."""
    if not raw or not str(raw).strip():
        return ""

    text = str(raw).strip()
    if "call:" in text.lower():
        text = re.sub(r"call:\s*\{.*", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    text = re.sub(
        r"<start_of_turn>|<end_of_turn>|<turn\|>|"
        r"<\|[^|]+\|>|</?think(?:ing)?>|"
        r"<\|?channel\|?>|channel\s*>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<\|.*?\|>", "", text)
    text = re.sub(r"<[^>]{0,48}>", "", text)
    text = strip_leading_channel_thought_preamble(text)
    text = strip_gemma_template_turn_leaks(text)
    text = strip_degenerate_token_runs(text)
    return text.strip()


def strip_degenerate_token_runs(text: str) -> str:
    """
    Trim tail when the decoder breaks down (getgetget…, willing- willing-, reg-t id …).
    """
    if not text:
        return text

    words_all = re.findall(r"\b\w+\b", text.lower())
    if len(words_all) >= 8 and words_all.count("turn") / len(words_all) >= 0.28:
        m_leak = re.search(r"\b(?:start_of_|end_of_)?turn\b", text, re.IGNORECASE)
        if m_leak:
            head = text[: m_leak.start()].rstrip()
            if not head or len(head) < 20:
                return ""
            return head

    earliest = len(text)
    for pat in (
        r"(.{2,16})(?:\s*\1){5,}",
        r"(\b\w{2,12}-\s+)(?:\1){4,}",
        r"((?:reg-)?t\s+id\s+)(?:\1){3,}",
        r"(\([^)]{0,24}\)\s*)(?:\1){3,}",
        r"\b(\w{2,10})\b(?:\s+\1\b){4,}",
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m and m.start() < earliest:
            earliest = m.start()

    m2 = re.search(r"(.)\1{23,}", text)
    if m2 and m2.start() < earliest:
        earliest = m2.start()

    if earliest < len(text):
        head = text[:earliest].rstrip()
        if head:
            if len(head) < 16 and (len(text) - len(head)) > 40:
                return ""
            return head

    if len(text) >= 48:
        tail = text[-160:]
        words = re.findall(r"\b\w+\b", tail.lower())
        if len(words) >= 10:
            from collections import Counter

            word, count = Counter(words).most_common(1)[0]
            if count / len(words) >= 0.4:
                idx = text.lower().rfind(word)
                if idx > 16:
                    return text[:idx].rstrip()

    return text


# Chat message when the model returns empty content (otherwise UI never gets finished_token).
_EMPTY_ASSISTANT_FALLBACK_KEY = "chat.worker.empty_model_reply"


def _worker_tr(key: str, locale: str = "ru") -> str:
    return tr_tools(key, locale)


def _emit_text_reply(worker: "ModelWorker", text: str) -> None:
    worker.finished_token.emit(text)
    worker.finished_answer.emit(text)


class ModelWorker(QThread):
    finished_token = pyqtSignal(str)
    finished_answer = pyqtSignal(str)
    gallery_data_found = pyqtSignal(str)

    def __init__(
        self,
        model,
        history,
        settings,
        tools=None,
        limbic_content=None,
        limbic_state_summary=None,
        sens_append_suffix=None,
    ):
        super().__init__()
        self.model = model
        self.history = history if history is not None else []
        self.settings = settings or {}
        self.tools = tools
        self.limbic_content = (limbic_content or "").strip() or None
        self.limbic_state_summary = (limbic_state_summary or "").strip()
        self.sens_append_suffix = (sens_append_suffix or "").strip() or None
        self.cancel_requested = False

    def run(self):
        messages = []

        # 1. Process main history first
        for i, m in enumerate(self.history):
            msg_dict = m.to_llm_dict() if hasattr(m, "to_llm_dict") else m
            role = msg_dict.get("role")
            content = msg_dict.get("content")

            # EMPTY FIX: if assistant content is None, supply action text
            if role == "assistant" and (content is None or content == ""):
                if msg_dict.get("tool_call_id") or (hasattr(m, "tool_call_id") and m.tool_call_id):
                    msg_dict["content"] = _worker_tr("chat.worker.gallery_searching")
                else:
                    continue

            # FILTER: replace heavy gallery JSON with a short label
            if isinstance(content, str) and content.startswith("UI_GALLERY|"):
                msg_dict["content"] = _worker_tr("chat.worker.gallery_shown")

            # Strip multimodal from old messages (save context)
            elif i < len(self.history) - 1:
                if isinstance(content, list):
                    text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    msg_dict["content"] = " ".join(text_parts).strip()
                msg_dict.pop("image_url", None)
                msg_dict.pop("image_base64", None)

            messages.append(msg_dict)

        # 2. TEMPLATE CHECK AND SENS INSERT
        # Check custom template on model chat_handler
        handler = getattr(self.model, "chat_handler", None)
        has_custom_template = False
        if handler and hasattr(handler, "external_template_path"):
            if handler.external_template_path:  # .jinja path set
                has_custom_template = True

        if has_custom_template:
            from infrastructure.templates.sens_snapshot import (
                build_sens_status_line,
                sens_hardware_suffix,
            )

            now = datetime.datetime.now()
            time_str = now.strftime("%H:%M")
            date_str = now.strftime("%d %B %Y (%A)")
            hw_suffix = sens_hardware_suffix()
            hw_plain = (hw_suffix or "").strip().lstrip("·").strip()

            lira_status = build_sens_status_line(
                perception_suffix=self.sens_append_suffix,
            )
            sens_message = {"role": "sens", "content": lira_status}
            print(
                f"[SENS] time={time_str!r} date={date_str!r} "
                f"hardware={repr(hw_plain) if hw_plain else 'none'} "
                f"content_len_chars={len(lira_status)}"
            )
            print(f"[SENS] content_full={lira_status!r}")

            # Insert SENS after System or first if no System
            if len(messages) > 0 and messages[0].get("role") == "system":
                insert_at = 1
                messages.insert(insert_at, sens_message)
            else:
                insert_at = 0
                messages.insert(insert_at, sens_message)

            if self.limbic_state_summary:
                print(f"[LIMBIC] state | {self.limbic_state_summary}")
            if self.limbic_content:
                limbic_message = {"role": "limbic", "content": self.limbic_content}
                messages.insert(insert_at + 1, limbic_message)
                print(f"[LIMBIC] content_len_chars={len(self.limbic_content)}")
                print(f"[LIMBIC] content_full={self.limbic_content!r}")
            elif self.limbic_state_summary:
                print("[LIMBIC] content_skipped=near_baseline")
        else:
            print("[SENS] skipped: model chat_handler has no external_template_path (sens block not inserted)")

        # --- DEBUG CONTEXT BEFORE MODEL CALL (temporary) ---
        try:
            print(f"[CTX_DUMP] messages_count={len(messages)}", flush=True)
            for idx, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                content_val = msg.get("content", "")
                tool_calls = msg.get("tool_calls")
                if isinstance(content_val, list):
                    chunks = []
                    for part in content_val:
                        if not isinstance(part, dict):
                            continue
                        ptype = part.get("type")
                        if ptype == "text":
                            txt = str(part.get("text", ""))
                            chunks.append(f"text:{txt[:400]!r}")
                        elif ptype == "image_url":
                            img = part.get("image_url")
                            if isinstance(img, dict):
                                url = str(img.get("url", ""))
                            else:
                                url = str(img or "")
                            chunks.append(f"image_url:{url[:200]!r}")
                        else:
                            chunks.append(f"{ptype}:{str(part)[:200]!r}")
                    content_repr = " | ".join(chunks)
                else:
                    content_repr = str(content_val)
                if len(content_repr) > 1200:
                    content_repr = content_repr[:1200] + " ...<truncated>"

                print(
                    f"[CTX_DUMP] #{idx} role={role!r} tool_calls={bool(tool_calls)} content={content_repr!r}",
                    flush=True,
                )
        except Exception as e:
            print(f"[CTX_DUMP] failed: {e!r}", flush=True)

        # 3. MODEL CALL
        try:
            completion_kw = dict(
                messages=messages,
                stream=False,
                temperature=self.settings.get("temperature", 0.7),
                repeat_penalty=1.22,
                presence_penalty=0.12,
            )
            if self.tools and not _messages_include_user_images(messages):
                completion_kw["tools"] = self.tools
                tc = self.settings.get("tool_choice")
                completion_kw["tool_choice"] = tc if tc is not None else "auto"
            max_tokens = self.settings.get("max_tokens")
            if max_tokens is not None:
                completion_kw["max_tokens"] = int(max_tokens)
            if _messages_include_user_images(messages):
                completion_kw["max_tokens"] = min(
                    int(completion_kw.get("max_tokens") or 240),
                    240,
                )
                completion_kw["temperature"] = min(
                    float(completion_kw.get("temperature", 0.7)),
                    0.38,
                )
                completion_kw["repeat_penalty"] = max(
                    float(completion_kw.get("repeat_penalty", 1.22)),
                    1.34,
                )
                completion_kw["presence_penalty"] = max(
                    float(completion_kw.get("presence_penalty", 0.12)),
                    0.18,
                )
            response = self.model.create_chat_completion(**completion_kw)
        except Exception as e:
            pass
            _emit_text_reply(self, _worker_tr("chat.worker.model_error").format(e=e))
            return

        if self.cancel_requested:
            return

        # 4. PARSE RESPONSE
        try:
            choice = response["choices"][0]
            message = choice["message"]
            content = message.get("content")
            tool_calls = message.get("tool_calls")

            pass

            if tool_calls:
                if not self.tools:
                    print(
                        "[PROACTIVE] model returned tool_calls without tools=; "
                        f"ignored names={[t.get('function', {}).get('name') for t in tool_calls[:3]]}",
                        flush=True,
                    )
                    raw = "" if content is None else str(content).strip()
                    if raw:
                        clean_text = re.sub(r"<\|.*?\|>", "", raw).strip()
                        clean_text = strip_leading_tool_results_echo(clean_text)
                        clean_text = strip_leading_channel_thought_preamble(clean_text)
                        clean_text = strip_degenerate_token_runs(clean_text)
                        if clean_text.strip():
                            _emit_text_reply(self, clean_text)
                            return
                    _emit_text_reply(
                        self,
                        _worker_tr("chat.worker.telegram_notice"),
                    )
                    return
                tc = tool_calls[0]
                fn = tc.get("function", {}).get("name", "")
                args_raw = tc.get("function", {}).get("arguments", "")
                payload = f"TOOL_CALL|{fn}|{args_raw}|{tc.get('id', '')}"
                self.finished_answer.emit(payload)
                return
            else:
                raw = "" if content is None else str(content).strip()
                if raw:
                    # Strip technical tags
                    clean_text = re.sub(r"<\|.*?\|>", "", raw).strip()
                    clean_text = strip_leading_tool_results_echo(clean_text)
                    clean_text = strip_leading_channel_thought_preamble(clean_text)
                    if _messages_include_user_images(messages):
                        clean_text = clean_vision_assistant_text(clean_text)
                    else:
                        clean_text = strip_degenerate_token_runs(clean_text)

                    # Gallery intercept
                    if "SHOW_GALLERY_RESULTS|" in clean_text:
                        pass
                        parts = clean_text.split("SHOW_GALLERY_RESULTS|")
                        clean_text = parts[0].strip()
                        json_data = parts[1].strip()
                        self.gallery_data_found.emit(json_data)

                    if not clean_text.strip():
                        clean_text = _worker_tr(_EMPTY_ASSISTANT_FALLBACK_KEY)
                    _emit_text_reply(self, clean_text)
                else:
                    fr = choice.get("finish_reason")
                    print(
                        f"[MODEL] empty assistant content finish_reason={fr!r} "
                        f"has_images={_messages_include_user_images(messages)}",
                        flush=True,
                    )
                    _emit_text_reply(self, _worker_tr(_EMPTY_ASSISTANT_FALLBACK_KEY))
        except Exception as e:
            pass
            _emit_text_reply(self, _worker_tr("chat.worker.parse_error").format(e=e))
