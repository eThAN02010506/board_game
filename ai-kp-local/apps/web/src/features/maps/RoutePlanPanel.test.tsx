import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthIdentity, SavedMap } from "../../api/types";
import { RoutePlanPanel } from "./RoutePlanPanel";

const identity: AuthIdentity = {
  member_id: "member_kp",
  session_id: "session_test",
  campaign_id: "campaign_test",
  role: "kp",
  display_name: "KP",
  pc_id: null
};

const map: SavedMap = {
  id: "map_test",
  campaign_id: "campaign_test",
  title: "镇中心",
  prompt: "",
  status: "published",
  width: 960,
  height: 640,
  locations: [
    { id: "loc_square", name: "广场", visibility: "table", x: 100, y: 100 },
    { id: "loc_police", name: "警局", visibility: "table", x: 300, y: 100 },
    { id: "loc_station", name: "车站", visibility: "table", x: 100, y: 300 }
  ],
  routes: [
    { id: "route_police", start_name: "广场", end_name: "警局", visibility: "table" },
    { id: "route_station", start_name: "广场", end_name: "车站", visibility: "table" }
  ],
  tokens: [
    {
      id: "token_a", map_id: "map_test", label: "甲", actor_type: "pc",
      actor_id: "pc_a", location_name: "广场", color: "#000", version: 0, x: 100, y: 100
    },
    {
      id: "token_b", map_id: "map_test", label: "乙", actor_type: "pc",
      actor_id: "pc_b", location_name: "广场", color: "#111", version: 0, x: 100, y: 100
    }
  ]
};

describe("RoutePlanPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("submits separate token paths as one split-party plan", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async (_input, init) =>
        new Response(JSON.stringify(init?.method === "POST" ? { id: "plan" } : []), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        })
    );
    render(<RoutePlanPanel identity={identity} map={map} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("本段终点"), {
      target: { value: "警局" }
    });
    fireEvent.click(screen.getByRole("button", { name: "加入方案" }));
    fireEvent.change(screen.getByLabelText("棋子"), { target: { value: "token_b" } });
    await waitFor(() =>
      expect(screen.getByLabelText("本段终点")).toHaveValue("警局")
    );
    fireEvent.change(screen.getByLabelText("本段终点"), {
      target: { value: "车站" }
    });
    fireEvent.click(screen.getByRole("button", { name: "加入方案" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并保存" }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(post).toBeDefined();
      const payload = JSON.parse(String(post?.[1]?.body));
      expect(payload.token_routes).toEqual([
        { token_id: "token_a", waypoints: ["广场", "警局"] },
        { token_id: "token_b", waypoints: ["广场", "车站"] }
      ]);
    });
  });
});
