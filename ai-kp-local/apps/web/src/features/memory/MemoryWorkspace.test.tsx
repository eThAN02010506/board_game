import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { curateMemory, listMemoryTimeline } from "../../api/client";
import type { MemoryTimelineItem } from "../../api/types";
import { MemoryWorkspace } from "./MemoryWorkspace";

vi.mock("../../api/client", () => ({
  curateMemory: vi.fn(),
  listMemoryTimeline: vi.fn()
}));

const campaign = {
  id: "camp_1",
  title: "雾港",
  system: "coc7",
  current_time: "1924-10-03"
};

const kp = {
  member_id: "member_kp",
  session_id: "session_1",
  campaign_id: "camp_1",
  role: "kp" as const,
  display_name: "OldOnes",
  pc_id: null
};

const player = {
  ...kp,
  member_id: "member_player",
  role: "player" as const,
  display_name: "Player",
  pc_id: "pc_1",
  player_profile_id: "profile_1"
};

const memory: MemoryTimelineItem = {
  id: "mem_1",
  campaign_id: "camp_1",
  pc_id: "pc_1",
  pc_name: "林默",
  investigator_id: "inv_1",
  investigator_name: "林默",
  npc_id: null,
  npc_name: null,
  scope: "pc_side",
  classification: "side",
  importance: 2,
  visibility: "player",
  hidden: false,
  text: "账簿记录了午夜运货",
  happened_at: "1924-10-03T21:10:00",
  effective_time: "1924-10-03T21:10:00",
  source_event_id: "evt_1",
  source_event_type: "clue.discovered",
  source_event_summary: "在旧档案馆找到港口账簿",
  source_event_happened_at: "1924-10-03T21:10:00",
  source_event_created_at: "2026-07-30T10:00:00",
  curation_head_id: "mca_head",
  curation_reason: "初次整理",
  curated_by_member_id: "member_kp",
  curated_at: "2026-07-30T10:05:00",
  created_at: "2026-07-30T10:00:00"
};

describe("MemoryWorkspace", () => {
  beforeEach(() => {
    vi.mocked(listMemoryTimeline).mockResolvedValue([memory]);
    vi.mocked(curateMemory).mockResolvedValue({ id: "mca_next" });
  });

  it("shows provenance and appends an optimistic KP correction", async () => {
    render(
      <MemoryWorkspace
        campaign={campaign}
        identity={kp}
        pcs={[{ id: "pc_1", campaign_id: "camp_1", name: "林默" }]}
      />
    );

    expect((await screen.findAllByText("账簿记录了午夜运货")).length).toBe(2);
    expect(screen.getByText("在旧档案馆找到港口账簿")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("类别", { selector: ".memory-curation select" }), {
      target: { value: "clue" }
    });
    fireEvent.change(screen.getByLabelText("重要性"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("校正理由"), {
      target: { value: "确认是主线证据" }
    });
    fireEvent.click(screen.getByRole("button", { name: "追加校正" }));

    await waitFor(() =>
      expect(curateMemory).toHaveBeenCalledWith("camp_1", "mem_1", {
        classification: "clue",
        importance: 5,
        hidden: false,
        reason: "确认是主线证据",
        expected_head_action_id: "mca_head"
      })
    );
  });

  it("keeps curation controls out of the player projection", async () => {
    render(<MemoryWorkspace campaign={campaign} identity={player} pcs={[]} />);

    expect((await screen.findAllByText("账簿记录了午夜运货")).length).toBe(2);
    expect(screen.queryByText("追加 KP 校正")).not.toBeInTheDocument();
    expect(listMemoryTimeline).toHaveBeenCalledWith("camp_1", {
      pcId: undefined,
      classification: undefined,
      query: "",
      includeHidden: false
    });
  });
});
