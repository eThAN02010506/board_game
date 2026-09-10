import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  listCampaignWorldEntities,
  updateCampaignWorldEntityState
} from "../../api/client";
import type { CampaignWorldEntityGraph } from "../../api/types";
import { WorldEntityStatePanel } from "./WorldEntityStatePanel";

vi.mock("../../api/client", () => ({
  listCampaignWorldEntities: vi.fn(),
  updateCampaignWorldEntityState: vi.fn()
}));

const listGraph = vi.mocked(listCampaignWorldEntities);
const updateState = vi.mocked(updateCampaignWorldEntityState);

const graph: CampaignWorldEntityGraph = {
  entities: [
    {
      id: "entity-officer",
      campaign_id: "campaign-1",
      entity_kind: "npc",
      archetype_id: "public_safety_officer",
      name: "约瑟夫·贝尔",
      description: "今晚的值班巡警。",
      visibility: "table",
      origin_kind: "world_expansion",
      origin_ref: "proposal-1:duty_officer",
      npc_id: "npc-1",
      created_from_event_id: "event-1",
      data: { state_dimensions: ["on_duty", "cooperation"] },
      state_version: 2,
      states: [
        {
          entity_id: "entity-officer",
          dimension: "on_duty",
          value: true,
          visibility: "table",
          version: 1,
          source_event_id: "event-2",
          updated_at: "2026-09-08 21:00:00"
        }
      ],
      created_at: "2026-09-08 20:00:00"
    },
    {
      id: "entity-agency",
      campaign_id: "campaign-1",
      entity_kind: "organization",
      archetype_id: "public_safety_agency",
      name: "黑溪镇警长办公室",
      description: "",
      visibility: "kp",
      origin_kind: "world_expansion",
      origin_ref: "proposal-1:agency",
      npc_id: null,
      created_from_event_id: "event-1",
      data: { state_dimensions: ["staffing"] },
      state_version: 0,
      states: [],
      created_at: "2026-09-08 20:00:00"
    }
  ],
  relations: [
    {
      id: "relation-1",
      campaign_id: "campaign-1",
      source_entity_id: "entity-officer",
      relation_slot_id: "agency",
      target_entity_id: "entity-agency",
      created_from_event_id: "event-1",
      created_at: "2026-09-08 20:00:00"
    }
  ],
  state_changes: [
    {
      id: "change-1",
      campaign_id: "campaign-1",
      entity_id: "entity-officer",
      dimension: "on_duty",
      from_value: null,
      to_value: true,
      visibility: "table",
      note: "亲眼看到值班",
      source_kind: "human_kp",
      state_version: 1,
      event_id: "event-2",
      created_at: "2026-09-08 21:00:00"
    }
  ]
};

describe("WorldEntityStatePanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listGraph.mockResolvedValue(graph);
    updateState.mockResolvedValue({
      entity: {
        id: "entity-officer",
        campaign_id: "campaign-1",
        state_version: 3
      },
      change: graph.state_changes[0]
    });
  });

  it("submits a catalog-bound optimistic state command through the shared kernel", async () => {
    const user = userEvent.setup();
    render(<WorldEntityStatePanel campaignId="campaign-1" />);

    expect(await screen.findByText("约瑟夫·贝尔")).toBeInTheDocument();
    expect(screen.getByText(/on_duty=true/)).toBeInTheDocument();
    expect(screen.getAllByText(/关系：agency/)).toHaveLength(2);

    await user.selectOptions(
      screen.getByRole("combobox", { name: "约瑟夫·贝尔 状态维度" }),
      "cooperation"
    );
    await user.type(
      screen.getByRole("textbox", { name: "约瑟夫·贝尔 状态值" }),
      "guarded"
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "约瑟夫·贝尔 状态可见性" }),
      "kp"
    );
    await user.type(
      screen.getByRole("textbox", { name: "约瑟夫·贝尔 状态备注" }),
      "交谈后的态度"
    );
    await user.click(screen.getAllByRole("button", { name: /提交状态/ })[0]);

    await waitFor(() => expect(updateState).toHaveBeenCalledTimes(1));
    expect(updateState).toHaveBeenCalledWith(
      "campaign-1",
      "entity-officer",
      expect.objectContaining({
        expected_version: 2,
        dimension: "cooperation",
        value: "guarded",
        visibility: "kp",
        note: "交谈后的态度",
        idempotency_key: expect.stringMatching(/^world-state:entity-officer:/)
      })
    );
    expect(listGraph).toHaveBeenCalledTimes(2);
  });

  it("does not offer table visibility for a KP-only entity", async () => {
    render(<WorldEntityStatePanel campaignId="campaign-1" />);

    const select = await screen.findByRole("combobox", {
      name: "黑溪镇警长办公室 状态可见性"
    });
    expect(within(select).queryByRole("option", { name: "table" })).not.toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "kp" })).toBeInTheDocument();
    expect(screen.getByText("亲眼看到值班")).toBeInTheDocument();
  });
});
