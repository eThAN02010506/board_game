import { useEffect, useState } from "react";

import {
  confirmSessionZero,
  resolveSessionSafety,
  saveSessionZeroConfig,
  saveSessionZeroPreferences,
  triggerSessionSafety
} from "../../api/client";
import type {
  AuthIdentity,
  Campaign,
  CampaignSetupConfig,
  SessionZeroView
} from "../../api/types";

type Props = {
  campaign: Campaign;
  identity: AuthIdentity;
  view: SessionZeroView | null;
  loading: boolean;
  error: string;
  onChanged: () => void;
};

const lines = (value: string) => value.split("\n").map((item) => item.trim()).filter(Boolean);

function defaultConfig(campaign: Campaign): CampaignSetupConfig {
  return {
    ruleset_id: campaign.ruleset_id ?? "coc7-keeper-cn-2002c",
    ruleset_version: campaign.ruleset_version ?? "2002c",
    worldview: "",
    hosting_mode: "ai_kp",
    expected_player_count: 4,
    campaign_type: "ongoing",
    starting_power: "standard",
    allowed_character_options: [],
    house_rules: [],
    default_visibility: "party",
    style: { roleplay: 3, exploration: 3, combat: 2, tone: "mixed" },
    content_warnings: [],
    lines: [],
    veils: [],
    safety_default: "pause",
    idle_policy: "wait",
    idle_timeout_seconds: 300,
    allow_player_whispers: false
  };
}

export function SessionZeroPanel(props: Props) {
  const [config, setConfig] = useState(() => defaultConfig(props.campaign));
  const [publicStyle, setPublicStyle] = useState("");
  const [privateStyle, setPrivateStyle] = useState("");
  const [privateLines, setPrivateLines] = useState("");
  const [privateVeils, setPrivateVeils] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (props.view?.revision?.config) setConfig(props.view.revision.config);
  }, [props.view?.revision?.id]);

  async function act(callback: () => Promise<unknown>) {
    setBusy(true);
    setMessage("");
    try {
      await callback();
      props.onChanged();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  const revision = props.view?.revision;
  return (
    <section className={`session-zero-panel ${props.view?.ready ? "ready" : "pending"}`}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Session 0 · 安全与团契约</p>
          <h2>{props.view?.ready ? "全桌已确认" : "正式游玩前需要确认"}</h2>
        </div>
        <strong>{props.view ? `${props.view.confirmed_count}/${props.view.required_count}` : "…"}</strong>
      </div>
      {props.error && <p className="error-text">{props.error}</p>}
      {revision && (
        <div className="session-zero-summary">
          <span>版本 {revision.version}</span>
          <span>{revision.config.hosting_mode}</span>
          <span>{revision.config.campaign_type}</span>
          <span>{revision.config.default_visibility}</span>
          <span>挂机：{revision.config.idle_policy} · {revision.config.idle_timeout_seconds} 秒</span>
        </div>
      )}

      {props.identity.role === "kp" && (
        <details open={!revision}>
          <summary>Campaign 设置与公共边界</summary>
          <div className="session-zero-form-grid">
            <label>世界观<textarea value={config.worldview} onChange={(event) => setConfig({ ...config, worldview: event.target.value })} /></label>
            <label>主持模式<select value={config.hosting_mode} onChange={(event) => setConfig({ ...config, hosting_mode: event.target.value as CampaignSetupConfig["hosting_mode"] })}><option value="ai_kp">AI KP</option><option value="hybrid">混合</option><option value="human_kp">真人 KP</option></select></label>
            <label>预计玩家数<input min={1} max={12} type="number" value={config.expected_player_count} onChange={(event) => setConfig({ ...config, expected_player_count: Number(event.target.value) })} /></label>
            <label>Campaign 类型<input value={config.campaign_type} onChange={(event) => setConfig({ ...config, campaign_type: event.target.value })} /></label>
            <label>起始能力<input value={config.starting_power} onChange={(event) => setConfig({ ...config, starting_power: event.target.value })} /></label>
            <label>默认可见性<select value={config.default_visibility} onChange={(event) => setConfig({ ...config, default_visibility: event.target.value as CampaignSetupConfig["default_visibility"] })}><option value="party">队伍</option><option value="public">公开</option><option value="private_by_default">默认私密</option></select></label>
            <label>允许角色选项（每行一项）<textarea value={config.allowed_character_options.join("\n")} onChange={(event) => setConfig({ ...config, allowed_character_options: lines(event.target.value) })} /></label>
            <label>House Rules（每行一项）<textarea value={config.house_rules.join("\n")} onChange={(event) => setConfig({ ...config, house_rules: lines(event.target.value) })} /></label>
            <label>内容预警（每行一项）<textarea value={config.content_warnings.join("\n")} onChange={(event) => setConfig({ ...config, content_warnings: lines(event.target.value) })} /></label>
            <label>Lines（绝不出现）<textarea value={config.lines.join("\n")} onChange={(event) => setConfig({ ...config, lines: lines(event.target.value) })} /></label>
            <label>Veils（淡出处理）<textarea value={config.veils.join("\n")} onChange={(event) => setConfig({ ...config, veils: lines(event.target.value) })} /></label>
            <label>挂机策略<select value={config.idle_policy} onChange={(event) => setConfig({ ...config, idle_policy: event.target.value as CampaignSetupConfig["idle_policy"] })}><option value="wait">等待</option><option value="skip">跳过</option><option value="defend">防御</option><option value="delegate">代管</option><option value="pause">暂停</option></select></label>
            <label>挂机判定时间（秒）<input min={30} max={3600} type="number" value={config.idle_timeout_seconds} onChange={(event) => setConfig({ ...config, idle_timeout_seconds: Number(event.target.value) })} /></label>
            <label className="checkbox-label"><input type="checkbox" checked={config.allow_player_whispers} onChange={(event) => setConfig({ ...config, allow_player_whispers: event.target.checked })} />允许玩家之间私信</label>
          </div>
          <button className="primary-button" disabled={busy} onClick={() => void act(() => saveSessionZeroConfig(props.campaign.id, { ...config, expected_version: revision?.version ?? 0 }))} type="button">保存新版本并确认</button>
        </details>
      )}

      {revision && (
        <details>
          <summary>我的风格与私密边界</summary>
          <label>可公开风格偏好<textarea value={publicStyle} onChange={(event) => setPublicStyle(event.target.value)} /></label>
          <label>私密风格偏好<textarea value={privateStyle} onChange={(event) => setPrivateStyle(event.target.value)} /></label>
          <label>我的 Lines（不会标记是谁）<textarea value={privateLines} onChange={(event) => setPrivateLines(event.target.value)} /></label>
          <label>我的 Veils（不会标记是谁）<textarea value={privateVeils} onChange={(event) => setPrivateVeils(event.target.value)} /></label>
          <button className="secondary-button" disabled={busy} onClick={() => void act(() => saveSessionZeroPreferences(props.campaign.id, { expected_version: revision.version, public_style: publicStyle ? { notes: publicStyle } : {}, private_style: privateStyle ? { notes: privateStyle } : {}, lines: lines(privateLines), veils: lines(privateVeils) }))} type="button">保存私密偏好并创建待确认版本</button>
        </details>
      )}

      {revision && !props.view?.confirmed && (
        <button className="primary-button" disabled={busy} onClick={() => void act(() => confirmSessionZero(props.campaign.id, revision.id, revision.version))} type="button">确认当前 Session 0</button>
      )}
      {message && <p className="error-text">{message}</p>}

      <div className="safety-tool-row" aria-label="即时安全工具">
        <strong>需要立即调整内容？无需解释原因：</strong>
        {(["pause", "fade", "change", "rewind"] as const).map((kind) => (
          <button className="safety-tool-button" disabled={busy || Boolean(props.view?.active_safety_event)} key={kind} onClick={() => void act(() => triggerSessionSafety(props.campaign.id, kind))} type="button">{{ pause: "暂停", fade: "淡出", change: "改内容", rewind: "回退" }[kind]}</button>
        ))}
      </div>
      {props.view?.active_safety_event && (
        <div className="safety-pause-card" role="alert">
          <strong>{props.view.active_safety_event.public_message}</strong>
          {props.view.can_resolve_safety && (
            <div className="inline-actions">
              {(["fade", "change", "rewind", "resume"] as const).map((kind) => (
                <button disabled={busy} key={kind} onClick={() => void act(() => resolveSessionSafety(props.campaign.id, props.view!.active_safety_event!.id, kind))} type="button">{{ fade: "淡出后继续", change: "改写后继续", rewind: "回退后继续", resume: "已处理，继续" }[kind]}</button>
              ))}
            </div>
          )}
        </div>
      )}
      {props.loading && <small>正在同步 Session 0…</small>}
    </section>
  );
}
