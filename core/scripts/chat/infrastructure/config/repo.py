import json
import os
import shutil
from pathlib import Path

from core.scripts.chat.domain.model import AIModel
from infrastructure.config.defaults import (
    DEFAULT_UI_LOCALE,
    TTS_PROFILES,
    UI_LOCALES,
    config_path_tilde,
    default_tts_block,
    ensure_config_defaults,
)
from infrastructure.paths import lira_data, path_for_config, resolve_path
from infrastructure.persona.store import PersonaStore, default_persona_document


class ConfigRepository:
    def __init__(self, config_path):
        self.config_path = os.path.expanduser(config_path)
        self.config = self.load_config()

    def get_ui_locale(self) -> str:
        loc = str(self.config.get("ui_locale") or DEFAULT_UI_LOCALE).strip().lower()
        return loc if loc in UI_LOCALES else DEFAULT_UI_LOCALE

    def get_tts_block(self) -> dict:
        tts = self.config.get("tts")
        if isinstance(tts, dict):
            return tts
        return default_tts_block(self.get_ui_locale())

    def get_tts_model_path(self, locale: str | None = None) -> str:
        loc = locale or self.get_ui_locale()
        tts = self.get_tts_block()
        if str(tts.get("locale")) == loc and tts.get("model_path"):
            return resolve_path(str(tts["model_path"]))
        return resolve_path(TTS_PROFILES[loc]["model_path"])

    def get_tts_speaker_for_locale(self, model_info: AIModel, locale: str | None = None) -> str:
        loc = locale or self.get_ui_locale()
        tts = self.get_tts_block()
        if str(tts.get("locale")) == loc:
            sp = str(tts.get("speaker") or "").strip()
            if sp:
                return sp
        if loc == "en":
            sp = str(tts.get("en_speaker") or "").strip()
            if sp:
                return sp
            return TTS_PROFILES["en"]["default_speaker"]
        return (model_info.voice or "kseniya").strip() or "kseniya"

    def save_ui_locale(
        self,
        locale: str,
        *,
        speaker: str | None = None,
        model_path: str | None = None,
    ) -> str:
        """Persist ui_locale and active Silero pack (like save_active_model)."""
        loc = str(locale or DEFAULT_UI_LOCALE).strip().lower()
        if loc not in UI_LOCALES:
            loc = DEFAULT_UI_LOCALE
        m_info = self.get_active_model_info()
        prof = TTS_PROFILES[loc]
        sp = (speaker or "").strip() or self.get_tts_speaker_for_locale(m_info, loc)
        path = (model_path or "").strip() or prof["model_path"]

        self.config["ui_locale"] = loc
        tts = self.config.setdefault("tts", {})
        tts["locale"] = loc
        tts["speaker"] = sp
        tts["model_path"] = config_path_tilde(path)
        tts["sample_rate"] = int(prof.get("sample_rate", 48000))
        tts["en_speaker"] = sp if loc == "en" else str(tts.get("en_speaker") or TTS_PROFILES["en"]["default_speaker"])
        self.save_config()
        return loc

    def load_config(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if ensure_config_defaults(cfg):
            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=4)
                    f.write("\n")
            except OSError:
                pass
        return cfg

    def get_active_model_info(self):
        active_id = self.config.get("active_model_id", "1")
        for m in self.config["models"]:
            if str(m["id"]) == str(active_id):
                return AIModel.from_dict(m)
        return AIModel.from_dict(self.config["models"][0])

    def get_user_format_vars(self) -> dict[str, str]:
        """Placeholders for persona texts ({user_name} from config.user)."""
        from infrastructure.locale.runtime_vars import user_format_vars_from_config

        return user_format_vars_from_config(self.config, locale=self.get_ui_locale())

    def get_runtime_format_vars(self, model_info: AIModel | None = None) -> dict[str, str]:
        from infrastructure.locale.runtime_vars import runtime_format_vars

        mn = model_info.name if model_info is not None else None
        return runtime_format_vars(self.config, locale=self.get_ui_locale(), model_name=mn)

    def get_app_name(self) -> str:
        from infrastructure.locale.runtime_vars import app_name_from_config

        return app_name_from_config(self.config, locale=self.get_ui_locale())

    def _persona_format_kwargs(self, model_info: AIModel, **extra) -> dict[str, str]:
        out = {**self.get_runtime_format_vars(model_info)}
        out.update({k: str(v) for k, v in extra.items()})
        return out

    def get_model_info_by_id(self, model_id: str) -> AIModel | None:
        for m in self.config.get("models", []):
            if str(m.get("id")) == str(model_id):
                return AIModel.from_dict(m)
        return None

    def get_path(self, key, model_id=None):
        if not model_id:
            model = self.get_active_model_info()
        else:
            raw_dict = next(m for m in self.config["models"] if str(m["id"]) == str(model_id))
            model = AIModel.from_dict(raw_dict)

        # Read path via getattr — it is now an object
        path = getattr(model, key, None)
        return resolve_path(path) if path else None

    def get_or_create_db_path(self):
        m_info = self.get_active_model_info()

        if getattr(m_info, "model_class", "text") in ("text-to-image", "image-edit"):
            db_path = str(lira_data("db", "gallery.db"))
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            return db_path

        # Ensure persona exists while preparing model environment
        self._load_or_create_persona(m_info)

        if m_info.db_path:
            return resolve_path(m_info.db_path)

        safe_type = m_info.model_type.lower().replace(" ", "_").replace("-", "_")
        model_id = m_info.id
        new_db_path = path_for_config(lira_data("memory", f"{safe_type}-{model_id}.db"))

        # Update raw config for persistence
        for m in self.config["models"]:
            if str(m.get("id")) == str(model_id):
                m["db_path"] = new_db_path

        self.save_config()
        pass
        return resolve_path(new_db_path)

    def save_active_model(self, model_id):
        self.config["active_model_id"] = str(model_id)
        for m in self.config.get("models", []):
            if str(m.get("id")) == str(model_id):
                self.config["active_model"] = str(m.get("name") or "")
                break
        self.save_config()

    def save_config(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
            pass
        except Exception:
            pass

    def update_settings(self, model_id, new_data):
        for model in self.config["models"]:
            current_id = str(model.get("id", "")).strip()
            target_id = str(model_id).strip()

            if current_id == target_id:
                if "settings" not in model:
                    model["settings"] = {}

                for key, val in new_data.items():
                    try:
                        if val.isdigit():
                            model["settings"][key] = int(val)
                        else:
                            model["settings"][key] = float(val)
                    except (AttributeError, ValueError, TypeError):
                        # Fallback: keep raw value if it is not a clean int/float string.
                        model["settings"][key] = val

                self.save_config()
                return True
        return False

    def _load_or_create_persona(self, model_info: AIModel):
        """Load or create persona file for the model."""
        if not model_info.persona_file:
            # 1. Build path: type-id.json (ASCII only)
            safe_type = model_info.model_type.lower().replace(" ", "_").replace("-", "_")
            auto_path = path_for_config(lira_data("personas", f"{safe_type}-{model_info.id}.json"))

            # 2. Update config dict and save to disk
            for m in self.config.get("models", []):
                if str(m.get("id")) == str(model_info.id):
                    m["persona_file"] = auto_path
                    break

            self.save_config()
            model_info.persona_file = auto_path
            pass

        persona_path = Path(os.path.expanduser(model_info.persona_file))

        if not persona_path.exists():
            persona_path.parent.mkdir(parents=True, exist_ok=True)
            template = persona_path.parent / "_template" / "persona.json"
            if template.is_file():
                shutil.copyfile(template, persona_path)
            else:
                PersonaStore.save_path(persona_path, default_persona_document())
            return PersonaStore.load_path(persona_path)

        return PersonaStore.load_and_upgrade(persona_path)

    def get_persona_document(self, model_info: AIModel) -> dict:
        return self._load_or_create_persona(model_info)

    def get_persona_prompt(self, model_info: AIModel, locale: str | None = None) -> str:
        """System + additional_instructions from persona file."""
        doc = self._load_or_create_persona(model_info)
        if not doc:
            return ""
        loc = locale or self.get_ui_locale()
        return PersonaStore.build_system_prompt(
            doc,
            locale=loc,
            **self._persona_format_kwargs(model_info),
        )

    def get_persona_text(
        self,
        model_info: AIModel,
        prompt_key: str,
        locale: str | None = None,
        **format_kwargs,
    ) -> str:
        """Dynamic prompt by key (vision, gallery, telegram, …)."""
        doc = self._load_or_create_persona(model_info)
        kwargs = self._persona_format_kwargs(model_info, **format_kwargs)
        loc = locale or self.get_ui_locale()
        return PersonaStore.get_prompt(doc, prompt_key, locale=loc, **kwargs)
