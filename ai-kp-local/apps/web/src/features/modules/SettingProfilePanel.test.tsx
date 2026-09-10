import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  analyzeSettingProfileSettlement,
  createModuleSettingProfile,
  getRunSettingSelection,
  listModuleSettingProfiles,
  listSettingCatalogs,
  setRunSettingSelection,
  updateModuleSettingProfile
} from "../../api/client";
import type {
  ModuleRun,
  ModuleSettingAnalysis,
  ModuleSettingProfile,
  SettingCatalog
} from "../../api/types";
import { SettingProfilePanel } from "./SettingProfilePanel";

vi.mock("../../api/client", () => ({
  analyzeSettingProfileSettlement: vi.fn(),
  createModuleSettingProfile: vi.fn(),
  getRunSettingSelection: vi.fn(),
  listModuleSettingProfiles: vi.fn(),
  listSettingCatalogs: vi.fn(),
  setRunSettingSelection: vi.fn(),
  updateModuleSettingProfile: vi.fn()
}));

const run: ModuleRun = {
  id: "run-1",
  campaign_id: "campaign-1",
  module_id: "module-1",
  module_title: "多区域模组",
  module_source_hash: "a".repeat(64),
  status: "active",
  current_scene_key: "opening",
  current_scene_title: "开场",
  play_pace: "freeform",
  current_location_entity_id: "location-police",
  scene_started_world_time: null,
  active_spoiler_tags: [],
  state: {},
  version: 4,
  started_by_member_id: "kp-1",
  started_at: "2026-09-08T00:00:00Z",
  updated_at: "2026-09-08T00:00:00Z",
  completed_at: null
};

const catalog: SettingCatalog = {
  setting_pack_id: "us.1920s",
  schema_version: "1",
  pack_version: "1.0.0",
  title: "1920 年代美国",
  locale: "United States",
  content_scope: "通用历史模板",
  provenance: ["historical sources"],
  settlement_kinds: ["city", "town", "village", "rural"],
  region_patterns: [
    {
      pattern_id: "county_town_network",
      title: "县城网络",
      applicability_tags: [],
      nodes: [
        { node_role: "hub", settlement_kind: "town", minimum_count: 1, maximum_count: 1 },
        { node_role: "nearby", settlement_kind: "village", minimum_count: 1, maximum_count: 4 }
      ],
      routes: []
    },
    {
      pattern_id: "port_hinterland",
      title: "港口腹地",
      applicability_tags: [],
      nodes: [
        { node_role: "hub", settlement_kind: "city", minimum_count: 1, maximum_count: 1 },
        { node_role: "rural", settlement_kind: "rural", minimum_count: 1, maximum_count: 5 }
      ],
      routes: []
    }
  ]
};

const profile: ModuleSettingProfile = {
  id: "profile-1",
  module_id: run.module_id,
  title: "新英格兰",
  setting_pack_id: catalog.setting_pack_id,
  status: "active",
  current_version: 1,
  version: 1,
  setting_pack_version: catalog.pack_version,
  document: {
    schema_version: "1",
    regions: [{
      region_id: "essex",
      title: "埃塞克斯县",
      pattern_id: "county_town_network",
      role_counts: {}
    }],
    settlements: [{
      settlement_id: "essex.hub_1",
      title: "阿卡姆",
      region_id: "essex",
      node_id: "essex.hub_1",
      settlement_kind: "town",
      include_typical: true,
      condition_tags: [],
      location_bindings: []
    }]
  },
  content_hash: "b".repeat(64),
  created_at: "2026-09-08T00:00:00Z",
  updated_at: "2026-09-08T00:00:00Z",
  version_created_at: "2026-09-08T00:00:00Z"
};

const analysis: ModuleSettingAnalysis = {
  module_id: run.module_id,
  profile_id: profile.id,
  profile_version: 1,
  profile_content_hash: profile.content_hash,
  setting_pack: {
    setting_pack_id: catalog.setting_pack_id,
    schema_version: "1",
    pack_version: catalog.pack_version,
    title: catalog.title,
    content_scope: catalog.content_scope,
    provenance: catalog.provenance
  },
  configured_settlement: profile.document.settlements[0],
  settlement_template: {
    setting_pack_id: catalog.setting_pack_id,
    settlement_id: "essex.hub_1",
    settlement_kind: "town",
    scene_slots: [{
      slot_id: "law_enforcement",
      function: "维护公共治安",
      frequency: "core",
      building_candidates: ["治安官办公室"],
      entity_archetype_candidates: [{
        archetype_id: "public_safety_officer",
        entity_kind: "npc",
        label_variants: ["巡警"],
        applicable_scene_slots: ["law_enforcement"],
        capability_tags: ["public_safety"],
        profession_ids: ["law_officer"],
        state_dimensions: ["on_duty"],
        relation_slots: []
      }],
      selected: true
    }]
  },
  source_coverage: {
    bindings: [],
    covered_slot_ids: [],
    missing_core_slot_ids: ["law_enforcement"],
    suggested_typical_slot_ids: []
  },
  unassigned_location_entity_ids: ["location-police"],
  source_entity_bindings: [],
  unassigned_entity_ids: ["npc-sheriff"],
  write_policy: "read_only_kp_review_required"
};

describe("SettingProfilePanel", () => {
  beforeEach(() => {
    vi.mocked(listSettingCatalogs).mockResolvedValue([catalog]);
    vi.mocked(listModuleSettingProfiles).mockResolvedValue([]);
    vi.mocked(getRunSettingSelection).mockResolvedValue(null);
    vi.mocked(createModuleSettingProfile).mockResolvedValue(profile);
    vi.mocked(analyzeSettingProfileSettlement).mockResolvedValue(analysis);
    vi.mocked(updateModuleSettingProfile).mockResolvedValue({
      ...profile,
      current_version: 2,
      version: 2
    });
    vi.mocked(setRunSettingSelection).mockResolvedValue({
      run: { ...run, version: 5 },
      selection: {
        run_id: run.id,
        profile_id: profile.id,
        profile_version: 1,
        settlement_id: "essex.hub_1",
        version: 1,
        profile
      }
    });
  });

  it("creates a profile from multiple independently selected region patterns", async () => {
    const user = userEvent.setup();
    render(<SettingProfilePanel entities={[]} onRunChanged={vi.fn()} run={run} />);

    await screen.findByText("建立多区域骨架");
    await user.click(screen.getByRole("button", { name: "增加区域" }));
    const patterns = screen.getAllByText("拓扑模式").map((label) => label.closest("label")!.querySelector("select")!);
    await user.selectOptions(patterns[1], "port_hinterland");
    await user.click(screen.getByRole("button", { name: "创建完整骨架" }));

    await waitFor(() => expect(createModuleSettingProfile).toHaveBeenCalledWith(
      run.module_id,
      expect.objectContaining({
        setting_pack_id: "us.1920s",
        regions: [
          expect.objectContaining({ pattern_id: "county_town_network" }),
          expect.objectContaining({ pattern_id: "port_hinterland" })
        ]
      })
    ));
  });

  it("assigns a source location and pins the current profile for Full AI", async () => {
    const user = userEvent.setup();
    const onRunChanged = vi.fn();
    vi.mocked(listModuleSettingProfiles).mockResolvedValue([profile]);
    render(
      <SettingProfilePanel
        entities={[{
          entity_id: "location-police",
          entity_type: "location",
          name: "阿卡姆警察局"
        }]}
        onRunChanged={onRunChanged}
        run={run}
      />
    );

    await screen.findByText("核心缺项候选：law_enforcement");
    await user.selectOptions(screen.getByLabelText("未分配模组地点"), "location-police");
    await user.click(screen.getByRole("button", { name: "分配到当前聚落" }));
    await waitFor(() => expect(updateModuleSettingProfile).toHaveBeenCalledWith(
      profile.id,
      expect.objectContaining({
        expected_version: 1,
        document: expect.objectContaining({
          settlements: [expect.objectContaining({
            location_bindings: [{ module_entity_id: "location-police", slot_id: null }]
          })]
        })
      })
    ));

    await user.click(screen.getByRole("button", { name: "固定到当前运行" }));
    await waitFor(() => expect(setRunSettingSelection).toHaveBeenCalledWith(
      run.id,
      expect.objectContaining({
        expected_run_version: run.version,
        profile_id: profile.id,
        settlement_id: "essex.hub_1"
      })
    ));
    expect(onRunChanged).toHaveBeenCalledWith(expect.objectContaining({ version: 5 }));
  });

  it("binds an existing module entity to a compatible archetype and function", async () => {
    const user = userEvent.setup();
    vi.mocked(listModuleSettingProfiles).mockResolvedValue([profile]);
    render(
      <SettingProfilePanel
        entities={[{
          entity_id: "npc-sheriff",
          entity_type: "npc",
          name: "治安官阿米蒂奇"
        }]}
        onRunChanged={vi.fn()}
        run={run}
      />
    );

    await screen.findByText("核心缺项候选：law_enforcement");
    await user.selectOptions(screen.getByLabelText("未绑定来源实体"), "npc-sheriff");
    await user.selectOptions(screen.getByLabelText("来源实体功能槽"), "law_enforcement");
    await user.selectOptions(
      screen.getByLabelText("来源实体原型"),
      "public_safety_officer"
    );
    await user.click(screen.getByRole("button", { name: "保存来源实体绑定" }));

    await waitFor(() => expect(updateModuleSettingProfile).toHaveBeenCalledWith(
      profile.id,
      expect.objectContaining({
        document: expect.objectContaining({
          entity_bindings: [{
            module_entity_id: "npc-sheriff",
            archetype_id: "public_safety_officer",
            settlement_id: "essex.hub_1",
            slot_id: "law_enforcement"
          }]
        })
      })
    ));
  });
});
