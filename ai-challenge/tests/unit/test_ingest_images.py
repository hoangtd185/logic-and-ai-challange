"""Large uploaded images are re-encoded as JPEG before being sent; small ones are not touched."""

import io
import sys

import pytest
from PIL import Image

from medextract.config import IMAGE_MAX_SIDE, IMAGE_REENCODE_ABOVE_BYTES
from medextract.ingest import load_bytes, shrink_image


def png(size, mode="L", noise=True):
    img = Image.effect_noise(size, 60).convert(mode) if noise else Image.new(mode, size, 255)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def jpeg(size, quality=95):
    buf = io.BytesIO()
    Image.effect_noise(size, 60).convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def test_small_png_is_sent_unchanged():
    data = png((400, 300), noise=False)
    assert len(data) < IMAGE_REENCODE_ABOVE_BYTES
    page = shrink_image(data, "image/png")
    assert page.data == data and page.mime_type == "image/png" and page.note == ""


def test_large_png_scan_becomes_jpeg():
    data = png((1240, 1750))
    assert len(data) > IMAGE_REENCODE_ABOVE_BYTES
    page = shrink_image(data, "image/png")
    assert page.mime_type == "image/jpeg" and len(page.data) < len(data)
    assert Image.open(io.BytesIO(page.data)).size == (1240, 1750)  # same resolution
    assert page.note.startswith("re-encoded PNG")


def test_huge_photo_is_scaled_down():
    data = jpeg((4000, 3000))
    page = shrink_image(data, "image/jpeg")
    assert max(Image.open(io.BytesIO(page.data)).size) == IMAGE_MAX_SIDE
    assert "scaled to 2400x1800" in page.note


def test_transparent_png_is_flattened_on_white():
    img = Image.new("RGBA", (1400, 1400), (0, 0, 0, 0))
    img.paste(Image.effect_noise((700, 700), 80).convert("RGBA"), (0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    page = shrink_image(buf.getvalue(), "image/png")
    out = Image.open(io.BytesIO(page.data))
    assert out.mode == "RGB" and out.getpixel((1300, 1300))[0] > 240  # was transparent → white


def test_without_pillow_images_are_sent_as_is(monkeypatch):
    data = png((1240, 1750))
    monkeypatch.setitem(sys.modules, "PIL", None)  # import fails
    page = shrink_image(data, "image/png")
    assert page.data == data and page.mime_type == "image/png"


def test_load_bytes_uses_it():
    doc = load_bytes(png((1240, 1750)), "scan.png")
    assert doc.source_mime_type == "image/png" and doc.pages[0].mime_type == "image/jpeg"


def test_unreadable_image_bytes_are_passed_through():
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * (IMAGE_REENCODE_ABOVE_BYTES + 10)
    page = shrink_image(data, "image/png")
    assert page.data == data
