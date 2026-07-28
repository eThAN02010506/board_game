import { afterEach, describe, expect, it, vi } from "vitest";

import { requestJson } from "./client";


describe("API client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("shows FastAPI detail instead of raw JSON", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "地图不存在" }), {
        status: 404,
        headers: { "Content-Type": "application/json" }
      })
    );

    await expect(requestJson("/missing")).rejects.toThrow("地图不存在");
  });

  it("forwards an abort signal to fetch", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await requestJson("/health");

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][1]?.signal).toBeInstanceOf(AbortSignal);
  });
});
