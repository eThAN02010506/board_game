"""Fail-fast in-process capacity gate for human-KP help calls."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Lock


class DirectorHelpRunInProgressError(RuntimeError):
    """The requested module run already owns a Need Help slot."""

    code = "director_help_in_progress"


class DirectorHelpCapacityExceededError(RuntimeError):
    """Every configured Need Help slot is currently occupied."""

    code = "director_help_capacity_exceeded"


class DirectorHelpCallGate:
    """Reserve bounded Need Help slots without queueing or sharing results."""

    def __init__(self, max_concurrency: int):
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or not 1 <= max_concurrency <= 32
        ):
            raise ValueError("Need Help concurrency must be an integer from 1 to 32")
        self.max_concurrency = max_concurrency
        self._active_run_ids: set[str] = set()
        self._lock = Lock()

    @asynccontextmanager
    async def reserve(self, run_id: str) -> AsyncIterator[None]:
        """Hold one slot or fail immediately, then always release it."""

        normalized_run_id = run_id.strip() if isinstance(run_id, str) else ""
        if not normalized_run_id:
            raise ValueError("Need Help run ID is required")
        with self._lock:
            if normalized_run_id in self._active_run_ids:
                raise DirectorHelpRunInProgressError(
                    "A Need Help request is already in progress for this module run"
                )
            if len(self._active_run_ids) >= self.max_concurrency:
                raise DirectorHelpCapacityExceededError(
                    "Need Help model capacity is currently full"
                )
            self._active_run_ids.add(normalized_run_id)
        try:
            yield
        finally:
            with self._lock:
                self._active_run_ids.discard(normalized_run_id)

    def active_count(self) -> int:
        """Return the current reservation count for diagnostics and tests."""

        with self._lock:
            return len(self._active_run_ids)


__all__ = [
    "DirectorHelpCallGate",
    "DirectorHelpCapacityExceededError",
    "DirectorHelpRunInProgressError",
]
