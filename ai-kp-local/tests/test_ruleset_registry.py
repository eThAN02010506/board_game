import pytest

from ai_kp.application.campaign_service import CampaignService, CreateCampaignCommand
from ai_kp.rulesets import get_ruleset, list_rulesets


class _CampaignRepo:
    def __init__(self) -> None:
        self.created: tuple[str, str, str | None] | None = None

    def create_campaign(
        self, title: str, system: str, current_time: str | None
    ) -> dict:
        self.created = (title, system, current_time)
        return {"id": "camp_test", "title": title, "system": system}


def test_only_coc7_is_installed_and_aliases_resolve_to_one_plugin() -> None:
    manifests = list_rulesets()

    assert len(manifests) == 1
    assert manifests[0]["slug"] == "coc7"
    assert manifests[0]["ruleset_id"] == "coc7-keeper-cn-2002c"
    assert get_ruleset("coc7") is get_ruleset("coc7-keeper-cn-2002c")


def test_unknown_ruleset_is_not_enabled_by_uploading_a_book() -> None:
    with pytest.raises(ValueError, match="does not install an executable ruleset"):
        get_ruleset("cyberpunk-red")


def test_campaigns_store_the_canonical_installed_ruleset_slug() -> None:
    repo = _CampaignRepo()
    service = CampaignService(repo)  # type: ignore[arg-type]

    campaign = service.create(
        CreateCampaignCommand(title="Test", system="call-of-cthulhu-7e")
    )

    assert campaign["system"] == "coc7"
    assert repo.created == ("Test", "coc7", None)
