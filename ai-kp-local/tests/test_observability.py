from ai_kp.observability import OperationalTelemetry


def test_operational_metrics_report_percentiles_and_alerts() -> None:
    telemetry = OperationalTelemetry()
    for value in (10, 20, 30, 100):
        telemetry.record_request(value)
    telemetry.record_lock_wait(150)
    telemetry.record_retrieval(
        {"mean_recall_at_k": 0.5, "forbidden_hit_count": 1}
    )
    snapshot = telemetry.snapshot()
    assert snapshot["latency_ms"]["p50"] == 30
    assert snapshot["latency_ms"]["p95"] == 100
    assert snapshot["database_lock_wait_ms"]["max"] == 150
    assert {item["kind"] for item in snapshot["alerts"]} == {
        "database_lock_wait",
        "retrieval_quality_low",
        "secret_retrieval_hit",
    }
