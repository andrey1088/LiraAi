import math

from core.scripts.chat.domain.message import Message
from infrastructure.locale.variables import var_get


class ContextManager:
    def __init__(self, window):
        self.window = window
        self.last_build_tool_clipped = False

    def _ui_locale(self) -> str:
        return self.window.config_repo.get_ui_locale()

    def _v(self, key: str, **fmt) -> str:
        text = str(var_get(key, self._ui_locale(), default="") or "")
        if fmt and text:
            try:
                return text.format(**fmt)
            except (KeyError, ValueError):
                return text
        return text

    def _context_compress_system_prompt(self) -> str:
        try:
            m_info = self.window.config_repo.get_active_model_info()
            return self.window.config_repo.get_persona_text(m_info, "context_compress_system")
        except Exception:
            from infrastructure.persona.store import PersonaStore

            return PersonaStore.get_prompt_from_file(None, "context_compress_system")

    def _content_to_text(self, content):
        """Flatten Message content to plain text for sizing/summarization."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                p_type = part.get("type")
                if p_type == "text":
                    chunks.append(part.get("text", ""))
                elif p_type == "image_url":
                    chunks.append("[image]")
            return " ".join(chunks).strip()
        return str(content)

    def _estimate_tokens(self, messages):
        """
        Rough token estimate.
        Good enough for llama.cpp to avoid hitting n_ctx early.
        """
        total_chars = 0
        for msg in messages:
            if hasattr(msg, "role"):
                role = msg.role
                content = msg.content
            else:
                role = msg.get("role", "")
                content = msg.get("content", "")
            total_chars += len(role) + len(self._content_to_text(content)) + 12
        # More conservative /4: Cyrillic, tool text, and template tokens use more tokens per char.
        return max(1, math.ceil(total_chars / 3))

    def _sanitize_summary_text(self, text):
        """Normalize summary to plain text and strip markdown noise."""
        if not text:
            return ""

        sanitized_lines = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            # Strip markdown list/emphasis markers.
            line = line.lstrip("-*# ").replace("**", "")
            # Collapse repeated spaces.
            line = " ".join(line.split())
            if line:
                sanitized_lines.append(line)
        return "\n".join(sanitized_lines).strip()

    def _summarize_history_chunk(self, chunk_messages, summary_limit=320, previous_summary=""):
        """Compress old dialog into a short summary via the current model."""
        cc = self.window.chat_controller
        if hasattr(cc, "_llm_inference_busy") and cc._llm_inference_busy():
            return self._sanitize_summary_text(previous_summary or "")
        mc = self.window.model_controller
        if not mc.llm or not chunk_messages:
            return ""

        transcript_lines = []
        for msg in chunk_messages:
            role = getattr(msg, "role", "user")
            text = self._content_to_text(getattr(msg, "content", ""))
            if not text:
                continue
            transcript_lines.append(f"{role.upper()}: {text}")

        if not transcript_lines:
            return ""

        transcript = "\n".join(transcript_lines)
        if previous_summary:
            prompt = self._v(
                "chat.context_update_prompt",
                previous=previous_summary,
                transcript=transcript,
            )
        else:
            prompt = self._v("chat.context_compress_prompt", transcript=transcript)

        try:
            resp = mc.llm.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": self._context_compress_system_prompt(),
                    },
                    {"role": "user", "content": prompt},
                ],
                stream=False,
                temperature=0.2,
                max_tokens=summary_limit,
                tools=None,
            )
            summary_text = self._sanitize_summary_text((resp["choices"][0]["message"].get("content") or "").strip())
            return summary_text
        except Exception:
            return ""

    def _effective_context_budget(self, settings: dict) -> int:
        """
        History budget: min(configured cap, n_ctx − reply reserve − template/tools slack).
        Reserve is at least max_tokens + context_generation_slack.
        """
        n_ctx = int(settings.get("n_ctx", 8192))
        max_tokens = int(settings.get("max_tokens", 2048))
        reserve = int(settings.get("context_reserve_tokens", 2800))
        gen_slack = int(settings.get("context_generation_slack", 128))
        template_slack = int(settings.get("context_template_slack_tokens", 400))
        user_cap = int(settings.get("context_budget_tokens", 5200))
        reserve = max(reserve, max_tokens + gen_slack)
        hardware_cap = n_ctx - reserve - template_slack
        cap = min(user_cap, hardware_cap)
        return max(1800, cap)

    def _shrink_messages_to_budget(self, messages, budget):
        """Drop oldest messages after the first system while estimate > budget."""
        msgs = list(messages)
        guard = 0
        while self._estimate_tokens(msgs) > budget and len(msgs) > 2 and guard < 3000:
            guard += 1
            if getattr(msgs[0], "role", "") == "system":
                msgs.pop(1)
            else:
                msgs.pop(0)
        return msgs

    def _clip_tool_messages_to_budget(self, messages, budget):
        """Shrink longest role=tool replies (web_search/fetch) if still over budget."""
        msgs = list(messages)
        guard = 0
        while self._estimate_tokens(msgs) > budget and guard < 500:
            guard += 1
            idx, best = -1, 0
            for i, m in enumerate(msgs):
                if getattr(m, "role", "") != "tool":
                    continue
                ln = len(self._content_to_text(m.content))
                if ln > best:
                    best, idx = ln, i
            if idx < 0 or best < 1200:
                break
            m = msgs[idx]
            text = self._content_to_text(m.content)
            cut = max(900, int(len(text) * 2 // 3))
            newc = text[:cut] + self._v("chat.tool_fragment_truncated")
            msgs[idx] = Message(
                m.role,
                newc,
                m.tool_call_id,
                m.image_url,
                getattr(m, "tool_function_name", None),
                getattr(m, "tool_function_arguments", None),
            )
            self.last_build_tool_clipped = True
        return msgs

    def _ensure_under_budget(self, messages, budget):
        msgs = self._shrink_messages_to_budget(messages, budget)
        msgs = self._clip_tool_messages_to_budget(msgs, budget)
        return msgs

    def build_proactive_context(self, full_system_msg: str, event_user_line: str) -> list:
        """Minimal context for Telegram proactive — no chat history or tool traces."""
        msgs = []
        if (full_system_msg or "").strip():
            msgs.append(Message(role="system", content=full_system_msg.strip()))
        line = (event_user_line or "").strip()
        if line:
            msgs.append(Message(role="user", content=line))
        return msgs

    def build_context(self, sc, full_system_msg, m_info):
        """
        Build context; compress older messages when over token budget.
        """
        from infrastructure.external_events.perception_rules import (
            is_telegram_channel_user_content,
        )

        self.last_build_tool_clipped = False

        context_history = [
            m
            for m in sc.history
            if not (
                m.role == "user"
                and is_telegram_channel_user_content(m.content if isinstance(m.content, str) else str(m.content or ""))
            )
        ]
        if full_system_msg:
            context_history.insert(0, Message(role="system", content=full_system_msg))

        settings = m_info.settings or {}
        effective_budget = self._effective_context_budget(settings)
        tail_keep = settings.get("context_tail_keep_messages", 10)
        min_tail_keep = 4
        summary_max_tokens = settings.get("summary_max_tokens", 240)

        estimated = self._estimate_tokens(context_history)
        if estimated <= effective_budget:
            return self._ensure_under_budget(context_history, effective_budget)

        history_no_system = [m for m in sc.history]
        if len(history_no_system) <= tail_keep:
            trimmed = [m for m in history_no_system]
            while len(trimmed) > min_tail_keep:
                candidate = [Message(role="system", content=full_system_msg)] + trimmed if full_system_msg else trimmed
                if self._estimate_tokens(candidate) <= effective_budget:
                    return self._ensure_under_budget(candidate, effective_budget)
                trimmed.pop(0)
            out = [Message(role="system", content=full_system_msg)] + trimmed if full_system_msg else trimmed
            return self._ensure_under_budget(out, effective_budget)

        old_chunk = history_no_system[:-tail_keep]
        tail_chunk = history_no_system[-tail_keep:]
        session_id = sc.current_session_id or "default"
        cached = None
        if session_id != "default":
            cached = self.window.repository.get_context_summary(session_id)

        if cached and cached.get("covered_messages") == len(old_chunk):
            summary_text = self._sanitize_summary_text(cached.get("summary", ""))
        else:
            previous_summary = self._sanitize_summary_text(cached.get("summary", "")) if cached else ""
            summary_text = self._summarize_history_chunk(
                old_chunk, summary_limit=summary_max_tokens, previous_summary=previous_summary
            )
            if summary_text and session_id != "default":
                self.window.repository.save_context_summary(
                    session_id=session_id, summary_text=summary_text, covered_messages=len(old_chunk)
                )

        compacted = []
        if full_system_msg:
            compacted.append(Message(role="system", content=full_system_msg))
        if summary_text:
            compacted.append(Message(role="system", content=self._v("chat.summary_header") + summary_text))
        compacted.extend(tail_chunk)

        while self._estimate_tokens(compacted) > effective_budget and len(tail_chunk) > min_tail_keep:
            tail_chunk = tail_chunk[1:]
            compacted = []
            if full_system_msg:
                compacted.append(Message(role="system", content=full_system_msg))
            if summary_text:
                compacted.append(Message(role="system", content=self._v("chat.summary_header") + summary_text))
            compacted.extend(tail_chunk)

        return self._ensure_under_budget(compacted, effective_budget)
