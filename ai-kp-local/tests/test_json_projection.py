import pytest

from ai_kp.platform.resolution.json_projection import (
    canonical_json_bytes,
    validate_strict_json,
)


def test_canonical_json_is_stable_and_utf8() -> None:
    assert canonical_json_bytes({"乙": 2, "甲": 1}) == b'{"\xe4\xb9\x99":2,"\xe7\x94\xb2":1}'


@pytest.mark.parametrize(
    "value",
    (
        {"bad": float("nan")},
        {"bad": {"not-json"}},
        {1: "non-string key"},
        {"bad": "\ud800"},
    ),
)
def test_strict_json_rejects_ambiguous_python_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_strict_json(value)


def test_strict_json_rejects_cycles_but_allows_shared_children() -> None:
    shared: list[object] = []
    validate_strict_json({"left": shared, "right": shared})

    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="cyclic"):
        validate_strict_json(cyclic)


def test_strict_json_enforces_optional_structural_limits() -> None:
    with pytest.raises(ValueError, match="depth limit"):
        validate_strict_json({"one": {"two": {}}}, max_depth=1)

    with pytest.raises(ValueError, match="node limit"):
        validate_strict_json([None, None], max_nodes=2)

    with pytest.raises(ValueError, match="string byte limit"):
        validate_strict_json("甲乙", max_string_bytes=5)


def test_canonical_encoder_normalizes_encoder_recursion_failure() -> None:
    root: list[object] = []
    cursor = root
    for _ in range(10_000):
        child: list[object] = []
        cursor.append(child)
        cursor = child

    with pytest.raises(ValueError, match="canonical JSON"):
        canonical_json_bytes(root)
