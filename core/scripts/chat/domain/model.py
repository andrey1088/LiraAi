from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class AIModel:
    id: str
    name: str
    model_class: str  # Model class (text, text-to-image, etc.)
    model_type: str
    model_path: str
    icon_path: str
    voice: str
    chat_format: str
    vae_path: Optional[str] = None
    llm_path: Optional[str] = None
    lora_path: Optional[str] = None
    persona_file: Optional[str] = None
    db_path: Optional[str] = None
    clip_model_path: Optional[str] = None
    template_path: Optional[str] = None
    limbic_images_path: Optional[str] = None
    perception_daemon: bool = False
    settings: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _normalize_settings(data: Dict[str, Any]) -> Dict[str, Any]:
        settings = dict(data.get("settings") or {})
        # sd_aspect_sizes must live under settings; tolerate top-level in config.json
        top_aspects = data.get("sd_aspect_sizes")
        if isinstance(top_aspects, dict) and "sd_aspect_sizes" not in settings:
            settings["sd_aspect_sizes"] = top_aspects
        return settings

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AIModel":
        """Build entity from configuration dict."""
        return cls(
            id=str(data.get("id", "")),
            name=data.get("name", ""),
            # Legacy configs without model_class default to 'text'
            model_class=data.get("model_class", "text"),
            model_type=data.get("model_type", ""),
            model_path=data.get("model_path", ""),
            icon_path=data.get("icon_path", ""),
            voice=data.get("voice", ""),
            chat_format=data.get("chat_format", ""),
            persona_file=data.get("persona_file"),
            db_path=data.get("db_path"),
            lora_path=data.get("lora_path"),
            clip_model_path=data.get("clip_model_path"),
            settings=cls._normalize_settings(data),
            template_path=data.get("template_path"),
            limbic_images_path=data.get("limbic_images_path"),
            perception_daemon=bool(data.get("perception_daemon", False)),
        )
