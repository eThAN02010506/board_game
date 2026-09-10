"""Deterministic fingerprints and AI-safe snapshots for terminal skill checks."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Mapping
from hashlib import sha256
from math import isfinite
from typing import Any

CHECK_CONSEQUENCE_SCHEMA_VERSION = "check-consequence.v1"
HIDDEN_CHECK_PUBLIC_NARRATION = "局势仍在发展，当前没有可公开确认的新信息。"

_TERMINAL_STATUSES = frozenset({"resolved", "overridden", "cancelled"})
_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 20_000
_MAX_CONTAINER_ITEMS = 4_096
_MAX_STRING_LENGTH = 100_000
_MAX_INTEGER_BITS = 4_096


def _required_text(value: Any, *, field_name: str, collapse: bool = False) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be text")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.split()) if collapse else normalized.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    if len(normalized) > _MAX_STRING_LENGTH:
        raise ValueError(f"{field_name} is too long")
    return normalized


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name=field_name)


def _optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer")
    return value


def _normalize_json(
    value: Any,
    *,
    field_name: str,
    depth: int = 0,
    ancestors: tuple[int, ...] = (),
    node_count: list[int] | None = None,
) -> Any:
    counter = node_count if node_count is not None else [0]
    counter[0] += 1
    if counter[0] > _MAX_JSON_NODES:
        raise ValueError(f"{field_name} exceeds the JSON node limit")
    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"{field_name} exceeds the JSON depth limit")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value.bit_length() > _MAX_INTEGER_BITS:
            raise ValueError(f"{field_name} contains an oversized integer")
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError(f"{field_name} must contain finite JSON data")
        return 0.0 if value == 0 else value
    if type(value) is str:
        if len(value) > _MAX_STRING_LENGTH:
            raise ValueError(f"{field_name} contains an oversized string")
        return value
    if type(value) not in {dict, list, tuple}:
        raise ValueError(f"{field_name} must contain only JSON data")

    container_id = id(value)
    if container_id in ancestors:
        raise ValueError(f"{field_name} cannot contain cycles")
    if len(value) > _MAX_CONTAINER_ITEMS:
        raise ValueError(f"{field_name} contains an oversized collection")
    child_ancestors = (*ancestors, container_id)
    if type(value) in {list, tuple}:
        return [
            _normalize_json(
                item,
                field_name=field_name,
                depth=depth + 1,
                ancestors=child_ancestors,
                node_count=counter,
            )
            for item in value
        ]

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if type(key) is not str:
            raise ValueError(f"{field_name} object keys must be strings")
        if len(key) > _MAX_STRING_LENGTH:
            raise ValueError(f"{field_name} contains an oversized object key")
        normalized[key] = _normalize_json(
            item,
            field_name=field_name,
            depth=depth + 1,
            ancestors=child_ancestors,
            node_count=counter,
        )
    return normalized


def _json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{field_name} must be an object")
    normalized = _normalize_json(value, field_name=field_name)
    if not isinstance(normalized, dict):
        raise ValueError(f"{field_name} must be an object")  # noqa: TRY004
    return normalized


def _optional_json_object(
    value: Any,
    *,
    field_name: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    return _json_object(value, field_name=field_name)


def _normalize_status(value: Any) -> str:
    status = _required_text(value, field_name="status").casefold()
    if status == "requested":
        raise ValueError("Requested checks have no consequence snapshot")
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"Unknown skill check status: {status}")
    return status


def _normalize_check(check: Mapping[str, Any]) -> dict[str, Any]:
    if type(check) is not dict:
        raise ValueError("Each skill check must be a dict")

    check_id = _required_text(check.get("id"), field_name="id")
    status = _normalize_status(check.get("status"))
    proposal_id = _optional_text(check.get("proposal_id"), field_name="proposal_id")
    player_action_id = _optional_text(
        check.get("player_action_id"),
        field_name="player_action_id",
    )
    if proposal_id is None and player_action_id is None:
        raise ValueError(
            "Skill checks require a proposal_id or player_action_id origin"
        )

    raw_dice = _optional_json_object(
        check.get("raw_dice"),
        field_name=f"{check_id}.raw_dice",
    )
    selected_roll = _optional_int(
        check.get("selected_roll"),
        field_name=f"{check_id}.selected_roll",
    )
    threshold = _optional_int(
        check.get("threshold"),
        field_name=f"{check_id}.threshold",
    )
    success_level = _optional_text(
        check.get("success_level"),
        field_name=f"{check_id}.success_level",
    )
    passed = check.get("passed")
    if passed is not None and type(passed) is not bool:
        raise ValueError(f"{check_id}.passed must be a boolean")

    override_reason = _optional_text(
        check.get("override_reason"),
        field_name=f"{check_id}.override_reason",
    )
    original_result = _optional_json_object(
        check.get("original_result"),
        field_name=f"{check_id}.original_result",
    )
    input_method = _optional_text(
        check.get("input_method"),
        field_name=f"{check_id}.input_method",
    )

    result_values = (
        input_method,
        raw_dice,
        selected_roll,
        threshold,
        success_level,
        passed,
    )
    if status in {"resolved", "overridden"} and any(
        value is None for value in result_values
    ):
        raise ValueError(f"{status} check {check_id} requires a complete result")
    if status == "cancelled" and any(value is not None for value in result_values):
        raise ValueError(f"cancelled check {check_id} cannot contain a result")
    if status == "overridden":
        if override_reason is None or original_result is None:
            raise ValueError(
                f"overridden check {check_id} requires override audit data"
            )
    elif override_reason is not None or original_result is not None:
        raise ValueError(
            f"{status} check {check_id} cannot contain override audit data"
        )

    target = _optional_int(check.get("target"), field_name=f"{check_id}.target")
    if target is None:
        raise ValueError(f"{check_id}.target is required")
    bonus_dice = _optional_int(
        check.get("bonus_dice"),
        field_name=f"{check_id}.bonus_dice",
    )
    if bonus_dice is None:
        raise ValueError(f"{check_id}.bonus_dice is required")
    hidden = check.get("hidden")
    if type(hidden) is not bool:
        raise ValueError(f"{check_id}.hidden must be a boolean")

    normalized = {
        "id": check_id,
        "status": status,
        "hidden": hidden,
        "origin": {
            "proposal_id": proposal_id,
            "player_action_id": player_action_id,
        },
        "skill": {
            "key": _optional_text(
                check.get("skill_key"),
                field_name=f"{check_id}.skill_key",
            ),
            "name": _required_text(
                check.get("skill_name"),
                field_name=f"{check_id}.skill_name",
                collapse=True,
            ),
            "target": target,
            "difficulty": _required_text(
                check.get("difficulty"),
                field_name=f"{check_id}.difficulty",
            ),
            "bonus_dice": bonus_dice,
        },
        "raw_result": {
            "input_method": input_method,
            "raw_dice": raw_dice,
            "selected_roll": selected_roll,
            "threshold": threshold,
        },
        "success_level": success_level,
        "passed": passed,
        "override": {
            "reason": override_reason,
            "original_result": original_result,
        },
        "pushed_from_check_id": _optional_text(
            check.get("pushed_from_check_id"),
            field_name=f"{check_id}.pushed_from_check_id",
        ),
        "ruleset": {
            "id": _required_text(
                check.get("ruleset_id"),
                field_name=f"{check_id}.ruleset_id",
            ),
            "version": _required_text(
                check.get("ruleset_version"),
                field_name=f"{check_id}.ruleset_version",
            ),
        },
        "source_reference": _json_object(
            check.get("source_reference"),
            field_name=f"{check_id}.source_reference",
        ),
    }
    check_plan = _json_object(
        check.get("check_plan") or {},
        field_name=f"{check_id}.check_plan",
    )
    normalized["consequence_contract"] = {
        "failure_stakes": str(check_plan.get("failure_stakes") or "").strip(),
        "pushed_failure_stakes": str(
            check_plan.get("pushed_failure_stakes") or ""
        ).strip(),
        "automatic_information": _normalize_json(
            check_plan.get("automatic_information") or [],
            field_name=f"{check_id}.check_plan.automatic_information",
        ),
    }
    actions = check.get("actions")
    if isinstance(actions, list):
        pushed = next(
            (
                item
                for item in actions
                if isinstance(item, dict)
                and item.get("action_type") == "pushed"
                and str(item.get("reason") or "").strip()
            ),
            None,
        )
        if pushed is not None:
            normalized["push_approach"] = _required_text(
                pushed.get("reason"),
                field_name=f"{check_id}.push_approach",
                collapse=True,
            )
    push_decision = check.get("push_decision")
    if push_decision is not None:
        decision = _json_object(
            push_decision, field_name=f"{check_id}.push_decision"
        )
        if decision.get("decision") != "accept_failure":
            raise ValueError(f"{check_id}.push_decision is unsupported")
        normalized["push_decision"] = {
            "decision": "accept_failure",
            "actor_member_id": _required_text(
                decision.get("actor_member_id"),
                field_name=f"{check_id}.push_decision.actor_member_id",
            ),
            "reason": _required_text(
                decision.get("reason"),
                field_name=f"{check_id}.push_decision.reason",
                collapse=True,
            ),
        }
    return normalized


def _prepare_checks(
    checks: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[str]]]:
    normalized = [_normalize_check(check) for check in checks]
    if not normalized:
        raise ValueError("At least one skill check is required")

    by_id: dict[str, dict[str, Any]] = {}
    for check in normalized:
        if check["id"] in by_id:
            raise ValueError(f"Duplicate skill check id: {check['id']}")
        by_id[check["id"]] = check

    origin = normalized[0]["origin"]
    if any(check["origin"] != origin for check in normalized[1:]):
        raise ValueError("Skill checks must share one proposal/action origin")

    children: dict[str, list[str]] = {check_id: [] for check_id in by_id}
    for check in normalized:
        parent_id = check["pushed_from_check_id"]
        if parent_id is None:
            continue
        if parent_id not in by_id:
            raise ValueError(
                f"Push chain is missing parent check: {parent_id}"
            )
        children[parent_id].append(check["id"])

    for parent_id, child_ids in children.items():
        if len(child_ids) > 1:
            raise ValueError(f"Push check {parent_id} has multiple children")
        if not child_ids:
            continue
        parent = by_id[parent_id]
        if parent["status"] not in {"resolved", "overridden"}:
            raise ValueError("Only a resolved check can be a push parent")
        if parent["passed"] is not False:
            raise ValueError("Only a failed check can be a push parent")

    roots = sorted(
        check_id
        for check_id, check in by_id.items()
        if check["pushed_from_check_id"] is None
    )
    ordered: list[dict[str, Any]] = []
    visited: set[str] = set()
    for root_id in roots:
        current_id: str | None = root_id
        while current_id is not None:
            if current_id in visited:
                raise ValueError("Push chain contains a cycle")
            visited.add(current_id)
            ordered.append(by_id[current_id])
            current_children = children[current_id]
            current_id = current_children[0] if current_children else None
    if len(visited) != len(by_id):
        raise ValueError("Push chain contains a cycle")
    return origin, ordered, children


def has_pending_push_decision(
    checks: Iterable[Mapping[str, Any]],
) -> bool:
    """Return whether a failed check still needs its player-owned push choice.

    This predicate deliberately accepts the repository-shaped check mappings
    instead of the stricter consequence snapshot schema.  Callers use it before
    a terminal consequence is legal, when a failed root may still be pushable.
    A push is no longer pending once a child exists or the player explicitly
    accepts the ordinary failure.
    """

    materialized = tuple(checks)
    pushed_parent_ids = {
        str(check["pushed_from_check_id"])
        for check in materialized
        if check.get("pushed_from_check_id") is not None
    }
    for check in materialized:
        if (
            check.get("status") not in {"resolved", "overridden"}
            or check.get("passed") is not False
            or check.get("allow_push") is not True
            or str(check.get("id")) in pushed_parent_ids
        ):
            continue
        decision = check.get("push_decision")
        if not isinstance(decision, Mapping) or (
            decision.get("decision") != "accept_failure"
        ):
            return True
    return False


def _canonical_payload(
    checks: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[str]]]:
    origin, ordered, children = _prepare_checks(checks)
    return (
        {
            "schema_version": CHECK_CONSEQUENCE_SCHEMA_VERSION,
            "origin": origin,
            "checks": ordered,
        },
        ordered,
        children,
    )


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def check_consequence_fingerprint(
    checks: Iterable[Mapping[str, Any]],
) -> str:
    """Hash every terminal row, including non-leaf rows in push chains."""

    payload, _ordered, _children = _canonical_payload(checks)
    return _payload_fingerprint(payload)


def exact_kernel_outcome(
    preview: Mapping[str, Any],
    checks: Iterable[Mapping[str, Any]],
) -> str:
    """Map terminal checks to the most specific branch authorized by a preview."""

    _origin, ordered, children = _prepare_checks(checks)
    if any(item["status"] == "cancelled" for item in ordered):
        raise ValueError("Cancelled checks cannot authorize a kernel outcome")
    leaves = [item for item in ordered if not children[item["id"]]]
    if len(leaves) != 1:
        raise ValueError(
            "Exact kernel outcomes require one independent resolved check chain"
        )
    leaf = leaves[0]

    available = {
        str(item.get("outcome_key"))
        for item in preview.get("outcome_branches") or ()
        if isinstance(item, Mapping)
    }
    if (
        leaf.get("passed") is False
        and leaf.get("pushed_from_check_id")
        and "pushed_failure" in available
    ):
        return "pushed_failure"
    level = str(leaf["success_level"]).strip()
    if level in available:
        return level
    return "success" if leaf.get("passed") is True else "failure"


def build_check_consequence_snapshot(
    checks: Iterable[Mapping[str, Any]],
    opposed_checks: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return leaf-only effective results safe to include as AI data context."""

    payload, ordered, children = _canonical_payload(checks)
    by_id = {check["id"]: check for check in ordered}
    effective_results: list[dict[str, Any]] = []
    for check in ordered:
        if children[check["id"]]:
            continue
        chain_ids = [check["id"]]
        parent_id = check["pushed_from_check_id"]
        while parent_id is not None:
            chain_ids.append(parent_id)
            parent_id = by_id[parent_id]["pushed_from_check_id"]
        chain_ids.reverse()
        chain = [by_id[item] for item in chain_ids]
        push_approach = next(
            (
                str(item["push_approach"])
                for item in chain
                if item.get("push_approach")
            ),
            "",
        )
        pushed_failure = (
            len(chain_ids) > 1
            and check["passed"] is False
            and check["status"] in {"resolved", "overridden"}
        )
        consequence_contract = dict(check["consequence_contract"])
        effective_results.append(
            {
                "check_id": check["id"],
                "status": check["status"],
                "hidden": check["hidden"],
                "skill_name": check["skill"]["name"],
                "target": check["skill"]["target"],
                "difficulty": check["skill"]["difficulty"],
                "selected_roll": check["raw_result"]["selected_roll"],
                "threshold": check["raw_result"]["threshold"],
                "success_level": check["success_level"],
                "passed": check["passed"],
                "was_overridden": check["status"] == "overridden",
                "push_chain_ids": chain_ids,
                "outcome": (
                    "pushed_failure"
                    if pushed_failure
                    else "success" if check["passed"] is True else "failure"
                ),
                "push_approach": push_approach,
                "failure_stakes": consequence_contract["failure_stakes"],
                "pushed_failure_stakes": consequence_contract[
                    "pushed_failure_stakes"
                ],
                "accepted_stakes": (
                    consequence_contract["pushed_failure_stakes"]
                    if pushed_failure
                    else consequence_contract["failure_stakes"]
                    if check["passed"] is False
                    else ""
                ),
                "automatic_information": consequence_contract[
                    "automatic_information"
                ],
                "ruleset": check["ruleset"],
            }
        )
    opposed_results = []
    for opposed in sorted(opposed_checks, key=lambda item: str(item["id"])):
        if opposed["status"] != "resolved":
            # 平局（reroll_required）必须先重掷出裁决者，后果不能基于"赢家未定"生成。
            raise ValueError(
                "All linked opposed checks must be resolved before a consequence "
                f"can be generated (got status={opposed['status']})"
            )
        opposed_results.append(
            {
                "opposed_check_id": opposed["id"],
                "status": opposed["status"],
                "left_check_id": opposed["left_check_id"],
                "right_check_id": opposed["right_check_id"],
                "result": opposed["result"],
                "result_fingerprint": opposed["result_fingerprint"],
            }
        )
    skill_fingerprint = _payload_fingerprint(payload)
    combined_fingerprint = (
        _payload_fingerprint(
            {
                "skill_check_fingerprint": skill_fingerprint,
                "opposed_results": opposed_results,
            }
        )
        if opposed_results
        else skill_fingerprint
    )
    return {
        "schema_version": CHECK_CONSEQUENCE_SCHEMA_VERSION,
        "origin": payload["origin"],
        "result_fingerprint": combined_fingerprint,
        "has_hidden_checks": any(check["hidden"] for check in ordered),
        "effective_results": effective_results,
        "opposed_results": opposed_results,
    }


def project_public_check_consequences(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep observable fiction while dropping every mechanical result field.

    A blind check hides its roll, target, skill and success tier, not effects the
    characters can observe. The only text crossing this boundary was fixed
    before the roll: automatic information and player-accepted failure stakes.
    Outcome narration is supplied separately from the contract's public cues.
    """

    automatic_information: list[str] = []
    accepted_stakes: list[dict[str, Any]] = []
    results = snapshot.get("effective_results")
    if not isinstance(results, (list, tuple)):
        results = ()
    for result in results:
        if not isinstance(result, Mapping):
            continue
        information = result.get("automatic_information")
        if isinstance(information, (list, tuple)):
            for item in information:
                text = str(item).strip()
                if text and text not in automatic_information:
                    automatic_information.append(text)
        stakes = str(result.get("accepted_stakes") or "").strip()
        if not stakes or any(item["text"] == stakes for item in accepted_stakes):
            continue
        accepted_stakes.append(
            {
                "text": stakes,
                "push_approach": str(result.get("push_approach") or "").strip(),
                "was_pushed": result.get("outcome") == "pushed_failure",
            }
        )
    return {
        "automatic_information": automatic_information,
        "accepted_stakes": accepted_stakes,
    }


__all__ = [
    "CHECK_CONSEQUENCE_SCHEMA_VERSION",
    "HIDDEN_CHECK_PUBLIC_NARRATION",
    "build_check_consequence_snapshot",
    "check_consequence_fingerprint",
    "exact_kernel_outcome",
    "has_pending_push_decision",
    "project_public_check_consequences",
]
