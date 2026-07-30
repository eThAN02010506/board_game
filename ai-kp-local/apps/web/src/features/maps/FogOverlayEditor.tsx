import { Eye, RotateCcw, Save, Trash2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { requestJson } from "../../api/client";
import type { MapFogRegion, SavedMap } from "../../api/types";

type Point = { x: number; y: number };
type Props = { map: SavedMap; onSaved: () => void };

function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

export function FogOverlayEditor({ map, onSaved }: Props) {
  const [label, setLabel] = useState("未探索区域");
  const [points, setPoints] = useState<Point[]>([]);
  const [drawing, setDrawing] = useState(false);
  const [message, setMessage] = useState("按住并圈出区域；迷雾是独立覆盖层。");
  const svgRef = useRef<SVGSVGElement>(null);
  const mapSvg = useMemo(
    () =>
      map.svg_text
        ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(map.svg_text)}`
        : "",
    [map.svg_text]
  );

  function pointFromEvent(event: ReactPointerEvent<SVGSVGElement>): Point {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(map.width, ((event.clientX - bounds.left) / bounds.width) * map.width)),
      y: Math.max(0, Math.min(map.height, ((event.clientY - bounds.top) / bounds.height) * map.height))
    };
  }

  function start(event: ReactPointerEvent<SVGSVGElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    setPoints([pointFromEvent(event)]);
    setDrawing(true);
  }

  function draw(event: ReactPointerEvent<SVGSVGElement>) {
    if (!drawing) return;
    const next = pointFromEvent(event);
    setPoints((current) => {
      if (current.length >= 64 || distance(current[current.length - 1], next) < 6) return current;
      return [...current, next];
    });
  }

  function finish(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDrawing(false);
    if (points.length < 3) setMessage("区域太小，请重新圈出至少三个点。");
  }

  async function save() {
    try {
      await requestJson(`/maps/${map.id}/fog-regions`, {
        method: "POST",
        body: JSON.stringify({ label, polygon: points })
      });
      setPoints([]);
      setMessage("独立雾层已保存；背景重生成不会影响它。");
      onSaved();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function reveal(fog: MapFogRegion) {
    await requestJson(`/map-fog-regions/${fog.id}/reveal`, {
      method: "POST",
      body: JSON.stringify({ expected_version: fog.version })
    });
    onSaved();
  }

  async function remove(fog: MapFogRegion) {
    await requestJson(
      `/map-fog-regions/${fog.id}?expected_version=${encodeURIComponent(fog.version)}`,
      { method: "DELETE" }
    );
    onSaved();
  }

  return (
    <section className="fog-overlay-editor">
      <header>
        <div>
          <strong>自由雾层</strong>
          <small>{message}</small>
        </div>
        <label>
          名称
          <input value={label} onChange={(event) => setLabel(event.target.value)} />
        </label>
      </header>
      <svg
        aria-label="自由雾区绘制画布"
        className="fog-drawing-canvas"
        onPointerCancel={finish}
        onPointerDown={start}
        onPointerMove={draw}
        onPointerUp={finish}
        ref={svgRef}
        role="application"
        style={{ touchAction: "none" }}
        viewBox={`0 0 ${map.width} ${map.height}`}
      >
        {mapSvg && <image height={map.height} href={mapSvg} width={map.width} />}
        {map.fog_regions?.filter((fog) => fog.status === "hidden").map((fog) => (
          <polygon
            fill="rgba(19, 23, 28, 0.68)"
            key={fog.id}
            points={fog.polygon.map((point) => `${point.x},${point.y}`).join(" ")}
            stroke="#c4ae7e"
            strokeWidth="2"
          />
        ))}
        {points.length > 0 && (
          <polygon
            fill="rgba(128, 74, 44, 0.48)"
            points={points.map((point) => `${point.x},${point.y}`).join(" ")}
            stroke="#f1c47b"
            strokeDasharray="8 5"
            strokeWidth="3"
          />
        )}
      </svg>
      <div className="inline-actions">
        <button className="primary-button" disabled={points.length < 3 || !label.trim()} onClick={() => void save()}>
          <Save size={14} />保存雾区
        </button>
        <button className="ghost-button" disabled={!points.length} onClick={() => setPoints([])}>
          <RotateCcw size={14} />重画
        </button>
      </div>
      <div className="fog-region-list">
        {map.fog_regions?.map((fog) => (
          <article key={fog.id}>
            <span><strong>{fog.label}</strong><small>{fog.status === "hidden" ? "遮挡中" : "已揭开"}</small></span>
            {fog.status === "hidden" && (
              <span className="inline-actions">
                <button onClick={() => void reveal(fog)}><Eye size={13} />揭开</button>
                <button onClick={() => void remove(fog)}><Trash2 size={13} />删除</button>
              </span>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
