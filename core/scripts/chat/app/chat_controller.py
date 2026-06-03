import base64
import io
import json
import os
import re
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import Qt, QThread, QTimer
from PyQt6.QtGui import QImage

from core.scripts.chat.app.context_manager import ContextManager
from core.scripts.chat.domain.message import Message
from core.scripts.chat.ui.camera_capture_dialog import CameraCaptureDialog
from core.scripts.chat.workers.image_generation_worker import ImageGenerationWorker
from core.scripts.chat.workers.model_worker import (
    ModelWorker,
    clean_vision_assistant_text,
    strip_degenerate_token_runs,
    strip_leading_channel_thought_preamble,
    strip_leading_tool_results_echo,
)
from core.scripts.extract_for_train import save_alpaca_data
from infrastructure.attachments.text import (
    MAX_ATTACHMENT_BYTES,
    extract_document_text,
)
from infrastructure.config.tool_policy_registry import (
    TOOL_FOLLOWUP_TOOLS_KEY,
    TOOL_FORBIDDEN_IF_LAST_TOOL_KEY,
    TOOL_ONLY_AT_CHAIN_STEPS_KEY,
    camera_capture_user_intent_refusal,
    camera_intent_substrings,
    camera_web_suppress_config,
    chain_limits,
    gallery_intent_substrings,
    gallery_search_user_intent_refusal,
    gallery_web_suppress_config,
    load_tool_policy_registry,
    memory_search_gallery_redirect_refusal,
    merge_policies_into_tool_schema,
    orphan_web_fetch_refusal,
    strip_tool_schema_meta as _strip_tool_schema_meta,
    system_policy_append_texts,
    tool_forbidden_if_last_hint,
    web_intent_substrings,
    web_search_user_intent_refusal,
)
from infrastructure.limbic.emotion_detector import EmotionDetector
from infrastructure.limbic.prompt import render_limbic_prompt
from infrastructure.limbic.state import LimbicState, format_emotion_vector
from infrastructure.locale.variables import var_get, var_list
from infrastructure.paths import lira_data, lira_root, resolve_path
from infrastructure.memory.repo import normalize_research_url
from infrastructure.model_tasks.gallery import (
    GalleryDescribeProcess,
    is_bad_gallery_description,
    load_gallery_describe_settings,
    sanitize_gallery_description,
    should_redescribe_gallery_lead,
)
from infrastructure.semantic.engine import SemanticEngine
from tools.llm import (
    build_chat_tool_schema,
    camera_intent_fallback,
    chat_tool_implementations,
    gallery_intent_fallback,
    localize_tool_policy_registry,
    tool_history_trunc_marker,
    web_intent_fallback,
)

# P1: hard cap on one tool's text in session history (before ContextManager emergency clip).
_TOOL_HISTORY_MAX_CHARS = 28000

_USER_QUESTION_FOR_VISION_MAX_CHARS = 4500


class ChatController:
    def __init__(self, window):
        self.pending_attachments: list[dict] = []
        self._document_upload_buffers: dict[str, dict] = {}
        self.pending_image = None
        self.window = window
        self.current_user_text = ""
        self.is_first_model_token = True
        self.last_pair = None
        self.worker = None
        self._proactive_worker = None
        self._proactive_turn_active = False
        self._proactive_user_prompt = ""
        self._perception_eval_worker = None
        self._proactive_is_telegram = False
        self._gallery_describe_busy = False
        self._gallery_describe_cancel = False
        self._gallery_describe_total = 0
        self._gallery_describe_done = 0
        self._gallery_describe_process: GalleryDescribeProcess | None = None
        self._gallery_describe_job_path: str | None = None
        self._gallery_describe_mode = "missing"
        self._gallery_describe_perception_was_running = False
        self._gallery_describe_gpu_handoff = False
        self._gallery_describe_stored_items: list[tuple[int, str]] = []
        self._gallery_describe_defer_vectors = False
        self._gallery_describe_vector_pending: list[tuple[int, str]] = []
        self._gallery_redescribe_pass = False
        self._gallery_describe_redescribe_queue: list[tuple[int, str]] | None = None
        self._gallery_describe_silent = False
        self._gallery_describe_cuda_retries = 0
        self._gallery_describe_use_subprocess = True
        self._image_gen_worker: ImageGenerationWorker | None = None
        # Qwen Image Edit: generate() only on Qt GUI thread (same as offload), else TypeError in CUDA/accelerate.
        self._qwen_edit_main_busy = False
        self._pending_qwen_edit: tuple | None = None
        # Qwen Image Edit: primary + optional second image (order = pipeline order).
        self.pending_image_edit_primary: dict | None = None
        self.pending_image_edit_secondary: dict | None = None
        self.semantic_engine = SemanticEngine()
        self.emotion_detector = EmotionDetector()
        self.limbic_state = LimbicState()
        self.context_manager = ContextManager(window)
        self._tool_chain_count = 0
        self._web_search_cache_this_turn = {}
        self._web_fetch_urls_ok_this_turn = set()
        self._web_fetch_success_this_turn = 0
        self._web_fetch_dedupe_streak = 0
        self._web_research_touched_this_turn = False
        self._last_tool_name = None
        self._tool_history_truncated_this_turn = False
        self.available_tools = chat_tool_implementations()
        self.rebuild_tools_for_locale(self.window.config_repo.get_ui_locale())

    def _tr_ui(self, msgid: str, **fmt) -> str:
        from infrastructure.locale.i18n import tr

        loc = self.window.config_repo.get_ui_locale()
        merged = {**self.window.config_repo.get_runtime_format_vars(), **fmt}
        text = tr(msgid, loc)
        try:
            return text.format(**merged)
        except (KeyError, ValueError):
            return text

    def _tr_tools(self, key: str, **fmt) -> str:
        from infrastructure.locale.i18n import tr_tools

        text = tr_tools(key, getattr(self, "_tools_locale", "ru"))
        if fmt:
            try:
                return text.format(**fmt)
            except (KeyError, ValueError):
                return text
        return text

    def _var(self, dotted: str, **fmt) -> str:
        from infrastructure.locale.variables import var_get

        loc = getattr(self, "_tools_locale", "ru")
        val = var_get(dotted, loc, default="")
        text = str(val) if val is not None else ""
        if fmt and text:
            try:
                return text.format(**fmt)
            except (KeyError, ValueError):
                return text
        return text

    def _gallery_description_locale(self) -> str:
        return self.window.config_repo.get_ui_locale()

    def rebuild_tools_for_locale(self, locale: str | None = None) -> None:
        """Rebuild tool schema and policies when ui_locale changes."""
        from infrastructure.locale.i18n import clear_cache

        loc = locale or self.window.config_repo.get_ui_locale()
        clear_cache()
        self._tools_locale = loc
        self._tool_policy_registry = localize_tool_policy_registry(
            load_tool_policy_registry(),
            loc,
        )
        lim = chain_limits(self._tool_policy_registry)
        self.MAX_TOOL_CHAIN_STEPS = int(lim.get("max_tool_chain_steps", 10))
        self.MAX_WEB_FETCH_SUCCESS_PER_TURN = int(lim.get("max_web_fetch_success_per_turn", 3))
        self._web_fetch_dedupe_disable_at = int(lim.get("disable_tools_after_web_fetch_dedupe_streak", 2))
        self._policy_system_appends = system_policy_append_texts(self._tool_policy_registry)
        gi = gallery_intent_substrings(self._tool_policy_registry)
        self._gallery_user_intent_needles = gi if gi else gallery_intent_fallback(loc)
        ci = camera_intent_substrings(self._tool_policy_registry)
        self._camera_user_intent_needles = ci if ci else camera_intent_fallback(loc)
        wi = web_intent_substrings(self._tool_policy_registry)
        self._web_user_intent_needles = wi if wi else web_intent_fallback(loc)
        fmt = self.window.config_repo.get_runtime_format_vars()
        self.debug_tool_schema = merge_policies_into_tool_schema(
            build_chat_tool_schema(loc, format_vars=fmt),
            self._tool_policy_registry,
        )
        self._tool_schema_by_name = {t["function"]["name"]: t for t in self.debug_tool_schema}
        prefix = "\n\n"
        self._tool_history_trunc_marker = prefix + tool_history_trunc_marker(loc).lstrip()

    def _retire_model_worker(self, worker: ModelWorker | None, *, wait_ms: int = 180_000) -> None:
        """Disconnect only our slots (no wildcard disconnect — Qt warns about destroyed)."""
        if worker is None:
            return
        try:
            worker.finished_token.disconnect(self.window.update_chat)
        except TypeError:
            pass
        try:
            worker.finished_answer.disconnect(self.window.finalize_answer)
        except TypeError:
            pass
        if not worker.isRunning():
            return
        if QThread.currentThread() is worker:
            return
        if not worker.wait(wait_ms):
            worker.terminate()
            worker.wait(3000)

    def _allow_tools_for_followup(self, allow_more_tools: bool) -> bool:
        if self._web_fetch_dedupe_streak >= self._web_fetch_dedupe_disable_at:
            if allow_more_tools:
                print(
                    f"[TOOL] web_fetch_url: dedupe streak ≥{self._web_fetch_dedupe_disable_at} — disabling tools next pass so the model answers in text"
                )
            return False
        if self._web_fetch_success_this_turn >= self.MAX_WEB_FETCH_SUCCESS_PER_TURN:
            if allow_more_tools:
                print(
                    f"[TOOL] web_fetch_url: successful fetch limit reached ({self.MAX_WEB_FETCH_SUCCESS_PER_TURN}) — disabling tools next pass"
                )
            return False
        return allow_more_tools

    @staticmethod
    def _tool_policy_name_tuple(val) -> tuple[str, ...]:
        """Normalize x_lira_forbidden_if_last_tool: tuple[str] from tuple/list/single str."""
        if val is None:
            return ()
        if isinstance(val, str):
            return (val,) if val else ()
        if isinstance(val, (list, tuple)):
            out: list[str] = []
            for x in val:
                if isinstance(x, str) and x:
                    out.append(x)
            return tuple(out)
        return ()

    @staticmethod
    def _tool_policy_step_tuple(val) -> tuple[int, ...]:
        """Normalize x_lira_only_at_chain_steps: positive ints."""
        if val is None:
            return ()
        if isinstance(val, (list, tuple)):
            out: list[int] = []
            for x in val:
                try:
                    n = int(x)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    out.append(n)
            return tuple(out)
        try:
            n = int(val)
        except (TypeError, ValueError):
            return ()
        return (n,) if n > 0 else ()

    @staticmethod
    def _unlink_qwen_edit_staging_files(gen_kwargs: dict) -> None:
        """Remove staging input files under data/media (only within that tree)."""
        root = os.path.realpath(str(lira_data("media")))
        for key in ("source_image_path", "source_image_path_2"):
            p = gen_kwargs.get(key)
            if not p or not isinstance(p, str):
                continue
            try:
                ap = os.path.realpath(os.path.expanduser(p))
                if ap != root and not ap.startswith(root + os.sep):
                    continue
                if os.path.isfile(ap):
                    os.unlink(ap)
            except OSError:
                pass

    def _tool_chain_policy_refusal(self, fn_name: str) -> str | None:
        """
        Single chain-policy check from schema metadata.
        Returns refusal text for role=tool or None if the call is allowed.
        """
        entry = self._tool_schema_by_name.get(fn_name)
        if not entry:
            return None

        allowed_steps = self._tool_policy_step_tuple(entry.get(TOOL_ONLY_AT_CHAIN_STEPS_KEY))
        if allowed_steps and self._tool_chain_count not in allowed_steps:
            steps_s = ", ".join(str(s) for s in sorted(set(allowed_steps)))
            return self._tr_tools(
                "chat.tool_refusal.chain_step",
                fn_name=fn_name,
                steps=steps_s,
                step=self._tool_chain_count,
            )

        forbidden_after = self._tool_policy_name_tuple(entry.get(TOOL_FORBIDDEN_IF_LAST_TOOL_KEY))
        last = self._last_tool_name
        if forbidden_after and last in forbidden_after:
            hint_tr = tool_forbidden_if_last_hint(fn_name, getattr(self, "_tools_locale", "ru"))
            hint_s = f" {hint_tr}" if hint_tr and not hint_tr.startswith("policies.tools.") else ""
            return self._tr_tools(
                "chat.tool_refusal.after_last",
                fn_name=fn_name,
                last=last,
                hint=hint_s,
            )

        return None

    def _orphan_web_fetch_refusal(self, fn_name: str) -> str | None:
        if fn_name != "web_fetch_url":
            return None
        entry = self._tool_schema_by_name.get("web_fetch_url")
        return orphan_web_fetch_refusal(
            entry,
            self._web_research_touched_this_turn,
            locale=getattr(self, "_tools_locale", "ru"),
        )

    def _limbic_for_worker(self) -> tuple[str | None, str]:
        from infrastructure.limbic.assets import model_limbic_prompt_enabled

        m_info = self.window.model_controller.get_active_model_info()
        if not model_limbic_prompt_enabled(m_info):
            return None, ""
        return (
            render_limbic_prompt(
                self.limbic_state,
                locale=self.window.config_repo.get_ui_locale(),
                format_vars=self.window.config_repo.get_runtime_format_vars(m_info),
            ),
            format_emotion_vector(self.limbic_state.snapshot()),
        )

    def _sens_append_for_worker(self) -> str | None:
        from infrastructure.external_events.world_state import get_world_state
        from infrastructure.lifecycle.perception_daemon import get_perception_daemon

        parts: list[str] = []
        absence = get_perception_daemon(self.window).consume_absence_summary()
        if absence:
            parts.append(absence)
        world_bit = get_world_state(self.window).format_for_sens()
        if world_bit:
            parts.append(world_bit)
        joined = " ".join(parts).strip()
        return joined or None

    def _llm_inference_busy(self) -> bool:
        """Any llm decode/create_chat_completion (single GPU context)."""
        if self._gallery_describe_busy:
            return True
        if self.worker is not None and self.worker.isRunning():
            return True
        if self._proactive_worker is not None and self._proactive_worker.isRunning():
            return True
        if self._perception_eval_worker is not None and self._perception_eval_worker.isRunning():
            return True
        proc = self._gallery_describe_process
        if proc is not None and proc.is_running():
            return True
        return False

    def proactive_block_reason(self) -> str | None:
        from infrastructure.lifecycle.perception_daemon import get_perception_daemon
        from infrastructure.limbic.assets import model_perception_daemon_enabled

        m_info = self.window.model_controller.get_active_model_info()
        if not model_perception_daemon_enabled(m_info):
            return "perception_off"
        if not get_perception_daemon(self.window).is_running():
            return "daemon_stopped"
        gate = self.window.activity_gate
        if gate._user_typing:
            return "user_typing"
        if self.worker is not None and self.worker.isRunning():
            return "main_worker"
        if self._llm_inference_busy():
            return "llm_busy"
        if self._gallery_describe_busy:
            return "gallery_describe"
        if self._image_gen_busy() or self._qwen_edit_main_busy:
            return "image_gen"
        if self.window.model_controller.llm is None:
            return "llm_unloaded"
        return None

    def can_run_proactive(self) -> bool:
        return self.proactive_block_reason() is None

    def perception_eval_block_reason(self) -> str | None:
        """WorldState eval: not while typing or chat reply (no 5 min wait)."""
        from infrastructure.lifecycle.perception_daemon import get_perception_daemon
        from infrastructure.limbic.assets import model_perception_daemon_enabled

        m_info = self.window.model_controller.get_active_model_info()
        if not model_perception_daemon_enabled(m_info):
            return "perception_off"
        if not get_perception_daemon(self.window).is_running():
            return "daemon_stopped"
        gate = self.window.activity_gate
        if gate.is_dialog_busy():
            return "dialog_busy"
        if self._llm_inference_busy():
            return "llm_busy"
        if self._perception_eval_worker is not None and self._perception_eval_worker.isRunning():
            return "perception_eval"
        if self.window.model_controller.llm is None:
            return "llm_unloaded"
        return None

    def _perception_life_system_content(self) -> str:
        m_info = self.window.model_controller.get_active_model_info()
        return self.window.config_repo.get_persona_text(m_info, "perception_eval_system")

    def _perception_eval_settings(self) -> dict:
        import json
        from pathlib import Path

        from tools.notify_andrey import TELEGRAM_LIFE_EVAL_TOOL

        m_info = self.window.model_controller.get_active_model_info()
        settings = dict(m_info.settings or {})
        path = lira_root() / "config.perception_rules.json"
        max_t = 160
        temp = 0.2
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                max_t = max(64, int(data.get("perception_eval_max_tokens", max_t)))
                temp = float(data.get("perception_eval_temperature", temp))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
        settings["max_tokens"] = max_t
        settings["temperature"] = temp
        settings["tool_choice"] = {
            "type": "function",
            "function": {"name": TELEGRAM_LIFE_EVAL_TOOL},
        }
        return settings

    def run_perception_evaluation(self) -> bool:
        if self.perception_eval_block_reason() is not None:
            return False
        mc = self.window.model_controller
        if mc.llm is None:
            return False
        from infrastructure.external_events.world_state import (
            TELEGRAM_LAST_MESSAGE,
            get_world_state,
        )
        from infrastructure.lifecycle.perception_eval import (
            build_evaluation_user_prompt,
            perception_life_tools,
        )

        event = get_world_state(self.window).get(TELEGRAM_LAST_MESSAGE)
        user_prompt = build_evaluation_user_prompt(
            event,
            self._tools_locale,
            format_vars=self.window.config_repo.get_runtime_format_vars(),
        )
        _pm = var_list("detection.proactive_user_markers", self._tools_locale)
        if any(m in user_prompt for m in _pm):
            return False
        self._retire_perception_eval_worker()
        messages = [
            {"role": "system", "content": self._perception_life_system_content()},
            {"role": "user", "content": user_prompt},
        ]
        self._perception_eval_worker = ModelWorker(
            mc.llm,
            messages,
            self._perception_eval_settings(),
            tools=perception_life_tools(
                self._tools_locale,
                format_vars=self.window.config_repo.get_runtime_format_vars(),
            ),
            limbic_content=None,
            limbic_state_summary="",
            sens_append_suffix=None,
        )
        self._perception_eval_worker.finished_answer.connect(
            self._on_perception_eval_answer,
            Qt.ConnectionType.QueuedConnection,
        )
        self.window.show_thinking_indicator(self._tr_ui("Reacting to event…"))
        self._perception_eval_worker.start()
        return True

    def _on_perception_eval_answer(self, full_response: str) -> None:
        from infrastructure.lifecycle.perception_daemon import get_perception_daemon
        from infrastructure.lifecycle.perception_eval import verdict_from_model_answer

        self.window.hide_thinking_indicator()
        self._retire_perception_eval_worker()
        if (full_response or "").startswith(
            var_get("detection.error_response_prefix", self._tools_locale, default="Error")
        ):
            return
        verdict = verdict_from_model_answer(full_response)
        get_perception_daemon(self.window).apply_perception_verdict(verdict)

    def _retire_perception_eval_worker(self) -> None:
        worker = self._perception_eval_worker
        self._perception_eval_worker = None
        if worker is None:
            return
        try:
            worker.finished_answer.disconnect(self._on_perception_eval_answer)
        except (TypeError, RuntimeError):
            pass
        if worker.isRunning():
            worker.cancel_requested = True
            worker.wait(60_000)

    def cancel_perception_evaluation(self) -> None:
        self._retire_perception_eval_worker()

    def _proactive_no_tools_fallback(self) -> str:
        return self._tr_ui("Accepted Telegram message. Replying without tools — send again if you need more.")

    def _proactive_max_tokens(self) -> int:
        import json
        import os
        from pathlib import Path

        path = lira_root() / "config.perception_rules.json"
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                return max(64, int(data.get("proactive_max_tokens", 280)))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
        return 280

    def _proactive_system_content(self) -> str:
        if getattr(self, "_proactive_is_telegram", False):
            m_info = self.window.model_controller.get_active_model_info()
            parts = [self.window.config_repo.get_persona_text(m_info, "telegram_bot_reply")]
        else:
            m_info = self.window.model_controller.get_active_model_info()
            parts = []
            persona = self.window.config_repo.get_persona_prompt(m_info)
            if persona:
                parts.append(persona)
        user_line = (self._proactive_user_prompt or "").strip()
        if user_line:
            parts.append(self._var("chat.event_prefix") + user_line)
        return "\n\n".join(parts)

    def _proactive_allow_tools(self) -> bool:
        import json
        import os
        from pathlib import Path

        path = lira_root() / "config.perception_rules.json"
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                return bool(data.get("proactive_allow_tools", False))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
        return False

    def run_proactive(self, job) -> bool:
        if not self.can_run_proactive():
            return False

        mc = self.window.model_controller
        m_info = mc.get_active_model_info()
        sc = self.window.session_controller
        sc.ensure_session()

        self.window.interrupt_voice()

        user_line = (job.user_prompt or "").strip()
        self._proactive_is_telegram = job.rule_id == "telegram_incoming"
        self._proactive_turn_active = True
        self._proactive_user_prompt = user_line
        self.current_user_text = user_line
        self.is_first_model_token = True
        self._tool_chain_count = 0
        self._web_search_cache_this_turn = {}
        self._web_fetch_urls_ok_this_turn = set()
        self._web_fetch_success_this_turn = 0
        self._web_fetch_dedupe_streak = 0
        self._web_research_touched_this_turn = False
        self._last_tool_name = None
        self._tool_history_truncated_this_turn = False

        system_content = self._proactive_system_content()
        context_history = self.context_manager.build_proactive_context(system_content, user_line)
        self._print_ctx_line(context_history, 0)

        if self._proactive_is_telegram:
            limbic_content, limbic_state_summary = None, ""
            sens_suffix = None
        else:
            limbic_content, limbic_state_summary = self._limbic_for_worker()
            sens_suffix = self._sens_append_for_worker()
        self._retire_proactive_worker()

        settings = dict(m_info.settings or {})
        settings["max_tokens"] = self._proactive_max_tokens()
        tools = (
            self._stripped_tools_for_menu(self.debug_tool_schema)
            if self._proactive_allow_tools() and not self._proactive_is_telegram
            else None
        )

        self._proactive_worker = ModelWorker(
            mc.llm,
            context_history,
            settings,
            tools=tools,
            limbic_content=limbic_content,
            limbic_state_summary=limbic_state_summary,
            sens_append_suffix=sens_suffix,
        )
        self._proactive_worker.finished_answer.connect(
            self.window.finalize_answer,
            Qt.ConnectionType.QueuedConnection,
        )
        self.window.show_thinking_indicator(self._tr_ui("Telegram…"))
        print(
            f"[PROACTIVE] run rule={job.rule_id!r} session={sc.current_session_id}",
            flush=True,
        )
        self._proactive_worker.start()
        return True

    def _retire_proactive_worker(self) -> None:
        worker = self._proactive_worker
        self._proactive_worker = None
        if worker is None:
            return
        try:
            worker.finished_answer.disconnect(self.window.finalize_answer)
        except (TypeError, RuntimeError):
            pass
        if worker.isRunning():
            worker.wait(120_000)

    def _finalize_proactive_telegram_reply(self, full_response: str) -> None:
        """Reply in Telegram only — no UI chat, history, or TTS."""
        from infrastructure.lifecycle.perception_daemon import get_perception_daemon

        self.window.hide_thinking_indicator()
        text = strip_degenerate_token_runs(
            strip_leading_channel_thought_preamble(strip_leading_tool_results_echo(full_response))
        )
        if not text.strip():
            text = self._tr_ui("Noticed a Telegram message.")
        self._proactive_turn_active = False
        self._proactive_is_telegram = False
        self._proactive_user_prompt = ""
        self._tool_chain_count = 0
        self._web_fetch_dedupe_streak = 0
        self._web_fetch_success_this_turn = 0
        self._web_research_touched_this_turn = False
        self._last_tool_name = None
        self._tool_history_truncated_this_turn = False
        self.last_pair = None
        self._retire_proactive_worker()
        print(f"[PROACTIVE] telegram-only len={len(text)}", flush=True)
        daemon = get_perception_daemon(self.window)
        daemon.deliver_telegram_reply(text)
        daemon.clear_active_proactive_job()
        daemon._try_dispatch_proactive()
        self._log_model_emotion_probe(text)

    def cancel_proactive(self) -> None:
        self._proactive_turn_active = False
        self._proactive_user_prompt = ""
        self._retire_proactive_worker()
        from infrastructure.lifecycle.perception_daemon import get_perception_daemon

        get_perception_daemon(self.window).clear_active_proactive_job()

    def shutdown_for_close(self, *, worker_wait_ms: int = 8000, image_wait_ms: int = 5000) -> None:
        """Stop chat background workers before app exit."""
        self.cancel_proactive()
        self.cancel_perception_evaluation()
        self.cancel_gallery_description_refresh()
        self.cancel_pending_qwen_edit()
        worker = self.worker
        if worker is not None:
            worker.cancel_requested = True
            self._retire_model_worker(worker, wait_ms=worker_wait_ms)
            self.worker = None
            self._set_model_worker_busy(False)
        image_worker = self._image_gen_worker
        if image_worker is not None and image_worker.isRunning():
            image_worker.wait(image_wait_ms)
        proc = self._gallery_describe_process
        if proc is not None:
            proc.cancel()

    def _gallery_describe_intro(self, m_info) -> str:
        return self.window.config_repo.get_persona_text(m_info, "gallery_describe_intro")

    def _emit_gallery_describe_event(self, event: dict) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        bridge = self.window.bridge
        if hasattr(bridge, "gallery_describe_event"):
            bridge.gallery_describe_event.emit(payload)

    def start_gallery_description_refresh(self, mode: str = "missing") -> dict:
        if self._gallery_describe_busy:
            return {"ok": False, "error": self._tr_ui("Gallery description refresh already running.")}
        if self._llm_inference_busy():
            return {"ok": False, "error": self._tr_ui("Wait for the chat reply to finish.")}
        if self._image_gen_or_qwen_main_busy():
            return {"ok": False, "error": self._tr_ui("Wait for image generation to finish.")}

        repo = self.window.gallery_repo
        repair = (mode or "missing").strip().lower() == "repair"
        if repair:
            for gen_id, _path in repo.list_generations_bad_description():
                repo.clear_generation_description(gen_id)
            items = repo.list_generations_for_description_repair()
            empty_msg = self._tr_ui("No empty or broken descriptions to fix.")
        else:
            items = repo.list_generations_missing_description()
            empty_msg = self._tr_ui("All images already have descriptions.")

        if not items:
            return {"ok": True, "total": 0, "message": empty_msg, "mode": mode}

        if not self._begin_gallery_describe_run(items, mode=mode, silent=False):
            return {
                "ok": False,
                "error": self._tr_ui("A multimodal vision model must be loaded (clip) — switch model in chat."),
            }
        return {"ok": True, "total": self._gallery_describe_total, "mode": mode}

    def _gallery_describe_settings(self) -> dict:
        return load_gallery_describe_settings()

    def _can_describe_gallery_in_process(self) -> bool:
        """Active vision model already loaded — no unload or subprocess."""
        gd = self._gallery_describe_settings()
        if gd.get("use_subprocess") is True:
            return False
        mc = self.window.model_controller
        if gd.get("force_subprocess_after_switch", True) and getattr(mc, "_gallery_describe_subprocess_guard", False):
            return False
        active = self.window.model_controller.get_active_model_info()
        mc = self.window.model_controller
        if active is None or not mc.llm or not self._model_has_vision(mc, active):
            return False
        if self._image_gen_or_qwen_main_busy():
            return False
        return True

    def _active_gallery_describe_model_info(self):
        """Chat active model only; separate vision_model_id is not used."""
        active = self.window.model_controller.get_active_model_info()
        if active is None:
            return None
        mc = self.window.model_controller
        if not self._model_has_vision(mc, active):
            return None
        return active

    def _begin_gallery_describe_run(
        self,
        items: list[tuple[int, str]],
        *,
        mode: str,
        silent: bool,
    ) -> bool:
        if not items:
            return False
        m_info = self._active_gallery_describe_model_info()
        if m_info is None:
            print("[GALLERY] describe: active model has no vision (clip)", flush=True)
            return False

        self._gallery_describe_vision_m_info = m_info
        self._gallery_describe_cancel = False
        self._gallery_describe_total = len(items)
        self._gallery_describe_done = 0
        self._gallery_describe_busy = True
        self._gallery_describe_mode = mode
        self._gallery_describe_silent = silent
        self._gallery_describe_saved_count = 0
        self._gallery_describe_cuda_retries = 0
        gd = self._gallery_describe_settings()
        mc = self.window.model_controller
        self._gallery_describe_use_subprocess = not self._can_describe_gallery_in_process()
        self._gallery_describe_gpu_handoff = self._gallery_describe_use_subprocess and bool(gd.get("gpu_handoff", True))
        if (
            self._gallery_describe_use_subprocess
            and gd.get("force_subprocess_after_switch", True)
            and getattr(mc, "_gallery_describe_subprocess_guard", False)
        ):
            self._gallery_describe_gpu_handoff = True
        self._gallery_describe_stored_items = list(items)
        self._gallery_describe_defer_vectors = len(items) > 1
        self._gallery_describe_vector_pending = []
        self._gallery_redescribe_pass = False
        self._gallery_describe_redescribe_queue = None

        self._gallery_describe_perception_was_running = False
        if self._gallery_describe_use_subprocess:
            from infrastructure.lifecycle.perception_daemon import get_perception_daemon

            daemon = get_perception_daemon(self.window)
            self._gallery_describe_perception_was_running = daemon.is_running()
            if self._gallery_describe_perception_was_running:
                daemon.stop()

        mode_label = "subprocess" if self._gallery_describe_use_subprocess else "in-process"
        post_switch_guard = (
            self._gallery_describe_use_subprocess
            and gd.get("force_subprocess_after_switch", True)
            and getattr(mc, "_gallery_describe_subprocess_guard", False)
        )
        print(
            f"[GALLERY] describe start mode={mode!r} {mode_label} "
            f"items={len(items)} vision={m_info.name!r}"
            f"{' handoff' if self._gallery_describe_gpu_handoff else ''}"
            f"{' post_switch' if post_switch_guard else ''}",
            flush=True,
        )
        self._emit_gallery_describe_event(
            {
                "type": "started",
                "total": self._gallery_describe_total,
                "done": 0,
                "mode": mode,
                "subprocess": self._gallery_describe_use_subprocess,
                "gpu_handoff": self._gallery_describe_gpu_handoff,
                "silent": silent,
            }
        )
        if self._gallery_describe_use_subprocess:
            if self._gallery_describe_gpu_handoff:
                if not silent:
                    self._emit_gallery_describe_event(
                        {
                            "type": "handoff",
                            "phase": "release",
                            "total": self._gallery_describe_total,
                        }
                    )
                QTimer.singleShot(0, self._gallery_gpu_handoff_then_subprocess)
            else:
                self._run_next_gallery_subprocess_chunk(m_info)
        else:
            QTimer.singleShot(0, self._run_next_gallery_inprocess_item)
        return True

    def _gallery_describe_vision_info(self):
        return getattr(self, "_gallery_describe_vision_m_info", None) or (
            self.window.model_controller.get_active_model_info()
        )

    def _apply_gallery_describe_item(self, gen_id: int, path: str, description: str, *, skipped: bool = False) -> None:
        if skipped or not path or not os.path.isfile(path):
            self._gallery_describe_done += 1
            self._emit_gallery_describe_progress(gen_id, path, skipped=True)
            return
        desc = sanitize_gallery_description(description or "")
        if desc and is_bad_gallery_description(desc, self._gallery_description_locale()):
            print(
                f"[GALLERY] rejected description id={gen_id}: {desc[:120]!r}",
                flush=True,
            )
            self.window.gallery_repo.clear_generation_description(gen_id)
            desc = ""
        elif desc:
            if self.window.gallery_repo.set_generation_description(
                gen_id,
                desc,
                semantic_engine=self.semantic_engine,
                skip_vector=self._gallery_describe_defer_vectors,
                locale=self._gallery_description_locale(),
            ):
                self._gallery_describe_saved_count += 1
                if self._gallery_describe_defer_vectors:
                    self._gallery_describe_vector_pending.append((gen_id, desc))
                print(
                    f"[GALLERY] saved description id={gen_id} len={len(desc)}",
                    flush=True,
                )
            else:
                print(f"[GALLERY] DB update failed id={gen_id}", flush=True)
        else:
            print(
                f"[GALLERY] empty description id={gen_id} path={path!r}",
                flush=True,
            )
        self._gallery_describe_done += 1
        self._emit_gallery_describe_progress(gen_id, path, description=desc)

    def _describe_gallery_frame(self, file_path: str) -> str:
        """Vision caption for one frame on already-loaded Lira (GUI thread)."""
        from infrastructure.runtime.llm_cuda_hygiene import release_llm_cuda_cache

        m_info = self._gallery_describe_vision_info()
        if m_info is None:
            return ""

        mc = self.window.model_controller
        expanded = os.path.expanduser(file_path)
        if not os.path.isfile(expanded) or not mc.llm:
            return ""
        handler = getattr(mc.llm, "chat_handler", None)
        if handler is None:
            return ""

        vision_content = [{"type": "text", "text": self._gallery_describe_intro(m_info)}]
        try:
            with open(expanded, "rb") as f:
                raw_b64 = base64.b64encode(f.read()).decode()
            _, final_b64 = self.process_incoming_image(raw_b64)
            vision_content.append({"type": "image_url", "image_url": {"url": final_b64}})
        except Exception as exc:
            print(f"[GALLERY] image read failed {expanded!r}: {exc!r}", flush=True)
            return ""

        try:
            if hasattr(mc.llm, "reset"):
                mc.llm.reset()
        except Exception:
            pass

        try:
            response = handler(
                llama=mc.llm,
                messages=[
                    {
                        "role": "system",
                        "content": self.window.config_repo.get_persona_text(m_info, "gallery_describe_system"),
                    },
                    {"role": "user", "content": vision_content},
                ],
                max_tokens=220,
                temperature=0.45,
                stream=False,
            )
            raw = response["choices"][0]["message"].get("content") or ""
            text = sanitize_gallery_description(str(raw))
            if text and is_bad_gallery_description(text, self._gallery_description_locale()):
                return ""
            return text
        except Exception as exc:
            print(f"[GALLERY] in-process vision failed: {exc!r}", flush=True)
            traceback.print_exc()
            return ""
        finally:
            release_llm_cuda_cache(mc.llm, deep=True)

    def _run_next_gallery_inprocess_item(self) -> None:
        if self._gallery_describe_cancel or not self._gallery_describe_busy:
            self._gallery_describe_complete(cancelled=True)
            return
        remaining = self._gallery_describe_stored_items[self._gallery_describe_done :]
        if not remaining:
            m_info = self._gallery_describe_vision_info()
            if self._try_start_wrong_locale_lead_redescribe(m_info):
                return
            self._gallery_describe_complete(cancelled=False)
            return

        gen_id, path = remaining[0]
        print(
            f"[GALLERY] in-process {self._gallery_describe_done + 1}/{self._gallery_describe_total} id={gen_id}",
            flush=True,
        )
        desc = self._describe_gallery_frame(path)
        self._apply_gallery_describe_item(gen_id, path, desc)
        QTimer.singleShot(80, self._run_next_gallery_inprocess_item)

    def _subprocess_n_gpu_layers(self, m_info, gd: dict) -> int:
        if "subprocess_n_gpu_layers" in gd:
            return int(gd["subprocess_n_gpu_layers"])
        settings = m_info.settings or {}
        return int(settings.get("n_gpu_layers", -1))

    def _subprocess_n_ctx(self, m_info, gd: dict) -> int:
        """Smaller n_ctx than chat: one vision pass — else SWA KV eats VRAM."""
        model_n_ctx = int((m_info.settings or {}).get("n_ctx", 8192))
        want = int(gd.get("subprocess_n_ctx", 4096))
        return min(max(want, 2048), model_n_ctx)

    def _gallery_gpu_handoff_then_subprocess(self) -> None:
        if self._gallery_describe_cancel or not self._gallery_describe_busy:
            self._gallery_describe_complete(cancelled=True)
            return
        from infrastructure.runtime.llm_cuda_hygiene import _empty_torch_cuda_cache

        self.cancel_proactive()
        mc = self.window.model_controller
        mc._stop_loader_thread()
        mc._free_llm_on_gui_thread()
        _empty_torch_cuda_cache()
        _empty_torch_cuda_cache()
        QTimer.singleShot(450, self._gallery_start_subprocess_after_handoff)

    def _gallery_start_subprocess_after_handoff(self) -> None:
        if self._gallery_describe_cancel or not self._gallery_describe_busy:
            self._gallery_describe_complete(cancelled=True)
            return
        m_info = self._gallery_describe_vision_info()
        self._run_next_gallery_subprocess_chunk(m_info)

    def _run_next_gallery_subprocess_chunk(self, m_info) -> None:
        if self._gallery_describe_cancel or not self._gallery_describe_busy:
            self._gallery_describe_finish_gpu_handoff(cancelled=True)
            return
        if self._gallery_describe_redescribe_queue is not None:
            remaining = self._gallery_describe_redescribe_queue
        else:
            remaining = self._gallery_describe_stored_items[self._gallery_describe_done :]
        if not remaining:
            if self._try_start_wrong_locale_lead_redescribe(m_info):
                return
            self._gallery_describe_finish_gpu_handoff(cancelled=False)
            return
        gd = self._gallery_describe_settings()
        chunk_size = max(1, int(gd.get("subprocess_chunk_size", 8)))
        chunk = remaining[:chunk_size]
        if self._gallery_describe_redescribe_queue is not None:
            self._gallery_describe_redescribe_queue = remaining[chunk_size:]
        n_gpu = self._subprocess_n_gpu_layers(m_info, gd)
        print(
            f"[GALLERY] subprocess chunk: {len(chunk)} items "
            f"({self._gallery_describe_done}/{self._gallery_describe_total} done) "
            f"n_ctx={self._subprocess_n_ctx(m_info, gd)} n_gpu_layers={n_gpu}"
            f"{' [redescribe]' if self._gallery_redescribe_pass else ''}",
            flush=True,
        )
        self._start_gallery_describe_subprocess(chunk, m_info)

    def _purge_wrong_locale_lead_from_session(self) -> list[tuple[int, str]]:
        """Reset descriptions with wrong language prefix / tags this session."""
        repo = self.window.gallery_repo
        loc = self._gallery_description_locale()
        requeue: list[tuple[int, str]] = []
        for gen_id, path in self._gallery_describe_stored_items:
            desc = repo.get_generation_description(int(gen_id))
            if not desc:
                continue
            if not should_redescribe_gallery_lead(desc, loc):
                continue
            repo.clear_generation_description(int(gen_id))
            requeue.append((int(gen_id), path))
            self._gallery_describe_vector_pending = [
                (i, d) for i, d in self._gallery_describe_vector_pending if i != int(gen_id)
            ]
            print(
                f"[GALLERY] cleared wrong-locale/tag prefix id={gen_id} ui_locale={loc}: {desc[:80]!r}",
                flush=True,
            )
        return requeue

    def _try_start_wrong_locale_lead_redescribe(self, m_info) -> bool:
        if self._gallery_redescribe_pass or self._gallery_describe_cancel:
            return False
        requeue = self._purge_wrong_locale_lead_from_session()
        if not requeue:
            return False
        self._gallery_redescribe_pass = True
        self._gallery_describe_redescribe_queue = list(requeue)
        self._gallery_describe_total += len(requeue)
        self._emit_gallery_describe_event(
            {
                "type": "redescribe",
                "count": len(requeue),
                "total": self._gallery_describe_total,
                "done": self._gallery_describe_done,
            }
        )
        print(
            f"[GALLERY] redescribe {len(requeue)} items "
            f"(wrong locale for ui_locale={self._gallery_description_locale()})",
            flush=True,
        )
        QTimer.singleShot(300, lambda: self._run_next_gallery_subprocess_chunk(m_info))
        return True

    def _start_gallery_describe_subprocess(self, items, m_info) -> None:
        import tempfile

        gd = self._gallery_describe_settings()
        job = {
            "model_path": m_info.model_path,
            "clip_model_path": m_info.clip_model_path,
            "model_type": m_info.model_type,
            "template_path": m_info.template_path,
            "n_ctx": self._subprocess_n_ctx(m_info, gd),
            "n_gpu_layers": self._subprocess_n_gpu_layers(m_info, gd),
            "max_side": int(gd.get("max_side", 384)),
            "max_tokens": int(gd.get("max_tokens", 220)),
            "intro": self._gallery_describe_intro(m_info),
            "ui_locale": self._gallery_description_locale(),
            "persona_file": m_info.persona_file,
            "cuda_deep_every": max(1, int(gd.get("cuda_deep_every", 1))),
            "items": [{"id": int(gen_id), "path": file_path} for gen_id, file_path in items],
        }
        fd, job_path = tempfile.mkstemp(suffix=".json", prefix="lira_gd_job_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(job, f, ensure_ascii=False)
        except Exception:
            os.close(fd)
            raise
        self._gallery_describe_job_path = job_path

        proc = GalleryDescribeProcess(self.window)
        proc.json_line.connect(
            self._on_gallery_subprocess_event,
            Qt.ConnectionType.QueuedConnection,
        )
        proc.failed.connect(
            self._on_gallery_subprocess_failed,
            Qt.ConnectionType.QueuedConnection,
        )
        proc.finished_ok.connect(
            self._on_gallery_subprocess_chunk_ok,
            Qt.ConnectionType.QueuedConnection,
        )
        self._gallery_describe_process = proc
        proc.start(job_path)

    def _on_gallery_subprocess_event(self, data: dict) -> None:
        ev_type = data.get("type")
        if ev_type == "item":
            gen_id = int(data.get("id", 0))
            path = data.get("path") or ""
            self._apply_gallery_describe_item(
                gen_id,
                path,
                data.get("description") or "",
                skipped=bool(data.get("skipped")),
            )
        elif ev_type == "error":
            print(f"[GALLERY] subprocess: {data.get('message')}", flush=True)
        elif ev_type in ("started", "done"):
            pass

    def _cleanup_gallery_subprocess_proc(self) -> None:
        proc = self._gallery_describe_process
        if proc is not None:
            proc.cancel()
        self._gallery_describe_process = None
        job_path = self._gallery_describe_job_path
        self._gallery_describe_job_path = None
        if job_path and os.path.isfile(job_path):
            try:
                os.unlink(job_path)
            except OSError:
                pass

    def _on_gallery_subprocess_chunk_ok(self) -> None:
        self._cleanup_gallery_subprocess_proc()
        if self._gallery_describe_cancel:
            self._gallery_describe_finish_gpu_handoff(cancelled=True)
            return
        if self._gallery_describe_redescribe_queue is not None:
            if self._gallery_describe_redescribe_queue:
                from infrastructure.runtime.llm_cuda_hygiene import _empty_torch_cuda_cache

                _empty_torch_cuda_cache()
                QTimer.singleShot(
                    400,
                    lambda: self._run_next_gallery_subprocess_chunk(self._gallery_describe_vision_info()),
                )
                return
            self._gallery_describe_redescribe_queue = None
        else:
            remaining = self._gallery_describe_stored_items[self._gallery_describe_done :]
            if remaining:
                from infrastructure.runtime.llm_cuda_hygiene import _empty_torch_cuda_cache

                _empty_torch_cuda_cache()
                QTimer.singleShot(
                    400,
                    lambda: self._run_next_gallery_subprocess_chunk(self._gallery_describe_vision_info()),
                )
                return
            if self._try_start_wrong_locale_lead_redescribe(self._gallery_describe_vision_info()):
                return
        self._gallery_describe_finish_gpu_handoff(cancelled=False)

    def _on_gallery_subprocess_failed(self, message: str) -> None:
        print(f"[GALLERY] subprocess failed: {message}", flush=True)
        self._cleanup_gallery_subprocess_proc()
        if self._gallery_describe_cancel:
            self._gallery_describe_finish_gpu_handoff(cancelled=True)
            return

        cuda_like = any(
            k in (message or "").lower()
            for k in var_list("detection.gallery_cuda_ram_keys", self._gallery_description_locale())
        )
        remaining = self._gallery_describe_stored_items[self._gallery_describe_done :]

        if remaining and cuda_like and self._gallery_describe_cuda_retries < 1:
            self._gallery_describe_cuda_retries += 1
            print(
                f"[GALLERY] CUDA/RAM — cache reset and GPU retry, remaining {len(remaining)}",
                flush=True,
            )
            from infrastructure.runtime.llm_cuda_hygiene import _empty_torch_cuda_cache

            _empty_torch_cuda_cache()
            _empty_torch_cuda_cache()
            QTimer.singleShot(
                2000,
                lambda: self._run_next_gallery_subprocess_chunk(self._gallery_describe_vision_info()),
            )
            return

        self.window.inject_message(
            "model",
            self._tr_ui("Gallery descriptions aborted: {message}", message=message),
        )
        self._gallery_describe_finish_gpu_handoff(cancelled=True)

    def _gallery_describe_finish_gpu_handoff(self, *, cancelled: bool) -> None:
        if self._gallery_describe_gpu_handoff and self.window.model_controller.llm is None:
            self._emit_gallery_describe_event({"type": "handoff", "phase": "restore"})
            self.window.model_controller.reload_after_gallery_handoff(
                when_ready=lambda: self._gallery_describe_complete(cancelled=cancelled)
            )
            return
        self._gallery_describe_complete(cancelled=cancelled)

    def on_gallery_handoff_reload_failed(self, message: str) -> None:
        print(f"[GALLERY] chat reload failed: {message}", flush=True)
        self.window.inject_message(
            "model",
            self._tr_ui(
                "Descriptions done but could not restore chat model: {message}. Restart {app_name} or switch model in the menu.",
                message=message,
                **self.window.config_repo.get_runtime_format_vars(),
            ),
        )
        self._gallery_describe_complete(cancelled=False)

    def cancel_gallery_description_refresh(self) -> None:
        if not self._gallery_describe_busy:
            return
        self._gallery_describe_cancel = True
        proc = self._gallery_describe_process
        if proc is not None:
            proc.cancel()

    def _gallery_describe_complete(self, *, cancelled: bool = False) -> None:
        defer = self._gallery_describe_defer_vectors
        self.window.model_controller.clear_gallery_describe_subprocess_guard()
        self._gallery_describe_busy = False
        self._gallery_describe_cancel = False
        self._gallery_describe_stored_items = []
        self._gallery_describe_gpu_handoff = False
        self._gallery_describe_defer_vectors = False
        self._gallery_redescribe_pass = False
        self._gallery_describe_redescribe_queue = None
        silent = self._gallery_describe_silent
        self._gallery_describe_silent = False
        if defer and not cancelled:
            self._reindex_gallery_descriptions_vectors()
        if self._gallery_describe_perception_was_running:
            from infrastructure.lifecycle.perception_daemon import get_perception_daemon

            get_perception_daemon(self.window).start()
            self._gallery_describe_perception_was_running = False
        missing_left = self.window.gallery_repo.count_generations_missing_description()
        saved = int(getattr(self, "_gallery_describe_saved_count", 0))
        print(
            f"[GALLERY] complete cancelled={cancelled} saved={saved} "
            f"processed={self._gallery_describe_done}/{self._gallery_describe_total} "
            f"missing_left={missing_left}",
            flush=True,
        )
        self._emit_gallery_describe_event(
            {
                "type": "cancelled" if cancelled else "finished",
                "total": self._gallery_describe_total,
                "done": self._gallery_describe_done,
                "saved": saved,
                "missing_remaining": missing_left,
                "mode": self._gallery_describe_mode,
                "silent": silent,
            }
        )

    def _reindex_gallery_descriptions_vectors(self) -> None:
        """Vectors only for frames this session — not during subprocess."""
        repo = self.window.gallery_repo
        n = 0
        for gen_id, desc in self._gallery_describe_vector_pending:
            if repo.set_generation_description(
                int(gen_id),
                desc,
                semantic_engine=self.semantic_engine,
                skip_vector=False,
                locale=self._gallery_description_locale(),
            ):
                n += 1
        self._gallery_describe_vector_pending = []
        print(f"[GALLERY] reindexed {n} description vectors", flush=True)

    def _emit_gallery_describe_progress(
        self, gen_id: int, path: str, *, skipped: bool = False, description: str = ""
    ) -> None:
        self._emit_gallery_describe_event(
            {
                "type": "progress",
                "total": self._gallery_describe_total,
                "done": self._gallery_describe_done,
                "current_id": gen_id,
                "current_path": os.path.basename(path),
                "skipped": skipped,
                "description_len": len(description or ""),
            }
        )

    def save_generation_description(self, gen_id: int, description: str) -> dict:
        ok = self.window.gallery_repo.set_generation_description(
            int(gen_id),
            description,
            semantic_engine=self.semantic_engine,
            locale=self._gallery_description_locale(),
        )
        return {"ok": bool(ok)}

    def _log_model_emotion_probe(self, assistant_text: str) -> None:
        """BERT signal from model reply → blend + save to model DB."""
        from infrastructure.limbic.assets import model_limbic_enabled

        if not model_limbic_enabled(self.window.model_controller.get_active_model_info()):
            return
        text = (assistant_text or "").strip()
        if not text or len(text) < 3:
            return
        if text.startswith("TOOL_CALL|") or text.startswith("UI_"):
            return
        result = self.emotion_detector.analyze_text(text)
        if not result.get("ok"):
            return
        signal = result["probs"]
        self.limbic_state.blend_signal(signal)
        self.window.sync_limbic_to_db()
        self.window.notify_limbic_emotion()

    def _new_attachment_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def clear_pending_attachments(self) -> None:
        self.pending_attachments = []

    def remove_pending_attachment(self, attachment_id: str) -> bool:
        before = len(self.pending_attachments)
        self.pending_attachments = [a for a in self.pending_attachments if a.get("id") != attachment_id]
        return len(self.pending_attachments) < before

    def _log_attachment_path(self, label: str, path: str) -> None:
        p = Path(path) if path else None
        parent = p.parent if p else None
        print(
            f"[Attachment] {label}: path={path!r} "
            f"exists={p.exists() if p else False} "
            f"isfile={p.is_file() if p else False} "
            f"readable={os.access(p, os.R_OK) if p and p.exists() else False} "
            f"parent_writable={os.access(parent, os.W_OK) if parent and parent.exists() else False} "
            f"lira_root={lira_root()}",
            flush=True,
        )

    def register_image_attachment(self, base64_data: str) -> dict | None:
        hint = (base64_data or "")[:80]
        if base64_data and not (base64_data or "").strip().startswith("data:"):
            print(
                f"[Attachment] register_image_attachment input prefix={hint!r} "
                f"len={len(base64_data)} (expected data:image/... base64)",
                flush=True,
            )
        file_path, clean_b64 = self.process_incoming_image(base64_data)
        if not file_path or not clean_b64:
            print("[Attachment] register_image_attachment failed: process_incoming_image returned no file", flush=True)
            return None
        att_id = self._new_attachment_id()
        self.pending_attachments.append({"id": att_id, "kind": "image", "b64": clean_b64, "path": file_path})
        return {"id": att_id, "kind": "image", "preview": clean_b64}

    def register_image_attachment_from_path(self, file_path: str) -> dict:
        """Attach an already saved gallery frame to the next chat message."""
        raw = (file_path or "").strip()
        print(f"[Attachment] gallery attach raw={raw!r}", flush=True)
        if raw.lower().startswith("file://"):
            raw = raw[7:]
        expanded = resolve_path(raw)
        self._log_attachment_path("gallery resolved", expanded or raw)
        if not expanded or not os.path.isfile(expanded):
            print(
                f"[Attachment] gallery file not found: raw={raw!r} expanded={expanded!r}",
                flush=True,
            )
            return {"error": self._tr_ui("Image file not found.")}
        size = os.path.getsize(expanded)
        if size > MAX_ATTACHMENT_BYTES:
            mb = MAX_ATTACHMENT_BYTES // (1024 * 1024)
            print(f"[Attachment] gallery too large: {size} bytes (max {MAX_ATTACHMENT_BYTES})", flush=True)
            return {"error": self._tr_ui("Image too large (max. {mb} MB).", mb=mb)}
        ext = os.path.splitext(expanded)[1].lower()
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(ext, "image/jpeg")
        try:
            with open(expanded, "rb") as f:
                raw_b64 = base64.b64encode(f.read()).decode()
        except OSError as e:
            print(f"[Attachment] gallery read failed {expanded!r}: {e}", flush=True)
            return {"error": self._tr_ui("Could not attach image.")}
        print(f"[Attachment] gallery read ok size={size} mime={mime}", flush=True)
        data_url = f"data:{mime};base64,{raw_b64}"
        reg = self.register_image_attachment(data_url)
        if reg is None:
            print("[Attachment] gallery attach failed after read (process_incoming_image)", flush=True)
            return {"error": self._tr_ui("Could not attach image.")}
        print(f"[Attachment] gallery attach ok id={reg.get('id')}", flush=True)
        return reg

    def begin_document_upload(self, filename: str, total_size: int) -> str:
        if total_size <= 0 or total_size > MAX_ATTACHMENT_BYTES:
            return ""
        uid = self._new_attachment_id()
        self._document_upload_buffers[uid] = {
            "filename": filename or "document",
            "buf": bytearray(),
            "expected": int(total_size),
        }
        print(
            f"[Attachment] upload begin id={uid} name={filename!r} size={total_size}",
            flush=True,
        )
        return uid

    def document_upload_chunk(self, upload_id: str, chunk_b64: str) -> None:
        entry = self._document_upload_buffers.get(upload_id)
        if not entry or not chunk_b64:
            return
        if "," in chunk_b64:
            chunk_b64 = chunk_b64.split(",", 1)[1]
        try:
            entry["buf"].extend(base64.b64decode(chunk_b64, validate=True))
        except Exception as e:
            print(f"[Attachment] chunk decode failed id={upload_id}: {e}", flush=True)

    def finish_document_upload(self, upload_id: str) -> dict:
        entry = self._document_upload_buffers.pop(upload_id, None)
        if not entry:
            return {"error": self._tr_ui("Document upload not found (session reset).")}
        raw = bytes(entry["buf"])
        expected = entry["expected"]
        print(
            f"[Attachment] upload finish id={upload_id} got={len(raw)} expected={expected} head={raw[:12]!r}",
            flush=True,
        )
        if expected and abs(len(raw) - expected) > 64:
            return {
                "error": (
                    self._tr_ui(
                        "File incomplete ({got} of {expected} bytes). Try again.", got=len(raw), expected=expected
                    )
                )
            }
        return self._register_document_from_raw(entry["filename"], raw)

    def register_document_attachment(self, filename: str, file_b64: str) -> dict:
        """Single call (small files). Large PDFs via begin/chunk/finish."""
        try:
            if "," in file_b64:
                file_b64 = file_b64.split(",", 1)[1]
            raw = base64.b64decode(file_b64, validate=True)
        except Exception as e:
            return {"error": self._tr_ui("File decode error: {e}", e=e)}
        print(
            f"[Attachment] document single name={filename!r} size={len(raw)} head={raw[:12]!r}",
            flush=True,
        )
        return self._register_document_from_raw(filename, raw)

    def _append_pdf_pages_as_images(self, raw: bytes, filename: str) -> int:
        from infrastructure.attachments.pdf_render import render_pdf_pages

        try:
            pages = render_pdf_pages(raw)
        except Exception as e:
            print(f"[Attachment] pdf render failed name={filename!r}: {e}", flush=True)
            return 0
        count = 0
        for i, pil_img in enumerate(pages):
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=85)
            data_url = f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"
            stem = re.sub(r"[^\w.-]+", "_", Path(filename).stem)[:48]
            fp, b64 = self.process_incoming_image(
                data_url,
                save_as=f"pdf_{stem}_p{i + 1:02d}.jpg",
            )
            if not fp or not b64:
                continue
            self.pending_attachments.append(
                {
                    "id": self._new_attachment_id(),
                    "kind": "image",
                    "b64": b64,
                    "path": fp,
                    "source_pdf": filename,
                    "pdf_page": i + 1,
                }
            )
            count += 1
        return count

    def pending_attachments_for_ui(self) -> list[dict]:
        out = []
        for a in self.pending_attachments:
            kind = a.get("kind", "image")
            if kind == "image":
                preview = a.get("b64", "")
                name = a.get("source_pdf") or ""
                if a.get("pdf_page"):
                    name = f"{name}{self._tr_ui(' p.{page}', page=a['pdf_page'])}".strip()
            else:
                text = a.get("text", "")
                preview = text[:240] + ("…" if len(text) > 240 else "")
                name = a.get("name", "")
            out.append(
                {
                    "id": a["id"],
                    "kind": kind,
                    "name": name,
                    "preview": preview,
                }
            )
        return out

    def _register_document_from_raw(self, filename: str, raw: bytes) -> dict:
        try:
            text = extract_document_text(filename, raw)
        except Exception as e:
            print(f"[Attachment] extract failed name={filename!r}: {e}", flush=True)
            return {"error": str(e)}

        print(
            f"[Attachment] text len={len(text)} from {filename!r}",
            flush=True,
        )
        mc = self.window.model_controller
        m_info = mc.get_active_model_info()
        pages_n = 0
        pdf_vision_warning = ""
        if (filename or "").lower().endswith(".pdf") and self._model_has_vision(mc, m_info):
            pages_n = self._append_pdf_pages_as_images(raw, filename)
            print(f"[Attachment] pdf→jpeg pages={pages_n}", flush=True)
            if pages_n == 0:
                pdf_vision_warning = self._tr_ui(
                    "PDF not converted to images for vision. In Lira2 venv run: pip install pymupdf"
                )

        sc = self.window.session_controller
        s_id = sc.current_session_id
        if s_id is None:
            sc.ensure_session()
            s_id = sc.current_session_id or "default"
        safe_type = m_info.model_type.lower().replace(" ", "_").replace("-", "_")
        media_path = str(lira_data("media", f"{safe_type}-{m_info.id}", s_id))
        os.makedirs(media_path, exist_ok=True)
        safe_name = re.sub(r"[^\w.\-]+", "_", os.path.basename(filename or "doc"))[:120]
        file_path = os.path.join(media_path, f"doc_{datetime.now().strftime('%H%M%S')}_{safe_name}")
        with open(file_path, "wb") as f:
            f.write(raw)

        att_id = self._new_attachment_id()
        self.pending_attachments.append(
            {
                "id": att_id,
                "kind": "document",
                "name": filename or safe_name,
                "path": file_path,
                "text": text,
            }
        )
        preview = text[:240] + ("…" if len(text) > 240 else "")
        if pages_n:
            preview = self._tr_ui("🖼 {pages} PDF pages → vision", pages=pages_n) + (
                f" · {preview[:100]}" if preview else ""
            )
        out = {
            "id": att_id,
            "kind": "document",
            "name": filename,
            "preview": preview,
            "vision_pages": pages_n,
        }
        if pdf_vision_warning:
            out["warning"] = pdf_vision_warning
        return out

    def _take_pending_attachments(self) -> list[dict]:
        items = list(self.pending_attachments)
        self.pending_attachments = []
        self.pending_image = None
        return items

    def _format_document_block(self, name: str, text: str) -> str:
        return self._var("chat.attachment_block", name=name, text=text)

    def _merge_user_text_with_documents(self, text: str, documents: list[dict]) -> str:
        if not documents:
            return text
        blocks = [self._format_document_block(d["name"], d["text"]) for d in documents]
        body = "\n\n".join(blocks)
        if text.strip():
            return f"{text.strip()}\n\n{body}"
        return body

    def add_to_pending_images(self, b64, path):
        """Compatibility: enqueue an image attachment."""
        self.pending_attachments.append(
            {
                "id": self._new_attachment_id(),
                "kind": "image",
                "b64": b64,
                "path": path,
            }
        )

    def _set_model_worker_busy(self, busy: bool) -> None:
        self.window.activity_gate.set_worker_busy(busy)

    def process_web_message(self, text):
        mc = self.window.model_controller
        sc = self.window.session_controller
        m_info = mc.get_active_model_info()
        self.window.activity_gate.touch_user_message()

        if mc.llm is None:
            pass
            return
        if self._gallery_describe_busy:
            self.window.inject_message(
                "model",
                self._tr_ui("Wait for gallery description refresh to finish."),
            )
            return
        if self._proactive_worker is not None and self._proactive_worker.isRunning():
            self.window.inject_message(
                "model",
                self._tr_ui("Wait for the proactive notification to finish."),
            )
            return

        if m_info.model_class == "image-edit":
            primary = getattr(self, "pending_image_edit_primary", None) or {}
            src_path = primary.get("path")
            if not src_path or not os.path.isfile(src_path):
                self.window.inject_message(
                    "model",
                    self._tr_ui("📎 Attach the main image (Photo button), then describe the edit in text."),
                )
                return
            sec = getattr(self, "pending_image_edit_secondary", None) or {}
            p2 = sec.get("path")
            src_path_2 = p2 if p2 and os.path.isfile(p2) else None
            self.pending_image_edit_primary = None
            self.pending_image_edit_secondary = None
            self.clear_pending_attachments()
            self.pending_image = None
            self.process_image_edit_generation(text, src_path, src_path_2)
            return

        # Image generation mode — separate code path
        if m_info.model_class == "text-to-image":
            self.process_image_generation(text)
            return

        # --- 1. COLLECT ATTACHMENTS (fast; before WebChannel reply) ---
        pending = self._take_pending_attachments()
        images_to_process = [a for a in pending if a.get("kind") == "image"]
        documents = [a for a in pending if a.get("kind") == "document"]

        model_text = self._merge_user_text_with_documents(text, documents)
        pdf_vision_pages = [img for img in images_to_process if img.get("source_pdf")]
        if pdf_vision_pages:
            pdfs = ", ".join(sorted({i["source_pdf"] for i in pdf_vision_pages}))
            hint = self._var("memory.pdf_attach_prefix", pages=pdfs) + "\n\n"
            model_text = hint + model_text
        ui_text = text
        if documents:
            doc_names = ", ".join(d["name"] for d in documents)
            ui_text = f"{text}\n\n📎 {doc_names}" if text.strip() else f"📎 {doc_names}"
        if pdf_vision_pages:
            ui_text = (
                f"{ui_text}\n\n" + self._tr_ui("🖼 {pages} PDF pages", pages=len(pdf_vision_pages))
                if ui_text.strip()
                else self._tr_ui("🖼 {pages} PDF pages", pages=len(pdf_vision_pages))
            )

        all_paths = [img["path"] for img in images_to_process] if images_to_process else None
        all_images_b64 = [img["b64"] for img in images_to_process]

        if images_to_process:
            llm_user_content = [{"type": "text", "text": model_text}]
            for b64 in all_images_b64:
                llm_user_content.append({"type": "image_url", "image_url": {"url": b64}})
        else:
            llm_user_content = model_text

        sc.ensure_session()

        # User bubble first, then “Thinking…” below
        self.window.inject_message("user", ui_text, images=all_images_b64)
        self.window.show_thinking_indicator(self._tr_ui("Thinking…"))
        self.window._pump_ui()

        self.window.interrupt_voice()
        self.is_first_model_token = True
        self.current_user_text = model_text

        payload = {
            "ui_text": ui_text,
            "model_text": model_text,
            "llm_user_content": llm_user_content,
            "all_paths": all_paths,
            "images_to_process": images_to_process,
        }
        QTimer.singleShot(0, lambda: self._complete_web_message(payload))
        return

    def _complete_web_message(self, payload: dict) -> None:
        """RAG, context, and worker start — do not block sendMessage / WebChannel."""
        mc = self.window.model_controller
        sc = self.window.session_controller
        m_info = mc.get_active_model_info()
        if mc.llm is None:
            self.window.hide_thinking_indicator()
            return

        ui_text = payload["ui_text"]
        model_text = payload["model_text"]
        llm_user_content = payload["llm_user_content"]
        all_paths = payload.get("all_paths")
        images_to_process = payload.get("images_to_process") or []

        self.window.repository.add_chat_message(
            session_id=sc.current_session_id,
            role="user",
            content=ui_text,
            image_path=all_paths,
        )

        # --- 5. BUILD SYSTEM CONTEXT (Persona + RAG) ---
        system_blocks = []
        persona_prompt = self.window.config_repo.get_persona_prompt(m_info)
        if persona_prompt:
            system_blocks.append(self._var("chat.system_role_header") + persona_prompt)
        for block in self._policy_system_appends:
            system_blocks.append(block)

        # RAG on global history: no images, not a long “ping”;
        # at most for the first two user messages in the session (this turn not counted yet);
        # drop history rows that duplicate Q/A already in this chat context.
        prior_user_msgs = sum(1 for m in sc.history if getattr(m, "role", None) == "user")
        if not images_to_process and len(model_text.strip()) > 5 and prior_user_msgs <= 1:
            raw_hist = self.window.repository.get_raw_history_for_search()
            skip_pairs = self._rag_session_qa_pairs(sc)
            if skip_pairs:
                raw_hist = [
                    row for row in raw_hist if ((row[0] or "").strip(), (row[1] or "").strip()) not in skip_pairs
                ]
            memory_content = self.semantic_engine.search(model_text, raw_hist, threshold=0.8)
            if memory_content:
                system_blocks.append(self._var("chat.system_memory_header") + memory_content)

        tools_for_first_pass = self._stripped_tools_for_menu(self.debug_tool_schema)
        tool_hints = self._persona_tool_hints_block(m_info, tools_for_first_pass)
        if tool_hints:
            system_blocks.append(tool_hints)
        full_system_msg = "\n\n".join(system_blocks)

        # --- 6. WRITE TO SESSION WORKING MEMORY ---
        sc.history.append(Message(role="user", content=llm_user_content))
        self._tool_chain_count = 0
        self._web_search_cache_this_turn = {}
        self._web_fetch_urls_ok_this_turn = set()
        self._web_fetch_success_this_turn = 0
        self._web_fetch_dedupe_streak = 0
        self._web_research_touched_this_turn = False
        self._last_tool_name = None
        self._tool_history_truncated_this_turn = False

        # Build worker history (with compression if needed)
        context_history = self.context_manager.build_context(sc, full_system_msg, m_info)
        self._print_ctx_line(context_history, 0)

        # --- 7. RUN THE MODEL ---
        self._retire_model_worker(self.worker)
        limbic_content, limbic_state_summary = self._limbic_for_worker()
        self.worker = ModelWorker(
            mc.llm,
            context_history,
            m_info.settings or {},
            tools=tools_for_first_pass,
            limbic_content=limbic_content,
            limbic_state_summary=limbic_state_summary,
            sens_append_suffix=self._sens_append_for_worker(),
        )

        self.worker.finished_token.connect(
            self.window.update_chat,
            Qt.ConnectionType.QueuedConnection,
        )
        self.worker.finished_answer.connect(
            self.window.finalize_answer,
            Qt.ConnectionType.QueuedConnection,
        )
        self._set_model_worker_busy(True)
        self.worker.start()

    def call_tool(self, name, args, repository=None):
        if name not in self.available_tools:
            return self._tr_tools("chat.tool_not_found", name=name)

        repo = repository or self.window.repository

        return self.available_tools[name](
            **args,
            repository=repo,
            semantic_engine=self.semantic_engine,
            window=self.window,
            locale=getattr(self, "_tools_locale", self.window.config_repo.get_ui_locale()),
        )

    def _tool_content_for_history(self, content):
        """
        Fit tool text into session history: long SERP/memory must not bloat context forever.
        Returns (content_for_message, truncated_bool).
        """
        if not isinstance(content, str):
            return content, False
        cap = _TOOL_HISTORY_MAX_CHARS
        if len(content) <= cap:
            return content, False
        tail = self._tool_history_trunc_marker
        keep = max(2000, cap - len(tail))
        return content[:keep] + tail, True

    def _append_tool_message(self, sc, content, tool_call_id):
        """Append role=tool to history with length trimming."""
        out, trunc = self._tool_content_for_history(content)
        if trunc:
            self._tool_history_truncated_this_turn = True
        sc.history.append(Message(role="tool", content=out, tool_call_id=tool_call_id))

    def _print_ctx_line(self, context_messages, chain_step: int):
        """P1: one log line — token estimate of built context and tool compression flags."""
        try:
            est = self.context_manager._estimate_tokens(context_messages)
            ctx_clip = int(getattr(self.context_manager, "last_build_tool_clipped", False))
            hist_trunc = int(getattr(self, "_tool_history_truncated_this_turn", False))
            print(
                f"[CTX] chain_step={chain_step}/{self.MAX_TOOL_CHAIN_STEPS} "
                f"est_tok~{est} ctx_tool_clip={ctx_clip} hist_tool_trunc={hist_trunc}"
            )
        except Exception as e:
            print(f"[CTX] chain_step={chain_step} metrics_failed: {e!r}")

    def _followup_tool_names_from_schema(self, last_tool):
        """Tool order from x_lira_followup_tools on the just-finished tool entry."""
        ordered = tuple(t["function"]["name"] for t in self.debug_tool_schema)
        if not last_tool:
            return ordered
        by_name = {t["function"]["name"]: t for t in self.debug_tool_schema}
        entry = by_name.get(last_tool)
        if not entry:
            return ordered
        nxt = entry.get(TOOL_FOLLOWUP_TOOLS_KEY)
        if nxt is None:
            return ordered
        return tuple(nxt)

    def _next_tool_chain_step(self) -> int:
        """Next TOOL_CALL step number (counter already bumped for the finished call)."""
        return self._tool_chain_count + 1

    def _tool_eligible_for_menu(self, name: str) -> bool:
        """
        Whether to expose a tool in the model menu at this chain step.
        Blocks bad calls: web without explicit request/research, gallery without intent or not step 1, forbidden_if_last dupes.
        """
        if name in ("web_search", "web_search_saved", "web_fetch_url"):
            if not self._user_asks_web_in_message() and not self._web_research_touched_this_turn:
                return False
        if name == "gallery_search" and not self._user_asks_gallery_in_message():
            return False
        if name == "camera_capture" and not self._user_asks_camera_in_message():
            return False
        entry = self._tool_schema_by_name.get(name)
        if not entry:
            return False
        allowed_steps = self._tool_policy_step_tuple(entry.get(TOOL_ONLY_AT_CHAIN_STEPS_KEY))
        if allowed_steps and self._next_tool_chain_step() not in allowed_steps:
            return False
        forbidden_after = self._tool_policy_name_tuple(entry.get(TOOL_FORBIDDEN_IF_LAST_TOOL_KEY))
        last = self._last_tool_name
        if forbidden_after and last in forbidden_after:
            return False
        return True

    def _stripped_tools_for_menu(self, schema_entries: list) -> list:
        mc = self.window.model_controller
        m_info = mc.get_active_model_info()
        has_vision = self._model_has_vision(mc, m_info)
        out: list[dict] = []
        for t in schema_entries:
            n = t.get("function", {}).get("name")
            if not n or not self._tool_eligible_for_menu(n):
                continue
            if n in ("gallery_search", "camera_capture") and not has_vision:
                continue
            out.append(_strip_tool_schema_meta(t))
        return out

    def _persona_tool_hints_block(self, m_info, tools_menu: list | None) -> str:
        """Build localized tool hints from the currently exposed tool menu."""
        tools = tools_menu or []
        if not tools:
            return ""
        lines: list[str] = []
        for tool in tools:
            fn = tool.get("function") or {}
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            desc = str(fn.get("description") or "").strip()
            lines.append(f"- {name}: {desc}" if desc else f"- {name}")
        if not lines:
            return ""
        return (
            self.window.config_repo.get_persona_text(
                m_info,
                "tool_hints",
                tool_list="\n".join(lines),
            )
            or ""
        ).strip()

    def _tools_for_followup(self, allow_tools: bool):
        if not allow_tools:
            return None
        by_name = {t["function"]["name"]: t for t in self.debug_tool_schema}
        names = self._followup_tool_names_from_schema(self._last_tool_name)
        picked: list[dict] = []
        for n in names:
            if n not in by_name:
                continue
            if not self._tool_eligible_for_menu(n):
                continue
            picked.append(_strip_tool_schema_meta(by_name[n]))
        return picked if picked else None

    def _start_proactive_followup_worker(self, sc, m_info, *, allow_tools: bool, last_completed_tool=None) -> None:
        if last_completed_tool is not None:
            self._last_tool_name = last_completed_tool

        system_content = self._proactive_system_content()
        user_line = (self._proactive_user_prompt or "").strip()
        context_history = self.context_manager.build_proactive_context(system_content, user_line)
        self._print_ctx_line(context_history, self._tool_chain_count)

        tools = self._tools_for_followup(allow_tools) if self._proactive_allow_tools() else None
        menu = [x["function"]["name"] for x in tools] if tools else []
        print(
            f"[PROACTIVE] followup: allow_tools={allow_tools} "
            f"chain_step={self._tool_chain_count}/{self.MAX_TOOL_CHAIN_STEPS} "
            f"last_tool={self._last_tool_name!r} tool_menu={menu}",
            flush=True,
        )

        settings = dict(m_info.settings or {})
        settings["max_tokens"] = self._proactive_max_tokens()
        limbic_content, limbic_state_summary = self._limbic_for_worker()
        self._retire_proactive_worker()
        self._proactive_worker = ModelWorker(
            self.window.model_controller.llm,
            context_history,
            settings,
            tools=tools,
            limbic_content=limbic_content,
            limbic_state_summary=limbic_state_summary,
            sens_append_suffix=self._sens_append_for_worker(),
        )
        self._proactive_worker.finished_answer.connect(
            self.window.finalize_answer,
            Qt.ConnectionType.QueuedConnection,
        )
        self.window.show_thinking_indicator(self._tr_ui("Telegram…"))
        self._proactive_worker.start()

    def _start_followup_worker(self, sc, m_info, *, allow_tools: bool, last_completed_tool=None):
        """
        Next pass after a tool: with allow_tools=True the model may call another tool
        (e.g. web_fetch_url after web_search). With False — final text only.
        last_completed_tool — just finished tool; narrows the menu this step.
        """
        if getattr(self, "_proactive_turn_active", False):
            self._start_proactive_followup_worker(
                sc, m_info, allow_tools=allow_tools, last_completed_tool=last_completed_tool
            )
            return

        if last_completed_tool is not None:
            self._last_tool_name = last_completed_tool

        persona_prompt = self.window.config_repo.get_persona_prompt(m_info)
        sys_parts = []
        if persona_prompt:
            sys_parts.append(self._var("chat.system_role_header") + persona_prompt)
        sys_parts.extend(self._policy_system_appends)
        followup_hint = self._followup_instruction_for_last_tool()
        if followup_hint:
            sys_parts.append(followup_hint)
        tools = self._tools_for_followup(allow_tools)
        tool_hints = self._persona_tool_hints_block(m_info, tools)
        if tool_hints:
            sys_parts.append(tool_hints)
        system_content = "\n\n".join(sys_parts)

        context_history = self.context_manager.build_context(sc, system_content, m_info)
        self._print_ctx_line(context_history, self._tool_chain_count)

        menu = [x["function"]["name"] for x in tools] if tools else []
        print(
            f"[TOOL] followup worker: allow_tools={allow_tools} "
            f"chain_step={self._tool_chain_count}/{self.MAX_TOOL_CHAIN_STEPS} "
            f"last_tool={self._last_tool_name!r} tool_menu={menu}"
        )
        self._retire_model_worker(self.worker)
        limbic_content, limbic_state_summary = self._limbic_for_worker()
        self.worker = ModelWorker(
            self.window.model_controller.llm,
            context_history,
            m_info.settings or {},
            tools=tools,
            limbic_content=limbic_content,
            limbic_state_summary=limbic_state_summary,
            sens_append_suffix=self._sens_append_for_worker(),
        )
        self.worker.finished_token.connect(
            self.window.update_chat,
            Qt.ConnectionType.QueuedConnection,
        )
        self.worker.finished_answer.connect(
            self.window.finalize_answer,
            Qt.ConnectionType.QueuedConnection,
        )
        self.window.show_thinking_indicator(self._tr_ui("Preparing…"))
        self._set_model_worker_busy(True)
        self.worker.start()

    def _followup_instruction_for_last_tool(self) -> str:
        """
        Force grounded follow-up after tool execution.
        Prevents generic/flirty filler when concrete tool output exists.
        """
        last = (self._last_tool_name or "").strip()
        if not last:
            return ""
        if last in ("web_search", "web_search_saved", "web_fetch_url"):
            return (
                "FOLLOW-UP RULE: A web tool has just returned data. "
                "Answer the user using the latest tool result directly (facts, links, numbers). "
                "Do not ignore tool output and do not switch to generic small talk. "
                "If tool output is empty or error, say that explicitly and ask a precise next step."
            )
        if last in ("memory_search", "gallery_search", "camera_capture"):
            return (
                "FOLLOW-UP RULE: A tool has just returned context. "
                "Base your reply on that result first; avoid generic fallback lines. "
                "If result says no hit, state no result and ask a targeted clarifying question."
            )
        return ""

    def _rag_session_qa_pairs(self, sc) -> set[tuple[str, str]]:
        """
        (user, assistant) pairs already in sc.history this session.
        Exclude them from global history RAG — otherwise we duplicate chat context.
        """
        pairs: set[tuple[str, str]] = set()
        msgs = list(sc.history)
        n = len(msgs)
        i = 0
        while i < n:
            if getattr(msgs[i], "role", None) != "user":
                i += 1
                continue
            u_text = self.context_manager._content_to_text(getattr(msgs[i], "content", "")).strip()
            j = i + 1
            while j < n:
                if getattr(msgs[j], "role", None) == "assistant" and not getattr(msgs[j], "tool_call_id", None):
                    a_text = self.context_manager._content_to_text(getattr(msgs[j], "content", "")).strip()
                    if u_text or a_text:
                        pairs.add((u_text, a_text))
                    break
                j += 1
            i += 1
        return pairs

    def _user_asks_gallery_in_message(self) -> bool:
        """Explicit gallery/image request in the current user message (not dealers/prices)."""
        ug = (self.current_user_text or "").casefold()
        return any(n in ug for n in self._gallery_user_intent_needles)

    def _user_asks_web_in_message(self) -> bool:
        """Explicit web search / online data request in the current user message."""
        ug = (self.current_user_text or "").casefold()
        return any(n in ug for n in self._web_user_intent_needles)

    def _user_asks_camera_in_message(self) -> bool:
        ug = (self.current_user_text or "").casefold()
        return any(n in ug for n in self._camera_user_intent_needles)

    def _model_has_vision(self, mc, m_info) -> bool:
        if m_info is None:
            return False
        if getattr(m_info, "model_class", None) in ("text-to-image", "image-edit"):
            return False
        if not mc.llm:
            return False
        if getattr(m_info, "clip_model_path", None):
            return True
        return getattr(mc.llm, "chat_handler", None) is not None

    def _user_question_for_vision_block(self) -> str:
        uq_raw = (self.current_user_text or "").strip()
        if len(uq_raw) > _USER_QUESTION_FOR_VISION_MAX_CHARS:
            return uq_raw[:_USER_QUESTION_FOR_VISION_MAX_CHARS] + self._var("chat.user_question_truncated")
        return uq_raw

    @staticmethod
    def _is_qwen3_vl_model(m_info) -> bool:
        mt = (getattr(m_info, "model_type", None) or "").strip().lower()
        return "qwen3-vl" in mt or "qwen3vl" in mt

    def _gallery_vision_limits(self, m_info) -> tuple[int, int]:
        """(max_vision_images, vision_batch_size). Qwen3-VL MoE: few frames per call — KV/slots."""
        from core.scripts.chat.tools.gallery_search import (
            gallery_max_vision_images,
            gallery_vision_batch_size,
        )

        if self._is_qwen3_vl_model(m_info):
            # MoE: one image per call + KV reset between batches (see _clear_llm_before_vision).
            return gallery_max_vision_images(), 1
        return gallery_max_vision_images(), gallery_vision_batch_size()

    def _active_model_display_name(self, m_info) -> str:
        return (getattr(m_info, "name", None) or self._var("chat.assistant_default_name")).strip()

    def _clear_llm_before_vision(self, mc) -> None:
        """Reset KV after main chat — else gallery/camera vision fails on decode."""
        llm = getattr(mc, "llm", None)
        if llm is None:
            return
        try:
            from infrastructure.runtime.llm_cuda_hygiene import release_llm_cuda_cache

            release_llm_cuda_cache(llm)
            clear_kv = getattr(getattr(llm, "_ctx", None), "kv_cache_clear", None)
            if callable(clear_kv):
                clear_kv()
            llm.n_tokens = 0
            if hasattr(llm, "input_ids"):
                llm.input_ids.fill(0)
            handler = getattr(llm, "chat_handler", None)
            if handler is not None and hasattr(handler, "_last_image_embed"):
                handler._last_image_embed = None
                handler._last_image_hash = None
        except Exception as exc:
            print(f"[VISION] pre-call clear failed: {exc!r}", flush=True)

    def _vision_comment_on_images(
        self,
        mc,
        m_info,
        *,
        intro_text: str,
        image_paths: list[str],
        max_tokens: int = 300,
    ) -> str:
        vision_content = [{"type": "text", "text": intro_text}]
        for path in image_paths:
            if not path:
                continue
            try:
                with open(path, "rb") as f:
                    raw_b64 = base64.b64encode(f.read()).decode()
                _, final_b64 = self.process_incoming_image(raw_b64)
                vision_content.append({"type": "image_url", "image_url": {"url": final_b64}})
            except Exception:
                continue
        if not mc.llm or not mc.llm.chat_handler:
            return self._tr_tools("chat.vision.images_no_vision")
        model_name = self._active_model_display_name(m_info)
        n_img_pre = sum(1 for p in vision_content if isinstance(p, dict) and p.get("type") == "image_url")
        suffix_key = "vision_comment_multi" if n_img_pre > 1 else "vision_comment_suffix"
        vision_content.append(
            {
                "type": "text",
                "text": self.window.config_repo.get_persona_text(
                    m_info,
                    suffix_key,
                    model_name=model_name,
                    n_images=n_img_pre,
                ),
            }
        )
        n_img = sum(1 for p in vision_content if isinstance(p, dict) and p.get("type") == "image_url")
        if self._is_qwen3_vl_model(m_info):
            self._clear_llm_before_vision(mc)
        print(f"[VISION] gallery/camera comment images={n_img} model={m_info.model_type!r}", flush=True)
        try:
            response = mc.llm.chat_handler(
                llama=mc.llm,
                messages=[
                    {
                        "role": "system",
                        "content": self.window.config_repo.get_persona_text(m_info, "vision_chat_subcall_system"),
                    },
                    {"role": "user", "content": vision_content},
                ],
                max_tokens=max_tokens,
                temperature=0.7,
            )
            content = response["choices"][0]["message"]["content"]
            raw_out = ("" if content is None else str(content)).strip()
            out = clean_vision_assistant_text(raw_out)
            if not out and raw_out:
                print(
                    f"[VISION] comment degenerate after clean model={m_info.model_type!r} "
                    f"raw_preview={raw_out[:120]!r}",
                    flush=True,
                )
                return self._tr_tools("chat.vision.frames_comment_failed")
            print(
                f"[VISION] comment done model={m_info.model_type!r} images={n_img} reply_len={len(out)}",
                flush=True,
            )
            return out
        except Exception as exc:
            print(f"[VISION] comment failed: {exc!r}", flush=True)
            traceback.print_exc()
            return self._tr_tools("chat.vision.frames_comment_failed")

    def _vision_comment_on_gallery_search(
        self,
        mc,
        m_info,
        *,
        intro_text: str,
        image_paths: list[str],
    ) -> str:
        """Vision on gallery_search frames; limits and batch — config.json → gallery_search."""
        max_total, batch_size = self._gallery_vision_limits(m_info)
        paths = [p for p in image_paths if p][:max_total]
        if not paths:
            return ""
        if len(paths) <= batch_size:
            return self._vision_comment_on_images(mc, m_info, intro_text=intro_text, image_paths=paths)
        parts: list[str] = []
        n_batches = (len(paths) + batch_size - 1) // batch_size
        for bi, start in enumerate(range(0, len(paths), batch_size)):
            chunk = paths[start : start + batch_size]
            sub_intro = f"{intro_text}\n\n" + self._tr_tools(
                "chat.vision.batch_note",
                bi=bi + 1,
                n_batches=n_batches,
                start=start + 1,
                end=start + len(chunk),
                total=len(paths),
            )
            piece = self._vision_comment_on_images(
                mc,
                m_info,
                intro_text=sub_intro,
                image_paths=chunk,
                max_tokens=280,
            )
            if piece:
                parts.append(piece)
        return "\n\n".join(parts)

    def _emit_gallery_ui(self, sc, repo, items: list) -> None:
        ui_images = []
        for item in items or []:
            if isinstance(item, dict):
                path = (item.get("path") or "").strip()
                if not path:
                    continue
                ui_images.append(
                    {
                        "id": item.get("id"),
                        "path": path,
                        "prompt": item.get("prompt") or "",
                        "description": item.get("description") or "",
                    }
                )
            elif isinstance(item, (list, tuple)) and len(item) >= 3:
                ui_images.append(
                    {
                        "id": item[0],
                        "prompt": item[1] or "",
                        "path": item[2] or "",
                        "description": (item[4] if len(item) > 4 else "") or "",
                    }
                )
            elif isinstance(item, str) and item.strip():
                ui_images.append({"id": None, "path": item.strip(), "prompt": "", "description": ""})
        if not ui_images:
            return
        ui_data = {"role": "model", "text": "", "images": ui_images}
        ui_json = json.dumps(ui_data, ensure_ascii=False)
        self.window.bridge.send_gallery_to_ui(ui_json)
        db_gallery_string = f"UI_GALLERY|{ui_json}"
        repo.add_chat_message(sc.current_session_id, "assistant", db_gallery_string)

    def _qimage_to_jpeg_data_url(self, img: QImage) -> str:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice

        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPEG", quality=85)
        return f"data:image/jpeg;base64,{base64.b64encode(bytes(ba.data())).decode()}"

    def _open_camera_capture_dialog(self) -> tuple[int, CameraCaptureDialog]:
        sc = self.window.session_controller
        sc.ensure_session()
        sid = sc.normalized_session_id()
        skip = sc.has_camera_consent(sid)
        on_grant = (lambda session_key=sid: sc.grant_camera_consent(session_key)) if sid is not None else None
        print(f"[Camera] open session_id={sid} skip_consent={skip}", flush=True)
        dlg = CameraCaptureDialog(
            self.window,
            skip_consent=skip,
            on_consent_granted=on_grant,
        )
        return dlg.exec(), dlg

    def capture_camera_for_user_attachment(self) -> str:
        """
        Camera dialog (like camera_capture tool), frame in pending_attachments.
        JSON {id, preview} for Web UI or "" on cancel.
        """
        code, dlg = self._open_camera_capture_dialog()
        if code != CameraCaptureDialog.DialogCode.Accepted:
            return ""
        img = dlg.captured_image()
        if img is None or img.isNull():
            return ""
        final_b64 = self._qimage_to_jpeg_data_url(img)
        file_path, clean_b64 = self.process_incoming_image(final_b64)
        if not file_path or not clean_b64:
            return ""
        att_id = self._new_attachment_id()
        self.pending_attachments.append({"id": att_id, "kind": "image", "b64": clean_b64, "path": file_path})
        return json.dumps({"id": att_id, "preview": clean_b64}, ensure_ascii=False)

    def capture_camera_frame_for_tool(self, reason: str = "") -> dict:
        sc = self.window.session_controller
        print("[TOOL] camera_capture: opening dialog", flush=True)
        skip_before = sc.has_camera_consent(sc.normalized_session_id())
        code, dlg = self._open_camera_capture_dialog()
        print(f"[TOOL] camera_capture: dialog closed code={code}", flush=True)
        out = dlg.outcome()
        if code != CameraCaptureDialog.DialogCode.Accepted:
            st = out.status
            if st == "cancelled":
                st = "denied" if not skip_before else "cancelled"
            return {
                "status": st,
                "path": None,
                "message": out.error_message or self._tr_tools("chat.camera_capture.user_cancelled"),
                "reason": reason,
            }
        img = dlg.captured_image()
        if img is None or img.isNull():
            return {
                "status": "error",
                "path": None,
                "message": self._tr_tools("chat.camera_capture.empty_frame"),
                "reason": reason,
            }
        m_info = self.window.model_controller.get_active_model_info()
        sid = sc.normalized_session_id() or sc.current_session_id or "default"
        safe_type = m_info.model_type.lower().replace(" ", "_").replace("-", "_")
        media_path = str(lira_data("media", f"{safe_type}-{m_info.id}", sid))
        os.makedirs(media_path, exist_ok=True)
        filename = f"cam_{datetime.now().strftime('%H%M%S')}.jpg"
        file_path = os.path.join(media_path, filename)
        if not img.save(file_path, "JPG"):
            return {
                "status": "error",
                "path": None,
                "message": self._tr_tools("chat.camera_capture.save_failed"),
                "reason": reason,
            }
        print(f"[TOOL] camera_capture: saved {file_path!r} reason={reason!r}")
        return {"status": "captured", "path": file_path, "message": None, "reason": reason}

    def finalize_answer(self, full_response):
        import json

        sc = self.window.session_controller
        mc = self.window.model_controller
        repo = self.window.repository
        m_info = self.window.model_controller.get_active_model_info()

        from_proactive = getattr(self, "_proactive_turn_active", False)

        if from_proactive:
            if full_response.startswith("TOOL_CALL|"):
                parts = full_response.split("|", 3)
                fn_name = parts[1] if len(parts) > 1 else "?"
                print(
                    f"[PROACTIVE] ignored tool {fn_name!r} (telegram-only, no chat history)",
                    flush=True,
                )
                full_response = self._proactive_no_tools_fallback()
            self._finalize_proactive_telegram_reply(full_response)
            return

        if full_response.startswith("TOOL_CALL|"):
            parts = full_response.split("|", 3)
            fn_name = parts[1] if len(parts) > 1 else "?"
            if from_proactive and not self._proactive_allow_tools():
                print(
                    f"[PROACTIVE] refused tool {fn_name!r} (tools disabled)",
                    flush=True,
                )
                self.window.hide_thinking_indicator()
                full_response = self._proactive_no_tools_fallback()

        if full_response.startswith("TOOL_CALL|"):
            parts = full_response.split("|", 3)
            self._tool_chain_count += 1
            allow_more_tools = self._tool_chain_count < self.MAX_TOOL_CHAIN_STEPS

            fn_name = parts[1]
            fn_args_json = parts[2]
            call_id = parts[3]
            try:
                parsed_args = json.loads(fn_args_json) if fn_args_json else {}
            except Exception:
                parsed_args = {}
            if isinstance(parsed_args, dict):
                embedded_name = parsed_args.get("tool_name") or parsed_args.get("name")
                if isinstance(embedded_name, str):
                    fn_name = embedded_name.strip().replace(" ", "_")
                nested_args = parsed_args.get("arguments")
                if isinstance(nested_args, dict):
                    merged = dict(parsed_args)
                    for k, v in nested_args.items():
                        if k not in merged or not merged.get(k):
                            merged[k] = v
                    merged.pop("arguments", None)
                    parsed_args = merged
                if fn_name in ("memory_search", "gallery_search", "web_search"):
                    q = str(parsed_args.get("query") or parsed_args.get("input") or "").strip()
                    if not q or q.casefold() in {"поиск", "search", "find", "query"}:
                        fallback_q = str(self.current_user_text or "").strip()
                        if fallback_q:
                            parsed_args["query"] = fallback_q[:800]
                    else:
                        parsed_args["query"] = q
                fn_args_json = json.dumps(parsed_args, ensure_ascii=False)

            # Dedupe streak only counts consecutive web_fetch_url; another tool resets it.
            if fn_name != "web_fetch_url":
                self._web_fetch_dedupe_streak = 0

            print(f"[TOOL] call: {fn_name} | raw_args={fn_args_json} | call_id={call_id}")

            policy_refusal = self._tool_chain_policy_refusal(fn_name)
            if policy_refusal is not None:
                print(f"[TOOL] {fn_name}: policy blocked — {policy_refusal[:200]!r}")
                sc.history.append(
                    Message(
                        role="assistant",
                        content=None,
                        tool_call_id=call_id,
                        tool_function_name=fn_name,
                        tool_function_arguments=fn_args_json,
                    )
                )
                self._append_tool_message(sc, policy_refusal, call_id)
                self._start_followup_worker(
                    sc,
                    m_info,
                    allow_tools=self._allow_tools_for_followup(allow_more_tools),
                    last_completed_tool=fn_name,
                )
                return

            orphan_fetch_refusal = self._orphan_web_fetch_refusal(fn_name)
            if orphan_fetch_refusal is not None:
                print(f"[TOOL] {fn_name}: blocked — {orphan_fetch_refusal[:200]!r}")
                sc.history.append(
                    Message(
                        role="assistant",
                        content=None,
                        tool_call_id=call_id,
                        tool_function_name=fn_name,
                        tool_function_arguments=fn_args_json,
                    )
                )
                self._append_tool_message(sc, orphan_fetch_refusal, call_id)
                self._start_followup_worker(
                    sc,
                    m_info,
                    allow_tools=self._allow_tools_for_followup(allow_more_tools),
                    last_completed_tool=fn_name,
                )
                return

            if fn_name == "gallery_search":
                self.window.show_thinking_indicator(self._tr_ui("Search"))
            elif fn_name == "camera_capture":
                self.window.show_thinking_indicator(self._tr_ui("Camera…"))
            elif fn_name == "memory_search":
                self.window.show_thinking_indicator(self._tr_ui("Search"))
            elif fn_name == "web_search":
                self.window.show_thinking_indicator(self._tr_ui("Search"))
            elif fn_name == "web_search_saved":
                self.window.show_thinking_indicator(self._tr_ui("Reading saved results…"))
            elif fn_name == "web_fetch_url":
                self.window.show_thinking_indicator(self._tr_ui("Opening link…"))
            else:
                self.window.show_thinking_indicator(self._tr_ui("Processing request…"))

            sc.history.append(
                Message(
                    role="assistant",
                    content=None,
                    tool_call_id=call_id,
                    tool_function_name=fn_name,
                    tool_function_arguments=fn_args_json,
                )
            )

            if fn_name == "gallery_search":
                args = {}
                try:
                    args = json.loads(fn_args_json)
                except Exception:
                    args = {}
                q_val = (args.get("query") or args.get("input") or "").strip() or self._tr_tools(
                    "chat.gallery_search.default_query"
                )

                intent_refusal = gallery_search_user_intent_refusal(
                    self._tool_schema_by_name.get("gallery_search"),
                    has_gallery_intent=self._user_asks_gallery_in_message(),
                    locale=self._tools_locale,
                )
                if intent_refusal is not None:
                    print("[TOOL] gallery_search: blocked — no explicit gallery/visual intent in user message")
                    self._append_tool_message(sc, intent_refusal, call_id)
                    self._start_followup_worker(
                        sc,
                        m_info,
                        allow_tools=self._allow_tools_for_followup(allow_more_tools),
                        last_completed_tool=fn_name,
                    )
                    return

                gs_on, gs_msg = gallery_web_suppress_config(
                    self._tool_schema_by_name.get("gallery_search"),
                    locale=self._tools_locale,
                )
                if gs_on and self._web_research_touched_this_turn and not self._user_asks_gallery_in_message():
                    print(
                        "[TOOL] gallery_search: suppressed — web tools already used for this user message without gallery intent"
                    )
                    result = gs_msg
                    self._append_tool_message(sc, result, call_id)
                    self._start_followup_worker(
                        sc,
                        m_info,
                        allow_tools=self._allow_tools_for_followup(allow_more_tools),
                        last_completed_tool=fn_name,
                    )
                    return

                print(f"[TOOL] gallery_search: query={q_val!r}")

                result = self.call_tool(fn_name, {"query": q_val}, repository=self.window.gallery_repo)

                if isinstance(result, list) and len(result) > 0:
                    max_vision, vision_batch = self._gallery_vision_limits(m_info)
                    result_for_vision = result[:max_vision]

                    # Prompts only for frames actually sent to vision
                    db_data_str = ""
                    for i, item in enumerate(result_for_vision, 1):
                        if isinstance(item, dict):
                            cap = item.get("caption") or item.get("description") or item.get("prompt") or ""
                        elif isinstance(item, (list, tuple)):
                            cap = item[1] if len(item) > 1 else ""
                        else:
                            cap = ""
                        db_data_str += self._tr_tools("chat.gallery_search.frame_line", i=i, cap=cap) + "\n"

                    uq_show = self._user_question_for_vision_block()
                    model_name = self._active_model_display_name(m_info)
                    batch_note = f", batches of {vision_batch}" if len(result_for_vision) > vision_batch else ""
                    intro = self._tr_tools(
                        "chat.gallery_search.vision_context",
                        question=uq_show,
                        query=q_val,
                        vision_n=len(result_for_vision),
                        total_n=len(result),
                        batch_note=batch_note,
                        prompts=db_data_str,
                        model_name=model_name,
                    )
                    vision_paths = []
                    for item in result_for_vision:
                        p = item[2] if isinstance(item, tuple) else item.get("path")
                        if p:
                            vision_paths.append(p)
                    vision_reply = self._vision_comment_on_gallery_search(
                        mc, m_info, intro_text=intro, image_paths=vision_paths
                    )
                    tool_meta = self._tr_tools(
                        "chat.gallery_search.meta",
                        query=q_val,
                        total=len(result),
                        vision_n=len(result_for_vision),
                        prompts=db_data_str,
                    )
                    if vision_reply:
                        tool_text = f"{vision_reply}{tool_meta}"
                    else:
                        tool_text = self._tr_tools("chat.gallery_search.found") + tool_meta
                    try:
                        self._emit_gallery_ui(sc, repo, result)
                    except Exception as e:
                        print(f"[TOOL] gallery_search: UI gallery emit failed: {e!r}")
                    content = tool_text
                else:
                    content = self._tr_tools("chat.gallery_search.empty")

                self._append_tool_message(sc, content, call_id)

            elif fn_name == "camera_capture":
                args = {}
                try:
                    args = json.loads(fn_args_json)
                except Exception:
                    args = {}
                reason = (args.get("reason") or "").strip()

                if not self._model_has_vision(mc, m_info):
                    content = self._tr_tools("chat.camera_capture.no_vision")
                    self._append_tool_message(sc, content, call_id)
                    self._start_followup_worker(
                        sc,
                        m_info,
                        allow_tools=self._allow_tools_for_followup(allow_more_tools),
                        last_completed_tool=fn_name,
                    )
                    return

                intent_refusal = camera_capture_user_intent_refusal(
                    self._tool_schema_by_name.get("camera_capture"),
                    has_camera_intent=self._user_asks_camera_in_message(),
                    locale=self._tools_locale,
                )
                if intent_refusal is not None:
                    print("[TOOL] camera_capture: blocked — no camera intent in user message")
                    self._append_tool_message(sc, intent_refusal, call_id)
                    self._start_followup_worker(
                        sc,
                        m_info,
                        allow_tools=self._allow_tools_for_followup(allow_more_tools),
                        last_completed_tool=fn_name,
                    )
                    return

                cam_on, cam_msg = camera_web_suppress_config(
                    self._tool_schema_by_name.get("camera_capture"),
                    locale=self._tools_locale,
                )
                if cam_on and self._web_research_touched_this_turn and not self._user_asks_camera_in_message():
                    print("[TOOL] camera_capture: suppressed after web tools without camera intent")
                    self._append_tool_message(
                        sc,
                        cam_msg,
                        call_id,
                    )
                    self._start_followup_worker(
                        sc,
                        m_info,
                        allow_tools=self._allow_tools_for_followup(allow_more_tools),
                        last_completed_tool=fn_name,
                    )
                    return

                print(f"[TOOL] camera_capture: reason={reason!r}")
                cap = self.call_tool(fn_name, {"reason": reason})
                st = (cap or {}).get("status") if isinstance(cap, dict) else None
                path = (cap or {}).get("path") if isinstance(cap, dict) else None
                msg = (cap or {}).get("message") if isinstance(cap, dict) else None

                if st != "captured" or not path:
                    if st == "denied":
                        content = self._tr_tools("chat.camera_capture.denied")
                    elif st == "cancelled":
                        content = self._tr_tools("chat.camera_capture.cancelled")
                    elif st == "unavailable":
                        content = self._tr_tools(
                            "chat.camera_capture.unavailable",
                            msg=msg or self._tr_tools("chat.camera_capture.no_device"),
                        )
                    else:
                        content = self._tr_tools(
                            "chat.camera_capture.failed",
                            msg=msg or st or self._tr_tools("chat.camera_capture.error_generic"),
                        )
                else:
                    uq_show = self._user_question_for_vision_block()
                    model_name = self._active_model_display_name(m_info)
                    intro = self._tr_tools(
                        "chat.camera_capture.vision_context",
                        question=uq_show,
                        model_name=model_name,
                    )
                    vision_reply = self._vision_comment_on_images(mc, m_info, intro_text=intro, image_paths=[path])
                    tool_meta = self._tr_tools("chat.camera_capture.meta_path", path=path)
                    if reason:
                        tool_meta += self._tr_tools("chat.camera_capture.meta_reason", reason=reason)
                    if vision_reply:
                        content = f"{vision_reply}{tool_meta}"
                    else:
                        content = self._tr_tools("chat.camera_capture.received") + tool_meta
                    try:
                        self._emit_gallery_ui(sc, repo, [path])
                    except Exception as e:
                        print(f"[TOOL] camera_capture: UI emit failed: {e!r}")

                self._append_tool_message(sc, content, call_id)

            elif fn_name == "memory_search":
                try:
                    # 1. Parse JSON from model-supplied arguments
                    raw_args = json.loads(fn_args_json)

                    # 2. Build dict for call_tool
                    # Extract search value whatever the model named it
                    search_query = (raw_args.get("query") or raw_args.get("input") or "").strip()
                    print(f"[TOOL] memory_search: query={search_query!r}")
                except Exception as e:
                    search_query = ""
                    print(f"[TOOL] memory_search: parse_error={e!r} -> empty query")

                mem_gallery_refusal = memory_search_gallery_redirect_refusal(
                    has_gallery_intent=self._user_asks_gallery_in_message(),
                    query=search_query,
                    locale=self._tools_locale,
                )
                if mem_gallery_refusal is not None:
                    print(
                        "[TOOL] memory_search: blocked — visual/gallery intent "
                        f"(user_gallery={self._user_asks_gallery_in_message()!r})"
                    )
                    self._append_tool_message(sc, mem_gallery_refusal, call_id)
                    self._start_followup_worker(
                        sc,
                        m_info,
                        allow_tools=self._allow_tools_for_followup(allow_more_tools),
                        last_completed_tool=fn_name,
                    )
                    return

                if not search_query:
                    result = self._tr_tools("tools.memory_search.skipped_empty")
                else:
                    result = self.call_tool(fn_name, {"query": search_query}, repository=self.window.repository)

                self._append_tool_message(sc, str(result), call_id)
            elif fn_name == "web_search":
                try:
                    raw_args = json.loads(fn_args_json)
                    query = (raw_args.get("query") or raw_args.get("input") or "").strip()
                    limit = int(raw_args.get("limit", 5))
                    language = raw_args.get("language") or "ru"
                    route_mode = (raw_args.get("route_mode") or "auto").strip().lower()
                    limit = max(1, min(limit, 10))
                    print(
                        f"[TOOL] web_search: query={query!r} limit={limit} language={language!r} route_mode={route_mode!r}"
                    )
                except Exception as e:
                    query = ""
                    limit = 5
                    language = "ru"
                    route_mode = "auto"
                    print(f"[TOOL] web_search: parse_error={e!r} -> empty query")

                web_intent_refusal = web_search_user_intent_refusal(
                    self._tool_schema_by_name.get("web_search"),
                    has_web_intent=self._user_asks_web_in_message(),
                    locale=self._tools_locale,
                )
                if web_intent_refusal is not None:
                    print("[TOOL] web_search: blocked — no explicit web/internet intent in user message")
                    self._append_tool_message(sc, web_intent_refusal, call_id)
                    self._start_followup_worker(
                        sc,
                        m_info,
                        allow_tools=self._allow_tools_for_followup(allow_more_tools),
                        last_completed_tool=fn_name,
                    )
                    return

                if not query:
                    result = self._tr_tools("tools.web_search.skipped_empty")
                else:
                    cache_key = (
                        query.casefold().strip(),
                        (language or "ru").casefold().strip(),
                        route_mode,
                    )
                    if cache_key in self._web_search_cache_this_turn:
                        result = self._web_search_cache_this_turn[cache_key]
                        print(
                            f"[TOOL] web_search: dedupe — same query already run this reply (query={query!r} route_mode={route_mode!r})"
                        )
                    else:
                        print(
                            f"[TOOL] invoke web_search: "
                            f"query={query!r}, limit={limit}, language={language!r}, route_mode={route_mode!r}"
                        )
                        result = self.call_tool(
                            fn_name,
                            {"query": query, "limit": limit, "language": language, "route_mode": route_mode},
                            repository=self.window.repository,
                        )
                        rs = str(result)
                        if not rs.startswith(self._tr_tools("tools.web_search.error_prefix")):
                            self._web_search_cache_this_turn[cache_key] = rs
                        print(f"[TOOL] web_search result: {rs[:600]}")
                self._append_tool_message(sc, str(result), call_id)
                if not str(result).startswith(self._tr_tools("tools.web_search.skipped_empty")[:20]):
                    self._web_research_touched_this_turn = True
            elif fn_name == "web_search_saved":
                try:
                    raw_args = json.loads(fn_args_json)
                    rid_raw = raw_args.get("run_id")
                    if rid_raw is None:
                        rid_raw = raw_args.get("research_run_id")
                    run_id_saved = int(rid_raw)
                except Exception as e:
                    run_id_saved = None
                    print(f"[TOOL] web_search_saved: parse_error={e!r}")

                if run_id_saved is None:
                    result = self._tr_tools("tools.web_search_saved.skipped")
                else:
                    print(f"[TOOL] invoke web_search_saved: run_id={run_id_saved}")
                    result = self.call_tool(
                        "web_search_saved",
                        {"run_id": run_id_saved},
                        repository=self.window.repository,
                    )
                    print(f"[TOOL] web_search_saved result: {str(result)[:600]}")
                self._append_tool_message(sc, str(result), call_id)
                if not str(result).startswith(self._tr_tools("tools.web_search_saved.skipped")[:24]):
                    self._web_research_touched_this_turn = True
            elif fn_name == "web_fetch_url":
                try:
                    raw_args = json.loads(fn_args_json)
                    url = (raw_args.get("url") or "").strip()
                    run_id = raw_args.get("run_id")
                    route_mode = (raw_args.get("route_mode") or "auto").strip().lower()
                    print(f"[TOOL] web_fetch_url: url={url!r} run_id={run_id!r} route_mode={route_mode!r}")
                except Exception as e:
                    url = ""
                    run_id = None
                    route_mode = "auto"
                    print(f"[TOOL] web_fetch_url: parse_error={e!r} -> empty url")

                if not url:
                    result = self._tr_tools("tools.web_fetch.skipped_empty")
                    self._web_fetch_dedupe_streak = 0
                else:
                    url_key = normalize_research_url(url)
                    if url_key and url_key in self._web_fetch_urls_ok_this_turn:
                        self._web_fetch_dedupe_streak += 1
                        result = self._tr_tools("tools.web_fetch.dedupe", url=url)
                        print(f"[TOOL] web_fetch_url: dedupe skip url={url!r} key={url_key!r}")
                    else:
                        self._web_fetch_dedupe_streak = 0
                        print(f"[TOOL] invoke web_fetch_url: url={url!r}, run_id={run_id!r}, route_mode={route_mode!r}")
                        args = {"url": url, "route_mode": route_mode}
                        if run_id is not None:
                            args["run_id"] = run_id
                        result = self.call_tool(fn_name, args, repository=self.window.repository)
                        rs = str(result)
                        if (
                            self._tr_tools("tools.web_fetch.error_prefix") not in rs
                            and self._tr_tools("tools.web_fetch.empty_url") not in rs
                        ):
                            self._web_fetch_urls_ok_this_turn.add(url_key)
                            self._web_fetch_success_this_turn += 1
                        print(f"[TOOL] web_fetch_url result: {rs[:600]}")
                self._append_tool_message(sc, str(result), call_id)
                self._web_research_touched_this_turn = True

            self._start_followup_worker(
                sc,
                m_info,
                allow_tools=self._allow_tools_for_followup(allow_more_tools),
                last_completed_tool=fn_name,
            )
            return

        self.window.hide_thinking_indicator()
        full_response = strip_degenerate_token_runs(
            strip_leading_channel_thought_preamble(strip_leading_tool_results_echo(full_response))
        )
        self._tool_chain_count = 0
        self._web_fetch_dedupe_streak = 0
        self._web_fetch_success_this_turn = 0
        self._web_research_touched_this_turn = False
        self._last_tool_name = None
        self._tool_history_truncated_this_turn = False
        self.last_pair = (self.current_user_text, full_response)
        repo.save_interaction(self.current_user_text, full_response)
        repo.add_chat_message(sc.current_session_id, "assistant", full_response)
        sc.history.append(Message(role="assistant", content=full_response))

        if self.window.pending_switch is not None:
            self.window._finish_pending_switch()
        else:
            self.window.voice_controller.process_voice_tail()

        self._log_model_emotion_probe(full_response)
        self._retire_model_worker(self.worker)
        self.worker = None
        self._set_model_worker_busy(False)

    def mark_last_interaction_for_memory(self):
        if self.last_pair is None:
            self.window.inject_message("model", self._tr_ui("[Nothing to save to memory yet]"))
            return
        u_input, a_output = self.last_pair
        ok = self.window.repository.set_verified_for_pair(u_input, a_output, 1)
        if ok:
            self._promote_pair_to_long_term(u_input, a_output)
            self.window.inject_message("model", self._tr_ui("[System: Marked for memory]"))
        else:
            self.window.inject_message("model", self._tr_ui("[No history entry for this pair]"))

    def _promote_pair_to_long_term(self, user_text: str, assistant_text: str) -> None:
        """Immediately add/update one verified pair in long_term_memory."""
        try:
            query_vector = self.semantic_engine.embedder.encode(user_text).tolist()
            existing_id = self.window.repository.find_similar_knowledge(query_vector, threshold=0.92)
            if existing_id:
                self.window.repository.delete_long_term_entry(existing_id)
            full_text = f"Q: {user_text} A: {assistant_text}"
            full_vector = self.semantic_engine.embedder.encode(full_text).tolist()
            self.window.repository.add_long_term_entry(user_text, assistant_text, full_vector)
        except Exception as e:
            print(f"[MEMORY] immediate promote failed: {e!r}", flush=True)

    def save_experience(self):
        if self.last_pair is None:
            pass
            self.window.inject_message("model", self._tr_ui("[Nothing to save yet]"))
            # Optional UI notification
            return
        else:
            # 1. Read data from the last pair
            u_input, a_output = self.last_pair

            m_info = self.window.config_repo.get_active_model_info()
            persona = self.window.config_repo.get_persona_prompt(m_info)

            pass
            try:
                # 2. Pass u_input and a_output
                # Argument names must match dataset_saver.py definition

                save_alpaca_data(user_input=u_input, assistant_output=a_output, instruction=persona)

                # 3. Update DB status
                self.window.repository.update_last_interaction_status(1)
                self.window.inject_message("model", self._tr_ui("[System: Saved]"))

            except Exception:
                pass

    def handle_pending_image(self, base64_data):
        # Existing image processing (resize, etc.)
        file_path, final_b64 = self.process_incoming_image(base64_data)

        if final_b64:
            self.pending_attachments.append(
                {
                    "id": self._new_attachment_id(),
                    "kind": "image",
                    "path": file_path,
                    "b64": final_b64,
                }
            )

    def handle_send_message(self, text):
        """Same path as WebChannel (bridge): one worker, correct thread handoff."""
        self.process_web_message(text)

    def process_incoming_image(self, base64_data: str, *, save_as: str | None = None):
        """Process image and save under structured session folder."""
        try:
            # 1. Prepare data (Pillow)
            if "," in base64_data:
                header, encoded = base64_data.split(",", 1)
            else:
                encoded = base64_data

            image_bytes = base64.b64decode(encoded)
            img = Image.open(io.BytesIO(image_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # Resize for vision
            max_size = 1024
            if max(img.size) > max_size:
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

            # 2. BUILD STRUCTURED PATH
            m_info = self.window.model_controller.get_active_model_info()
            s_id = self.window.session_controller.current_session_id
            if s_id is None and m_info.model_class in ("text-to-image", "image-edit"):
                s_id = "gallery"
            elif s_id is None:
                self.window.session_controller.ensure_session()
                s_id = self.window.session_controller.current_session_id or "default"

            # Safe model type name (no spaces)
            safe_type = m_info.model_type.lower().replace(" ", "_").replace("-", "_")

            media_path = str(lira_data("media", f"{safe_type}-{m_info.id}", s_id))
            os.makedirs(media_path, exist_ok=True)
            self._log_attachment_path("session media dir", media_path)

            if save_as:
                filename = os.path.basename(save_as)
            else:
                filename = f"img_{datetime.now().strftime('%H%M%S_%f')}.jpg"
            file_path = os.path.join(media_path, filename)

            # 3. Save
            img.save(file_path, "JPEG", quality=85)

            # Prepare base64 for the model
            buffered = io.BytesIO()
            img.save(buffered, format="JPEG")
            final_b64 = f"data:image/jpeg;base64,{base64.b64encode(buffered.getvalue()).decode()}"

            print(f"[Attachment] saved {file_path!r} ({img.size[0]}x{img.size[1]})", flush=True)
            return file_path, final_b64

        except Exception as e:
            print(
                f"[Attachment] process_incoming_image: {e}\n{traceback.format_exc()}",
                flush=True,
            )
            return None, base64_data

    def cancel_pending_qwen_edit(self) -> None:
        """Clear Qwen edit queue on model switch (else wait_image_gen_idle hangs until timeout)."""
        self._pending_qwen_edit = None
        self._qwen_edit_main_busy = False

    def wait_image_gen_idle(self, timeout_ms: int = 300_000) -> None:
        """Wait for background image gen before model switch/unload."""
        w = self._image_gen_worker
        if w is not None and w.isRunning():
            w.wait(timeout_ms)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while self._qwen_edit_main_busy and time.monotonic() < deadline:
            from PyQt6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.processEvents()
            time.sleep(0.02)

    def _image_gen_busy(self) -> bool:
        w = self._image_gen_worker
        return w is not None and w.isRunning()

    def _image_gen_or_qwen_main_busy(self) -> bool:
        return self._image_gen_busy() or self._qwen_edit_main_busy

    def _start_image_gen_worker(self, worker: ImageGenerationWorker) -> bool:
        if self._image_gen_or_qwen_main_busy():
            self.window.inject_message(
                "model",
                self._tr_ui("⏳ Wait for the current generation or edit to finish."),
            )
            return False
        self._image_gen_worker = worker
        worker.succeeded.connect(self._on_image_gen_succeeded)
        worker.failed.connect(self._on_image_gen_failed)
        worker.empty.connect(self._on_image_gen_empty)
        worker.keepalive.connect(self._on_image_gen_keepalive)
        worker.finished.connect(self._on_image_gen_worker_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        return True

    def _on_image_gen_keepalive(self) -> None:
        from infrastructure.runtime.qt_keepalive import pump_qt_events

        pump_qt_events()

    def _on_image_gen_worker_finished(self) -> None:
        self._image_gen_worker = None

    def _on_image_gen_succeeded(self, full_path: str, prompt: str, negative: str, model_name: str) -> None:
        file_url = "file://" + full_path.replace("\\", "/")
        self.window.display_art_on_canvas(file_url, prompt)
        gen_id = self.window.gallery_repo.add_generation(
            prompt=prompt,
            negative_prompt=negative,
            file_path=full_path,
            model_name=model_name,
        )
        if gen_id:
            print(
                f"[GALLERY] saved generation id={gen_id} path={full_path!r}",
                flush=True,
            )

    def _on_image_gen_failed(self, message: str) -> None:
        self.window.inject_message("model", self._tr_ui("⚠️ An error occurred: {message}", message=message))

    def _on_image_gen_empty(self) -> None:
        self.window.inject_message("model", self._tr_ui("❌ Error: model returned no image."))

    def process_image_generation(self, prompt, negative="", ratio="1:1"):
        mc = self.window.model_controller
        sc = self.window.session_controller
        m_info = mc.get_active_model_info()
        self.window.interrupt_voice()

        if mc.llm is None:
            self.window.inject_message("model", self._tr_ui("❌ Model not loaded yet."))
            return

        sc.ensure_session()

        safe_model_name = "".join([c if c.isalnum() else "_" for c in m_info.name])
        model_dir = os.path.join(sc.get_session_media_dir(), safe_model_name)
        os.makedirs(model_dir, exist_ok=True)

        file_name = f"gen_{int(time.time())}.png"
        full_path = os.path.join(model_dir, file_name)

        gen_kwargs = {
            "user_prompt": prompt,
            "negative_prompt": negative,
            "aspect_ratio": ratio,
        }
        worker = ImageGenerationWorker(
            mc.llm,
            gen_kwargs,
            full_path,
            prompt,
            negative or "",
            m_info.name,
        )
        self._start_image_gen_worker(worker)

    def set_image_edit_primary(self, b64: str, path: str) -> None:
        self.pending_image_edit_primary = {"b64": b64, "path": path}

    def set_image_edit_secondary(self, b64: str, path: str) -> None:
        self.pending_image_edit_secondary = {"b64": b64, "path": path}

    def clear_image_edit_secondary(self) -> None:
        self.pending_image_edit_secondary = None

    def clear_image_edit_primary(self) -> None:
        self.pending_image_edit_primary = None

    def process_image_edit_generation(
        self, prompt: str, source_image_path: str, source_image_path_2: str | None = None
    ):
        """Edit via Qwen Image Edit (GGUF); second path optional (multi-image in diffusers)."""
        mc = self.window.model_controller
        sc = self.window.session_controller
        m_info = mc.get_active_model_info()
        self.window.interrupt_voice()

        if mc.llm is None:
            self.window.inject_message("model", self._tr_ui("❌ Model not loaded yet."))
            return

        if self._image_gen_or_qwen_main_busy():
            self.window.inject_message(
                "model",
                self._tr_ui("⏳ Wait for the current generation or edit to finish."),
            )
            return

        sc.ensure_session()

        safe_model_name = "".join([c if c.isalnum() else "_" for c in m_info.name])
        model_dir = os.path.join(sc.get_session_media_dir(), safe_model_name)
        os.makedirs(model_dir, exist_ok=True)

        file_name = f"edit_{int(time.time())}.png"
        full_path = os.path.join(model_dir, file_name)

        gen_kwargs = {
            "user_prompt": prompt,
            "negative_prompt": "",
            "aspect_ratio": "1:1",
            "source_image_path": source_image_path,
            "source_image_path_2": source_image_path_2,
        }
        # Qwen + accelerate: pipe() must run on the same Qt GUI thread as enable_model_cpu_offload.
        self._qwen_edit_main_busy = True
        self._pending_qwen_edit = (mc.llm, gen_kwargs, full_path, prompt, m_info.name)
        QTimer.singleShot(0, self._run_pending_qwen_edit_on_main_thread)

    def _run_pending_qwen_edit_on_main_thread(self) -> None:
        pending = self._pending_qwen_edit
        self._pending_qwen_edit = None
        if pending is None:
            self._qwen_edit_main_busy = False
            return
        llm, gen_kwargs, full_path, prompt, model_name = pending
        try:
            output = llm.generate(**gen_kwargs)
            if not output:
                self._on_image_gen_empty()
                return
            img = output[0] if isinstance(output, list) and len(output) > 0 else output
            img.save(full_path)
            self._on_image_gen_succeeded(full_path, prompt, "", model_name)
        except Exception as e:
            try:
                from infrastructure.model_backends.image_qwen.diag_log import (
                    qwen_diag_append,
                )

                qwen_diag_append("chat_controller._run_pending_qwen_edit_on_main_thread:\n" + traceback.format_exc())
            except Exception:
                pass
            self._on_image_gen_failed(str(e))
        finally:
            self._unlink_qwen_edit_staging_files(gen_kwargs)
            self._qwen_edit_main_busy = False
