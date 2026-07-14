from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class LlmClient(Protocol):
    async def complete(self, messages: list[ChatMessage], temperature: float = 0.7) -> str:
        raise NotImplementedError

