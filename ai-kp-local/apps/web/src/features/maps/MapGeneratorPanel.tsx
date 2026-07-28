import { Map, ShieldCheck, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Campaign, MapGenerationInput } from "../../api/types";

type Props = {
  campaign: Campaign | null;
  loading: boolean;
  onGenerate: (input: MapGenerationInput) => Promise<void> | void;
};

type Draft = {
  title: string;
  prompt: string;
  mapKind: MapGenerationInput["map_kind"];
  locationsText: string;
  routesText: string;
  featuresText: string;
  requiredText: string;
  eraYear: string;
  locale: string;
  season: string;
  timeOfDay: string;
  weather: string;
  publicArchitectureText: string;
  forbiddenText: string;
  visualStyle: MapGenerationInput["visual_style"];
};

const INITIAL_DRAFT: Draft = {
  title: "旧码头区域图",
  prompt: "一张适合调查、标记线索和移动棋子的旧码头地图。",
  mapKind: "regional",
  locationsText: "旧码头, 废弃仓库, 报社, 警局",
  routesText: "旧码头 > 废弃仓库\n旧码头 > 报社\n报社 > 警局",
  featuresText: "煤气路灯, 木制货箱, 有线电话",
  requiredText: "旧码头, 废弃仓库, 报社, 警局, 煤气路灯, 木制货箱, 有线电话",
  eraYear: "1928",
  locale: "美国马萨诸塞州",
  season: "秋季",
  timeOfDay: "夜晚",
  weather: "潮湿、有薄雾",
  publicArchitectureText: "新英格兰港口建筑, 红砖仓库, 深色木材, 黄铜五金",
  forbiddenText: "LED 灯, 液晶屏, 监控摄像头, 现代塑料家具",
  visualStyle: "period_illustrated_map"
};

export function parseMapList(value: string): string[] {
  return value
    .split(/[,\n，、]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function parseMapRoutes(value: string): {
  routes: [string, string][];
  errors: string[];
} {
  const routes: [string, string][] = [];
  const errors: string[] = [];
  value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line, index) => {
      const items = line.split(/\s*(?:->|>|→|—>)\s*/).map((item) => item.trim());
      if (items.length !== 2 || !items[0] || !items[1]) {
        errors.push(`第 ${index + 1} 行路线格式无效，应为“起点 > 终点”。`);
        return;
      }
      routes.push([items[0], items[1]]);
    });
  return { routes, errors };
}

function inferredYear(campaign: Campaign | null): string {
  const source = `${campaign?.current_time ?? ""} ${campaign?.title ?? ""}`;
  return source.match(/(?:1\d{3}|20\d{2}|21\d{2})/)?.[0] ?? "";
}

export function MapGeneratorPanel({ campaign, loading, onGenerate }: Props) {
  const [draft, setDraft] = useState<Draft>(INITIAL_DRAFT);
  const draftCampaignId = useRef<string | null>(null);

  useEffect(() => {
    const campaignId = campaign?.id ?? null;
    const year = inferredYear(campaign);
    if (draftCampaignId.current !== campaignId) {
      draftCampaignId.current = campaignId;
      setDraft({ ...INITIAL_DRAFT, eraYear: year });
    }
  }, [campaign?.id, campaign?.current_time, campaign?.title]);

  const validation = useMemo(() => {
    const locations = parseMapList(draft.locationsText);
    const features = parseMapList(draft.featuresText);
    const required = parseMapList(draft.requiredText);
    const routeResult = parseMapRoutes(draft.routesText);
    const errors = [...routeResult.errors];
    const duplicateLocations = locations.filter(
      (item, index) => locations.indexOf(item) !== index
    );
    if (locations.length < 2) errors.push("至少填写两个地点。");
    if (duplicateLocations.length) {
      errors.push(`地点名称重复：${[...new Set(duplicateLocations)].join("、")}`);
    }
    const known = new Set(locations);
    const unknownRoutes = routeResult.routes
      .flat()
      .filter((item) => !known.has(item));
    if (unknownRoutes.length) {
      errors.push(`路线引用未知地点：${[...new Set(unknownRoutes)].join("、")}`);
    }
    const available = new Set([...locations, ...features]);
    const missing = required.filter((item) => !available.has(item));
    if (missing.length) errors.push(`必需元素尚未列入地图：${missing.join("、")}`);
    if (!draft.title.trim()) errors.push("请填写地图名。");
    if (!draft.prompt.trim()) errors.push("请填写场景描述。");
    if (
      draft.eraYear.trim() &&
      (!/^\d{4}$/.test(draft.eraYear.trim()) ||
        Number(draft.eraYear) < 1000 ||
        Number(draft.eraYear) > 2100)
    ) {
      errors.push("年代必须是 1000–2100 之间的四位整数，或留空由团时间推断。");
    }
    return { errors, locations, features, required, routes: routeResult.routes };
  }, [draft]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (validation.errors.length || loading) return;
    await onGenerate({
      title: draft.title.trim(),
      prompt: draft.prompt.trim(),
      style: "investigation",
      map_kind: draft.mapKind,
      locations: validation.locations,
      routes: validation.routes,
      features: validation.features,
      required_elements: validation.required,
      era_year: draft.eraYear ? Number(draft.eraYear) : null,
      locale: draft.locale.trim(),
      season: draft.season.trim(),
      time_of_day: draft.timeOfDay.trim(),
      weather: draft.weather.trim(),
      public_architecture: parseMapList(draft.publicArchitectureText),
      forbidden_elements: parseMapList(draft.forbiddenText),
      visual_style: draft.visualStyle,
      width: draft.mapKind === "floorplan" ? 1200 : 1100,
      height: draft.mapKind === "floorplan" ? 800 : 720
    });
  }

  function patch<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  return (
    <section className="tool-panel map-panel map-generator-card">
      <div className="panel-heading">
        <div>
          <span className="map-panel-kicker">MAPSPEC V1</span>
          <h2>时代地图生成</h2>
        </div>
        <Map size={19} />
      </div>
      <p className="map-panel-lead">
        先生成可校验的结构地图，再生成无文字的时代背景图。标签、秘密和棋子始终由安全图层控制。
      </p>
      <form className="map-form" onSubmit={submit}>
        <fieldset>
          <legend>场景结构</legend>
          <label>
            地图名
            <input value={draft.title} onChange={(event) => patch("title", event.target.value)} />
          </label>
          <label>
            地图用途
            <select
              value={draft.mapKind}
              onChange={(event) => patch("mapKind", event.target.value as Draft["mapKind"])}
            >
              <option value="regional">区域关系图</option>
              <option value="site">地点场景图</option>
              <option value="floorplan">建筑平面图</option>
            </select>
          </label>
          <label>
            场景描述
            <textarea value={draft.prompt} onChange={(event) => patch("prompt", event.target.value)} />
          </label>
          <label>
            地点
            <textarea
              value={draft.locationsText}
              onChange={(event) => patch("locationsText", event.target.value)}
            />
            <small>用逗号分隔；名称必须唯一。</small>
          </label>
          <label>
            路线
            <textarea
              value={draft.routesText}
              onChange={(event) => patch("routesText", event.target.value)}
            />
            <small>每行一条，例如“接待大厅 &gt; 走廊”。错误路线不会被静默丢弃。</small>
          </label>
          <label>
            场景物件
            <textarea
              value={draft.featuresText}
              onChange={(event) => patch("featuresText", event.target.value)}
            />
          </label>
          <label>
            必须出现的元素
            <textarea
              value={draft.requiredText}
              onChange={(event) => patch("requiredText", event.target.value)}
            />
            <small>保存前必须达到 100% 结构覆盖。</small>
          </label>
        </fieldset>

        <fieldset>
          <legend>年代与地域</legend>
          <div className="map-form-pair">
            <label>
              年代
              <input
                max={2100}
                min={1000}
                step={1}
                type="number"
                value={draft.eraYear}
                onChange={(event) => patch("eraYear", event.target.value)}
              />
            </label>
            <label>
              地域文化
              <input value={draft.locale} onChange={(event) => patch("locale", event.target.value)} />
            </label>
          </div>
          <div className="map-form-triple">
            <label>
              季节
              <input value={draft.season} onChange={(event) => patch("season", event.target.value)} />
            </label>
            <label>
              时段
              <input
                value={draft.timeOfDay}
                onChange={(event) => patch("timeOfDay", event.target.value)}
              />
            </label>
            <label>
              天气
              <input value={draft.weather} onChange={(event) => patch("weather", event.target.value)} />
            </label>
          </div>
          <label>
            公共建筑与材料
            <textarea
              value={draft.publicArchitectureText}
              onChange={(event) => patch("publicArchitectureText", event.target.value)}
            />
            <small>会展示给玩家并发送给图片模型；秘密建筑信息请勿填写在这里。</small>
          </label>
          <label>
            KP 额外视觉审查项
            <textarea
              value={draft.forbiddenText}
              onChange={(event) => patch("forbiddenText", event.target.value)}
            />
            <small>仅保存在 KP 修订审计中，不发送给公共图片模型；请勿在这里依赖负面提示隐藏秘密。</small>
          </label>
          <label>
            视觉风格
            <select
              value={draft.visualStyle}
              onChange={(event) =>
                patch("visualStyle", event.target.value as Draft["visualStyle"])
              }
            >
              <option value="period_illustrated_map">时代插画地图</option>
              <option value="architectural_blueprint">建筑蓝图</option>
              <option value="ink_atlas">墨线地图集</option>
              <option value="tactical_floorplan">清晰战术平面图</option>
            </select>
          </label>
        </fieldset>

        {validation.errors.length > 0 && (
          <div className="map-form-errors" role="alert">
            <strong>还不能生成</strong>
            {validation.errors.map((error) => <span key={error}>{error}</span>)}
          </div>
        )}
        <div className="map-generation-safety">
          <ShieldCheck size={17} />
          <span>
            公共图片只使用明确标记为公开的结构、建筑文本与程序年代约束；KP 私密审查项和秘密不会进入提示词。
          </span>
        </div>
        <button
          className="primary-button map-generate-button"
          disabled={loading || validation.errors.length > 0}
          type="submit"
        >
          <Sparkles size={16} />
          {loading ? "正在生成…" : "生成结构地图"}
        </button>
      </form>
    </section>
  );
}
