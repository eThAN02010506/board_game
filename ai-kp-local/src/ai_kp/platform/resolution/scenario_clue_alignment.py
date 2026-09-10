"""Deterministic alignment of clue disclosure with authoritative outcomes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def align_automatic_clue_state(
    payload: dict[str, Any],
    evidence_by_id: Mapping[str, str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Align clue knowledge with source-authorized outcome commands.

    Model-authored ``automatic_information`` is not authority. A clue becomes
    unconditional only when cited source text places its exact public content
    next to an explicit automatic/no-check instruction. Otherwise its fact
    remains outcome-bound and its declared content is attached to each outcome
    that actually commits the fact.
    """

    operators = {
        str(operator["operator_id"]): operator
        for operator in payload.get("operators", [])
    }
    automatic_marker = re.compile(
        r"(?:无需检定|不需要?检定|自动获得|直接交付|"
        r"\bwithout\s+(?:a\s+)?check\b|\bautomatically\b)",
        re.IGNORECASE,
    )
    diagnostics: list[str] = []

    def same_fact(
        candidate: dict[str, Any], expected_path: str, expected_value: Any
    ) -> bool:
        return (
            candidate.get("kind") == "set_fact"
            and candidate.get("path") == expected_path
            and type(candidate.get("value")) is type(expected_value)
            and candidate.get("value") == expected_value
        )

    def source_marks_automatic(
        clue: dict[str, Any], content: tuple[str, ...]
    ) -> bool:
        source_ids = {
            str(ref.get("source_block_id", ""))
            for ref in clue.get("source_refs", [])
        }
        for source_id in source_ids:
            source_text = evidence_by_id.get(source_id, "")
            for item in content:
                index = source_text.find(item)
                if index < 0:
                    continue
                local = source_text[
                    max(0, index - 160) : index + len(item) + 160
                ]
                if automatic_marker.search(local):
                    return True
        return False

    def mutation_target(command: dict[str, Any]) -> tuple[Any, ...] | None:
        kind = command.get("kind")
        target_fields = {
            "set_fact": ("path",),
            "remove_fact": ("path",),
            "set_entity_status": ("entity_id",),
            "update_entity_runtime": ("entity_id",),
            "set_world_entity_state": ("entity_id", "path"),
            "move_actor": ("actor_id",),
            "adjust_resource": ("path",),
            "advance_clock": ("clock_id",),
            "apply_ruleset_effect": ("event_type", "actor_id"),
            "register_entity": ("entity_id",),
            "register_clock": ("clock_id",),
            "register_resource": ("path",),
            "set_scene": (),
            "complete_run": (),
        }
        fields = target_fields.get(str(kind))
        if fields is None:
            return None
        return (kind, *(command.get(field) for field in fields))

    # A pushed failure escalates ordinary failure, while an explicit pushed
    # command replaces the ordinary mutation of the same authoritative target.
    # Thus a worse damage expression replaces ordinary damage, but an unrelated
    # fail-forward clue remains available after the pushed failure.
    for operator in operators.values():
        failure = list(operator.get("failure_commands", []))
        if not failure:
            continue
        for branch in operator.get("outcome_branches", []):
            if branch.get("outcome_key") != "pushed_failure":
                continue
            branch_commands = list(branch.get("commands", []))
            replaced_targets = {
                target
                for branch_command in branch_commands
                if (target := mutation_target(branch_command)) is not None
            }
            combined = [
                failure_command
                for failure_command in failure
                if mutation_target(failure_command) not in replaced_targets
            ]
            for branch_command in branch_commands:
                if branch_command not in combined:
                    combined.append(branch_command)
            branch["commands"] = combined

    for clue in payload.get("clues", []):
        content = tuple(str(item) for item in clue.get("public_content", []))
        fact_path = clue.get("fact_path")
        if not content or not fact_path:
            continue
        command = {
            "kind": "set_fact",
            "path": fact_path,
            "value": clue.get("fact_value", True),
            "entity_id": None,
            "actor_id": None,
            "clock_id": None,
            "delta": None,
            "event_type": None,
            "payload": {},
        }
        for operator_id in clue.get("discovery_operator_ids", []):
            operator = operators.get(str(operator_id))
            if operator is None:
                continue
            automatic = tuple(
                str(item) for item in operator.get("automatic_information", [])
            )
            if source_marks_automatic(clue, content):
                always = list(operator.get("always_commands", []))
                if not any(
                    same_fact(item, str(fact_path), command["value"])
                    for item in always
                ):
                    always.append(command)
                operator["always_commands"] = always
                for field in ("success_commands", "failure_commands"):
                    operator[field] = [
                        item
                        for item in operator.get(field, [])
                        if not same_fact(item, str(fact_path), command["value"])
                    ]
                for branch in operator.get("outcome_branches", []):
                    branch["commands"] = [
                        item
                        for item in branch.get("commands", [])
                        if not same_fact(item, str(fact_path), command["value"])
                    ]
                diagnostics.append(
                    "Server source-authorized automatic clue aligned: "
                    f"{operator_id} -> {clue['clue_id']}"
                )
                continue

            operator["automatic_information"] = [
                item for item in automatic if item not in content
            ]
            outcome_commands = {
                "success": operator.get("success_commands", []),
                "failure": operator.get("failure_commands", []),
                **{
                    str(branch.get("outcome_key")): branch.get("commands", [])
                    for branch in operator.get("outcome_branches", [])
                },
            }
            cues = list(operator.get("narrative_cues", []))
            cue_by_key = {str(cue.get("outcome_key")): cue for cue in cues}
            public_summary = "\n".join(content)[:1200]
            aligned_outcomes: list[str] = []
            for outcome_key, outcome_command_list in outcome_commands.items():
                if not any(
                    same_fact(item, str(fact_path), command["value"])
                    for item in outcome_command_list
                ):
                    continue
                existing = cue_by_key.get(outcome_key)
                if existing is None:
                    existing = {
                        "outcome_key": outcome_key,
                        "public_summary": public_summary,
                        "speaker_entity_id": None,
                        "tone": "",
                    }
                    cues.append(existing)
                    cue_by_key[outcome_key] = existing
                elif public_summary not in str(existing.get("public_summary", "")):
                    existing["public_summary"] = (
                        str(existing.get("public_summary", ""))
                        + "\n"
                        + public_summary
                    )[:1200]
                aligned_outcomes.append(outcome_key)
            if cues and not str(operator.get("public_setup", "")).strip():
                operator["public_setup"] = "The action enters resolution."
            operator["narrative_cues"] = cues[:8]
            if aligned_outcomes:
                diagnostics.append(
                    "Server outcome-bound clue narration aligned: "
                    f"{operator_id} -> {clue['clue_id']}"
                )
    return payload, tuple(dict.fromkeys(diagnostics))


__all__ = ["align_automatic_clue_state"]
