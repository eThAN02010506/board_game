import httpx

from ai_kp.llm.base import ChatMessage


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def complete(self, messages: list[ChatMessage], temperature: float = 0.7) -> str:
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [{"role": msg.role, "content": msg.content} for msg in messages],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]

