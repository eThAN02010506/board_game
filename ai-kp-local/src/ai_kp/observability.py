"""Bounded, process-local operational metrics for a local-first deployment."""

from __future__ import annotations

import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any


class OperationalTelemetry:
    def __init__(self, max_samples: int = 1000):
        self._lock = threading.Lock()
        self.request_ms: deque[float] = deque(maxlen=max_samples)
        self.db_lock_wait_ms: deque[float] = deque(maxlen=max_samples)
        self.retrieval_reports: deque[dict[str, Any]] = deque(maxlen=100)
        self.alerts: deque[dict[str, Any]] = deque(maxlen=250)

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
        return round(ordered[index], 2)

    def record_request(self, duration_ms: float) -> None:
        with self._lock:
            self.request_ms.append(duration_ms)

    def record_lock_wait(self, duration_ms: float) -> None:
        with self._lock:
            self.db_lock_wait_ms.append(duration_ms)
            if duration_ms >= 100:
                self._alert_unlocked(
                    "database_lock_wait",
                    "warning",
                    {"duration_ms": round(duration_ms, 2)},
                )

    def record_retrieval(self, report: dict[str, Any]) -> None:
        with self._lock:
            payload = {"at": datetime.now(UTC).isoformat(), **report}
            self.retrieval_reports.appendleft(payload)
            if int(report.get("forbidden_hit_count", 0)) > 0:
                self._alert_unlocked("secret_retrieval_hit", "critical", payload)
            if float(report.get("mean_recall_at_k", 1.0)) < 0.7:
                self._alert_unlocked("retrieval_quality_low", "warning", payload)

    def record_secret_leak(self, source: str, details: dict[str, Any]) -> None:
        with self._lock:
            self._alert_unlocked("secret_leakage", "critical", {"source": source, **details})

    def _alert_unlocked(self, kind: str, severity: str, details: dict[str, Any]) -> None:
        self.alerts.appendleft(
            {
                "at": datetime.now(UTC).isoformat(),
                "kind": kind,
                "severity": severity,
                "details": details,
            }
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            request = list(self.request_ms)
            locks = list(self.db_lock_wait_ms)
            return {
                "latency_ms": {
                    "samples": len(request),
                    "p50": self._percentile(request, 0.50),
                    "p95": self._percentile(request, 0.95),
                    "p99": self._percentile(request, 0.99),
                },
                "database_lock_wait_ms": {
                    "samples": len(locks),
                    "p50": self._percentile(locks, 0.50),
                    "p95": self._percentile(locks, 0.95),
                    "p99": self._percentile(locks, 0.99),
                    "max": round(max(locks), 2) if locks else 0.0,
                },
                "retrieval": list(self.retrieval_reports),
                "alerts": list(self.alerts),
            }


operational_telemetry = OperationalTelemetry()

__all__ = ["OperationalTelemetry", "operational_telemetry"]
