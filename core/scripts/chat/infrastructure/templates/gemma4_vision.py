import json
import os
import re

import jinja2
from llama_cpp import llama_chat_format
from llama_cpp.llama_chat_format import Llava15ChatHandler
from llama_cpp.llama_grammar import LlamaGrammar

_GEMMA_CONTROL_TOKEN_RE = re.compile(
    r"<start_of_turn>|<end_of_turn>|<turn\|>|"
    r"<\|[^|]+\|>|</?think(?:ing)?>|"
    r"<\|?channel\|?>|channel\s*>",
    re.IGNORECASE,
)


def _message_text(message: dict) -> str:
    raw = message.get("content")
    if raw is None:
        raw = message.get("text")
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else str(raw)


def _vision_generation_kwargs(kwargs: dict) -> dict:
    """Vision generation params: no loops, no tool-json."""
    temp = float(kwargs.get("temperature", 0.7))
    return {
        "temperature": min(temp, 0.38),
        "max_tokens": min(int(kwargs.get("max_tokens") or 512), 240),
        "repeat_penalty": float(kwargs.get("repeat_penalty", 1.34)),
        "presence_penalty": float(kwargs.get("presence_penalty", 0.18)),
        "stop": [
            "<end_of_turn>",
            "<start_of_turn>",
            "start_of_turn",
            "end_of_turn",
            "<eos>",
            "<|eot_id|>",
            "\n turn",
            " turn turn",
            "//thought",
            "// thought",
            "<|channel|>thought",
        ],
    }


def _sanitize_vision_model_text(raw: str) -> str:
    """Strip Gemma service tokens without deleting the whole reply."""
    from workers.model_worker import strip_leading_channel_thought_preamble

    if not raw or not str(raw).strip():
        return ""
    text = str(raw).strip()
    if "call:" in text.lower():
        text = re.sub(r"call:\s*\{.*", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    text = _GEMMA_CONTROL_TOKEN_RE.sub("", text)
    text = re.sub(r"<[^>]{0,48}>", "", text)
    text = strip_leading_channel_thought_preamble(text)
    return text.strip()


class Gemma4ChatHandler(Llava15ChatHandler):
    # Keep working vision format only
    CHAT_FORMAT = (
        "{% for message in messages %}"
        "<start_of_turn>{{ message.role }}\n"
        "{{ message.content }}<end_of_turn>\n"
        "{% endfor %}"
        "<start_of_turn>model\n"
    )

    def __init__(self, *args, **kwargs):
        self.external_template_path = kwargs.pop("template_path", None)
        self.persona_file = kwargs.pop("persona_file", None)
        self._native_template = None

        # 2. kwargs now only fields the parent accepts.
        # Call super.
        super().__init__(*args, **kwargs)

        # Debug (self.verbose set after super)
        if self.external_template_path:
            pass

    def __call__(self, **kwargs):
        llama = kwargs.get("llama")
        messages = kwargs.get("messages", [])

        # 1. Check for image (vision)
        # Normalize to avoid Message object errors
        fixed_messages = []
        for m in messages:
            content = m.content if hasattr(m, "content") else m.get("content", "")
            role = m.role if hasattr(m, "role") else m.get("role", "user")
            fixed_messages.append({"role": role, "content": content})

        has_image = any(isinstance(m["content"], list) for m in fixed_messages)

        if has_image:
            if llama:
                llama.reset()
            old_meta = llama.metadata.get("tokenizer.chat_template")
            llama.metadata["tokenizer.chat_template"] = self.CHAT_FORMAT
            try:
                vision_msg = next(m for m in reversed(fixed_messages) if isinstance(m["content"], list))
                vision_msg_copy = vision_msg.copy()
                vision_msg_copy["role"] = "user"

                from infrastructure.persona.store import PersonaStore

                sys_msg = next(
                    (m for m in fixed_messages if m["role"] == "system"),
                    {
                        "role": "system",
                        "content": PersonaStore.get_prompt_from_file(self.persona_file, "vision_system"),
                    },
                )

                # CALL WITHOUT TOOLS OR GRAMMAR
                gen_kw = _vision_generation_kwargs(kwargs)
                res = super().__call__(
                    llama=llama,
                    messages=[sys_msg, vision_msg_copy],
                    tools=None,
                    grammar=None,
                    **gen_kw,
                )

                if not kwargs.get("stream", False) and "choices" in res:
                    from workers.model_worker import clean_vision_assistant_text

                    choice = res["choices"][0]
                    message = choice.get("message") or {}
                    raw_content = _message_text(message)
                    cleaned = clean_vision_assistant_text(raw_content)
                    if not cleaned.strip() and raw_content.strip():
                        print(
                            f"[VISION] gemma4: reply empty after clean "
                            f"raw_len={len(raw_content)} preview={raw_content[:120]!r}",
                            flush=True,
                        )
                    choice["message"]["content"] = cleaned
                    choice["message"]["tool_calls"] = None
                    if cleaned.strip():
                        print(
                            f"[VISION] gemma4: done reply_len={len(cleaned)} finish={choice.get('finish_reason')!r}",
                            flush=True,
                        )
                    else:
                        print(
                            f"[VISION] gemma4: empty reply "
                            f"finish={choice.get('finish_reason')!r} "
                            f"raw_preview={raw_content[:160]!r}",
                            flush=True,
                        )

                return res
            finally:
                if old_meta:
                    llama.metadata["tokenizer.chat_template"] = old_meta

        # 2. TEXT MODE
        if not has_image:
            # 1. Template from GGUF metadata
            if self._native_template is None:
                raw_temp = None

                # Try external template file
                if self.external_template_path and os.path.exists(self.external_template_path):
                    try:
                        with open(self.external_template_path, "r", encoding="utf-8") as f:
                            raw_temp = f.read()
                        pass
                    except Exception:
                        pass

                # On miss/error — GGUF metadata as before
                if not raw_temp:
                    raw_temp = llama.metadata.get("tokenizer.chat_template")

                # If still empty — hardcoded fallback
                if not raw_temp:
                    raw_temp = (
                        "{% for message in messages %}<start_of_turn>{{ message.role }}\n{{ message.content }}"
                        "<end_of_turn>\n{% endfor %}<start_of_turn>model\n"
                    )

                self._native_template = jinja2.Environment(loader=jinja2.BaseLoader()).from_string(raw_temp)
                if not raw_temp:
                    # Fallback Gemma format when GGUF metadata empty
                    raw_temp = (
                        "{% for message in messages %}<start_of_turn>{{ message.role }}\n{{ message.content }}"
                        "<end_of_turn>\n{% endfor %}<start_of_turn>model\n"
                    )
                self._native_template = jinja2.Environment(loader=jinja2.BaseLoader()).from_string(raw_temp)

            # 2. Render prompt (pass tools into template)
            tools = kwargs.get("tools")
            prompt = self._native_template.render(messages=fixed_messages, tools=tools, add_generation_prompt=True)

            # 3. Grammar for tool use (JSON not plain text)
            grammar = None
            if tools and isinstance(tools, list):
                try:
                    tool_names = [t["function"]["name"] for t in tools]
                    schema = {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "enum": tool_names},
                            "arguments": {"type": "object"},
                        },
                        "required": ["name", "arguments"],
                    }
                    grammar = LlamaGrammar.from_json_schema(json.dumps(schema))
                except Exception:
                    pass

            # 4. create_completion (bypasses handlers, avoids recursion)
            res_raw = llama.create_completion(
                prompt=prompt,
                grammar=grammar,
                stop=["<end_of_turn>", "<start_of_turn>", "<file_separator>"],
                temperature=kwargs.get("temperature", 0.1),
                max_tokens=kwargs.get("max_tokens", 1024),
            )

            # 5. Convert response
            res = llama_chat_format._convert_completion_to_chat(res_raw, stream=False)

            message = res["choices"][0]["message"]
            content = message.get("content") or ""

            # CASE A: model returned JSON (tool call)
            if 'name":' in content and 'arguments":' in content and not message.get("tool_calls"):
                try:
                    json_str = content.strip()
                    if not json_str.startswith("{"):
                        json_str = "{" + json_str
                    if not json_str.endswith("}"):
                        json_str = json_str + "}"

                    data = json.loads(json_str)

                    message["tool_calls"] = [
                        {
                            "id": f"call_{res['id']}",
                            "type": "function",
                            "function": {
                                "name": data["name"].replace(" ", "_"),
                                "arguments": json.dumps(data["arguments"]),
                            },
                        }
                    ]
                    message["content"] = None
                    pass
                except Exception:
                    # If parse fails, fall through to text cleanup
                    pass

            # CASE B: plain text (or unparsed JSON)
            if not message.get("tool_calls") and content:
                # CALL PARENT IMPLEMENTATION
                cleaned = self.clean_lira_output(content)

                message["content"] = cleaned
                pass

            return res

    def clean_lira_output(self, text: str) -> str:
        from workers.model_worker import strip_leading_channel_thought_preamble

        if not text:
            return ""

        text = strip_leading_channel_thought_preamble(text)
        text = re.sub(
            r"^(?://+\s*)?(?:thought|thinking)\b\s*(?::\s*)?",
            "",
            text,
            count=1,
            flags=re.IGNORECASE,
        ).lstrip()
        text = text.replace("_", " ")
        return text.strip()
