"""Deterministic contract runner for multi-turn simulated campaigns."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from ai_kp.observability import operational_telemetry

RUNNER_VERSION = "simulation.v1"


def _hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def run_simulation(definition: dict[str, Any]) -> dict[str, Any]:
    steps = definition.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 500:
        raise ValueError("Simulation requires 1-500 steps")
    public: list[str] = []
    secrets: list[str] = []
    trajectory: list[dict[str, Any]] = []
    passed = 0
    failed = 0
    leakage_failures = 0
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise TypeError(f"Step {index + 1} must be an object")
        action = raw.get("action")
        record: dict[str, Any] = {"index": index, "action": action}
        if action == "emit":
            content = str(raw.get("content") or "").strip()
            audience = raw.get("audience")
            if not content or audience not in {"player", "kp"}:
                raise ValueError(f"Invalid emit step {index + 1}")
            (public if audience == "player" else secrets).append(content)
            record["audience"] = audience
            record["content_hash"] = _hash(content)
        elif action in {"assert_player_sees", "assert_player_not_sees"}:
            needle = str(raw.get("text") or "")
            visible = any(needle in item for item in public)
            expected = action == "assert_player_sees"
            ok = visible is expected
            passed += int(ok)
            failed += int(not ok)
            if action == "assert_player_not_sees" and not ok:
                leakage_failures += 1
            record.update({"assertion": action, "passed": ok, "text_hash": _hash(needle)})
        elif action == "reveal":
            needle = str(raw.get("text") or "")
            matches = [item for item in secrets if needle in item]
            if not matches:
                raise ValueError(f"Reveal step {index + 1} has no matching KP secret")
            public.extend(matches)
            record.update({"revealed_count": len(matches), "text_hash": _hash(needle)})
        else:
            raise ValueError(f"Unsupported simulation action at step {index + 1}: {action}")
        record["public_count"] = len(public)
        record["secret_count"] = len(secrets)
        trajectory.append(record)
    metrics = {
        "assertions": passed + failed,
        "passed_assertions": passed,
        "failed_assertions": failed,
        "steps": len(steps),
    }
    result = {
        "runner_version": RUNNER_VERSION,
        "status": "passed" if failed == 0 else "failed",
        "metrics": metrics,
        "trajectory": trajectory,
    }
    result["result_fingerprint"] = _hash(result)
    if leakage_failures:
        operational_telemetry.record_secret_leak(
            "simulated_campaign",
            {
                "result_fingerprint": result["result_fingerprint"],
                "failed_visibility_assertions": leakage_failures,
            },
        )
    return result


__all__ = ["RUNNER_VERSION", "run_simulation"]
