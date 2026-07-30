import { FlaskConical, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { requestJson } from "../../api/client";
import type { AuthIdentity, Campaign } from "../../api/types";

type Props = { campaign: Campaign | null; identity: AuthIdentity | null };
type SimulationCase = {
  id: string; name: string; definition_hash: string;
  runs: Array<{ id: string; status: string; metrics: Record<string, number>; result_fingerprint: string }>;
};

const sample = {
  steps: [
    { action: "emit", audience: "kp", content: "地下室藏有仪式书" },
    { action: "assert_player_not_sees", text: "仪式书" },
    { action: "reveal", text: "仪式书" },
    { action: "assert_player_sees", text: "仪式书" }
  ]
};

export function SimulationWorkbench({ campaign, identity }: Props) {
  const [cases, setCases] = useState<SimulationCase[]>([]);
  const [name, setName] = useState("线索揭示基础回归");
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
    <header className="panel-heading"><div><p className="eyebrow">固定数据集 · 轨迹 · 指纹</p><h2>可重放模拟团</h2></div><FlaskConical size={18} /></header>
    <p>{message}</p>
    <label>案例名称<input value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label>场景定义 JSON<textarea rows={14} value={definition} onChange={(event) => setDefinition(event.target.value)} /></label>
    <button className="primary-button" onClick={() => void create()}>保存不可变案例</button>
    <div className="simulation-case-list">{cases.map((item) => <article key={item.id}><strong>{item.name}</strong><small>{item.definition_hash.slice(0, 12)} · 已运行 {item.runs.length} 次</small><button onClick={() => void run(item.id)}><Play size={14} />重放</button>{item.runs[0] && <span>{item.runs[0].status} · {item.runs[0].metrics.passed_assertions}/{item.runs[0].metrics.assertions}</span>}</article>)}</div>
  </section>;
}
