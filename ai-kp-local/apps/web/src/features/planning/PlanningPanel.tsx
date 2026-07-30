import type { Capability, CapabilityAudience, CapabilityStatus } from "../../api/types";

type Props = {
  activeNav: string;
  capabilities: Capability[];
  error: string;
  loading: boolean;
  onRetry: () => void;
};

const statusLabels: Record<CapabilityStatus, string> = {
  available: "已可用",
  partial: "部分可用",
  planned: "规划中"
};

const audienceLabels: Record<CapabilityAudience, string> = {
  all: "KP 与玩家",
  kp: "KP",
  player: "玩家"
};

const navCopy = {
  planning: {
    eyebrow: "后端能力目录",
    title: "功能规划",
    intro:
      "以下内容来自后端唯一的能力目录，只展示尚未完整交付的功能。它们是开发占位与验收约定，不代表现在已经可以使用。"
  },
  npcs: {
    eyebrow: "功能规划 · NPC",
    title: "NPC 档案与跨本关系",
    intro:
      "这里规划 NPC 搜索、相遇记录、关系变化、可再出现判断，以及跨模组重逢时间线；当前仅呈现真实实现进度。"
  }
} as const;

function selectCapabilities(activeNav: string, capabilities: Capability[]) {
  const unfinished = capabilities.filter((capability) => capability.status !== "available");
  if (activeNav === "npcs") {
    return unfinished.filter((capability) => capability.id === "npc_reappearance");
  }
  return unfinished;
}

export function PlanningPanel({
  activeNav,
  capabilities,
  error,
  loading,
  onRetry
}: Props) {
  if (activeNav !== "planning") return null;

  const copy = navCopy[activeNav];
  const visibleCapabilities = selectCapabilities(activeNav, capabilities);
  const labelsById = new Map(capabilities.map((capability) => [capability.id, capability.label]));

  return (
    <section className="planning-panel" id="planning-section">
      <div className="planning-heading">
        <div>
          <p className="eyebrow">{copy.eyebrow}</p>
          <h2>{copy.title}</h2>
        </div>
        <p>{copy.intro}</p>
      </div>

      {loading && <p className="planning-feedback">正在读取本地能力目录…</p>}

      {!loading && error && (
        <div className="planning-feedback planning-error" role="alert">
          <p>{error}</p>
          <button className="ghost-button" onClick={onRetry} type="button">
            重试读取
          </button>
        </div>
      )}

      {!loading && !error && visibleCapabilities.length === 0 && (
        <p className="planning-feedback">后端目录中没有符合当前分类的未完成功能。</p>
      )}

      {!loading && !error && visibleCapabilities.length > 0 && (
        <div className="capability-grid">
          {visibleCapabilities.map((capability) => (
            <article className="capability-card" key={capability.id}>
              <div className="capability-title-row">
                <h3>{capability.label}</h3>
                <span className={`capability-status ${capability.status}`}>
                  {statusLabels[capability.status]}
                </span>
              </div>
              <p className="capability-meta">
                阶段 {capability.phase} · 面向 {audienceLabels[capability.audience]}
              </p>
              <p>{capability.summary}</p>
              <div className="capability-detail">
                <h4>依赖</h4>
                <p>
                  {capability.dependencies.length > 0
                    ? capability.dependencies
                        .map((dependency) => labelsById.get(dependency) ?? dependency)
                        .join("、")
                    : "无"}
                </p>
              </div>
              <div className="capability-detail">
                <h4>验收标准</h4>
                <ul>
                  {capability.acceptance.map((criterion) => (
                    <li key={criterion}>{criterion}</li>
                  ))}
                </ul>
              </div>
              <p className="capability-warning">
                {capability.status === "partial"
                  ? "当前仅部分能力可用，尚不能按完整流程使用。"
                  : "当前尚未实现，本页不提供不可用的操作入口。"}
              </p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
