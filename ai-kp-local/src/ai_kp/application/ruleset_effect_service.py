"""Consume persisted scenario effect commands through the installed ruleset."""

from __future__ import annotations

import hashlib
from typing import Any

from ai_kp.application.gameplay_service import GameplayCommand, GameplayService
from ai_kp.platform.resolution.contracts import WorldCommand
from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.rulesets import get_campaign_ruleset


class RulesetEffectService:
    def __init__(self, repo: Any):
        self.repo = repo

    def apply_batch(
        self,
        batch: dict[str, Any],
        identity: AuthenticatedMember,
        *,
        campaign_id: str,
        fallback_actor_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.repo.begin_immediate()
        savepoint = "ruleset_effect_" + hashlib.sha256(
            str(batch.get("id") or "missing").encode()
        ).hexdigest()[:16]
        self.repo.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            result = self._apply_batch_locked(
                batch,
                identity,
                campaign_id=campaign_id,
                fallback_actor_id=fallback_actor_id,
            )
            self.repo.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return result
        except Exception:
            self.repo.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.repo.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def _apply_batch_locked(
        self,
        batch: dict[str, Any],
        identity: AuthenticatedMember,
        *,
        campaign_id: str,
        fallback_actor_id: str | None = None,
    ) -> list[dict[str, Any]]:
        commands = tuple(WorldCommand.model_validate(item) for item in batch["commands"])
        effects = [
            (index, item)
            for index, item in enumerate(commands)
            if item.kind == "apply_ruleset_effect"
        ]
        if not effects:
            return []
        if identity.role != "kp" or identity.campaign_id != campaign_id:
            raise PermissionError("Ruleset effects require the campaign KP authority")
        campaign = self.repo.get_campaign(campaign_id)
        ruleset = get_campaign_ruleset(campaign)
        catalog = ruleset.scenario_effect_catalog()
        results: list[dict[str, Any]] = []
        for command_index, effect in effects:
            actor_id = effect.actor_id or fallback_actor_id
            if not actor_id:
                raise ValueError(
                    "Actor-targeted ruleset effects require an approved investigator"
                )
            try:
                investigator = self.repo.get_campaign_investigator_by_legacy_pc(
                    campaign_id, str(actor_id)
                )
            except KeyError as exc:
                raise ValueError(
                    "Actor-targeted ruleset effects require an approved investigator"
                ) from exc
            effect_key = str(effect.event_type)
            errors = catalog.validate_effect(effect_key, effect.payload)
            if errors:
                raise ValueError("Invalid ruleset effect: " + "; ".join(errors))
            transition = ruleset.translate_scenario_effect(effect_key, effect.payload)
            current = self.repo.get_campaign_investigator(
                campaign_id, str(investigator["investigator_id"])
            )
            state = current.get("campaign_state")
            if state is None:
                raise ValueError("Ruleset effect target has no campaign state")
            primary = GameplayService(self.repo).command_character(
                campaign_id,
                str(investigator["investigator_id"]),
                identity,
                GameplayCommand(
                    command_id=f"kernel-effect:{batch['id']}:{command_index}",
                    expected_version=int(state["state_version"]),
                    command_type=transition.command_type,
                    payload=dict(transition.payload),
                    visibility="table",
                ),
            )
            primary_result = dict(primary.get("event", {}).get("result") or {})
            follow_up_results: list[dict[str, Any]] = []
            for follow_index, follow_up in enumerate(transition.follow_ups):
                if not bool(primary_result.get(follow_up.result_flag)):
                    continue
                command_id = (
                    f"kernel-effect:{batch['id']}:{command_index}:follow:{follow_index}"
                )
                existing = self.repo.find_coc7_gameplay_event(
                    identity.session_id, command_id
                )
                if existing is not None:
                    current = self.repo.get_campaign_investigator(
                        campaign_id, str(investigator["investigator_id"])
                    )
                    follow_up_results.append(
                        {
                            "state": current["campaign_state"],
                            "event": existing,
                            "idempotent_replay": True,
                        }
                    )
                    continue
                current = self.repo.get_campaign_investigator(
                    campaign_id, str(investigator["investigator_id"])
                )
                current_state = current.get("campaign_state")
                if current_state is None:
                    raise ValueError("Ruleset follow-up target has no campaign state")
                follow_up_results.append(
                    GameplayService(self.repo).command_character(
                        campaign_id,
                        str(investigator["investigator_id"]),
                        identity,
                        GameplayCommand(
                            command_id=command_id,
                            expected_version=int(current_state["state_version"]),
                            command_type=follow_up.command_type,
                            payload=dict(follow_up.payload),
                            visibility="table",
                        ),
                    )
                )
            primary["follow_ups"] = follow_up_results
            results.append(primary)
        return results


__all__ = ["RulesetEffectService"]
