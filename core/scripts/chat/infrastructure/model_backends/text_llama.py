"""Load text / vision LLM (llama.cpp + Gemma handlers)."""

from __future__ import annotations

import os

from llama_cpp import Llama

from infrastructure.templates.gemma3_vision import Gemma3ChatHandler
from infrastructure.templates.gemma4_vision import Gemma4ChatHandler
from infrastructure.templates.qwen3vl import Qwen3VLChatHandler, _is_qwen3_vl_model_type
from infrastructure.paths import resolve_path
from infrastructure.templates.qwen35_vl import Qwen35ChatHandler


def _is_qwen(model_type: str) -> bool:
    return "qwen" in (model_type or "").strip().lower()


def load_text_llama(model_data) -> Llama:
    model_path = resolve_path(model_data.model_path)
    if not os.path.isfile(model_path):
        raise FileNotFoundError(f"model file not found: {model_path}")

    clip_path = resolve_path(model_data.clip_model_path) if model_data.clip_model_path else None
    template_path = resolve_path(model_data.template_path) if model_data.template_path else None
    if not template_path:
        candidate_template = os.path.join(os.path.dirname(model_path), "chat_template.jinja")
        if os.path.isfile(candidate_template):
            template_path = candidate_template
    persona_file = resolve_path(model_data.persona_file) if model_data.persona_file else None

    settings = model_data.settings or {}
    n_ctx = int(settings.get("n_ctx", 8192))
    n_gpu_layers = int(settings.get("n_gpu_layers", -1))

    custom_handler = None
    if clip_path:
        if model_data.model_type == "Gemma-4-26B":
            custom_handler = Gemma4ChatHandler(
                clip_model_path=clip_path,
                verbose=False,
                template_path=template_path,
                persona_file=persona_file,
            )
        elif _is_qwen3_vl_model_type(model_data.model_type):
            custom_handler = Qwen3VLChatHandler(
                clip_model_path=clip_path,
                verbose=False,
                template_path=template_path,
                persona_file=persona_file,
                image_min_tokens=int(
                    settings.get(
                        "image_min_tokens",
                        Qwen3VLChatHandler.DEFAULT_IMAGE_MIN_TOKENS,
                    )
                ),
                image_max_tokens=int(settings.get("image_max_tokens", -1)),
            )
        elif _is_qwen(model_data.model_type):
            custom_handler = Qwen35ChatHandler(
                clip_model_path=clip_path,
                verbose=False,
                template_path=template_path,
                persona_file=persona_file,
                image_min_tokens=int(settings.get("image_min_tokens", Qwen35ChatHandler.DEFAULT_IMAGE_MIN_TOKENS)),
                image_max_tokens=int(settings.get("image_max_tokens", -1)),
            )
        else:
            custom_handler = Gemma3ChatHandler(
                clip_model_path=clip_path,
                verbose=False,
                template_path=template_path,
                model_path=model_path,
                persona_file=persona_file,
            )

    if _is_qwen(model_data.model_type):
        llm = Llama(
            model_path=model_path,
            chat_handler=custom_handler,
            clip_model_path=clip_path,
            n_gpu_layers=n_gpu_layers,
            n_ctx=n_ctx,
            flash_attn=True,
            verbose=False,
            n_batch=int(settings.get("n_batch", 3072)),
            n_ubatch=int(settings.get("n_ubatch", 1024)),
            **({"swa_full": bool(settings["swa_full"])} if "swa_full" in settings else {}),
        )
        if n_gpu_layers >= 0:
            print(
                f"[ModelLoader] {model_data.model_type}: n_gpu_layers={n_gpu_layers} "
                f"(remaining layers on CPU), n_ctx={n_ctx}, "
                f"n_batch={settings.get('n_batch', 3072)}, "
                f"n_ubatch={settings.get('n_ubatch', 1024)}",
                flush=True,
            )
        return llm

    llm = Llama(
        model_path=model_path,
        chat_handler=custom_handler,
        clip_model_path=clip_path,
        n_gpu_layers=n_gpu_layers,
        n_ctx=n_ctx,
        flash_attn=True,
        verbose=False,
        type_k=8,
        type_v=8,
    )
    if n_gpu_layers >= 0:
        print(
            f"[ModelLoader] {model_data.model_type}: n_gpu_layers={n_gpu_layers} "
            f"(remaining layers on CPU), n_ctx={n_ctx}",
            flush=True,
        )
    return llm
