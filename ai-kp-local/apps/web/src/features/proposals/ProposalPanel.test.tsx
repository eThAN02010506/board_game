import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ContextAssembly, TurnProposal } from "../../api/types";
import { ProposalPanel } from "./ProposalPanel";

const draftProposal: TurnProposal = {
  id: "proposal_draft",
  campaign_id: "campaign_test",
  status: "draft",
  proposal_kind: "standard",
  check_consequence: null,
  player_action: "检查窗框上的泥土",
  public_narration: "窗框边缘沾着尚未干透的黑泥。",
  kp_notes: "黑泥来自旧码头。",
  proposed_checks: [{ skill_name: "侦查", target: 60 }],
  proposed_events: [{ type: "clue_seen" }],
  proposed_memories: [],
  proposed_npc_updates: [],
  proposed_map_moves: []
};

const approvedProposal: TurnProposal = {
  ...draftProposal,
  id: "proposal_approved",
  status: "approved",
  player_action: "询问值班警员",
  public_narration: "警员回避了你的目光。"
};

const consequenceProposal: TurnProposal = {
  ...draftProposal,
  id: "proposal_consequence",
  proposal_kind: "check_consequence",
  check_consequence: {
    proposal_kind: "check_consequence",
    origin_proposal_id: "proposal_origin",
    player_action_id: "action_test",
    check_ids: ["check_test"],
    result_fingerprint: "a".repeat(64)
  },
  proposed_checks: [],
  player_action: "检查档案柜"
};

const context: ContextAssembly = {
  proposal_id: "proposal_draft",
  visibility_scope: "kp",
  token_estimate: 420,
  final_prompt: [
    { role: "system", content: "只使用已批准的事实。" },
    { role: "user", content: "检查窗框上的泥土" }
  ],
  included_sources: [
    {
      id: "memory_1",
      kind: "memory",
      label: "码头黑泥",
      content: "旧码头仓库外有相同的黑泥。",
      visibility: "kp"
    }
  ],
  excluded_sources: [
    {
      id: "memory_2",
      kind: "memory",
      label: "另一名玩家的秘密",
      excluded_reason: "visibility"
    }
  ]
};

function renderPanel({
  proposals = [draftProposal, approvedProposal],
  activeProposal = draftProposal,
  proposalContext = null,
  loading = false
}: {
  proposals?: TurnProposal[];
  activeProposal?: TurnProposal | null;
  proposalContext?: ContextAssembly | null;
  loading?: boolean;
} = {}) {
  const callbacks = {
    onOverrideTextChange: vi.fn(),
    onSelectProposal: vi.fn(),
    onRefresh: vi.fn(),
    onInspectContext: vi.fn(),
    onApprove: vi.fn(),
    onReject: vi.fn()
  };

  const view = render(
    <ProposalPanel
      activeProposal={activeProposal}
      loading={loading}
      overrideText=""
      proposalContext={proposalContext}
      proposals={proposals}
      {...callbacks}
    />
  );

  return { ...view, ...callbacks };
}

describe("ProposalPanel", () => {
  it("selects proposal records and renders the active draft effects", () => {
    const { onSelectProposal } = renderPanel();

    expect(
      screen.getByRole("button", { name: /草稿 检查窗框上的泥土/ })
    ).toHaveClass("selected");
    expect(
      screen.getByRole("button", { name: /已批准 询问值班警员/ })
    ).not.toHaveClass("selected");
    expect(screen.getByText("窗框边缘沾着尚未干透的黑泥。")).toBeVisible();
    expect(screen.getByText(/"skill_name": "侦查"/)).toBeVisible();
    expect(screen.getByText("待检定")).toHaveTextContent("1");

    fireEvent.click(
      screen.getByRole("button", { name: /已批准 询问值班警员/ })
    );
    expect(onSelectProposal).toHaveBeenCalledWith("proposal_approved");
  });

  it("enables approval actions only for an idle draft", () => {
    const { onApprove, onReject, rerender } = renderPanel();
    const approveButton = screen.getByRole("button", { name: "批准落库" });
    const rejectButton = screen.getByRole("button", { name: "拒绝" });

    expect(approveButton).toBeEnabled();
    expect(rejectButton).toBeEnabled();
    fireEvent.click(approveButton);
    fireEvent.click(rejectButton);
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledTimes(1);

    rerender(
      <ProposalPanel
        activeProposal={draftProposal}
        loading
        onApprove={onApprove}
        onInspectContext={vi.fn()}
        onOverrideTextChange={vi.fn()}
        onRefresh={vi.fn()}
        onReject={onReject}
        onSelectProposal={vi.fn()}
        overrideText=""
        proposalContext={null}
        proposals={[draftProposal]}
      />
    );
    expect(screen.getByRole("button", { name: "批准落库" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "批准落库" }));
    expect(onApprove).toHaveBeenCalledTimes(1);

    rerender(
      <ProposalPanel
        activeProposal={approvedProposal}
        loading={false}
        onApprove={onApprove}
        onInspectContext={vi.fn()}
        onOverrideTextChange={vi.fn()}
        onRefresh={vi.fn()}
        onReject={onReject}
        onSelectProposal={vi.fn()}
        overrideText=""
        proposalContext={null}
        proposals={[approvedProposal]}
      />
    );
    expect(screen.getByRole("button", { name: "批准落库" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
  });

  it("forwards text and toolbar actions to the owning handlers", () => {
    const {
      onInspectContext,
      onOverrideTextChange,
      onRefresh
    } = renderPanel();

    fireEvent.change(screen.getByLabelText("覆写公开描述"), {
      target: { value: "改为只描述玩家能看见的泥土。" }
    });
    fireEvent.click(screen.getByRole("button", { name: "刷新草稿" }));
    fireEvent.click(screen.getByRole("button", { name: "检查 AI 上下文" }));

    expect(onOverrideTextChange).toHaveBeenCalledWith(
      "改为只描述玩家能看见的泥土。"
    );
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(onInspectContext).toHaveBeenCalledTimes(1);
  });

  it("labels a second-stage check consequence distinctly", () => {
    renderPanel({
      proposals: [consequenceProposal],
      activeProposal: consequenceProposal
    });

    expect(
      screen.getByRole("button", { name: /草稿 · 检定后果 检查档案柜/ })
    ).toBeVisible();
    expect(screen.getAllByText("草稿 · 检定后果")).toHaveLength(2);
  });

  it("shows a context snapshot only when it belongs to the active proposal", () => {
    const { rerender } = renderPanel({ proposalContext: context });

    expect(screen.getByText("上下文快照")).toBeVisible();
    expect(screen.getByText(/约 420 tokens · 纳入 1 项 · 排除 1 项/)).toBeVisible();
    expect(screen.getByText("旧码头仓库外有相同的黑泥。")).toBeVisible();
    expect(screen.getByText(/memory · 另一名玩家的秘密 · visibility/)).toBeInTheDocument();

    rerender(
      <ProposalPanel
        activeProposal={approvedProposal}
        loading={false}
        onApprove={vi.fn()}
        onInspectContext={vi.fn()}
        onOverrideTextChange={vi.fn()}
        onRefresh={vi.fn()}
        onReject={vi.fn()}
        onSelectProposal={vi.fn()}
        overrideText=""
        proposalContext={context}
        proposals={[draftProposal, approvedProposal]}
      />
    );

    expect(screen.queryByText("上下文快照")).not.toBeInTheDocument();
  });
});
