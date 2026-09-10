import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ParallelActionPlayerBatch } from "../../api/types";
import { ParallelActionBatchStatus } from "./ParallelActionBatchStatus";

function batch(
  overrides: Partial<ParallelActionPlayerBatch> = {}
): ParallelActionPlayerBatch {
  return {
    id: "batch-own",
    status: "awaiting_checks",
    version: 5,
    participant_count: 4,
    confirmed_count: 4,
    waiting_count: 0,
    self_phase: "awaiting_push_decision",
    own_item: {
      action_id: "action-own",
      adjudication: {
        id: "adjudication-own",
        action_id: "action-own",
        mode: "skill_check",
        status: "confirmed",
        version: 2,
        reason: "需要检定。",
        prompt: "",
        skill_options: [],
        selected_skill: "侦查",
        source_model: "kernel:test",
        updated_at: "2026-08-21T10:00:00Z",
        confirmed_at: "2026-08-21T10:00:01Z",
        ruling: {}
      },
      checks: []
    },
    updated_at: "2026-08-21T10:00:02Z",
    settled_at: null,
    public_message: "请选择接受失败或孤注一掷。",
    ...overrides
  };
}

describe("ParallelActionBatchStatus", () => {
  it("shows aggregate progress and the viewer's phase without participant details", () => {
    render(<ParallelActionBatchStatus batch={batch()} />);

    const status = screen.getByRole("status", { name: "多人并行动作状态" });
    expect(status).toHaveTextContent("等待你决定是否孤注一掷");
    expect(status).toHaveTextContent("参与4 人");
    expect(status).toHaveTextContent("已确认4 人");
    expect(status).toHaveTextContent("待确认0 人");
    expect(status).toHaveTextContent("请选择接受失败或孤注一掷。");
    expect(status).not.toHaveTextContent("action-own");
    expect(status).not.toHaveTextContent("adjudication-own");
  });

  it("offers a single accessible route to the existing check desk", () => {
    const onOpenChecks = vi.fn();
    render(
      <ParallelActionBatchStatus
        batch={batch()}
        onOpenChecks={onOpenChecks}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "前往检定" }));
    expect(onOpenChecks).toHaveBeenCalledOnce();
  });

  it("does not offer the check route while only waiting for other players", () => {
    render(
      <ParallelActionBatchStatus
        batch={batch({ self_phase: "waiting_for_others" })}
        onOpenChecks={vi.fn()}
      />
    );

    expect(screen.queryByRole("button", { name: "前往检定" })).not.toBeInTheDocument();
  });
});
