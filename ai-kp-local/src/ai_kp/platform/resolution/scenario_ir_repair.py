"""Deterministic isolation and application of model-facing IR record repairs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_kp.platform.resolution.scenario_ir_models import (
    IrAction,
    IrClock,
    IrClue,
    IrConsequenceSignal,
    IrEnding,
    IrEntity,
    IrLocation,
    IrLocationLink,
    IrReactivePolicy,
    IrResource,
    IrTaskMethod,
)

ScenarioIrRecordGroup = Literal[
    "locations",
    "location_links",
    "entities",
    "clocks",
    "resources",
    "clues",
    "actions",
    "task_methods",
    "reactive_policies",
    "consequence_signals",
    "endings",
]

_RECORD_TYPES: dict[ScenarioIrRecordGroup, type[BaseModel]] = {
    "locations": IrLocation,
    "location_links": IrLocationLink,
    "entities": IrEntity,
    "clocks": IrClock,
    "resources": IrResource,
    "clues": IrClue,
    "actions": IrAction,
    "task_methods": IrTaskMethod,
    "reactive_policies": IrReactivePolicy,
    "consequence_signals": IrConsequenceSignal,
    "endings": IrEnding,
}
_IDENTITY_FIELDS: dict[ScenarioIrRecordGroup, tuple[str, ...]] = {
    "locations": ("id",),
    "location_links": ("from_id", "to_id"),
    "entities": ("id",),
    "clocks": ("id",),
    "resources": ("id",),
    "clues": ("id",),
    "actions": ("id",),
    "task_methods": ("id", "task_key"),
    "reactive_policies": ("id", "entity_id"),
    "consequence_signals": ("id",),
    "endings": ("id",),
}
_REPAIR_TARGET_LIMIT = 8
_SOURCE_CITATION_LIMIT = 8


class ScenarioIrRepairTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group: ScenarioIrRecordGroup
    record_index: int = Field(ge=0, le=31)
    original_record: dict[str, Any]
    validation_errors: tuple[str, ...] = Field(min_length=1, max_length=16)


class ScenarioIrRepairPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    targets: tuple[ScenarioIrRepairTarget, ...] = Field(
        min_length=1,
        max_length=_REPAIR_TARGET_LIMIT,
    )

    def identity_set(self) -> set[tuple[str, int]]:
        return {(item.group, item.record_index) for item in self.targets}

    def record_schemas(self) -> dict[str, dict[str, Any]]:
        return {
            group: _RECORD_TYPES[group].model_json_schema()
            for group in dict.fromkeys(item.group for item in self.targets)
        }


class ScenarioIrRecordReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group: ScenarioIrRecordGroup
    record_index: int = Field(ge=0, le=31)
    record: dict[str, Any]


class ScenarioIrRepairEnvelope(BaseModel):
    # Small models sometimes echo explanations beside the requested envelope.
    # Those fields are inert: only validated replacements enter the authority
    # checks below, so ignoring top-level metadata avoids an unnecessary retry
    # without allowing another record to be changed.
    model_config = ConfigDict(extra="ignore", frozen=True)

    replacements: tuple[ScenarioIrRecordReplacement, ...] = Field(
        min_length=1,
        max_length=_REPAIR_TARGET_LIMIT,
    )


class ScenarioIrCitationRepairTarget(BaseModel):
    """One record whose provenance alone may be narrowed by the model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    group: ScenarioIrRecordGroup
    record_index: int = Field(ge=0, le=31)
    original_source_block_ids: tuple[str, ...] = Field(min_length=1)
    allowed_source_block_ids: tuple[str, ...] = Field(
        min_length=1, max_length=256
    )
    validation_errors: tuple[str, ...] = (
        "source_block_ids: select 1..8 original partition citations",
    )


class ScenarioIrCitationRepairPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    targets: tuple[ScenarioIrCitationRepairTarget, ...] = Field(
        min_length=1, max_length=_REPAIR_TARGET_LIMIT
    )

    def identity_set(self) -> set[tuple[str, int]]:
        return {(item.group, item.record_index) for item in self.targets}


class ScenarioIrCitationReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group: ScenarioIrRecordGroup
    record_index: int = Field(ge=0, le=31)
    source_block_ids: tuple[str, ...] = Field(
        min_length=1, max_length=_SOURCE_CITATION_LIMIT
    )


class ScenarioIrCitationRepairEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    replacements: tuple[ScenarioIrCitationReplacement, ...] = Field(
        min_length=1, max_length=_REPAIR_TARGET_LIMIT
    )


class ScenarioIrRepairDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    partition_index: int = Field(ge=0)
    model_attempt: int = Field(ge=1, le=3)
    group: ScenarioIrRecordGroup
    record_index: int = Field(ge=0, le=31)
    status: Literal["applied", "failed", "discarded"]
    validation_errors: tuple[str, ...] = Field(default=(), max_length=16)


def plan_validation_repairs(
    payload: Any,
    error: ValidationError,
) -> ScenarioIrRepairPlan | None:
    """Return a plan only when every validation error belongs to a record."""

    if not isinstance(payload, dict):
        return None
    grouped: dict[tuple[ScenarioIrRecordGroup, int], list[str]] = {}
    for issue in error.errors(include_url=False):
        location = issue.get("loc", ())
        if (
            len(location) < 2
            or location[0] not in _RECORD_TYPES
            or not isinstance(location[1], int)
        ):
            return None
        group = location[0]
        index = location[1]
        records = payload.get(group)
        if (
            not isinstance(records, list)
            or not 0 <= index < len(records)
            or not isinstance(records[index], dict)
        ):
            return None
        path = ".".join(str(item) for item in location[2:]) or "record"
        grouped.setdefault((group, index), []).append(
            f"{path}: {issue.get('msg', 'invalid value')}"
        )
    return _build_plan(payload, grouped)


def contract_invalid_reactive_children(
    payload: dict[str, Any],
    error: ValidationError,
) -> tuple[dict[str, Any], int]:
    """Drop only invalid reactive rules identified by schema error locations."""

    invalid_rules: dict[int, set[int]] = {}
    for issue in error.errors(include_url=False):
        location = issue.get("loc", ())
        if (
            len(location) >= 4
            and location[0] == "reactive_policies"
            and isinstance(location[1], int)
            and location[2] == "rules"
            and isinstance(location[3], int)
        ):
            invalid_rules.setdefault(location[1], set()).add(location[3])
    if not invalid_rules:
        return payload, 0
    policies = payload.get("reactive_policies")
    if not isinstance(policies, list):
        return payload, 0
    contracted = dict(payload)
    retained_policies = deepcopy(policies)
    removed = 0
    for policy_index in sorted(invalid_rules, reverse=True):
        if not 0 <= policy_index < len(retained_policies):
            continue
        policy = retained_policies[policy_index]
        rules = policy.get("rules") if isinstance(policy, dict) else None
        if not isinstance(rules, list):
            continue
        for rule_index in sorted(invalid_rules[policy_index], reverse=True):
            if 0 <= rule_index < len(rules):
                rules.pop(rule_index)
                removed += 1
        if not rules:
            retained_policies.pop(policy_index)
    if not removed:
        return payload, 0
    contracted["reactive_policies"] = retained_policies
    assumptions = contracted.get("assumptions")
    safe_assumptions = assumptions if isinstance(assumptions, list) else []
    contracted["assumptions"] = list(
        dict.fromkeys(
            (
                f"Invalid reactive children were contracted locally: {removed} rules.",
                *safe_assumptions,
            )
        )
    )[:16]
    return contracted, removed


def plan_unknown_source_repairs(
    payload: dict[str, Any],
    *,
    allowed_source_ids: set[str],
) -> ScenarioIrRepairPlan | None:
    """Locate otherwise valid records that cite source blocks outside the partition."""

    grouped: dict[tuple[ScenarioIrRecordGroup, int], list[str]] = {}
    for group in _RECORD_TYPES:
        records = payload.get(group, [])
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            source_ids = record.get("source_block_ids", [])
            if not isinstance(source_ids, list):
                continue
            unknown = sorted(
                str(source_id)
                for source_id in source_ids
                if str(source_id) not in allowed_source_ids
            )
            if unknown:
                grouped[(group, index)] = [
                    "source_block_ids: unknown partition source IDs "
                    + ", ".join(unknown)
                ]
    return _build_plan(payload, grouped)


def plan_source_citation_repairs(
    payload: dict[str, Any],
    *,
    allowed_source_ids: set[str],
) -> ScenarioIrCitationRepairPlan | None:
    """Plan citation-only repair for bounded overflow or unknown provenance.

    The plan deliberately exposes no record body. A replacement can only select
    citations already present in the original record and authorized by the
    partition; it cannot turn provenance repair into semantic rewriting.
    """

    targets: list[ScenarioIrCitationRepairTarget] = []
    for group in _RECORD_TYPES:
        records = payload.get(group, [])
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            raw_ids = record.get("source_block_ids")
            if not isinstance(raw_ids, list) or not all(
                isinstance(item, str) and item.strip() for item in raw_ids
            ):
                continue
            deduplicated = tuple(dict.fromkeys(raw_ids))
            needs_repair = len(deduplicated) > _SOURCE_CITATION_LIMIT or any(
                source_id not in allowed_source_ids for source_id in deduplicated
            )
            if not needs_repair:
                continue
            allowed = tuple(
                source_id
                for source_id in deduplicated
                if source_id in allowed_source_ids
            )
            if not allowed:
                continue
            targets.append(
                ScenarioIrCitationRepairTarget(
                    group=group,
                    record_index=index,
                    original_source_block_ids=deduplicated,
                    allowed_source_block_ids=allowed,
                )
            )
            if len(targets) > _REPAIR_TARGET_LIMIT:
                return None
    return ScenarioIrCitationRepairPlan(targets=tuple(targets)) if targets else None


def apply_source_citation_envelope(
    payload: dict[str, Any],
    plan: ScenarioIrCitationRepairPlan,
    envelope: ScenarioIrCitationRepairEnvelope,
) -> dict[str, Any]:
    """Apply provenance-only selections without exposing semantic fields."""

    replacement_ids = [
        (item.group, item.record_index) for item in envelope.replacements
    ]
    if len(replacement_ids) != len(set(replacement_ids)):
        raise ValueError("Scenario IR citation repair contains duplicate targets")
    if set(replacement_ids) != plan.identity_set():
        raise ValueError("Scenario IR citation repair does not match requested targets")
    targets = {(item.group, item.record_index): item for item in plan.targets}
    repaired = dict(payload)
    copied_groups: dict[str, list[Any]] = {}
    for replacement in envelope.replacements:
        target = targets[(replacement.group, replacement.record_index)]
        selected = tuple(dict.fromkeys(replacement.source_block_ids))
        if set(selected) - set(target.allowed_source_block_ids):
            raise ValueError(
                "Scenario IR citation repair selected an unknown or non-original source"
            )
        records = copied_groups.get(replacement.group)
        if records is None:
            original_records = payload.get(replacement.group)
            if not isinstance(original_records, list):
                raise ValueError("Scenario IR citation repair group is no longer a list")
            records = list(original_records)
            copied_groups[replacement.group] = records
            repaired[replacement.group] = records
        original = records[replacement.record_index]
        if not isinstance(original, dict):
            raise TypeError("Scenario IR citation repair target is no longer a record")
        # Copy the server-held record and replace exactly one field. The model
        # never supplies title, identity, commands, or narrative content.
        records[replacement.record_index] = {
            **original,
            "source_block_ids": list(selected),
        }
    return repaired


def decode_source_citation_repair(
    decoded: dict[str, Any],
    payload: dict[str, Any],
    plan: ScenarioIrCitationRepairPlan,
) -> ScenarioIrCitationRepairEnvelope:
    """Decode current or legacy citation-only transport without body authority."""

    try:
        return ScenarioIrCitationRepairEnvelope.model_validate(decoded)
    except ValidationError:
        legacy = ScenarioIrRepairEnvelope.model_validate(decoded)
    targets = {
        (item.group, item.record_index): item for item in plan.targets
    }
    replacements = []
    for replacement in legacy.replacements:
        target = targets.get((replacement.group, replacement.record_index))
        if target is None:
            raise ValueError("citation repair target was not requested")
        original = payload[replacement.group][replacement.record_index]
        proposed = replacement.record
        if not isinstance(original, dict):
            raise TypeError("citation repair target is no longer a record")
        original_body = {
            key: value for key, value in original.items() if key != "source_block_ids"
        }
        proposed_body = {
            key: value for key, value in proposed.items() if key != "source_block_ids"
        }
        if original_body != proposed_body:
            raise ValueError("citation repair cannot change record content")
        replacements.append(
            {
                "group": replacement.group,
                "record_index": replacement.record_index,
                "source_block_ids": proposed.get("source_block_ids", []),
            }
        )
    return ScenarioIrCitationRepairEnvelope.model_validate(
        {"replacements": replacements}
    )


def discard_unknown_source_records(
    payload: dict[str, Any],
    *,
    allowed_source_ids: set[str],
) -> tuple[dict[str, Any], int]:
    """Fail closed when too many records contain untrusted source identifiers.

    A bounded model repair prompt intentionally accepts only a small target set.
    If a weak model corrupts more records than that bound, dropping those records
    is safer than retrying the entire partition or inventing replacement citations.
    """

    repaired = dict(payload)
    discarded = 0
    for group in _RECORD_TYPES:
        records = payload.get(group, [])
        if not isinstance(records, list):
            continue
        retained = []
        changed = False
        for record in records:
            source_ids = record.get("source_block_ids", []) if isinstance(record, dict) else []
            has_unknown = isinstance(source_ids, list) and any(
                str(source_id) not in allowed_source_ids for source_id in source_ids
            )
            if has_unknown:
                discarded += 1
                changed = True
            else:
                retained.append(record)
        if changed:
            repaired[group] = retained
    return repaired, discarded


def apply_repair_envelope(
    payload: dict[str, Any],
    plan: ScenarioIrRepairPlan,
    envelope: ScenarioIrRepairEnvelope,
) -> dict[str, Any]:
    """Apply exactly the replacements authorized by the current server plan."""

    replacement_ids = [
        (item.group, item.record_index) for item in envelope.replacements
    ]
    if len(replacement_ids) != len(set(replacement_ids)):
        raise ValueError("Scenario IR repair response contains duplicate targets")
    if set(replacement_ids) != plan.identity_set():
        raise ValueError("Scenario IR repair response does not match requested targets")

    repaired = dict(payload)
    copied_groups: dict[str, list[Any]] = {}
    targets = {
        (item.group, item.record_index): item for item in plan.targets
    }
    for replacement in envelope.replacements:
        target = targets[(replacement.group, replacement.record_index)]
        for field in _IDENTITY_FIELDS[replacement.group]:
            original_value = target.original_record.get(field)
            if (
                isinstance(original_value, str)
                and original_value.strip()
                and replacement.record.get(field) != original_value
            ):
                raise ValueError(
                    f"Scenario IR repair cannot change stable identity field {field}"
                )
        records = copied_groups.get(replacement.group)
        if records is None:
            original_records = payload.get(replacement.group)
            if not isinstance(original_records, list):
                raise ValueError("Scenario IR repair target group is no longer a list")
            records = list(original_records)
            copied_groups[replacement.group] = records
            repaired[replacement.group] = records
        if not 0 <= replacement.record_index < len(records):
            raise ValueError("Scenario IR repair target index is out of range")
        validated = _RECORD_TYPES[replacement.group].model_validate(
            replacement.record
        )
        records[replacement.record_index] = validated.model_dump(mode="json")
    return repaired


def discard_repair_targets(
    payload: dict[str, Any],
    plan: ScenarioIrRepairPlan | ScenarioIrCitationRepairPlan,
) -> dict[str, Any]:
    """Drop only server-identified invalid records after repair is exhausted.

    This is a fail-closed degradation path: it never creates or edits authority.
    Source coverage and semantic review still decide whether the smaller candidate
    can be published.
    """

    repaired = dict(payload)
    grouped: dict[ScenarioIrRecordGroup, list[int]] = {}
    for target in plan.targets:
        grouped.setdefault(target.group, []).append(target.record_index)
    for group, indexes in grouped.items():
        original_records = payload.get(group)
        if not isinstance(original_records, list):
            raise TypeError("Scenario IR discard target group is no longer a list")
        records = list(original_records)
        for index in sorted(indexes, reverse=True):
            if not 0 <= index < len(records):
                raise ValueError("Scenario IR discard target index is out of range")
            records.pop(index)
        repaired[group] = records
    return repaired


def _build_plan(
    payload: dict[str, Any],
    grouped: dict[tuple[ScenarioIrRecordGroup, int], list[str]],
) -> ScenarioIrRepairPlan | None:
    if not grouped or len(grouped) > _REPAIR_TARGET_LIMIT:
        return None
    targets = []
    for (group, index), errors in sorted(grouped.items()):
        records = payload[group]
        targets.append(
            ScenarioIrRepairTarget(
                group=group,
                record_index=index,
                original_record=records[index],
                validation_errors=tuple(dict.fromkeys(errors))[:16],
            )
        )
    return ScenarioIrRepairPlan(targets=tuple(targets))


__all__ = [
    "ScenarioIrRecordReplacement",
    "ScenarioIrRepairDiagnostic",
    "ScenarioIrRepairEnvelope",
    "ScenarioIrRepairPlan",
    "ScenarioIrRepairTarget",
    "apply_repair_envelope",
    "discard_repair_targets",
    "discard_unknown_source_records",
    "plan_unknown_source_repairs",
    "plan_validation_repairs",
]
