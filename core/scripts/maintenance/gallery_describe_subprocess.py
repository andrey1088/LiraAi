#!/usr/bin/env python3
"""
Batch-describe gallery frames in a separate process.
Does not touch the GUI LLM; CPU-only by default (n_gpu_layers=0).
Progress lines: NDJSON on stdout.
"""
from __future__ import annotations

import gc
import json
import os
import sys
import tempfile

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
CHAT_DIR = os.path.join(PROJECT_ROOT, "core", "scripts", "chat")
if CHAT_DIR not in sys.path:
    sys.path.insert(0, CHAT_DIR)

def _persona_prompt(job: dict, key: str) -> str:
    from infrastructure.persona.store import PersonaStore

    path = job.get("persona_file")
    if path:
        return PersonaStore.get_prompt_from_file(path, key)
    intro = job.get("intro")
    if key == "gallery_describe_intro" and intro:
        return str(intro)
    return PersonaStore.get_prompt_from_file(None, key)


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _log_stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _prepare_image_url(image_path: str, max_side: int) -> tuple[str, str | None]:
    from PIL import Image

    expanded = os.path.abspath(os.path.expanduser(image_path))
    img = Image.open(expanded)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    tmp_path: str | None = None
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="lira_gd_sub_")
        os.close(fd)
        img.save(tmp_path, "JPEG", quality=82)
        return "file://" + tmp_path, tmp_path
    return "file://" + expanded, None


def _load_llm(job: dict):
    from llama_cpp import Llama
    from infrastructure.templates.gemma3_vision import Gemma3ChatHandler
    from infrastructure.templates.gemma4_vision import Gemma4ChatHandler

    model_path = os.path.expanduser(job["model_path"])
    clip_path = job.get("clip_model_path")
    clip_path = os.path.expanduser(clip_path) if clip_path else None
    if not os.path.isfile(model_path):
        raise FileNotFoundError(model_path)
    if not clip_path or not os.path.isfile(clip_path):
        raise FileNotFoundError(f"clip/mmproj not found: {clip_path!r}")

    n_gpu_layers = int(job.get("n_gpu_layers", 0))
    n_ctx = int(job.get("n_ctx", 8192))
    if n_gpu_layers == 0:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    model_type = (job.get("model_type") or "").strip()
    template_path = job.get("template_path")
    template_path = os.path.expanduser(template_path) if template_path else None

    if model_type == "Gemma-4-26B":
        handler = Gemma4ChatHandler(
            clip_model_path=clip_path,
            verbose=False,
            template_path=template_path,
        )
    else:
        handler = Gemma3ChatHandler(clip_model_path=clip_path, verbose=False)

    return Llama(
        model_path=model_path,
        chat_handler=handler,
        clip_model_path=clip_path,
        n_gpu_layers=n_gpu_layers,
        n_ctx=n_ctx,
        flash_attn=n_gpu_layers != 0,
        verbose=False,
        type_k=8,
        type_v=8,
    )


def _message_content(response: dict) -> str:
    try:
        msg = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""
    content = msg.get("content")
    if content is None:
        content = msg.get("text")
    return "" if content is None else str(content)


def _vision_completion(
    llm,
    *,
    job: dict,
    intro: str,
    file_url: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """Same path as chat vision: chat_handler + multimodal user content."""
    vision_content = [
        {"type": "text", "text": intro},
        {"type": "image_url", "image_url": {"url": file_url}},
    ]
    handler = getattr(llm, "chat_handler", None)
    if handler is None:
        raise RuntimeError("model has no chat_handler (vision)")

    if hasattr(llm, "reset"):
        llm.reset()

    return handler(
        llama=llm,
        messages=[
            {"role": "system", "content": _persona_prompt(job, "gallery_describe_system")},
            {"role": "user", "content": vision_content},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        stream=False,
    )


def _finalize_description(
    raw: str, *, gen_id: int, image_path: str, locale: str = "ru"
) -> str:
    from infrastructure.model_tasks.gallery.quality import (
        is_bad_gallery_description,
        normalize_gallery_locale,
        sanitize_gallery_description,
    )

    loc = normalize_gallery_locale(locale)
    text = sanitize_gallery_description(raw)
    if text and not is_bad_gallery_description(text, loc):
        return text
    if raw and raw.strip():
        _log_stderr(
            f"[gallery_describe] rejected id={gen_id} path={image_path!r} "
            f"raw={raw[:200]!r} sanitized={text!r}"
        )
    elif raw is not None:
        _log_stderr(
            f"[gallery_describe] empty model output id={gen_id} path={image_path!r}"
        )
    return ""


def _describe_one(llm, image_path: str, job: dict) -> str:
    from infrastructure.runtime.llm_cuda_hygiene import release_llm_cuda_cache
    from infrastructure.model_tasks.gallery.quality import (
        gallery_describe_retry_intro,
        normalize_gallery_locale,
    )

    max_side = max(256, int(job.get("max_side", 384)))
    max_tokens = max(96, int(job.get("max_tokens", 200)))
    intro = (job.get("intro") or _persona_prompt(job, "gallery_describe_intro")).strip()
    ui_locale = normalize_gallery_locale(job.get("ui_locale"))
    gen_id = int(job.get("_current_gen_id", 0))
    tmp_path: str | None = None
    try:
        file_url, tmp_path = _prepare_image_url(image_path, max_side)
        attempts = (
            (intro, max_tokens, 0.45),
            (
                gallery_describe_retry_intro(ui_locale),
                max(max_tokens, 220),
                0.55,
            ),
        )
        for attempt_idx, (prompt, tokens, temp) in enumerate(attempts):
            response = _vision_completion(
                llm,
                job=job,
                intro=prompt,
                file_url=file_url,
                max_tokens=tokens,
                temperature=temp,
            )
            raw = _message_content(response)
            text = _finalize_description(
                raw, gen_id=gen_id, image_path=image_path, locale=ui_locale
            )
            if text:
                if attempt_idx > 0:
                    _log_stderr(
                        f"[gallery_describe] ok on retry {attempt_idx} id={gen_id}"
                    )
                return text
        return ""
    finally:
        release_llm_cuda_cache(llm, deep=True)
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def run_job(job_path: str) -> int:
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)

    items = job.get("items") or []
    _emit({"type": "started", "total": len(items)})

    llm = None
    try:
        llm = _load_llm(job)
    except Exception as e:
        _emit({"type": "error", "message": f"load model: {e}"})
        return 1

    from infrastructure.runtime.llm_cuda_hygiene import release_llm_cuda_cache

    done = 0
    deep_every = max(1, int(job.get("cuda_deep_every", 1)))
    try:
        for row in items:
            gen_id = int(row["id"])
            path = row.get("path") or ""
            expanded = os.path.expanduser(path)
            if not os.path.isfile(expanded):
                _emit(
                    {
                        "type": "item",
                        "id": gen_id,
                        "path": expanded,
                        "description": "",
                        "skipped": True,
                    }
                )
                done += 1
                continue
            desc = ""
            try:
                item_job = dict(job)
                item_job["_current_gen_id"] = gen_id
                desc = _describe_one(llm, expanded, item_job)
            except Exception as e:
                _emit({"type": "item_error", "id": gen_id, "message": str(e)})
                _emit({"type": "error", "message": str(e)})
                return 2
            _emit(
                {
                    "type": "item",
                    "id": gen_id,
                    "path": expanded,
                    "description": desc,
                    "skipped": False,
                }
            )
            done += 1
            if done % deep_every == 0:
                release_llm_cuda_cache(llm, deep=True)
    finally:
        if llm is not None:
            try:
                if hasattr(llm, "close") and callable(llm.close):
                    llm.close()
            except Exception:
                pass
            del llm
        gc.collect()
        from infrastructure.runtime.llm_cuda_hygiene import _empty_torch_cuda_cache

        _empty_torch_cuda_cache()

    _emit({"type": "done", "done": done})
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: gallery_describe_subprocess.py <job.json>", file=sys.stderr)
        return 2
    return run_job(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
