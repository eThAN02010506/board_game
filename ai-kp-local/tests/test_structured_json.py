import pytest

from ai_kp.platform.structured_json import StructuredJsonError, decode_json_object


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"ok": true}', {"ok": True}),
        ('```json\n{"ok": true}\n```', {"ok": True}),
        ('```\n{"records": []}\n```', {"records": []}),
    ],
)
def test_decode_json_object_accepts_only_plain_or_exact_fenced_object(
    raw: str,
    expected: dict,
) -> None:
    assert decode_json_object(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        'Here is JSON: {"ok": true}',
        '```json\n{"ok": true}\n```\nextra',
        '[{"ok": true}]',
        '```json\n{"ok": true}\n```\n```json\n{}\n```',
        '{not json}',
    ],
)
def test_decode_json_object_rejects_prose_arrays_multiple_blocks_and_invalid_json(
    raw: str,
) -> None:
    with pytest.raises(StructuredJsonError):
        decode_json_object(raw)
