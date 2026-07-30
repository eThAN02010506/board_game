import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createWorldFact,
  listWorldFacts,
  retconWorldFact
} from "../../api/client";
import type { WorldFactEntry } from "../../api/types";
import { FactWorkspace } from "./FactWorkspace";

vi.mock("../../api/client", () => ({
  createWorldFact: vi.fn(),
  listWorldFacts: vi.fn(),
  retconWorldFact: vi.fn()
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

const fact: WorldFactEntry = {
  campaign_id: "camp_1",
  fact_key: "fact_1",
  event_id: "evt_1",
  revision: 1,
  active: true,
  supersedes_event_id: null,
  asserted_by: "kp:member_kp",
  evidence_event_ids: [],
  source_reference: { kind: "approved_turn_proposal", proposal_id: "proposal_1" },
  happened_at: "1924-10-03 21:00:00",
  created_at: "2026-07-30 12:00:00",
  fact: {
    fact_id: "evt_1",
    category: "canonical_fact",
    visibility: "table",
    subject: "档案室门",
    predicate: "状态",
    object_text: "已经上锁",
    pc_id: null,
    supersedes_fact_id: null
  }
};

describe("FactWorkspace", () => {
  beforeEach(() => {
    vi.mocked(listWorldFacts).mockResolvedValue([fact]);
    vi.mocked(createWorldFact).mockResolvedValue(fact);
    vi.mocked(retconWorldFact).mockResolvedValue({
      append_only: true,
      retained_revision: fact,
      appended_revision: {
        ...fact,
        event_id: "evt_2",
        revision: 2,
        active: false,
        fact: {
          ...fact.fact,
          fact_id: "evt_2",
          category: "retconned",
          object_text: "此前记录错误",
          supersedes_fact_id: "evt_1"
        }
      }
    });
  });

  it("lets the KP append a typed fact and an optimistic retcon", async () => {
    render(
      <FactWorkspace
        campaign={campaign}
        identity={kp}
        pcs={[{ id: "pc_1", campaign_id: "camp_1", name: "林默" }]}
      />
    );

    expect(await screen.findByText("已经上锁")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("主体"), {
      target: { value: "码头仓库" }
    });
    fireEvent.change(screen.getByLabelText("关系或属性"), {
      target: { value: "营业状态" }
    });
    fireEvent.change(screen.getByLabelText("内容"), {
      target: { value: "今晚无人值守" }
    });
    fireEvent.click(screen.getByRole("button", { name: "追加到事实账本" }));

    await waitFor(() =>
      expect(createWorldFact).toHaveBeenCalledWith("camp_1", {
        fact_type: "canonical_fact",
        subject: "码头仓库",
        predicate: "营业状态",
        object_text: "今晚无人值守",
        pc_id: null,
        happened_at: null,
        source_reference: { kind: "fact_workspace" }
      })
    );

    fireEvent.change(screen.getByLabelText("撤回原因"), {
      target: { value: "此前记录错误" }
    });
    fireEvent.click(screen.getByRole("button", { name: "追加撤回" }));
    await waitFor(() =>
      expect(retconWorldFact).toHaveBeenCalledWith("camp_1", "fact_1", {
        expected_head_event_id: "evt_1",
        reason: "此前记录错误",
        source_reference: { kind: "fact_workspace" },
        happened_at: null
      })
    );
  });

  it("keeps KP mutation controls and provenance out of the player view", async () => {
    vi.mocked(listWorldFacts).mockResolvedValue([
      {
        ...fact,
        asserted_by: undefined,
        evidence_event_ids: undefined,
        source_reference: undefined,
        supersedes_event_id: undefined
      }
    ]);
    render(<FactWorkspace campaign={campaign} identity={player} pcs={[]} />);

    expect(await screen.findByText("已经上锁")).toBeInTheDocument();
    expect(screen.queryByText("追加事实")).not.toBeInTheDocument();
    expect(screen.queryByText("来源")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "追加撤回" })).not.toBeInTheDocument();
  });
});
