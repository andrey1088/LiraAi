"""Extract text from attachments (txt, pdf, …) for LLM without vision."""

from __future__ import annotations

import io

MAX_ATTACHMENT_BYTES = 6 * 1024 * 1024
MAX_DOCUMENT_BYTES = MAX_ATTACHMENT_BYTES
MAX_DOCUMENT_CHARS = 24_000


def _truncate(text: str) -> str:
    if len(text) <= MAX_DOCUMENT_CHARS:
        return text
    return text[:MAX_DOCUMENT_CHARS] + "\n\n[… document truncated by character limit …]"


def _decode_text_bytes(raw: bytes, name: str) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _validate_pdf_bytes(raw: bytes) -> None:
    head = raw.lstrip()[:8]
    if not head.startswith(b"%PDF"):
        raise ValueError("File does not look like a valid PDF (corrupt or truncated). Try again; limit 6 MB.")


def _extract_pdf(raw: bytes) -> str:
    _validate_pdf_bytes(raw)
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as e:
        raise RuntimeError("Install PDF support: pip install pypdf") from e

    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
    except PdfReadError as e:
        raise ValueError(f"Could not read PDF: {e}") from e
    parts: list[str] = []
    for page in reader.pages[:40]:
        chunk = page.extract_text() or ""
        if chunk.strip():
            parts.append(chunk)
    if not parts:
        return "[PDF has no extractable text — may be scans only; attach pages as JPG/PNG.]"
    return "\n\n".join(parts)


def extract_document_text(filename: str, raw: bytes) -> str:
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"File too large (max. {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB).")

    name = (filename or "document").lower()
    if name.endswith(".pdf"):
        text = _extract_pdf(raw)
    elif name.endswith((".txt", ".md", ".markdown", ".csv", ".log", ".json", ".xml", ".html", ".htm")):
        text = _decode_text_bytes(raw, name)
    else:
        raise ValueError(
            f"Format «{filename}» not supported. Text: .txt, .md, .csv; PDF: .pdf; images: JPG, PNG, WebP."
        )

    text = text.strip()
    if not text:
        raise ValueError(f"No text found in file «{filename}».")
    return _truncate(text)
