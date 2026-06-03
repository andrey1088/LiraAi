"""Runtime placeholders: owner name, active model, app branding (from config.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from infrastructure.locale.variables import var_get
from infrastructure.paths import config_path as default_config_path


def _load_config_dict(config_path: str | Path | None = None) -> dict[str, Any]:
    raw = config_path or os.environ.get("LIRA_CONFIG") or str(default_config_path())
    path = Path(os.path.expanduser(str(raw)))
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def user_format_vars_from_config(
    config: dict[str, Any] | None = None,
    *,
    locale: str = "ru",
) -> dict[str, str]:
    """{user_name}, {user_name_genitive}, … from config.user."""
    block = (config or {}).get("user")
    if not isinstance(block, dict):
        block = {}
    default = str(var_get("user.default_display_name", locale) or "User").strip()
    name = str(block.get("display_name") or default).strip() or default
    return {
        "user_name": name,
        "user_name_genitive": str(block.get("display_name_genitive") or name).strip() or name,
        "user_name_dative": str(block.get("display_name_dative") or name).strip() or name,
        "user_name_instrumental": str(block.get("display_name_instrumental") or name).strip() or name,
    }


def app_name_from_config(
    config: dict[str, Any] | None = None,
    *,
    locale: str = "ru",
) -> str:
    block = (config or {}).get("app")
    if isinstance(block, dict):
        name = str(block.get("product_name") or "").strip()
        if name:
            return name
    return str(var_get("app.product_name", locale) or var_get("app.product_name", "en") or "Lira").strip()


def model_name_from_config(
    config: dict[str, Any] | None = None,
    *,
    model_id: str | None = None,
) -> str:
    cfg = config or {}
    models = cfg.get("models")
    if not isinstance(models, list):
        return ""
    target_id = model_id if model_id is not None else cfg.get("active_model_id")
    if target_id is not None:
        for m in models:
            if isinstance(m, dict) and str(m.get("id")) == str(target_id):
                return str(m.get("name") or "").strip()
    active = str(cfg.get("active_model") or "").strip()
    if active:
        return active
    if models and isinstance(models[0], dict):
        return str(models[0].get("name") or "").strip()
    return ""


def runtime_format_vars(
    config: dict[str, Any] | None = None,
    *,
    locale: str = "ru",
    model_name: str | None = None,
    config_path: str | Path | None = None,
) -> dict[str, str]:
    """Merged placeholders for .format() in locale strings and tool descriptions."""
    cfg = config if config is not None else _load_config_dict(config_path)
    out = user_format_vars_from_config(cfg, locale=locale)
    out["app_name"] = app_name_from_config(cfg, locale=locale)
    mn = (model_name or "").strip() or model_name_from_config(cfg)
    out["model_name"] = mn or out["app_name"]
    return out


def format_locale_string(
    template: str,
    *,
    locale: str = "ru",
    config: dict[str, Any] | None = None,
    model_name: str | None = None,
    **extra: Any,
) -> str:
    if not template:
        return ""
    vars_map = runtime_format_vars(config, locale=locale, model_name=model_name)
    vars_map.update({k: str(v) for k, v in extra.items()})
    try:
        return str(template).format(**vars_map)
    except (KeyError, ValueError):
        return str(template)
