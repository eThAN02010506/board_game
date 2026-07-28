"""Validated, content-addressed storage for generated raster map assets."""

from __future__ import annotations

import tempfile
import warnings
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ai_kp.platform.ports.images import StoredMapAsset

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_777_216


def inspect_raster_image(content: bytes) -> dict[str, Any]:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("地图图片为空或超过 32 MiB 上限")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                image_format = image.format
                width, height = image.size
                if image_format not in {"PNG", "JPEG"}:
                    raise ValueError("地图图片必须是 PNG 或 JPEG 位图")
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("地图图片尺寸非法或像素数量超过上限")
                image.verify()
            # ``verify`` checks container integrity without decoding pixels. A second
            # open/load also rejects truncated streams and invalid compressed data.
            with Image.open(BytesIO(content)) as image:
                image.load()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("地图图片不是完整有效的 PNG 或 JPEG 位图") from exc
    mime_type = "image/png" if image_format == "PNG" else "image/jpeg"
    extension = "png" if image_format == "PNG" else "jpg"
    return {
        "content_hash": sha256(content).hexdigest(),
        "mime_type": mime_type,
        "extension": extension,
        "width": width,
        "height": height,
        "size_bytes": len(content),
    }


class MapAssetFileStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def write(self, content: bytes) -> StoredMapAsset:
        image = inspect_raster_image(content)
        relative = (
            Path("sha256")
            / str(image["content_hash"])[:2]
            / f"{image['content_hash']}.{image['extension']}"
        )
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=".map-asset-",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary_path = Path(temporary.name)
            temporary_path.replace(destination)
        return StoredMapAsset(
            content_hash=str(image["content_hash"]),
            storage_path=relative.as_posix(),
            mime_type=str(image["mime_type"]),
            width=int(image["width"]),
            height=int(image["height"]),
            size_bytes=int(image["size_bytes"]),
        )

    def exists(self, relative_path: str) -> bool:
        try:
            self.resolve(relative_path)
        except (KeyError, ValueError):
            return False
        return True

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("地图资产路径越界")
        if not candidate.is_file():
            raise KeyError("地图图片文件不存在")
        return candidate
