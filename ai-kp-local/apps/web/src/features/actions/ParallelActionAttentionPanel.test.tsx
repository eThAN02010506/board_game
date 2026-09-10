import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AuthIdentity,
  ParallelActionAttentionBatch
} from "../../api/types";
import { ParallelActionAttentionPanel } from "./ParallelActionAttentionPanel";

const listParallelActionAttentionBatches = vi.fn();
const resumeParallelActionBatch = vi.fn();
const abandonParallelActionBatch = vi.fn();

vi.mock("../../api/client", () => ({
  listParallelActionAttentionBatches: (...args: unknown[]) =>
    listParallelActionAttentionBatches(...args),
  resumeParallelActionBatch: (...args: unknown[]) =>
    resumeParallelActionBatch(...args),
  abandonParallelActionBatch: (...args: unknown[]) =>
    abandonParallelActionBatch(...args)
}));

const kp: AuthIdentity = {
  campaign_id: "campaign-1",
  display_name: "Keeper",
  member_id: "member-kp",
  pc_id: null,
  role: "kp",
  session_id: "session-1"
};

function attentionBatch(
  overrides: Partial<ParallelActionAttentionBatch> = {}
): ParallelActionAttentionBatch {
  return {
    abandon_allowed: true,
    abandon_block_reason: "",
    attention_reason: "场景版本在结算前发生变化。",
    confirmed_count: 1,
    id: "batch-1",
    participant_count: 2,
    pending_check_count: 0,
    status: "needs_attention",
    updated_at: "2026-08-21T12:00:00Z",
    version: 7,
    ...overrides
  };
}

describe("ParallelActionAttentionPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listParallelActionAttentionBatches.mockResolvedValue([attentionBatch()]);
    resumeParallelActionBatch.mockResolvedValue({ status: "awaiting_confirmation" });
    abandonParallelActionBatch.mockResolvedValue({ status: "needs_attention" });
  });

  it("resumes the exact durable version with a required audited reason", async () => {
    const onChanged = vi.fn();
    render(
      <ParallelActionAttentionPanel
        campaignId="campaign-1"
        identity={kp}
        jobs={[]}
        onChanged={onChanged}
      />
    );

    expect(await screen.findByText("场景版本在结算前发生变化。")).toBeVisible();
    const resume = screen.getByRole("button", { name: "安全恢复" });
    expect(resume).toBeDisabled();
    fireEvent.change(screen.getByLabelText("处理原因"), {
      target: { value: "已核对冻结场景与玩家确认记录。" }
    });
    fireEvent.click(resume);

    await waitFor(() => expect(resumeParallelActionBatch).toHaveBeenCalledWith(
      "batch-1",
      {
        expected_version: 7,
        reason: "已核对冻结场景与玩家确认记录。"
      }
    ));
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(abandonParallelActionBatch).not.toHaveBeenCalled();
  });

  it("allows a server-approved direct-only batch even after everyone confirmed", async () => {
    listParallelActionAttentionBatches.mockResolvedValue([
      attentionBatch({
        abandon_allowed: true,
        confirmed_count: 2,
        participant_count: 2,
        pending_check_count: 0
      })
    ]);
    render(
      <ParallelActionAttentionPanel
        campaignId="campaign-1"
        identity={kp}
        jobs={[]}
        onChanged={vi.fn()}
      />
    );

    await screen.findByText("场景版本在结算前发生变化。");
    fireEvent.change(screen.getByLabelText("处理原因"), {
      target: { value: "玩家同意整批重报。" }
    });
    fireEvent.click(screen.getByRole("button", { name: "放弃整批" }));

    await waitFor(() => expect(abandonParallelActionBatch).toHaveBeenCalledWith(
      "batch-1",
      { expected_version: 7, reason: "玩家同意整批重报。" }
    ));
  });

  it("uses the server's known-result guard and displays its safe reason", async () => {
    const blockReason = (
      "A linked skill check already has a known result; resume and settle "
      + "this batch instead."
    );
    listParallelActionAttentionBatches.mockResolvedValue([
      attentionBatch({
        abandon_allowed: false,
        abandon_block_reason: blockReason,
        confirmed_count: 2,
        pending_check_count: 0
      })
    ]);
    render(
      <ParallelActionAttentionPanel
        campaignId="campaign-1"
        identity={kp}
        jobs={[]}
        onChanged={vi.fn()}
      />
    );

    await screen.findByText("场景版本在结算前发生变化。");
    fireEvent.change(screen.getByLabelText("处理原因"), {
      target: { value: "尝试在看到结算阶段后撤回。" }
    });

    expect(screen.getByRole("button", { name: "放弃整批" })).toBeDisabled();
    expect(screen.getByText(blockReason)).toBeVisible();
    expect(abandonParallelActionBatch).not.toHaveBeenCalled();
  });

  it("shows a version conflict and lets the KP clear it with an explicit refresh", async () => {
    resumeParallelActionBatch.mockRejectedValue(
      new Error("多人行动批次已变化；请刷新后恢复")
    );
    render(
      <ParallelActionAttentionPanel
        campaignId="campaign-1"
        identity={kp}
        jobs={[]}
        onChanged={vi.fn()}
      />
    );

    await screen.findByText("场景版本在结算前发生变化。");
    fireEvent.change(screen.getByLabelText("处理原因"), {
      target: { value: "按冻结版本恢复。" }
    });
    fireEvent.click(screen.getByRole("button", { name: "安全恢复" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "多人行动批次已变化；请刷新后恢复"
    );
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("never queries or renders KP recovery controls for a player", async () => {
    const player = { ...kp, member_id: "member-player", role: "player" as const };
    render(
      <ParallelActionAttentionPanel
        campaignId="campaign-1"
        identity={player}
        jobs={[]}
        onChanged={vi.fn()}
      />
    );

    await waitFor(() => expect(listParallelActionAttentionBatches).not.toHaveBeenCalled());
    expect(screen.queryByLabelText("多人行动恢复工作台")).not.toBeInTheDocument();
  });
});
