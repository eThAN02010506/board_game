import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  analyzeModuleRunIntent,
  generateWorldExpansionProposal,
  getModuleRunDirectorState,
  transitionModuleRunScene,
  updateModuleRunEntityState
} from "../../api/client";
import type {
  DirectorAnalysis,
  ModuleRun,
  ModuleRunDirectorState,
  TurnProposal
} from "../../api/types";
import { SceneDirectorPanel } from "./SceneDirectorPanel";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    analyzeModuleRunIntent: vi.fn(),
    generateWorldExpansionProposal: vi.fn(),
    getModuleRunDirectorState: vi.fn(),
    transitionModuleRunScene: vi.fn(),
    updateModuleRunEntityState: vi.fn()
  };
});

const run: ModuleRun = {
  id: "run/1",
  campaign_id: "camp_1",
  module_id: "mod_1",
  module_title: "雾港疑云",
  module_source_hash: "a".repeat(64),
  status: "active",
  current_scene_key: "warehouse",
  current_scene_title: "旧仓库",
  play_pace: "freeform",
  current_location_entity_id: "location/1",
  scene_started_world_time: "1928-10-03 21:00",
  active_spoiler_tags: ["act-1"],
  state: {},
  version: 3,
  started_by_member_id: "member_kp",
  started_at: "2026-07-30 09:00:00",
  updated_at: "2026-07-30 09:05:00",
  completed_at: null
};

const directorState: ModuleRunDirectorState = {
  run,
  entity_states: [
    {
      run_id: run.id,
      entity_id: "location/1",
      entity_type: "location",
      name: "旧仓库",
      description: "港口边的仓库",
      visibility: "kp",
      spoiler_tag: "act-1",
      status: "available",
      version: 1,
      updated_by_member_id: "member_kp",
      updated_at: "2026-07-30 09:01:00"
    },
    {
      run_id: run.id,
      entity_id: "clue/1",
      entity_type: "clue",
      name: "仓库照片",
      description: "照片背后写着灯塔编号",
      visibility: "kp",
      spoiler_tag: "act-1",
      status: "available",
      version: 1,
      updated_by_member_id: "member_kp",
      updated_at: "2026-07-30 09:02:00"
    }
  ],
  scene_events: [],
  entity_state_events: []
};

const analysis: DirectorAnalysis = {
  run_id: run.id,
  module_id: run.module_id,
  module_title: run.module_title,
  player_intent: "检查仓库照片",
  scene: {
    key: "warehouse",
    title: "旧仓库",
    play_pace: "freeform",
    location_entity_id: "location/1",
    started_world_time: "1928-10-03 21:00"
  },
  decision: "answer_from_canon",
  recommended_action: "narrate_existing_world",
  reasons: ["当前剧透范围内存在与玩家意图相关的模组来源。"],
  sources: [{
    source_type: "chunk",
    source_id: "chunk/1",
    title: "仓库",
    text: "仓库照片指向灯塔航海日志。",
    source_locator: "PDF p.7"
  }],
  deferred_source_count: 0,
  entity_states: directorState.entity_states,
  reachability: {
    module_id: run.module_id,
    entry_entity_ids: ["clue/1"],
    reached_entity_ids: ["clue/1"],
    anchors: [],
    all_anchors_reachable: true,
    has_conflicts: false,
    safe: true,
    conflicts: [],
    evaluated: true
  },
  unreachable_anchor_count: 0,
  writes_performed: false
};

const worldGapAnalysis: DirectorAnalysis = {
  ...analysis,
  player_intent: "寻找镇上的警察局",
  decision: "world_gap",
  recommended_action: "propose_world_expansion",
  reasons: ["当前允许的模组来源没有直接回答该意图，可进入受约束世界补全。"],
  sources: []
};

const worldExpansionProposal: TurnProposal = {
  id: "proposal_world_1",
  campaign_id: run.campaign_id,
  status: "draft",
  proposal_kind: "world_expansion",
  check_consequence: null,
  action_ruling: null,
  world_expansion: {
    proposal_kind: "world_expansion",
    module_run_id: run.id,
    module_run_version: run.version,
    module_id: run.module_id,
    module_source_hash: run.module_source_hash,
    fingerprint: "b".repeat(64),
    analysis: {
      fingerprint: "b".repeat(64),
      decision: "world_gap",
      reasons: worldGapAnalysis.reasons,
      scene: worldGapAnalysis.scene,
      module_id: run.module_id,
      module_title: run.module_title,
      module_source_hash: run.module_source_hash,
      module_run_id: run.id,
      module_run_version: run.version,
      active_spoiler_tags: run.active_spoiler_tags,
      unreachable_anchor_count: 0,
      deferred_source_count: 0,
      world_fact_head_hash: "c".repeat(64),
      writes_performed: false
    },
    candidate: {
      expansion_kind: "environment",
      branch_plan: null,
      subject: "小镇警务设施",
      proposal: "设置一间小型治安官办公室。",
      rationale: "符合年代和聚落规模。",
      confidence: "medium",
      assumptions: ["采用治安官制度"],
      conflicts: [],
      alternatives: [
        { title: "邻镇辖区", description: "由邻镇负责", tradeoff: "路程更远" },
        { title: "临时驻点", description: "只有临时巡警", tradeoff: "档案有限" }
      ]
    }
  },
  player_action: worldGapAnalysis.player_intent,
  public_narration: "你找到了一间不起眼的治安官办公室。",
  kp_notes: "批准前不是事实。",
  proposed_checks: [],
  proposed_events: [],
  proposed_memories: [],
  proposed_npc_updates: [],
  proposed_map_moves: []
};

describe("SceneDirectorPanel", () => {
  beforeEach(() => {
    vi.mocked(getModuleRunDirectorState).mockResolvedValue(directorState);
    vi.mocked(analyzeModuleRunIntent).mockResolvedValue(analysis);
    vi.mocked(generateWorldExpansionProposal).mockResolvedValue(worldExpansionProposal);
    vi.mocked(transitionModuleRunScene).mockResolvedValue({
      run: { ...run, current_scene_title: "灯塔前厅", version: 4 },
      event: {
        id: "scene_event_1",
        run_id: run.id,
        from_scene_key: "warehouse",
        to_scene_key: "lighthouse-hall",
        from_scene_title: "旧仓库",
        to_scene_title: "灯塔前厅",
        from_play_pace: "freeform",
        to_play_pace: "structured",
        from_location_entity_id: "location/1",
        to_location_entity_id: "location/1",
        world_time: "1928-10-03 22:00",
        note: "",
        created_at: "2026-07-30 09:10:00"
      }
    });
    vi.mocked(updateModuleRunEntityState).mockResolvedValue({
      run: { ...run, version: 4 },
      entity_state: {
        ...directorState.entity_states[1],
        status: "discovered",
        version: 2
      },
      event: {
        id: "entity_event_1",
        run_id: run.id,
        entity_id: "clue/1",
        entity_name: "仓库照片",
        entity_type: "clue",
        from_status: "available",
        to_status: "discovered",
        note: "",
        created_at: "2026-07-30 09:10:00"
      }
    });
  });

  it("transitions scenes with the current optimistic version", async () => {
    const user = userEvent.setup();
    const onRunChanged = vi.fn();
    render(<SceneDirectorPanel onRunChanged={onRunChanged} run={run} />);
    await screen.findByText(/导演状态已同步/);

    await user.clear(screen.getByLabelText("场景名称"));
    await user.type(screen.getByLabelText("场景名称"), "灯塔前厅");
    await user.clear(screen.getByLabelText("稳定场景键"));
    await user.type(screen.getByLabelText("稳定场景键"), "lighthouse-hall");
    await user.selectOptions(screen.getByLabelText(/^推进节奏/), "structured");
    await user.clear(screen.getByLabelText("场景开始的世界时间"));
    await user.type(screen.getByLabelText("场景开始的世界时间"), "1928-10-03 22:00");
    await user.click(screen.getByRole("button", { name: "保存并进入场景" }));

    await waitFor(() => expect(transitionModuleRunScene).toHaveBeenCalledWith(
      "run/1",
      expect.objectContaining({
        expected_version: 3,
        scene_key: "lighthouse-hall",
        scene_title: "灯塔前厅",
        play_pace: "structured",
        location_entity_id: "location/1",
        world_time: "1928-10-03 22:00"
      })
    ));
    expect(onRunChanged).toHaveBeenCalledWith(expect.objectContaining({ version: 4 }));
  });

  it("shows source-backed analysis and explicitly reports zero writes", async () => {
    const user = userEvent.setup();
    render(<SceneDirectorPanel onRunChanged={vi.fn()} run={run} />);
    await screen.findByText(/导演状态已同步/);

    await user.type(screen.getByLabelText("玩家准备做什么？"), "检查仓库照片");
    await user.click(screen.getByRole("button", { name: "分析现有答案与世界缺口" }));

    expect(await screen.findByText("模组已有答案")).toBeVisible();
    expect(screen.getByText("确认：零状态写入")).toBeVisible();
    await user.click(screen.getByText("查看当前允许的来源（1）"));
    expect(screen.getByText("仓库照片指向灯塔航海日志。")).toBeVisible();
  });

  it("saves a clue state with the current run version", async () => {
    const user = userEvent.setup();
    render(<SceneDirectorPanel onRunChanged={vi.fn()} run={run} />);
    await screen.findByText(/导演状态已同步/);

    await user.selectOptions(screen.getByLabelText("仓库照片的运行状态"), "discovered");
    const clueRow = screen.getByText("仓库照片").closest("article");
    expect(clueRow).not.toBeNull();
    await user.click(clueRow!.querySelector("button")!);

    await waitFor(() => expect(updateModuleRunEntityState).toHaveBeenCalledWith(
      "run/1",
      "clue/1",
      {
        expected_version: 3,
        status: "discovered"
      }
    ));
  });

  it("creates a reviewable proposal only after a world-gap result", async () => {
    const user = userEvent.setup();
    vi.mocked(analyzeModuleRunIntent).mockResolvedValueOnce(worldGapAnalysis);
    render(<SceneDirectorPanel onRunChanged={vi.fn()} run={run} />);
    await screen.findByText(/导演状态已同步/);

    await user.type(screen.getByLabelText("玩家准备做什么？"), "寻找镇上的警察局");
    await user.click(screen.getByRole("button", { name: "分析现有答案与世界缺口" }));
    await user.click(
      await screen.findByRole("button", { name: "生成可审批的世界补全草稿" })
    );

    await waitFor(() => expect(generateWorldExpansionProposal).toHaveBeenCalledWith(
      "run/1",
      { player_intent: "寻找镇上的警察局" }
    ));
    expect(await screen.findByText("小镇警务设施")).toBeVisible();
    expect(screen.getByText("设置一间小型治安官办公室。")).toBeVisible();
    expect(screen.getByRole("link", { name: "前往游玩页审批" })).toHaveAttribute(
      "href",
      "/play"
    );
  });
});
