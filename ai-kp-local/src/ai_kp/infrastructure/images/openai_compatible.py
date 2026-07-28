"""OpenAI-compatible ``/images/generations`` map image adapter."""

from __future__ import annotations

import base64
import binascii
import json

import httpx

from ai_kp.infrastructure.images.storage import MAX_IMAGE_BYTES, inspect_raster_image
from ai_kp.platform.ports.images import MapImageRequest, MapImageResult


class OpenAICompatibleImageProvider:
    provider_id = "openai_compatible_images"
    max_response_bytes = 48 * 1024 * 1024

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 300,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_id = model
        self.timeout_seconds = timeout_seconds

    async def generate(self, request: MapImageRequest) -> MapImageResult:
        payload = {
            "model": self.model_id,
            "prompt": request.prompt,
            "n": 1,
            "size": f"{request.width}x{request.height}",
            "response_format": "b64_json",
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            response_content, status_code = await self._post_bounded(payload, headers)
        except httpx.RequestError as exc:
            raise RuntimeError(f"无法连接图片模型：{exc}") from exc
        if status_code < 200 or status_code >= 300:
            detail = response_content.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"图片模型返回 HTTP {status_code}：{detail.replace(chr(10), ' ')[:1000]}"
            )
        try:
            data = json.loads(response_content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("图片模型返回了无效 JSON") from exc
        items = data.get("data", []) if isinstance(data, dict) else []
        encoded = items[0].get("b64_json") if items and isinstance(items[0], dict) else None
        if not isinstance(encoded, str) or not encoded:
            raise RuntimeError("图片模型没有返回 b64_json；本地安全模式不下载远程 URL")
        max_encoded_length = ((MAX_IMAGE_BYTES + 2) // 3) * 4
        if len(encoded) > max_encoded_length:
            raise RuntimeError("图片模型返回的 Base64 图片超过 32 MiB 上限")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("图片模型返回了无效的 Base64 数据") from exc
        try:
            image = inspect_raster_image(content)
        except ValueError as exc:
            raise RuntimeError(f"图片模型返回了无效位图：{exc}") from exc
        metadata = {
            "revised_prompt": items[0].get("revised_prompt"),
        }
        return MapImageResult(
            content=content,
            mime_type=image["mime_type"],
            width=image["width"],
            height=image["height"],
            seed=request.seed,
            metadata=metadata,
        )

    async def _post_bounded(
        self,
        payload: dict,
        headers: dict[str, str],
    ) -> tuple[bytes, int]:
        body = bytearray()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/images/generations",
                json=payload,
                headers=headers,
            ) as response:
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        declared_size = 0
                    if declared_size > self.max_response_bytes:
                        raise RuntimeError("图片模型响应超过 48 MiB 上限")
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.max_response_bytes:
                        raise RuntimeError("图片模型响应超过 48 MiB 上限")
                return bytes(body), response.status_code
