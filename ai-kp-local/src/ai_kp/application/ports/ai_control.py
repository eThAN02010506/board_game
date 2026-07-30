"""Read boundary for campaign-wide game AI control."""

from typing import Protocol


class AiControlStore(Protocol):
    def get_active_campaign_module_run(self, campaign_id: str) -> dict | None: ...


__all__ = ["AiControlStore"]
