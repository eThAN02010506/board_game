import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listManualKernelCandidates } from "../../api/client";
import type { ActionAdjudication, AuthIdentity, AutoKpJob, ParallelActionPlayerBatch, ParallelActionPlayerRegather, PlayerActionRecord, PublicTurn } from "../../api/types";
import { ActionPanel } from "./ActionPanel";

vi.mock("../../api/client", () => ({
  listManualKernelCandidates: vi.fn().mockResolvedValue({ candidates: [] })
}));

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
  adjudication: ActionAdjudication | null = null,
  parallelBatch: ParallelActionPlayerBatch | null = null,
  parallelRegather: ParallelActionPlayerRegather | null = null,
  publicTurns: PublicTurn[] = []
) {
  const callbacks = {
    onPlayerActionChange: vi.fn(),
    onProposalTextChange: vi.fn(),
    onSelectPlayerAction: vi.fn(),
    onSearchMemory: vi.fn(),
    onSubmitPlayerAction: vi.fn(),
    onAutoKpEnabledChange: vi.fn(),
    onRefreshPlayerActions: vi.fn(),
    onRetryAutoKpJob: vi.fn(),
    onCreateProposal: vi.fn(),
    onPrepareManualKernel: vi.fn(),
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
      publicTurns={publicTurns}
      proposalText=""
      selectedPlayerActionId="action_submitted"
      autoKpEnabled
      autoKpJobs={autoKpJobs}
      adjudication={adjudication}
      parallelBatch={parallelBatch}
      parallelRegather={parallelRegather}
      {...callbacks}
    />
  );

  return callbacks;
}

describe("ActionPanel", () => {
  beforeEach(() => {
    vi.mocked(listManualKernelCandidates).mockResolvedValue({
      action_id: "action_submitted",
      action_text: "检查窗框上的泥土",
      candidates: []
    });
  });

  it("blocks player input after the module run reaches a terminal state", () => {
    render(
      <ActionPanel
        adjudication={{
          id: "stale-ruling", action_id: "stale-action", proposal_id: "stale-proposal",
          mode: "roleplay_or_clarification", status: "pending", version: 1,
          reason: "终局前遗留裁定", prompt: "继续描述", skill_options: [],
          selected_skill: null, source_model: "test-model", source_error: null, ruling: {}
        }} autoKpEnabled autoKpJobs={[{
          id: "stale-job", campaign_id: "campaign_test", run_id: "run-complete",
          job_type: "player_action", resource_id: "stale-action", status: "succeeded",
          stage: "player_action", attempt_count: 1, max_attempts: 3,
          created_at: "2026-08-13T10:00:00Z", updated_at: "2026-08-13T10:00:01Z"
        }]}
        identity={playerIdentity} loading={false}
        onAutoKpEnabledChange={vi.fn()}
        onConfirmAdjudication={vi.fn()} onCreateProposal={vi.fn()}
        onGenerateAiProposal={vi.fn()} onPlayerActionChange={vi.fn()}
        onPrepareManualKernel={vi.fn()} onProposalTextChange={vi.fn()}
        onRefreshPlayerActions={vi.fn()} onRetryAutoKpJob={vi.fn()}
        onReviseAdjudication={vi.fn()} onSearchMemory={vi.fn()}
        onSelectPlayerAction={vi.fn()} onSubmitPlayerAction={vi.fn()}
        modulePlayState={{
          accepts_actions: false, status: "completed", module_title: "测试模组",
          completed_at: "2026-08-13T10:00:00Z", ending_id: "safe-exit",
          ending_title: "平安归来", opening_narration: null
        }}
        playBlockedReason="《测试模组》已经结束。" playerAction=""
        playerActions={[]} proposalText="" publicTurns={[{
          id: "turn-ending", player_action_id: "action-ending",
          player_action: "离开宅邸", public_narration: "你在黎明前回到了道路上。",
          created_at: null, decided_at: null
        }]} selectedPlayerActionId=""
      />
    );

    expect(screen.getByRole("heading", { name: "平安归来" })).toBeVisible();
    expect(screen.getByText(/共记录 1 个公开回合/)).toBeVisible();
    expect(screen.getByText("查看关键选择与完整回放")).toBeVisible();
    expect(screen.getByLabelText("玩家行动")).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交并后台推进" })).toBeDisabled();
    expect(screen.queryByText("终局前遗留裁定")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("自动 KP 任务")).not.toBeInTheDocument();
  });

  it("lets a human KP select a contract operator without calling AI", async () => {
    vi.mocked(listManualKernelCandidates).mockResolvedValueOnce({
      action_id: "action_submitted",
      action_text: "检查窗框上的泥土",
      candidates: [{
        candidate_id: "inspect-window",
        title: "检查窗框",
        available: true,
        score: 240,
        skill_choices: [{
          skill_key: "侦查",
          difficulty: "regular",
          reason: "寻找可见痕迹。",
          hidden: false
        }]
      }]
    });
    const { onPrepareManualKernel } = renderPanel(kpIdentity);

    expect(await screen.findByRole("option", { name: "检查窗框" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成 Kernel 裁定" }));

    expect(onPrepareManualKernel).toHaveBeenCalledWith("inspect-window", "侦查");
  });

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

  it("shows only the public narration in the player turn feed", () => {
    render(
      <ActionPanel
        identity={playerIdentity}
        loading={false}
        playerAction=""
        playerActions={[]}
        publicTurns={[{
          id: "proposal_public",
          player_action_id: "action_public",
          player_action: "检查车门上的便签",
          public_narration: "便签上只写着一行潦草的字。",
          created_at: null,
          decided_at: null
        }]}
        proposalText=""
        selectedPlayerActionId=""
        autoKpEnabled
        autoKpJobs={[]}
        adjudication={null}
        onPlayerActionChange={vi.fn()}
        onProposalTextChange={vi.fn()}
        onSelectPlayerAction={vi.fn()}
        onSearchMemory={vi.fn()}
        onSubmitPlayerAction={vi.fn()}
        onAutoKpEnabledChange={vi.fn()}
        onRefreshPlayerActions={vi.fn()}
        onRetryAutoKpJob={vi.fn()}
        onCreateProposal={vi.fn()}
        onPrepareManualKernel={vi.fn()}
        onGenerateAiProposal={vi.fn()}
        onConfirmAdjudication={vi.fn()}
        onReviseAdjudication={vi.fn()}
      />
    );

    expect(screen.getByText("检查车门上的便签")).toBeInTheDocument();
    expect(screen.getByText("便签上只写着一行潦草的字。")).toBeInTheDocument();
  });

  it("lets a player turn off automatic KP advancement", () => {
    const { onAutoKpEnabledChange } = renderPanel(playerIdentity);

    fireEvent.click(screen.getByLabelText("AI KP 自动推进"));

    expect(onAutoKpEnabledChange).toHaveBeenCalledWith(false);
  });

  it("lets an unsubmitted regather participant act but blocks duplicate submission", () => {
    const base: ParallelActionPlayerRegather = {
      id: "regather-action-panel",
      status: "gathering",
      version: 3,
      participant_count: 3,
      submitted_count: 1,
      waiting_count: 2,
      self_phase: "awaiting_submission",
      own_action_id: null,
      updated_at: "2026-08-22T00:00:00Z",
      public_message: "Submit your current action when ready."
    };
    const first = renderPanel(playerIdentity, false, [], null, null, base);
    expect(screen.getByLabelText("玩家行动")).toBeEnabled();
    expect(screen.getByRole("button", { name: "提交并后台推进" })).toBeEnabled();
    first.onSubmitPlayerAction.mockClear();

    // A submitted participant waits durably and cannot create a duplicate.
    renderPanel(playerIdentity, false, [], null, null, {
      ...base,
      submitted_count: 2,
      waiting_count: 1,
      self_phase: "waiting_for_others",
      own_action_id: "replacement-own"
    });
    const textareas = screen.getAllByLabelText("玩家行动");
    expect(textareas[textareas.length - 1]).toBeDisabled();
    const buttons = screen.getAllByRole("button", { name: "提交并后台推进" });
    expect(buttons[buttons.length - 1]).toBeDisabled();
  });

  it("shows the AI mode and lets the player confirm or revise the skill", async () => {
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
      source_model: "kernel:test-model",
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
      true,
      [],
      adjudication
    );

    expect(screen.getByText("技能检定")).toBeInTheDocument();
    expect(screen.getByText("AI 初步裁定 · 尚未执行")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("确定性规则内核");
    expect(screen.getByLabelText("选择本次判定技能")).toHaveValue("INT");
    expect(screen.getByText("只意识到风险，不保证安全。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交并后台推进" })).toBeDisabled();
    const confirm = screen.getByRole("button", { name: "确认此裁定" });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "修改行动并重新裁定" }));
    expect(onConfirmAdjudication).toHaveBeenCalledWith("INT");
    expect(onReviseAdjudication).toHaveBeenCalledTimes(1);
  });

  it("binds confirmation and revision to the current parallel batch version", async () => {
    const adjudication: ActionAdjudication = {
      id: "adjudication_parallel",
      action_id: "action_submitted",
      proposal_id: "proposal_parallel",
      mode: "skill_check",
      status: "pending",
      version: 3,
      reason: "需要确认本人的判定方式。",
      prompt: "",
      skill_options: [{
        skill_name: "侦查",
        skill_key: "coc7.spot_hidden",
        target: 55,
        difficulty: "regular",
        reason: "寻找肉眼可见的痕迹。",
        hidden: false
      }],
      selected_skill: "侦查",
      source_model: "kernel:test",
      source_error: null,
      ruling: {}
    };
    const parallelBatch: ParallelActionPlayerBatch = {
      id: "batch_parallel",
      status: "awaiting_confirmation",
      version: 7,
      participant_count: 3,
      confirmed_count: 1,
      waiting_count: 2,
      self_phase: "awaiting_confirmation",
      own_item: {
        action_id: adjudication.action_id,
        adjudication: {
          ...adjudication,
          updated_at: "2026-08-21T10:00:00Z",
          confirmed_at: null
        },
        checks: []
      },
      updated_at: "2026-08-21T10:00:00Z",
      settled_at: null,
      public_message: "请分别确认自己的裁定。"
    };
    const { onConfirmAdjudication, onReviseAdjudication, onSubmitPlayerAction } =
      renderPanel(playerIdentity, true, [], adjudication, parallelBatch);

    expect(screen.getByRole("status", { name: "多人并行动作状态" })).toHaveTextContent(
      "参与3 人已确认1 人待确认2 人"
    );
    expect(screen.getByLabelText("玩家行动")).toBeEnabled();
    const submit = screen.getByRole("button", { name: "提交并后台推进" });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(onSubmitPlayerAction).not.toHaveBeenCalled();

    const confirm = screen.getByRole("button", { name: "确认此裁定" });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(onConfirmAdjudication).toHaveBeenCalledWith("侦查", 7, adjudication);

    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "修改行动并重新裁定" }));
    expect(onReviseAdjudication).toHaveBeenCalledWith(7, adjudication);
  });

  it("keeps direct adjudication independent from unrelated loading and locks only its own request", async () => {
    const adjudication: ActionAdjudication = {
      id: "adjudication_direct",
      action_id: "action_submitted",
      proposal_id: "proposal_direct",
      mode: "direct_resolution",
      status: "pending",
      version: 1,
      reason: "可直接回应。",
      prompt: "",
      skill_options: [],
      selected_skill: null,
      source_model: "kernel:test",
      source_error: null,
      ruling: {}
    };
    const callbacks = renderPanel(playerIdentity, true, [], adjudication);
    let finishRequest!: () => void;
    callbacks.onConfirmAdjudication.mockImplementationOnce(() => new Promise<void>((resolve) => {
      finishRequest = resolve;
    }));

    const confirm = screen.getByRole("button", { name: "确认此裁定" });
    const revise = screen.getByRole("button", { name: "修改行动并重新裁定" });
    expect(confirm).toBeEnabled();
    expect(revise).toBeEnabled();

    fireEvent.click(confirm);
    expect(confirm).toBeDisabled();
    expect(revise).toBeDisabled();
    expect(callbacks.onConfirmAdjudication).toHaveBeenCalledWith(null);

    await act(async () => finishRequest());
    expect(confirm).toBeEnabled();
    expect(revise).toBeEnabled();
  });

  it("locks new action editing after this player has confirmed", () => {
    const waitingBatch: ParallelActionPlayerBatch = {
      id: "batch_waiting",
      status: "awaiting_confirmation",
      version: 8,
      participant_count: 2,
      confirmed_count: 1,
      waiting_count: 1,
      self_phase: "waiting_for_others",
      own_item: {
        action_id: "action_submitted",
        adjudication: {
          id: "adjudication_waiting",
          action_id: "action_submitted",
          mode: "direct_resolution",
          status: "confirmed",
          version: 2,
          reason: "已确认。",
          prompt: "",
          skill_options: [],
          selected_skill: null,
          source_model: "kernel:test",
          updated_at: "2026-08-21T10:00:00Z",
          confirmed_at: "2026-08-21T10:00:01Z",
          ruling: {}
        },
        checks: []
      },
      updated_at: "2026-08-21T10:00:01Z",
      settled_at: null,
      public_message: "正在等待其他玩家。"
    };

    renderPanel(playerIdentity, false, [], null, waitingBatch);

    expect(screen.getByLabelText("玩家行动")).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交并后台推进" })).toBeDisabled();
    expect(screen.getByText("你的裁定已确认，等待其他玩家")).toBeVisible();
  });

  it("shows the ruleset-neutral tabletop route before the mechanical ruling", () => {
    renderPanel(playerIdentity, false, [], {
      id: "adjudication_conversation",
      action_id: "action_submitted",
      proposal_id: "proposal_conversation",
      mode: "direct_resolution",
      status: "pending",
      version: 1,
      reason: "这句话只需要 NPC 回应。",
      prompt: "",
      skill_options: [],
      selected_skill: null,
      source_model: "kernel:test-model",
      source_error: null,
      tabletop_turn: {
        schema_version: "tabletop-turn.v1",
        route: "roleplay",
        attempt_count: 1,
        audit_count: 0,
        audit_reason: "",
        validation_errors: [],
        frame: {
          kind: "npc_dialogue",
          goal: "询问警告",
          method: "当面提问",
          target_entity_ids: ["witness"],
          dialogue: "为什么？",
          steps: [],
          time_span: "",
          ambiguity: null,
          confidence: "high"
        },
        response: null
      },
      ruling: {}
    });

    expect(screen.getByText("角色扮演回应")).toBeVisible();
    expect(screen.getByText("对 NPC 说话")).toBeVisible();
  });

  it("shows safe per-entity Actor execution for pending and completed turns", () => {
    const actorTrace = {
      schema_version: "entity-actor-trace.v1" as const,
      entity_id: "witness",
      entity_title: "维托里奥",
      execution: "deterministic_fallback" as const,
      generation_attempt_count: 2,
      error_codes: ["actor_output_rejected" as const]
    };
    renderPanel(playerIdentity, false, [], {
      id: "adjudication_actor_trace",
      action_id: "action_submitted",
      proposal_id: "proposal_actor_trace",
      mode: "direct_resolution",
      status: "pending",
      version: 1,
      reason: "NPC 已回应。",
      prompt: "",
      skill_options: [],
      selected_skill: null,
      source_model: "kernel:test-model",
      source_error: null,
      tabletop_turn: {
        schema_version: "tabletop-turn.v1",
        route: "roleplay",
        attempt_count: 1,
        audit_count: 0,
        frame: {
          kind: "npc_dialogue", goal: "询问", method: "当面询问",
          target_entity_ids: ["witness"], dialogue: "为什么？", steps: [], time_span: "",
          ambiguity: null, confidence: "high"
        },
        response: {
          public_narration: "维托里奥拒绝回答。",
          speaker_entity_ids: ["witness"], source: "deterministic",
          attempt_count: 2, actor_traces: [actorTrace]
        }
      },
      ruling: {}
    }, null, null, [{
      id: "public_actor_turn", player_action_id: "action_old",
      player_action: "询问证人", public_narration: "证人低声回答。",
      created_at: null, decided_at: null,
      actor_traces: [{ ...actorTrace, entity_id: "old-witness", entity_title: "旧证人", execution: "model", generation_attempt_count: 1, error_codes: [] }]
    }]);

    expect(screen.getAllByText("角色代理执行状态")).toHaveLength(2);
    expect(screen.getByText("维托里奥")).toBeInTheDocument();
    expect(screen.getByText("旧证人")).toBeInTheDocument();
    expect(screen.getByText(/actor_output_rejected/)).toBeInTheDocument();
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
      "玩家行动自动裁定失败尝试 3/3"
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
