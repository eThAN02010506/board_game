import { useEffect, useRef, useState } from "react";

import { compileScenarioContract, requestJson } from "../../api/client";
import type { ModuleEntity, ScenarioContractVersion } from "../../api/types";

type Props = {
  version: ScenarioContractVersion;
  disabled: boolean;
  onSaved: () => Promise<void>;
};

export function ScenarioEntityIdentityEditor({ version, disabled, onSaved }: Props) {
  const [sources, setSources] = useState<ModuleEntity[] | null>(null);
  const [selection, setSelection] = useState<Record<string, string>>(() =>
    Object.fromEntries(version.contract.entities.map((item) => [item.entity_id, item.module_entity_id ?? ""]))
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  async function load() {
    setBusy(true);
    try {
      const records = await requestJson<ModuleEntity[]>(`/modules/${encodeURIComponent(version.module_id)}/entities`);
      if (live.current) setSources(records);
    } catch (error) {
      if (live.current) setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (live.current) setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    try {
      const contract = {
        ...version.contract,
        entities: version.contract.entities.map((entity) => {
          const { module_entity_id: previous, ...rest } = entity;
          const selected = selection[entity.entity_id];
          return selected ? { ...rest, module_entity_id: selected } : rest;
        })
      };
      const result = await compileScenarioContract(version.module_id, contract);
      if (!live.current) return;
      setMessage(result.version ? "身份关联已保存为新草稿，仍需审核发布。" : "关联未通过编译，请检查来源与重复关联。");
      if (result.version) await onSaved();
    } catch (error) {
      if (live.current) setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (live.current) setBusy(false);
    }
  }

  return (
    <div>
      <button type="button" disabled={disabled || busy} onClick={() => void load()}>关联模组原文实体</button>
      {sources !== null && <>
        <p>选择确认为同一对象的原文实体。同名并不代表同一身份；不确定时保留未关联。</p>
        {version.contract.entities.map((entity) => <label key={entity.entity_id}>
          {entity.title}
          <select aria-label={`${entity.title} 原文身份`} disabled={disabled || busy}
            value={selection[entity.entity_id] ?? ""}
            onChange={(event) => setSelection((current) => ({ ...current, [entity.entity_id]: event.target.value }))}>
            <option value="">未关联</option>
            {sources.map((source) => <option key={source.id} value={source.id}>
              {source.name} · {source.description?.slice(0, 80) || source.entity_type} · {source.id}
            </option>)}
          </select>
          {selection[entity.entity_id] && <small>
            {sources.find((source) => source.id === selection[entity.entity_id])?.description || "暂无补充描述，请核对模组实体图中的原文依据。"}
          </small>}
        </label>)}
        <button type="button" disabled={disabled || busy} onClick={() => void save()}>保存身份关联草稿</button>
      </>}
      {message && <p role="status">{message}</p>}
    </div>
  );
}
