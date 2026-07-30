import { afterEach, describe, expect, it, vi } from "vitest";

import {
  analyzeModuleRunIntent,
  getCurrentModuleRun,
  getModuleRunDirectorState,
  generateWorldExpansionProposal,
  listModuleRuns,
  listRuleReviewCandidates,
  requestJson,
  reviewRuleCandidate,
  startModuleRun,
  transitionModuleRunScene,
  updateModuleRunEntityState,
  updateModuleRun
} from "./client";


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

  it("preserves HTTP status and application code for conflict recovery", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "运行记录已改变",
          code: "conflict"
        }),
        {
          status: 409,
          headers: { "Content-Type": "application/json" }
        }
      )
    );

    await expect(requestJson("/conflict")).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
      code: "conflict",
      message: "运行记录已改变"
    });
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

  it("uses the module-run endpoints and includes optimistic concurrency", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await listModuleRuns("camp/1", { limit: 10, offset: 2 });
    await getCurrentModuleRun("camp/1");
    await startModuleRun("camp/1", { module_id: "mod_1" });
    await updateModuleRun("run/1", {
      expected_version: 7,
      status: "paused"
    });
    await getModuleRunDirectorState("run/1");
    await transitionModuleRunScene("run/1", {
      expected_version: 7,
      scene_key: "warehouse",
      scene_title: "旧仓库",
      play_pace: "freeform"
    });
    await updateModuleRunEntityState("run/1", "clue/1", {
      expected_version: 8,
      status: "discovered"
    });
    await analyzeModuleRunIntent("run/1", "检查照片");
    await generateWorldExpansionProposal("run/1", {
      player_intent: "寻找警察局"
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/campaigns/camp%2F1/module-runs?limit=10&offset=2"
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/campaigns/camp%2F1/module-runs/current"
    );
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ module_id: "mod_1" })
    });
    expect(fetchMock.mock.calls[3]?.[0]).toBe("/api/module-runs/run%2F1");
    expect(fetchMock.mock.calls[3]?.[1]).toMatchObject({
      method: "PATCH",
      body: JSON.stringify({ expected_version: 7, status: "paused" })
    });
    expect(fetchMock.mock.calls[4]?.[0]).toBe(
      "/api/module-runs/run%2F1/director-state"
    );
    expect(fetchMock.mock.calls[5]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        expected_version: 7,
        scene_key: "warehouse",
        scene_title: "旧仓库",
        play_pace: "freeform"
      })
    });
    expect(fetchMock.mock.calls[6]?.[0]).toBe(
      "/api/module-runs/run%2F1/entities/clue%2F1/state"
    );
    expect(fetchMock.mock.calls[7]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ player_intent: "检查照片" })
    });
    expect(fetchMock.mock.calls[8]?.[0]).toBe(
      "/api/module-runs/run%2F1/director/world-expansion-proposals"
    );
    expect(fetchMock.mock.calls[8]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ player_intent: "寻找警察局" })
    });
  });

  it("uses the KP-only rule review endpoints", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await listRuleReviewCandidates("source/1");
    await reviewRuleCandidate("rule/1", {
      decision: "approved",
      note: "核对完成",
      golden_cases: [{
        name: "示例",
        inputs: { damage: 6 },
        expected_output: { major_wound: true }
      }]
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/rulebooks/sources/source%2F1/rules?status=review_required"
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/rulebooks/rules/rule%2F1/review"
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        decision: "approved",
        note: "核对完成",
        golden_cases: [{
          name: "示例",
          inputs: { damage: 6 },
          expected_output: { major_wound: true }
        }]
      })
    });
  });
});
