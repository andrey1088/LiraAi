"""Gallery task availability for active model."""

from __future__ import annotations


def model_supports_gallery_describe(m_info) -> bool:
    """Chat vision model (clip) — describe buttons in sidebar."""
    if m_info is None:
        return False
    if getattr(m_info, "model_class", None) in ("text-to-image", "image-edit"):
        return False
    return bool((getattr(m_info, "clip_model_path", None) or "").strip())
