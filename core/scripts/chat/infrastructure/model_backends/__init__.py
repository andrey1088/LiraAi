"""
Inference backends by model_class (config.json).

- text_llama — llama.cpp + Gemma vision handlers
- image_sd — Stable Diffusion (stable_diffusion_cpp)
- image_qwen — Qwen Image Edit (diffusers + GGUF)
"""

from infrastructure.model_backends.loader import ModelLoader
from infrastructure.model_backends.registry import (
    MODEL_CLASS_IMAGE_EDIT,
    MODEL_CLASS_TEXT_TO_IMAGE,
    load_model_backend,
)

__all__ = [
    "MODEL_CLASS_IMAGE_EDIT",
    "MODEL_CLASS_TEXT_TO_IMAGE",
    "ModelLoader",
    "load_model_backend",
]
