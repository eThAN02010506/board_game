"""One atomic consumer for persisted character and world-entity effects."""

from __future__ import annotations

import hashlib
from typing import Any

from ai_kp.application.ruleset_effect_service import RulesetEffectService
from ai_kp.application.world_entity_state_service import (
    SetWorldEntityStateCommand,
    WorldEntityStateService,
)
from ai_kp.platform.resolution.contracts import WorldCommand
from ai_kp.platform.sessions.models import AuthenticatedMember


class ScenarioEffectService:
    def __init__(self, repo: Any):
        self.repo = repo

    def apply_batch(
        self,
        batch: dict[str, Any],
        identity: AuthenticatedMember,
        *,
        campaign_id: str,
        fallback_actor_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        self.repo.begin_immediate()
        savepoint = "scenario_effect_" + hashlib.sha256(str(batch["id"]).encode()).hexdigest()[:16]
        self.repo.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            # Never allow a caller-provided command list to masquerade as a receipt.
            saved = self.repo.get_scenario_command_batch_by_key(
                batch["run_id"], batch["idempotency_key"]
            )
            if (
                saved is None
                or saved["id"] != batch["id"]
                or saved["commands"] != batch["commands"]
            ):
                raise ValueError("Scenario effects require an unchanged persisted batch")
            run = self.repo.get_campaign_module_run(saved["run_id"])
            if run["campaign_id"] != campaign_id:
                raise PermissionError("Scenario effect batch belongs to another campaign")
            ruleset_results = RulesetEffectService(self.repo).apply_batch(
                saved, identity, campaign_id=campaign_id, fallback_actor_id=fallback_actor_id
            )
            world_results = []
            for index, raw in enumerate(saved["commands"]):
                command = WorldCommand.model_validate(raw)
                if command.kind == "set_world_entity_state":
                    world_results.append(
                        self._apply_world_state(
                            saved, index, command, identity, campaign_id=campaign_id
                        )
                    )
            self.repo.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return {"ruleset_effects": ruleset_results, "world_entity_effects": world_results}
        except Exception:
            self.repo.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.repo.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def _apply_world_state(
        self,
        batch: dict[str, Any],
        index: int,
        command: WorldCommand,
        identity: AuthenticatedMember,
        *,
        campaign_id: str,
    ) -> dict[str, Any]:
        key = f"kernel-world:{batch['id']}:{index}"
        receipt = self.repo.find_campaign_world_entity_state_change_by_key(campaign_id, key)
        if receipt is not None:
            # Reuse the original optimistic version, not today's entity version.
            # The shared ledger checks the full command hash before returning it.
            entity_id = receipt["entity_id"]
            version = receipt["state_version"] - 1
        else:
            binding = self.repo.get_module_run_contract_binding(batch["run_id"])
            if binding["contract_hash"] != batch["authority_basis"]["contract_hash"]:
                raise ValueError("World state effect contract authority changed")
            source_id = next(
                (
                    entity.module_entity_id
                    for entity in binding["contract"].entities
                    if entity.entity_id == command.entity_id
                ),
                None,
            )
            if source_id is None:
                raise ValueError("World state effect has no explicit source entity identity")
            run = self.repo.get_campaign_module_run(batch["run_id"])
            if source_id not in {
                item["id"] for item in self.repo.list_module_entities(run["module_id"])
            }:
                raise ValueError("World state effect source entity is no longer confirmed")
            entity = self.repo.find_campaign_world_entity_by_origin(
                campaign_id, "module_source", source_id
            )
            if entity is None:
                raise ValueError("World state effect source entity has not been materialized")
            entity_id, version = entity["id"], entity["state_version"]
        return WorldEntityStateService(self.repo).set_state(
            campaign_id,
            entity_id,
            identity,
            SetWorldEntityStateCommand(
                expected_version=version,
                dimension=str(command.path),
                value=command.value,
                visibility=command.payload["visibility"],
                idempotency_key=key,
                source_kind="rules_kernel",
            ),
        )
