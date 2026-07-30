"""In-process cancellation registry for campaign-scoped game AI calls."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager


class CampaignAiCallRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, set[asyncio.Task]] = {}

    @contextmanager
    def track(self, campaign_id: str) -> Iterator[None]:
        task = asyncio.current_task()
        if task is None:
            yield
            return
        tasks = self._tasks.setdefault(campaign_id, set())
        tasks.add(task)
        try:
            yield
        finally:
            tasks.discard(task)
            if not tasks:
                self._tasks.pop(campaign_id, None)

    def cancel_campaign(self, campaign_id: str) -> int:
        tasks = tuple(self._tasks.get(campaign_id, ()))
        for task in tasks:
            task.cancel()
        return len(tasks)

    def active_count(self, campaign_id: str) -> int:
        return len(self._tasks.get(campaign_id, ()))


__all__ = ["CampaignAiCallRegistry"]
