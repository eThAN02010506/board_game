from ai_kp.core.repository import Repository


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

        member = self.repo.revoke_session_member(session_id, member_id)
        rotated = self.repo.rotate_session_join_code(session_id)
        return {"member": member, "join_code": rotated["join_code"]}

    def assign_member_pc(self, session_id: str, member_id: str, pc_id: str) -> dict:
        return self.repo.assign_member_pc(session_id, member_id, pc_id)

    def rotate_join_code(self, session_id: str) -> dict:
        return self.repo.rotate_session_join_code(session_id)

    def close(self, session_id: str) -> dict:
        return self.repo.close_campaign_session(session_id)
