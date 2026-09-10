import { History, Network, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import {
  listCampaignWorldEntities,
  updateCampaignWorldEntityState
} from "../../api/client";
import type {
  CampaignWorldEntity,
  CampaignWorldEntityGraph,
  CampaignWorldEntityStateValue
} from "../../api/types";

type Props = { campaignId: string };
type ValueKind = "text" | "integer" | "boolean" | "clear";
type Draft = {
  dimension: string;
  valueKind: ValueKind;
  valueText: string;
  visibility: "table" | "kp" | "secret";
  note: string;
  idempotencyKey: string;
};

let fallbackCommandSequence = 0;

function nextIdempotencyKey(entityId: string) {
  const uuid = globalThis.crypto?.randomUUID?.();
  fallbackCommandSequence += 1;
  return `world-state:${entityId}:${uuid ?? `${Date.now()}-${fallbackCommandSequence}`}`;
}

function stateDimensions(entity: CampaignWorldEntity): string[] {
  const value = entity.data.state_dimensions;
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function defaultDraft(entity: CampaignWorldEntity): Draft {
  const dimension = stateDimensions(entity)[0] ?? "";
  return {
    dimension,
    valueKind: "text",
    valueText: "",
    visibility: entity.visibility === "table" ? "table" : entity.visibility,
    note: "",
    idempotencyKey: nextIdempotencyKey(entity.id)
  };
}

function parseValue(draft: Draft): CampaignWorldEntityStateValue {
  if (draft.valueKind === "clear") return null;
  if (draft.valueKind === "boolean") return draft.valueText === "true";
  if (draft.valueKind === "integer") {
    const value = Number(draft.valueText);
    if (!Number.isInteger(value)) throw new Error("整数状态必须填写有效整数。");
    return value;
  }
  const value = draft.valueText.trim();
  if (!value) throw new Error("文本状态不能为空。");
  return value;
}

export function WorldEntityStatePanel({ campaignId }: Props) {
  const [graph, setGraph] = useState<CampaignWorldEntityGraph | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busyEntityId, setBusyEntityId] = useState<string | null>(null);
  const [message, setMessage] = useState("正在读取长期世界实体……");
  const epochRef = useRef(0);

  async function load(silent = false) {
    const epoch = ++epochRef.current;
    if (!silent) setMessage("正在读取长期世界实体……");
    try {
      const loaded = await listCampaignWorldEntities(campaignId);
      if (epoch !== epochRef.current) return;
      setGraph(loaded);
      setDrafts((current) => Object.fromEntries(
        loaded.entities.map((entity) => [
          entity.id,
          current[entity.id] ?? defaultDraft(entity)
        ])
      ));
      if (!silent) {
        setMessage(
          loaded.entities.length
            ? `已载入 ${loaded.entities.length} 个长期实体。`
            : "玩家实际接触或 KP 确认后，类型化实体会出现在这里。"
        );
      }
    } catch (error) {
      if (epoch === epochRef.current) {
        setMessage(error instanceof Error ? error.message : String(error));
      }
    }
  }

  useEffect(() => {
    setGraph(null);
    setDrafts({});
    void load();
    return () => {
      epochRef.current += 1;
    };
  }, [campaignId]);

  function updateDraft(entityId: string, changes: Partial<Draft>) {
    setDrafts((current) => ({
      ...current,
      [entityId]: { ...current[entityId], ...changes }
    }));
  }

  async function submit(event: FormEvent, entity: CampaignWorldEntity) {
    event.preventDefault();
    const draft = drafts[entity.id];
    if (!draft?.dimension) {
      setMessage("这个实体没有可提交的原型状态维度。");
      return;
    }
    setBusyEntityId(entity.id);
    try {
      await updateCampaignWorldEntityState(campaignId, entity.id, {
        expected_version: entity.state_version,
        dimension: draft.dimension,
        value: parseValue(draft),
        visibility: draft.visibility,
        idempotency_key: draft.idempotencyKey,
        note: draft.note.trim()
      });
      updateDraft(entity.id, {
        valueText: "",
        note: "",
        idempotencyKey: nextIdempotencyKey(entity.id)
      });
      await load(true);
      setMessage(`${entity.name} 的 ${draft.dimension} 状态已提交。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyEntityId(null);
    }
  }

  return (
    <section className="investigation-ledger" aria-label="长期世界实体状态">
      <div className="director-card-title">
        <Network size={17} />
        <div>
          <strong>长期世界实体</strong>
          <small>AI KP 与真人 KP 共用同一版本、事件和可见性提交内核</small>
        </div>
      </div>
      <p className="inline-message" role="status">{message}</p>
      <div className="investigation-ledger-list">
        {(graph?.entities ?? []).map((entity) => {
          const dimensions = stateDimensions(entity);
          const draft = drafts[entity.id] ?? defaultDraft(entity);
          const visibleOptions = entity.visibility === "table"
            ? ["table", "kp", "secret"] as const
            : entity.visibility === "kp"
              ? ["kp", "secret"] as const
              : ["secret"] as const;
          const relations = (graph?.relations ?? []).filter(
            (item) => item.source_entity_id === entity.id || item.target_entity_id === entity.id
          );
          return (
            <article key={entity.id}>
              <div>
                <span>{entity.entity_kind} · v{entity.state_version}</span>
                <strong>{entity.name}</strong>
                {entity.scenario_identity && (
                  <small>已关联当前模组规则实体：{entity.scenario_identity.entity_id}</small>
                )}
                <small>{entity.description || entity.archetype_id || "无补充描述"}</small>
                {!!entity.states.length && (
                  <small>{entity.states.map((item) => (
                    `${item.dimension}=${String(item.value)} (${item.visibility})`
                  )).join(" · ")}</small>
                )}
                {!!relations.length && (
                  <small>关系：{relations.map((item) => item.relation_slot_id).join(" · ")}</small>
                )}
              </div>
              {dimensions.length ? (
                <form onSubmit={(event) => void submit(event, entity)}>
                  <label>
                    状态维度
                    <select
                      aria-label={`${entity.name} 状态维度`}
                      value={draft.dimension}
                      onChange={(event) => updateDraft(entity.id, { dimension: event.target.value })}
                    >
                      {dimensions.map((dimension) => (
                        <option key={dimension} value={dimension}>{dimension}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    值类型
                    <select
                      aria-label={`${entity.name} 值类型`}
                      value={draft.valueKind}
                      onChange={(event) => updateDraft(entity.id, {
                        valueKind: event.target.value as ValueKind,
                        valueText: event.target.value === "boolean" ? "true" : ""
                      })}
                    >
                      <option value="text">文本</option>
                      <option value="integer">整数</option>
                      <option value="boolean">布尔值</option>
                      <option value="clear">清除当前值</option>
                    </select>
                  </label>
                  {draft.valueKind !== "clear" && (
                    <label>
                      状态值
                      {draft.valueKind === "boolean" ? (
                        <select
                          aria-label={`${entity.name} 状态值`}
                          value={draft.valueText}
                          onChange={(event) => updateDraft(entity.id, { valueText: event.target.value })}
                        >
                          <option value="true">是</option>
                          <option value="false">否</option>
                        </select>
                      ) : (
                        <input
                          aria-label={`${entity.name} 状态值`}
                          inputMode={draft.valueKind === "integer" ? "numeric" : "text"}
                          value={draft.valueText}
                          onChange={(event) => updateDraft(entity.id, { valueText: event.target.value })}
                        />
                      )}
                    </label>
                  )}
                  <label>
                    可见性
                    <select
                      aria-label={`${entity.name} 状态可见性`}
                      value={draft.visibility}
                      onChange={(event) => updateDraft(entity.id, {
                        visibility: event.target.value as Draft["visibility"]
                      })}
                    >
                      {visibleOptions.map((visibility) => (
                        <option key={visibility} value={visibility}>{visibility}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    依据或备注
                    <input
                      aria-label={`${entity.name} 状态备注`}
                      value={draft.note}
                      onChange={(event) => updateDraft(entity.id, { note: event.target.value })}
                    />
                  </label>
                  <button disabled={busyEntityId === entity.id} type="submit">
                    <Save size={14} />提交状态
                  </button>
                </form>
              ) : (
                <small>该来源实体没有固定的状态维度；需先在 Setting Profile 绑定原型。</small>
              )}
            </article>
          );
        })}
      </div>
      {!!graph?.state_changes.length && (
        <details className="director-audit">
          <summary><History size={14} />实体状态审计（{graph.state_changes.length}）</summary>
          <div>
            {graph.state_changes.map((change) => (
              <p key={change.id}>
                <strong>{change.dimension}</strong>
                <span>{String(change.from_value)} → {String(change.to_value)}</span>
                <small>{change.note || change.created_at}</small>
              </p>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
