"""Production-service lifecycle exercise used by AC-LONG durability runs."""

from __future__ import annotations

from typing import Any

from ai_kp.application.character_lifecycle_service import (
    CharacterLifecycleService,
    LifecycleDecisionCommand,
    LifecycleProposalCommand,
)
from ai_kp.application.investigator_service import (
    CreateInvestigatorCommand,
    InvestigatorService,
)
from ai_kp.application.session_service import SessionService
from ai_kp.application.world_service import AppendEventCommand, WorldService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember


def bootstrap_lifecycle_players(
    repo: Repository,
    kp: AuthenticatedMember,
) -> dict[str, Any]:
    """Create four stable seats, approved PCs/spares, and one late-join seat."""

    session_service = SessionService(repo)
    players: list[dict[str, Any]] = []
    for index in range(1, 5):
        seat = session_service.create_seat(
            kp.session_id,
            label=f"Player {index}",
            kp_member_id=kp.member_id,
        )
        claimed = session_service.claim_seat(
            str(seat["invitation_code"]),
            display_name=f"Player {index}",
            player=None,
        )
        player = {
            "label": f"player-{index}",
            "access_token": str(claimed["access_token"]),
            "member_id": str(claimed["member"]["id"]),
            "profile_id": str(claimed["profile"]["id"]),
            "investigator_ids": [],
        }
        character_count = 3 if index == 1 else 1
        for character_index in range(1, character_count + 1):
            investigator_id = _create_approved_investigator(
                repo,
                campaign_id=kp.campaign_id,
                session_id=kp.session_id,
                kp_member_id=kp.member_id,
                member_id=player["member_id"],
                profile_id=player["profile_id"],
                name=f"Player {index} Investigator {character_index}",
                assign=character_index == 1,
            )
            player["investigator_ids"].append(investigator_id)
        player["current_investigator_id"] = player["investigator_ids"][0]
        repo.ensure_member_presence(
            campaign_id=kp.campaign_id,
            session_id=kp.session_id,
            member_id=player["member_id"],
            investigator_id=player["current_investigator_id"],
            state="active",
        )
        players.append(player)
    late_seat = session_service.create_seat(
        kp.session_id,
        label="Late Player",
        kp_member_id=kp.member_id,
    )
    return {
        "players": players,
        "late_player_invitation": str(late_seat["invitation_code"]),
    }


def join_late_player(
    repo: Repository,
    state: dict[str, Any],
    kp: AuthenticatedMember,
    *,
    session_number: int,
) -> dict[str, Any]:
    claimed = SessionService(repo).claim_seat(
        str(state.pop("late_player_invitation")),
        display_name="Late Player",
        player=None,
    )
    player = {
        "label": "late-player",
        "access_token": str(claimed["access_token"]),
        "member_id": str(claimed["member"]["id"]),
        "profile_id": str(claimed["profile"]["id"]),
        "investigator_ids": [],
    }
    investigator_id = _create_approved_investigator(
        repo,
        campaign_id=kp.campaign_id,
        session_id=kp.session_id,
        kp_member_id=kp.member_id,
        member_id=player["member_id"],
        profile_id=player["profile_id"],
        name="Late Player Investigator",
        assign=True,
    )
    player["investigator_ids"] = [investigator_id]
    player["current_investigator_id"] = investigator_id
    repo.ensure_member_presence(
        campaign_id=kp.campaign_id,
        session_id=kp.session_id,
        member_id=player["member_id"],
        investigator_id=investigator_id,
        state="active",
    )
    state["players"].append(player)
    return {
        "session": session_number,
        "action": "member_joined",
        "member_id": player["member_id"],
        "investigator_id": investigator_id,
    }


def exercise_lifecycle_session(
    repo: Repository,
    state: dict[str, Any],
    kp: AuthenticatedMember,
    *,
    session_number: int,
    world: WorldService,
) -> list[dict[str, Any]]:
    """Apply the scheduled multi-death/replacement/absence transitions."""

    primary = state["players"][0]
    second = state["players"][1]
    transitions: list[dict[str, Any]] = []
    if session_number in {7, 14}:
        death_event = world.append_event(
            kp.campaign_id,
            AppendEventCommand(
                actor_type="rules_engine",
                event_type="durability.character_death",
                summary=(
                    f"{primary['current_investigator_id']} died during "
                    f"Session {session_number}."
                ),
                visibility="table",
                happened_at=f"campaign-hour-{session_number * 3}",
            ),
        )
        result = CharacterLifecycleService(repo).sync_character_state(
            campaign_id=kp.campaign_id,
            session_id=kp.session_id,
            investigator_id=str(primary["current_investigator_id"]),
            character_state={"conditions": [{"type": "dead", "active": True}]},
            source_event_id=str(death_event["id"]),
            actor_member_id=kp.member_id,
        )
        if result is None:
            raise RuntimeError("Durability death did not transition lifecycle")
        transitions.append(
            {
                "session": session_number,
                "action": "ruleset_state_sync",
                "investigator_id": str(primary["current_investigator_id"]),
                "to_state": "dead",
            }
        )
    if session_number in {8, 15}:
        replacement_index = 1 if session_number == 8 else 2
        transitions.extend(
            _observe_and_replace(
                repo,
                primary,
                kp,
                session_number=session_number,
                replacement_id=str(primary["investigator_ids"][replacement_index]),
            )
        )
    if session_number == 9:
        transitions.append(
            _temporary_leave(repo, second, kp, session_number=session_number)
        )
    if session_number == 10:
        transitions.append(_return_player(repo, second, kp, session_number=session_number))
    return transitions


def summarize_lifecycle(
    repo: Repository,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    first_member = repo.get_session_member(state["players"][0]["member_id"])
    campaign_id = str(first_member["campaign_id"])
    session_id = str(first_member["session_id"])
    lifecycles = repo.list_investigator_lifecycles(campaign_id)
    presence = repo.list_member_presence(campaign_id, session_id)
    events = repo.list_lifecycle_events(campaign_id)
    action_counts = {
        action: sum(item["action"] == action for item in events)
        for action in (
            "ruleset_state_sync",
            "observe",
            "replace",
            "temporary_leave",
            "return",
        )
    }
    dead_ids = sorted(
        str(item["investigator_id"])
        for item in lifecycles
        if item["state"] == "dead"
    )
    active_presence = {
        str(item["member_id"]): str(item["investigator_id"])
        for item in presence
        if item["state"] == "active" and item["investigator_id"]
    }
    player_member_ids = {str(item["member_id"]) for item in state["players"]}
    late_join_count = sum(item["action"] == "member_joined" for item in evidence)
    passed = bool(
        action_counts["ruleset_state_sync"] == 2
        and action_counts["observe"] == 2
        and action_counts["replace"] == 2
        and action_counts["temporary_leave"] == 1
        and action_counts["return"] == 1
        and len(dead_ids) == 2
        and set(active_presence) == player_member_ids
        and len(player_member_ids) == 5
        and late_join_count == 1
    )
    return {
        "passed": passed,
        "member_count": len(player_member_ids),
        "late_join_count": late_join_count,
        "action_counts": action_counts,
        "dead_investigator_ids": dead_ids,
        "active_member_ids": sorted(active_presence),
        "active_investigator_ids": sorted(active_presence.values()),
        "evidence": evidence,
    }


def _create_approved_investigator(
    repo: Repository,
    *,
    campaign_id: str,
    session_id: str,
    kp_member_id: str,
    member_id: str,
    profile_id: str,
    name: str,
    assign: bool,
) -> str:
    service = InvestigatorService(repo)
    created = service.create_investigator(
        profile_id,
        CreateInvestigatorCommand(canonical_sheet=_durability_coc7_sheet(name)),
    )
    investigator_id = str(created["id"])
    submitted = service.submit_to_campaign(
        campaign_id=campaign_id,
        investigator_id=investigator_id,
        revision_id=str(created["current_revision_id"]),
        owner_profile_id=profile_id,
        member_id=member_id,
        session_id=session_id,
    )
    service.review(
        campaign_id=campaign_id,
        investigator_id=investigator_id,
        action="approved",
        comment="AC-LONG durability deterministic approval",
        kp_member_id=kp_member_id,
        session_id=session_id,
    )
    if assign:
        service.assign(
            session_id=session_id,
            member_id=member_id,
            investigator_id=investigator_id,
        )
    if submitted["status"] != "submitted":
        raise RuntimeError("Durability investigator did not enter review")
    return investigator_id


def _observe_and_replace(
    repo: Repository,
    player: dict[str, Any],
    kp: AuthenticatedMember,
    *,
    session_number: int,
    replacement_id: str,
) -> list[dict[str, Any]]:
    lifecycle = CharacterLifecycleService(repo)
    player_identity = _authenticate(repo, str(player["access_token"]))
    observe = lifecycle.propose(
        kp.campaign_id,
        kp,
        LifecycleProposalCommand(
            member_id=str(player["member_id"]),
            action="observe",
            reason="Dead investigator owner chooses observer mode",
        ),
    )
    lifecycle.decide(
        str(observe["id"]),
        player_identity,
        LifecycleDecisionCommand(
            action="accept",
            reason="Player confirms observer transition",
            expected_version=int(observe["version"]),
        ),
    )
    replace = lifecycle.propose(
        kp.campaign_id,
        kp,
        LifecycleProposalCommand(
            member_id=str(player["member_id"]),
            action="replace",
            reason="Continue with approved replacement investigator",
            replacement_investigator_id=replacement_id,
        ),
    )
    observer_identity = _authenticate(repo, str(player["access_token"]))
    lifecycle.decide(
        str(replace["id"]),
        observer_identity,
        LifecycleDecisionCommand(
            action="accept",
            reason="Player confirms replacement investigator",
            expected_version=int(replace["version"]),
        ),
    )
    player["current_investigator_id"] = replacement_id
    return [
        {"session": session_number, "action": "observe"},
        {
            "session": session_number,
            "action": "replace",
            "investigator_id": replacement_id,
        },
    ]


def _temporary_leave(
    repo: Repository,
    player: dict[str, Any],
    kp: AuthenticatedMember,
    *,
    session_number: int,
) -> dict[str, Any]:
    lifecycle = CharacterLifecycleService(repo)
    request = lifecycle.propose(
        kp.campaign_id,
        kp,
        LifecycleProposalCommand(
            member_id=str(player["member_id"]),
            action="temporary_leave",
            reason="Player is absent for the next Session",
        ),
    )
    lifecycle.decide(
        str(request["id"]),
        kp,
        LifecycleDecisionCommand(
            action="accept",
            reason="Session 0 absence policy applied",
            expected_version=int(request["version"]),
        ),
    )
    return {"session": session_number, "action": "temporary_leave"}


def _return_player(
    repo: Repository,
    player: dict[str, Any],
    kp: AuthenticatedMember,
    *,
    session_number: int,
) -> dict[str, Any]:
    lifecycle = CharacterLifecycleService(repo)
    request = lifecycle.propose(
        kp.campaign_id,
        kp,
        LifecycleProposalCommand(
            member_id=str(player["member_id"]),
            action="return",
            reason="Player returns after a temporary absence",
            replacement_investigator_id=str(player["current_investigator_id"]),
        ),
    )
    observer_identity = _authenticate(repo, str(player["access_token"]))
    lifecycle.decide(
        str(request["id"]),
        observer_identity,
        LifecycleDecisionCommand(
            action="accept",
            reason="Player confirms return to the same investigator",
            expected_version=int(request["version"]),
        ),
    )
    return {"session": session_number, "action": "return"}


def _authenticate(repo: Repository, token: str) -> AuthenticatedMember:
    identity = repo.authenticate_access_token(token)
    if identity is None:
        raise RuntimeError("Durability credential did not survive restart")
    return identity


def _durability_coc7_sheet(name: str) -> dict[str, Any]:
    return {
        "schema_version": "coc7-investigator-v1",
        "ruleset_id": "coc7-keeper-cn-2002c",
        "identity": {
            "name": name,
            "occupation": "Investigator",
            "age": 30,
            "era": "1920s",
        },
        "characteristics": {
            "str": 50,
            "con": 50,
            "siz": 50,
            "dex": 50,
            "app": 50,
            "int": 50,
            "pow": 50,
            "edu": 50,
            "luck": 50,
        },
        "skills": [],
        "assets": {"items": []},
        "background": {},
        "provenance": {"source_type": "ac-long-durability"},
    }


__all__ = [
    "bootstrap_lifecycle_players",
    "exercise_lifecycle_session",
    "join_late_player",
    "summarize_lifecycle",
]
