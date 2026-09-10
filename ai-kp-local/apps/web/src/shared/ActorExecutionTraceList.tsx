import type {
  ActorExecutionErrorCode,
  ActorExecutionTrace
} from "../api/types";

const errorLabels: Record<ActorExecutionErrorCode, string> = {
  actor_provider_unavailable: "模型服务暂不可用",
  actor_output_rejected: "模型输出未通过结构或安全校验",
  actor_required_content_missing: "模型遗漏了必须回应的内容",
  actor_verifier_rejected: "独立审查未通过",
  actor_turn_deadline_exhausted: "本轮角色生成达到时间上限",
  actor_model_budget_exhausted: "本轮模型角色名额已用完"
};

type Props = {
  traces?: ActorExecutionTrace[] | null;
};

export function ActorExecutionTraceList({ traces }: Props) {
  if (!traces?.length) return null;
  return (
    <details className="actor-execution-traces">
      <summary>角色代理执行状态</summary>
      <ul>
        {traces.map((trace) => (
          <li key={trace.entity_id}>
            <strong>{trace.entity_title}</strong>
            <span>
              {trace.execution === "model" ? "Actor 模型" : "确定性兜底"}
              {` · 生成尝试 ${trace.generation_attempt_count} 次`}
            </span>
            {trace.error_codes.map((code) => (
              <small key={code}>
                {errorLabels[code]} <code>{code}</code>
              </small>
            ))}
          </li>
        ))}
      </ul>
    </details>
  );
}
