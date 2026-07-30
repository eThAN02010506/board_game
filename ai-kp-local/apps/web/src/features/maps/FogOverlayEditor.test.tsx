import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SavedMap } from "../../api/types";
import { FogOverlayEditor } from "./FogOverlayEditor";

const map: SavedMap = {
  id: "map_test",
  campaign_id: "campaign_test",
  title: "地图",
  prompt: "",
  status: "draft",
  width: 960,
  height: 640,
  fog_regions: [
    {
      id: "fog_test",
      map_id: "map_test",
      revision_id: "revision_test",
      label: "地下室",
      polygon: [{ x: 10, y: 10 }, { x: 200, y: 10 }, { x: 200, y: 200 }],
      status: "hidden",
      version: 3
    }
  ]
};

describe("FogOverlayEditor", () => {
  afterEach(() => vi.restoreAllMocks());

  it("treats fog as a separately deletable overlay", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ deleted: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );
    render(<FogOverlayEditor map={map} onSaved={vi.fn()} />);

    expect(screen.getByRole("application", { name: "自由雾区绘制画布" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/map-fog-regions/fog_test?expected_version=3",
        expect.objectContaining({ method: "DELETE" })
      )
    );
  });
});
