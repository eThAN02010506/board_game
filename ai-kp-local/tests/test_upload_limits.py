import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from ai_kp.api.uploads import read_limited_body, safe_upload_filename


def _request(chunks: list[bytes], *, content_length: str | None = None) -> Request:
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]

    async def receive():
        return messages.pop(0)

    headers = []
    if content_length is not None:
        headers.append((b"content-length", content_length.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": headers,
        },
        receive,
    )


def test_limited_body_rejects_declared_size_before_streaming() -> None:
    request = _request([b"ignored"], content_length="11")

    with pytest.raises(HTTPException) as captured:
        asyncio.run(read_limited_body(request, max_bytes=10, label="测试文件"))

    assert captured.value.status_code == 413


def test_limited_body_rejects_chunked_body_at_boundary() -> None:
    request = _request([b"12345", b"678901"])

    with pytest.raises(HTTPException) as captured:
        asyncio.run(read_limited_body(request, max_bytes=10, label="测试文件"))

    assert captured.value.status_code == 413


def test_limited_body_returns_content_within_limit() -> None:
    request = _request([b"12345", b"67890"])

    assert asyncio.run(
        read_limited_body(request, max_bytes=10, label="测试文件")
    ) == b"1234567890"


def test_safe_upload_filename_removes_paths_and_rejects_controls() -> None:
    assert safe_upload_filename("../资料/人物卡.xlsx", default="fallback.xlsx") == "人物卡.xlsx"
    assert safe_upload_filename(r"C:\资料\人物卡.xlsx", default="fallback.xlsx") == "人物卡.xlsx"
    with pytest.raises(HTTPException):
        safe_upload_filename("bad%00name.pdf", default="fallback.pdf")
