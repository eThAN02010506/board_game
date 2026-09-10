"""Persistence for durable, stepwise task-method execution."""

from __future__ import annotations

from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class KernelPlanRepository(SQLiteRepository):
    def create_kernel_plan(
        self,
        *,
        run_id: str,
        root_action_id: str,
        contract_hash: str,
        method_id: str,
        task_key: str,
        steps: list[tuple[str, str]],
    ) -> dict[str, Any]:
        existing = self.get_kernel_plan_for_action(root_action_id)
        if existing is not None:
            return existing
        if not 1 <= len(steps) <= 8:
            raise ValueError("Kernel plan requires one to eight primitive steps")
        plan_id = new_id("kernelplan")
        self.connection.execute(
            """
            INSERT INTO kernel_plan_instances
              (id, run_id, root_action_id, contract_hash, method_id, task_key, step_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (plan_id, run_id, root_action_id, contract_hash, method_id, task_key, len(steps)),
        )
        for index, (step_id, operator_id) in enumerate(steps):
            self.connection.execute(
                """
                INSERT INTO kernel_plan_steps
                  (plan_id, step_index, step_id, operator_id, action_id, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    index,
                    step_id,
                    operator_id,
                    root_action_id if index == 0 else None,
                    "awaiting_resolution" if index == 0 else "pending",
                ),
            )
        return self.get_kernel_plan(plan_id)

    def get_kernel_plan(self, plan_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM kernel_plan_instances WHERE id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Kernel plan not found: {plan_id}")
        result = dict(row)
        result["steps"] = [
            dict(item)
            for item in self.connection.execute(
                "SELECT * FROM kernel_plan_steps WHERE plan_id = ? ORDER BY step_index",
                (plan_id,),
            ).fetchall()
        ]
        return result

    def get_kernel_plan_for_action(self, action_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT plan_id FROM kernel_plan_steps WHERE action_id = ?
            """,
            (action_id,),
        ).fetchone()
        return self.get_kernel_plan(str(row["plan_id"])) if row is not None else None

    def record_kernel_plan_preview(
        self, plan_id: str, step_index: int, *, action_id: str, preview_hash: str
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE kernel_plan_steps
            SET action_id = ?, preview_hash = ?, status = 'awaiting_resolution',
                updated_at = CURRENT_TIMESTAMP
            WHERE plan_id = ? AND step_index = ? AND status IN ('pending', 'awaiting_resolution')
              AND (action_id IS NULL OR action_id = ?)
            """,
            (action_id, preview_hash, plan_id, step_index, action_id),
        )
        if updated.rowcount != 1:
            raise ValueError("Kernel plan step is no longer awaiting resolution")
        return self.get_kernel_plan(plan_id)

    def create_kernel_plan_step_action(
        self, plan_id: str, step_index: int, *, title: str
    ) -> dict[str, Any]:
        plan = self.get_kernel_plan(plan_id)
        if plan["status"] != "active" or int(plan["current_step_index"]) != step_index:
            raise ValueError("Kernel plan is not ready for this step")
        step = plan["steps"][step_index]
        if step["action_id"]:
            return self.get_player_action(str(step["action_id"]))  # type: ignore[attr-defined]
        root = self.get_player_action(str(plan["root_action_id"]))  # type: ignore[attr-defined]
        action_id = new_id("action")
        client_action_id = f"kernel-plan:{plan_id}:{step_index}"
        self.connection.execute(
            """
            INSERT INTO player_actions
              (id, session_id, campaign_id, member_id, pc_id, action_text, location,
               map_id, client_action_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                root["session_id"],
                root["campaign_id"],
                root["member_id"],
                root["pc_id"],
                f"[计划 {step_index + 1}/{plan['step_count']}] {title}",
                root.get("location"),
                root.get("map_id"),
                client_action_id,
            ),
        )
        self.connection.execute(
            """
            UPDATE kernel_plan_steps SET action_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE plan_id = ? AND step_index = ? AND action_id IS NULL
            """,
            (action_id, plan_id, step_index),
        )
        return self.get_player_action(action_id)  # type: ignore[attr-defined]

    def finish_kernel_plan_step(
        self, action_id: str, *, outcome: str
    ) -> dict[str, Any]:
        plan = self.get_kernel_plan_for_action(action_id)
        if plan is None:
            raise KeyError("Action is not part of a kernel plan")
        index = int(plan["current_step_index"])
        step = plan["steps"][index]
        if step["action_id"] != action_id or step["status"] != "awaiting_resolution":
            raise ValueError("Kernel plan step is no longer current")
        succeeded = outcome not in {"failure", "fumble", "pushed_failure"}
        step_status = "committed" if succeeded else "failed"
        self.connection.execute(
            """
            UPDATE kernel_plan_steps SET status = ?, outcome = ?, updated_at = CURRENT_TIMESTAMP
            WHERE plan_id = ? AND step_index = ? AND status = 'awaiting_resolution'
            """,
            (step_status, outcome, plan["id"], index),
        )
        if not succeeded:
            self.connection.execute(
                """
                UPDATE kernel_plan_instances SET status = 'failed', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'active'
                """,
                (plan["id"],),
            )
        elif index + 1 >= int(plan["step_count"]):
            self.connection.execute(
                """
                UPDATE kernel_plan_instances SET status = 'completed', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'active'
                """,
                (plan["id"],),
            )
        else:
            self.connection.execute(
                """
                UPDATE kernel_plan_instances
                SET current_step_index = current_step_index + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'active' AND current_step_index = ?
                """,
                (plan["id"], index),
            )
        return self.get_kernel_plan(str(plan["id"]))

    def fail_kernel_plan(self, plan_id: str, *, reason: str) -> dict[str, Any]:
        plan = self.get_kernel_plan(plan_id)
        if plan["status"] != "active":
            return plan
        index = int(plan["current_step_index"])
        self.connection.execute(
            """
            UPDATE kernel_plan_steps SET status = 'failed', outcome = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE plan_id = ? AND step_index = ? AND status IN ('pending', 'awaiting_resolution')
            """,
            (f"blocked:{reason}"[:1000], plan_id, index),
        )
        self.connection.execute(
            """
            UPDATE kernel_plan_instances SET status = 'failed', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'active'
            """,
            (plan_id,),
        )
        return self.get_kernel_plan(plan_id)


__all__ = ["KernelPlanRepository"]
