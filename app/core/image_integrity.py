"""Pure image-integrity validation by magic bytes (header + footer).

A truncated download (hang killed mid-stream, proxy drop, partial body) leaves
a file that *looks* present on disk but is unopenable. The canonical symptom is
a correct header with a missing footer. This module checks both ends cheaply —
two small reads, no full decode — so it is fast enough to gate every download
and every skip.

``validate_image_file(path, fmt) -> (ok: bool, reason: str)`` is the only
public entry point. ``fmt`` is derived from the file extension at the call
site; it is normalized here (case-folded, leading dot stripped, ``jpeg`` ->
``jpg``). ``ok=True`` means the file passed; ``ok=False`` comes with a short
Traditional-Chinese reason for logging.
"""
from __future__ import annotations

import os

# Below this many bytes a file cannot be a real image — treat as truncated.
MIN_IMAGE_BYTES = 64

# How many trailing bytes to scan for a footer marker (covers encoder padding).
_FOOTER_SCAN = 32

# Magic-byte signatures.
_JPEG_HEADER = b"\xff\xd8\xff"
_JPEG_FOOTER = b"\xff\xd9"
_PNG_HEADER = b"\x89PNG\r\n\x1a\n"
_PNG_IEND = b"IEND\xae\x42\x60\x82"
_GIF_HEADERS = (b"GIF87a", b"GIF89a")
_GIF_TRAILER = 0x3B
_JXL_CODESTREAM = b"\xff\x0a"
_JXL_CONTAINER = b"\x00\x00\x00\x0cJXL \x0d\x0a\x87\x0a"


def _normalize_fmt(fmt: str) -> str:
    f = str(fmt or "").strip().lower().lstrip(".")
    if f == "jpeg":
        return "jpg"
    return f


def _read_head_tail(path: str, head_n: int, tail_n: int) -> tuple[bytes, bytes, int]:
    """Return ``(head, tail, size)`` reading at most ``head_n`` / ``tail_n``
    bytes from each end without slurping the whole file."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(head_n)
        if size > tail_n:
            fh.seek(-tail_n, os.SEEK_END)
            tail = fh.read(tail_n)
        else:
            tail = head if size <= head_n else fh.read()
    return head, tail, size


def _validate_jpeg(head: bytes, tail: bytes) -> tuple[bool, str]:
    if not head.startswith(_JPEG_HEADER):
        return False, "JPEG 檔頭不符"
    # Tolerate trailing padding after the EOI marker.
    if _JPEG_FOOTER not in tail and not tail.rstrip(b"\x00").endswith(_JPEG_FOOTER):
        return False, "JPEG 缺少結尾標記（檔案不完整）"
    return True, ""


def _validate_png(head: bytes, tail: bytes) -> tuple[bool, str]:
    if not head.startswith(_PNG_HEADER):
        return False, "PNG 檔頭不符"
    if _PNG_IEND not in tail:
        return False, "PNG 缺少 IEND 區塊（檔案不完整）"
    return True, ""


def _validate_gif(head: bytes, tail: bytes) -> tuple[bool, str]:
    if not head.startswith(_GIF_HEADERS):
        return False, "GIF 檔頭不符"
    stripped = tail.rstrip(b"\x00")
    if not stripped or stripped[-1] != _GIF_TRAILER:
        return False, "GIF 缺少結尾標記（檔案不完整）"
    return True, ""


def _validate_jxl(head: bytes) -> tuple[bool, str]:
    # JXL has no simple footer; signature + size floor is the contract.
    if head.startswith(_JXL_CODESTREAM) or head.startswith(_JXL_CONTAINER):
        return True, ""
    return False, "JXL 檔頭不符"


def validate_image_file(path: str, fmt: str) -> tuple[bool, str]:
    """Validate an image file by header + footer magic bytes.

    Returns ``(ok, reason)``. ``ok=False`` for: missing file, size below
    :data:`MIN_IMAGE_BYTES`, an unrecognized ``fmt``, a wrong header, or a
    missing footer (the truncated-download signature). ``reason`` is a short
    Traditional-Chinese string for logging when ``ok`` is False, ``""`` when ok.
    """
    f = _normalize_fmt(fmt)
    if f not in ("jpg", "png", "gif", "jxl"):
        return False, f"未知格式：{fmt}"
    try:
        if not os.path.isfile(path):
            return False, "檔案不存在"
        head, tail, size = _read_head_tail(path, MIN_IMAGE_BYTES, _FOOTER_SCAN)
    except OSError as err:
        return False, f"讀取失敗：{err}"
    if size < MIN_IMAGE_BYTES:
        return False, f"檔案過小（{size} bytes，疑似截斷）"
    if f == "jpg":
        return _validate_jpeg(head, tail)
    if f == "png":
        return _validate_png(head, tail)
    if f == "gif":
        return _validate_gif(head, tail)
    return _validate_jxl(head)
