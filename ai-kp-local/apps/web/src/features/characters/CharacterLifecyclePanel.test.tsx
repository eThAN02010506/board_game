import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  decideCharacterLifecycle,
  getCharacterLifecycle,
  proposeCharacterLifecycle
} from "../../api/client";
import type { AuthIdentity, CharacterLifecycleView } from "../../api/types";
import { CharacterLifecyclePanel } from "./CharacterLifecyclePanel";

vi.mock("../../api/client", () => ({
  decideCharacterLifecycle: vi.fn(),
  getCharacterLifecycle: vi.fn(),
  proposeCharacterLifecycle: vi.fn()
}));

const player: AuthIdentity = {
  member_id: "member-player",
  session_id: "session-1",
  campaign_id: "campaign-1",
  role: "player",
  display_name: "玩家",
  pc_id: "pc-1"
};

const view: CharacterLifecycleView = {
  characters: [
    { campaign_id: "campaign-1", investigator_id: "investigator-old", investigator_name: "旧角色", state: "dead", version: 2, updated_at: "now" },
    { campaign_id: "campaign-1", investigator_id: "investigator-new", investigator_name: "备用角色", state: "active", version: 1, updated_at: "now" }
  ],
  presence: [{ member_id: "member-player", display_name: "玩家", state: "active", investigator_id: "investigator-old", version: 1 }],
  requests: [{
    id: "request-1",
    campaign_id: "campaign-1",
    session_id: "session-1",
    member_id: "member-player",
    investigator_id: "investigator-old",
    replacement_investigator_id: null,
    action: "observe",
    status: "awaiting_player",
    reason: "角色死亡后转为观战",
    version: 1,
    created_at: "now"
  }],
  events: [{ id: "event-1", investigator_id: "investigator-old", member_id: null, action: "ruleset_state_sync", from_state: "active", to_state: "dead", public_summary: "调查员已经死亡；玩家可观战或换角。", created_at: "now", sequence: 1 }],
  capabilities: { resurrection: false, resurrection_reason: "当前规则插件不支持复活。", replacement_character: true, observer_after_death: true }
};

describe("CharacterLifecyclePanel", () => {
  beforeEach(() => {
    vi.mocked(getCharacterLifecycle).mockReset().mockResolvedValue(view);
    vi.mocked(decideCharacterLifecycle).mockReset().mockResolvedValue({});
    vi.mocked(proposeCharacterLifecycle).mockReset().mockResolvedValue({});
  });

  it("lets the affected player explicitly accept or reject a transition", async () => {
    const onIdentityChanged = vi.fn();
    const onPlayerActionBlockChanged = vi.fn();
    render(<CharacterLifecyclePanel campaignId="campaign-1" identity={player} onIdentityChanged={onIdentityChanged} onPlayerActionBlockChanged={onPlayerActionBlockChanged} />);
    expect(await screen.findByText("旧角色")).toBeInTheDocument();
    expect(onPlayerActionBlockChanged).toHaveBeenCalledWith(
      "当前调查员已经死亡；请先观战或确认换入后备角色。"
    );
    expect(screen.getByText("角色死亡后转为观战")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(decideCharacterLifecycle).toHaveBeenCalledWith(
      "request-1",
      { action: "accept", reason: "确认执行此生命周期变更", expected_version: 1 }
    ));
    expect(onIdentityChanged).toHaveBeenCalled();
  });

  it("gives the KP an explicit proposal form without offering unsupported resurrection", async () => {
    render(<CharacterLifecyclePanel campaignId="campaign-1" identity={{ ...player, member_id: "kp", role: "kp", pc_id: null }} />);
    await screen.findByText("旧角色");
    expect(screen.queryByRole("option", { name: /复活/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("选择桌员"), { target: { value: "member-player" } });
    fireEvent.change(screen.getByLabelText("选择生命周期动作"), { target: { value: "replace" } });
    fireEvent.change(screen.getByLabelText("选择进入游戏的角色"), { target: { value: "investigator-new" } });
    fireEvent.change(screen.getByLabelText("生命周期变更原因"), { target: { value: "原角色已死亡，换角继续" } });
    fireEvent.click(screen.getByRole("button", { name: "提交给玩家确认" }));
    await waitFor(() => expect(proposeCharacterLifecycle).toHaveBeenCalledWith(
      "campaign-1",
      expect.objectContaining({
        member_id: "member-player",
        action: "replace",
        replacement_investigator_id: "investigator-new"
      })
    ));
  });
});
