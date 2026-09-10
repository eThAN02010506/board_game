"""Strict transport normalization for model-produced JSON objects."""

from __future__ import annotations

import json
import re
from typing import Any


class StructuredJsonError(ValueError):
    """The model response is not one complete JSON object."""


_JSON_FENCE = re.compile(
    r"```(?:json)?\s*(.*?)\s*```",
    flags=re.DOTALL | re.IGNORECASE,
)


def decode_json_object(raw: str) -> dict[str, Any]:
    """Decode plain JSON or one exact Markdown-fenced JSON object.

    Arbitrary prose, embedded objects and multiple fenced blocks remain invalid;
    this only normalizes a common presentation wrapper and never guesses intent.
    """

    text = raw.strip()
    fenced = _JSON_FENCE.fullmatch(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{") or not text.endswith("}"):
        raise StructuredJsonError("model output is not one complete JSON object")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredJsonError(str(exc)) from exc
    if not isinstance(value, dict):
        raise StructuredJsonError("model output root must be a JSON object")
    return value
