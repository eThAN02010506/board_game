import { describe, expect, it, vi } from "vitest";

import {
  advancePlayerDriverHistory,
  buildPlayerDriverPrompt,
  EMPTY_PLAYER_DRIVER_HISTORY,
  observationFingerprint,
  planAdaptivePlayerAction,
  type PlayerVisibleObservation
} from "./adaptivePlayerDriver";

const observation: PlayerVisibleObservation = {
  currentLocation: "报社前厅",
  publicObjectives: ["查明旧宅传闻的来源"],
  recentTurns: [{
    playerAction: "我请求查阅剪报。",
    publicNarration: "编辑拒绝开放资料室，并请你离开柜台。"
  }],
  visibleCheckOutcome: "failure",
  visibleCheckSummary: "说服失败：今天失去查档机会。"
};

describe("adaptive player driver", () => {
  it("uses a stable compact observation label without copying public prose", () => {
    const fingerprint = observationFingerprint(observation);
    expect(fingerprint).toMatch(/^pv1-[0-9a-f]{8}$/);
    expect(fingerprint).not.toContain("编辑");
    expect(observationFingerprint(observation)).toBe(fingerprint);
  });

  it("uses only bounded player-visible evidence in the small-model prompt", () => {
    const request = buildPlayerDriverPrompt(
      { ...observation, recentTurns: Array.from({ length: 10 }, (_, index) => ({
        playerAction: `action-${index}`,
        publicNarration: "x".repeat(2_000)
      })) },
      { id: "roleplayer", style: "具体对话" },
      EMPTY_PLAYER_DRIVER_HISTORY
    );

    const payload = JSON.parse(request.user) as { recent_public_turns: unknown[] };
    expect(payload.recent_public_turns).toHaveLength(4);
    expect(request.user.length).toBeLessThan(8_000);
    expect(request.system).toContain("不知道模组秘密或结局条件");
    expect(request.maxOutputCharacters).toBe(1_600);
  });

  it("accepts one strict JSON decision from an injected bounded model", async () => {
    const model = vi.fn().mockResolvedValue(JSON.stringify({
      action: "我离开柜台，去公开阅览区查找同日的报纸目录，只记下可核对的期号。",
      approach: "recover",
      reason: "遵守离开要求，改用公开且独立的资料来源。"
    }));

    const decision = await planAdaptivePlayerAction(
      observation,
      { id: "roleplayer", style: "具体对话" },
      EMPTY_PLAYER_DRIVER_HISTORY,
      model
    );

    expect(decision.source).toBe("model");
    expect(decision.approach).toBe("recover");
    expect(decision.recoveryReason).toBeNull();
    expect(model).toHaveBeenCalledOnce();
  });

  it("fails closed to a consequence-preserving recovery after invalid JSON", async () => {
    const decision = await planAdaptivePlayerAction(
      observation,
      { id: "rules_veteran", style: "有限效果" },
      EMPTY_PLAYER_DRIVER_HISTORY,
      async () => "```json\n{}\n```"
    );

    expect(decision.source).toBe("deterministic_fallback");
    expect(decision.approach).toBe("recover");
    expect(decision.action).toContain("接受刚才已发生的失败与代价");
    expect(decision.recoveryReason).toMatch(/JSON object/);
  });

  it("detects an unchanged visible state and changes route instead of looping", async () => {
    let history = advancePlayerDriverHistory(EMPTY_PLAYER_DRIVER_HISTORY, observation);
    history = advancePlayerDriverHistory(history, {
      ...observation,
      recentTurns: [{ playerAction: "行动一", publicNarration: "描述换了一种说法。" }]
    }, "重复行动一");
    history = advancePlayerDriverHistory(history, {
      ...observation,
      recentTurns: [{ playerAction: "行动二", publicNarration: "描述再次换了说法。" }]
    }, "重复行动二");
    const decision = await planAdaptivePlayerAction(
      { ...observation, visibleCheckOutcome: null, visibleCheckSummary: null },
      { id: "trpg_newcomer", style: "小心观察" },
      history
    );

    expect(history.repeatedObservationCount).toBe(2);
    expect(decision.approach).toBe("travel");
    expect(decision.action).toContain("停止重复先前的做法");
  });

  it("rejects a model action that exactly repeats recent player history", async () => {
    const repeated = "我检查桌上已公开的记录，只核对一个日期。";
    const history = {
      ...EMPTY_PLAYER_DRIVER_HISTORY,
      actions: [repeated]
    };
    const decision = await planAdaptivePlayerAction(
      { ...observation, visibleCheckOutcome: null, visibleCheckSummary: null },
      { id: "trpg_newcomer", style: "小心观察" },
      history,
      async () => JSON.stringify({
        action: repeated,
        approach: "investigate",
        reason: "继续核对。"
      })
    );

    expect(decision.source).toBe("deterministic_fallback");
    expect(decision.recoveryReason).toContain("repeated");
  });
});
