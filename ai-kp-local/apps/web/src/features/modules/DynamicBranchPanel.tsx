import { CheckCircle2, GitBranch, PauseCircle, RefreshCw, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  abandonDynamicBranch,
  listDynamicBranches,
  resolveDynamicBranchBeat,
  resumeDynamicBranch
} from "../../api/client";
import type { DynamicBranchRun } from "../../api/types";

type Props = {
  campaignId: string;
  moduleRunId: string;
};

const statusLabels: Record<DynamicBranchRun["status"], string> = {
  approved: "待实际接触",
  active: "进行中",
  paused: "等待 KP",
  completed: "已完成",
  abandoned: "已放弃"
};

function commandId(prefix: string) {
  return `${prefix}:${crypto.randomUUID()}`;
}

export function DynamicBranchPanel({ campaignId, moduleRunId }: Props) {
  const [branches, setBranches] = useState<DynamicBranchRun[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [note, setNote] = useState("");
  const [observedEffect, setObservedEffect] = useState("");
  const [message, setMessage] = useState("正在读取动态支线……");
  const [busy, setBusy] = useState(false);
  const visibleBranches = useMemo(
    () => branches.filter((branch) => branch.module_run_id === moduleRunId),
    [branches, moduleRunId]
  );
  const selected = visibleBranches.find((branch) => branch.id === selectedId)
    ?? visibleBranches[0]
    ?? null;
  const currentBeat = selected?.plan.beats[selected.current_beat_index] ?? null;

  async function load(silent = false) {
    if (!silent) setBusy(true);
    try {
      const loaded = await listDynamicBranches(campaignId);
      setBranches(loaded);
      setSelectedId((current) =>
        loaded.some((branch) => branch.id === current)
          ? current
          : loaded.find((branch) => branch.module_run_id === moduleRunId)?.id ?? ""
      );
      setMessage(loaded.length ? "支线进度已同步。" : "当前团还没有已批准的动态支线。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (!silent) setBusy(false);
    }
  }

  useEffect(() => {
    void load();
  }, [campaignId, moduleRunId]);

  async function resolve(outcome: "succeeded" | "failed" | "skipped") {
    if (!selected) return;
    setBusy(true);
    try {
      const result = await resolveDynamicBranchBeat(selected.id, {
        expected_version: selected.version,
        command_id: commandId("branch-beat"),
        outcome,
        note,
        observed_effects: observedEffect.trim() ? [observedEffect.trim()] : []
      });
      setBranches((current) => current.map((item) =>
        item.id === result.branch.id ? result.branch : item
      ));
      setNote("");
      setObservedEffect("");
      setMessage(
        result.branch.status === "completed"
          ? "支线已完成；候选效果仍需通过事实落地接口确认。"
          : result.branch.status === "paused"
          ? "支线已安全暂停，等待人类 KP 判断。"
          : "节拍已记录；没有自动写入世界事实。"
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function resume() {
    if (!selected || !note.trim()) return;
    setBusy(true);
    try {
      const result = await resumeDynamicBranch(selected.id, {
        expected_version: selected.version,
        command_id: commandId("branch-resume"),
        note
      });
      setBranches((current) => current.map((item) =>
        item.id === result.branch.id ? result.branch : item
      ));
      setNote("");
      setMessage("人类 KP 已确认恢复支线。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function abandon() {
    if (!selected || !note.trim()) return;
    setBusy(true);
    try {
      const result = await abandonDynamicBranch(selected.id, {
        expected_version: selected.version,
        command_id: commandId("branch-abandon"),
        note
      });
      setBranches((current) => current.map((item) =>
        item.id === result.branch.id ? result.branch : item
      ));
      setNote("");
      setMessage("支线已保留审计记录并终止。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="dynamic-branch-panel" aria-label="动态支线工作台">
      <header>
        <div>
          <GitBranch size={18} />
          <span>
            <strong>动态支线</strong>
            <small>因果节拍与世界事实分离记录</small>
          </span>
        </div>
        <button disabled={busy} onClick={() => void load()} type="button">
          <RefreshCw size={14} />刷新
        </button>
      </header>
      {visibleBranches.length ? (
        <>
          <nav aria-label="支线列表">
            {visibleBranches.map((branch) => (
              <button
                className={branch.id === selected?.id ? "selected" : ""}
                key={branch.id}
                onClick={() => setSelectedId(branch.id)}
                type="button"
              >
                <span>{statusLabels[branch.status]}</span>
                <strong>{branch.plan.goal}</strong>
                <small>
                  {Math.min(branch.current_beat_index + 1, branch.plan.beats.length)}
                  /{branch.plan.beats.length} 节拍
                </small>
              </button>
            ))}
          </nav>
          {selected && (
            <article className="dynamic-branch-detail">
              <div className="branch-progress">
                {selected.plan.beats.map((beat, index) => (
                  <span
                    className={
                      index < selected.current_beat_index
                        ? "done"
                        : index === selected.current_beat_index
                        ? "current"
                        : ""
                    }
                    key={beat.beat_id}
                    title={beat.title}
                  />
                ))}
              </div>
              <div className="branch-status-line">
                <span className={`branch-status ${selected.status}`}>
                  {statusLabels[selected.status]}
                </span>
                <small>v{selected.version} · {selected.events.length} 条审计事件</small>
              </div>
              {currentBeat ? (
                <>
                  <h4>{currentBeat.title}</h4>
                  <p>{currentBeat.action}</p>
                  <small>角色意图：{currentBeat.character_intent}</small>
                  <details>
                    <summary>候选效果（不会自动落库）</summary>
                    <ul>
                      {currentBeat.expected_effects.map((effect) => (
                        <li key={`${effect.effect_type}:${effect.description}`}>
                          {effect.description}
                        </li>
                      ))}
                    </ul>
                  </details>
                </>
              ) : (
                <p>这条支线没有待处理节拍。</p>
              )}
              {selected.status === "active" && currentBeat && (
                <div className="branch-command-form">
                  <label>
                    实际观察
                    <input
                      maxLength={1000}
                      onChange={(event) => setObservedEffect(event.target.value)}
                      placeholder="只记录桌面上真实发生的结果"
                      value={observedEffect}
                    />
                  </label>
                  <label>
                    KP 备注
                    <textarea
                      maxLength={2000}
                      onChange={(event) => setNote(event.target.value)}
                      value={note}
                    />
                  </label>
                  <div>
                    <button disabled={busy} onClick={() => void resolve("succeeded")} type="button">
                      <CheckCircle2 size={14} />成功
                    </button>
                    <button disabled={busy} onClick={() => void resolve("failed")} type="button">
                      <PauseCircle size={14} />失败
                    </button>
                    <button disabled={busy} onClick={() => void resolve("skipped")} type="button">
                      跳过
                    </button>
                  </div>
                </div>
              )}
              {selected.status === "paused" && (
                <button disabled={busy || !note.trim()} onClick={() => void resume()} type="button">
                  恢复支线
                </button>
              )}
              {!["completed", "abandoned"].includes(selected.status) && (
                <button
                  className="danger-button"
                  disabled={busy || !note.trim()}
                  onClick={() => void abandon()}
                  type="button"
                >
                  <XCircle size={14} />终止并保留记录
                </button>
              )}
            </article>
          )}
        </>
      ) : (
        <p>批准包含结构化计划的世界补全后，支线会出现在这里；玩家实际接触前不会启动。</p>
      )}
      <output>{message}</output>
    </section>
  );
}
