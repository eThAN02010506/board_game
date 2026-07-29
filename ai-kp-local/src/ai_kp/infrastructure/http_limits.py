"""Shared decoded-response limits for untrusted HTTP model providers."""

from __future__ import annotations

from typing import Any

import httpx

_READ_CHUNK_BYTES = 64 * 1024


async def request_bounded_bytes(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_bytes: int,
    limit_error: str,
    **request_kwargs: Any,
) -> tuple[bytes, int]:
    """Stream one response and stop before its decoded body exceeds ``max_bytes``."""

    body = bytearray()
    async with client.stream(method, url, **request_kwargs) as response:
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = 0
            if declared_size > max_bytes:
                raise RuntimeError(limit_error)
        async for chunk in response.aiter_bytes(chunk_size=_READ_CHUNK_BYTES):
            body.extend(chunk)
            if len(body) > max_bytes:
                raise RuntimeError(limit_error)
        return bytes(body), response.status_code
