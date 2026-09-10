import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  commandInventoryItem,
  createInventoryItem,
  createInventoryOffer,
  decideInventoryOffer,
  getInventory,
  tradeInventoryItem
} from "../../api/client";
import type { AuthIdentity, InventoryState } from "../../api/types";
import { InventoryPanel } from "./InventoryPanel";

vi.mock("../../api/client", () => ({
  commandInventoryItem: vi.fn(),
  createInventoryItem: vi.fn(),
  createInventoryOffer: vi.fn(),
  decideInventoryOffer: vi.fn(),
  getInventory: vi.fn(),
  tradeInventoryItem: vi.fn()
}));

const identity: AuthIdentity = {
  member_id: "member-1",
  session_id: "session-1",
  campaign_id: "campaign-1",
  role: "player",
  display_name: "玩家甲",
  pc_id: "pc-1"
};

const state: InventoryState = {
  items: [
    {
      id: "item-owned",
      campaign_id: "campaign-1",
      item_type: "consumable",
      public_name: "绷带",
      public_description: "可公开知道的医疗用品。",
      publicly_listed: false,
      quantity: 2,
      is_unique: false,
      holder_kind: "investigator",
      holder_id: "investigator-1",
      state: "available",
      equipped_slot: null,
      version: 1,
      revealed_properties: { warning: "仅一次有效" }
    }
  ],
  balances: [{
    campaign_id: "campaign-1",
    account_kind: "investigator",
    account_id: "investigator-1",
    currency_code: "USD_CENTS",
    balance_minor: 725,
    version: 1
  }],
  transfer_offers: [],
  transfer_targets: [{ investigator_id: "investigator-2", name: "玩家乙" }],
  self_investigator_id: "investigator-1",
  recipes: []
};

describe("InventoryPanel", () => {
  beforeEach(() => {
    vi.mocked(getInventory).mockReset().mockResolvedValue(state);
    vi.mocked(commandInventoryItem).mockReset().mockResolvedValue({});
    vi.mocked(createInventoryItem).mockReset().mockResolvedValue({});
    vi.mocked(createInventoryOffer).mockReset().mockResolvedValue({});
    vi.mocked(decideInventoryOffer).mockReset().mockResolvedValue({});
    vi.mocked(tradeInventoryItem).mockReset().mockResolvedValue({});
  });

  it("shows the player-safe ledger and submits versioned item commands", async () => {
    render(<InventoryPanel campaignId="campaign-1" identity={identity} />);

    expect(await screen.findByText("绷带")).toBeInTheDocument();
    expect(screen.getByText("725 USD_CENTS")).toBeInTheDocument();
    expect(screen.getByText(/仅一次有效/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "消耗 1" }));
    await waitFor(() => expect(commandInventoryItem).toHaveBeenCalledWith(
      "item-owned",
      expect.objectContaining({
        command_type: "consume",
        expected_version: 1,
        quantity: 1
      })
    ));
  });

  it("requires an explicit recipient before creating a transfer offer", async () => {
    render(<InventoryPanel campaignId="campaign-1" identity={identity} />);
    await screen.findByText("绷带");
    const button = screen.getByRole("button", { name: "提出转交" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("将绷带交给"), {
      target: { value: "investigator-2" }
    });
    fireEvent.click(button);
    await waitFor(() => expect(createInventoryOffer).toHaveBeenCalledWith(
      "campaign-1",
      expect.objectContaining({
        item_id: "item-owned",
        expected_item_version: 1,
        to_investigator_id: "investigator-2"
      })
    ));
  });

  it("purchases only a server-listed item with the current balance version", async () => {
    vi.mocked(getInventory).mockResolvedValue({
      ...state,
      items: [{
        ...state.items[0],
        id: "vendor-item",
        public_name: "手提灯",
        item_type: "tool",
        holder_kind: "npc",
        holder_id: "vendor-1",
        publicly_listed: true,
        unit_value_minor: 250,
        currency_code: "USD_CENTS"
      }]
    });
    render(<InventoryPanel campaignId="campaign-1" identity={identity} />);
    fireEvent.click(await screen.findByRole("button", { name: "购买 250 USD_CENTS" }));
    await waitFor(() => expect(tradeInventoryItem).toHaveBeenCalledWith(
      "campaign-1",
      expect.objectContaining({
        item_id: "vendor-item",
        investigator_id: "investigator-1",
        expected_investigator_balance_version: 1,
        expected_counterparty_balance_version: null
      })
    ));
  });

  it("lets only the KP register public loot through the authority API", async () => {
    render(<InventoryPanel campaignId="campaign-1" identity={{ ...identity, role: "kp" }} />);
    await screen.findByText("绷带");
    fireEvent.click(screen.getByText("登记公开战利品"));
    fireEvent.change(screen.getByLabelText("战利品名称"), {
      target: { value: "旧皮包" }
    });
    fireEvent.change(screen.getByLabelText("战利品公开说明"), {
      target: { value: "冲突结束后留下的公开物品。" }
    });
    fireEvent.click(screen.getByRole("button", { name: "登记到权威账本" }));

    await waitFor(() => expect(createInventoryItem).toHaveBeenCalledWith(
      "campaign-1",
      expect.objectContaining({
        public_name: "旧皮包",
        holder_kind: "loot",
        quantity: 1
      })
    ));
  });
});
