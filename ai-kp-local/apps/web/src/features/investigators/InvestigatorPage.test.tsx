import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  credentialBridge,
  requestFile,
  requestJson
} from "../../api/client";
import { InvestigatorPage } from "./InvestigatorPage";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    requestFile: vi.fn(),
    requestJson: vi.fn()
  };
});

const profile = {
  id: "profile_test",
  display_name: "测试玩家",
  created_at: "2026-07-30 10:00:00",
  updated_at: "2026-07-30 10:00:00"
};

function mockLoadedLibrary() {
  vi.mocked(requestJson).mockImplementation(async (url) => {
    if (url === "/investigator-skills/catalog") return [];
    if (url === "/player-profile") return profile;
    if (url === "/investigators") return [];
    return [];
  });
}

describe("InvestigatorPage identity and editor isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    credentialBridge.session("");
    credentialBridge.admin("");
    credentialBridge.player("");
    vi.mocked(requestFile).mockReset();
  });

  it("keeps the stable player token after a transient server failure", async () => {
    localStorage.setItem("ai-kp-player-profile-token", "player_stable");
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/investigator-skills/catalog") return [];
      if (url === "/player-profile") {
        throw new ApiError("临时故障", 503);
      }
      if (url === "/investigators") return [];
      return [];
    });

    render(<InvestigatorPage campaign={null} identity={null} />);

    expect(await screen.findByText("临时故障")).toBeVisible();
    expect(localStorage.getItem("ai-kp-player-profile-token")).toBe("player_stable");
    expect(credentialBridge.snapshot().playerToken).toBe("player_stable");
  });

  it("evicts the stable player token only after an explicit authentication failure", async () => {
    localStorage.setItem("ai-kp-player-profile-token", "player_expired");
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/investigator-skills/catalog") return [];
      if (url === "/player-profile") {
        throw new ApiError("玩家身份已失效", 401);
      }
      if (url === "/investigators") return [];
      return [];
    });

    render(<InvestigatorPage campaign={null} identity={null} />);

    expect(await screen.findByText("玩家身份已失效")).toBeVisible();
    expect(localStorage.getItem("ai-kp-player-profile-token")).toBeNull();
    expect(credentialBridge.snapshot().playerToken).toBe("");
  });

  it("locks the complete manual editor while applying an asynchronous recommendation", async () => {
    const user = userEvent.setup();
    let resolveRecommendation: ((value: unknown) => void) | undefined;
    const recommendation = new Promise((resolve) => {
      resolveRecommendation = resolve;
    });
    localStorage.setItem("ai-kp-player-profile-token", "player_stable");
    mockLoadedLibrary();
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/investigator-skills/catalog") return [];
      if (url === "/player-profile") return profile;
      if (url === "/investigators") return [];
      if (url === "/investigator-skills/recommend") return recommendation;
      return [];
    });

    render(<InvestigatorPage campaign={null} identity={null} />);

    await screen.findByRole("heading", { name: "调查员编辑器" });
    await user.type(screen.getByLabelText("职业"), "记者");
    await user.clear(screen.getByLabelText("时代"));
    await user.type(screen.getByLabelText("时代"), "1920s");
    await user.type(screen.getByLabelText("年龄"), "28");
    for (const key of ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU", "LUCK"]) {
      await user.type(screen.getByLabelText(key), "60");
    }

    await user.click(screen.getByRole("button", { name: "生成并应用推荐加点" }));

    await waitFor(() => expect(screen.getByLabelText("职业")).toBeDisabled());
    expect(screen.getByLabelText("STR")).toBeDisabled();

    resolveRecommendation?.({
      profile_id: "journalist",
      profile_name: "记者",
      occupation_budget: 240,
      occupation_spent: 0,
      interest_budget: 120,
      interest_spent: 0,
      allocations: [],
      rationale: []
    });
    await waitFor(() => expect(screen.getByLabelText("职业")).toBeEnabled());
  });

  it("lets the owner inspect continuity and accept a sourced permanent change", async () => {
    const user = userEvent.setup();
    localStorage.setItem("ai-kp-player-profile-token", "player_stable");
    const investigator = {
      id: "inv_1",
      owner_profile_id: profile.id,
      name: "林若川",
      ruleset_id: "coc7-keeper-cn-2002c",
      current_revision_id: "rev_1",
      current_revision: {
        id: "rev_1",
        investigator_id: "inv_1",
        revision_no: 1,
        source_type: "manual",
        origin_type: "player_edit",
        canonical_sheet: {
          identity: { name: "林若川", occupation: "记者" },
          derived: { max_hp: 10, initial_san: 60, max_mp: 12, mov: 8, damage_bonus: "0", build: 0 },
          skills: []
        },
        public_summary: { occupation: "记者" },
        warnings: []
      }
    };
    const timeline = {
      investigator_id: "inv_1",
      branches: [{
        id: "branch_1",
        investigator_id: "inv_1",
        label: "主时间线",
        is_primary: true,
        status: "active",
        version: 1,
        created_at: "2026-07-30",
        updated_at: "2026-07-30"
      }],
      participations: [],
      memories: [],
      npc_encounters: [],
      permanent_changes: []
    };
    const proposal = {
      id: "change_1",
      investigator_id: "inv_1",
      campaign_id: "camp_1",
      campaign_title: "幽暗之门",
      branch_id: "branch_1",
      base_revision_id: "rev_1",
      source_event_id: "event_1",
      kind: "scar",
      summary: "左手掌留下烧伤",
      change: { text: "左手掌留下烧伤" },
      status: "proposed",
      resulting_revision_id: null,
      created_at: "2026-07-30",
      decided_at: null
    };
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/investigator-skills/catalog") return [];
      if (url === "/player-profile") return profile;
      if (url === "/investigators") return [investigator];
      if (url === "/campaigns/camp_1/my-investigators") return [];
      if (url === "/investigators/inv_1/timeline") return timeline;
      if (url === "/investigators/inv_1/permanent-changes") return [proposal];
      if (url === "/investigator-permanent-changes/change_1/decision") return {
        ...proposal,
        status: "accepted"
      };
      return [];
    });

    render(<InvestigatorPage
      campaign={{ id: "camp_1", title: "幽暗之门", system: "coc7", current_time: null }}
      identity={{
        member_id: "member_1",
        session_id: "session_1",
        campaign_id: "camp_1",
        role: "player",
        display_name: "测试玩家",
        pc_id: null
      }}
    />);

    await user.click(await screen.findByRole("button", { name: "跨团时间线" }));
    expect(await screen.findByText(/左手掌留下烧伤/)).toBeVisible();
    await user.type(screen.getByLabelText("玩家决定理由"), "符合本次实际经历");
    await user.click(screen.getByRole("button", { name: "接受变化" }));

    await waitFor(() => expect(requestJson).toHaveBeenCalledWith(
      "/investigator-permanent-changes/change_1/decision",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          action: "accepted",
          reason: "符合本次实际经历",
          expected_revision_id: "rev_1"
        })
      })
    ));
  });
});
