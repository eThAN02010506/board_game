"""Deterministic command-local repair for source-authored ruleset effects."""

from __future__ import annotations

import json
from typing import Literal

from ai_kp.platform.resolution.contracts import WorldCommand
from ai_kp.platform.resolution.effect_catalog import ScenarioEffectCatalog
from ai_kp.platform.resolution.scenario_ir_models import ScenarioIrBatch

EffectOutcome = Literal["always", "success", "failure", "pushed_failure"]


def _source_candidates(
    catalog: ScenarioEffectCatalog,
    *,
    source_ids: tuple[str, ...],
    source_texts: dict[str, str],
    outcome: EffectOutcome,
) -> tuple[WorldCommand, ...]:
    candidates: dict[str, WorldCommand] = {}
    for source_id in source_ids:
        source_text = source_texts.get(source_id)
        if source_text is None:
            continue
        for extracted in catalog.extract_source_effects(source_text):
            candidate_payload = extracted.payload
            split = catalog.split_outcome_payload(
                extracted.effect_key, extracted.payload
            )
            if split is not None and outcome in {"success", "failure"}:
                candidate_payload = split[0 if outcome == "success" else 1]
            elif extracted.outcome not in {outcome, "always"}:
                continue
            if catalog.validate_effect(extracted.effect_key, candidate_payload):
                continue
            candidate = WorldCommand(
                kind="apply_ruleset_effect",
                event_type=extracted.effect_key,
                payload=candidate_payload,
            )
            signature = json.dumps(
                candidate.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            candidates[signature] = candidate
    return tuple(candidates.values())


def contract_invalid_ruleset_effects(
    batch: ScenarioIrBatch,
    catalog: ScenarioEffectCatalog | None,
    *,
    source_texts: dict[str, str],
) -> ScenarioIrBatch:
    """Replace source-unique bad commands, otherwise delete only that command."""

    if catalog is None:
        return batch
    changes: list[str] = []

    def contract_commands(
        commands: tuple[WorldCommand, ...],
        *,
        source_ids: tuple[str, ...],
        outcome: EffectOutcome,
        address: str,
    ) -> tuple[WorldCommand, ...]:
        retained: list[WorldCommand] = []
        for command_index, command in enumerate(commands):
            if command.kind != "apply_ruleset_effect" or not catalog.validate_effect(
                str(command.event_type), command.payload
            ):
                retained.append(command)
                continue
            candidates = _source_candidates(
                catalog,
                source_ids=source_ids,
                source_texts=source_texts,
                outcome=outcome,
            )
            child_address = f"{address}[{command_index}]"
            if len(candidates) == 1:
                retained.append(candidates[0])
                changes.append(f"{child_address} replaced from unique source effect")
            else:
                changes.append(f"{child_address} removed after catalog rejection")
        return tuple(retained)

    actions = tuple(
        action.model_copy(
            update={
                branch: contract_commands(
                    getattr(action, branch),
                    source_ids=action.source_block_ids,
                    outcome=outcome,
                    address=f"actions.{action.id}.{branch}",
                )
                for branch, outcome in (
                    ("always", "always"),
                    ("on_success", "success"),
                    ("on_failure", "failure"),
                    ("on_pushed_failure", "pushed_failure"),
                )
            }
        )
        for action in batch.actions
    )
    policies = tuple(
        policy.model_copy(
            update={
                "rules": tuple(
                    rule.model_copy(
                        update={
                            "commands": contract_commands(
                                rule.commands,
                                source_ids=policy.source_block_ids,
                                outcome="always",
                                address=(
                                    f"reactive_policies.{policy.id}.rules."
                                    f"{rule.rule_id}.commands"
                                ),
                            )
                        }
                    )
                    for rule in policy.rules
                )
            }
        )
        for policy in batch.reactive_policies
    )
    clocks = tuple(
        clock.model_copy(
            update={
                "pressure_stages": tuple(
                    stage.model_copy(
                        update={
                            "commands": contract_commands(
                                stage.commands,
                                source_ids=clock.source_block_ids,
                                outcome="always",
                                address=(
                                    f"clocks.{clock.id}.pressure_stages."
                                    f"{stage.threshold}.commands"
                                ),
                            )
                        }
                    )
                    for stage in clock.pressure_stages
                )
            }
        )
        for clock in batch.clocks
    )
    endings = tuple(
        ending.model_copy(
            update={
                "commands": contract_commands(
                    ending.commands,
                    source_ids=ending.source_block_ids,
                    outcome="always",
                    address=f"endings.{ending.id}.commands",
                )
            }
        )
        for ending in batch.endings
    )
    if not changes:
        return batch
    note = "Catalog-invalid ruleset effects contracted locally: " + "; ".join(changes)
    return batch.model_copy(
        update={
            "actions": actions,
            "reactive_policies": policies,
            "clocks": clocks,
            "endings": endings,
            "assumptions": tuple(dict.fromkeys((note, *batch.assumptions)))[:16],
        }
    )


__all__ = ["contract_invalid_ruleset_effects"]
