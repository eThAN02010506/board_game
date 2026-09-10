"""Read authorization must use committed membership, not a cached socket role."""

import pytest

from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db


@pytest.fixture
def room(tmp_path):
    path = tmp_path / "authority.sqlite3"
    connection = connect(path)
    init_db(connection)
    repo = Repository(connection)
    campaign = repo.create_campaign(title="Authority")
    bundle = repo.create_campaign_session(campaign["id"])
    repo.commit()
    members = {"kp": bundle["member"]["id"]}
    for role in ("player", "observer"):
        joined = repo.join_campaign_session(bundle["join_code"], display_name=role, role=role)
        members[role] = joined["member"]["id"]
        repo.commit()
    scope = {"session_id": bundle["session"]["id"], "campaign_id": campaign["id"]}
    events = {}
    for name, audience, event_type, member in (
        ("public", "session", "world.updated", None),
        ("action", "session", "player_action.created", None),
        ("secret", "kp", "proposal.created", None),
        ("private", "member", "table_message.created", members["player"]),
    ):
        events[name] = repo.append_realtime_event(
            **scope, audience=audience, event_type=event_type, member_id=member
        )
    repo.commit()
    try:
        yield path, repo, scope, members, events
    finally:
        connection.close()


def assert_visibility(repo, session_id, member_id, role, events, expected):
    scope = {"session_id": session_id, "member_id": member_id, "role": role}
    visible = {event["id"] for event in repo.list_visible_realtime_events(**scope)}
    for name, event in events.items():
        assert (event["id"] in visible) == (name in expected)
        assert repo.resolve_visible_realtime_cursor(**scope, event_key=event["event_key"]) == (
            event["id"] if name in expected else None
        )


@pytest.mark.parametrize(
    "role,expected",
    [
        ("kp", {"public", "action", "secret"}),
        ("player", {"public", "action", "private"}),
        ("observer", {"public"}),
    ],
)
def test_event_and_cursor_share_audience_rules(room, role, expected):
    _, repo, scope, members, events = room
    assert_visibility(repo, scope["session_id"], members[role], role, events, expected)


@pytest.mark.parametrize(
    "identity,role", [("player", "kp"), ("missing", "kp"), ("observer", "player")]
)
def test_claimed_role_cannot_grant_visibility(room, identity, role):
    _, repo, scope, members, events = room
    assert_visibility(
        repo, scope["session_id"], members.get(identity, "missing"), role, events, set()
    )


@pytest.mark.parametrize("transition", ["demote", "revoke", "close"])
def test_committed_authority_change_invalidates_cached_kp_scope(room, transition):
    path, repo, scope, members, events = room
    assert_visibility(
        repo, scope["session_id"], members["kp"], "kp", events, {"public", "action", "secret"}
    )
    writer = connect(path)
    try:
        # Simulate a committed authority change between socket authentication
        # and the next read, using a separate connection like the HTTP adapter.
        if transition == "demote":
            writer.execute(
                "UPDATE session_members SET role = 'player' WHERE id = ?", (members["kp"],)
            )
        elif transition == "revoke":
            writer.execute(
                "UPDATE session_members SET revoked_at = CURRENT_TIMESTAMP WHERE id = ?",
                (members["kp"],),
            )
        else:
            writer.execute(
                "UPDATE campaign_sessions SET status = 'closed' WHERE id = ?",
                (scope["session_id"],),
            )
        writer.commit()
    finally:
        writer.close()
    assert_visibility(repo, scope["session_id"], members["kp"], "kp", events, set())


def test_invalid_role_rejected_by_both_queries(room):
    _, repo, scope, members, events = room
    with pytest.raises(ValueError, match="Unsupported realtime role"):
        assert_visibility(repo, scope["session_id"], members["kp"], "admin", events, set())
    with pytest.raises(ValueError, match="Unsupported realtime role"):
        repo.resolve_visible_realtime_cursor(
            session_id=scope["session_id"],
            member_id=members["kp"],
            role="admin",
            event_key=events["public"]["event_key"],
        )
