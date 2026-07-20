from dataclasses import dataclass

from ai_kp.core.repository import Repository


@dataclass(frozen=True)
class CreateCampaignCommand:
    title: str
    system: str = "coc7"
    current_time: str | None = None


class CampaignService:
    """Campaign catalogue use cases, independent from the HTTP transport."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def create(self, command: CreateCampaignCommand) -> dict:
        return self.repo.create_campaign(
            command.title,
            command.system,
            command.current_time,
        )

    def list_accessible(self, campaign_id: str | None = None) -> list[dict]:
        """Return either the caller's campaign or the local-admin catalogue."""

        if campaign_id is not None:
            return [self.repo.get_campaign(campaign_id)]
        return self.repo.list_campaigns()
