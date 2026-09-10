"""Test builders for the production investigator approval workflow.

These helpers deliberately use public HTTP endpoints. Integration tests should
not recreate the removed direct ``pc_id`` join/assignment shortcuts.
"""

from __future__ import annotations

from typing import Any


async def confirm_current_session_zero(
    client: Any,
    *,
    campaign_id: str,
    member_headers: tuple[dict[str, str], ...],
) -> dict[str, Any]:
    """Explicitly consent every listed table member to the current revision."""
    if not member_headers:
        raise ValueError("at least one table member must confirm Session 0")
    view_response = await client.get(
        f"/campaigns/{campaign_id}/session-zero",
        headers=member_headers[0],
    )
    view_response.raise_for_status()
    view = view_response.json()
    revision = view.get("revision")
    if revision is None:
        raise AssertionError("product campaign did not create a Session 0 revision")
    payload = {
        "revision_id": revision["id"],
        "expected_version": revision["version"],
    }
    for headers in member_headers:
        response = await client.post(
            f"/campaigns/{campaign_id}/session-zero/confirm",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        view = response.json()
    return view


def confirm_current_session_zero_sync(
    client: Any,
    *,
    campaign_id: str,
    member_headers: tuple[dict[str, str], ...],
) -> dict[str, Any]:
    """Synchronous counterpart used by TestClient integration tests."""
    if not member_headers:
        raise ValueError("at least one table member must confirm Session 0")
    view_response = client.get(
        f"/campaigns/{campaign_id}/session-zero",
        headers=member_headers[0],
    )
    view_response.raise_for_status()
    view = view_response.json()
    revision = view.get("revision")
    if revision is None:
        raise AssertionError("product campaign did not create a Session 0 revision")
    payload = {
        "revision_id": revision["id"],
        "expected_version": revision["version"],
    }
    for headers in member_headers:
        response = client.post(
            f"/campaigns/{campaign_id}/session-zero/confirm",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        view = response.json()
    return view


def coc7_sheet(
    name: str,
    *,
    occupation: str = "调查员",
    skills: dict[str, int] | None = None,
    characteristics: dict[str, int] | None = None,
    assets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attributes = {
        "str": 50,
        "con": 50,
        "siz": 50,
        "dex": 50,
        "app": 50,
        "int": 50,
        "pow": 50,
        "edu": 50,
        "luck": 50,
    }
    attributes.update(characteristics or {})
    return {
        "schema_version": "coc7-investigator-v1",
        "ruleset_id": "coc7-keeper-cn-2002c",
        "identity": {
            "name": name,
            "occupation": occupation,
            "age": 30,
            "era": "1920s",
        },
        "characteristics": attributes,
        "skills": [
            {
                "skill_key": f"test.{index}",
                "display_name": skill_name,
                "base_value": value,
                "occupation_points": 0,
                "interest_points": 0,
                "development_points": 0,
            }
            for index, (skill_name, value) in enumerate((skills or {}).items(), start=1)
        ],
        "assets": {"items": [], **(assets or {})},
        "background": {},
        "provenance": {"source_type": "test"},
    }


async def create_approved_player(
    client: Any,
    *,
    campaign: dict,
    session: dict,
    kp_headers: dict[str, str],
    display_name: str,
    sheet: dict[str, Any],
    assign: bool = True,
) -> dict[str, Any]:
    profile_response = await client.post(
        "/player-profiles",
        json={"display_name": display_name},
    )
    profile_response.raise_for_status()
    profile_bundle = profile_response.json()
    joined_response = await client.post(
        "/sessions/join",
        json={
            "join_code": session["join_code"],
            "display_name": display_name,
        },
    )
    joined_response.raise_for_status()
    joined = joined_response.json()
    player_headers = {
        "Authorization": f"Bearer {joined['access_token']}",
        "X-AI-KP-Player-Token": profile_bundle["player_token"],
    }
    investigator_response = await client.post(
        "/investigators",
        headers=player_headers,
        json={"canonical_sheet": sheet, "source_type": "manual"},
    )
    investigator_response.raise_for_status()
    investigator = investigator_response.json()
    submitted_response = await client.post(
        f"/campaigns/{campaign['id']}/investigators/{investigator['id']}/submit",
        headers=player_headers,
        json={"revision_id": investigator["current_revision_id"]},
    )
    submitted_response.raise_for_status()
    approved_response = await client.post(
        f"/campaigns/{campaign['id']}/investigators/{investigator['id']}/review",
        headers=kp_headers,
        json={"action": "approved", "comment": "integration test approval"},
    )
    approved_response.raise_for_status()
    approved = approved_response.json()
    if assign:
        assigned_response = await client.post(
            f"/sessions/{session['session']['id']}/members/"
            f"{joined['member']['id']}/assign-investigator",
            headers=kp_headers,
            json={"investigator_id": investigator["id"]},
        )
        assigned_response.raise_for_status()
        joined["member"] = assigned_response.json()
    pcs_response = await client.get(
        f"/campaigns/{campaign['id']}/pcs",
        headers=kp_headers,
    )
    pcs_response.raise_for_status()
    pc = next(
        item
        for item in pcs_response.json()
        if item["id"] == approved["legacy_pc_id"]
    )
    return {
        "profile": profile_bundle["profile"],
        "player_token": profile_bundle["player_token"],
        "bundle": joined,
        "headers": player_headers,
        "investigator": investigator,
        "approved": approved,
        "pc": pc,
    }


def create_approved_player_sync(
    client: Any,
    *,
    campaign: dict,
    session: dict,
    kp_headers: dict[str, str],
    display_name: str,
    sheet: dict[str, Any],
    assign: bool = True,
) -> dict[str, Any]:
    profile_response = client.post(
        "/player-profiles",
        json={"display_name": display_name},
    )
    profile_response.raise_for_status()
    profile_bundle = profile_response.json()
    joined_response = client.post(
        "/sessions/join",
        json={
            "join_code": session["join_code"],
            "display_name": display_name,
        },
    )
    joined_response.raise_for_status()
    joined = joined_response.json()
    player_headers = {
        "Authorization": f"Bearer {joined['access_token']}",
        "X-AI-KP-Player-Token": profile_bundle["player_token"],
    }
    investigator_response = client.post(
        "/investigators",
        headers=player_headers,
        json={"canonical_sheet": sheet, "source_type": "manual"},
    )
    investigator_response.raise_for_status()
    investigator = investigator_response.json()
    submitted_response = client.post(
        f"/campaigns/{campaign['id']}/investigators/{investigator['id']}/submit",
        headers=player_headers,
        json={"revision_id": investigator["current_revision_id"]},
    )
    submitted_response.raise_for_status()
    approved_response = client.post(
        f"/campaigns/{campaign['id']}/investigators/{investigator['id']}/review",
        headers=kp_headers,
        json={"action": "approved", "comment": "integration test approval"},
    )
    approved_response.raise_for_status()
    approved = approved_response.json()
    if assign:
        assigned_response = client.post(
            f"/sessions/{session['session']['id']}/members/"
            f"{joined['member']['id']}/assign-investigator",
            headers=kp_headers,
            json={"investigator_id": investigator["id"]},
        )
        assigned_response.raise_for_status()
        joined["member"] = assigned_response.json()
    pcs_response = client.get(
        f"/campaigns/{campaign['id']}/pcs",
        headers=kp_headers,
    )
    pcs_response.raise_for_status()
    pc = next(
        item
        for item in pcs_response.json()
        if item["id"] == approved["legacy_pc_id"]
    )
    return {
        "profile": profile_bundle["profile"],
        "player_token": profile_bundle["player_token"],
        "bundle": joined,
        "headers": player_headers,
        "investigator": investigator,
        "approved": approved,
        "pc": pc,
    }
