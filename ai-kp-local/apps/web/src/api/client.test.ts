import { afterEach, describe, expect, it, vi } from "vitest";

import {
  analyzeModuleRunIntent,
  askDirectorHelp,
  abandonParallelActionBatch,
  abandonDynamicBranch,
  analyzeSettingProfileSettlement,
  createModuleSettingProfile,
  getCurrentModuleRun,
  getRunSettingSelection,
  getModuleRunDirectorState,
  generateWorldExpansionProposal,
  getCurrentParallelActionPlayerBatch,
  getCurrentParallelActionPlayerRegather,
  listParallelActionAttentionBatches,
  listCampaignWorldEntities,
  listDirectorHelpAudits,
  listDynamicBranches,
  listNpcReappearanceCandidates,
  materializeWorldExpansionEncounter,
  resolveDynamicBranchBeat,
  resumeParallelActionBatch,
  resumeDynamicBranch,
  listModuleRuns,
  listModuleSettingProfiles,
  listSettingCatalogs,
  listRuleReviewCandidates,
  requestJson,
  reviewRuleCandidate,
  settleParallelPlayerActions,
  setRunSettingSelection,
  startModuleRun,
  transitionModuleRunScene,
  updateAutomationLevel,
  updateCampaignWorldEntityState,
  updateModuleRunEntityState,
  updateModuleRun,
  updateModuleSettingProfile
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

  it("keeps the request timeout active while consuming a deferred response body", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      const signal = init?.signal;
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('{"ok":'));
          signal?.addEventListener("abort", () => {
            controller.error(signal.reason);
          }, { once: true });
        }
      });
      return new Response(body, {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    });

    const request = requestJson("/deferred-body");
    const rejection = expect(request).rejects.toThrow(
      "请求超时，请检查后端或模型服务是否仍在运行"
    );
    await vi.advanceTimersByTimeAsync(310_000);
    await rejection;
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
    await updateAutomationLevel("run/1", {
      expected_version: 9,
      level: "balanced",
      reason: "降低人工审批频率"
    });
    await generateWorldExpansionProposal("run/1", {
      player_intent: "寻找警察局"
    });
    await materializeWorldExpansionEncounter("proposal/1", {
      idempotency_key: "contact:proposal-1",
      summary: "调查员进入警察局并与治安官交谈。",
      happened_at: "1928-10-03 22:15",
      facts: [
        {
          fact_type: "canonical_fact",
          subject: "警察局",
          predicate: "实际存在",
          object_text: "镇中心有一间警察局。"
        }
      ]
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
      "/api/module-runs/run%2F1/automation"
    );
    expect(fetchMock.mock.calls[8]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        expected_version: 9,
        level: "balanced",
        reason: "降低人工审批频率"
      })
    });
    expect(fetchMock.mock.calls[9]?.[0]).toBe(
      "/api/module-runs/run%2F1/director/world-expansion-proposals"
    );
    expect(fetchMock.mock.calls[9]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ player_intent: "寻找警察局" })
    });
    expect(fetchMock.mock.calls[10]?.[0]).toBe(
      "/api/kp/proposals/proposal%2F1/world-expansion-encounters"
    );
    expect(fetchMock.mock.calls[10]?.[1]).toMatchObject({
      method: "POST"
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

  it("uses versioned setting-profile and run-selection endpoints", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );
    const regions = [{
      region_id: "new-england",
      title: "新英格兰",
      pattern_id: "county_town_network",
      role_counts: { nearby: 2 }
    }];
    const document = { schema_version: "1" as const, regions, settlements: [] };

    await listSettingCatalogs();
    await listModuleSettingProfiles("module/1");
    await createModuleSettingProfile("module/1", {
      title: "Profile",
      setting_pack_id: "us.1920s",
      regions
    });
    await updateModuleSettingProfile("profile/1", {
      expected_version: 1,
      title: "Profile v2",
      document
    });
    await getRunSettingSelection("run/1");
    await setRunSettingSelection("run/1", {
      expected_run_version: 4,
      profile_id: "profile/1",
      profile_version: 2,
      settlement_id: "new-england.hub_1",
      reason: "当前场景"
    });
    await analyzeSettingProfileSettlement(
      "profile/1",
      2,
      "new-england.hub_1"
    );

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/setting-catalogs",
      "/api/modules/module%2F1/setting-profiles",
      "/api/modules/module%2F1/setting-profiles",
      "/api/setting-profiles/profile%2F1",
      "/api/module-runs/run%2F1/setting-selection",
      "/api/module-runs/run%2F1/setting-selection",
      "/api/setting-profiles/profile%2F1/settlements/new-england.hub_1/analysis?version=2"
    ]);
    expect(fetchMock.mock.calls[5]?.[1]).toMatchObject({
      method: "PUT",
      body: JSON.stringify({
        expected_run_version: 4,
        profile_id: "profile/1",
        profile_version: 2,
        settlement_id: "new-england.hub_1",
        reason: "当前场景"
      })
    });
  });

  it("loads the visibility-filtered campaign world entity graph", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ entities: [], relations: [], state_changes: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await listCampaignWorldEntities("campaign/1");

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/campaigns/campaign%2F1/world-entities"
    );
  });

  it("submits a versioned campaign world entity state command", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ entity: {}, change: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await updateCampaignWorldEntityState("campaign/1", "entity/1", {
      expected_version: 2,
      dimension: "cooperation",
      value: "guarded",
      visibility: "kp",
      idempotency_key: "world-state:test:1",
      note: "实际交谈后的状态"
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/campaigns/campaign%2F1/world-entities/entity%2F1/states"
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        expected_version: 2,
        dimension: "cooperation",
        value: "guarded",
        visibility: "kp",
        idempotency_key: "world-state:test:1",
        note: "实际交谈后的状态"
      })
    });
  });

  it("uses the parallel action settlement endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "approved" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await settleParallelPlayerActions("camp/1", {
      action_ids: ["action/1", "action/2"]
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/campaigns/camp%2F1/actions/settle"
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        action_ids: ["action/1", "action/2"]
      })
    });
  });

  it("loads only the current player's parallel batch projection", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("null", {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await expect(getCurrentParallelActionPlayerBatch("camp/1")).resolves.toBeNull();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/campaigns/camp%2F1/parallel-action-batches/current",
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    );
  });

  it("loads the KP recovery index with an explicit safe status filter", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("[]", {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await expect(
      listParallelActionAttentionBatches("camp/1")
    ).resolves.toEqual([]);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/campaigns/camp%2F1/parallel-action-batches?status=needs_attention",
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    );
  });

  it("loads only the current player's safe parallel regather projection", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("null", {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await expect(
      getCurrentParallelActionPlayerRegather("camp/1")
    ).resolves.toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/campaigns/camp%2F1/parallel-action-regathers/current",
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    );
  });

  it("binds parallel recovery commands to the exact batch version and reason", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response("{}", {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );
    const payload = { expected_version: 7, reason: "KP verified frozen authority." };

    await resumeParallelActionBatch("batch/1", payload);
    await abandonParallelActionBatch("batch/1", payload);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/parallel-action-batches/batch%2F1/resume"
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify(payload)
    });
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/parallel-action-batches/batch%2F1/abandon"
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify(payload)
    });
  });

  it("uses optimistic, idempotent dynamic-branch commands", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ branch: {}, event: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await listDynamicBranches("camp/1", "active");
    await resolveDynamicBranchBeat("branch/1", {
      expected_version: 2,
      command_id: "resolve:branch-001",
      outcome: "succeeded",
      note: "已发生",
      observed_effects: ["治安官确认身份"]
    });
    await resumeDynamicBranch("branch/1", {
      expected_version: 3,
      command_id: "resume:branch-001",
      note: "KP 确认恢复"
    });
    await abandonDynamicBranch("branch/1", {
      expected_version: 4,
      command_id: "abandon:branch-001",
      note: "玩家离开小镇"
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/campaigns/camp%2F1/dynamic-branches?status=active"
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/dynamic-branches/branch%2F1/beats/resolve"
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        expected_version: 2,
        command_id: "resolve:branch-001",
        outcome: "succeeded",
        note: "已发生",
        observed_effects: ["治安官确认身份"]
      })
    });
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "/api/dynamic-branches/branch%2F1/resume"
    );
    expect(fetchMock.mock.calls[3]?.[0]).toBe(
      "/api/dynamic-branches/branch%2F1/abandon"
    );
  });

  it("encodes the server-authorized NPC reappearance query", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await listNpcReappearanceCandidates("camp/1", "报社 线人");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/campaigns/camp%2F1/npc-reappearance-candidates?query=%E6%8A%A5%E7%A4%BE+%E7%BA%BF%E4%BA%BA",
      expect.any(Object)
    );
  });

  it("posts a director help question to the encoded module run", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    const currentRunController = new AbortController();
    const helpController = new AbortController();
    await getCurrentModuleRun("camp/1", { signal: currentRunController.signal });
    await askDirectorHelp(
      "run/1",
      "玩家想保护被害人，我该怎么办？",
      { signal: helpController.signal }
    );

    expect(fetchMock.mock.calls[0]).toEqual([
      "/api/campaigns/camp%2F1/module-runs/current",
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    ]);
    expect(fetchMock.mock.calls[0]?.[1]?.signal).not.toBe(currentRunController.signal);
    expect(fetchMock.mock.calls[1]).toEqual([
      "/api/module-runs/run%2F1/director/help",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ question: "玩家想保护被害人，我该怎么办？" }),
        signal: expect.any(AbortSignal)
      })
    ]);
    expect(fetchMock.mock.calls[1]?.[1]?.signal).not.toBe(helpController.signal);
  });

  it("lets callers abort current-run lookup and director help requests", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      const signal = init?.signal;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    });

    const currentRunController = new AbortController();
    const currentRunRequest = getCurrentModuleRun("camp/1", {
      signal: currentRunController.signal
    });
    const currentRunRejection = expect(currentRunRequest).rejects.toMatchObject({
      name: "AbortError"
    });
    currentRunController.abort();
    await currentRunRejection;

    const helpController = new AbortController();
    const helpRequest = askDirectorHelp("run/1", "现在怎么办？", {
      signal: helpController.signal
    });
    const helpRejection = expect(helpRequest).rejects.toMatchObject({
      name: "AbortError"
    });
    helpController.abort();
    await helpRejection;
  });

  it("encodes director-help audit pagination and lets callers abort it", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      const signal = init?.signal;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    });

    const controller = new AbortController();
    const request = listDirectorHelpAudits("camp/1", {
      limit: 10,
      beforeId: "audit/older cursor",
      signal: controller.signal
    });
    const rejection = expect(request).rejects.toMatchObject({ name: "AbortError" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/campaigns/camp%2F1/director-help/audits?limit=10&before_id=audit%2Folder+cursor",
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    );
    controller.abort();
    await rejection;
  });
});
