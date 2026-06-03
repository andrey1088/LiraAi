"""Render PDF pages to PIL.Image for vision models (Gemma + clip)."""

from __future__ import annotations

import io

from PIL import Image

MAX_PDF_VISION_PAGES = 6
MAX_PDF_PAGE_SIDE = 1024


def render_pdf_pages(
    raw: bytes,
    *,
    max_pages: int = MAX_PDF_VISION_PAGES,
    max_side: int = MAX_PDF_PAGE_SIDE,
) -> list[Image.Image]:
    try:
        import fitz  # pymupdf
    except ImportError as e:
        raise RuntimeError("For PDF vision install: pip install pymupdf") from e

    doc = fitz.open(stream=raw, filetype="pdf")
    try:
        n = min(doc.page_count, max(1, max_pages))
        # ~144 DPI — readable page text without huge tensors
        matrix = fitz.Matrix(2.0, 2.0)
        out: list[Image.Image] = []
        for i in range(n):
            pix = doc[i].get_pixmap(matrix=matrix, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            if max(img.size) > max_side:
                img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            out.append(img)
        return out
    finally:
        doc.close()
