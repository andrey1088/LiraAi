import sys

from stable_diffusion_cpp import StableDiffusion

from infrastructure.model_backends.image_sd.cuda_probe import log_sd_backend_hint, stable_diffusion_linked_cuda
from infrastructure.paths import resolve_path


def _sd_init_kwargs(settings: dict, lora_dir: str) -> dict:
    """stable-diffusion.cpp init (see stable-diffusion-cpp-python README, SD_CUDA=ON)."""
    return {
        "lora_model_dir": lora_dir,
        "offload_params_to_cpu": bool(settings.get("sd_offload_to_cpu", False)),
        "keep_clip_on_cpu": bool(settings.get("keep_clip_on_cpu", False)),
        "keep_vae_on_cpu": bool(settings.get("keep_vae_on_cpu", False)),
        "keep_control_net_on_cpu": bool(settings.get("keep_control_net_on_cpu", False)),
        "diffusion_flash_attn": bool(settings.get("diffusion_flash_attn", False)),
        "flash_attn": bool(settings.get("flash_attn", False)),
        "verbose": bool(settings.get("sd_verbose", True)),
        "rng_type": "cuda",
        "sampler_rng_type": "cuda",
        "n_threads": int(settings.get("sd_n_threads", -1)),
    }


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
            f"offload_cpu={sd_kwargs['offload_params_to_cpu']}",
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

        ratios = {"1:1": (768, 768), "16:9": (1216, 832), "9:16": (832, 1216)}
        w, h = ratios.get(aspect_ratio, (768, 768))

        print(
            f"[SD] generate {w}x{h} steps={self.settings.get('steps', 40)} "
            f"(смотрите nvidia-smi во время сэмплинга)",
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
