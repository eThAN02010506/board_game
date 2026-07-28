"""Bounded raw-body upload helpers for trusted local document import routes."""

from __future__ import annotations

import re
from urllib.parse import unquote

from fastapi import HTTPException, Request


MAX_UPLOAD_FILENAME_CHARACTERS = 255
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


async def read_limited_body(
    request: Request,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    """Read a raw request body without buffering beyond ``max_bytes``."""

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = -1
        if declared_size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{label}超过 {max_bytes // (1024 * 1024)} MiB 限制",
            )

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{label}超过 {max_bytes // (1024 * 1024)} MiB 限制",
            )
    return bytes(body)


def safe_upload_filename(encoded: str | None, *, default: str) -> str:
    """Decode an informational upload name and discard path/control characters."""

    filename = unquote(encoded or default).replace("\\", "/").rsplit("/", 1)[-1].strip()
    if (
        not filename
        or len(filename) > MAX_UPLOAD_FILENAME_CHARACTERS
        or _CONTROL_CHARACTERS.search(filename)
    ):
        raise HTTPException(status_code=400, detail="上传文件名无效或过长")
    return filename
