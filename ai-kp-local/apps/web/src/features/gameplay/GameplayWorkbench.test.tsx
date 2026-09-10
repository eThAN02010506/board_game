import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestJson } from "../../api/client";
import type { EncounterActionRequest } from "../../api/types";
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
        expect.objectContaining({ investigator_id: "investigator_1", side: "party" }),
        expect.objectContaining({
          side: "opposition",
          action_profiles: [
            expect.objectContaining({
              action_key: "primary_attack",
              kind: "melee",
              skill_target: 50,
              damage_expression: "1d3"
            })
          ]
        })
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

  it("lets the active player preview, edit, and explicitly confirm a bounded turn", async () => {
    const player = {
      ...kp,
      role: "player" as const,
      member_id: "player_1",
      pc_id: "pc_1",
      player_profile_id: "profile_1"
    };
    const encounter = {
      id: "encounter_1",
      campaign_id: "camp_1",
      session_id: "session_1",
      kind: "combat",
      title: "仓库遭遇",
      status: "active",
      round_no: 1,
      turn_index: 0,
      version: 0,
      state: {
        participants: [
          {
            participant_id: "pc",
            name: "林若川",
            investigator_id: "investigator_1",
            dex: 70
          },
          { participant_id: "threat", name: "黑影", dex: 40 }
        ],
        turn_order: ["pc", "threat"]
      },
      created_at: "2026-08-22T00:00:00Z",
      updated_at: "2026-08-22T00:00:00Z",
      completed_at: null
    };
    const preview: EncounterActionRequest = {
      id: "encaction_1",
      encounter_id: encounter.id,
      participant_id: "pc",
      client_action_id: "client-action-0001",
      encounter_version: 0,
      action_text: "我用手肘撞向它的肩膀。",
      action_key: "unarmed",
      target_id: "threat",
      status: "awaiting_confirmation",
      preview: {
        action_key: "unarmed",
        action_label: "徒手格斗",
        action_kind: "melee",
        action_text: "我用手肘撞向它的肩膀。",
        actor_name: "林若川",
        target_name: "黑影",
        target_required: true,
        description: "按 CoC7 结算",
        confirmation_required: true,
        public_message: "请确认这个动作、目标和公开做法；骰值尚未生成。"
      },
      result: null,
      version: 1,
      created_at: "2026-08-22T00:00:00Z",
      updated_at: "2026-08-22T00:00:00Z",
      committed_at: null
    };
    const improvised: EncounterActionRequest = {
      ...preview,
      id: "encaction_improvised",
      action_key: "improvised",
      target_id: null,
      status: "needs_attention",
      preview: {
        ...preview.preview,
        action_key: "improvised",
        action_label: "规则外 / Rule of Cool",
        action_kind: "improvised",
        target_name: null,
        target_required: false,
        confirmation_required: false,
        public_message: "请让遭遇意图 Agent 提出方案。"
      }
    };
    const agentProposal: EncounterActionRequest = {
      ...improvised,
      action_key: "defend",
      status: "awaiting_confirmation",
      version: 2,
      preview: {
        ...improvised.preview,
        action_key: "defend",
        action_label: "采取防御姿态",
        action_kind: "defend",
        confirmation_required: true,
        public_message: "可以按一次防御动作处理。"
      }
    };
    let activeRequest: EncounterActionRequest | null = null;
    vi.mocked(requestJson).mockImplementation(async (url, init) => {
      if (url.endsWith("/coc7/encounters")) return [encounter];
      if (url.endsWith("/my-investigators")) return [record];
      if (url.endsWith("/coc7/state")) {
        return { investigator_id: record.investigator_id, state: record.campaign_state, events: [] };
      }
      if (url.endsWith("/events")) return [];
      if (url.endsWith("/action-options")) {
        return {
          encounter_id: encounter.id,
          encounter_version: 0,
          participant_id: "pc",
          participant_name: "林若川",
          options: [
            {
              action_key: "unarmed", label: "徒手格斗", kind: "melee",
              target_required: true, description: "按 CoC7 结算"
            },
            {
              action_key: "improvised", label: "规则外 / Rule of Cool", kind: "improvised",
              target_required: false, description: "由 Agent 规划"
            }
          ],
          targets: [{ participant_id: "threat", name: "黑影" }],
          active_request: activeRequest
        };
      }
      if (url.endsWith("/action-previews") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        activeRequest = body.action_key === "improvised" ? improvised : preview;
        return activeRequest;
      }
      if (url.endsWith("/agent-proposal") && init?.method === "POST") {
        activeRequest = agentProposal;
        return agentProposal;
      }
      if (url.endsWith("/cancel") && init?.method === "POST") {
        activeRequest = null;
        return { ...improvised, status: "cancelled" };
      }
      if (url.endsWith("/confirm") && init?.method === "POST") {
        activeRequest = null;
        return { ...preview, status: "committed", version: 3, committed_at: "now" };
      }
      return [];
    });

    render(<GameplayWorkbench campaign={campaign} identity={player} />);
    expect(await screen.findByRole("heading", { name: "我的遭遇回合" })).toBeInTheDocument();
    expect(
      screen.queryByText("AI KP 正在让敌方回合 Agent", { exact: false })
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("遭遇规则动作"), {
      target: { value: "improvised" }
    });
    fireEvent.change(screen.getByLabelText("遭遇行动描述"), {
      target: { value: "我踹翻桌子挡住对方的视线。" }
    });
    fireEvent.click(screen.getByRole("button", { name: "预览并手动确认" }));
    fireEvent.click(await screen.findByRole("button", { name: "让遭遇 Agent 提案" }));
    expect(await screen.findByText("可以按一次防御动作处理。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修改行动" }));
    await waitFor(() => expect(screen.getByLabelText("遭遇行动描述")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("遭遇规则动作"), {
      target: { value: "unarmed" }
    });
    fireEvent.change(screen.getByLabelText("遭遇行动描述"), {
      target: { value: "我用手肘撞向它的肩膀。" }
    });
    fireEvent.click(screen.getByRole("button", { name: "预览并手动确认" }));
    expect(await screen.findByText("骰值尚未生成。", { exact: false })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认并结算" }));
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith(
      "/encounter-action-requests/encaction_1/confirm",
      expect.objectContaining({ method: "POST" })
    ));
  });
});
