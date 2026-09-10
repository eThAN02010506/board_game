from __future__ import annotations

import pytest

from ai_kp.platform.resolution.location_references import (
    condition_references_location,
    is_location_condition_path,
)


@pytest.mark.parametrize(
    "path",
    (
        "scene_id",
        "current_scene_id",
        "facts.current_location",
        "entities.caretaker.location_id",
        "actor_locations.investigator-1",
        "actors[cultist].location",
    ),
)
def test_recognizes_explicit_location_condition_paths(path: str) -> None:
    assert is_location_condition_path(path)
    assert condition_references_location(path, "cellar", "cellar")


@pytest.mark.parametrize(
    "path",
    ("facts.favorite_scene", "facts.notes.location_name", "ending_id", "status"),
)
def test_does_not_treat_arbitrary_equal_values_as_location_refs(path: str) -> None:
    assert not is_location_condition_path(path)
    assert not condition_references_location(path, "cellar", "cellar")


def test_requires_exact_location_identity_value() -> None:
    assert not condition_references_location("scene_id", "attic", "cellar")
