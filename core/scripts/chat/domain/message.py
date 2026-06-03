from dataclasses import dataclass

from infrastructure.locale.i18n import tr_tools


@dataclass
class Message:
    def __init__(
        self,
        role,
        content,
        tool_call_id=None,
        image_url=None,
        tool_function_name=None,
        tool_function_arguments=None,
    ):
        self.role = role
        self.content = content
        self.tool_call_id = tool_call_id
        self.image_url = image_url  # File path or base64
        self.tool_function_name = tool_function_name
        self.tool_function_arguments = tool_function_arguments

    def to_dict(self):
        """For internal use and the UI."""
        return {"role": self.role, "content": self.content, "image_url": self.image_url}

    def to_llm_dict(self):
        """Convert to Gemma 3 / OpenAI Vision format."""
        if self.role == "system":
            return {"role": self.role, "content": self.content}

        # TOOL role (tool response)
        if self.role == "tool":
            return {"role": self.role, "content": self.content or "", "tool_call_id": self.tool_call_id}

        # ASSISTANT when invoking a tool
        if self.role == "assistant" and self.tool_call_id:
            if not self.tool_function_name:
                # Legacy DB rows without tool name — do not invent tool_calls.
                return {
                    "role": self.role,
                    "content": self.content or tr_tools("chat.domain.tool_pending", "en"),
                }
            fn_name = str(self.tool_function_name).replace(" ", "_")
            fn_args = self.tool_function_arguments
            if fn_args is None:
                fn_args = "{}"
            elif not isinstance(fn_args, str):
                import json

                fn_args = json.dumps(fn_args, ensure_ascii=False)
            return {
                "role": self.role,
                "content": self.content,
                "tool_calls": [
                    {
                        "id": self.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": fn_name,
                            "arguments": fn_args,
                        },
                    }
                ],
            }

        # Image(s) present (one or more — PDF pages)
        if self.image_url:
            urls = self.image_url if isinstance(self.image_url, list) else [self.image_url]
            content_list = [{"type": "text", "text": self.content or ""}]
            for url in urls:
                if not url:
                    continue
                u = str(url)
                if u.startswith("/") and not u.startswith("file://"):
                    u = "file://" + u
                content_list.append({"type": "image_url", "image_url": {"url": u}})
            return {"role": self.role, "content": content_list}

        return {"role": self.role, "content": self.content or ""}
