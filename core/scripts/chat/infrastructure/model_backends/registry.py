"""Backend factory by model_class from config."""

from __future__ import annotations

import os
from typing import Callable

from infrastructure.model_backends.image_qwen.generator import QwenImageEditGenerator
from infrastructure.model_backends.image_sd.generator import ImageGenerator
from infrastructure.model_backends.text_llama import load_text_llama
from infrastructure.paths import resolve_path

MODEL_CLASS_TEXT_TO_IMAGE = "text-to-image"
MODEL_CLASS_IMAGE_EDIT = "image-edit"


def load_model_backend(
    model_data,
    *,
    interruption_requested: Callable[[], bool] | None = None,
) -> object:
    """
    Create and optionally initialize backend for AIModel.
    Returns Llama, ImageGenerator, or QwenImageEditGenerator.
    """
    model_path = resolve_path(model_data.model_path)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"model file not found: {model_path}")

    mc = model_data.model_class

    if mc == MODEL_CLASS_TEXT_TO_IMAGE:
        return ImageGenerator(model_data)

    if mc == MODEL_CLASS_IMAGE_EDIT:
        generator = QwenImageEditGenerator(model_data)
        generator.load_weights_in_background()
        if interruption_requested and interruption_requested():
            try:
                generator.close()
            except Exception:
                pass
            raise InterruptedError("Qwen load interrupted")
        return generator

    return load_text_llama(model_data)
