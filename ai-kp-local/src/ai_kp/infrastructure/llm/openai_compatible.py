"""OpenAI-compatible HTTP model adapter."""

import httpx

from ai_kp.platform.ports.llm import ChatMessage


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 300,
        max_tokens: int = 4096,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens

    async def complete(self, messages: list[ChatMessage], temperature: float = 0.7) -> str:
        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
            "messages": [{"role": msg.role, "content": msg.content} for msg in messages],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text.replace("\n", " ")[:1000]
                raise RuntimeError(
                    f"LLM HTTP {response.status_code} from {self.base_url}: {detail}"
                ) from exc
            data = response.json()
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
            raise RuntimeError(
                "LLM returned empty final content "
                f"(finish_reason={choice.get('finish_reason')!r}, "
                f"reasoning_characters={len(str(reasoning))})"
            )
        return content
