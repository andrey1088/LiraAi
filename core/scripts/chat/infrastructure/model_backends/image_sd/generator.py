import math
import sys

from stable_diffusion_cpp import StableDiffusion

from infrastructure.model_backends.image_sd.cuda_probe import log_sd_backend_hint, stable_diffusion_linked_cuda
from infrastructure.paths import resolve_path


def _sd_init_kwargs(settings: dict, lora_dir: str) -> dict:
    """stable-diffusion.cpp init (see stable-diffusion-cpp-python README, SD_CUDA=ON)."""
    # VAE decode on GPU can reserve multi-GB (see log: vae compute buffer ~7GB) → OOM after
    # sampling on 16 GB cards. Default decode on CPU unless config sets keep_vae_on_cpu: false.
    keep_vae = settings.get("keep_vae_on_cpu")
    if keep_vae is None:
        keep_vae = True
    else:
        keep_vae = bool(keep_vae)
    return {
        "lora_model_dir": lora_dir,
        "offload_params_to_cpu": bool(settings.get("sd_offload_to_cpu", False)),
        "keep_clip_on_cpu": bool(settings.get("keep_clip_on_cpu", False)),
        "keep_vae_on_cpu": keep_vae,
        "keep_control_net_on_cpu": bool(settings.get("keep_control_net_on_cpu", False)),
        "diffusion_flash_attn": bool(settings.get("diffusion_flash_attn", False)),
        "flash_attn": bool(settings.get("flash_attn", False)),
        "verbose": bool(settings.get("sd_verbose", True)),
        "rng_type": "cuda",
        "sampler_rng_type": "cuda",
        "n_threads": int(settings.get("sd_n_threads", -1)),
    }


def _align8(n: int) -> int:
    """stable-diffusion.cpp expects dimensions aligned (multiple of 8)."""
    return max(64, (int(n) // 8) * 8)


def _aspect_pair(pair) -> tuple[int, int] | None:
    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
        return int(pair[0]), int(pair[1])
    return None


def _iso_area_dimensions(area: int, aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "16:9":
        h = int(math.sqrt(area * 9 / 16))
        w = int(math.sqrt(area * 16 / 9))
        return _align8(w), _align8(h)
    if aspect_ratio == "9:16":
        w = int(math.sqrt(area * 9 / 16))
        h = int(math.sqrt(area * 16 / 9))
        return _align8(w), _align8(h)
    return _align8(int(math.sqrt(area))), _align8(int(math.sqrt(area)))


def _default_sd_aspect_sizes(base_w: int, base_h: int) -> dict[str, tuple[int, int]]:
    """Fallback when keys are missing from settings.sd_aspect_sizes."""
    area = base_w * base_h
    return {
        "1:1": (_align8(base_w), _align8(base_h)),
        "16:9": _iso_area_dimensions(area, "16:9"),
        "9:16": _iso_area_dimensions(area, "9:16"),
    }


def _merged_sd_aspect_sizes(settings: dict) -> dict[str, tuple[int, int]]:
    """
    Output sizes per UI aspect ratio.

    Primary source: settings.sd_aspect_sizes in config.json (слот text-to-image).
    Missing keys are filled from width/height: 1:1 = base, 16:9/9:16 = same pixel area as 1:1.
    """
    base_w = int(settings.get("width", 768))
    base_h = int(settings.get("height", 768))
    merged = _default_sd_aspect_sizes(base_w, base_h)

    custom = settings.get("sd_aspect_sizes")
    if not isinstance(custom, dict):
        return merged

    for key, pair in custom.items():
        parsed = _aspect_pair(pair)
        if parsed is not None:
            merged[str(key)] = (_align8(parsed[0]), _align8(parsed[1]))
    return merged


def _sd_dimensions(settings: dict, aspect_ratio: str) -> tuple[int, int]:
    sizes = _merged_sd_aspect_sizes(settings)
    return sizes.get(aspect_ratio) or sizes["1:1"]


class ImageGenerator:
    def __init__(self, model_data):
        self.model_data = model_data
        self.settings = model_data.settings or {}
        self.model_path = resolve_path(model_data.model_path)
        self.lora_path = resolve_path(model_data.lora_path)
        self.vae_path = ""
        self.llm_path = ""
        self.cuda_build = stable_diffusion_linked_cuda()

        log_sd_backend_hint()

        if model_data.vae_path:
            self.vae_path = resolve_path(model_data.vae_path)

        if model_data.llm_path:
            self.llm_path = resolve_path(model_data.llm_path)

        sd_kwargs = _sd_init_kwargs(self.settings, self.lora_path)

        print(
            f"[SD] loading model_path={self.model_path!r} cuda_build={self.cuda_build} "
            f"offload_cpu={sd_kwargs['offload_params_to_cpu']} "
            f"keep_vae_on_cpu={sd_kwargs['keep_vae_on_cpu']}",
            file=sys.stderr,
            flush=True,
        )

        if model_data.vae_path:
            self.sd = StableDiffusion(
                diffusion_model_path=self.model_path,
                vae_path=self.vae_path,
                llm_path=self.llm_path,
                **sd_kwargs,
            )
        else:
            self.sd = StableDiffusion(model_path=self.model_path, **sd_kwargs)

        if not self.cuda_build:
            print(
                "[SD] Генерация пойдёт на CPU. Для GPU см. docs/image-generation.md",
                file=sys.stderr,
                flush=True,
            )

    def generate(self, user_prompt: str, negative_prompt: str = "", aspect_ratio: str = "1:1"):
        neg = negative_prompt if negative_prompt else self.settings.get("negative_prompt", "")
        w, h = _sd_dimensions(self.settings, aspect_ratio)

        pixels = w * h
        print(
            f"[SD] generate {w}x{h} ({pixels} px) aspect={aspect_ratio!r} "
            f"steps={self.settings.get('steps', 40)} (watch nvidia-smi during sampling)",
            file=sys.stderr,
            flush=True,
        )
        if pixels > 1_050_000:
            print(
                f"[SD] warning: {pixels} px > ~1.05M (1024²); on 16 GB VRAM may OOM → gray/blank image",
                file=sys.stderr,
                flush=True,
            )

        output = self.sd.generate_image(
            prompt=user_prompt,
            negative_prompt=neg,
            width=w,
            height=h,
            sample_steps=self.settings.get("steps", 40),
            cfg_scale=self.settings.get("cfg_scale", 7.0),
            sample_method="euler_a",
        )

        return output
