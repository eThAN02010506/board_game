import { Eye, Map as MapIcon, MapPin } from "lucide-react";
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { requestJson } from "../../api/client";
import type { MapAwareness, MapLocation, SavedMap } from "../../api/types";

function svgDataUri(svg: string | undefined): string {
  return svg ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` : "";
}

type Props = {
  activeMap: SavedMap | null;
  loading?: boolean;
  onRefresh: () => void;
};

export function AwarenessMap({ activeMap, loading, onRefresh }: Props) {
  const [awareness, setAwareness] = useState<MapAwareness | null>(null);

  useEffect(() => {
    if (!activeMap) {
      setAwareness(null);
      return;
    }
    let active = true;
    void requestJson<MapAwareness>(`/maps/${encodeURIComponent(activeMap.id)}/awareness`)
      .then((next) => {
        if (active) setAwareness(next);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [activeMap?.id]);

  if (!activeMap || !awareness) {
    return (
      <section className="tool-panel awareness-map">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">当前位置感知</p>
            <h2>位置</h2>
          </div>
          <MapIcon size={18} />
        </div>
        <div className="empty-state">
          <MapPin size={28} />
          <p>当前还没有可感知的地图位置。</p>
        </div>
      </section>
    );
  }

  const stateById = new Map(
    awareness.states.map((item) => [item.location_id, item.state])
  );
  const specLocations: MapLocation[] = activeMap.locations ?? [];
  const currentId = awareness.current_location_id;
  const currentLocation = specLocations.find((loc) => loc.id === currentId) ?? null;

  const mapWidth = activeMap.width || 960;
  const mapHeight = activeMap.height || 640;
  const fullSvg = svgDataUri(activeMap.svg_text);
  const overlaySvg = svgDataUri(activeMap.overlay_svg_text);

  const currentTokens = activeMap.tokens?.filter(
    (token) => token.location_name === currentLocation?.name
  ) ?? [];

  return (
    <section className="tool-panel awareness-map">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">当前位置感知</p>
          <h2>{currentLocation?.name ?? "未知位置"}</h2>
        </div>
        <button className="ghost-button" disabled={loading} onClick={onRefresh} type="button">
          刷新
        </button>
      </div>

      {currentLocation && (
        <div className="awareness-current">
          <p className="awareness-current-desc">
            {currentLocation.public_description || "这里看起来没有什么特别值得注意的。"}
          </p>
          {currentTokens.length > 0 && (
            <div className="awareness-current-tokens">
              {currentTokens.map((token) => (
                <span className="awareness-token" key={token.id}>
                  {token.label}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="awareness-minimap" style={{ "--map-aspect": `${mapWidth} / ${mapHeight}` } as CSSProperties}>
        <svg
          aria-label={`${activeMap.title}已知结构小地图`}
          className="map-canvas-svg"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          viewBox={`0 0 ${mapWidth} ${mapHeight}`}
        >
          <image
            height={mapHeight}
            href={fullSvg}
            preserveAspectRatio="xMidYMid meet"
            width={mapWidth}
            x={0}
            y={0}
          />
          {overlaySvg && fullSvg !== overlaySvg && (
            <image
              height={mapHeight}
              href={overlaySvg}
              preserveAspectRatio="xMidYMid meet"
              width={mapWidth}
              x={0}
              y={0}
            />
          )}
          {specLocations.map((loc) => {
            const state = stateById.get(loc.id) ?? "unknown";
            if (state === "destroyed") return null;
            const isCurrent = state === "current";
            const isSeen = state === "seen";
            // Unknown locations are removed by the server projection. Treat an
            // unexpected state as absent instead of trying to hide leaked data.
            if (!isCurrent && !isSeen) return null;
            return (
              <g
                aria-label={`${loc.name}${isCurrent ? "（当前位置）" : ""}`}
                className={isCurrent ? "awareness-current-mark" : "awareness-seen-mark"}
                key={loc.id}
                transform={`translate(${loc.x} ${loc.y})`}
              >
                <circle
                  fill={isCurrent ? "#e8b93a" : "rgba(232, 185, 58, 0.25)"}
                  r={isCurrent ? 30 : 24}
                  stroke={isCurrent ? "#fff8e8" : "#d8c48a"}
                  strokeWidth={isCurrent ? 3 : 1.5}
                />
                <text
                  fill={isCurrent ? "#202020" : "#d8c48a"}
                  fontSize={isCurrent ? 12 : 10}
                  fontWeight={isCurrent ? "700" : "500"}
                  textAnchor="middle"
                  y={4}
                >
                  {loc.name}
                </text>
              </g>
            );
          })}
          {activeMap.tokens?.map((token) => (
            <g className="map-token" key={token.id} transform={`translate(${token.x} ${token.y})`}>
              <circle fill={token.color} r="18" stroke="#fff8e8" strokeWidth="3" />
              <text
                fill="#fff"
                fontSize="10"
                fontWeight="700"
                textAnchor="middle"
                y="3"
              >
                {token.label.slice(0, 3)}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <small className="permission-hint">
        <Eye size={13} /> 小地图只接收你已经见过的位置；未探索区域不会发送到此设备。
      </small>
    </section>
  );
}
