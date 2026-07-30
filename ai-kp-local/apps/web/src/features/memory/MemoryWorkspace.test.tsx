import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  curateMemory,
  generateSessionRecap,
  getLatestSessionRecap,
  listMemoryTimeline,
  reviewSessionRecapCandidate
} from "../../api/client";
import type { MemoryTimelineItem, SessionRecapRun } from "../../api/types";
import { MemoryWorkspace } from "./MemoryWorkspace";

vi.mock("../../api/client", () => ({
  curateMemory: vi.fn(),
  generateSessionRecap: vi.fn(),
  getLatestSessionRecap: vi.fn(),
  listMemoryTimeline: vi.fn(),
  reviewSessionRecapCandidate: vi.fn()
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

const recap: SessionRecapRun = {
  id: "recap_1",
  campaign_id: "camp_1",
  session_id: "session_1",
  status: "draft",
  event_window_hash: "a".repeat(64),
  event_ids: ["evt_1"],
  generation_cutoff: "2026-07-30 10:10:00",
  source_model: "local-test",
  prompt_version: "session-recap.v1",
  repaired: false,
  created_by_member_id: "member_kp",
  created_at: "2026-07-30 10:10:00",
  completed_at: null,
  candidates: [{
    id: "recap_candidate_1",
    run_id: "recap_1",
    campaign_id: "camp_1",
    order_index: 0,
    text: "林默发现港口账簿记录午夜运货。",
    scope: "clue",
    importance: 4,
    visibility: "player",
    pc_id: "pc_1",
    npc_id: null,
    happened_at: "1924-10-03T21:10:00",
    source_event_ids: ["evt_1"],
    rationale: "这是可继续调查的关键线索。",
    status: "draft",
    review_payload: {},
    memory_id: null,
    reviewed_by_member_id: null,
    reviewed_at: null,
    created_at: "2026-07-30 10:10:00"
  }]
};

describe("MemoryWorkspace", () => {
  beforeEach(() => {
    vi.mocked(listMemoryTimeline).mockResolvedValue([memory]);
    vi.mocked(curateMemory).mockResolvedValue({ id: "mca_next" });
    vi.mocked(getLatestSessionRecap).mockResolvedValue(null);
    vi.mocked(generateSessionRecap).mockResolvedValue(recap);
    vi.mocked(reviewSessionRecapCandidate).mockResolvedValue({
      ...recap.candidates[0],
      status: "approved",
      memory_id: "mem_recap"
    });
  });

  it("keeps recap suggestions draft until the KP explicitly approves one", async () => {
    vi.mocked(getLatestSessionRecap).mockResolvedValue(recap);
    render(
      <MemoryWorkspace
        campaign={campaign}
        identity={kp}
        pcs={[{ id: "pc_1", campaign_id: "camp_1", name: "林默" }]}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "生成团后摘要候选" }));
    expect(await screen.findByDisplayValue("林默发现港口账簿记录午夜运货。")).toBeInTheDocument();
    expect(screen.getByText("这是可继续调查的关键线索。")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("KP 审核理由"), {
      target: { value: "来源和角色归属已核对" }
    });
    fireEvent.click(screen.getByRole("button", { name: "批准并写入记忆" }));

    await waitFor(() =>
      expect(reviewSessionRecapCandidate).toHaveBeenCalledWith(
        "recap_candidate_1",
        {
          action: "approve",
          reason: "来源和角色归属已核对",
          text: "林默发现港口账簿记录午夜运货。",
          scope: "clue",
          importance: 4,
          visibility: "player",
          pc_id: "pc_1"
        }
      )
    );
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
