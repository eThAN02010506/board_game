from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedPlayer


class SessionService:
    """Coordinate session lifecycle operations on one repository transaction."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def create(
        self,
        campaign_id: str,
        *,
        title: str | None = None,
        kp_display_name: str = "KP",
    ) -> dict:
        return self.repo.create_campaign_session(
            campaign_id,
            title=title,
            kp_display_name=kp_display_name,
        )

    def join(
        self,
        join_code: str,
        *,
        display_name: str,
        pc_id: str | None = None,
    ) -> dict:
        return self.repo.join_campaign_session(
            join_code,
            display_name=display_name,
            pc_id=pc_id,
        )

    def revoke_member_and_rotate_code(self, session_id: str, member_id: str) -> dict:
        """Revoke a credential and invalidate the shared join code atomically."""

        seat = self.repo.seat_for_member(member_id)
        if seat is not None:
            self.repo.revoke_session_seat(session_id, str(seat["id"]))
            member = self.repo.get_session_member(member_id)
        else:
            member = self.repo.revoke_session_member(session_id, member_id)
        rotated = self.repo.rotate_session_join_code(session_id)
        return {"member": member, "join_code": rotated["join_code"]}

    def assign_member_pc(self, session_id: str, member_id: str, pc_id: str) -> dict:
        member = self.repo.assign_member_pc(session_id, member_id, pc_id)
        self.repo.sync_member_seat_pc(member_id, pc_id)
        return member

    def rotate_join_code(self, session_id: str) -> dict:
        return self.repo.rotate_session_join_code(session_id)

    def close(self, session_id: str) -> dict:
        return self.repo.close_campaign_session(session_id)

    def create_seat(
        self,
        session_id: str,
        *,
        label: str,
        kp_member_id: str,
        pc_id: str | None = None,
    ) -> dict:
        return self.repo.create_session_seat(
            session_id,
            label=label,
            created_by_member_id=kp_member_id,
            pc_id=pc_id,
        )

    def claim_seat(
        self,
        invitation_code: str,
        *,
        display_name: str,
        player: AuthenticatedPlayer | None,
    ) -> dict:
        player_token = None
        if player is None:
            created = self.repo.create_player_profile(display_name)
            profile = created["profile"]
            player_token = created["player_token"]
        else:
            profile = self.repo.get_player_profile(player.profile_id)
        result = self.repo.claim_session_seat(
            invitation_code,
            player_profile_id=str(profile["id"]),
            display_name=str(profile["display_name"]),
        )
        result["profile"] = profile
        if player_token is not None:
            result["player_token"] = player_token
        return result

    def recover_seat(self, seat_id: str, player: AuthenticatedPlayer) -> dict:
        return self.repo.recover_session_seat(seat_id, player.profile_id)

    def list_player_seats(self, player: AuthenticatedPlayer) -> list[dict]:
        return self.repo.list_player_session_seats(player.profile_id)

    def reissue_seat_invitation(self, session_id: str, seat_id: str) -> dict:
        return self.repo.reissue_seat_invitation(session_id, seat_id)

    def revoke_seat(self, session_id: str, seat_id: str) -> dict:
        return self.repo.revoke_session_seat(session_id, seat_id)

    def assign_seat_pc(
        self,
        session_id: str,
        seat_id: str,
        pc_id: str | None,
    ) -> dict:
        return self.repo.assign_session_seat_pc(session_id, seat_id, pc_id)
