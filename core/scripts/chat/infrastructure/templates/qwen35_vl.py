"""Qwen3.5/3.6: text via external jinja (sens/tools), vision via Qwen25VL."""

from __future__ import annotations

import io
import json
import os
import re
from typing import Any, List

import jinja2
import llama_cpp
from llama_cpp import llama_chat_format
from llama_cpp.llama_chat_format import (
    Llava15ChatHandler,
    Qwen25VLChatHandler,
    suppress_stdout_stderr,
)
from PIL import Image

_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
_THINKING_RE = re.compile(
    r"<think>.*?</think>|"
    r"<thinking>.*?</thinking>|"
    r"<\|im_start\|>\s*think\s*>\s*.*?<\|(?:im_end|redacted_im_end)\|>",
    re.DOTALL | re.IGNORECASE,
)

# mtmd: image URL in text → replace with media_marker.
# Padding <|vision_start|>… causes «bitmaps (1) != markers (0)».
VISION_CHAT_FORMAT = (
    "{% for message in messages %}"
    "{% if loop.first and message['role'] != 'system' %}"
    "<|im_start|>system\n"
    "{{ self.DEFAULT_SYSTEM_MESSAGE }}<|im_end|>\n"
    "{% endif %}"
    "<|im_start|>{{ message['role'] }}\n"
    "{% if message['content'] is string %}"
    "{{ message['content'] }}<|im_end|>\n"
    "{% else %}"
    "{% for content in message['content'] %}"
    "{% if content['type'] == 'image_url' %}"
    "{% if content.image_url is string %}"
    "{{ content.image_url }}"
    "{% else %}"
    "{{ content.image_url.url }}"
    "{% endif %}"
    "{% elif content['type'] == 'text' %}"
    "{{ content['text'] }}"
    "{% endif %}"
    "{% endfor %}"
    "<|im_end|>\n"
    "{% endif %}"
    "{% endfor %}"
    "<|im_start|>assistant\n"
    "<think>\n\n</think>\n\n"
)

_TEXT_STOPS = [
    "<|im_end|>",
    "<|im_start|>",
    "</tool_call>",
    "<think>",
]


class Qwen35ChatHandler(Qwen25VLChatHandler):
    # hybrid qwen35: 1024 image tokens → find_slot + blank image on llama.cpp 0.3.x; 576 (24×24) is stabler.
    DEFAULT_IMAGE_MIN_TOKENS = 576

    def __init__(self, *args, **kwargs):
        self.external_template_path = kwargs.pop("template_path", None)
        self.persona_file = kwargs.pop("persona_file", None)
        self.image_min_tokens = int(kwargs.pop("image_min_tokens", self.DEFAULT_IMAGE_MIN_TOKENS))
        self.image_max_tokens = int(kwargs.pop("image_max_tokens", -1))
        self._jinja_template = None
        super().__init__(*args, **kwargs)

    def _vision_system_prompt(self) -> str:
        from infrastructure.persona.store import PersonaStore

        return PersonaStore.get_prompt_from_file(self.persona_file, "vision_system")

    def _init_mtmd_context(self, llama_model: llama_cpp.Llama) -> None:
        if self.mtmd_ctx is not None:
            return
        with suppress_stdout_stderr(disable=self.verbose):
            ctx_params = self._mtmd_cpp.mtmd_context_params_default()
            ctx_params.use_gpu = True
            ctx_params.print_timings = self.verbose
            ctx_params.n_threads = llama_model.n_threads
            ctx_params.flash_attn_type = (
                llama_cpp.LLAMA_FLASH_ATTN_TYPE_ENABLED
                if llama_model.context_params.flash_attn_type == llama_cpp.LLAMA_FLASH_ATTN_TYPE_ENABLED
                else llama_cpp.LLAMA_FLASH_ATTN_TYPE_DISABLED
            )
            if self.image_min_tokens > 0:
                ctx_params.image_min_tokens = self.image_min_tokens
            if self.image_max_tokens > 0:
                ctx_params.image_max_tokens = self.image_max_tokens
            self.mtmd_ctx = self._mtmd_cpp.mtmd_init_from_file(
                self.clip_model_path.encode(), llama_model.model, ctx_params
            )
            if self.mtmd_ctx is None:
                raise ValueError(f"Failed to load mtmd context from: {self.clip_model_path}")
            if not self._mtmd_cpp.mtmd_support_vision(self.mtmd_ctx):
                raise ValueError("Vision is not supported by this model")

            def mtmd_free() -> None:
                with suppress_stdout_stderr(disable=self.verbose):
                    if self.mtmd_ctx is not None:
                        self._mtmd_cpp.mtmd_free(self.mtmd_ctx)
                        self.mtmd_ctx = None

            self._exit_stack.callback(mtmd_free)
        print(
            f"[VISION] mtmd init image_min_tokens={self.image_min_tokens} "
            f"image_max_tokens={self.image_max_tokens or 'auto'}",
            flush=True,
        )

    def _normalize_messages(self, messages: List[Any]) -> List[dict]:
        fixed: List[dict] = []
        for m in messages:
            if hasattr(m, "to_llm_dict"):
                msg = m.to_llm_dict()
            elif hasattr(m, "role"):
                msg = {
                    "role": m.role,
                    "content": m.content if m.content is not None else "",
                }
            else:
                msg = dict(m)
            if msg.get("content") is None:
                msg["content"] = ""
            fixed.append(msg)
        return fixed

    @staticmethod
    def _has_image(messages: List[dict]) -> bool:
        for m in messages:
            content = m.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if part.get("type") == "image_url":
                    return True
        return False

    @staticmethod
    def clean_lira_output(text: str) -> str:
        from workers.model_worker import strip_leading_channel_thought_preamble

        if not text:
            return ""
        text = strip_leading_channel_thought_preamble(text)
        text = _THINKING_RE.sub("", text)
        text = _TOOL_CALL_RE.sub("", text)
        text = re.sub(r"<\|[^|]+\|>", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text

    @staticmethod
    def _normalize_image_url(url: str) -> str:
        u = (url or "").strip()
        if u.startswith(("data:", "http://", "https://", "file://")):
            return u
        expanded = os.path.expanduser(u)
        if os.path.isfile(expanded):
            return "file://" + expanded
        return u

    def _log_image_payload(self, url: str) -> None:
        kind = "data" if str(url).startswith("data:") else "file"
        try:
            raw = self._load_image(str(url))
            im = Image.open(io.BytesIO(raw))
            print(
                f"[VISION] payload {kind} bytes={len(raw)} size={im.size} mode={im.mode}",
                flush=True,
            )
        except Exception as exc:
            print(f"[VISION] payload {kind} load_failed: {exc!r}", flush=True)

    @staticmethod
    def _extract_balanced_json_object(text: str, start: int) -> str | None:
        if start < 0 or start >= len(text) or text[start] != "{":
            return None
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return None

    @staticmethod
    def _parse_tool_call_json_blob(content: str, choice_id: str) -> list | None:
        """JSON in <tool_call>…</tool_call> or without closing tag (stop/max_tokens)."""
        text = content.strip()
        search_from = 0
        low = text.lower()
        tag = "<tool_call>"
        if tag in low:
            search_from = low.index(tag) + len(tag)
        brace = text.find("{", search_from)
        if brace < 0:
            return None
        blob = Qwen35ChatHandler._extract_balanced_json_object(text, brace)
        if not blob:
            return None
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            return None
        name = data.get("name")
        if not name:
            return None
        args = data.get("arguments", {})
        return [
            {
                "id": f"call_{choice_id}",
                "type": "function",
                "function": {
                    "name": str(name).replace(" ", "_"),
                    "arguments": (args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)),
                },
            }
        ]

    @staticmethod
    def _parse_qwen_xml_tool_call(content: str, choice_id: str) -> list | None:
        """Qwen3.5/3.6 jinja: <tool_call><function=name><parameter=k>v</parameter>…"""
        block = re.search(
            r"<tool_call>\s*<function=([^>\s]+)>\s*(.*?)</function>\s*</tool_call>",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if not block:
            return None
        name = block.group(1).strip().replace(" ", "_")
        inner = block.group(2)
        args: dict[str, str] = {}
        for pname, pval in re.findall(
            r"<parameter=([^>]+)>\s*(.*?)\s*</parameter>",
            inner,
            re.DOTALL | re.IGNORECASE,
        ):
            args[pname.strip()] = pval.strip()
        return [
            {
                "id": f"call_{choice_id}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        ]

    def _parse_tool_calls(self, content: str, choice_id: str) -> list | None:
        if not content or "<tool_call>" not in content.lower():
            if '"name"' not in content or '"arguments"' not in content:
                return None
        xml_tc = self._parse_qwen_xml_tool_call(content, choice_id)
        if xml_tc:
            return xml_tc
        json_tc = self._parse_tool_call_json_blob(content, choice_id)
        if json_tc:
            return json_tc
        m = _TOOL_CALL_RE.search(content)
        if m:
            try:
                data = json.loads(m.group(1))
                name = data.get("name")
                args = data.get("arguments", {})
                if name:
                    return [
                        {
                            "id": f"call_{choice_id}",
                            "type": "function",
                            "function": {
                                "name": str(name).replace(" ", "_"),
                                "arguments": (args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)),
                            },
                        }
                    ]
            except json.JSONDecodeError:
                pass
        return None

    def __call__(self, **kwargs):
        llama = kwargs.get("llama")
        messages = self._normalize_messages(kwargs.get("messages", []))

        if self._has_image(messages):
            n_img = 0
            for m in messages:
                content = m.get("content")
                if not isinstance(content, list):
                    continue
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "image_url":
                        n_img += 1
            img_urls: list[str] = []
            last_user: dict | None = None
            for m in reversed(messages):
                if m.get("role") != "user":
                    continue
                content = m.get("content")
                if not isinstance(content, list):
                    continue
                if any(isinstance(p, dict) and p.get("type") == "image_url" for p in content):
                    last_user = dict(m)
                    break
            if last_user is None:
                raise ValueError("No user message with image_url in messages")

            parts: list[dict] = []
            for part in last_user.get("content") or []:
                if not isinstance(part, dict):
                    parts.append(part)
                    continue
                if part.get("type") != "image_url":
                    parts.append(part)
                    continue
                u = part.get("image_url")
                url = u.get("url") if isinstance(u, dict) else u
                if isinstance(url, str):
                    url = self._normalize_image_url(url)
                    self._log_image_payload(url)
                    kind = "file" if url.startswith("file://") else "data"
                    img_urls.append(f"{kind}:{len(url)}")
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    parts.append(part)
            last_user["content"] = parts

            vision_messages = [
                {"role": "system", "content": self._vision_system_prompt()},
                last_user,
            ]
            print(
                f"[VISION] path=raw_sys in_messages={len(messages)} "
                f"vision_messages=2 images={n_img} sources={img_urls}",
                flush=True,
            )

            try:
                if llama is not None:
                    llama.reset()
                    clear_kv = getattr(getattr(llama, "_ctx", None), "kv_cache_clear", None)
                    if callable(clear_kv):
                        clear_kv()
                    llama.n_tokens = 0
                # Qwen25VLChatHandler.__call__ resets extra; raw_mtmd uses Llava15.
                # Do not stop on "<think>" — else 2 tokens and empty reply.
                vision_stops = [s for s in (kwargs.get("stop") or _TEXT_STOPS) if s and s != "<think>"]
                res = Llava15ChatHandler.__call__(
                    self,
                    llama=llama,
                    messages=vision_messages,
                    temperature=kwargs.get("temperature", 0.0),
                    max_tokens=kwargs.get("max_tokens"),
                    tools=kwargs.get("tools"),
                    grammar=kwargs.get("grammar"),
                    stop=vision_stops,
                    stream=kwargs.get("stream", False),
                )
                if not kwargs.get("stream", False) and res.get("choices"):
                    choice = res["choices"][0]
                    msg = choice.get("message", {})
                    raw = msg.get("content") or ""
                    cleaned = self.clean_lira_output(str(raw))
                    msg["content"] = cleaned or None
                    msg["tool_calls"] = None
                    preview = cleaned[:160].replace("\n", " ")
                    usage = res.get("usage") or {}
                    pt = usage.get("prompt_tokens")
                    # usage.prompt_tokens omits ~image_min_tokens; find_slot breaks KV.
                    warn = ""
                    if isinstance(pt, int) and pt < 400:
                        warn = " WARN: prompt_tokens<<image_min — possible find_slot/KV (model may not see the image)"
                    print(
                        f"[VISION] done n_tokens={getattr(llama, 'n_tokens', '?')} "
                        f"usage_prompt={pt} usage_total={usage.get('total_tokens')} "
                        f"reply_len={len(cleaned)} preview={preview!r}{warn}",
                        flush=True,
                    )
                return res
            finally:
                pass

        if self._jinja_template is None:
            raw = None
            if self.external_template_path and os.path.isfile(os.path.expanduser(self.external_template_path)):
                path = os.path.expanduser(self.external_template_path)
                with open(path, encoding="utf-8") as f:
                    raw = f.read()
            if not raw and llama:
                raw = llama.metadata.get("tokenizer.chat_template")
            if not raw:
                raise RuntimeError("Qwen35: no chat_template (file or GGUF)")
            self._jinja_template = jinja2.Environment(loader=jinja2.BaseLoader()).from_string(raw)

        tools = kwargs.get("tools")
        prompt = self._jinja_template.render(
            messages=messages,
            tools=tools,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        res_raw = llama.create_completion(
            prompt=prompt,
            stop=_TEXT_STOPS,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        res = llama_chat_format._convert_completion_to_chat(res_raw, stream=False)
        message = res["choices"][0]["message"]
        content = message.get("content") or ""
        tool_calls = self._parse_tool_calls(content, res.get("id", "chat"))
        if tool_calls:
            message["tool_calls"] = tool_calls
            message["content"] = None
        elif content:
            message["content"] = self.clean_lira_output(content)
        return res
