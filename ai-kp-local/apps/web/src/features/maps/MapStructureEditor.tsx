import { Layers3, Save } from "lucide-react";
import { useEffect, useState } from "react";

import { requestJson } from "../../api/client";
import type { SavedMap } from "../../api/types";
import { FogOverlayEditor } from "./FogOverlayEditor";

type Props = { map: SavedMap | null; onSaved: () => void };

export function MapStructureEditor({ map, onSaved }: Props) {
  const [specText, setSpecText] = useState("");
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

  return <section className="tool-panel map-structure-editor">
    <div className="panel-heading"><div><p className="eyebrow">MapSpec revision</p><h2>地点、路线与雾区</h2></div><Layers3 size={18} /></div>
    <p className="form-hint">{message}</p>
    <details><summary>编辑完整地点/路线结构</summary>
      <textarea aria-label="MapSpec JSON" rows={16} value={specText} onChange={(event) => setSpecText(event.target.value)} />
      <button className="primary-button" onClick={() => void saveRevision()}><Save size={15} />验证并保存新 revision</button>
    </details>
    <FogOverlayEditor map={map} onSaved={onSaved} />
  </section>;
}
