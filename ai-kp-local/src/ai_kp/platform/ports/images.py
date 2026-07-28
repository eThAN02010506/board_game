"""Provider-neutral contracts for map background image generation."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class MapImageRequest:
    prompt: str
    width: int
    height: int
    seed: int | None = None
    output_format: str = "png"


@dataclass(frozen=True)
class MapImageResult:
    content: bytes
    mime_type: str
    width: int
    height: int
    seed: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredMapAsset:
    content_hash: str
    storage_path: str
    mime_type: str
    width: int
    height: int
    size_bytes: int


class MapImageProvider(Protocol):
    provider_id: str
    model_id: str

    async def generate(self, request: MapImageRequest) -> MapImageResult: ...


class MapAssetStore(Protocol):
    def write(self, content: bytes) -> StoredMapAsset: ...

    def exists(self, relative_path: str) -> bool: ...
