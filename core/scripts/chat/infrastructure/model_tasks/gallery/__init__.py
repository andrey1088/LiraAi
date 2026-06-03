"""Gallery tasks: frame captions, embedding, subprocess."""

from infrastructure.model_tasks.gallery.capabilities import model_supports_gallery_describe
from infrastructure.model_tasks.gallery.embedder import GalleryEmbedder, get_gallery_embedder
from infrastructure.model_tasks.gallery.process import GalleryDescribeProcess
from infrastructure.model_tasks.gallery.quality import (
    gallery_describe_retry_intro,
    is_bad_gallery_description,
    normalize_gallery_locale,
    resolve_gallery_description_locale,
    sanitize_gallery_description,
    should_redescribe_gallery_lead,
    starts_with_cyrillic_letters_or_tags,
    starts_with_english_letters_or_tags,
    starts_with_wrong_locale_lead,
)
from infrastructure.model_tasks.gallery.settings import load_gallery_describe_settings

__all__ = [
    "GalleryDescribeProcess",
    "GalleryEmbedder",
    "get_gallery_embedder",
    "gallery_describe_retry_intro",
    "is_bad_gallery_description",
    "load_gallery_describe_settings",
    "model_supports_gallery_describe",
    "normalize_gallery_locale",
    "resolve_gallery_description_locale",
    "sanitize_gallery_description",
    "should_redescribe_gallery_lead",
    "starts_with_cyrillic_letters_or_tags",
    "starts_with_english_letters_or_tags",
    "starts_with_wrong_locale_lead",
]
