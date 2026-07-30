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
  world_expansion: null,
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

const worldExpansionProposal: TurnProposal = {
  ...draftProposal,
  id: "proposal_world",
  proposal_kind: "world_expansion",
  check_consequence: null,
  world_expansion: {
    proposal_kind: "world_expansion",
    module_run_id: "run_1",
    module_run_version: 4,
    module_id: "module_1",
    module_source_hash: "a".repeat(64),
    fingerprint: "b".repeat(64),
    analysis: {
      fingerprint: "b".repeat(64),
      decision: "world_gap",
      reasons: ["模组没有直接答案"],
      scene: {
        key: "town",
        title: "小镇",
        play_pace: "freeform",
        location_entity_id: null,
        started_world_time: "1928-10-03"
      },
      module_id: "module_1",
      module_title: "小镇疑云",
      module_source_hash: "a".repeat(64),
      module_run_id: "run_1",
      module_run_version: 4,
      active_spoiler_tags: ["act-1"],
      unreachable_anchor_count: 0,
      deferred_source_count: 0,
      world_fact_head_hash: "c".repeat(64),
      writes_performed: false
    },
    candidate: {
      expansion_kind: "environment",
      subject: "小镇警务设施",
      proposal: "采用治安官办公室。",
      rationale: "符合年代与聚落规模。",
      confidence: "medium",
      assumptions: ["采用治安官制度"],
      conflicts: ["模组没有说明辖区"],
      alternatives: [
        { title: "邻镇辖区", description: "由邻镇负责", tradeoff: "耗时较长" },
        { title: "临时驻点", description: "巡警临时值守", tradeoff: "资源有限" }
      ]
    }
  },
  player_action: "寻找镇上的警察局",
  proposed_checks: [],
  proposed_events: []
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
    onReject: vi.fn(),
    onConfirmWorldExpansion: vi.fn()
  };

  const view = render(
    <ProposalPanel
      activeMap={null}
      activeProposal={activeProposal}
      campaignTime="1928-10-03 22:15"
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
        activeMap={null}
        activeProposal={draftProposal}
        campaignTime="1928-10-03 22:15"
        loading
        onApprove={onApprove}
        onConfirmWorldExpansion={vi.fn()}
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
        activeMap={null}
        activeProposal={approvedProposal}
        campaignTime="1928-10-03 22:15"
        loading={false}
        onApprove={onApprove}
        onConfirmWorldExpansion={vi.fn()}
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

  it("shows a world-expansion basis, risks and alternatives", () => {
    renderPanel({
      proposals: [worldExpansionProposal],
      activeProposal: worldExpansionProposal
    });

    expect(
      screen.getByRole("button", { name: /草稿 · 世界补全 寻找镇上的警察局/ })
    ).toBeVisible();
    expect(screen.getByText("小镇警务设施")).toBeVisible();
    expect(screen.getByText("模组没有说明辖区")).toBeVisible();
    expect(screen.getByText(/来源：模组运行 v4/)).toBeVisible();
    fireEvent.click(screen.getByText("替代方案（2）"));
    expect(screen.getByText("邻镇辖区")).toBeVisible();
    expect(screen.getByText("临时驻点")).toBeVisible();
  });

  it("materializes an approved world expansion as fact, NPC and existing map token", () => {
    const approvedExpansion = {
      ...worldExpansionProposal,
      status: "approved" as const
    };
    const onConfirmWorldExpansion = vi.fn();
    const savedMap = {
      id: "map_town",
      campaign_id: "camp_1",
      title: "阿卡姆郊外",
      prompt: "1920 年代小镇",
      style: "paper",
      width: 1000,
      height: 700,
      map_kind: "settlement",
      era_year: 1928,
      locale: "新英格兰",
      season: null,
      time_of_day: null,
      weather: null,
      visual_style: null,
      locations: [
        {
          id: "loc_center",
          name: "镇中心",
          x: 0.5,
          y: 0.5,
          kind: "poi",
          visibility: "table" as const
        }
      ],
      routes: [],
      tokens: [],
      status: "draft" as const,
      revision_id: "maprev_1",
      selected_asset: null,
      created_at: "2026-07-30T00:00:00Z"
    };

    render(
      <ProposalPanel
        activeMap={savedMap}
        activeProposal={approvedExpansion}
        campaignTime="1928-10-03 22:15"
        loading={false}
        onApprove={vi.fn()}
        onConfirmWorldExpansion={onConfirmWorldExpansion}
        onInspectContext={vi.fn()}
        onOverrideTextChange={vi.fn()}
        onRefresh={vi.fn()}
        onReject={vi.fn()}
        onSelectProposal={vi.fn()}
        overrideText=""
        proposalContext={null}
        proposals={[approvedExpansion]}
      />
    );

    expect(screen.getByText("玩家是否已经实际接触？")).toBeVisible();
    fireEvent.click(
      screen.getByLabelText("这次接触中出现了需要长期记录的 NPC")
    );
    fireEvent.change(screen.getByLabelText("姓名"), {
      target: { value: "艾萨克·霍尔" }
    });
    fireEvent.change(screen.getByLabelText("职业"), {
      target: { value: "治安官" }
    });
    fireEvent.click(screen.getByLabelText("同步到当前地图"));
    fireEvent.click(
      screen.getByRole("button", { name: "确认实际接触并原子落地" })
    );

    expect(onConfirmWorldExpansion).toHaveBeenCalledTimes(1);
    expect(onConfirmWorldExpansion.mock.calls[0][0]).toMatchObject({
      happened_at: "1928-10-03 22:15",
      facts: [
        {
          fact_type: "canonical_fact",
          subject: "小镇警务设施",
          predicate: "实际存在"
        }
      ],
      npc: {
        name: "艾萨克·霍尔",
        profession: "治安官"
      },
      map_placement: {
        map_id: "map_town",
        location_name: "镇中心"
      }
    });
  });

  it("only offers server-authorized prior NPCs and submits stable investigator evidence", () => {
    const approvedExpansion = {
      ...worldExpansionProposal,
      status: "approved" as const
    };
    const onConfirmWorldExpansion = vi.fn();
    const approvedInvestigator = {
      campaign_id: "camp_1",
      investigator_id: "investigator_lin",
      owner_profile_id: "player_1",
      name: "林若川",
      status: "approved" as const,
      submitted_revision_id: "rev_1",
      approved_revision_id: "rev_1",
      legacy_pc_id: "pc_1",
      review_comment: null,
      submitted_revision: null,
      approved_revision: null,
      campaign_state: null,
      reviews: [],
      diff: []
    };
    const knownNpc = {
      npc_id: "npc_zhou",
      name: "周怀民",
      profession: "报社线人",
      home_location: "雾港旧码头",
      qualifying_investigators: [
        {
          investigator_id: "investigator_lin",
          investigator_name: "林若川",
          interaction_summary: "一起查阅报社旧档案并交换联系方式。",
          happened_at: "1927-06-11 16:00"
        }
      ],
      appearance_gate: {
        decision: "eligible" as const,
        reasons: ["存在当前已批准调查员的跨团接触记录"],
        warnings: [],
        remaining_campaign_budget: 1,
        max_returning_npcs: 1
      },
      availability_profile: null
    };

    render(
      <ProposalPanel
        activeMap={null}
        activeProposal={approvedExpansion}
        campaignTime="1928-10-03 22:15"
        contactInvestigators={[approvedInvestigator]}
        loading={false}
        npcReappearanceCandidates={[knownNpc]}
        onApprove={vi.fn()}
        onConfirmWorldExpansion={onConfirmWorldExpansion}
        onInspectContext={vi.fn()}
        onOverrideTextChange={vi.fn()}
        onRefresh={vi.fn()}
        onReject={vi.fn()}
        onSelectProposal={vi.fn()}
        overrideText=""
        proposalContext={null}
        proposals={[approvedExpansion]}
      />
    );

    fireEvent.click(
      screen.getByLabelText("这次接触中出现了需要长期记录的 NPC")
    );
    fireEvent.click(screen.getByRole("button", { name: "旧识复现" }));
    expect(screen.getByText("林若川记得此人")).toBeVisible();
    expect(
      screen.getByText("一起查阅报社旧档案并交换联系方式。")
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("他们一起做了什么"), {
      target: { value: "重逢后一起讨论了镇上的失踪案。" }
    });
    fireEvent.click(
      screen.getByRole("button", { name: "确认实际接触并原子落地" })
    );

    expect(onConfirmWorldExpansion).toHaveBeenCalledWith(
      expect.objectContaining({
        npc: {
          npc_id: "npc_zhou",
          role: "reappeared"
        },
        participant_investigator_ids: ["investigator_lin"],
        interaction_summary: "重逢后一起讨论了镇上的失踪案。"
      })
    );
  });

  it("shows a context snapshot only when it belongs to the active proposal", () => {
    const { rerender } = renderPanel({ proposalContext: context });

    expect(screen.getByText("上下文快照")).toBeVisible();
    expect(screen.getByText(/约 420 tokens · 纳入 1 项 · 排除 1 项/)).toBeVisible();
    expect(screen.getByText("旧码头仓库外有相同的黑泥。")).toBeVisible();
    expect(screen.getByText(/memory · 另一名玩家的秘密 · visibility/)).toBeInTheDocument();

    rerender(
      <ProposalPanel
        activeMap={null}
        activeProposal={approvedProposal}
        campaignTime="1928-10-03 22:15"
        loading={false}
        onApprove={vi.fn()}
        onConfirmWorldExpansion={vi.fn()}
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
