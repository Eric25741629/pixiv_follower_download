"""Tests for the pure image-integrity validator (header+footer magic bytes)."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.image_integrity import validate_image_file


# ── byte fixtures ───────────────────────────────────────────────────────────

# A minimal-but-complete JPEG: SOI ... EOI. Padded body so it clears the floor.
_JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_JPEG_FOOTER = b"\xff\xd9"
JPEG_OK = _JPEG_HEADER + b"\x10" * 64 + _JPEG_FOOTER
JPEG_TRUNCATED = _JPEG_HEADER + b"\x10" * 64  # no EOI marker

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
PNG_OK = PNG_SIG + b"\x00" * 64 + PNG_IEND
PNG_TRUNCATED = PNG_SIG + b"\x00" * 64  # no IEND chunk

GIF_OK = b"GIF89a" + b"\x00" * 64 + b"\x3b"
GIF87_OK = b"GIF87a" + b"\x00" * 64 + b"\x3b"
GIF_TRUNCATED = b"GIF89a" + b"\x00" * 64  # no trailer byte

JXL_CODESTREAM = b"\xff\x0a" + b"\x00" * 128  # bare codestream signature
JXL_CONTAINER = b"\x00\x00\x00\x0cJXL \x0d\x0a\x87\x0a" + b"\x00" * 128


def _w(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ── valid files ─────────────────────────────────────────────────────────────

def test_valid_jpeg(tmp_path):
    ok, reason = validate_image_file(_w(tmp_path, "a.jpg", JPEG_OK), "jpg")
    assert ok is True
    assert reason == ""


def test_valid_png(tmp_path):
    ok, reason = validate_image_file(_w(tmp_path, "a.png", PNG_OK), "png")
    assert ok is True


def test_valid_gif_both_versions(tmp_path):
    assert validate_image_file(_w(tmp_path, "a.gif", GIF_OK), "gif")[0] is True
    assert validate_image_file(_w(tmp_path, "b.gif", GIF87_OK), "gif")[0] is True


def test_valid_jxl_codestream_and_container(tmp_path):
    assert validate_image_file(_w(tmp_path, "a.jxl", JXL_CODESTREAM), "jxl")[0] is True
    assert validate_image_file(_w(tmp_path, "b.jxl", JXL_CONTAINER), "jxl")[0] is True


def test_jpeg_tolerates_trailing_padding(tmp_path):
    # Some encoders append a few null bytes after EOI.
    data = JPEG_OK + b"\x00\x00\x00"
    ok, _ = validate_image_file(_w(tmp_path, "a.jpg", data), "jpg")
    assert ok is True


# ── truncated / invalid files ───────────────────────────────────────────────

def test_truncated_jpeg(tmp_path):
    ok, reason = validate_image_file(_w(tmp_path, "a.jpg", JPEG_TRUNCATED), "jpg")
    assert ok is False
    assert reason  # non-empty diagnostic


def test_truncated_png(tmp_path):
    ok, reason = validate_image_file(_w(tmp_path, "a.png", PNG_TRUNCATED), "png")
    assert ok is False


def test_truncated_gif(tmp_path):
    ok, reason = validate_image_file(_w(tmp_path, "a.gif", GIF_TRUNCATED), "gif")
    assert ok is False


def test_wrong_header_jpeg(tmp_path):
    ok, reason = validate_image_file(_w(tmp_path, "a.jpg", b"not a jpeg" + b"\x00" * 64), "jpg")
    assert ok is False


# ── missing / too-small ─────────────────────────────────────────────────────

def test_missing_file(tmp_path):
    ok, reason = validate_image_file(str(tmp_path / "nope.jpg"), "jpg")
    assert ok is False
    assert reason


def test_empty_file(tmp_path):
    ok, reason = validate_image_file(_w(tmp_path, "a.jpg", b""), "jpg")
    assert ok is False


def test_too_small_file(tmp_path):
    ok, reason = validate_image_file(_w(tmp_path, "a.jpg", b"\xff\xd8"), "jpg")
    assert ok is False


def test_jxl_too_small(tmp_path):
    # Correct signature but below the size floor -> not complete.
    ok, reason = validate_image_file(_w(tmp_path, "a.jxl", b"\xff\x0a"), "jxl")
    assert ok is False


# ── format normalization ────────────────────────────────────────────────────

def test_fmt_case_and_dot_insensitive(tmp_path):
    p = _w(tmp_path, "a.jpg", JPEG_OK)
    assert validate_image_file(p, "JPG")[0] is True
    assert validate_image_file(p, ".jpg")[0] is True
    assert validate_image_file(p, "jpeg")[0] is True


def test_unknown_format_is_invalid(tmp_path):
    ok, reason = validate_image_file(_w(tmp_path, "a.bin", JPEG_OK), "bin")
    assert ok is False
