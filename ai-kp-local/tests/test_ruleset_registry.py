import pytest

from ai_kp.application.campaign_service import CampaignService, CreateCampaignCommand
from ai_kp.rulesets import get_ruleset, list_rulesets


class _CampaignRepo:
    def __init__(self) -> None:
        self.created: dict | None = None

    def create_campaign(
        self,
        title: str,
        system: str,
        current_time: str | None,
        **ruleset_pin,
    ) -> dict:
        self.created = {
            "title": title,
            "system": system,
            "current_time": current_time,
            **ruleset_pin,
        }
        return {"id": "camp_test", **self.created}


def test_only_coc7_is_installed_and_aliases_resolve_to_one_plugin() -> None:
    manifests = list_rulesets()

    assert len(manifests) == 1
    assert manifests[0]["slug"] == "coc7"
    assert manifests[0]["ruleset_id"] == "coc7-keeper-cn-2002c"
    assert manifests[0]["support_level"] == "playable_alpha"
    assert get_ruleset("coc7") is get_ruleset("coc7-keeper-cn-2002c")
    assert get_ruleset("coc7", version="2002c") is get_ruleset("coc7")
    with pytest.raises(ValueError, match="version is not installed"):
        get_ruleset("coc7", version="future")


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
    assert repo.created == {
        "title": "Test",
        "system": "coc7",
        "current_time": None,
        "ruleset_id": "coc7-keeper-cn-2002c",
        "ruleset_version": "2002c",
        "character_schema_version": "coc7-investigator-v1",
        "event_schema_version": "coc7-event-v1",
        "session_zero_required": True,
    }
