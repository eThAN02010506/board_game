import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestJson } from "../../api/client";
import { GameplayWorkbench } from "./GameplayWorkbench";

vi.mock("../../api/client", () => ({
  requestJson: vi.fn()
}));

const campaign = {
  id: "camp_1",
  title: "常暗之厢测试",
  system: "coc7",
  current_time: "2017-05-11"
};

const kp = {
  member_id: "kp_1",
  session_id: "session_1",
  campaign_id: "camp_1",
  role: "kp" as const,
  display_name: "OldOnes",
  pc_id: null
};

const record = {
  campaign_id: "camp_1",
  investigator_id: "investigator_1",
  owner_profile_id: "profile_1",
  name: "林若川",
  status: "approved",
  submitted_revision_id: "revision_1",
  approved_revision_id: "revision_1",
  legacy_pc_id: "pc_1",
  timeline_branch_id: "timeline_1",
  timeline_branch: null,
  review_comment: null,
  submitted_revision: null,
  approved_revision: {
    id: "revision_1",
    investigator_id: "investigator_1",
    revision_no: 1,
    source_type: "manual",
    origin_type: "player_edit",
    canonical_sheet: {
      schema_version: "coc7-investigator-v1",
      ruleset_id: "coc7-keeper-cn-2002c",
      identity: { name: "林若川" },
      characteristics: {},
      derived: {
        max_hp: 10,
        max_mp: 10,
        initial_san: 60,
        max_san: 99,
        mov: 8,
        damage_bonus: "0",
        build: 0,
        dodge: 30
      },
      skills: []
    },
    public_summary: {},
    warnings: []
  },
  campaign_state: {
    campaign_id: "camp_1",
    investigator_id: "investigator_1",
    approved_revision_id: "revision_1",
    current_hp: 10,
    current_san: 60,
    current_mp: 10,
    current_luck: 50,
    conditions: [],
    inventory_delta: {},
    state_version: 0,
    current_game_time: null
  },
  reviews: [],
  diff: []
};

describe("GameplayWorkbench", () => {
  beforeEach(() => {
    vi.mocked(requestJson).mockImplementation(async (url, init) => {
      if (url.endsWith("/coc7/encounters") && init?.method === "POST") {
        return { id: "encounter_1" };
      }
      if (url.endsWith("/coc7/encounters")) return [];
      if (url.endsWith("/investigator-submissions")) return [record];
      if (url.endsWith("/coc7/state")) {
        return {
          investigator_id: record.investigator_id,
          state: record.campaign_state,
          events: []
        };
      }
      return [];
    });
  });

  it("lets a KP create an encounter from approved investigators", async () => {
    render(<GameplayWorkbench campaign={campaign} identity={kp} />);

    expect(await screen.findByText("HP 10 · SAN 60 · MP 10")).toBeInTheDocument();
    fireEvent.click(screen.getByText("创建战斗或追逐"));
    fireEvent.click(screen.getByLabelText("林若川"));
    fireEvent.click(screen.getByRole("button", { name: "创建战斗" }));

    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(
        "/campaigns/camp_1/coc7/encounters",
        expect.objectContaining({ method: "POST" })
      )
    );
    const createCall = vi
      .mocked(requestJson)
      .mock.calls.find((call) => call[1]?.method === "POST");
    const body = JSON.parse(String(createCall?.[1]?.body));
    expect(body.participants).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ investigator_id: "investigator_1" })
      ])
    );
  });

  it("keeps authoritative mutation controls hidden from players", async () => {
    const player = {
      ...kp,
      role: "player" as const,
      member_id: "player_1",
      pc_id: "pc_1",
      player_profile_id: "profile_1"
    };
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url.endsWith("/coc7/encounters")) return [];
      if (url.endsWith("/my-investigators")) return [record];
      if (url.endsWith("/coc7/state")) {
        return {
          investigator_id: record.investigator_id,
          state: record.campaign_state,
          events: []
        };
      }
      return [];
    });
    render(<GameplayWorkbench campaign={campaign} identity={player} />);
    expect(await screen.findByText("HP 10 · SAN 60 · MP 10")).toBeInTheDocument();
    expect(screen.queryByText("创建战斗或追逐")).not.toBeInTheDocument();
    expect(screen.queryByText("伤害、治疗、理智与成长")).not.toBeInTheDocument();
  });
});
