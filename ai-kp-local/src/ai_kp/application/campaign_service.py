from dataclasses import dataclass

from ai_kp.application.ports.repositories import CampaignStore
from ai_kp.rulesets import get_ruleset


@dataclass(frozen=True)
class CreateCampaignCommand:
    title: str
    system: str = "coc7"
    current_time: str | None = None


class CampaignService:
    """Campaign catalogue use cases, independent from the HTTP transport."""

    def __init__(self, repo: CampaignStore):
        self.repo = repo

    def create(self, command: CreateCampaignCommand) -> dict:
        ruleset = get_ruleset(command.system)
        return self.repo.create_campaign(
            command.title,
            ruleset.manifest.slug,
            command.current_time,
        )

    def list_accessible(self, campaign_id: str | None = None) -> list[dict]:
        """Return either the caller's campaign or the local-admin catalogue."""

        if campaign_id is not None:
            return [self.repo.get_campaign(campaign_id)]
        return self.repo.list_campaigns()
