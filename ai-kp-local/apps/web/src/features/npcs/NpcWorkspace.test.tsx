import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getNpcReappearancePolicy,
  getTravelGraph,
  listCampaignNpcs,
  listNpcHiddenAppearances,
  previewTravelRoute,
  resolveNpcHiddenAppearance,
  saveNpcAvailability
} from "../../api/client";
import { NpcWorkspace } from "./NpcWorkspace";

vi.mock("../../api/client", () => ({
  createTravelLocation: vi.fn(),
  createTravelRoute: vi.fn(),
  deleteTravelLocation: vi.fn(),
  deleteTravelRoute: vi.fn(),
  getNpcReappearancePolicy: vi.fn(),
  getTravelGraph: vi.fn(),
  listCampaignNpcs: vi.fn(),
  listNpcHiddenAppearances: vi.fn(),
  previewTravelRoute: vi.fn(),
  resolveNpcHiddenAppearance: vi.fn(),
  saveNpcAvailability: vi.fn(),
  saveNpcReappearancePolicy: vi.fn()
}));

const campaign = {
  id: "camp_1",
  title: "雾港",
  system: "coc7",
  current_time: "1928-10-03"
};

const identity = {
  member_id: "member_kp",
  session_id: "session_1",
  campaign_id: "camp_1",
  role: "kp" as const,
  display_name: "OldOnes",
  pc_id: null
};

const npc = {
  id: "npc_zhou",
  name: "周怀民",
  home_location: "旧码头",
  profession: "报社线人",
  public_notes: "",
  role: "encountered",
  first_seen_time: "1927-01-01",
  last_seen_time: "1927-01-02",
  relationship_score: 10,
  campaign_notes: "",
  availability_profile: {
    lifecycle_state: "active" as const,
    born_year: 1880,
    died_year: null,
    active_from_year: 1910,
    active_until_year: 1940,
    location_tags: ["旧码头"],
    profession_tags: ["线人"],
    kp_notes: ""
  }
};

const policy = {
  campaign_id: "camp_1",
  max_returning_npcs: 1,
  require_location_match: true,
  require_profession_match: true,
  max_travel_minutes: 120,
  updated_at: null
};

const graph = {
  locations: [
    {
      id: "loc_harbour",
      campaign_id: "camp_1",
      name: "旧码头",
      normalized_name: "旧码头",
      aliases: ["码头区"],
      source_kind: "manual" as const,
      source_ref: null,
      kp_notes: ""
    },
    {
      id: "loc_station",
      campaign_id: "camp_1",
      name: "车站",
      normalized_name: "车站",
      aliases: [],
      source_kind: "manual" as const,
      source_ref: null,
      kp_notes: ""
    }
  ],
  routes: []
};

describe("NpcWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listCampaignNpcs).mockResolvedValue([npc]);
    vi.mocked(getNpcReappearancePolicy).mockResolvedValue(policy);
    vi.mocked(getTravelGraph).mockResolvedValue(graph);
    vi.mocked(listNpcHiddenAppearances).mockResolvedValue([]);
    vi.mocked(saveNpcAvailability).mockResolvedValue(npc.availability_profile);
  });

  it("runs a KP-private appearance roll from explicit destinations", async () => {
    vi.mocked(resolveNpcHiddenAppearance).mockResolvedValue({
      id: "hiddenroll_1",
      campaign_id: "camp_1",
      npc_id: "npc_zhou",
      npc_name: "周怀民",
      idempotency_key: "hidden-test",
      trigger_text: "调查员抵达车站",
      appearance_chance: 60,
      appearance_roll: 42,
      appears: true,
      eligible_locations: [
        {
          location_id: "loc_station",
          location_name: "车站",
          requested_name: "车站",
          weight: 2,
          travel_minutes: 90
        }
      ],
      selected_location_id: "loc_station",
      selected_location_name: "车站",
      location_roll: null,
      created_by_member_id: "member_kp",
      created_at: "2026-07-30",
      public_result: { appears: true, location_name: "车站" }
    });
    render(<NpcWorkspace campaign={campaign} identity={identity} />);
    await screen.findByText("周怀民");

    fireEvent.change(
      screen.getByPlaceholderText("调查员抵达中央车站并观察候车厅"),
      { target: { value: "调查员抵达车站" } }
    );
    fireEvent.change(screen.getByLabelText("出现概率（%）"), {
      target: { value: "60" }
    });
    fireEvent.change(screen.getByLabelText(/候选地点/), {
      target: { value: "车站|2" }
    });
    fireEvent.click(screen.getByRole("button", { name: "执行私密暗骰" }));

    expect(await screen.findByText("周怀民 会出现在 车站")).toBeInTheDocument();
    expect(resolveNpcHiddenAppearance).toHaveBeenCalledWith(
      "camp_1",
      expect.objectContaining({
        npc_id: "npc_zhou",
        appearance_chance: 60,
        destinations: [{ location_name: "车站", weight: 2 }]
      })
    );
  });

  it("loads the KP-only NPC profile and saves explicit availability facts", async () => {
    render(<NpcWorkspace campaign={campaign} identity={identity} />);

    expect(await screen.findByText("周怀民")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("职业与行业标签"), {
      target: { value: "线人，记者" }
    });
    fireEvent.click(screen.getByRole("button", { name: "保存 NPC 档案" }));

    await waitFor(() =>
      expect(saveNpcAvailability).toHaveBeenCalledWith(
        "camp_1",
        "npc_zhou",
        expect.objectContaining({ profession_tags: ["线人", "记者"] })
      )
    );
  });

  it("previews an explainable shortest route", async () => {
    vi.mocked(previewTravelRoute).mockResolvedValue({
      status: "reachable",
      total_minutes: 90,
      max_minutes: 120,
      within_limit: true,
      locations: [
        { id: "loc_harbour", name: "旧码头" },
        { id: "loc_station", name: "车站" }
      ],
      legs: []
    });
    render(<NpcWorkspace campaign={campaign} identity={identity} />);
    await screen.findByText("周怀民");

    fireEvent.change(screen.getByPlaceholderText("NPC 出发地点或别名"), {
      target: { value: "旧码头" }
    });
    fireEvent.change(screen.getByPlaceholderText("玩家当前地点或别名"), {
      target: { value: "车站" }
    });
    fireEvent.click(screen.getByRole("button", { name: "计算最短旅行时间" }));

    expect(await screen.findByText("旧码头 → 车站")).toBeInTheDocument();
    expect(screen.getByText("1 小时 30 分钟")).toBeInTheDocument();
  });
});
