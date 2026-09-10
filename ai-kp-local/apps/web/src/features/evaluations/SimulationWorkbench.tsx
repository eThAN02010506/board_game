import { FlaskConical, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { requestJson } from "../../api/client";
import type { AuthIdentity, Campaign } from "../../api/types";

type Props = { campaign: Campaign | null; identity: AuthIdentity | null };
type SimulationCase = {
  id: string; name: string; definition_hash: string;
  runs: Array<{
    id: string;
    status: string;
    runner_version: string;
    metrics: Record<string, number>;
    trajectory: Array<Record<string, unknown>>;
    result_fingerprint: string;
  }>;
};
type ReplayLog = {
  actor?: string;
  audience?: string;
  content?: string;
};

const actorLabel: Record<string, string> = {
  player: "PL",
  kp: "KP",
  model: "模型",
  rules_engine: "规则",
  system: "系统",
};

function replayLogs(trajectory: Array<Record<string, unknown>>): ReplayLog[] {
  return trajectory.flatMap((step) => (
    Array.isArray(step.log) ? step.log as ReplayLog[] : []
  ));
}

const sample = {
  mode: "service_replay",
  steps: [
    {
      action: "generate_map", alias: "layout", title: "通用连通性测试",
      locations: ["节点甲", "节点乙", "节点丙"],
      routes: [["节点甲", "节点乙"], ["节点乙", "节点丙"]]
    },
    {
      action: "place_token", alias: "actor", map_ref: "layout",
      label: "测试角色", location: "节点甲"
    },
    {
      action: "plan_routes", alias: "route", map_ref: "layout",
      title: "沿已声明连接移动",
      token_routes: [{ token_ref: "actor", waypoints: ["节点甲", "节点乙", "节点丙"] }]
    },
    { action: "move_token", token_ref: "actor", to: "节点乙" },
    { action: "move_token", token_ref: "actor", to: "节点丙" },
    { action: "assert_equals", ref: "actor", path: "location_name", value: "节点丙" }
  ]
};

export function SimulationWorkbench({ campaign, identity }: Props) {
  const [cases, setCases] = useState<SimulationCase[]>([]);
  const [name, setName] = useState("通用自由行动回归评估");
  const [definition, setDefinition] = useState(JSON.stringify(sample, null, 2));
  const [message, setMessage] = useState("保存固定案例后可反复执行并比较结果指纹。");
  async function load() {
    if (!campaign || identity?.role !== "kp") return;
    setCases(await requestJson(`/campaigns/${campaign.id}/simulation-cases`));
  }
  useEffect(() => { void load(); }, [campaign?.id, identity?.member_id]);
  if (!campaign || identity?.role !== "kp") return <section className="page-card"><h2>模拟团评测</h2><p>请先以 KP 身份进入团会话。</p></section>;
  async function create() {
    try {
      await requestJson(`/campaigns/${campaign!.id}/simulation-cases`, {
        method: "POST", body: JSON.stringify({ name, definition: JSON.parse(definition) })
      });
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }
  async function run(id: string) {
    const result = await requestJson<{ status: string; result_fingerprint: string }>(
      `/simulation-cases/${id}/runs`, { method: "POST" }
    );
    setMessage(`运行 ${result.status} · ${result.result_fingerprint.slice(0, 12)}`);
    await load();
  }
  return <section className="page-card simulation-workbench">
    <header className="panel-heading"><div><p className="eyebrow">真实服务 · 回滚沙盒 · 逐步轨迹</p><h2>可重放模拟团</h2></div><FlaskConical size={18} /></header>
    <p>{message}</p>
    <label>案例名称<input value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label>场景定义 JSON<textarea rows={14} value={definition} onChange={(event) => setDefinition(event.target.value)} /></label>
    <button className="primary-button" onClick={() => void create()}>保存不可变案例</button>
    <div className="simulation-case-list">{cases.map((item) => <article key={item.id}>
      <strong>{item.name}</strong>
      <small>{item.definition_hash.slice(0, 12)} · 已运行 {item.runs.length} 次</small>
      <button onClick={() => void run(item.id)}><Play size={14} />重放</button>
      {item.runs[0] && <>
        <span>{item.runs[0].status} · {item.runs[0].metrics.passed_assertions}/{item.runs[0].metrics.assertions}</span>
        {replayLogs(item.runs[0].trajectory).length > 0 && <ol className="simulation-log">
          {replayLogs(item.runs[0].trajectory).map((entry, index) => <li key={`${index}-${entry.actor}`}>
            <span className={`simulation-log-actor actor-${entry.actor || "system"}`}>
              {actorLabel[entry.actor || "system"] || entry.actor}
            </span>
            <p>{entry.content}</p>
            <small>{entry.audience === "kp" ? "仅 KP" : "桌面公开"}</small>
          </li>)}
        </ol>}
        <details>
          <summary>{item.runs[0].runner_version} · 查看逐步轨迹</summary>
          <pre>{JSON.stringify(item.runs[0].trajectory, null, 2)}</pre>
        </details>
      </>}
    </article>)}</div>
  </section>;
}
