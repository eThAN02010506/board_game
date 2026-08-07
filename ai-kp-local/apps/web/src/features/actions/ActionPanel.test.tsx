import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActionAdjudication, AuthIdentity, AutoKpJob, PlayerActionRecord } from "../../api/types";
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

function renderPanel(
  identity: AuthIdentity | null,
  loading = false,
  autoKpJobs: AutoKpJob[] = [],
  adjudication: ActionAdjudication | null = null
) {
  const callbacks = {
    onPlayerActionChange: vi.fn(),
    onProposalTextChange: vi.fn(),
    onSelectPlayerAction: vi.fn(),
    onSearchMemory: vi.fn(),
    onSubmitPlayerAction: vi.fn(),
    onAutoConfirmAdjudicationChange: vi.fn(),
    onAutoKpEnabledChange: vi.fn(),
    onRefreshPlayerActions: vi.fn(),
    onRetryAutoKpJob: vi.fn(),
    onCreateProposal: vi.fn(),
    onGenerateAiProposal: vi.fn(),
    onConfirmAdjudication: vi.fn(),
    onReviseAdjudication: vi.fn()
  };

  render(
    <ActionPanel
      identity={identity}
      loading={loading}
      playerAction=""
      playerActions={actions}
      proposalText=""
      selectedPlayerActionId="action_submitted"
      autoConfirmAdjudication
      autoKpEnabled
      autoKpJobs={autoKpJobs}
      adjudication={adjudication}
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
    fireEvent.click(screen.getByRole("button", { name: "提交并后台推进" }));

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

  it("shows the AI mode and lets the player confirm or revise the skill", () => {
    const adjudication: ActionAdjudication = {
      id: "adjudication_test",
      action_id: "action_submitted",
      proposal_id: "proposal_test",
      mode: "skill_check",
      status: "pending",
      version: 1,
      reason: "需要先判断是否意识到跳车风险。",
      prompt: "",
      skill_options: [
        {
          skill_name: "INT",
          skill_key: "int",
          target: 70,
          difficulty: "regular",
          reason: "成功只代表意识到风险。",
          hidden: false
        }
      ],
      selected_skill: "INT",
      source_model: "test-model",
      source_error: null,
      ruling: {
        goal: "理解跳车风险",
        method: "灵感/INT",
        target: "玩家角色",
        maximum_effect: "只意识到风险，不保证安全。"
      }
    };
    const { onConfirmAdjudication, onReviseAdjudication } = renderPanel(
      playerIdentity,
      false,
      [],
      adjudication
    );

    expect(screen.getByText("技能检定")).toBeInTheDocument();
    expect(screen.getByText("AI 初步裁定 · 尚未执行")).toBeInTheDocument();
    expect(screen.getByLabelText("选择本次判定技能")).toHaveValue("INT");
    expect(screen.getByText("只意识到风险，不保证安全。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认此裁定" }));
    fireEvent.click(screen.getByRole("button", { name: "修改行动并重新裁定" }));
    expect(onConfirmAdjudication).toHaveBeenCalledWith("INT");
    expect(onReviseAdjudication).toHaveBeenCalledTimes(1);
  });

  it("shows the player's durable Auto KP progress", () => {
    const { onRetryAutoKpJob } = renderPanel(playerIdentity, false, [
      {
        id: "job_action",
        campaign_id: "campaign_test",
        run_id: null,
        job_type: "player_action",
        resource_id: "action_submitted",
        status: "failed",
        stage: "failed",
        attempt_count: 3,
        max_attempts: 3,
        created_at: "2026-08-01 10:00:00",
        updated_at: "2026-08-01 10:00:01"
      }
    ]);

    expect(screen.getByLabelText("自动 KP 任务")).toHaveTextContent(
      "玩家行动failed · failed尝试 3/3"
    );
    fireEvent.click(screen.getByRole("button", { name: "重新尝试" }));
    expect(onRetryAutoKpJob).toHaveBeenCalledWith("job_action");
  });

  it("accepts dialogue or a described approach when the ruling needs roleplay", () => {
    renderPanel(playerIdentity, false, [], {
      id: "adjudication_rp",
      action_id: "action_submitted",
      proposal_id: "proposal_rp",
      mode: "roleplay_or_clarification",
      status: "pending",
      version: 1,
      reason: "需要说明如何取信于列车长。",
      prompt: "请补充说辞。",
      skill_options: [],
      selected_skill: null,
      source_model: "test-model",
      source_error: null,
      ruling: {}
    });

    expect(screen.getByText(/可在上方直接输入角色台词/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "确认此裁定" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改行动并重新裁定" })).toBeVisible();
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
