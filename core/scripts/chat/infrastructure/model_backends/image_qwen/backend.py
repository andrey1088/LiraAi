"""
Qwen-Image-Edit-2511: local GGUF transformer + Hugging Face pipeline weights.

Used by the app (QwenImageEditGenerator) and smoke scripts.

App inference runs on the Qt GUI thread (see ChatController): accelerate hooks
and CUDA must live on the same thread as pipe(), else TypeError (NoneType → C++ int).

On ~16GB VRAM placement=model_offload defaults to enable_model_cpu_offload();
full pipe.to("cuda") OOMs — only with placement=full_gpu or skip_accelerate_offload.

Logs: ~/Lira2/logs/qwen_image_edit.log (append) and ~/Lira2/logs/lira.log on gui start.

Default text_encoder_encode_on_cpu: drop accelerate hooks on text_encoder and encode on CPU
so Qwen2.5-VL is not fully on GPU next to GGUF (OOM on ~16GB).
After each pipe() diffusers calls maybe_free_model_hooks → enable_model_cpu_offload again; we patch
maybe_free to strip text_encoder hooks again (else the second edit OOMs).
"""

from __future__ import annotations

import gc
import inspect
import os
import sys
import threading
import traceback
import types
from collections.abc import Sequence
from typing import Any

from PIL import Image

from infrastructure.model_backends.image_qwen.diag_log import qwen_diag_append
from infrastructure.runtime.qt_keepalive import call_pipe_with_keepalive, pump_qt_events


def _qwen_boot(msg: str) -> None:
    """stderr + qwen_image_edit.log file (gio often hides console)."""
    print(f"[QwenEdit] {msg}", file=sys.stderr, flush=True)
    qwen_diag_append(msg)


def _qwen_dprint(msg: str) -> None:
    """Debug logging: export LIRA_QWEN_EDIT_DEBUG=1"""
    if os.environ.get("LIRA_QWEN_EDIT_DEBUG", "0").lower() not in ("1", "true", "yes", "on"):
        return
    print(f"[QwenEdit] {msg}", flush=True)


def make_synthetic_gradient_rgb(width: int, height: int) -> Image.Image:
    w, h = max(64, width), max(64, height)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (min(255, x // 2), min(255, y // 2), 128)
    return img


class QwenImageEditBackend:
    """Load pipeline once and run edit on PIL image."""

    def __init__(
        self,
        *,
        gguf_path: str,
        hf_repo_id: str = "Qwen/Qwen-Image-Edit-2511",
        torch_dtype_name: str = "bfloat16",
        placement: str = "model_offload",
        text_encoder_gpu: bool = False,
        skip_accelerate_offload: bool = False,
        text_encoder_encode_on_cpu: bool = True,
    ) -> None:
        self.gguf_path = gguf_path
        self.hf_repo_id = hf_repo_id
        self.torch_dtype_name = torch_dtype_name
        self.placement = placement
        self.text_encoder_gpu = text_encoder_gpu
        # Default model_offload = enable_model_cpu_offload (else OOM on ~16GB). Full pipe.to(cuda) —
        # only with placement=full_gpu or skip_accelerate_offload / LIRA_QWEN_SKIP_OFFLOAD=1.
        self.skip_accelerate_offload = skip_accelerate_offload or (
            os.environ.get("LIRA_QWEN_SKIP_OFFLOAD", "").strip() == "1"
        )
        # Encode prompt on CPU: else accelerate pulls full Qwen2.5-VL to GPU → OOM next to GGUF.
        self.text_encoder_encode_on_cpu = text_encoder_encode_on_cpu
        self._pipe: Any = None

    def _require_qt_gui_thread_for_finalize(self) -> None:
        try:
            from PyQt6.QtCore import QCoreApplication, QThread

            app = QCoreApplication.instance()
            if app is not None and QThread.currentThread() is not app.thread():
                raise RuntimeError(
                    "QwenImageEditBackend: offload/hooks only on Qt GUI thread; "
                    "call finish_load_on_main_thread from ModelController."
                )
        except ImportError:
            pass

    def load_weights(self) -> None:
        """Heavy weight load (ModelLoader / background) — no accelerate offload."""
        if self._pipe is not None:
            return

        _qwen_boot(f"load_weights: start thread={threading.current_thread().name!r} ident={threading.get_ident()}")

        import torch
        from diffusers import GGUFQuantizationConfig, QwenImageEditPlusPipeline, QwenImageTransformer2DModel

        torch_dtype = torch.bfloat16 if self.torch_dtype_name == "bfloat16" else torch.float16

        transformer = QwenImageTransformer2DModel.from_single_file(
            self.gguf_path,
            quantization_config=GGUFQuantizationConfig(compute_dtype=torch_dtype),
            torch_dtype=torch_dtype,
            config=self.hf_repo_id,
            subfolder="transformer",
        )

        pipe = QwenImageEditPlusPipeline.from_pretrained(
            self.hf_repo_id,
            transformer=transformer,
            torch_dtype=torch_dtype,
        )
        _qwen_boot("load_weights: from_pretrained OK")

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        for name in (
            "vae",
            "text_encoder",
            "text_encoder_2",
            "image_encoder",
            "unet",
        ):
            mod = getattr(pipe, name, None)
            if mod is not None and hasattr(mod, "to"):
                try:
                    mod.to("cpu")
                except Exception:
                    pass

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        if hasattr(pipe, "vae") and pipe.vae is not None and hasattr(pipe.vae, "enable_slicing"):
            pipe.vae.enable_slicing()

        self._pipe = pipe
        _qwen_boot("load_weights: complete")

    def finalize_on_main_thread(self) -> None:
        """Offload and hooks on GUI thread only (shorter than from_pretrained)."""
        if self._pipe is None:
            self.load_weights()
        if getattr(self._pipe, "_lira_qwen_finalized", False):
            return

        self._require_qt_gui_thread_for_finalize()
        pipe = self._pipe

        _qwen_boot(f"finalize_on_main_thread: start thread={threading.current_thread().name!r}")

        import warnings

        import torch

        if self.placement == "full_gpu":
            _qwen_boot("placement=full_gpu -> pipe.to('cuda')")
            pipe.to("cuda")
        elif self.placement == "model_offload":
            if self.skip_accelerate_offload:
                _qwen_boot(
                    "model_offload + skip_accelerate_offload -> pipe.to('cuda') (needs large VRAM headroom; else OOM)"
                )
                if torch.cuda.is_available():
                    pipe.to("cuda")
                else:
                    pipe.to("cpu")
            else:
                _qwen_boot("model_offload -> enable_model_cpu_offload()")
                try:
                    pipe.enable_model_cpu_offload()
                except BaseException as e:
                    _qwen_boot(
                        f"enable_model_cpu_offload failed ({type(e).__name__}: {e}); "
                        "fallback pipe.to('cuda') — OOM possible on 16GB"
                    )
                    warnings.warn(
                        f"enable_model_cpu_offload failed ({type(e).__name__}: {e}); falling back to pipe.to('cuda').",
                        UserWarning,
                        stacklevel=2,
                    )
                    if torch.cuda.is_available():
                        pipe.to("cuda")
                    else:
                        pipe.to("cpu")
        else:
            _qwen_boot(f"placement={self.placement!r} -> enable_model_cpu_offload (cuda fallback on error)")
            try:
                pipe.enable_model_cpu_offload()
            except BaseException as e:
                _qwen_boot(f"offload failed ({type(e).__name__}: {e}); pipe.to('cuda'|'cpu')")
                warnings.warn(
                    f"enable_model_cpu_offload failed ({type(e).__name__}: {e}); falling back.",
                    UserWarning,
                    stacklevel=2,
                )
                if torch.cuda.is_available():
                    pipe.to("cuda")
                else:
                    pipe.to("cpu")

        pump_qt_events()

        if self.text_encoder_gpu and torch.cuda.is_available():
            try:
                te = getattr(pipe, "text_encoder", None)
                if te is not None and hasattr(te, "to"):
                    te.to("cuda")
            except Exception as ex:
                warnings.warn(
                    f"text_encoder.to(cuda) skipped: {type(ex).__name__}: {ex}",
                    UserWarning,
                    stacklevel=2,
                )

        if self.text_encoder_encode_on_cpu and not self.text_encoder_gpu:
            from diffusers.pipelines.pipeline_utils import DiffusionPipeline

            orig_gpe = inspect.getattr_static(type(pipe), "_get_qwen_prompt_embeds")
            if isinstance(orig_gpe, staticmethod):
                orig_gpe = orig_gpe.__func__
            elif isinstance(orig_gpe, classmethod):
                orig_gpe = orig_gpe.__func__

            def _wrap_gpe(self, prompt, image, device):
                pump_qt_events()
                pe, mask = orig_gpe(self, prompt, image, torch.device("cpu"))
                pump_qt_events()
                tgt = device if device is not None else self._execution_device
                pe = pe.to(device=tgt)
                if mask is not None:
                    mask = mask.to(device=tgt)
                return pe, mask

            def _strip_te_and_install_gpe(p) -> None:
                from accelerate.hooks import remove_hook_from_module

                te = getattr(p, "text_encoder", None)
                if te is not None:
                    remove_hook_from_module(te, recurse=True)
                    te.to("cpu")
                p._get_qwen_prompt_embeds = types.MethodType(_wrap_gpe, p)

            def _mfh_te(self):
                # Default maybe_free re-hooks all components — without this the second edit OOMs on text_encoder.
                DiffusionPipeline.maybe_free_model_hooks(self)
                try:
                    _strip_te_and_install_gpe(self)
                except Exception as ex:
                    _qwen_boot(f"post-maybe_free TE->CPU: {type(ex).__name__}: {ex}")

            try:
                _strip_te_and_install_gpe(pipe)
                pipe.maybe_free_model_hooks = types.MethodType(_mfh_te, pipe)
                _qwen_boot("text_encoder: encode on CPU; after each pipe() maybe_free -> detach TE hooks again")
            except Exception as ex:
                warnings.warn(
                    f"text_encoder CPU-encode / maybe_free hook: {type(ex).__name__}: {ex}",
                    UserWarning,
                    stacklevel=2,
                )

        pipe._lira_qwen_finalized = True
        pump_qt_events()
        _qwen_boot("finalize_on_main_thread: complete")
        _qwen_dprint("finalize_on_main_thread: complete")

    def ensure_loaded(self) -> None:
        self.load_weights()
        self.finalize_on_main_thread()

    def edit(
        self,
        images: Image.Image | Sequence[Image.Image],
        prompt: str,
        *,
        num_inference_steps: int = 8,
        true_cfg_scale: float = 4.0,
        guidance_scale: float = 1.0,
        negative_prompt: str = " ",
        seed: int = 0,
    ) -> Image.Image:
        import torch

        if isinstance(images, Image.Image):
            pil_list = [images.convert("RGB")]
        else:
            pil_list = [im.convert("RGB") for im in images]
        if not pil_list or len(pil_list) > 2:
            raise ValueError("Qwen Image Edit: pass 1 or 2 images (PIL).")

        _qwen_boot(
            f"edit: thread={threading.current_thread().name!r} ident={threading.get_ident()} "
            f"cuda={torch.cuda.is_available()} n_images={len(pil_list)}"
        )
        _qwen_dprint(
            f"edit: thread={threading.current_thread().name!r} ident={threading.get_ident()} "
            f"cuda={torch.cuda.is_available()} n_images={len(pil_list)}"
        )
        if self._pipe is None or not getattr(self._pipe, "_lira_qwen_finalized", False):
            self.ensure_loaded()
        if torch.cuda.is_available():
            torch.cuda.set_device(0)

        gen_device = torch.device("cuda", 0) if torch.cuda.is_available() else torch.device("cpu")
        gen = torch.Generator(device=gen_device)
        gen.manual_seed(int(seed) & 0x7FFFFFFF)

        inputs = {
            "image": pil_list,
            "prompt": prompt,
            "generator": gen,
            "true_cfg_scale": float(true_cfg_scale),
            "negative_prompt": negative_prompt or " ",
            "num_inference_steps": int(num_inference_steps),
            "guidance_scale": float(guidance_scale),
            "num_images_per_prompt": 1,
        }
        _qwen_boot("edit: calling pipe(**inputs)")
        _qwen_dprint("edit: calling pipe(**inputs)")
        pump_qt_events()
        try:
            with torch.inference_mode():
                out = call_pipe_with_keepalive(self._pipe, **inputs)
        except BaseException:
            _qwen_boot("edit: FAILED\n" + traceback.format_exc())
            _qwen_dprint("edit: FAILED\n" + traceback.format_exc())
            raise
        _qwen_boot("edit: ok")
        _qwen_dprint("edit: ok")
        return out.images[0]

    def close(self) -> None:
        pipe = self._pipe
        self._pipe = None
        if pipe is not None:
            try:
                if hasattr(pipe, "remove_all_hooks"):
                    pipe.remove_all_hooks()
            except Exception:
                pass
            for name in (
                "transformer",
                "vae",
                "text_encoder",
                "text_encoder_2",
                "image_encoder",
                "unet",
            ):
                try:
                    setattr(pipe, name, None)
                except Exception:
                    pass
            try:
                del pipe
            except Exception:
                pass
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception:
            pass
