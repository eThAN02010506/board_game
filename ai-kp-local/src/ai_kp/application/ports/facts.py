"""Persistence boundary for the event-backed world-fact ledger."""

from typing import Protocol

from ai_kp.application.ports.repositories import RealtimeOutbox
from ai_kp.platform.facts import FactLedgerEntry


class FactStore(RealtimeOutbox, Protocol):
    def begin_immediate(self) -> None: ...

    def list_fact_entries(self, campaign_id: str) -> list[FactLedgerEntry]: ...

    def list_fact_heads(self, campaign_id: str) -> list[FactLedgerEntry]: ...

    def get_fact_head(self, campaign_id: str, fact_key: str) -> FactLedgerEntry: ...

    def find_active_fact(
        self,
        campaign_id: str,
        *,
        category: str,
        subject: str,
        predicate: str,
    ) -> FactLedgerEntry | None: ...

    def append_fact_entry(self, entry: FactLedgerEntry) -> FactLedgerEntry: ...


__all__ = ["FactStore"]
