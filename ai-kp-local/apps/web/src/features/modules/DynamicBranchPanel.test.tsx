import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DynamicBranchRun } from "../../api/types";
import { DynamicBranchPanel } from "./DynamicBranchPanel";

const listDynamicBranches = vi.fn();
const resolveDynamicBranchBeat = vi.fn();
const resumeDynamicBranch = vi.fn();
const abandonDynamicBranch = vi.fn();

vi.mock("../../api/client", () => ({
  listDynamicBranches: (...args: unknown[]) => listDynamicBranches(...args),
  resolveDynamicBranchBeat: (...args: unknown[]) =>
    resolveDynamicBranchBeat(...args),
  resumeDynamicBranch: (...args: unknown[]) => resumeDynamicBranch(...args),
  abandonDynamicBranch: (...args: unknown[]) => abandonDynamicBranch(...args)
}));

const branch: DynamicBranchRun = {
  id: "branch_1",
  proposal_id: "proposal_1",
  campaign_id: "campaign_1",
  module_run_id: "run_1",
  status: "active",
  current_beat_index: 0,
  version: 1,
  activated_at: "2026-07-30",
  completed_at: null,
  created_at: "2026-07-30",
  updated_at: "2026-07-30",
  plan: {
    goal: "确认调查员是否能查阅旧档案",
    entry_conditions: [],
    completion_conditions: [],
    anchor_guards: [],
    beats: [
      {
        beat_id: "verify",
        title: "核实身份",
        character_intent: "治安官避免泄露档案",
        action: "检查介绍信",
        preconditions: [],
        expected_effects: [
          {
            effect_type: "fact_candidate",
            reference: null,
            description: "可能允许查阅档案",
            requires_contact: true
          }
        ],
        failure_policy: "pause_for_kp"
      }
    ]
  },
  events: [
    {
      id: "event_1",
      event_seq: 1,
      event_type: "activated",
      beat_id: "verify",
      outcome: null,
      note: "",
      payload: {},
      aggregate_version: 1,
      created_at: "2026-07-30"
    }
  ]
};

describe("DynamicBranchPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listDynamicBranches.mockResolvedValue([branch]);
    resolveDynamicBranchBeat.mockResolvedValue({
      branch: { ...branch, status: "completed", current_beat_index: 1, version: 2 },
      event: {
        ...branch.events[0],
        id: "event_2",
        event_seq: 2,
        event_type: "completed",
        outcome: "succeeded",
        aggregate_version: 2
      },
      idempotent_replay: false
    });
  });

  it("records an observed beat without presenting candidate effects as facts", async () => {
    render(
      <DynamicBranchPanel campaignId="campaign_1" moduleRunId="run_1" />
    );

    expect(await screen.findByText("核实身份")).toBeInTheDocument();
    expect(screen.getByText("候选效果（不会自动落库）")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("只记录桌面上真实发生的结果"), {
      target: { value: "治安官确认介绍信有效" }
    });
    fireEvent.click(screen.getByRole("button", { name: "成功" }));

    await waitFor(() => expect(resolveDynamicBranchBeat).toHaveBeenCalledOnce());
    expect(resolveDynamicBranchBeat).toHaveBeenCalledWith(
      "branch_1",
      expect.objectContaining({
        expected_version: 1,
        outcome: "succeeded",
        observed_effects: ["治安官确认介绍信有效"]
      })
    );
    expect(
      await screen.findByText(
        "支线已完成；候选效果仍需通过事实落地接口确认。"
      )
    ).toBeInTheDocument();
  });
});
