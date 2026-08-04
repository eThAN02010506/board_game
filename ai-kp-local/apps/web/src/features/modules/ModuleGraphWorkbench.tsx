import { GitBranch, Import, Link2, Network } from "lucide-react";
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
  campaignId?: string;
  candidates: ModuleKnowledgeCandidate[];
  onMessage: (message: string) => void;
};

type ImportPreview = {
  module_id: string;
  module_title: string;
  campaign_id: string;
  locations: Array<{ module_entity_id: string; name: string; source_candidate_id: string }>;
  routes: Array<{
    module_relation_id: string;
    from_name: string;
    to_name: string;
    predicate: string;
    default_minutes: number;
  }>;
  npc_profiles: Array<{
    npc_entity_id: string;
    npc_name: string;
    location_name: string;
    linked_to_campaign: boolean;
  }>;
  time_constraints: Array<{ candidate_id: string; statement: string; years: number[] }>;
  has_unlinked_npcs: boolean;
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

export function ModuleGraphWorkbench({ moduleId, campaignId, candidates, onMessage }: Props) {
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
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{
    created_locations: string[];
    merged_locations: string[];
    created_routes: string[];
    skipped_routes: string[];
    updated_npc_profiles: string[];
    skipped_npcs: string[];
  } | null>(null);
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

  async function loadImportPreview() {
    if (!campaignId) {
      onMessage("缺少团上下文，无法预览导入。");
      return;
    }
    const requestedModuleId = moduleId;
    const scopeEpoch = moduleScopeEpochRef.current;
    if (!isCurrentModule(requestedModuleId, scopeEpoch)) return;
    setImportResult(null);
    setImporting(true);
    try {
      const preview = await requestJson<ImportPreview>(
        `/campaigns/${encodeURIComponent(campaignId)}/modules/${requestedModuleId}/import-preview`
      );
      if (isCurrentModule(requestedModuleId, scopeEpoch)) {
        setImportPreview(preview);
      }
    } catch (error) {
      if (isCurrentModule(requestedModuleId, scopeEpoch)) {
        onMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (isCurrentModule(requestedModuleId, scopeEpoch)) {
        setImporting(false);
      }
    }
  }

  async function confirmImport() {
    if (!campaignId || !importPreview) return;
    const requestedModuleId = moduleId;
    const scopeEpoch = moduleScopeEpochRef.current;
    if (!isCurrentModule(requestedModuleId, scopeEpoch)) return;
    setImporting(true);
    try {
      const result = await requestJson<{
        import_id: string;
        created_locations: string[];
        merged_locations: string[];
        created_routes: string[];
        skipped_routes: string[];
        updated_npc_profiles: string[];
        skipped_npcs: string[];
      }>(
        `/campaigns/${encodeURIComponent(campaignId)}/modules/${requestedModuleId}/import`,
        {
          method: "POST",
          body: JSON.stringify({
            locations: importPreview.locations.map((item) => ({
              module_entity_id: item.module_entity_id,
              include: true
            })),
            routes: importPreview.routes.map((item) => ({
              module_relation_id: item.module_relation_id,
              include: true,
              travel_minutes: item.default_minutes
            })),
            npcs: importPreview.npc_profiles
              .filter((item) => item.linked_to_campaign)
              .map((item) => ({
                module_entity_id: item.npc_entity_id,
                include: true
              })),
            apply_time_constraints: true
          })
        }
      );
      if (isCurrentModule(requestedModuleId, scopeEpoch)) {
        setImportResult(result);
        setImportPreview(null);
        onMessage("已把已审核模组实体导入团级图谱与 NPC 档案。");
      }
    } catch (error) {
      if (isCurrentModule(requestedModuleId, scopeEpoch)) {
        onMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (isCurrentModule(requestedModuleId, scopeEpoch)) {
        setImporting(false);
      }
    }
  }

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
      {campaignId && (
        <div className="module-import-panel">
          <div className="module-import-heading">
            <div>
              <p className="eyebrow">来源约束导入</p>
              <h3>导入已审核实体到团级图谱</h3>
              <small>只接受已批准来源；未关联本团的 NPC 会被跳过，绝不自动建号。</small>
            </div>
            <Import size={20} />
          </div>
          <button
            disabled={importing || !approved.length}
            onClick={() => void loadImportPreview()}
            type="button"
          >
            {importing ? "处理中…" : "预览可导入项"}
          </button>
          {importPreview && (
            <div className="module-import-preview">
              <p className="module-import-summary">
                共 {importPreview.locations.length} 个地点、{importPreview.routes.length} 条路线、
                {importPreview.npc_profiles.length} 条 NPC 关联
                {importPreview.has_unlinked_npcs && "（含未关联 NPC）"}。
              </p>
              {importPreview.locations.length > 0 && (
                <div className="module-import-group">
                  <strong>地点</strong>
                  <ul>
                    {importPreview.locations.map((item) => (
                      <li key={item.module_entity_id}>{item.name}</li>
                    ))}
                  </ul>
                </div>
              )}
              {importPreview.routes.length > 0 && (
                <div className="module-import-group">
                  <strong>路线（默认时间，可后续调整）</strong>
                  <ul>
                    {importPreview.routes.map((item) => (
                      <li key={item.module_relation_id}>
                        {item.from_name} → {item.to_name}（约 {item.default_minutes} 分钟）
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {importPreview.npc_profiles.length > 0 && (
                <div className="module-import-group">
                  <strong>NPC 档案</strong>
                  <ul>
                    {importPreview.npc_profiles.map((item) => (
                      <li key={item.npc_entity_id}>
                        {item.npc_name} @ {item.location_name}
                        {!item.linked_to_campaign && (
                          <small className="module-import-warn">（未关联本团，将跳过）</small>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {importPreview.time_constraints.length > 0 && (
                <div className="module-import-group">
                  <strong>年代建议（写入需 KP 复核年份）</strong>
                  <ul>
                    {importPreview.time_constraints.map((item) => (
                      <li key={item.candidate_id}>
                        {item.statement}（{item.years.join("、")}）
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <button
                className="primary-button"
                disabled={importing}
                onClick={() => void confirmImport()}
                type="button"
              >
                确认导入已审核实体
              </button>
            </div>
          )}
          {importResult && (
            <div className="module-import-result">
              <p>
                新增地点 {importResult.created_locations.length} 个、合并 {importResult.merged_locations.length} 个；
                新增路线 {importResult.created_routes.length} 条、跳过 {importResult.skipped_routes.length} 条；
                更新 NPC 档案 {importResult.updated_npc_profiles.length} 条、跳过未关联 {importResult.skipped_npcs.length} 个。
              </p>
              {importResult.skipped_npcs.length > 0 && (
                <small className="module-import-warn">
                  未关联 NPC：{importResult.skipped_npcs.join("、")}
                </small>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
