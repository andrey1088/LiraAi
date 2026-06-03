"""Reset KV and release CUDA cache after vision call batches (gallery)."""

from __future__ import annotations

import gc


def release_llm_cuda_cache(llm, *, deep: bool = False) -> None:
    """Reset llama context and return VRAM to the driver when possible."""
    if llm is not None:
        try:
            reset = getattr(llm, "reset", None)
            if callable(reset):
                reset()
        except Exception as exc:
            print(f"[LLM] reset: {exc!r}", flush=True)

    gc.collect()

    if not deep:
        return

    _empty_torch_cuda_cache()


def _empty_torch_cuda_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            ipc = getattr(torch.cuda, "ipc_collect", None)
            if callable(ipc):
                ipc()
    except Exception as exc:
        print(f"[LLM] cuda cache: {exc!r}", flush=True)
