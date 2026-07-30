import {
  BookOpenCheck,
  Compass,
  GitBranch,
  History,
  Lightbulb,
  MapPin,
  Search,
  ShieldAlert
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";

import {
  analyzeModuleRunIntent,
  generateWorldExpansionProposal,
  getModuleRunDirectorState,
  isApiError,
  transitionModuleRunScene,
  updateModuleRunEntityState
} from "../../api/client";
import type {
  DirectorAnalysis,
  ModulePlayPace,
  ModuleRun,
  ModuleRunDirectorState,
  ModuleRuntimeEntityStatus,
  TurnProposal
} from "../../api/types";

type Props = {
  run: ModuleRun;
  onRunChanged: (run: ModuleRun) => void;
};

const paceOptions: Array<[ModulePlayPace, string, string]> = [
  ["freeform", "自由推进", "调查、对话、旅行等弹性时间"],
  ["structured", "结构化行动", "冲突或逐人行动，顺序很重要"],
  ["downtime", "休整", "按小时或天汇总活动"]
];

const entityStatusLabels: Record<ModuleRuntimeEntityStatus, string> = {
  hidden: "隐藏",
  available: "可接触",
  discovered: "已发现",
  resolved: "已处理"
};

const decisionLabels: Record<DirectorAnalysis["decision"], string> = {
  needs_scene: "需要先设置场景",
  answer_from_canon: "模组已有答案",
  blocked_by_spoiler: "被未来剧透阻断",
  world_gap: "可以提出世界补全"
};

export function SceneDirectorPanel({ run, onRunChanged }: Props) {
  const [directorState, setDirectorState] = useState<ModuleRunDirectorState | null>(null);
  const [analysis, setAnalysis] = useState<DirectorAnalysis | null>(null);
  const [generatedProposal, setGeneratedProposal] = useState<TurnProposal | null>(null);
  const [intent, setIntent] = useState("");
  const [sceneKey, setSceneKey] = useState(run.current_scene_key ?? "");
  const [sceneTitle, setSceneTitle] = useState(
    run.current_scene_title ?? run.current_scene_key ?? ""
  );
  const [playPace, setPlayPace] = useState<ModulePlayPace>(
    run.play_pace ?? "freeform"
  );
  const [locationId, setLocationId] = useState(run.current_location_entity_id ?? "");
  const [worldTime, setWorldTime] = useState(run.scene_started_world_time ?? "");
  const [transitionNote, setTransitionNote] = useState("");
  const [entityDrafts, setEntityDrafts] = useState<
    Record<string, ModuleRuntimeEntityStatus>
  >({});
  const [message, setMessage] = useState("正在读取场景导演状态……");
  const [busy, setBusy] = useState(false);
  const requestEpoch = useRef(0);
  const activeRun = directorState?.run ?? run;

  async function loadState(runId: string, silent = false) {
    const epoch = ++requestEpoch.current;
    if (!silent) setBusy(true);
    try {
      const loaded = await getModuleRunDirectorState(runId);
      if (epoch !== requestEpoch.current) return;
      setDirectorState(loaded);
      setEntityDrafts(Object.fromEntries(
        loaded.entity_states.map((item) => [item.entity_id, item.status])
      ));
      setMessage(
        loaded.run.current_scene_key
          ? `导演状态已同步：${loaded.run.current_scene_title ?? loaded.run.current_scene_key}。`
          : "活动模组还没有正式场景，请先建立第一个场景。"
      );
    } catch (error) {
      if (epoch === requestEpoch.current) {
        setMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (!silent && epoch === requestEpoch.current) setBusy(false);
    }
  }

  useEffect(() => {
    setDirectorState(null);
    setAnalysis(null);
    setGeneratedProposal(null);
    setSceneKey(run.current_scene_key ?? "");
    setSceneTitle(run.current_scene_title ?? run.current_scene_key ?? "");
    setPlayPace(run.play_pace ?? "freeform");
    setLocationId(run.current_location_entity_id ?? "");
    setWorldTime(run.scene_started_world_time ?? "");
    setTransitionNote("");
    void loadState(run.id);
    return () => {
      requestEpoch.current += 1;
    };
  }, [run.id]);

  const locations = useMemo(
    () => (directorState?.entity_states ?? []).filter(
      (item) => item.entity_type === "location"
    ),
    [directorState]
  );
  const investigationEntities = useMemo(
    () => (directorState?.entity_states ?? []).filter(
      (item) => item.entity_type === "clue" || item.entity_type === "anchor"
    ),
    [directorState]
  );

  async function recoverConflict() {
    await loadState(run.id, true);
    setMessage("导演状态已被其他 KP 更新；已重新载入，请确认后重试。");
  }

  async function transitionScene(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sceneKey.trim() || !sceneTitle.trim()) {
      setMessage("场景键和场景名称都不能为空。");
      return;
    }
    setBusy(true);
    try {
      const result = await transitionModuleRunScene(run.id, {
        expected_version: activeRun.version,
        scene_key: sceneKey.trim(),
        scene_title: sceneTitle.trim(),
        play_pace: playPace,
        location_entity_id: locationId || null,
        world_time: worldTime.trim() || null,
        note: transitionNote.trim()
      });
      onRunChanged(result.run);
      setTransitionNote("");
      await loadState(run.id, true);
      setMessage(`已进入场景“${result.run.current_scene_title}”，转换记录已写入审计。`);
    } catch (error) {
      if (isApiError(error, 409) && error.code === "conflict") {
        await recoverConflict();
      } else {
        setMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setBusy(false);
    }
  }

  async function saveEntityState(entityId: string) {
    const currentEntity = directorState?.entity_states.find(
      (item) => item.entity_id === entityId
    );
    const nextStatus = entityDrafts[entityId];
    if (!currentEntity || !nextStatus || nextStatus === currentEntity.status) return;
    setBusy(true);
    try {
      const result = await updateModuleRunEntityState(run.id, entityId, {
        expected_version: activeRun.version,
        status: nextStatus
      });
      onRunChanged(result.run);
      await loadState(run.id, true);
      setMessage(
        `“${currentEntity.name}”已更新为${entityStatusLabels[nextStatus]}，变更已写入审计。`
      );
    } catch (error) {
      if (isApiError(error, 409) && error.code === "conflict") {
        await recoverConflict();
      } else {
        setMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setBusy(false);
    }
  }

  async function analyzeIntent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!intent.trim()) {
      setMessage("请输入玩家准备做什么。");
      return;
    }
    setBusy(true);
    try {
      const result = await analyzeModuleRunIntent(run.id, intent.trim());
      setAnalysis(result);
      setGeneratedProposal(null);
      setMessage(`分析完成：${decisionLabels[result.decision]}。本次分析没有写入游戏状态。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function generateExpansion() {
    if (analysis?.decision !== "world_gap") return;
    setBusy(true);
    try {
      const proposal = await generateWorldExpansionProposal(run.id, {
        player_intent: analysis.player_intent
      });
      setGeneratedProposal(proposal);
      setMessage(
        proposal.status === "draft"
          ? "世界补全草稿已生成；它还不是世界事实，请前往游玩页审批。"
          : "相同场景版本已有世界补全提案，已复用原记录。"
      );
    } catch (error) {
      if (isApiError(error, 409) && error.code === "conflict") {
        await recoverConflict();
        setAnalysis(null);
      } else {
        setMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="scene-director-heading" className="scene-director">
      <header className="scene-director-header">
        <div>
          <p className="eyebrow">Scene Director · 只读分析优先</p>
          <h3 id="scene-director-heading">场景导演</h3>
          <p>先固定当前场景与可达线索，再判断应从模组回答、暂停，还是提出世界补全。</p>
        </div>
        <span className={`director-run-badge ${activeRun.status}`}>
          {activeRun.status === "active" ? "AI 可辅助" : "AI 已停止推进"}
        </span>
      </header>

      <div className="scene-director-grid">
        <form className="scene-transition-card" onSubmit={(event) => void transitionScene(event)}>
          <div className="director-card-title">
            <MapPin size={17} />
            <div><strong>当前场景</strong><small>每次保存都会追加转换记录</small></div>
          </div>
          <div className="scene-field-grid">
            <label>
              场景名称
              <input
                disabled={busy}
                maxLength={300}
                onChange={(event) => setSceneTitle(event.target.value)}
                placeholder="如：旧仓库的夜间调查"
                value={sceneTitle}
              />
            </label>
            <label>
              稳定场景键
              <input
                disabled={busy}
                maxLength={160}
                onChange={(event) => setSceneKey(event.target.value)}
                placeholder="如：warehouse-night"
                value={sceneKey}
              />
            </label>
            <label>
              推进节奏
              <select
                disabled={busy}
                onChange={(event) => setPlayPace(event.target.value as ModulePlayPace)}
                value={playPace}
              >
                {paceOptions.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
              <small>{paceOptions.find(([value]) => value === playPace)?.[2]}</small>
            </label>
            <label>
              来源地点
              <select
                disabled={busy}
                onChange={(event) => setLocationId(event.target.value)}
                value={locationId}
              >
                <option value="">不绑定地点实体</option>
                {locations.map((item) => (
                  <option key={item.entity_id} value={item.entity_id}>{item.name}</option>
                ))}
              </select>
            </label>
            <label>
              场景开始的世界时间
              <input
                disabled={busy}
                maxLength={160}
                onChange={(event) => setWorldTime(event.target.value)}
                placeholder="如：1928-10-03 21:00"
                value={worldTime}
              />
            </label>
            <label>
              转换说明
              <input
                disabled={busy}
                maxLength={2000}
                onChange={(event) => setTransitionNote(event.target.value)}
                placeholder="为什么进入该场景（可选）"
                value={transitionNote}
              />
            </label>
          </div>
          <button disabled={busy || activeRun.status !== "active"} type="submit">
            <Compass size={15} />保存并进入场景
          </button>
        </form>

        <form className="director-analysis-card" onSubmit={(event) => void analyzeIntent(event)}>
          <div className="director-card-title">
            <Search size={17} />
            <div><strong>玩家意图预检</strong><small>确定性检索，不调用模型、不写状态</small></div>
          </div>
          <label>
            玩家准备做什么？
            <textarea
              disabled={busy}
              maxLength={1000}
              onChange={(event) => setIntent(event.target.value)}
              placeholder="例如：我想找镇上行业内打过交道的警察。"
              rows={4}
              value={intent}
            />
          </label>
          <button disabled={busy} type="submit">
            <Search size={15} />分析现有答案与世界缺口
          </button>
          {analysis && (
            <section aria-label="导演分析结果" className={`director-analysis-result ${analysis.decision}`}>
              <header>
                {analysis.decision === "answer_from_canon"
                  ? <BookOpenCheck size={18} />
                  : <ShieldAlert size={18} />}
                <div>
                  <strong>{decisionLabels[analysis.decision]}</strong>
                  <small>确认：零状态写入</small>
                </div>
              </header>
              <ul>
                {analysis.reasons.map((reason) => <li key={reason}>{reason}</li>)}
              </ul>
              {!!analysis.sources.length && (
                <details>
                  <summary>查看当前允许的来源（{analysis.sources.length}）</summary>
                  {analysis.sources.map((source) => (
                    <blockquote key={`${source.source_type}:${source.source_id}`}>
                      <strong>{source.title}</strong>
                      <span>{source.text}</span>
                      <small>{source.source_locator ?? source.source_id}</small>
                    </blockquote>
                  ))}
                </details>
              )}
              {analysis.decision === "world_gap" && (
                <div className="world-expansion-action">
                  <button
                    disabled={busy}
                    onClick={() => void generateExpansion()}
                    type="button"
                  >
                    <Lightbulb size={15} />
                    生成可审批的世界补全草稿
                  </button>
                  <small>会调用当前模型；批准前不会写入世界、NPC、地图或记忆。</small>
                </div>
              )}
              {generatedProposal?.world_expansion && (
                <article className="world-expansion-preview">
                  <span>待审批 · {generatedProposal.world_expansion.candidate.confidence}</span>
                  <strong>{generatedProposal.world_expansion.candidate.subject}</strong>
                  <p>{generatedProposal.world_expansion.candidate.proposal}</p>
                  <a href="/play">前往游玩页审批</a>
                </article>
              )}
            </section>
          )}
        </form>
      </div>

      <section className="investigation-ledger">
        <div className="director-card-title">
          <GitBranch size={17} />
          <div>
            <strong>调查账本</strong>
            <small>状态属于本次跑团，不修改模组原文</small>
          </div>
        </div>
        {investigationEntities.length ? (
          <div className="investigation-ledger-list">
            {investigationEntities.map((entity) => {
              const selected = entityDrafts[entity.entity_id] ?? entity.status;
              return (
                <article key={entity.entity_id}>
                  <div>
                    <span>{entity.entity_type === "anchor" ? "剧情锚点" : "线索"}</span>
                    <strong>{entity.name}</strong>
                    <small>{entity.description || "没有补充说明"}</small>
                  </div>
                  <label>
                    运行状态
                    <select
                      aria-label={`${entity.name}的运行状态`}
                      disabled={busy}
                      onChange={(event) => setEntityDrafts((current) => ({
                        ...current,
                        [entity.entity_id]: event.target.value as ModuleRuntimeEntityStatus
                      }))}
                      value={selected}
                    >
                      {Object.entries(entityStatusLabels).map(([value, label]) => (
                        <option key={value} value={value}>{label}</option>
                      ))}
                    </select>
                  </label>
                  <button
                    disabled={busy || selected === entity.status}
                    onClick={() => void saveEntityState(entity.entity_id)}
                    type="button"
                  >
                    保存
                  </button>
                </article>
              );
            })}
          </div>
        ) : (
          <p className="empty-copy">
            暂无经过来源审核的线索或剧情锚点。请先在下方“实体与关系”工作台整理模组知识。
          </p>
        )}
      </section>

      <p className="inline-message" role="status">{message}</p>

      <details className="director-audit">
        <summary><History size={14} />导演审计（场景 {directorState?.scene_events.length ?? 0} · 状态 {
          directorState?.entity_state_events.length ?? 0
        }）</summary>
        <div>
          {(directorState?.scene_events ?? []).map((event) => (
            <p key={event.id}>
              <strong>{event.to_scene_title}</strong>
              <span>{event.to_play_pace} · {event.world_time || "未记录世界时间"}</span>
              <small>{event.note || event.created_at}</small>
            </p>
          ))}
          {(directorState?.entity_state_events ?? []).map((event) => (
            <p key={event.id}>
              <strong>{event.entity_name}</strong>
              <span>{entityStatusLabels[event.from_status]} → {entityStatusLabels[event.to_status]}</span>
              <small>{event.note || event.created_at}</small>
            </p>
          ))}
          {!directorState?.scene_events.length && !directorState?.entity_state_events.length && (
            <p className="empty-copy">还没有导演变更记录。</p>
          )}
        </div>
      </details>
    </section>
  );
}
