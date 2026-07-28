import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Campaign, Role } from "../../api/types";
import { CampaignPanel } from "./CampaignPanel";

const campaigns: Campaign[] = [
  {
    id: "campaign_active",
    title: "雾港疑云",
    system: "coc7",
    current_time: "1928-10-03 20:00"
  },
  {
    id: "campaign_other",
    title: "雪夜列车",
    system: "coc7",
    current_time: null
  }
];

function renderPanel(role: Role | undefined = "kp") {
  const callbacks = {
    onAdminTokenChange: vi.fn(),
    onCampaignTitleChange: vi.fn(),
    onCampaignTimeChange: vi.fn(),
    onApplyAdminToken: vi.fn(),
    onCreateCampaign: vi.fn((event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
    }),
    onSelectCampaign: vi.fn()
  };

  render(
    <CampaignPanel
      activeCampaignId="campaign_active"
      adminToken=""
      campaignTime="1928-10-03 20:00"
      campaigns={campaigns}
      campaignTitle=""
      role={role}
      {...callbacks}
    />
  );

  return callbacks;
}

describe("CampaignPanel", () => {
  it("keeps campaign creation and admin credentials out of the player view", () => {
    const { onSelectCampaign } = renderPanel("player");

    expect(screen.queryByLabelText(/管理员口令/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("团名")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建测试团" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /雪夜列车 coc7/ }));
    expect(onSelectCampaign).toHaveBeenCalledWith(campaigns[1]);
  });

  it("forwards KP form edits, token application, and campaign creation", () => {
    const {
      onAdminTokenChange,
      onApplyAdminToken,
      onCampaignTimeChange,
      onCampaignTitleChange,
      onCreateCampaign
    } = renderPanel();

    fireEvent.change(screen.getByLabelText(/管理员口令/), {
      target: { value: "local-admin-token" }
    });
    fireEvent.change(screen.getByLabelText("团名"), {
      target: { value: "灰塔来信" }
    });
    fireEvent.change(screen.getByLabelText("当前时间"), {
      target: { value: "1929-01-05 21:30" }
    });
    fireEvent.click(screen.getByRole("button", { name: "应用/清除管理员口令" }));
    fireEvent.click(screen.getByRole("button", { name: "创建测试团" }));

    expect(onAdminTokenChange).toHaveBeenCalledWith("local-admin-token");
    expect(onCampaignTitleChange).toHaveBeenCalledWith("灰塔来信");
    expect(onCampaignTimeChange).toHaveBeenCalledWith("1929-01-05 21:30");
    expect(onApplyAdminToken).toHaveBeenCalledTimes(1);
    expect(onCreateCampaign).toHaveBeenCalledTimes(1);
  });

  it("marks only the active campaign as selected", () => {
    renderPanel();

    expect(screen.getByRole("button", { name: /雾港疑云/ })).toHaveClass("selected");
    expect(screen.getByRole("button", { name: /雪夜列车/ })).not.toHaveClass("selected");
  });
});
