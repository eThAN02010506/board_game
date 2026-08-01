import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuthIdentity, PlayerActionRecord } from "../../api/types";
import { ActionPanel } from "./ActionPanel";

const kpIdentity: AuthIdentity = {
  member_id: "member_kp",
  session_id: "session_test",
  campaign_id: "campaign_test",
  role: "kp",
  display_name: "守密人",
  pc_id: null
};

const playerIdentity: AuthIdentity = {
  member_id: "member_player",
  session_id: "session_test",
  campaign_id: "campaign_test",
  role: "player",
  display_name: "林若川",
  pc_id: "pc_player"
};

const actions: PlayerActionRecord[] = [
  {
    id: "action_submitted",
    campaign_id: "campaign_test",
    member_id: "member_player",
    pc_id: "pc_player",
    action_text: "检查窗框上的泥土",
    location: "警局档案室",
    map_id: null,
    status: "submitted",
    proposal_id: null,
    display_name: "林若川"
  },
  {
    id: "action_processed",
    campaign_id: "campaign_test",
    member_id: "member_other",
    pc_id: "pc_other",
    action_text: "询问值班警员",
    location: "警局前台",
    map_id: null,
    status: "processed",
    proposal_id: "proposal_approved",
    display_name: "周闻"
  }
];

function renderPanel(identity: AuthIdentity | null, loading = false) {
  const callbacks = {
    onPlayerActionChange: vi.fn(),
    onProposalTextChange: vi.fn(),
    onSelectPlayerAction: vi.fn(),
    onSearchMemory: vi.fn(),
    onSubmitPlayerAction: vi.fn(),
    onAutoKpEnabledChange: vi.fn(),
    onRefreshPlayerActions: vi.fn(),
    onCreateProposal: vi.fn(),
    onGenerateAiProposal: vi.fn()
  };

  render(
    <ActionPanel
      identity={identity}
      loading={loading}
      playerAction=""
      playerActions={actions}
      proposalText=""
      selectedPlayerActionId="action_submitted"
      autoKpEnabled
      {...callbacks}
    />
  );

  return callbacks;
}

describe("ActionPanel", () => {
  it("shows a player submission flow without KP-only controls", () => {
    const { onPlayerActionChange, onSearchMemory, onSubmitPlayerAction } =
      renderPanel(playerIdentity);

    fireEvent.change(screen.getByLabelText("玩家行动"), {
      target: { value: "查看门锁是否有撬动痕迹" }
    });
    fireEvent.click(screen.getByRole("button", { name: "检索记忆" }));
    fireEvent.click(screen.getByRole("button", { name: "提交并自动推进" }));

    expect(onPlayerActionChange).toHaveBeenCalledWith("查看门锁是否有撬动痕迹");
    expect(onSearchMemory).toHaveBeenCalledTimes(1);
    expect(onSubmitPlayerAction).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("玩家行动队列")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "调用本地 AI" })).not.toBeInTheDocument();
  });

  it("lets a player turn off automatic KP advancement", () => {
    const { onAutoKpEnabledChange } = renderPanel(playerIdentity);

    fireEvent.click(screen.getByLabelText("AI KP 自动推进"));

    expect(onAutoKpEnabledChange).toHaveBeenCalledWith(false);
  });

  it("lets a KP select only submitted actions and protects AI generation while loading", () => {
    const {
      onGenerateAiProposal,
      onRefreshPlayerActions,
      onSelectPlayerAction
    } = renderPanel(kpIdentity, true);

    const submittedButton = screen.getByRole("button", {
      name: /林若川 · 已提交 检查窗框上的泥土/
    });
    const processedButton = screen.getByRole("button", {
      name: /周闻 · processed 询问值班警员/
    });
    expect(submittedButton).toHaveClass("selected");
    expect(submittedButton).toBeEnabled();
    expect(processedButton).toBeDisabled();

    fireEvent.click(submittedButton);
    expect(onSelectPlayerAction).toHaveBeenCalledWith(actions[0]);
    fireEvent.click(processedButton);
    expect(onSelectPlayerAction).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(onRefreshPlayerActions).toHaveBeenCalledTimes(1);

    const generateButton = screen.getByRole("button", { name: "调用本地 AI" });
    expect(generateButton).toBeDisabled();
    fireEvent.click(generateButton);
    expect(onGenerateAiProposal).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "提交给 KP" })).not.toBeInTheDocument();
  });

  it("prompts unauthenticated visitors and disables memory search", () => {
    renderPanel(null);

    expect(screen.getByText("先开启 KP 会话或使用加入码进入。")).toBeVisible();
    expect(screen.getByRole("button", { name: "检索记忆" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "提交给 KP" })).not.toBeInTheDocument();
    expect(screen.queryByText("玩家行动队列")).not.toBeInTheDocument();
  });
});
