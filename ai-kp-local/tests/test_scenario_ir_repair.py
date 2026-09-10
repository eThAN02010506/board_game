from __future__ import annotations

from pydantic import ValidationError

from ai_kp.platform.resolution.scenario_ir_models import ScenarioIrBatch
from ai_kp.platform.resolution.scenario_ir_repair import (
    ScenarioIrCitationRepairEnvelope,
    ScenarioIrRepairEnvelope,
    apply_repair_envelope,
    apply_source_citation_envelope,
    decode_source_citation_repair,
    discard_repair_targets,
    discard_unknown_source_records,
    plan_source_citation_repairs,
    plan_validation_repairs,
)


def invalid_payload() -> dict:
    return {
        "confidence": "high",
        "locations": [{
            "id": "room",
            "title": "Room",
            "source_block_ids": ["block-1"],
        }],
        "actions": [{
            "id": "inspect",
            "title": "Inspect",
            "policy": "roll_magic",
            "source_block_ids": ["block-1"],
        }],
    }


def test_validation_repair_plan_isolates_only_the_invalid_record() -> None:
    payload = invalid_payload()
    try:
        ScenarioIrBatch.model_validate(payload)
    except ValidationError as error:
        plan = plan_validation_repairs(payload, error)
    else:  # pragma: no cover - fixture invariant
        raise AssertionError("fixture must be invalid")

    assert plan is not None
    assert plan.identity_set() == {("actions", 0)}
    assert "locations" not in plan.record_schemas()


def test_repair_envelope_cannot_replace_unrequested_records() -> None:
    payload = invalid_payload()
    try:
        ScenarioIrBatch.model_validate(payload)
    except ValidationError as error:
        plan = plan_validation_repairs(payload, error)
    assert plan is not None
    envelope = ScenarioIrRepairEnvelope.model_validate({
        "replacements": [{
            "group": "locations",
            "record_index": 0,
            "record": payload["locations"][0],
        }]
    })

    try:
        apply_repair_envelope(payload, plan, envelope)
    except ValueError as error:
        assert "does not match requested targets" in str(error)
    else:  # pragma: no cover - authority invariant
        raise AssertionError("unrequested replacement must be rejected")


def test_repair_envelope_ignores_inert_top_level_model_commentary() -> None:
    payload = invalid_payload()
    try:
        ScenarioIrBatch.model_validate(payload)
    except ValidationError as error:
        plan = plan_validation_repairs(payload, error)
    assert plan is not None
    envelope = ScenarioIrRepairEnvelope.model_validate({
        "replacements": [{
            "group": "actions",
            "record_index": 0,
            "record": {
                "id": "inspect",
                "title": "Inspect",
                "policy": "automatic",
                "source_block_ids": ["block-1"],
            },
        }],
        "ending_2": "explanation accidentally emitted by a small model",
    })

    repaired = apply_repair_envelope(payload, plan, envelope)

    assert ScenarioIrBatch.model_validate(repaired).actions[0].policy == "automatic"


def test_repair_envelope_cannot_change_a_valid_record_identity() -> None:
    payload = invalid_payload()
    try:
        ScenarioIrBatch.model_validate(payload)
    except ValidationError as error:
        plan = plan_validation_repairs(payload, error)
    assert plan is not None
    envelope = ScenarioIrRepairEnvelope.model_validate({
        "replacements": [{
            "group": "actions",
            "record_index": 0,
            "record": {
                "id": "different-action",
                "title": "Inspect",
                "policy": "automatic",
                "source_block_ids": ["block-1"],
            },
        }]
    })

    try:
        apply_repair_envelope(payload, plan, envelope)
    except ValueError as error:
        assert "cannot change stable identity field id" in str(error)
    else:  # pragma: no cover - authority invariant
        raise AssertionError("stable identity mutation must be rejected")


def test_valid_replacement_preserves_all_non_target_records() -> None:
    payload = invalid_payload()
    try:
        ScenarioIrBatch.model_validate(payload)
    except ValidationError as error:
        plan = plan_validation_repairs(payload, error)
    assert plan is not None
    envelope = ScenarioIrRepairEnvelope.model_validate({
        "replacements": [{
            "group": "actions",
            "record_index": 0,
            "record": {
                "id": "inspect",
                "title": "Inspect",
                "policy": "automatic",
                "source_block_ids": ["block-1"],
            },
        }]
    })

    repaired = apply_repair_envelope(payload, plan, envelope)

    assert repaired["locations"] is payload["locations"]
    assert repaired["actions"] is not payload["actions"]
    assert ScenarioIrBatch.model_validate(repaired).actions[0].policy == "automatic"


def test_discard_removes_only_server_identified_invalid_records() -> None:
    payload = invalid_payload()
    payload["actions"].append({
        "id": "listen",
        "title": "Listen",
        "policy": "automatic",
        "source_block_ids": ["block-1"],
    })
    try:
        ScenarioIrBatch.model_validate(payload)
    except ValidationError as error:
        plan = plan_validation_repairs(payload, error)
    assert plan is not None

    degraded = discard_repair_targets(payload, plan)

    batch = ScenarioIrBatch.model_validate(degraded)
    assert [item.id for item in batch.actions] == ["listen"]
    assert degraded["locations"] is payload["locations"]


def test_bulk_unknown_source_discard_is_fail_closed_and_preserves_valid_records() -> None:
    payload = {"actions": [
        {
            "id": f"invalid-{index}",
            "title": "Invalid",
            "policy": "automatic",
            "source_block_ids": [f"invented-{index}"],
        }
        for index in range(9)
    ] + [{
        "id": "valid",
        "title": "Valid",
        "policy": "automatic",
        "source_block_ids": ["block-1"],
    }]}

    degraded, discarded = discard_unknown_source_records(
        payload,
        allowed_source_ids={"block-1"},
    )

    assert discarded == 9
    assert [item.id for item in ScenarioIrBatch.model_validate(degraded).actions] == [
        "valid"
    ]


def test_citation_repair_selects_only_original_authorized_sources() -> None:
    payload = invalid_payload()
    payload["actions"][0]["policy"] = "automatic"
    payload["actions"][0]["source_block_ids"] = [
        *(f"block-{index}" for index in range(1, 10)),
        "invented-source",
    ]
    plan = plan_source_citation_repairs(
        payload, allowed_source_ids={f"block-{index}" for index in range(1, 10)}
    )
    assert plan is not None

    repaired = apply_source_citation_envelope(
        payload,
        plan,
        ScenarioIrCitationRepairEnvelope.model_validate({
            "replacements": [{
                "group": "actions",
                "record_index": 0,
                "source_block_ids": ["block-2", "block-8"],
            }],
        }),
    )

    assert repaired["actions"][0]["id"] == "inspect"
    assert repaired["actions"][0]["source_block_ids"] == ["block-2", "block-8"]


def test_legacy_citation_repair_cannot_change_record_content() -> None:
    payload = invalid_payload()
    payload["actions"][0]["policy"] = "automatic"
    payload["actions"][0]["source_block_ids"] = [
        *(f"block-{index}" for index in range(1, 10)),
    ]
    plan = plan_source_citation_repairs(
        payload, allowed_source_ids={f"block-{index}" for index in range(1, 10)}
    )
    assert plan is not None

    try:
        decode_source_citation_repair(
            {
                "replacements": [{
                    "group": "actions",
                    "record_index": 0,
                    "record": {
                        **payload["actions"][0],
                        "title": "Rewritten title",
                        "source_block_ids": ["block-1"],
                    },
                }],
            },
            payload,
            plan,
        )
    except ValueError as error:
        assert "cannot change record content" in str(error)
    else:  # pragma: no cover - authority invariant
        raise AssertionError("citation repair must not gain record-body authority")
