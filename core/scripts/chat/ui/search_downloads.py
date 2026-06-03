"""Search mode download directory and safe file names."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from infrastructure.paths import lira_data

DOWNLOADS_DIR = str(lira_data("downloads"))

_GENERIC_STEMS = frozenset(
    {
        "download",
        "image",
        "img",
        "file",
        "untitled",
        "blob",
        "data",
        "saved image",
        "saved image alt",
    }
)

_MIME_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/x-ms-bmp": ".bmp",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
}

_FORMAT_ALIASES: dict[str, str] = {
    "jpg": ".jpg",
    "jpeg": ".jpg",
    "jpe": ".jpg",
    "png": ".png",
    "gif": ".gif",
    "webp": ".webp",
    "bmp": ".bmp",
    "svg": ".svg",
    "avif": ".avif",
    "ico": ".ico",
    "pdf": ".pdf",
}

_URL_EXT_RE = re.compile(
    r"\.(jpe?g|png|gif|webp|bmp|svg|avif|ico|pdf)(?:\?|#|$)",
    re.IGNORECASE,
)


def ensure_downloads_dir() -> str:
    path = Path(DOWNLOADS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def open_downloads_folder_in_os() -> tuple[bool, str]:
    """Open download folder in OS file manager."""
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices

    path = ensure_downloads_dir()
    ok = QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    return ok, path


def sanitize_filename(name: str) -> str:
    base = os.path.basename((name or "").strip()) or ""
    base = unquote(base)
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base)
    return base[:200] if len(base) > 200 else base


def has_file_extension(name: str) -> bool:
    _, ext = os.path.splitext(name)
    if not ext or len(ext) < 2:
        return False
    body = ext[1:].lower()
    return body.isalnum() and 1 <= len(body) <= 8


def extension_from_mime(mime: str) -> str | None:
    if not mime:
        return None
    key = mime.split(";")[0].strip().lower()
    if key in _MIME_EXT:
        return _MIME_EXT[key]
    if key.startswith("image/"):
        sub = key.split("/", 1)[1]
        return _FORMAT_ALIASES.get(sub) or f".{sub.split('+')[0]}"
    return None


def extension_from_url(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.split("#", 1)[0])
    path = unquote(parsed.path or "")
    _, ext = os.path.splitext(path)
    if ext:
        norm = ext.lower()
        if norm == ".jpeg":
            return ".jpg"
        if has_file_extension("x" + norm):
            return norm

    match = _URL_EXT_RE.search(url)
    if match:
        token = match.group(1).lower()
        return _FORMAT_ALIASES.get(token, f".{token}")

    query = parse_qs(parsed.query)
    for key in ("format", "fmt", "ext", "type", "f"):
        values = query.get(key)
        if not values:
            continue
        token = values[0].strip().lower().lstrip(".")
        if token in _FORMAT_ALIASES:
            return _FORMAT_ALIASES[token]
    return None


def _is_generic_name(name: str) -> bool:
    stem = os.path.splitext(name)[0].strip().lower()
    return not stem or stem in _GENERIC_STEMS


def _fallback_stem(url: str) -> str:
    digest = hashlib.sha256((url or "download").encode("utf-8", errors="replace")).hexdigest()
    return f"image_{digest[:10]}"


def resolve_download_filename(suggested: str, url: str, mime: str = "") -> str:
    """File name with extension for QWebEngineDownloadRequest."""
    suggested_clean = sanitize_filename(suggested)
    url_clean = sanitize_filename(os.path.basename(urlparse((url or "").split("#")[0]).path or ""))

    name = suggested_clean
    if _is_generic_name(name) or not has_file_extension(name):
        if url_clean and not _is_generic_name(url_clean):
            if has_file_extension(url_clean):
                name = url_clean
            elif not _is_generic_name(name):
                stem = os.path.splitext(name)[0] or url_clean
                name = stem
            else:
                name = os.path.splitext(url_clean)[0] or url_clean
        elif _is_generic_name(name):
            name = ""

    stem, cur_ext = os.path.splitext(name)
    if _is_generic_name(stem):
        stem = ""

    ext = cur_ext.lower() if cur_ext else ""
    if ext == ".jpeg":
        ext = ".jpg"
    if not ext:
        ext = extension_from_mime(mime) or extension_from_url(url) or ""
    if not ext and (mime or "").lower().startswith("image/"):
        ext = ".jpg"
    if not ext and _looks_like_image_url(url):
        ext = ".jpg"
    if not ext and (mime or "").lower() == "application/pdf":
        ext = ".pdf"

    if not stem:
        stem = os.path.splitext(url_clean)[0] if url_clean and not _is_generic_name(url_clean) else ""
    if not stem or _is_generic_name(stem):
        stem = _fallback_stem(url)

    if not ext:
        ext = ".bin"

    return f"{stem}{ext}"


def _looks_like_image_url(url: str) -> bool:
    u = (url or "").lower()
    if not u.startswith(("http://", "https://")):
        return False
    if _URL_EXT_RE.search(u):
        return True
    hints = ("/image", "/images/", "/img/", "/photo", "/photos/", "/media/", "/static/", "imgurl=", "imgur.com/")
    return any(h in u for h in hints)


def sniff_extension_from_file(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            header = f.read(64)
    except OSError:
        return None
    if len(header) < 3:
        return None
    if header[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return ".webp"
    if header[:2] == b"BM":
        return ".bmp"
    if header[:4] == b"%PDF":
        return ".pdf"
    head = header.lstrip()
    if head.startswith(b"<") and b"<svg" in header[:512].lower():
        return ".svg"
    return None


def finalize_download_path(path: str, mime: str = "") -> str:
    """Rename file without extension after download completes."""
    if not path or not os.path.isfile(path):
        return path
    base = os.path.basename(path)
    if has_file_extension(base):
        return path

    ext = extension_from_mime(mime) or sniff_extension_from_file(path)
    if not ext:
        ext = ".jpg"

    directory = os.path.dirname(path)
    new_path = unique_path(directory, f"{base}{ext}")
    if new_path == path:
        return path
    try:
        os.rename(path, new_path)
    except OSError:
        return path
    return new_path


def unique_path(directory: str, filename: str) -> str:
    ensure_downloads_dir()
    safe = sanitize_filename(filename)
    path = Path(directory) / safe
    if not path.exists():
        return str(path)
    stem = path.stem
    suffix = path.suffix
    for i in range(1, 10_000):
        candidate = Path(directory) / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return str(candidate)
    return str(Path(directory) / f"{stem}_{os.getpid()}{suffix}")
