import { MapPinned, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  analyzeSettingProfileSettlement,
  createModuleSettingProfile,
  getRunSettingSelection,
  listModuleSettingProfiles,
  listSettingCatalogs,
  setRunSettingSelection,
  updateModuleSettingProfile
} from "../../api/client";
import type {
  ConfiguredEntityBinding,
  ConfiguredRegion,
  ConfiguredSettlement,
  ModuleRun,
  ModuleSettingAnalysis,
  ModuleSettingProfile,
  ModuleEntity,
  RegionNodeRole,
  RunSettingSelection,
  SettingCatalog
} from "../../api/types";

type Props = {
  run: ModuleRun;
  entities: Array<{
    entity_id: string;
    entity_type: ModuleEntity["entity_type"];
    name: string;
  }>;
  onRunChanged: (run: ModuleRun) => void;
};

type RegionDraft = ConfiguredRegion & { draft_id: number };

const roleLabels: Record<RegionNodeRole, string> = {
  hub: "枢纽",
  nearby: "近邻",
  distant: "远方",
  rural: "乡村"
};

const entityKindLabels = {
  npc: "人物",
  organization: "组织",
  item: "物件",
  document: "档案",
  vehicle: "交通工具",
  event: "事件",
  clue_carrier: "线索载体"
} as const;

const moduleEntityArchetypeKinds: Record<ModuleEntity["entity_type"], string[]> = {
  npc: ["npc"],
  organization: ["organization"],
  item: ["item", "document", "vehicle"],
  clue: ["clue_carrier", "document", "item"],
  event: ["event"],
  location: [],
  anchor: []
};

export function SettingProfilePanel({ run, entities, onRunChanged }: Props) {
  const [catalogs, setCatalogs] = useState<SettingCatalog[]>([]);
  const [profiles, setProfiles] = useState<ModuleSettingProfile[]>([]);
  const [selection, setSelection] = useState<RunSettingSelection | null>(null);
  const [profileId, setProfileId] = useState("");
  const [settlementId, setSettlementId] = useState("");
  const [analysis, setAnalysis] = useState<ModuleSettingAnalysis | null>(null);
  const [message, setMessage] = useState("正在读取世界模板配置……");
  const [busy, setBusy] = useState(false);
  const [createTitle, setCreateTitle] = useState("模组世界结构");
  const [packId, setPackId] = useState("");
  const [regions, setRegions] = useState<RegionDraft[]>([]);
  const [nextDraftId, setNextDraftId] = useState(1);
  const [selectionReason, setSelectionReason] = useState("当前场景位于该聚落");
  const [unassignedLocationId, setUnassignedLocationId] = useState("");
  const [showCreator, setShowCreator] = useState(false);
  const [settlementTitle, setSettlementTitle] = useState("");
  const [conditionTags, setConditionTags] = useState("");
  const [unassignedEntityId, setUnassignedEntityId] = useState("");
  const [entitySlotId, setEntitySlotId] = useState("");
  const [entityArchetypeId, setEntityArchetypeId] = useState("");

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.id === profileId) ?? null,
    [profileId, profiles]
  );
  const settlements = selectedProfile?.document.settlements ?? [];
  const selectedSettlement = settlements.find(
    (item) => item.settlement_id === settlementId
  ) ?? null;
  const selectedCatalog = catalogs.find((item) => item.setting_pack_id === packId);

  async function load() {
    const [nextCatalogs, nextProfiles, nextSelection] = await Promise.all([
      listSettingCatalogs(),
      listModuleSettingProfiles(run.module_id),
      getRunSettingSelection(run.id)
    ]);
    setCatalogs(nextCatalogs);
    setProfiles(nextProfiles);
    setSelection(nextSelection);
    setPackId((current) => current || nextCatalogs[0]?.setting_pack_id || "");
    if (nextSelection) {
      setProfileId(nextSelection.profile_id);
      setSettlementId(nextSelection.settlement_id);
      setMessage(
        `运行已固定到 ${nextSelection.profile.title} v${nextSelection.profile_version}。`
      );
    } else if (nextProfiles[0]) {
      setProfileId(nextProfiles[0].id);
      setSettlementId(nextProfiles[0].document.settlements[0]?.settlement_id ?? "");
      setMessage("已有世界结构，但当前运行尚未选择聚落。");
    } else {
      setShowCreator(true);
      setMessage("尚无世界结构；可先从一个或多个区域模式建立完整骨架。");
    }
  }

  useEffect(() => {
    void load().catch((error) => {
      setMessage(error instanceof Error ? error.message : String(error));
    });
  }, [run.id, run.module_id]);

  useEffect(() => {
    if (!packId || regions.length) return;
    const pattern = catalogs.find((item) => item.setting_pack_id === packId)
      ?.region_patterns[0]?.pattern_id;
    if (pattern) addRegion(pattern);
  }, [catalogs, packId]);

  useEffect(() => {
    setAnalysis(null);
    if (!selectedProfile || !settlementId) return;
    let active = true;
    void analyzeSettingProfileSettlement(
      selectedProfile.id,
      selectedProfile.version,
      settlementId
    ).then((result) => {
      if (active) setAnalysis(result);
    }).catch((error) => {
      if (active) setMessage(error instanceof Error ? error.message : String(error));
    });
    return () => {
      active = false;
    };
  }, [selectedProfile?.id, selectedProfile?.version, settlementId]);

  useEffect(() => {
    setSettlementTitle(selectedSettlement?.title ?? "");
    setConditionTags(selectedSettlement?.condition_tags.join(", ") ?? "");
  }, [selectedSettlement?.settlement_id, selectedSettlement?.title]);

  function addRegion(patternId?: string) {
    const draftId = nextDraftId;
    setNextDraftId((value) => value + 1);
    setRegions((items) => [
      ...items,
      {
        draft_id: draftId,
        region_id: `region-${draftId}`,
        title: `区域 ${draftId}`,
        pattern_id: patternId ?? selectedCatalog?.region_patterns[0]?.pattern_id ?? "",
        role_counts: {}
      }
    ]);
  }

  function updateRegion(draftId: number, changes: Partial<ConfiguredRegion>) {
    setRegions((items) => items.map((item) => (
      item.draft_id === draftId ? { ...item, ...changes } : item
    )));
  }

  function updateRoleCount(
    draftId: number,
    role: RegionNodeRole,
    rawValue: string
  ) {
    setRegions((items) => items.map((item) => {
      if (item.draft_id !== draftId) return item;
      const role_counts = { ...item.role_counts };
      if (rawValue) role_counts[role] = Number(rawValue);
      else delete role_counts[role];
      return { ...item, role_counts };
    }));
  }

  async function createProfile() {
    if (!packId || !regions.length) return;
    setBusy(true);
    try {
      const created = await createModuleSettingProfile(run.module_id, {
        title: createTitle,
        setting_pack_id: packId,
        regions: regions.map(({ draft_id: _draftId, ...region }) => region)
      });
      setProfiles((items) => [created, ...items]);
      setProfileId(created.id);
      setSettlementId(created.document.settlements[0]?.settlement_id ?? "");
      setShowCreator(false);
      setMessage("多区域骨架已创建；先分配模组地点，再选择本次运行聚落。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function saveSelection() {
    if (!selectedProfile || !settlementId) return;
    setBusy(true);
    try {
      const result = await setRunSettingSelection(run.id, {
        expected_run_version: run.version,
        profile_id: selectedProfile.id,
        profile_version: selectedProfile.version,
        settlement_id: settlementId,
        reason: selectionReason
      });
      setSelection(result.selection);
      onRunChanged(result.run);
      setMessage("当前运行已固定到该 Profile 版本；AI KP 会自动继承。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function replaceBindings(
    bindings: Array<{ module_entity_id: string; slot_id: string | null }>
  ) {
    await updateSelectedSettlement(
      { location_bindings: bindings },
      "地点分配已保存为新的不可变 Profile 版本；请重新固定运行选择。"
    );
  }

  async function updateSelectedSettlement(
    changes: Partial<ConfiguredSettlement>,
    successMessage: string
  ) {
    if (!selectedProfile || !selectedSettlement) return;
    const document = {
      ...selectedProfile.document,
      settlements: selectedProfile.document.settlements.map((item) => (
        item.settlement_id === selectedSettlement.settlement_id
          ? { ...item, ...changes }
          : item
      ))
    };
    await updateProfileDocument(document, successMessage);
  }

  async function updateProfileDocument(
    document: ModuleSettingProfile["document"],
    successMessage: string
  ) {
    if (!selectedProfile) return;
    setBusy(true);
    try {
      const updated = await updateModuleSettingProfile(selectedProfile.id, {
        expected_version: selectedProfile.version,
        title: selectedProfile.title,
        document
      });
      setProfiles((items) => items.map((item) => item.id === updated.id ? updated : item));
      setMessage(successMessage);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function replaceEntityBindings(bindings: ConfiguredEntityBinding[]) {
    if (!selectedProfile) return;
    await updateProfileDocument(
      { ...selectedProfile.document, entity_bindings: bindings },
      "来源实体原型绑定已保存为新的不可变 Profile 版本；请重新固定运行选择。"
    );
  }

  const locations = entities.filter((item) => item.entity_type === "location");
  const locationNames = new Map(entities.map((item) => [item.entity_id, item.name]));
  const unassignedLocations = locations.filter(
    (item) => analysis?.unassigned_location_entity_ids.includes(item.entity_id)
  );
  const unassignedEntities = entities.filter(
    (item) => analysis?.unassigned_entity_ids.includes(item.entity_id)
  );
  const sourceEntity = entities.find((item) => item.entity_id === unassignedEntityId);
  const selectedEntitySlot = analysis?.settlement_template.scene_slots.find(
    (item) => item.slot_id === entitySlotId
  );
  const allowedEntityKinds = sourceEntity
    ? moduleEntityArchetypeKinds[sourceEntity.entity_type]
    : [];
  const availableEntityArchetypes = selectedEntitySlot?.entity_archetype_candidates.filter(
    (item) => allowedEntityKinds.includes(item.entity_kind)
  ) ?? [];
  const selectedIsPinned = Boolean(
    selection
    && selectedProfile
    && selection.profile_id === selectedProfile.id
    && selection.profile_version === selectedProfile.version
    && selection.settlement_id === settlementId
  );

  return (
    <section className="director-control-card setting-profile-panel" aria-label="时代与世界结构">
      <div className="director-card-title">
        <MapPinned size={17} />
        <div>
          <strong>时代与世界结构</strong>
          <small>多区域 Profile → 模组地点分配 → 运行固定版本 → AI/真人 KP 共用</small>
        </div>
      </div>

      {!!profiles.length && !showCreator && (
        <button onClick={() => setShowCreator(true)} type="button">
          <Plus size={14} />新建另一 Profile
        </button>
      )}

      {showCreator && (
        <details open>
          <summary>建立多区域骨架</summary>
          <label>Profile 名称<input value={createTitle} onChange={(event) => setCreateTitle(event.target.value)} /></label>
          <label>
            通用时代包
            <select value={packId} onChange={(event) => {
              setPackId(event.target.value);
              setRegions([]);
            }}>
              {catalogs.map((catalog) => <option key={catalog.setting_pack_id} value={catalog.setting_pack_id}>{catalog.title} · v{catalog.pack_version}</option>)}
            </select>
          </label>
          {regions.map((region) => (
            <fieldset key={region.draft_id}>
              <legend>{region.title}</legend>
              <label>区域 ID<input value={region.region_id} onChange={(event) => updateRegion(region.draft_id, { region_id: event.target.value })} /></label>
              <label>显示名称<input value={region.title} onChange={(event) => updateRegion(region.draft_id, { title: event.target.value })} /></label>
              <label>拓扑模式<select value={region.pattern_id} onChange={(event) => updateRegion(region.draft_id, { pattern_id: event.target.value })}>{selectedCatalog?.region_patterns.map((pattern) => <option key={pattern.pattern_id} value={pattern.pattern_id}>{pattern.title}</option>)}</select></label>
              <div className="scene-field-grid">
                {(selectedCatalog?.region_patterns.find(
                  (pattern) => pattern.pattern_id === region.pattern_id
                )?.nodes ?? []).map((node) => (
                  <label key={node.node_role}>
                    {roleLabels[node.node_role]}数量
                    <input
                      min={node.minimum_count}
                      max={node.maximum_count}
                      type="number"
                      value={region.role_counts[node.node_role] ?? ""}
                      onChange={(event) => updateRoleCount(
                        region.draft_id,
                        node.node_role,
                        event.target.value
                      )}
                      placeholder={`默认 ${node.minimum_count} · ${node.settlement_kind}`}
                    />
                  </label>
                ))}
              </div>
              <button disabled={regions.length === 1} onClick={() => setRegions((items) => items.filter((item) => item.draft_id !== region.draft_id))} type="button"><Trash2 size={14} />移除区域</button>
            </fieldset>
          ))}
          <div className="button-row">
            <button onClick={() => addRegion()} type="button"><Plus size={14} />增加区域</button>
            <button disabled={busy || !regions.length} onClick={() => void createProfile()} type="button">创建完整骨架</button>
            {!!profiles.length && <button onClick={() => setShowCreator(false)} type="button">取消</button>}
          </div>
        </details>
      )}

      {!!profiles.length && (
        <>
          <div className="scene-field-grid">
            <label>世界 Profile<select aria-label="世界 Profile" value={profileId} onChange={(event) => {
              const profile = profiles.find((item) => item.id === event.target.value);
              setProfileId(event.target.value);
              setSettlementId(profile?.document.settlements[0]?.settlement_id ?? "");
            }}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.title} · v{profile.version}</option>)}</select></label>
            <label>当前聚落<select aria-label="当前聚落" value={settlementId} onChange={(event) => setSettlementId(event.target.value)}>{settlements.map((item) => <option key={item.settlement_id} value={item.settlement_id}>{item.title} · {item.settlement_kind}</option>)}</select></label>
          </div>
          <label>选择理由<input maxLength={2000} value={selectionReason} onChange={(event) => setSelectionReason(event.target.value)} /></label>
          <button disabled={busy || selectedIsPinned || !settlementId} onClick={() => void saveSelection()} type="button">固定到当前运行</button>

          {analysis && selectedSettlement && (
            <details open>
              <summary>地点与功能覆盖</summary>
              <div className="scene-field-grid">
                <label>聚落名称<input maxLength={200} value={settlementTitle} onChange={(event) => setSettlementTitle(event.target.value)} /></label>
                <label>条件标签<input value={conditionTags} onChange={(event) => setConditionTags(event.target.value)} placeholder="如 port, university, industrial" /></label>
                <label><input checked={selectedSettlement.include_typical} onChange={(event) => void updateSelectedSettlement({ include_typical: event.target.checked }, "常见功能范围已保存为新版本。") } type="checkbox" />包含常见功能</label>
                <button disabled={busy || !settlementTitle.trim()} onClick={() => void updateSelectedSettlement({
                  title: settlementTitle.trim(),
                  condition_tags: conditionTags.split(",").map((item) => item.trim()).filter(Boolean)
                }, "聚落名称与条件标签已保存为新版本。") } type="button">保存聚落属性</button>
              </div>
              <p>已覆盖：{analysis.source_coverage.covered_slot_ids.join("、") || "无"}</p>
              <p>歧义：{analysis.source_coverage.bindings.filter((item) => item.status === "ambiguous").map((item) => item.mention_title).join("、") || "无"}</p>
              <p>核心缺项候选：{analysis.source_coverage.missing_core_slot_ids.join("、") || "无"}</p>
              <details>
                <summary>按功能组合实体原型</summary>
                {analysis.settlement_template.scene_slots.filter((slot) => slot.selected).map((slot) => (
                  <div className="setting-archetype-row" key={slot.slot_id}>
                    <strong>{slot.function}</strong>
                    <small>
                      {slot.entity_archetype_candidates.map((item) => (
                        `${entityKindLabels[item.entity_kind]}：${item.label_variants.join("/")}`
                      )).join("；") || "没有通用原型"}
                    </small>
                  </div>
                ))}
                <small>这里只列候选形状；名字、关系、线索内容和状态仍需由模组证据或 KP 审批填写。</small>
              </details>
              <details>
                <summary>把模组已有实体绑定到原型</summary>
                {!!unassignedEntities.length && (
                  <div className="scene-field-grid">
                    <label>
                      来源实体
                      <select aria-label="未绑定来源实体" value={unassignedEntityId} onChange={(event) => {
                        setUnassignedEntityId(event.target.value);
                        setEntitySlotId("");
                        setEntityArchetypeId("");
                      }}>
                        <option value="">选择人物、组织、物件、事件或线索</option>
                        {unassignedEntities.map((item) => <option key={item.entity_id} value={item.entity_id}>{item.name} · {item.entity_type}</option>)}
                      </select>
                    </label>
                    <label>
                      所属功能
                      <select aria-label="来源实体功能槽" value={entitySlotId} onChange={(event) => {
                        setEntitySlotId(event.target.value);
                        setEntityArchetypeId("");
                      }}>
                        <option value="">选择当前聚落功能</option>
                        {analysis.settlement_template.scene_slots.filter((slot) => slot.selected).map((slot) => <option key={slot.slot_id} value={slot.slot_id}>{slot.function}</option>)}
                      </select>
                    </label>
                    <label>
                      实体原型
                      <select aria-label="来源实体原型" value={entityArchetypeId} onChange={(event) => setEntityArchetypeId(event.target.value)}>
                        <option value="">选择兼容原型</option>
                        {availableEntityArchetypes.map((item) => <option key={item.archetype_id} value={item.archetype_id}>{entityKindLabels[item.entity_kind]} · {item.label_variants.join("/")}</option>)}
                      </select>
                    </label>
                    <button disabled={busy || !unassignedEntityId || !entitySlotId || !entityArchetypeId} onClick={() => {
                      void replaceEntityBindings([
                        ...(selectedProfile?.document.entity_bindings ?? []),
                        {
                          module_entity_id: unassignedEntityId,
                          archetype_id: entityArchetypeId,
                          settlement_id: selectedSettlement.settlement_id,
                          slot_id: entitySlotId
                        }
                      ]);
                      setUnassignedEntityId("");
                      setEntitySlotId("");
                      setEntityArchetypeId("");
                    }} type="button">保存来源实体绑定</button>
                  </div>
                )}
                {analysis.source_entity_bindings.map((binding) => (
                  <div className="button-row" key={binding.module_entity_id}>
                    <span>{binding.entity.name} → {entityKindLabels[binding.archetype.entity_kind]}：{binding.archetype.label_variants.join("/")}</span>
                    <button disabled={busy} onClick={() => void replaceEntityBindings(
                      (selectedProfile?.document.entity_bindings ?? []).filter(
                        (item) => item.module_entity_id !== binding.module_entity_id
                      )
                    )} type="button">移除原型绑定</button>
                  </div>
                ))}
                {!unassignedEntities.length && !analysis.source_entity_bindings.length && <small>当前模组没有可绑定的来源实体。</small>}
              </details>
              {!!unassignedLocations.length && (
                <div className="button-row">
                  <select aria-label="未分配模组地点" value={unassignedLocationId} onChange={(event) => setUnassignedLocationId(event.target.value)}>
                    <option value="">选择未分配地点</option>
                    {unassignedLocations.map((item) => <option key={item.entity_id} value={item.entity_id}>{item.name}</option>)}
                  </select>
                  <button disabled={!unassignedLocationId || busy} onClick={() => {
                    void replaceBindings([...selectedSettlement.location_bindings, { module_entity_id: unassignedLocationId, slot_id: null }]);
                    setUnassignedLocationId("");
                  }} type="button">分配到当前聚落</button>
                </div>
              )}
              {selectedSettlement.location_bindings.map((binding) => (
                <div className="button-row" key={binding.module_entity_id}>
                  <span>{locationNames.get(binding.module_entity_id) ?? binding.module_entity_id}</span>
                  <select aria-label={`功能槽位 ${binding.module_entity_id}`} value={binding.slot_id ?? ""} onChange={(event) => void replaceBindings(selectedSettlement.location_bindings.map((item) => item.module_entity_id === binding.module_entity_id ? { ...item, slot_id: event.target.value || null } : item))}>
                    <option value="">自动匹配</option>
                    {analysis.settlement_template.scene_slots.map((slot) => <option key={slot.slot_id} value={slot.slot_id}>{slot.function} · {slot.slot_id}</option>)}
                  </select>
                  <button disabled={busy} onClick={() => void replaceBindings(selectedSettlement.location_bindings.filter((item) => item.module_entity_id !== binding.module_entity_id))} type="button">移除分配</button>
                </div>
              ))}
              <small>Profile 更新会产生新版本；旧运行仍固定旧版本，重新选择后 AI 才会使用新结构。</small>
            </details>
          )}
        </>
      )}
      <small>{message}</small>
    </section>
  );
}
