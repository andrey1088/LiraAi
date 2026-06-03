import os
import json
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_PATH = os.path.join(BASE_DIR, "data", "train_data.jsonl")

_CHAT = os.path.join(BASE_DIR, "core", "scripts", "chat")
if _CHAT not in sys.path:
    sys.path.insert(0, _CHAT)

from infrastructure.locale.variables import var_get  # noqa: E402
from infrastructure.locale.runtime_vars import runtime_format_vars  # noqa: E402


def _default_instruction(locale: str = "ru") -> str:
    template = str(
        var_get("tuning.dpo_system_instruction", locale)
        or var_get("tuning.dpo_system_instruction", "en")
        or ""
    )
    return template.format(**runtime_format_vars(locale=locale))


def save_alpaca_data(user_input, assistant_output, instruction=None, file_path=DATA_PATH):
    if instruction is None:
        instruction = _default_instruction("ru")

    data_entry = {
        "instruction": instruction,
        "input": user_input,
        "output": assistant_output,
    }

    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data_entry, ensure_ascii=False) + "\n")
        f.flush()
