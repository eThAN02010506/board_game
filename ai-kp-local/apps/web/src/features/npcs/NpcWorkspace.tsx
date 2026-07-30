import { Clock3, Dices, MapPinned, Route, Save, Trash2, Users } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  createTravelLocation,
  createTravelRoute,
  deleteTravelLocation,
  deleteTravelRoute,
  getNpcReappearancePolicy,
  getTravelGraph,
  listNpcHiddenAppearances,
  listCampaignNpcs,
  previewTravelRoute,
  resolveNpcHiddenAppearance,
  saveNpcAvailability,
  saveNpcReappearancePolicy
} from "../../api/client";
import type {
  AuthIdentity,
  Campaign,
  CampaignNpcRecord,
  NpcAvailabilityProfile,
  NpcReappearancePolicy,
  NpcHiddenAppearanceResolution,
  TravelGraph,
  TravelRoutePreview
} from "../../api/types";

type Props = {
  campaign: Campaign | null;
  identity: AuthIdentity | null;
};

const emptyProfile: NpcAvailabilityProfile = {
  lifecycle_state: "unknown",
  born_year: null,
  died_year: null,
  active_from_year: null,
  active_until_year: null,
  location_tags: [],
  profession_tags: [],
  kp_notes: ""
};

function numericOrNull(value: string): number | null {
  const normalized = value.trim();
  return normalized ? Number(normalized) : null;
}

function tagsFromText(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean)
    )
  );
}

function describeMinutes(value: number): string {
  if (value < 60) return `${value} 分钟`;
  const hours = Math.floor(value / 60);
  const minutes = value % 60;
  return minutes ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`;
}

function newHiddenRollKey(): string {
  return `hidden-${crypto.randomUUID()}`;
}

function hiddenDestinationsFromText(value: string) {
  return value
    .split(/\n/)
    .map((line) => {
      const [locationName, rawWeight] = line.split("|");
      return {
        location_name: locationName?.trim() ?? "",
        weight: rawWeight?.trim() ? Number(rawWeight) : 1
      };
    })
    .filter((item) => item.location_name);
}

export function NpcWorkspace({ campaign, identity }: Props) {
  const [npcs, setNpcs] = useState<CampaignNpcRecord[]>([]);
  const [policy, setPolicy] = useState<NpcReappearancePolicy | null>(null);
  const [graph, setGraph] = useState<TravelGraph>({ locations: [], routes: [] });
  const [selectedNpcId, setSelectedNpcId] = useState("");
  const [profile, setProfile] = useState<NpcAvailabilityProfile>(emptyProfile);
  const [locationTagsText, setLocationTagsText] = useState("");
  const [professionTagsText, setProfessionTagsText] = useState("");
  const [locationName, setLocationName] = useState("");
  const [locationAliases, setLocationAliases] = useState("");
  const [routeFrom, setRouteFrom] = useState("");
  const [routeTo, setRouteTo] = useState("");
  const [routeMinutes, setRouteMinutes] = useState("30");
  const [routeMode, setRouteMode] = useState<
    "walk" | "drive" | "rail" | "boat" | "flight" | "other"
  >("walk");
  const [routeBidirectional, setRouteBidirectional] = useState(true);
  const [previewOrigin, setPreviewOrigin] = useState("");
  const [previewDestination, setPreviewDestination] = useState("");
  const [preview, setPreview] = useState<TravelRoutePreview | null>(null);
  const [hiddenRolls, setHiddenRolls] = useState<NpcHiddenAppearanceResolution[]>([]);
  const [hiddenTrigger, setHiddenTrigger] = useState("");
  const [hiddenChance, setHiddenChance] = useState("50");
  const [hiddenDestinations, setHiddenDestinations] = useState("");
  const [hiddenProfession, setHiddenProfession] = useState("");
  const [hiddenRollKey, setHiddenRollKey] = useState(newHiddenRollKey);
  const [latestHiddenRoll, setLatestHiddenRoll] =
    useState<NpcHiddenAppearanceResolution | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const selectedNpc = useMemo(
    () => npcs.find((item) => item.id === selectedNpcId) ?? null,
    [npcs, selectedNpcId]
  );

  useEffect(() => {
    if (!campaign || identity?.role !== "kp") return;
    let active = true;
    setBusy(true);
    setMessage("");
    Promise.all([
      listCampaignNpcs(campaign.id),
      getNpcReappearancePolicy(campaign.id),
      getTravelGraph(campaign.id),
      listNpcHiddenAppearances(campaign.id)
    ])
      .then(([nextNpcs, nextPolicy, nextGraph, nextHiddenRolls]) => {
        if (!active) return;
        setNpcs(nextNpcs);
        setPolicy(nextPolicy);
        setGraph(nextGraph);
        setHiddenRolls(nextHiddenRolls);
        setSelectedNpcId((current) => current || nextNpcs[0]?.id || "");
      })
      .catch((error) => {
        if (active) setMessage(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [campaign, identity?.role]);

  useEffect(() => {
    const next = selectedNpc?.availability_profile ?? emptyProfile;
    setProfile({ ...next });
    setLocationTagsText(next.location_tags.join("，"));
    setProfessionTagsText(next.profession_tags.join("，"));
  }, [selectedNpc]);

  async function refresh() {
    if (!campaign) return;
    const [nextNpcs, nextPolicy, nextGraph, nextHiddenRolls] = await Promise.all([
      listCampaignNpcs(campaign.id),
      getNpcReappearancePolicy(campaign.id),
      getTravelGraph(campaign.id),
      listNpcHiddenAppearances(campaign.id)
    ]);
    setNpcs(nextNpcs);
    setPolicy(nextPolicy);
    setGraph(nextGraph);
    setHiddenRolls(nextHiddenRolls);
  }

  async function perform(label: string, action: () => Promise<unknown>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
      await refresh();
      setMessage(`${label}已保存。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  if (!campaign) {
    return <section className="page-card empty-state">请先在“团与权限”中选择一个团。</section>;
  }
  if (identity?.role !== "kp") {
    return (
      <section className="page-card empty-state">
        NPC 档案、地点图谱和跨本策略只对当前团 KP 开放。
      </section>
    );
  }
  const campaignId = campaign.id;

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    if (!selectedNpc) return;
    await perform("NPC 可用性档案", () =>
      saveNpcAvailability(campaignId, selectedNpc.id, {
        lifecycle_state: profile.lifecycle_state,
        born_year: profile.born_year,
        died_year: profile.died_year,
        active_from_year: profile.active_from_year,
        active_until_year: profile.active_until_year,
        location_tags: tagsFromText(locationTagsText),
        profession_tags: tagsFromText(professionTagsText),
        kp_notes: profile.kp_notes ?? ""
      })
    );
  }

  return (
    <div className="npc-workspace">
      <section className="page-card npc-workspace-hero">
        <div>
          <p className="eyebrow">人物连续性与可达性</p>
          <h2>NPC 档案工作台</h2>
          <p>所有限制由 KP 明确保存；AI 只能读取和提出建议，不能自行改写。</p>
        </div>
        <Users size={30} />
      </section>

      {message && <div className="npc-workspace-message">{message}</div>}

      <section className="page-card npc-profile-card">
        <header>
          <div>
            <p className="eyebrow">全局人物，当前团视角</p>
            <h3>可用性档案</h3>
          </div>
          <select
            aria-label="选择 NPC"
            value={selectedNpcId}
            onChange={(event) => setSelectedNpcId(event.target.value)}
          >
            {npcs.map((npc) => (
              <option key={npc.id} value={npc.id}>
                {npc.name}{npc.profession ? ` · ${npc.profession}` : ""}
              </option>
            ))}
          </select>
        </header>
        {selectedNpc ? (
          <form className="npc-profile-form" onSubmit={saveProfile}>
            <div className="npc-public-summary">
              <strong>{selectedNpc.name}</strong>
              <span>{selectedNpc.home_location || "未记录常驻地"}</span>
              <span>本团角色：{selectedNpc.role}</span>
              <span>关系值：{selectedNpc.relationship_score}</span>
            </div>
            <label>
              生命周期状态
              <select
                value={profile.lifecycle_state}
                onChange={(event) =>
                  setProfile((current) => ({
                    ...current,
                    lifecycle_state: event.target
                      .value as NpcAvailabilityProfile["lifecycle_state"]
                  }))
                }
              >
                <option value="unknown">未知，需要复核</option>
                <option value="active">可活动</option>
                <option value="missing">失踪</option>
                <option value="unavailable">明确不可出场</option>
              </select>
            </label>
            <div className="npc-year-grid">
              {[
                ["born_year", "出生年份"],
                ["died_year", "死亡年份"],
                ["active_from_year", "活动开始"],
                ["active_until_year", "活动结束"]
              ].map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input
                    max={9999}
                    min={1}
                    type="number"
                    value={profile[key as keyof NpcAvailabilityProfile] ?? ""}
                    onChange={(event) =>
                      setProfile((current) => ({
                        ...current,
                        [key]: numericOrNull(event.target.value)
                      }))
                    }
                  />
                </label>
              ))}
            </div>
            <label>
              可解析地点别名
              <textarea
                value={locationTagsText}
                onChange={(event) => setLocationTagsText(event.target.value)}
                placeholder="雾港旧码头，码头区"
              />
            </label>
            <label>
              职业与行业标签
              <textarea
                value={professionTagsText}
                onChange={(event) => setProfessionTagsText(event.target.value)}
                placeholder="报社线人，记者"
              />
            </label>
            <label>
              KP 私密说明
              <textarea
                value={profile.kp_notes ?? ""}
                onChange={(event) =>
                  setProfile((current) => ({ ...current, kp_notes: event.target.value }))
                }
              />
            </label>
            <button className="primary-button" disabled={busy} type="submit">
              <Save size={16} />保存 NPC 档案
            </button>
          </form>
        ) : (
          <p className="empty-state">当前团还没有已落地的 NPC。</p>
        )}
      </section>

      <section className="page-card npc-policy-card">
        <header><div><p className="eyebrow">确定性门控</p><h3>本团复现策略</h3></div><Clock3 /></header>
        {policy && (
          <form
            className="npc-policy-form"
            onSubmit={(event) => {
              event.preventDefault();
              void perform("本团复现策略", () =>
                saveNpcReappearancePolicy(campaignId, {
                  max_returning_npcs: policy.max_returning_npcs,
                  require_location_match: policy.require_location_match,
                  require_profession_match: policy.require_profession_match,
                  max_travel_minutes: policy.max_travel_minutes
                })
              );
            }}
          >
            <label>旧识出场总预算<input min={0} max={50} type="number" value={policy.max_returning_npcs} onChange={(event) => setPolicy({ ...policy, max_returning_npcs: Number(event.target.value) })} /></label>
            <label>最大旅行分钟<input min={0} max={525600} type="number" value={policy.max_travel_minutes} onChange={(event) => setPolicy({ ...policy, max_travel_minutes: Number(event.target.value) })} /></label>
            <label className="world-materialization-toggle"><input checked={policy.require_location_match} type="checkbox" onChange={(event) => setPolicy({ ...policy, require_location_match: event.target.checked })} />地点必须可解析且可达</label>
            <label className="world-materialization-toggle"><input checked={policy.require_profession_match} type="checkbox" onChange={(event) => setPolicy({ ...policy, require_profession_match: event.target.checked })} />职业必须匹配</label>
            <button className="primary-button" disabled={busy} type="submit"><Save size={16} />保存策略</button>
          </form>
        )}
      </section>

      <section className="page-card hidden-roll-card">
        <header>
          <div>
            <p className="eyebrow">按需解析 · 仅 KP 可见</p>
            <h3>NPC 暗骰</h3>
          </div>
          <Dices />
        </header>
        <p className="hidden-roll-explainer">
          系统先检查年代、状态和一次性可达性，再暗骰是否出现；不会后台模拟移动，也不会自动写入世界事实。
        </p>
        <form
          className="hidden-roll-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!selectedNpc) return;
            const destinations = hiddenDestinationsFromText(hiddenDestinations);
            setBusy(true);
            setMessage("");
            resolveNpcHiddenAppearance(campaignId, {
              idempotency_key: hiddenRollKey,
              npc_id: selectedNpc.id,
              trigger_text: hiddenTrigger,
              appearance_chance: Number(hiddenChance),
              destinations,
              profession_hint: hiddenProfession.trim() || null
            })
              .then((result) => {
                setLatestHiddenRoll(result);
                setHiddenRolls((current) => [
                  result,
                  ...current.filter((item) => item.id !== result.id)
                ]);
                setHiddenRollKey(newHiddenRollKey());
                setMessage("NPC 暗骰已完成；只有实际接触后才应落地事实。");
              })
              .catch((error) =>
                setMessage(error instanceof Error ? error.message : String(error))
              )
              .finally(() => setBusy(false));
          }}
        >
          <label>
            触发原因
            <input
              required
              maxLength={500}
              placeholder="调查员抵达中央车站并观察候车厅"
              value={hiddenTrigger}
              onChange={(event) => setHiddenTrigger(event.target.value)}
            />
          </label>
          <label>
            出现概率（%）
            <input
              required
              min={0}
              max={100}
              type="number"
              value={hiddenChance}
              onChange={(event) => setHiddenChance(event.target.value)}
            />
          </label>
          <label>
            职业上下文（可选）
            <input
              placeholder="记者"
              value={hiddenProfession}
              onChange={(event) => setHiddenProfession(event.target.value)}
            />
          </label>
          <label className="hidden-roll-destinations">
            候选地点（每行“地点|权重”，默认权重 1）
            <textarea
              required
              placeholder={"中央车站|3\n旧码头|1"}
              value={hiddenDestinations}
              onChange={(event) => setHiddenDestinations(event.target.value)}
            />
          </label>
          <button
            className="primary-button"
            disabled={busy || !selectedNpc}
            type="submit"
          >
            <Dices size={16} />执行私密暗骰
          </button>
        </form>
        {latestHiddenRoll && (
          <div className={`hidden-roll-result ${latestHiddenRoll.appears ? "appears" : "absent"}`}>
            <strong>
              {latestHiddenRoll.appears
                ? `${latestHiddenRoll.npc_name} 会出现在 ${latestHiddenRoll.selected_location_name}`
                : `${latestHiddenRoll.npc_name} 此次不出现`}
            </strong>
            <span>
              私密 d100：{latestHiddenRoll.appearance_roll} / 阈值 {latestHiddenRoll.appearance_chance}
              {latestHiddenRoll.location_roll !== null
                ? ` · 地点权重骰 ${latestHiddenRoll.location_roll}`
                : ""}
            </span>
          </div>
        )}
        <div className="hidden-roll-history">
          {hiddenRolls.slice(0, 6).map((item) => (
            <article key={item.id}>
              <div>
                <strong>{item.npc_name}</strong>
                <span>{item.trigger_text}</span>
              </div>
              <small>
                {item.appears ? `出现于 ${item.selected_location_name}` : "未出现"}
                {" · "}d100 {item.appearance_roll}/{item.appearance_chance}
              </small>
            </article>
          ))}
        </div>
      </section>

      <section className="page-card travel-graph-card">
        <header><div><p className="eyebrow">明确路线，不猜像素距离</p><h3>团级地点图谱</h3></div><MapPinned /></header>
        <form
          className="travel-location-form"
          onSubmit={(event) => {
            event.preventDefault();
            void perform("地点", async () => {
              await createTravelLocation(campaignId, {
                name: locationName,
                aliases: tagsFromText(locationAliases),
                source_kind: "manual",
                source_ref: null,
                kp_notes: ""
              });
              setLocationName("");
              setLocationAliases("");
            });
          }}
        >
          <input required placeholder="地点名称" value={locationName} onChange={(event) => setLocationName(event.target.value)} />
          <input placeholder="别名，以逗号分隔" value={locationAliases} onChange={(event) => setLocationAliases(event.target.value)} />
          <button disabled={busy} type="submit">添加地点</button>
        </form>
        <div className="travel-node-list">
          {graph.locations.map((location) => (
            <article key={location.id}>
              <div><strong>{location.name}</strong><small>{location.aliases.join(" · ") || "无别名"}</small></div>
              <button aria-label={`删除${location.name}`} disabled={busy} onClick={() => void perform("地点", () => deleteTravelLocation(campaignId, location.id))} type="button"><Trash2 size={15} /></button>
            </article>
          ))}
        </div>
        <form
          className="travel-route-form"
          onSubmit={(event) => {
            event.preventDefault();
            void perform("旅行路线", () =>
              createTravelRoute(campaignId, {
                from_location_id: routeFrom,
                to_location_id: routeTo,
                travel_minutes: Number(routeMinutes),
                travel_mode: routeMode,
                bidirectional: routeBidirectional,
                status: "open",
                kp_notes: ""
              })
            );
          }}
        >
          <select required value={routeFrom} onChange={(event) => setRouteFrom(event.target.value)}><option value="">起点</option>{graph.locations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
          <select required value={routeTo} onChange={(event) => setRouteTo(event.target.value)}><option value="">终点</option>{graph.locations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
          <input required min={1} type="number" value={routeMinutes} onChange={(event) => setRouteMinutes(event.target.value)} />
          <select value={routeMode} onChange={(event) => setRouteMode(event.target.value as typeof routeMode)}><option value="walk">步行</option><option value="drive">驾车</option><option value="rail">铁路</option><option value="boat">船运</option><option value="flight">飞行</option><option value="other">其他</option></select>
          <label className="world-materialization-toggle"><input checked={routeBidirectional} type="checkbox" onChange={(event) => setRouteBidirectional(event.target.checked)} />双向</label>
          <button disabled={busy || graph.locations.length < 2} type="submit"><Route size={15} />添加路线</button>
        </form>
        <div className="travel-route-list">
          {graph.routes.map((route) => (
            <article key={route.id}>
              <span>{route.from_name} {route.bidirectional ? "↔" : "→"} {route.to_name}</span>
              <strong>{describeMinutes(route.travel_minutes)}</strong>
              <small>{route.travel_mode} · {route.status}</small>
              <button aria-label="删除路线" disabled={busy} onClick={() => void perform("旅行路线", () => deleteTravelRoute(campaignId, route.id))} type="button"><Trash2 size={15} /></button>
            </article>
          ))}
        </div>
      </section>

      <section className="page-card travel-preview-card">
        <header><div><p className="eyebrow">Dijkstra 加权最短路径</p><h3>路线预检</h3></div><Route /></header>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setBusy(true);
            previewTravelRoute(campaignId, {
              origins: tagsFromText(previewOrigin),
              destination: previewDestination,
              max_minutes: policy?.max_travel_minutes ?? 1440
            })
              .then(setPreview)
              .catch((error) => setMessage(error instanceof Error ? error.message : String(error)))
              .finally(() => setBusy(false));
          }}
        >
          <input required placeholder="NPC 出发地点或别名" value={previewOrigin} onChange={(event) => setPreviewOrigin(event.target.value)} />
          <input required placeholder="玩家当前地点或别名" value={previewDestination} onChange={(event) => setPreviewDestination(event.target.value)} />
          <button disabled={busy} type="submit">计算最短旅行时间</button>
        </form>
        {preview && (
          <div className={`travel-preview-result ${preview.within_limit ? "reachable" : "blocked"}`}>
            <strong>{preview.status}</strong>
            <p>{preview.locations.map((item) => item.name).join(" → ") || "没有可用路径"}</p>
            {preview.total_minutes !== null && <span>{describeMinutes(preview.total_minutes)}</span>}
          </div>
        )}
      </section>
    </div>
  );
}
