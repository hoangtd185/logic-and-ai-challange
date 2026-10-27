"""Turn an input file into page images the vision model can read.

- Images (PNG, JPEG, WebP) are passed through unchanged unless they are large:
  anything over IMAGE_REENCODE_ABOVE_BYTES, or with a side over IMAGE_MAX_SIDE, is
  re-encoded as JPEG (quality 85, scaled down if needed, EXIF rotation applied).
  That cuts a typical scan from ~1.2 MB to ~160 KB of upload without visible loss.
  If Pillow is missing, images are sent as they are.
- PDFs are rendered page by page to PNG with PyMuPDF. The text layer is kept as
  well (empty for scanned PDFs), for later evidence checks.

The file type is detected from the file's first bytes, not its extension.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

from medextract.config import (
    IMAGE_JPEG_QUALITY,
    IMAGE_MAX_SIDE,
    IMAGE_REENCODE_ABOVE_BYTES,
    MAX_INPUT_BYTES,
    MAX_PAGES,
    PDF_RENDER_DPI,
)

log = logging.getLogger(__name__)


class IngestError(ValueError):
    """The file can't be turned into page images."""


@dataclass(frozen=True)
class PageImage:
    data: bytes
    mime_type: str
    text: str = ""  # PDF text layer; "" for images and scanned PDFs
    note: str = ""  # what was done to the image before sending, e.g. "re-encoded PNG 917 KB → JPEG 164 KB"


@dataclass(frozen=True)
class Document:
    name: str
    source_mime_type: str
    pages: list[PageImage] = field(default_factory=list)
    sha256: str = ""

    @property
    def has_text_layer(self) -> bool:
        return any(p.text.strip() for p in self.pages)


_SIGNATURES = [
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
]


def sniff_mime_type(data: bytes) -> str | None:
    for magic, mime in _SIGNATURES:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def load_document(path: str | Path) -> Document:
    path = Path(path)
    return load_bytes(path.read_bytes(), name=path.name)


def load_bytes(data: bytes, name: str = "upload") -> Document:
    if not data:
        raise IngestError(f"{name}: file is empty")
    mime = sniff_mime_type(data)
    if mime is None:
        raise IngestError(f"{name}: unsupported file type (expected PDF, PNG, JPEG or WebP)")

    if mime == "application/pdf":
        pages = _render_pdf(data, name)
    else:
        pages = [shrink_image(data, mime)]

    total = sum(len(p.data) for p in pages)
    if total > MAX_INPUT_BYTES:
        raise IngestError(
            f"{name}: page images total {total / 1e6:.1f} MB, above the {MAX_INPUT_BYTES / 1e6:.0f} MB request limit"
        )
    return Document(name=name, source_mime_type=mime, pages=pages, sha256=hashlib.sha256(data).hexdigest())


def _render_pdf(data: bytes, name: str) -> list[PageImage]:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - older PyMuPDF only ships `fitz`
        import fitz as pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises its own error types
        raise IngestError(f"{name}: could not open PDF ({exc})") from exc

    with doc:
        if doc.needs_pass:
            raise IngestError(f"{name}: PDF is password-protected")
        if doc.page_count == 0:
            raise IngestError(f"{name}: PDF has no pages")
        if doc.page_count > MAX_PAGES:
            raise IngestError(f"{name}: {doc.page_count} pages, more than the {MAX_PAGES}-page limit")
        pages = []
        for page in doc:
            pixmap = page.get_pixmap(dpi=PDF_RENDER_DPI)
            pages.append(PageImage(data=pixmap.tobytes("png"), mime_type="image/png", text=page.get_text()))
    return pages


def shrink_image(data: bytes, mime: str) -> PageImage:
    """Re-encode a large image as JPEG; small ones are returned untouched."""
    if len(data) <= IMAGE_REENCODE_ABOVE_BYTES and mime == "image/jpeg":
        return PageImage(data=data, mime_type=mime)
    try:
        from PIL import Image, ImageOps
    except ImportError:
        log.warning("Pillow is not installed: sending the image as is (pip install -e . adds it)")
        return PageImage(data=data, mime_type=mime)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:  # unreadable by Pillow: let the model try the original bytes
        return PageImage(data=data, mime_type=mime)
    too_big = max(img.size) > IMAGE_MAX_SIDE
    if len(data) <= IMAGE_REENCODE_ABOVE_BYTES and not too_big:
        return PageImage(data=data, mime_type=mime)

    img = ImageOps.exif_transpose(img)
    if too_big:
        img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.LANCZOS)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        img = Image.new("RGB", rgba.size, "white")
        img.paste(rgba, mask=rgba.getchannel("A"))
    elif img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True)
    out = buf.getvalue()
    if len(out) >= len(data) and not too_big:
        return PageImage(data=data, mime_type=mime)
    kind = mime.split("/")[-1].upper()
    note = f"re-encoded {kind} {len(data) // 1024} KB → JPEG {len(out) // 1024} KB"
    if too_big:
        note += f", scaled to {img.size[0]}x{img.size[1]}"
    return PageImage(data=out, mime_type="image/jpeg", note=note)
