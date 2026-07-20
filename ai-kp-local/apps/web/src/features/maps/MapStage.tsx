import { Map, RefreshCw } from "lucide-react";
import type { Role, SavedMap } from "../../api/types";

type Props = {
  maps: SavedMap[];
  activeMap: SavedMap | null;
  activeMapImage: string;
  role?: Role;
  hasIdentity: boolean;
  onSetPublished: (published: boolean) => void;
  onRefresh: () => void;
  onOpenMap: (mapId: string) => void;
};

export function MapStage(props: Props) {
  return (
    <section className="map-stage" id="map-section">
      <div className="stage-toolbar">
        <div>
          <h2>{props.activeMap?.title ?? "地图预览"}</h2>
          <small>本地持久化地图库 · 再次进入时自动恢复上次使用的地图</small>
          {props.activeMap && props.role === "kp" && (
            <small>{props.activeMap.status === "published" ? "玩家可见" : "KP 草稿，玩家不可见"}</small>
          )}
        </div>
        <div className="inline-actions stage-actions">
          {props.activeMap && props.role === "kp" && (
            <button
              className={
                props.activeMap.status === "published" ? "secondary-button" : "primary-button"
              }
              onClick={() => props.onSetPublished(props.activeMap?.status !== "published")}
              type="button"
            >
              {props.activeMap.status === "published" ? "收回为草稿" : "发布给玩家"}
            </button>
          )}
          <button className="ghost-button" onClick={props.onRefresh} type="button">
            <RefreshCw size={16} />
            刷新地图
          </button>
        </div>
      </div>
      <div className="saved-map-list">
        {props.maps.map((map) => (
          <button
            className={`map-tab ${map.id === props.activeMap?.id ? "selected" : ""}`}
            key={map.id}
            onClick={() => props.onOpenMap(map.id)}
            type="button"
          >
            {map.title}
            {props.role === "kp" ? ` · ${map.status === "published" ? "已发布" : "草稿"}` : ""}
          </button>
        ))}
        {!props.maps.length && props.hasIdentity && <small>当前还没有可调用的地图。</small>}
      </div>
      <div className="svg-frame">
        {props.activeMapImage ? (
          <img
            alt={props.activeMap?.title ?? "团地图"}
            className="svg-image"
            draggable={false}
            src={props.activeMapImage}
          />
        ) : (
          <div className="empty-state">
            <Map size={34} />
            <p>生成地图后会存入本地地图库，可随时重新调用。</p>
          </div>
        )}
        {props.activeMap?.tokens?.map((token) => (
          <span
            className="token-chip"
            key={token.id}
            style={{
              backgroundColor: token.color,
              left: `${(token.x / (props.activeMap?.width || 1)) * 100}%`,
              top: `${(token.y / (props.activeMap?.height || 1)) * 100}%`
            }}
            title={`${token.label}: ${token.location_name}`}
          >
            {token.label}
          </span>
        ))}
      </div>
    </section>
  );
}
