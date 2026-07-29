import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  credentialBridge,
  requestFile,
  requestJson
} from "../../api/client";
import { InvestigatorPage } from "./InvestigatorPage";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    requestFile: vi.fn(),
    requestJson: vi.fn()
  };
});

const profile = {
  id: "profile_test",
  display_name: "测试玩家",
  created_at: "2026-07-30 10:00:00",
  updated_at: "2026-07-30 10:00:00"
};

function mockLoadedLibrary() {
  vi.mocked(requestJson).mockImplementation(async (url) => {
    if (url === "/investigator-skills/catalog") return [];
    if (url === "/player-profile") return profile;
    if (url === "/investigators") return [];
    return [];
  });
}

describe("InvestigatorPage identity and editor isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    credentialBridge.session("");
    credentialBridge.admin("");
    credentialBridge.player("");
    vi.mocked(requestFile).mockReset();
  });

  it("keeps the stable player token after a transient server failure", async () => {
    localStorage.setItem("ai-kp-player-profile-token", "player_stable");
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/investigator-skills/catalog") return [];
      if (url === "/player-profile") {
        throw new ApiError("临时故障", 503);
      }
      if (url === "/investigators") return [];
      return [];
    });

    render(<InvestigatorPage campaign={null} identity={null} />);

    expect(await screen.findByText("临时故障")).toBeVisible();
    expect(localStorage.getItem("ai-kp-player-profile-token")).toBe("player_stable");
    expect(credentialBridge.snapshot().playerToken).toBe("player_stable");
  });

  it("evicts the stable player token only after an explicit authentication failure", async () => {
    localStorage.setItem("ai-kp-player-profile-token", "player_expired");
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/investigator-skills/catalog") return [];
      if (url === "/player-profile") {
        throw new ApiError("玩家身份已失效", 401);
      }
      if (url === "/investigators") return [];
      return [];
    });

    render(<InvestigatorPage campaign={null} identity={null} />);

    expect(await screen.findByText("玩家身份已失效")).toBeVisible();
    expect(localStorage.getItem("ai-kp-player-profile-token")).toBeNull();
    expect(credentialBridge.snapshot().playerToken).toBe("");
  });

  it("locks the complete manual editor while applying an asynchronous recommendation", async () => {
    const user = userEvent.setup();
    let resolveRecommendation: ((value: unknown) => void) | undefined;
    const recommendation = new Promise((resolve) => {
      resolveRecommendation = resolve;
    });
    localStorage.setItem("ai-kp-player-profile-token", "player_stable");
    mockLoadedLibrary();
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/investigator-skills/catalog") return [];
      if (url === "/player-profile") return profile;
      if (url === "/investigators") return [];
      if (url === "/investigator-skills/recommend") return recommendation;
      return [];
    });

    render(<InvestigatorPage campaign={null} identity={null} />);

    await screen.findByRole("heading", { name: "调查员编辑器" });
    await user.type(screen.getByLabelText("职业"), "记者");
    await user.clear(screen.getByLabelText("时代"));
    await user.type(screen.getByLabelText("时代"), "1920s");
    await user.type(screen.getByLabelText("年龄"), "28");
    for (const key of ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU", "LUCK"]) {
      await user.type(screen.getByLabelText(key), "60");
    }

    await user.click(screen.getByRole("button", { name: "生成并应用推荐加点" }));

    await waitFor(() => expect(screen.getByLabelText("职业")).toBeDisabled());
    expect(screen.getByLabelText("STR")).toBeDisabled();

    resolveRecommendation?.({
      profile_id: "journalist",
      profile_name: "记者",
      occupation_budget: 240,
      occupation_spent: 0,
      interest_budget: 120,
      interest_spent: 0,
      allocations: [],
      rationale: []
    });
    await waitFor(() => expect(screen.getByLabelText("职业")).toBeEnabled());
  });
});
