"""Qwen3-VL (native VL / MoE): text via jinja, vision via Qwen25VL without hybrid workarounds."""

from __future__ import annotations

import json
import os
from typing import Any

from llama_cpp.llama_chat_format import Qwen25VLChatHandler

from infrastructure.templates.qwen35_vl import _TEXT_STOPS, Qwen35ChatHandler


def _is_qwen3_vl_model_type(model_type: str) -> bool:
    t = (model_type or "").strip().lower()
    return "qwen3-vl" in t or "qwen3vl" in t


class Qwen3VLChatHandler(Qwen35ChatHandler):
    """Native Qwen3-VL: do not trim history for vision; default image_min_tokens from mtmd."""

    DEFAULT_IMAGE_MIN_TOKENS = 0

    def _load_template_raw(self, llama) -> str:
        if self.external_template_path:
            path = os.path.expanduser(self.external_template_path)
            if os.path.isfile(path):
                if path.endswith(".json"):
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    raw = data.get("chat_template") or data.get("template")
                    if raw:
                        return raw
                with open(path, encoding="utf-8") as f:
                    return f.read()
        if llama:
            raw = llama.metadata.get("tokenizer.chat_template")
            if raw:
                return raw
        raise RuntimeError("Qwen3-VL: no chat_template (file or GGUF)")

    def __call__(self, **kwargs):
        llama = kwargs.get("llama")
        messages = self._normalize_messages(kwargs.get("messages", []))

        if self._has_image(messages):
            for m in messages:
                content = m.get("content")
                if not isinstance(content, list):
                    continue
                parts: list[Any] = []
                for part in content:
                    if not isinstance(part, dict) or part.get("type") != "image_url":
                        parts.append(part)
                        continue
                    u = part.get("image_url")
                    url = u.get("url") if isinstance(u, dict) else u
                    if isinstance(url, str):
                        url = self._normalize_image_url(url)
                        self._log_image_payload(url)
                        parts.append({"type": "image_url", "image_url": {"url": url}})
                    else:
                        parts.append(part)
                m["content"] = parts

            n_img = sum(
                1
                for m in messages
                for p in (m.get("content") or [])
                if isinstance(p, dict) and p.get("type") == "image_url"
            )
            print(
                f"[VISION] path=qwen3vl_full in_messages={len(messages)} images={n_img}",
                flush=True,
            )
            vision_stops = [s for s in (kwargs.get("stop") or _TEXT_STOPS) if s and s != "<think>"]
            res = Qwen25VLChatHandler.__call__(
                self,
                llama=llama,
                messages=messages,
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
                tool_calls = self._parse_tool_calls(str(raw), res.get("id", "chat"))
                msg["tool_calls"] = tool_calls
                usage = res.get("usage") or {}
                pt = usage.get("prompt_tokens")
                warn = ""
                if isinstance(pt, int) and pt < 400:
                    warn = " WARN: prompt_tokens low — check vision/KV"
                preview = (cleaned or "")[:160].replace("\n", " ")
                print(
                    f"[VISION] done usage_prompt={pt} reply_len={len(cleaned)} preview={preview!r}{warn}",
                    flush=True,
                )
            return res

        if self._jinja_template is None:
            import jinja2

            raw = self._load_template_raw(llama)
            self._jinja_template = jinja2.Environment(loader=jinja2.BaseLoader()).from_string(raw)

        tools = kwargs.get("tools")
        n_tools = len(tools) if tools else 0
        roles = [m.get("role") for m in messages]
        print(
            f"[TOOLS] qwen3vl jinja tools={n_tools} messages={len(messages)} roles={roles}",
            flush=True,
        )
        prompt = self._jinja_template.render(
            messages=messages,
            tools=tools,
            add_generation_prompt=True,
        )
        if n_tools:
            print(
                f"[TOOLS] prompt has <tools>={('<tools>' in prompt)} "
                f"tool_call_hint={('tool_call' in prompt)} sens={[r for r in roles if r == 'sens']}",
                flush=True,
            )
        res_raw = llama.create_completion(
            prompt=prompt,
            stop=kwargs.get("stop") or _TEXT_STOPS,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        from llama_cpp import llama_chat_format

        res = llama_chat_format._convert_completion_to_chat(res_raw, stream=False)
        message = res["choices"][0]["message"]
        content = message.get("content") or ""
        tool_calls = self._parse_tool_calls(content, res.get("id", "chat"))
        if tool_calls:
            fn = tool_calls[0].get("function", {})
            print(
                f"[TOOLS] parsed tool_call name={fn.get('name')!r} args_len={len(str(fn.get('arguments') or ''))}",
                flush=True,
            )
            message["tool_calls"] = tool_calls
            message["content"] = None
        elif content and "<tool_call>" in content.lower():
            print(
                f"[TOOLS] WARN: <tool_call> in output but parse failed preview={content[:200]!r}",
                flush=True,
            )
        elif content:
            message["content"] = self.clean_lira_output(content)
        return res
