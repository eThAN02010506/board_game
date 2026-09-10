import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthIdentity, Campaign } from "../../api/types";
import { useTableMessages } from "../../app/hooks/useTableMessages";
import { TableCommunicationPanel } from "./TableCommunicationPanel";

vi.mock("../../app/hooks/useTableMessages", () => ({
  useTableMessages: vi.fn()
}));

const campaign = { id: "campaign-1", title: "公开团" } as Campaign;
const observer: AuthIdentity = {
  member_id: "observer-1",
  session_id: "session-1",
  campaign_id: campaign.id,
  role: "observer",
  display_name: "旁观者",
  pc_id: null
};
const send = vi.fn();

describe("TableCommunicationPanel", () => {
  beforeEach(() => {
    send.mockReset();
    vi.mocked(useTableMessages).mockReturnValue({
      messages: [{
        id: "message-1",
        campaign_id: campaign.id,
        session_id: observer.session_id,
        audience: "announcement",
        content: "只展示公开叙事。",
        sender: { member_id: "kp-1", display_name: "KP", role: "kp" },
        recipient: null,
        created_at: "2026-08-22T00:00:00Z"
      }],
      members: [
        { id: "kp-1", display_name: "KP", role: "kp", joined_at: "now" },
        { id: "player-1", display_name: "玩家", role: "player", joined_at: "now" }
      ],
      error: "",
      loading: false,
      refresh: vi.fn(),
      send
    });
  });

  it("keeps observers read-only except for a direct note to KP", async () => {
    render(<TableCommunicationPanel campaign={campaign} identity={observer} />);

    expect(screen.getByText("只展示公开叙事。")).toBeInTheDocument();
    const audience = screen.getByLabelText("发送范围") as HTMLSelectElement;
    expect(audience.disabled).toBe(true);
    expect(audience.value).toBe("direct");
    expect(screen.queryByRole("option", { name: "全桌发言" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /玩家/ })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("向 KP 私信"), { target: { value: "请暂停一下。" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(send).toHaveBeenCalledWith("direct", "请暂停一下。", "kp-1"));
  });
});
