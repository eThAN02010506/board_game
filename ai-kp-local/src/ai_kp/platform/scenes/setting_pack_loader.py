"""Bounded loader for user- or DLC-supplied setting packs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from ai_kp.platform.scenes.settlement_templates import SettingPack

MAX_SETTING_PACK_BYTES = 2 * 1024 * 1024


class SettingPackLoadError(ValueError):
    """The pack cannot be decoded or does not satisfy the public contract."""


def load_setting_pack(path: Path) -> tuple[SettingPack, str]:
    """Load one bounded JSON document and return its canonical content hash."""

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SettingPackLoadError(f"Cannot read setting pack: {path.name}") from exc
    if len(payload) > MAX_SETTING_PACK_BYTES:
        raise SettingPackLoadError("Setting pack exceeds the 2 MiB limit")
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettingPackLoadError("Setting pack must be valid UTF-8 JSON") from exc
    try:
        pack = SettingPack.model_validate(raw)
    except ValidationError as exc:
        raise SettingPackLoadError(f"Invalid setting pack: {exc}") from exc
    canonical = json.dumps(
        pack.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return pack, hashlib.sha256(canonical).hexdigest()


__all__ = ["MAX_SETTING_PACK_BYTES", "SettingPackLoadError", "load_setting_pack"]
