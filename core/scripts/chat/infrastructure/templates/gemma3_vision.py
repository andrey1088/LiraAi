import json
import os
import re

import jinja2
from llama_cpp import llama_chat_format
from llama_cpp.llama_chat_format import Llava15ChatHandler
from llama_cpp.llama_grammar import LlamaGrammar

from infrastructure.persona.store import PersonaStore


def _message_text(message: dict) -> str:
    raw = message.get("content")
    if raw is None:
        raw = message.get("text")
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else str(raw)


def _vision_generation_kwargs(kwargs: dict) -> dict:
    temp = float(kwargs.get("temperature", 0.7))
    return {
        "temperature": min(temp, 0.38),
        "max_tokens": min(int(kwargs.get("max_tokens") or 512), 240),
        "repeat_penalty": float(kwargs.get("repeat_penalty", 1.34)),
        "presence_penalty": float(
            kwargs.get("presence_penalty", 0.18),
        ),
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


def _normalize_tool_name(name: str) -> str:
    key = (name or "").strip().replace(" ", "_")
    aliases = {
        "google_search": "web_search",
        "google": "web_search",
        "websearch": "web_search",
    }
    return aliases.get(key, key)


def _resolve_template_path(template_path: str | None, model_path: str | None) -> str | None:
    if template_path:
        p = os.path.expanduser(template_path)
        if os.path.isfile(p):
            return p
    if model_path:
        candidate = os.path.join(os.path.dirname(os.path.expanduser(model_path)), "chat_template.jinja")
        if os.path.isfile(candidate):
            return candidate
    return None


def _build_tool_hints(persona_file: str | None, tools: list | None) -> str:
    tool_list = []
    for tool in tools or []:
        fn = (tool or {}).get("function") or {}
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        desc = str(fn.get("description") or "").strip()
        tool_list.append(f"- {name}: {desc}" if desc else f"- {name}")
    if not tool_list:
        return ""
    hint = PersonaStore.get_prompt_from_file(
        persona_file,
        "tool_hints",
        tool_list="\n".join(tool_list),
    )
    return (hint or "").strip()


class Gemma3ChatHandler(Llava15ChatHandler):
    CHAT_FORMAT = (
        "{% for message in messages %}"
        "<start_of_turn>{{ message.role }}\n"
        "{% if message.content is string %}"
        "{{ message.content }}"
        "{% else %}"
        "{% for content in message.content %}"
        "{% if content.type == 'image_url' %}"
        "{{ content.image_url.url if content.image_url.url is defined else content.image_url }}"
        "{% elif content.type == 'text' %}"
        "{{ content.text }}"
        "{% endif %}"
        "{% endfor %}"
        "{% endif %}"
        "<end_of_turn>\n"
        "{% endfor %}"
        "<start_of_turn>model\n"
    )

    def __init__(self, *args, **kwargs):
        self.model_path = kwargs.pop("model_path", None)
        template_path = kwargs.pop("template_path", None)
        self.external_template_path = _resolve_template_path(template_path, self.model_path)
        self.persona_file = kwargs.pop("persona_file", None)
        self._native_template = None
        super().__init__(*args, **kwargs)

    def __call__(self, **kwargs):
        llama = kwargs.get("llama")
        messages = kwargs.get("messages", [])

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
                system_msgs = [m for m in fixed_messages if m.get("role") == "system"]
                vision_with_image = [m for m in fixed_messages if isinstance(m.get("content"), list)]
                text_context = [
                    m for m in fixed_messages if m.get("role") != "system" and isinstance(m.get("content"), str)
                ][-2:]
                vision_messages = system_msgs + text_context
                if vision_with_image:
                    vision_messages = vision_messages + [vision_with_image[-1]]

                gen_kw = _vision_generation_kwargs(kwargs)
                res = super().__call__(
                    llama=llama,
                    messages=vision_messages,
                    tools=None,
                    grammar=None,
                    **gen_kw,
                )
                if not kwargs.get("stream", False) and "choices" in res:
                    from workers.model_worker import clean_vision_assistant_text

                    choice = res["choices"][0]
                    raw_content = _message_text(choice.get("message") or {})
                    choice["message"]["content"] = clean_vision_assistant_text(raw_content)
                    choice["message"]["tool_calls"] = None
                return res
            finally:
                if old_meta:
                    llama.metadata["tokenizer.chat_template"] = old_meta

        if self._native_template is None:
            raw_temp = None
            if self.external_template_path and os.path.exists(self.external_template_path):
                try:
                    with open(self.external_template_path, encoding="utf-8") as f:
                        raw_temp = f.read()
                except OSError:
                    raw_temp = None
            if not raw_temp and llama:
                raw_temp = llama.metadata.get("tokenizer.chat_template")
            if not raw_temp:
                raw_temp = (
                    "{% for message in messages %}<start_of_turn>{{ message.role }}\n"
                    "{{ message.content }}<end_of_turn>\n{% endfor %}<start_of_turn>model\n"
                )
            self._native_template = jinja2.Environment(loader=jinja2.BaseLoader()).from_string(raw_temp)

        tools = kwargs.get("tools")
        prompt = self._native_template.render(
            messages=fixed_messages,
            tools=tools,
            tool_hints=_build_tool_hints(self.persona_file, tools),
            add_generation_prompt=True,
            bos_token="",
            eos_token="",
        )

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
                grammar = None

        res_raw = llama.create_completion(
            prompt=prompt,
            grammar=grammar,
            stop=["<end_of_turn>", "<start_of_turn>", "<file_separator>"],
            temperature=kwargs.get("temperature", 0.1),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        res = llama_chat_format._convert_completion_to_chat(res_raw, stream=False)

        message = res["choices"][0]["message"]
        content = message.get("content") or ""
        if 'name":' in content and 'arguments":' in content and not message.get("tool_calls"):
            try:
                json_str = content.strip()
                if not json_str.startswith("{"):
                    json_str = "{" + json_str
                if not json_str.endswith("}"):
                    json_str = json_str + "}"
                data = json.loads(json_str)
                name = _normalize_tool_name(str(data.get("name", "")))
                args = data.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                if not isinstance(args, dict):
                    args = {}
                message["tool_calls"] = [
                    {
                        "id": f"call_{res['id']}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args, ensure_ascii=False),
                        },
                    }
                ]
                message["content"] = None
            except Exception:
                pass

        if not message.get("tool_calls") and content:
            message["content"] = self.clean_lira_output(content)
        return res

    @staticmethod
    def clean_lira_output(text: str) -> str:
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
        return text.replace("_", " ").strip()
