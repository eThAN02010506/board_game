import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ParallelActionPlayerRegather } from "../../api/types";
import { ParallelActionRegatherStatus } from "./ParallelActionRegatherStatus";

const regather: ParallelActionPlayerRegather = {
  id: "regather-secret",
  status: "gathering",
  version: 2,
  participant_count: 4,
  submitted_count: 1,
  waiting_count: 3,
  self_phase: "awaiting_submission",
  own_action_id: null,
  updated_at: "2026-08-22T00:00:00Z",
  public_message: "The group revised its plan. Submit your current action when ready."
};

describe("ParallelActionRegatherStatus", () => {
  it("shows only aggregate progress and the viewer's next step", () => {
    render(<ParallelActionRegatherStatus regather={regather} />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("等待你的新行动");
    expect(status).toHaveTextContent("参与4 人");
    expect(status).toHaveTextContent("已提交1 人");
    expect(status).toHaveTextContent("待提交3 人");
    expect(status).not.toHaveTextContent("regather-secret");
  });
});
