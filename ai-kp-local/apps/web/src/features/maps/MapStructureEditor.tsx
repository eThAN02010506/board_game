import { Layers3, Save } from "lucide-react";
import { useEffect, useState } from "react";

import { requestJson } from "../../api/client";
import type { SavedMap } from "../../api/types";

type Props = { map: SavedMap | null; onSaved: () => void };

export function MapStructureEditor({ map, onSaved }: Props) {
  const [specText, setSpecText] = useState("");
  const [fogLabel, setFogLabel] = useState("未探索区域");
  const [fogRect, setFogRect] = useState("100,100,300,220");
  const [message, setMessage] = useState("地点、路线与雾区编辑会保留 revision。");

  useEffect(() => {
    setSpecText(map?.map_spec ? JSON.stringify(map.map_spec, null, 2) : "");
  }, [map?.revision_id]);

  if (!map?.map_spec || !map.revision_id) return null;

  async function saveRevision() {
    try {
      const mapSpec = JSON.parse(specText) as Record<string, unknown>;
      await requestJson(`/maps/${map!.id}/revisions`, {
        method: "POST",
        body: JSON.stringify({ expected_revision_id: map!.revision_id, map_spec: mapSpec })
      });
      setMessage("新 revision 已保存，旧 revision 保留用于重放。");
      onSaved();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function addFog() {
    const values = fogRect.split(/[,，\s]+/).map(Number);
    if (values.length !== 4 || values.some((value) => !Number.isFinite(value))) {
      setMessage("雾区矩形请输入 x, y, 宽, 高。"); return;
    }
    const [x, y, width, height] = values;
    await requestJson(`/maps/${map!.id}/fog-regions`, {
      method: "POST",
      body: JSON.stringify({
        label: fogLabel,
        polygon: [
          { x, y }, { x: x + width, y }, { x: x + width, y: y + height }, { x, y: y + height }
        ]
      })
    });
    setMessage("雾区已保存并立即遮挡玩家视图。");
    onSaved();
  }

  async function revealFog(id: string, version: number) {
    await requestJson(`/map-fog-regions/${id}/reveal`, {
      method: "POST", body: JSON.stringify({ expected_version: version })
    });
    onSaved();
  }

  return <section className="tool-panel map-structure-editor">
    <div className="panel-heading"><div><p className="eyebrow">MapSpec revision</p><h2>地点、路线与雾区</h2></div><Layers3 size={18} /></div>
    <p className="form-hint">{message}</p>
    <details><summary>编辑完整地点/路线结构</summary>
      <textarea aria-label="MapSpec JSON" rows={16} value={specText} onChange={(event) => setSpecText(event.target.value)} />
      <button className="primary-button" onClick={() => void saveRevision()}><Save size={15} />验证并保存新 revision</button>
    </details>
    <div className="fog-editor">
      <label>雾区名称<input value={fogLabel} onChange={(event) => setFogLabel(event.target.value)} /></label>
      <label>矩形 x,y,宽,高<input value={fogRect} onChange={(event) => setFogRect(event.target.value)} /></label>
      <button onClick={() => void addFog()}>添加雾区</button>
    </div>
    {map.fog_regions?.map((fog) => <div className="fog-row" key={fog.id}><span>{fog.label} · {fog.status}</span>{fog.status === "hidden" && <button onClick={() => void revealFog(fog.id, fog.version)}>揭开</button>}</div>)}
  </section>;
}
