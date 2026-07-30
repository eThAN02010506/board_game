import { Check, Plus, Route, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { requestJson } from "../../api/client";
import type { AuthIdentity, MapRoutePlan, SavedMap } from "../../api/types";

type Props = { map: SavedMap | null; identity: AuthIdentity | null };
type DraftRoute = { token_id: string; token_label: string; waypoints: string[] };

export function RoutePlanPanel({ map, identity }: Props) {
  const [plans, setPlans] = useState<MapRoutePlan[]>([]);
  const [drafts, setDrafts] = useState<DraftRoute[]>([]);
  const [tokenId, setTokenId] = useState("");
  const [nextLocation, setNextLocation] = useState("");
  const [title, setTitle] = useState("分队行动计划");
  const [message, setMessage] = useState("规划不会立即移动棋子；实际移动会逐段核销。");

  const tokens = useMemo(() => map?.tokens ?? [], [map?.tokens]);
  const selected = tokens.find((token) => token.id === tokenId) ?? tokens[0];
  const selectedDraft = drafts.find((draft) => draft.token_id === selected?.id);
  const routeStart = selectedDraft?.waypoints[selectedDraft.waypoints.length - 1] ?? selected?.location_name;
  const destinations = useMemo(() => {
    if (!routeStart) return [];
    const names = new Set<string>();
    map?.routes?.forEach((route) => {
      if (route.start_name === routeStart) names.add(route.end_name);
      if (route.end_name === routeStart) names.add(route.start_name);
    });
    return [...names];
  }, [map?.routes, routeStart]);

  async function load() {
    if (!map || !identity) return;
    try {
      setPlans(await requestJson(`/maps/${map.id}/route-plans`));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  useEffect(() => {
    setDrafts([]);
    setTokenId(tokens[0]?.id ?? "");
    setNextLocation(map?.locations?.[0]?.name ?? "");
    void load();
  }, [map?.id, identity?.member_id]);

  useEffect(() => {
    setNextLocation(destinations[0] ?? "");
  }, [selected?.id, routeStart, destinations.join("\u0000")]);

  function addDraft() {
    if (!selected || !nextLocation || nextLocation === routeStart) return;
    setDrafts((current) => {
      const existing = current.find((item) => item.token_id === selected.id);
      const updated = existing
        ? { ...existing, waypoints: [...existing.waypoints, nextLocation] }
        : {
            token_id: selected.id,
            token_label: selected.label,
            waypoints: [selected.location_name, nextLocation]
          };
      return [...current.filter((item) => item.token_id !== selected.id), updated];
    });
  }

  async function save() {
    if (!map) return;
    try {
      await requestJson(`/maps/${map.id}/route-plans`, {
        method: "POST",
        body: JSON.stringify({
          title,
          note: "",
          token_routes: drafts.map(({ token_id, waypoints }) => ({ token_id, waypoints }))
        })
      });
      setDrafts([]);
      setMessage(identity?.role === "kp" ? "路线已批准，可按计划移动。" : "路线已提交，等待 KP 批准。");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function transition(plan: MapRoutePlan, status: MapRoutePlan["status"]) {
    await requestJson(`/map-route-plans/${plan.id}`, {
      method: "PATCH",
      body: JSON.stringify({ expected_version: plan.version, status })
    });
    await load();
  }

  if (!map || !identity) return null;
  return (
    <section className="tool-panel route-plan-panel">
      <div className="panel-heading"><div><p className="eyebrow">先规划 · 后移动</p><h2>分队路线</h2></div><Route size={18} /></div>
      <p className="form-hint">{message}</p>
      <label>计划名称<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <div className="route-plan-builder">
        <label>棋子<select value={selected?.id ?? ""} onChange={(event) => setTokenId(event.target.value)}>
          {tokens.map((token) => <option key={token.id} value={token.id}>{token.label} @ {token.location_name}</option>)}
        </select></label>
        <label>本段终点<select value={nextLocation} onChange={(event) => setNextLocation(event.target.value)}>
          {destinations.map((location) => <option key={location} value={location}>{location}</option>)}
        </select></label>
        <button disabled={!selected || !nextLocation} onClick={addDraft}><Plus size={14} />加入方案</button>
      </div>
      <div className="route-drafts">
        {drafts.map((draft) => <span key={draft.token_id}>{draft.token_label}: {draft.waypoints.join(" → ")}<button aria-label={`移除${draft.token_label}`} onClick={() => setDrafts((items) => items.filter((item) => item.token_id !== draft.token_id))}><X size={12} /></button></span>)}
      </div>
      <button className="primary-button" disabled={!drafts.length || !title.trim()} onClick={() => void save()}>
        <Check size={14} />{identity.role === "kp" ? "批准并保存" : "提交 KP 审批"}
      </button>
      <div className="route-plan-list">
        {plans.map((plan) => <article key={plan.id}>
          <header><strong>{plan.title}</strong><small>{plan.status}</small></header>
          {plan.legs.map((leg) => <small key={leg.id}>{leg.token_label}: {leg.from_location_name} → {leg.to_location_name} · {leg.status}</small>)}
          {identity.role === "kp" && <div className="inline-actions">
            {plan.status === "proposed" && <button onClick={() => void transition(plan, "approved")}>批准</button>}
            {["proposed", "approved", "executing"].includes(plan.status) && <button onClick={() => void transition(plan, "cancelled")}>取消</button>}
          </div>}
        </article>)}
      </div>
    </section>
  );
}
