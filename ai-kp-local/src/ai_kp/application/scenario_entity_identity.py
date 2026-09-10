"""Validate explicit identities against the module's current confirmed graph."""

from collections.abc import Iterable, Mapping
from typing import Any

from ai_kp.platform.resolution.contracts import ScenarioContract
from ai_kp.platform.resolution.scenario_compiler import ContractValidationIssue


def validate_scenario_entity_identities(
    contract: ScenarioContract,
    module_entities: Iterable[Mapping[str, Any]],
) -> tuple[ContractValidationIssue, ...]:
    confirmed = {str(item["id"]) for item in module_entities}
    seen: set[str] = set()
    issues = []
    for index, entity in enumerate(contract.entities):
        source_id = entity.module_entity_id
        if source_id is None:
            continue
        code = None
        if source_id not in confirmed:
            code = "unknown_module_entity_identity"
            message = "Entity identity must reference this module's current confirmed graph."
        elif source_id in seen:
            code = "duplicate_module_entity_identity"
            message = "One module entity cannot silently become two scenario identities."
        seen.add(source_id)
        if code is not None:
            issues.append(
                ContractValidationIssue(
                    severity="error",
                    code=code,
                    path=f"entities.{index}.module_entity_id",
                    message=message,
                )
            )
    return tuple(issues)
