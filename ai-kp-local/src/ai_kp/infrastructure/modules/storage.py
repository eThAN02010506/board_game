"""Content-addressed private storage for module sources and extracted assets."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


class ModuleDocumentStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def store_source(self, data: bytes, source_hash: str, suffix: str) -> str:
        return self._store(data, source_hash, f"sources/{source_hash[:2]}", suffix)

    def store_asset(self, data: bytes, suffix: str) -> tuple[str, str]:
        content_hash = hashlib.sha256(data).hexdigest()
        path = self._store(data, content_hash, f"assets/{content_hash[:2]}", suffix)
        return content_hash, path

    def resolve(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if self.root not in path.parents:
            raise ValueError("Module asset path escapes its storage root")
        return path

    def _store(self, data: bytes, digest: str, folder: str, suffix: str) -> str:
        safe_suffix = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
        if not safe_suffix[1:].isalnum() or len(safe_suffix) > 12:
            safe_suffix = ".bin"
        directory = self.root / folder
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{digest}{safe_suffix}"
        if destination.exists():
            return destination.relative_to(self.root).as_posix()
        temporary = directory / f".{digest}.{os.getpid()}.partial"
        try:
            temporary.write_bytes(data)
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination.relative_to(self.root).as_posix()
