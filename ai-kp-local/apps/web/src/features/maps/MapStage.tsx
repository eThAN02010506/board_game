import {
  CheckCircle2,
  Diff,
  Image,
  Map,
  Minus,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TriangleAlert
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { requestBlob, requestJson } from "../../api/client";
import type { MapAsset, Role, SavedMap } from "../../api/types";

type Props = {
  maps: SavedMap[];
  activeMap: SavedMap | null;
  role?: Role;
  hasIdentity: boolean;
  loading?: boolean;
  showReviewControls?: boolean;
  onSetPublished: (published: boolean) => void;
  onRefresh: () => void;
  onOpenMap: (mapId: string) => void;
  onGenerateBackground?: (seed: number | null) => void;
  onSelectAsset?: (assetId: string) => void;
};

function svgDataUri(svg: string | undefined): string {
  return svg ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` : "";
}

type MapPublishDiff = {
  first_publish: boolean;
  revision: {
    current_id: string | null;
    current_no: number | null;
    published_id: string | null;
    published_no: number | null;
  };
  layout_hash_changed: boolean;
  locations: { added: ElementChange[]; removed: ElementChange[]; changed: ChangedElement[] };
  connections: { added: ElementChange[]; removed: ElementChange[]; changed: ChangedElement[] };
  canvas_changed: boolean;
  title_changed: boolean;
  player_visible_changes: ElementChange[];
};

type ElementChange = { id: string; name: string };
type ChangedElement = { id: string; name: string; fields: string[] };

export function MapStage({
  showReviewControls = true,
  ...props
}: Props) {
  const [previewAssetId, setPreviewAssetId] = useState<string | null>(null);
  const [backgroundPreview, setBackgroundPreview] = useState<{
    sourceUrl: string;
    objectUrl: string;
  } | null>(null);
  const [backgroundError, setBackgroundError] = useState("");
  const [seed, setSeed] = useState("1928");
  const [zoom, setZoom] = useState(1);
  const [publishDiff, setPublishDiff] = useState<MapPublishDiff | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState("");

  useEffect(() => {
    setPreviewAssetId(null);
    setZoom(1);
  }, [props.activeMap?.id]);

  useEffect(() => {
    if (
      previewAssetId &&
      !props.activeMap?.assets?.some((asset) => asset.id === previewAssetId)
    ) {
      setPreviewAssetId(null);
    }
  }, [previewAssetId, props.activeMap?.assets]);

  const effectiveAssetId =
    previewAssetId ?? props.activeMap?.render?.selected_asset_id ?? null;
  const effectiveAsset = props.activeMap?.assets?.find(
    (asset) => asset.id === effectiveAssetId
  );
  const assetContentUrl =
    effectiveAsset?.content_url ??
    (effectiveAssetId === props.activeMap?.render?.selected_asset_id
      ? props.activeMap?.render?.background_asset_url
      : null);
  const backgroundUrl =
    backgroundPreview?.sourceUrl === assetContentUrl
      ? backgroundPreview.objectUrl
      : "";

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setBackgroundError("");
    if (!assetContentUrl) {
      setBackgroundPreview(null);
      return;
    }
    requestBlob(assetContentUrl, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setBackgroundPreview({ sourceUrl: assetContentUrl, objectUrl });
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setBackgroundPreview(null);
        setBackgroundError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [assetContentUrl]);

  const fullSvg = useMemo(
    () => svgDataUri(props.activeMap?.svg_text),
    [props.activeMap?.svg_text]
  );
  const overlaySvg = useMemo(
    () => svgDataUri(props.activeMap?.overlay_svg_text),
    [props.activeMap?.overlay_svg_text]
  );
  const mapWidth = props.activeMap?.width ?? 1100;
  const mapHeight = props.activeMap?.height ?? 720;
  const validation = props.activeMap?.validation;
  const era = props.activeMap?.map_spec?.era;
  const canGenerateImage = Boolean(props.activeMap?.image_generation?.available);
  const selectedAssetId = props.activeMap?.render?.selected_asset_id ?? null;
  const hasUnselectedPreview = Boolean(
    previewAssetId && previewAssetId !== selectedAssetId
  );

  function selectCandidate(asset: MapAsset) {
    setPreviewAssetId(asset.id);
  }

  async function loadPublishDiff() {
    const mapId = props.activeMap?.id;
    const revisionId = props.activeMap?.revision_id;
    if (!mapId || !revisionId) return;
    setDiffLoading(true);
    setDiffError("");
    try {
      const diff = await requestJson<MapPublishDiff>(
        `/maps/${encodeURIComponent(mapId)}/publish-diff`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_revision_id: revisionId,
            expected_selected_asset_id:
              props.activeMap?.render?.selected_asset_id ?? null
          })
        }
      );
      setPublishDiff(diff);
    } catch (error) {
      setDiffError(error instanceof Error ? error.message : String(error));
      setPublishDiff(null);
    } finally {
      setDiffLoading(false);
    }
  }

  return (
    <section className="map-stage" id="map-section">
      <div className="stage-toolbar">
        <div>
          <span className="map-panel-kicker">LAYERED MAP</span>
          <h2>{props.activeMap?.title ?? "地图预览"}</h2>
          <small>结构、标签与棋子共用坐标；背景保持比例适配，重新进入时从本地恢复。</small>
          {props.activeMap && props.role === "kp" && (
            <small>{props.activeMap.status === "published" ? "玩家可见" : "KP 草稿，玩家不可见"}</small>
          )}
        </div>
        <div className="inline-actions stage-actions">
          {props.activeMap && props.role === "kp" && showReviewControls && (
            <button
              className={
                props.activeMap.status === "published" ? "secondary-button" : "primary-button"
              }
              disabled={
                props.loading ||
                (props.activeMap.status !== "published" &&
                  (!props.activeMap.revision_id ||
                    validation?.valid === false ||
                    hasUnselectedPreview))
              }
              onClick={() => props.onSetPublished(props.activeMap?.status !== "published")}
              title={
                props.activeMap.status !== "published" && hasUnselectedPreview
                  ? "请先选用当前预览候选，再发布地图"
                  : !props.activeMap.revision_id
                    ? "旧地图需要先建立可审核的 MapSpec revision"
                  : undefined
              }
              type="button"
            >
              {props.activeMap.status === "published" ? "收回为草稿" : "审核通过并发布"}
            </button>
          )}
          {props.activeMap && props.role === "kp" && showReviewControls && (
            <button
              className="ghost-button"
              disabled={props.loading || !props.activeMap.revision_id}
              onClick={() => void loadPublishDiff()}
              title="对比当前 revision 与上次发布，预览玩家将看到的变化"
              type="button"
            >
              <Diff size={15} />
              {diffLoading ? "计算中…" : "预览发布差异"}
            </button>
          )}
          <button className="ghost-button" onClick={props.onRefresh} type="button">
            <RefreshCw size={16} />
            刷新
          </button>
        </div>
      </div>

      {publishDiff && (
        <details className="map-publish-diff">
          <summary>
            发布差异预览
            {publishDiff.first_publish
              ? "（首次发布，无对比基线）"
              : `（R${publishDiff.revision.published_no} → R${publishDiff.revision.current_no}）`}
          </summary>
          {diffError && <p className="error-copy">{diffError}</p>}
          {publishDiff.first_publish ? (
            <p className="muted-copy">
              这是首次发布，玩家将首次看到当前地图结构。
            </p>
          ) : (
            <div className="map-diff-grid">
              <DiffGroup label="新增地点" changes={publishDiff.locations.added} tone="added" />
              <DiffGroup label="删除地点" changes={publishDiff.locations.removed} tone="removed" />
              <DiffGroup
                label="变更地点"
                changes={publishDiff.locations.changed.map((item) => ({
                  id: item.id,
                  name: item.fields.length
                    ? `${item.name}（${item.fields.join("、")}）`
                    : item.name
                }))}
                tone="changed"
              />
              <DiffGroup label="新增路线" changes={publishDiff.connections.added} tone="added" />
              <DiffGroup label="删除路线" changes={publishDiff.connections.removed} tone="removed" />
              {publishDiff.canvas_changed && (
                <p className="diff-note">画布尺寸变化</p>
              )}
              {publishDiff.title_changed && (
                <p className="diff-note">标题变化</p>
              )}
              {publishDiff.player_visible_changes.length > 0 && (
                <p className="diff-note">
                  玩家可见变化：{" "}
                  {publishDiff.player_visible_changes.map((item) => item.name).join("、")}
                </p>
              )}
            </div>
          )}
        </details>
      )}

      <div className="saved-map-list">
        {props.maps.map((map) => (
          <button
            aria-current={map.id === props.activeMap?.id ? "true" : undefined}
            className={`map-tab ${map.id === props.activeMap?.id ? "selected" : ""}`}
            key={map.id}
            onClick={() => props.onOpenMap(map.id)}
            type="button"
          >
            <span>{map.title}</span>
            <small>
              {props.role === "kp"
                ? `${map.revision_no ? `R${map.revision_no}` : "LEGACY"} · ${
                    map.status === "published" ? "已发布" : "草稿"
                  }`
                : "已发布"}
            </small>
          </button>
        ))}
        {!props.maps.length && props.hasIdentity && <small>当前还没有可调用的地图。</small>}
      </div>

      {props.activeMap && showReviewControls && (
        <div className="map-review-strip">
          <article>
            <span>年代上下文</span>
            <strong>{era?.year ?? "待确认"} · {era?.locale || "地域待确认"}</strong>
            <small>{[era?.season, era?.time_of_day, era?.weather].filter(Boolean).join(" · ") || "未填写环境信息"}</small>
          </article>
          <article>
            <span>必需元素覆盖</span>
            <strong className={validation?.coverage.percent === 100 ? "quality-pass" : "quality-fail"}>
              {validation?.coverage.percent ?? 0}%
            </strong>
            <small>
              {validation
                ? `${validation.coverage.covered} / ${validation.coverage.required}`
                : "旧地图暂无校验报告"}
            </small>
          </article>
          <article>
            <span>公共背景安全</span>
            <strong><ShieldCheck size={16} /> 玩家安全投影</strong>
            <small>秘密地点、KP 备注、标签与棋子不会发给图片模型。</small>
          </article>
          <article>
            <span>当前视觉层</span>
            <strong>{backgroundUrl ? "图片 + SVG" : "确定性 SVG"}</strong>
            <small>{effectiveAsset?.model ?? "图片失败也不会影响游玩"}</small>
          </article>
        </div>
      )}

      {props.role === "kp" && props.activeMap && showReviewControls && (
        <div className="map-image-workbench">
          <div>
            <div className="map-image-heading">
              <div>
                <span className="map-panel-kicker">OPTIONAL IMAGE LAYER</span>
                <h3>时代背景候选</h3>
              </div>
              <Image size={18} />
            </div>
            <p>
              图片只负责材质、灯光和氛围。房间、路线、全部必需元素与隐藏信息由 MapSpec/SVG 保证。
            </p>
          </div>
          <label className="map-seed-field">
            Seed
            <input
              min={0}
              step={1}
              type="number"
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
            />
          </label>
          <button
            className="secondary-button"
            disabled={
              !canGenerateImage ||
              props.loading ||
              (seed.trim() !== "" &&
                (!Number.isInteger(Number(seed)) || Number(seed) < 0))
            }
            onClick={() => {
              const parsedSeed = seed.trim() ? Number(seed) : null;
              props.onGenerateBackground?.(parsedSeed);
            }}
            type="button"
          >
            <Sparkles size={16} />
            {props.loading ? "生成中…" : "生成安全背景候选"}
          </button>
          {!canGenerateImage && (
            <small className="map-provider-note">
              {props.activeMap.image_generation?.unavailable_reason ===
              "legacy_map_requires_revision"
                ? "这张旧地图需要先升级为 MapSpec revision，结构 SVG 仍可正常使用。"
                : "尚未配置独立图片模型；结构地图与时代 SVG 已可完整使用。请前往“模型设置 → 地图图片模型”检测并保存服务。"}
            </small>
          )}
          {props.activeMap.assets?.length ? (
            <div className="map-asset-list">
              {props.activeMap.assets.map((asset) => {
                const selected = asset.id === props.activeMap?.render?.selected_asset_id;
                const previewed = asset.id === effectiveAssetId;
                return (
                  <article className={previewed ? "active" : ""} key={asset.id}>
                    <button
                      aria-label={`预览 ${asset.model} Seed ${asset.seed ?? "自动"}`}
                      aria-pressed={previewed}
                      onClick={() => selectCandidate(asset)}
                      type="button"
                    >
                      <Image size={16} />
                      <span>
                        <strong>{asset.model}</strong>
                        <small>Seed {asset.seed ?? "自动"} · {asset.width}×{asset.height}</small>
                      </span>
                    </button>
                    {selected ? (
                      <span className="asset-selected"><CheckCircle2 size={14} /> 正式背景</span>
                    ) : (
                      <button
                        aria-label={`选用 ${asset.model} Seed ${asset.seed ?? "自动"} 作为正式背景`}
                        className="ghost-button"
                        disabled={!previewed || props.loading}
                        onClick={() => props.onSelectAsset?.(asset.id)}
                        type="button"
                      >
                        选用
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
          ) : null}
        </div>
      )}

      {validation?.issues?.length ? (
        <div className="map-validation-notes">
          {validation.issues.map((issue, index) => (
            <span className={issue.level} key={`${issue.code}-${index}`}>
              {issue.level === "error" ? <TriangleAlert size={14} /> : <CheckCircle2 size={14} />}
              {issue.message}
            </span>
          ))}
        </div>
      ) : null}

      <div className="map-viewport-toolbar" aria-label="地图缩放">
        <button
          aria-label="缩小地图"
          className="ghost-button"
          disabled={zoom <= 0.7}
          onClick={() => setZoom((value) => Math.max(0.7, value - 0.15))}
          type="button"
        >
          <Minus size={15} />
        </button>
        <span>{Math.round(zoom * 100)}%</span>
        <button
          aria-label="放大地图"
          className="ghost-button"
          disabled={zoom >= 2}
          onClick={() => setZoom((value) => Math.min(2, value + 0.15))}
          type="button"
        >
          <Plus size={15} />
        </button>
      </div>

      <div
        className="map-viewport"
        style={{ "--map-aspect": `${mapWidth} / ${mapHeight}` } as CSSProperties}
      >
        {props.activeMap && fullSvg ? (
          <svg
            aria-label={`${props.activeMap.title}分层地图`}
            className="map-canvas-svg"
            preserveAspectRatio="xMidYMid meet"
            role="img"
            style={{ transform: `scale(${zoom})` }}
            viewBox={`0 0 ${mapWidth} ${mapHeight}`}
          >
            {backgroundUrl && (
              <image
                height={mapHeight}
                href={backgroundUrl}
                preserveAspectRatio="xMidYMid meet"
                width={mapWidth}
                x={0}
                y={0}
              />
            )}
            <image
              height={mapHeight}
              href={backgroundUrl && overlaySvg ? overlaySvg : fullSvg}
              preserveAspectRatio="xMidYMid meet"
              width={mapWidth}
              x={0}
              y={0}
            />
            {props.activeMap.tokens?.map((token) => (
              <g className="map-token" key={token.id} transform={`translate(${token.x} ${token.y})`}>
                <circle fill={token.color} r="23" stroke="#fff8e8" strokeWidth="4" />
                <text
                  fill="#fff"
                  fontSize="11"
                  fontWeight="700"
                  textAnchor="middle"
                  y="4"
                >
                  {token.label.slice(0, 5)}
                </text>
                <title>{token.label}: {token.location_name}</title>
              </g>
            ))}
            {props.activeMap.fog_regions?.filter((fog) => fog.status === "hidden").map((fog) => (
              <polygon
                aria-label={`迷雾：${fog.label}`}
                fill="rgba(11, 14, 19, 0.94)"
                key={fog.id}
                points={fog.polygon.map((point) => `${point.x},${point.y}`).join(" ")}
                stroke="rgba(196, 174, 126, 0.4)"
                strokeWidth="2"
              />
            ))}
          </svg>
        ) : (
          <div className="empty-state">
            <Map size={34} />
            <p>生成地图后会存入本地地图库，可随时重新调用。</p>
          </div>
        )}
      </div>
      {props.activeMap && (
        <div className="sr-only" aria-label="地图文字摘要">
          <h3>{props.activeMap.title}文字摘要</h3>
          <p>
            地点：
            {props.activeMap.locations?.map((location) => location.name).join("、") ||
              "暂无公开地点"}
          </p>
          <p>
            路线：
            {props.activeMap.routes
              ?.map((route) => `${route.start_name}到${route.end_name}`)
              .join("；") || "暂无公开路线"}
          </p>
          <p>
            场景元素：
            {props.activeMap.map_spec?.features
              ?.map((feature) => {
                const location = props.activeMap?.map_spec?.locations.find(
                  (item) => item.id === feature.location_id
                );
                return location ? `${feature.name}位于${location.name}` : feature.name;
              })
              .join("；") || "暂无公开场景元素"}
          </p>
          <p>
            棋子：
            {props.activeMap.tokens
              ?.map((token) => `${token.label}位于${token.location_name}`)
              .join("；") || "暂无公开棋子"}
          </p>
        </div>
      )}
      {backgroundError && (
        <div className="map-background-error" role="status">
          <TriangleAlert size={15} />
          背景图片无法读取，已安全回退到结构 SVG：{backgroundError}
        </div>
      )}
    </section>
  );
}

function DiffGroup({
  label,
  changes,
  tone
}: {
  label: string;
  changes: Array<{ id: string; name: string }>;
  tone: "added" | "removed" | "changed";
}) {
  if (!changes.length) return null;
  return (
    <div className={`map-diff-group diff-${tone}`}>
      <strong>{label}</strong>
      <ul>
        {changes.map((change) => (
          <li key={change.id}>{change.name}</li>
        ))}
      </ul>
    </div>
  );
}
