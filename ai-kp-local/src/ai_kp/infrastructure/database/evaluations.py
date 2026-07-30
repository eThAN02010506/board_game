"""Persistence for immutable simulation cases and replay runs."""

import json
from hashlib import sha256
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.evaluation.simulated_campaign import run_simulation
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class EvaluationRepository(SQLiteRepository):
    def create_simulation_case(
        self, campaign_id: str, name: str, definition: dict[str, Any], member_id: str
    ) -> dict:
        encoded = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        case_id = new_id("simcase")
        self.connection.execute(
            """
            INSERT INTO simulation_cases
              (id, campaign_id, name, definition_json, definition_hash,
               created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (case_id, campaign_id, name.strip(), encoded, sha256(encoded.encode()).hexdigest(), member_id),
        )
        return self.get_simulation_case(case_id)

    def get_simulation_case(self, case_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM simulation_cases WHERE id = ?", (case_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Simulation case not found: {case_id}")
        result = row_to_dict(row)
        result["definition"] = json.loads(result.pop("definition_json"))
        result["runs"] = self.list_simulation_runs(case_id)
        return result

    def list_simulation_cases(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT id FROM simulation_cases WHERE campaign_id = ? ORDER BY created_at DESC",
            (campaign_id,),
        ).fetchall()
        return [self.get_simulation_case(str(row["id"])) for row in rows]

    def run_simulation_case(self, case_id: str) -> dict:
        case = self.get_simulation_case(case_id)
        result = run_simulation(case["definition"])
        run_id = new_id("simrun")
        self.connection.execute(
            """
            INSERT INTO simulation_runs
              (id, case_id, definition_hash, runner_version, status,
               metrics_json, trajectory_json, result_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, case_id, case["definition_hash"], result["runner_version"],
                result["status"], json.dumps(result["metrics"]),
                json.dumps(result["trajectory"], ensure_ascii=False),
                result["result_fingerprint"],
            ),
        )
        return next(item for item in self.list_simulation_runs(case_id) if item["id"] == run_id)

    def list_simulation_runs(self, case_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM simulation_runs WHERE case_id = ? ORDER BY created_at DESC, id DESC",
            (case_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = row_to_dict(row)
            item["metrics"] = json.loads(item.pop("metrics_json"))
            item["trajectory"] = json.loads(item.pop("trajectory_json"))
            result.append(item)
        return result


__all__ = ["EvaluationRepository"]
