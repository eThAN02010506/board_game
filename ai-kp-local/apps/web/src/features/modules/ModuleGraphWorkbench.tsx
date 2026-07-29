import { GitBranch, Link2, Network } from "lucide-react";
import { FormEvent, useLayoutEffect, useMemo, useRef, useState } from "react";

import { requestJson } from "../../api/client";
import type {
  ModuleEntity,
  ModuleEntityRelation,
  ModuleKnowledgeCandidate,
  ModuleReachabilityReport
} from "../../api/types";

type Props = {
  moduleId: string;
  candidates: ModuleKnowledgeCandidate[];
  onMessage: (message: string) => void;
};

const entityTypes = [
  ["npc", "NPC"],
  ["location", "地点"],
  ["clue", "线索"],
  ["organization", "组织"],
  ["item", "物品"],
  ["event", "事件"],
  ["anchor", "剧情锚点"]
] as const;

const predicates = [
  ["leads_to", "通向"],
  ["reveals", "揭示"],
  ["provides_access_to", "提供访问"],
  ["contains", "包含"],
  ["located_at", "位于"],
  ["knows", "认识"],
  ["owns", "拥有"],
  ["member_of", "属于组织"],
  ["involves", "涉及"],
  ["blocks", "阻断"],
  ["contradicts", "冲突"],
  ["same_as", "同一实体"]
] as const;

export function ModuleGraphWorkbench({ moduleId, candidates, onMessage }: Props) {
  const [graph, setGraph] = useState<{
    moduleId: string;
    entities: ModuleEntity[];
    relations: ModuleEntityRelation[];
  }>({ moduleId: "", entities: [], relations: [] });
  const [entryIds, setEntryIds] = useState<string[]>([]);
  const [scopedReport, setScopedReport] = useState<{
    moduleId: string;
    value: ModuleReachabilityReport;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [entityType, setEntityType] = useState("npc");
  const currentModuleIdRef = useRef(moduleId);
  const moduleScopeEpochRef = useRef(0);
  const graphRequestEpochRef = useRef(0);
  const reachabilityRequestEpochRef = useRef(0);
  const operationEpochRef = useRef(0);
  const entities = graph.moduleId === moduleId ? graph.entities : [];
  const relations = graph.moduleId === moduleId ? graph.relations : [];
  const report = scopedReport?.moduleId === moduleId ? scopedReport.value : null;
  const approved = useMemo(
    () => candidates.filter((candidate) => candidate.status === "approved"),
    [candidates]
  );
  const entitySources = entityType === "anchor"
    ? approved.filter((candidate) => candidate.kind === "module_anchor")
    : approved;

  function isCurrentModule(requestedModuleId: string, scopeEpoch: number) {
    return (
      currentModuleIdRef.current === requestedModuleId &&
      moduleScopeEpochRef.current === scopeEpoch
    );
  }

  async function loadGraph(requestedModuleId: string, scopeEpoch: number) {
    if (!requestedModuleId || !isCurrentModule(requestedModuleId, scopeEpoch)) {
      return false;
    }
    const requestEpoch = ++graphRequestEpochRef.current;
    const [loadedEntities, loadedRelations] = await Promise.all([
      requestJson<ModuleEntity[]>(`/modules/${requestedModuleId}/entities`),
      requestJson<ModuleEntityRelation[]>(`/modules/${requestedModuleId}/relations`)
    ]);
    if (
      !isCurrentModule(requestedModuleId, scopeEpoch) ||
      requestEpoch !== graphRequestEpochRef.current
    ) {
      return false;
    }
    setGraph({
      moduleId: requestedModuleId,
      entities: loadedEntities,
      relations: loadedRelations
    });
    setEntryIds((current) => current.filter((id) => loadedEntities.some((item) => item.id === id)));
    return true;
  }

  useLayoutEffect(() => {
    currentModuleIdRef.current = moduleId;
    const scopeEpoch = ++moduleScopeEpochRef.current;
    graphRequestEpochRef.current += 1;
    reachabilityRequestEpochRef.current += 1;
    operationEpochRef.current += 1;
    setGraph({ moduleId, entities: [], relations: [] });
    setEntryIds([]);
    setScopedReport(null);
    setBusy(false);
    if (moduleId) {
      void loadGraph(moduleId, scopeEpoch).catch((error) => {
        if (isCurrentModule(moduleId, scopeEpoch)) {
          onMessage(error instanceof Error ? error.message : String(error));
        }
      });
    }
    return () => {
      if (isCurrentModule(moduleId, scopeEpoch)) {
        moduleScopeEpochRef.current += 1;
        graphRequestEpochRef.current += 1;
        reachabilityRequestEpochRef.current += 1;
        operationEpochRef.current += 1;
      }
    };
  }, [moduleId]);

  async function createEntity(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const requestedModuleId = moduleId;
    const scopeEpoch = moduleScopeEpochRef.current;
    if (!requestedModuleId || !isCurrentModule(requestedModuleId, scopeEpoch)) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const operationEpoch = ++operationEpochRef.current;
    setBusy(true);
    try {
      await requestJson(`/modules/${requestedModuleId}/entities`, {
        method: "POST",
        body: JSON.stringify({
          entity_type: data.get("entity_type"),
          name: data.get("name"),
          description: data.get("description"),
          source_candidate_id: data.get("source_candidate_id"),
          visibility: "kp"
        })
      });
      if (
        !isCurrentModule(requestedModuleId, scopeEpoch) ||
        operationEpoch !== operationEpochRef.current
      ) return;
      form.reset();
      setEntityType("npc");
      setScopedReport(null);
      await loadGraph(requestedModuleId, scopeEpoch);
      if (
        isCurrentModule(requestedModuleId, scopeEpoch) &&
        operationEpoch === operationEpochRef.current
      ) {
        onMessage("实体已保存，并保留已批准知识候选作为来源。");
      }
    } catch (error) {
      if (
        isCurrentModule(requestedModuleId, scopeEpoch) &&
        operationEpoch === operationEpochRef.current
      ) {
        onMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (
        isCurrentModule(requestedModuleId, scopeEpoch) &&
        operationEpoch === operationEpochRef.current
      ) {
        setBusy(false);
      }
    }
  }

  async function createRelation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const requestedModuleId = moduleId;
    const scopeEpoch = moduleScopeEpochRef.current;
    if (!requestedModuleId || !isCurrentModule(requestedModuleId, scopeEpoch)) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const operationEpoch = ++operationEpochRef.current;
    setBusy(true);
    try {
      await requestJson(`/modules/${requestedModuleId}/relations`, {
        method: "POST",
        body: JSON.stringify({
          source_entity_id: data.get("source_entity_id"),
          predicate: data.get("predicate"),
          target_entity_id: data.get("target_entity_id"),
          source_candidate_id: data.get("source_candidate_id"),
          note: data.get("note"),
          confidence: 1,
          visibility: "kp"
        })
      });
      if (
        !isCurrentModule(requestedModuleId, scopeEpoch) ||
        operationEpoch !== operationEpochRef.current
      ) return;
      form.reset();
      setScopedReport(null);
      await loadGraph(requestedModuleId, scopeEpoch);
      if (
        isCurrentModule(requestedModuleId, scopeEpoch) &&
        operationEpoch === operationEpochRef.current
      ) {
        onMessage("关系已保存。");
      }
    } catch (error) {
      if (
        isCurrentModule(requestedModuleId, scopeEpoch) &&
        operationEpoch === operationEpochRef.current
      ) {
        onMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (
        isCurrentModule(requestedModuleId, scopeEpoch) &&
        operationEpoch === operationEpochRef.current
      ) {
        setBusy(false);
      }
    }
  }

  async function checkReachability() {
    if (!entryIds.length) {
      onMessage("请至少选择一个当前可进入的地点、人物或线索。");
      return;
    }
    const requestedModuleId = moduleId;
    const scopeEpoch = moduleScopeEpochRef.current;
    if (!requestedModuleId || !isCurrentModule(requestedModuleId, scopeEpoch)) return;
    const requestEpoch = ++reachabilityRequestEpochRef.current;
    const operationEpoch = ++operationEpochRef.current;
    const requestedEntryIds = [...entryIds];
    setScopedReport(null);
    setBusy(true);
    try {
      const loadedReport = await requestJson<ModuleReachabilityReport>(
        `/modules/${requestedModuleId}/graph/reachability`,
        {
          method: "POST",
          body: JSON.stringify({ entry_entity_ids: requestedEntryIds })
        }
      );
      if (
        isCurrentModule(requestedModuleId, scopeEpoch) &&
        requestEpoch === reachabilityRequestEpochRef.current &&
        operationEpoch === operationEpochRef.current
      ) {
        setScopedReport({ moduleId: requestedModuleId, value: loadedReport });
      }
    } catch (error) {
      if (
        isCurrentModule(requestedModuleId, scopeEpoch) &&
        requestEpoch === reachabilityRequestEpochRef.current &&
        operationEpoch === operationEpochRef.current
      ) {
        onMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (
        isCurrentModule(requestedModuleId, scopeEpoch) &&
        operationEpoch === operationEpochRef.current
      ) {
        setBusy(false);
      }
    }
  }

  if (!moduleId) return null;

  return (
    <section className="page-card module-graph-card">
      <div className="page-intro">
        <div><p className="eyebrow">来源图与确定性遍历</p><h2>实体关系与锚点可达性</h2></div>
        <Network size={24} />
      </div>
      {!approved.length && (
        <p className="permission-hint">先在上方批准至少一条模组知识候选，才能建立实体与关系。</p>
      )}
      <div className="module-graph-forms">
        <form onSubmit={(event) => void createEntity(event)}>
          <h3>新增实体</h3>
          <select
            disabled={!approved.length || busy}
            name="entity_type"
            onChange={(event) => setEntityType(event.target.value)}
            required
            value={entityType}
          >
            {entityTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <input disabled={!approved.length || busy} name="name" placeholder="名称" required />
          <input disabled={!approved.length || busy} name="description" placeholder="简短说明" />
          <select disabled={!entitySources.length || busy} name="source_candidate_id" required>
            <option value="">选择已批准来源</option>
            {entitySources.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
          </select>
          <button disabled={!entitySources.length || busy} type="submit">保存实体</button>
        </form>
        <form onSubmit={(event) => void createRelation(event)}>
          <h3>新增关系</h3>
          <select disabled={entities.length < 2 || busy} name="source_entity_id" required>
            <option value="">起点实体</option>
            {entities.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          <select disabled={entities.length < 2 || busy} name="predicate" required>
            {predicates.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <select disabled={entities.length < 2 || busy} name="target_entity_id" required>
            <option value="">终点实体</option>
            {entities.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          <select disabled={!approved.length || busy} name="source_candidate_id" required>
            <option value="">选择已批准来源</option>
            {approved.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
          </select>
          <input name="note" placeholder="关系说明（可选）" />
          <button disabled={entities.length < 2 || busy} type="submit">保存关系</button>
        </form>
      </div>
      <div className="module-entity-list">
        {entities.map((entity) => (
          <label className={entity.entity_type} key={entity.id}>
            <input
              checked={entryIds.includes(entity.id)}
              onChange={(event) => setEntryIds((current) =>
                event.target.checked
                  ? [...current, entity.id]
                  : current.filter((id) => id !== entity.id)
              )}
              type="checkbox"
            />
            <span><strong>{entity.name}</strong><small>{entity.entity_type}</small></span>
          </label>
        ))}
      </div>
      <div className="module-relation-list">
        {relations.map((relation) => (
          <p key={relation.id}><Link2 size={13} />{relation.source_name} <b>{relation.predicate}</b> {relation.target_name}</p>
        ))}
      </div>
      <button disabled={!entities.length || busy} onClick={() => void checkReachability()} type="button">
        <GitBranch size={14} />从已选入口检查剧情锚点
      </button>
      {report && (
        <div className={`module-reachability-report ${report.safe ? "ok" : "warning"}`}>
          <strong>
            {report.safe
              ? "所有剧情锚点可达且没有显式冲突"
              : report.all_anchors_reachable
                ? "锚点可达，但存在显式冲突"
                : "存在不可达剧情锚点"}
          </strong>
          {report.anchors.map((anchor) => (
            <span key={anchor.id}>{anchor.reachable ? "✓" : "×"} {anchor.name}</span>
          ))}
          {report.conflicts.map((conflict) => (
            <span className="conflict" key={conflict.id}>
              冲突：{conflict.source_name} {conflict.predicate} {conflict.target_name}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
