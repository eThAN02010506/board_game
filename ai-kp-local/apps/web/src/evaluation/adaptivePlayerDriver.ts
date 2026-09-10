export type PlayerVisibleTurn = {
  playerAction: string;
  publicNarration: string;
};

export type PlayerVisibleObservation = {
  currentLocation: string | null;
  publicObjectives: readonly string[];
  recentTurns: readonly PlayerVisibleTurn[];
  visibleCheckOutcome: "success" | "failure" | null;
  visibleCheckSummary: string | null;
};

export type PlayerDriverPersona = {
  id: string;
  style: string;
};

export type PlayerDriverModelRequest = {
  system: string;
  user: string;
  maxOutputCharacters: number;
};

export type PlayerDriverModel = (
  request: PlayerDriverModelRequest
) => Promise<string>;

export type PlayerDriverDecision = {
  action: string;
  approach: "investigate" | "dialogue" | "travel" | "prepare" | "recover";
  reason: string;
  source: "model" | "deterministic_fallback";
  recoveryReason: string | null;
  observationFingerprint: string;
};

export type PlayerDriverHistory = {
  actions: readonly string[];
  lastObservationFingerprint: string | null;
  repeatedObservationCount: number;
};

const MAX_TURNS = 4;
const MAX_OBJECTIVES = 4;
const MAX_TURN_TEXT = 900;
const MAX_OBJECTIVE_TEXT = 300;
const MAX_ACTION_TEXT = 700;
const MAX_REASON_TEXT = 300;
const MODEL_OUTPUT_LIMIT = 1_600;
const DEFAULT_MODEL_TIMEOUT_MS = 20_000;

const APPROACHES = new Set([
  "investigate",
  "dialogue",
  "travel",
  "prepare",
  "recover"
]);

function bounded(value: string, limit: number): string {
  return value.replace(/\s+/g, " ").trim().slice(0, limit);
}

function normalizedAction(value: string): string {
  return bounded(value, MAX_ACTION_TEXT).toLocaleLowerCase();
}

function latestTurn(
  turns: readonly PlayerVisibleTurn[]
): PlayerVisibleTurn | undefined {
  return turns[turns.length - 1];
}

export function observationFingerprint(observation: PlayerVisibleObservation): string {
  const canonical = JSON.stringify({
    currentLocation: bounded(observation.currentLocation ?? "", 160),
    publicObjectives: observation.publicObjectives
      .slice(0, MAX_OBJECTIVES)
      .map((item) => bounded(item, MAX_OBJECTIVE_TEXT)),
    latestNarration: bounded(
      latestTurn(observation.recentTurns)?.publicNarration ?? "",
      MAX_TURN_TEXT
    ),
    visibleCheckOutcome: observation.visibleCheckOutcome,
    visibleCheckSummary: bounded(observation.visibleCheckSummary ?? "", MAX_TURN_TEXT)
  });
  // This is only a compact loop-detection/evaluation label, never an authority
  // or security hash. Keep the player-visible prose out of evidence files.
  let hash = 0x811c9dc5;
  for (let index = 0; index < canonical.length; index += 1) {
    hash = Math.imul(hash ^ canonical.charCodeAt(index), 0x01000193);
  }
  return `pv1-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function progressFingerprint(observation: PlayerVisibleObservation): string {
  return JSON.stringify({
    currentLocation: bounded(observation.currentLocation ?? "", 160),
    publicObjectives: observation.publicObjectives
      .slice(0, MAX_OBJECTIVES)
      .map((item) => bounded(item, MAX_OBJECTIVE_TEXT)),
    visibleCheckOutcome: observation.visibleCheckOutcome,
    visibleCheckSummary: bounded(observation.visibleCheckSummary ?? "", MAX_TURN_TEXT)
  });
}

export function advancePlayerDriverHistory(
  history: PlayerDriverHistory,
  observation: PlayerVisibleObservation,
  action?: string
): PlayerDriverHistory {
  // Public narration often changes wording without changing the actionable
  // situation. Loop recovery therefore watches location/objective/check state,
  // while the evidence label above still fingerprints the complete observation.
  const fingerprint = progressFingerprint(observation);
  const repeatedObservationCount = fingerprint === history.lastObservationFingerprint
    ? history.repeatedObservationCount + 1
    : 0;
  return {
    actions: action
      ? [...history.actions, bounded(action, MAX_ACTION_TEXT)].slice(-8)
      : history.actions,
    lastObservationFingerprint: fingerprint,
    repeatedObservationCount
  };
}

export function recordPlayerDriverAction(
  history: PlayerDriverHistory,
  action: string
): PlayerDriverHistory {
  return {
    ...history,
    actions: [...history.actions, bounded(action, MAX_ACTION_TEXT)].slice(-8)
  };
}

export function buildPlayerDriverPrompt(
  observation: PlayerVisibleObservation,
  persona: PlayerDriverPersona,
  history: PlayerDriverHistory
): PlayerDriverModelRequest {
  const safeObservation = {
    persona: {
      id: bounded(persona.id, 80),
      style: bounded(persona.style, 300)
    },
    current_location: bounded(observation.currentLocation ?? "未公开", 160),
    public_objectives: observation.publicObjectives
      .slice(0, MAX_OBJECTIVES)
      .map((item) => bounded(item, MAX_OBJECTIVE_TEXT)),
    recent_public_turns: observation.recentTurns.slice(-MAX_TURNS).map((turn) => ({
      player_action: bounded(turn.playerAction, MAX_TURN_TEXT),
      public_narration: bounded(turn.publicNarration, MAX_TURN_TEXT)
    })),
    own_visible_check: {
      outcome: observation.visibleCheckOutcome,
      summary: bounded(observation.visibleCheckSummary ?? "", MAX_TURN_TEXT)
    },
    recent_own_actions: history.actions.slice(-4).map((item) => bounded(item, MAX_ACTION_TEXT)),
    unchanged_observation_count: history.repeatedObservationCount
  };
  return {
    system: [
      "你是 TRPG 验收中的玩家 Driver，不是 KP，不知道模组秘密或结局条件。",
      "只能依据给出的玩家可见信息，为下一步生成一个具体、可执行、有限效果的自然语言行动。",
      "不得声称成功、创造身份/道具/线索，不得猜测隐藏 operator 或 ending witness。",
      "若刚失败，承认已发生代价并更换方法或来源；若状态重复，不要重复之前的行动。",
      "需要检定时只描述方法与目标，把技能、难度和失败代价留给 AI KP 裁定和玩家确认。",
      "仅返回严格 JSON，且恰好包含 action、approach、reason 三个键。",
      "approach 只能是 investigate/dialogue/travel/prepare/recover。"
    ].join("\n"),
    user: JSON.stringify(safeObservation),
    maxOutputCharacters: MODEL_OUTPUT_LIMIT
  };
}

function parseModelDecision(raw: string): Omit<PlayerDriverDecision, "source" | "recoveryReason" | "observationFingerprint"> {
  if (!raw.trim().startsWith("{") || !raw.trim().endsWith("}")) {
    throw new Error("player driver model did not return one JSON object");
  }
  const decoded: unknown = JSON.parse(raw);
  if (!decoded || typeof decoded !== "object" || Array.isArray(decoded)) {
    throw new Error("player driver model returned a non-object");
  }
  const record = decoded as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  if (keys.join(",") !== "action,approach,reason") {
    throw new Error("player driver model returned an unexpected schema");
  }
  if (
    typeof record.action !== "string"
    || typeof record.reason !== "string"
    || typeof record.approach !== "string"
    || !APPROACHES.has(record.approach)
  ) {
    throw new Error("player driver model returned invalid field types");
  }
  const action = bounded(record.action, MAX_ACTION_TEXT);
  const reason = bounded(record.reason, MAX_REASON_TEXT);
  if (action.length < 12 || reason.length < 2) {
    throw new Error("player driver model returned an empty decision");
  }
  return {
    action,
    approach: record.approach as PlayerDriverDecision["approach"],
    reason
  };
}

function narrationAnchor(observation: PlayerVisibleObservation): string {
  const latest = bounded(
    latestTurn(observation.recentTurns)?.publicNarration ?? "",
    120
  );
  return latest ? `针对刚才的公开情况“${latest}”，` : "";
}

function deterministicFallback(
  observation: PlayerVisibleObservation,
  persona: PlayerDriverPersona,
  history: PlayerDriverHistory,
  recoveryReason: string
): PlayerDriverDecision {
  const fingerprint = observationFingerprint(observation);
  const anchor = narrationAnchor(observation);
  if (observation.visibleCheckOutcome === "failure") {
    return {
      action: `${anchor}我接受刚才已发生的失败与代价，不重复原方法。我先盘点当前公开可接触的人、地点或记录，选一个独立来源做低风险核实；如果没有可行替代，请明确告诉我缺少什么条件。`,
      approach: "recover",
      reason: "可见检定已失败，改用独立来源并保留已发生代价。",
      source: "deterministic_fallback",
      recoveryReason,
      observationFingerprint: fingerprint
    };
  }
  if (history.repeatedObservationCount >= 2) {
    return {
      action: `${anchor}我停止重复先前的做法，先列出当前已公开但尚未核实的地点、人物或物件，选择其中一项有明确退路的替代路线推进；如果需要移动，我先前往已公开且可达的最近地点。`,
      approach: "travel",
      reason: "玩家可见状态连续未变，切换地点或证据来源解除循环。",
      source: "deterministic_fallback",
      recoveryReason,
      observationFingerprint: fingerprint
    };
  }
  const objective = bounded(observation.publicObjectives[0] ?? "", MAX_OBJECTIVE_TEXT);
  const location = bounded(observation.currentLocation ?? "当前场景", 160);
  const style = persona.id.includes("role") ? "dialogue" : "investigate";
  const action = style === "dialogue"
    ? `${anchor}我在${location}只向当前已公开、确实在场且愿意交流的一人追问：“关于${objective || "眼前问题"}，您亲眼确认的最近一件事是什么？”我只听具体回答，对方拒绝就结束。`
    : `${anchor}我在${location}先核对一项与${objective || "眼前问题"}直接相关、玩家已知且现场可接触的记录或痕迹；只做十分钟内可停止的目视或外观检查，目标是确认一项可验证的差异，没有合适对象就结束。`;
  return {
    action,
    approach: style,
    reason: "从最近公开信息出发，执行有限、可验证且可中止的下一步。",
    source: "deterministic_fallback",
    recoveryReason,
    observationFingerprint: fingerprint
  };
}

export async function planAdaptivePlayerAction(
  observation: PlayerVisibleObservation,
  persona: PlayerDriverPersona,
  history: PlayerDriverHistory,
  model?: PlayerDriverModel,
  timeoutMs = DEFAULT_MODEL_TIMEOUT_MS
): Promise<PlayerDriverDecision> {
  const fallback = (reason: string) => deterministicFallback(
    observation,
    persona,
    history,
    reason
  );
  if (!model) return fallback("player model not configured");
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const request = buildPlayerDriverPrompt(observation, persona, history);
    const raw = await Promise.race([
      model(request),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("player model timeout")), timeoutMs);
      })
    ]);
    if (raw.length > request.maxOutputCharacters) {
      return fallback("player model output exceeded the bounded response size");
    }
    const parsed = parseModelDecision(raw);
    if (history.actions.some((item) => normalizedAction(item) === normalizedAction(parsed.action))) {
      return fallback("player model repeated an earlier action");
    }
    return {
      ...parsed,
      source: "model",
      recoveryReason: null,
      observationFingerprint: observationFingerprint(observation)
    };
  } catch (error) {
    return fallback(error instanceof Error ? error.message : "player model failed");
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export const EMPTY_PLAYER_DRIVER_HISTORY: PlayerDriverHistory = {
  actions: [],
  lastObservationFingerprint: null,
  repeatedObservationCount: 0
};
