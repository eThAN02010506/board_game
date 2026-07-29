"""OpenAI-compatible HTTP model adapter."""

import json
from typing import Any

import httpx

from ai_kp.infrastructure.http_limits import request_bounded_bytes
from ai_kp.platform.ports.llm import ChatMessage


def _is_gpt_oss(model: str) -> bool:
    return "gpt-oss" in model.casefold()


def _default_chat_template_kwargs(model: str) -> dict[str, Any] | None:
    """Return narrowly scoped hints for model families that require them."""

    if _is_gpt_oss(model):
        # gpt-oss always reasons. llama.cpp accepts this Harmony template hint
        # per request; low effort preserves a final-answer budget for local
        # structured-output tasks.
        return {"reasoning_effort": "low"}
    return None


class OpenAICompatibleClient:
    max_response_bytes = 8 * 1024 * 1024

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 300,
        max_tokens: int = 4096,
        chat_template_kwargs: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.chat_template_kwargs = (
            dict(chat_template_kwargs)
            if chat_template_kwargs is not None
            else _default_chat_template_kwargs(model)
        )
        self.client = client

    async def complete(self, messages: list[ChatMessage], temperature: float = 0.7) -> str:
        is_gpt_oss = _is_gpt_oss(self.model)
        payload = {
            "model": self.model,
            # OpenAI and llama.cpp both recommend neutral 1.0/1.0 sampling for
            # gpt-oss. Lower temperatures used by generic structured-output
            # callers can otherwise degrade this model family.
            "temperature": 1.0 if is_gpt_oss else temperature,
            "max_tokens": self.max_tokens,
            "messages": [{"role": msg.role, "content": msg.content} for msg in messages],
        }
        if is_gpt_oss:
            payload["top_p"] = 1.0
        if self.chat_template_kwargs:
            payload["chat_template_kwargs"] = self.chat_template_kwargs
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        try:
            response_content, status_code = await self._post_bounded(payload, headers)
            if (
                "chat_template_kwargs" in payload
                and status_code in {400, 422}
            ):
                # `chat_template_kwargs` is a llama.cpp extension, not part of
                # the OpenAI contract. Strict compatible providers get one
                # safe retry without the optional hint.
                standard_payload = dict(payload)
                standard_payload.pop("chat_template_kwargs")
                response_content, status_code = await self._post_bounded(
                    standard_payload,
                    headers,
                )
        except httpx.RequestError as exc:
            raise RuntimeError(f"无法连接模型服务 {self.base_url}：{exc}") from exc
        if status_code < 200 or status_code >= 300:
            detail = response_content.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"LLM HTTP {status_code} from {self.base_url}: "
                f"{detail.replace(chr(10), ' ')[:1000]}"
            )
        try:
            data = json.loads(response_content)
            choice = data["choices"][0]
            message = choice["message"]
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("模型服务返回了无效的 OpenAI-compatible JSON") from exc
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
            raise RuntimeError(
                "LLM returned empty final content "
                f"(finish_reason={choice.get('finish_reason')!r}, "
                f"reasoning_characters={len(str(reasoning))})"
            )
        return content

    async def _post_bounded(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[bytes, int]:
        if self.client is not None:
            return await self._stream_response(self.client, payload, headers)
        async with httpx.AsyncClient() as client:
            return await self._stream_response(client, payload, headers)

    async def _stream_response(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[bytes, int]:
        return await request_bounded_bytes(
            client,
            "POST",
            f"{self.base_url}/chat/completions",
            max_bytes=self.max_response_bytes,
            limit_error="模型服务响应超过 8 MiB 上限",
            json=payload,
            headers=headers,
            timeout=self.timeout_seconds,
        )
