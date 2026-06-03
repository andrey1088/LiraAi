"""Qwen Image Edit (GGUF) wrapper for ChatController interface."""

from __future__ import annotations

import os
import time

from PIL import Image

from infrastructure.paths import resolve_path
from infrastructure.model_backends.image_qwen.backend import (
    QwenImageEditBackend,
    _qwen_boot,
    _qwen_dprint,
)


class QwenImageEditGenerator:
    def __init__(self, model_data):
        self.model_data = model_data
        self.settings = model_data.settings or {}
        gguf = resolve_path(model_data.model_path)
        if not os.path.isfile(gguf):
            raise FileNotFoundError(f"Qwen GGUF not found: {gguf}")

        self._backend = QwenImageEditBackend(
            gguf_path=gguf,
            hf_repo_id=str(self.settings.get("hf_repo_id", "Qwen/Qwen-Image-Edit-2511")),
            torch_dtype_name=str(self.settings.get("dtype", "bfloat16")),
            placement=str(self.settings.get("placement", "model_offload")),
            text_encoder_gpu=bool(self.settings.get("text_encoder_gpu", False)),
            skip_accelerate_offload=bool(self.settings.get("skip_accelerate_offload", False)),
            text_encoder_encode_on_cpu=bool(self.settings.get("text_encoder_on_cpu", True)),
        )
        # Weights in ModelLoader (background); offload/hooks on GUI via finish_load_on_main_thread.

    def load_weights_in_background(self) -> None:
        """Call from ModelLoader.run (not GUI thread)."""
        self._backend.load_weights()

    def finish_load_on_main_thread(self) -> None:
        """Offload and hooks on GUI thread (after load_weights_in_background)."""
        self._backend.finalize_on_main_thread()

    def generate(
        self,
        user_prompt: str,
        negative_prompt: str = "",
        aspect_ratio: str = "1:1",
        source_image_path: str | None = None,
        source_image_path_2: str | None = None,
    ):
        """
        Returns one PIL.Image. source_image_path is required; source_image_path_2 optional
        (second frame for multi-image, as in QwenImageEditPlusPipeline).
        """
        del aspect_ratio  # reserved
        if not source_image_path or not os.path.isfile(source_image_path):
            raise ValueError("Qwen Image Edit requires source_image_path to an existing file")

        paths: list[str] = [source_image_path]
        if source_image_path_2 and os.path.isfile(source_image_path_2):
            paths.append(source_image_path_2)

        _qwen_boot(f"generate: open {paths!r}")
        neg = negative_prompt if negative_prompt else str(self.settings.get("negative_prompt", " "))
        _raw_steps = self.settings.get("steps", 8)
        steps = int(_raw_steps) if _raw_steps is not None else 8
        true_cfg = float(self.settings.get("true_cfg_scale", 4.0))
        guidance = float(self.settings.get("guidance_scale", 1.0))
        seed = int(self.settings.get("seed", time.time() * 1000)) % (2**31)

        _qwen_dprint(f"generate: paths={paths!r} steps={steps} true_cfg={true_cfg} guidance={guidance} seed={seed}")
        pil_images = [Image.open(p).convert("RGB") for p in paths]
        _qwen_dprint("generate: PIL open ok, calling backend.edit")
        return self._backend.edit(
            pil_images,
            user_prompt,
            num_inference_steps=steps,
            true_cfg_scale=true_cfg,
            guidance_scale=guidance,
            negative_prompt=neg,
            seed=seed,
        )

    def close(self) -> None:
        self._backend.close()
