"""Director lifecycle failures shared by delivery adapters."""


class CampaignAiCallCancelledError(RuntimeError):
    """A persisted safety pause or human takeover cancelled an in-flight call."""


__all__ = ["CampaignAiCallCancelledError"]
