import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createCampaignObjective,
  listCampaignObjectives,
  updateCampaignObjective
} from "../../api/client";
import type { AuthIdentity, CampaignObjective } from "../../api/types";
import { CampaignObjectivePanel } from "./CampaignObjectivePanel";

vi.mock("../../api/client", () => ({
  createCampaignObjective: vi.fn(),
  listCampaignObjectives: vi.fn(),
  updateCampaignObjective: vi.fn()
}));

const kp: AuthIdentity = {
  campaign_id: "campaign-1",
  display_name: "KP",
  member_id: "member-kp",
  pc_id: null,
  role: "kp",
  session_id: "session-1"
};

const objective: CampaignObjective = {
  id: "objective-1",
  campaign_id: "campaign-1",
  title: "查明钟楼停摆原因",
  public_description: "找到仍未解决的机械故障来源。",
  status: "blocked",
  visibility: "table",
  version: 2,
  events: [{
    id: "event-1",
    from_status: "open",
    to_status: "blocked",
    public_progress: "入口已经找到，但机关仍无法启动。",
    created_at: "2026-08-22T10:00:00Z"
  }]
};

describe("CampaignObjectivePanel", () => {
  beforeEach(() => {
    vi.mocked(listCampaignObjectives).mockReset().mockResolvedValue([objective]);
    vi.mocked(createCampaignObjective).mockReset().mockResolvedValue(objective);
    vi.mocked(updateCampaignObjective).mockReset().mockResolvedValue({
      ...objective,
      status: "completed",
      version: 3
    });
  });

  it("shows the same public authoritative objective to players without KP controls", async () => {
    render(<CampaignObjectivePanel campaignId="campaign-1" identity={{ ...kp, role: "player", pc_id: "pc-1" }} />);
    expect(await screen.findByText("查明钟楼停摆原因")).toBeInTheDocument();
    expect(screen.getByText("入口已经找到，但机关仍无法启动。")).toBeInTheDocument();
    expect(screen.queryByLabelText("任务标题")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "完成" })).not.toBeInTheDocument();
  });

  it("lets the KP create and transition objectives through typed commands", async () => {
    render(<CampaignObjectivePanel campaignId="campaign-1" identity={kp} />);
    expect(await screen.findByText("查明钟楼停摆原因")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务标题"), { target: { value: "调查旧医院" } });
    fireEvent.change(screen.getByLabelText("公开目标"), { target: { value: "确认失踪者去向" } });
    fireEvent.click(screen.getByRole("button", { name: "建立任务" }));
    await waitFor(() => expect(createCampaignObjective).toHaveBeenCalledOnce());
    expect(vi.mocked(createCampaignObjective).mock.calls[0]?.[1]).toMatchObject({
      title: "调查旧医院",
      public_description: "确认失踪者去向",
      visibility: "table"
    });

    fireEvent.change(screen.getByLabelText("查明钟楼停摆原因进展"), {
      target: { value: "修复了主齿轮。" }
    });
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    await waitFor(() => expect(updateCampaignObjective).toHaveBeenCalledWith(
      "objective-1",
      expect.objectContaining({
        expected_version: 2,
        public_progress: "修复了主齿轮。",
        status: "completed"
      })
    ));
  });
});
