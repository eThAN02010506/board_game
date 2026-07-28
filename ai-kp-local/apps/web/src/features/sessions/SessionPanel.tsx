import { Copy, Plus, RefreshCw, ShieldCheck, UserPlus, Users } from "lucide-react";
import type { FormEventHandler } from "react";
import type {
  AuthIdentity,
  PlayerCharacter,
  SessionInfo,
  SessionMember,
  SessionSeat
} from "../../api/types";
import type { RealtimeStatus } from "../../realtime";

type Props = {
  identity: AuthIdentity | null;
  session: SessionInfo | null;
  activeCampaignPresent: boolean;
  realtimeStatus: RealtimeStatus;
  realtimeNote: string;
  visibleJoinCode: string;
  visibleSeatInvites: Record<string, string>;
  kpDisplayName: string;
  joinCodeInput: string;
  seatInvitationInput: string;
  seatLabel: string;
  seatPcId: string;
  playerDisplayName: string;
  joinPcId: string;
  pcName: string;
  members: SessionMember[];
  seats: SessionSeat[];
  recoverableSeats: SessionSeat[];
  pcs: PlayerCharacter[];
  memberPcDrafts: Record<string, string>;
  onKpDisplayNameChange: (value: string) => void;
  onJoinCodeInputChange: (value: string) => void;
  onSeatInvitationInputChange: (value: string) => void;
  onSeatLabelChange: (value: string) => void;
  onSeatPcIdChange: (value: string) => void;
  onPlayerDisplayNameChange: (value: string) => void;
  onJoinPcIdChange: (value: string) => void;
  onPcNameChange: (value: string) => void;
  onMemberPcDraftChange: (memberId: string, pcId: string) => void;
  onRefreshIdentity: () => void;
  onRotateJoinCode: () => void;
  onRefreshMembers: () => void;
  onRefreshSeats: () => void;
  onRefreshRecoverableSeats: () => void;
  onCloseSession: () => void;
  onCreatePc: FormEventHandler<HTMLFormElement>;
  onCreateSeat: FormEventHandler<HTMLFormElement>;
  onClaimSeat: FormEventHandler<HTMLFormElement>;
  onAssignMemberPc: (memberId: string) => void;
  onAssignSeatPc: (seatId: string, pcId: string) => void;
  onReissueSeat: (seatId: string) => void;
  onRevokeSeat: (seatId: string) => void;
  onRecoverSeat: (seatId: string) => void;
  onRevokeMember: (memberId: string) => void;
  onStartSession: () => void;
  onRecoverKp: () => void;
  onJoinSession: FormEventHandler<HTMLFormElement>;
};

function seatStatusLabel(status: SessionSeat["status"]) {
  if (status === "open") return "等待认领";
  if (status === "claimed") return "已认领";
  return "已撤销";
}

export function SessionPanel(props: Props) {
  return (
    <section className="tool-panel session-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">稳定玩家身份</p>
          <h2>团会话与逐席邀请</h2>
        </div>
        <Users size={18} />
      </div>
      {props.identity && props.session ? (
        <>
          <div className="identity-card">
            <span className={`role-badge ${props.identity.role}`}>
              {props.identity.role === "kp" ? "KP" : "玩家"}
            </span>
            <strong>{props.identity.display_name}</strong>
            <small>
              会话 {props.session.status} · 角色卡 {props.identity.pc_id ?? "待 KP 分配"}
            </small>
            {props.identity.seat_id && <small>稳定席位 {props.identity.seat_id}</small>}
            <small className={`realtime-note ${props.realtimeStatus}`}>{props.realtimeNote}</small>
          </div>
          <button className="ghost-button" onClick={props.onRefreshIdentity} type="button">
            <RefreshCw size={16} />
            刷新身份与角色绑定
          </button>

          {props.identity.role === "kp" && (
            <>
              <div className="session-section-heading">
                <div>
                  <h3>玩家席位</h3>
                  <small>每个邀请码只属于一个席位，认领后立即失效。</small>
                </div>
                <button className="icon-button" onClick={props.onRefreshSeats} title="刷新席位" type="button">
                  <RefreshCw size={15} />
                </button>
              </div>

              <form className="seat-create-form" onSubmit={props.onCreateSeat}>
                <label>
                  席位名称
                  <input
                    placeholder="例如：玩家 1 / 林若川"
                    value={props.seatLabel}
                    onChange={(event) => props.onSeatLabelChange(event.target.value)}
                  />
                </label>
                <label>
                  预留角色（可选）
                  <select
                    value={props.seatPcId}
                    onChange={(event) => props.onSeatPcIdChange(event.target.value)}
                  >
                    <option value="">暂不绑定</option>
                    {props.pcs.map((pc) => <option key={pc.id} value={pc.id}>{pc.name}</option>)}
                  </select>
                </label>
                <button className="primary-button" type="submit">
                  <UserPlus size={16} />
                  创建单席邀请
                </button>
              </form>

              <div className="seat-list">
                {props.seats.length ? props.seats.map((seat) => {
                  const invitation = props.visibleSeatInvites[seat.id];
                  return (
                    <article className={`seat-card ${seat.status}`} key={seat.id}>
                      <div className="seat-card-heading">
                        <div>
                          <strong>{seat.label}</strong>
                          <small>{seatStatusLabel(seat.status)} · {seat.profile_display_name ?? "尚无玩家"}</small>
                        </div>
                        <span className={`seat-status ${seat.status}`}>{seatStatusLabel(seat.status)}</span>
                      </div>
                      {invitation && (
                        <div className="seat-invitation">
                          <code>{invitation}</code>
                          <button
                            className="icon-button"
                            onClick={() => void navigator.clipboard?.writeText(invitation)}
                            title="复制邀请码"
                            type="button"
                          >
                            <Copy size={14} />
                          </button>
                        </div>
                      )}
                      {seat.status === "open" && !invitation && (
                        <small className="permission-hint">明文邀请码不会入库；需要时请重新签发。</small>
                      )}
                      {seat.status !== "revoked" && (
                        <label>
                          该席位角色
                          <select
                            value={seat.assigned_pc_id ?? ""}
                            onChange={(event) => props.onAssignSeatPc(seat.id, event.target.value)}
                          >
                            <option value="">未绑定角色卡</option>
                            {props.pcs.map((pc) => <option key={pc.id} value={pc.id}>{pc.name}</option>)}
                          </select>
                        </label>
                      )}
                      <div className="inline-actions">
                        {seat.status === "open" && (
                          <button className="ghost-button" onClick={() => props.onReissueSeat(seat.id)} type="button">
                            重新签发
                          </button>
                        )}
                        {seat.status !== "revoked" && (
                          <button className="secondary-button" onClick={() => props.onRevokeSeat(seat.id)} type="button">
                            撤销此席
                          </button>
                        )}
                      </div>
                    </article>
                  );
                }) : <p className="empty-note">还没有席位。创建后把该席专属邀请码发给对应玩家。</p>}
              </div>

              <details className="legacy-session-tools">
                <summary>旧版共享加入码与成员管理</summary>
                <div className="join-code-card">
                  <small>共享加入码（兼容旧流程）</small>
                  <strong>{props.visibleJoinCode || "明文未保存，需轮换后重新分享"}</strong>
                </div>
                <div className="inline-actions">
                  <button className="ghost-button" onClick={props.onRotateJoinCode} type="button">轮换共享码</button>
                  <button className="ghost-button" onClick={props.onRefreshMembers} type="button">刷新成员</button>
                  <button className="secondary-button" onClick={props.onCloseSession} type="button">关闭会话</button>
                </div>

                <form onSubmit={props.onCreatePc}>
                  <label>
                    新建兼容角色卡名称
                    <input value={props.pcName} onChange={(event) => props.onPcNameChange(event.target.value)} />
                  </label>
                  <button className="secondary-button" type="submit"><Plus size={16} />创建空白角色卡</button>
                </form>

                <div className="member-list">
                  {props.members.map((member) => (
                    <div className={`member-card ${member.revoked_at ? "revoked" : ""}`} key={member.id}>
                      <div>
                        <strong>{member.display_name}</strong>
                        <small>{member.role} · {member.revoked_at ? "已撤销" : "在线凭证有效"}</small>
                      </div>
                      {member.role === "player" && !member.revoked_at && (
                        <>
                          <select
                            aria-label={`为 ${member.display_name} 选择角色卡`}
                            value={props.memberPcDrafts[member.id] ?? member.pc_id ?? ""}
                            onChange={(event) => props.onMemberPcDraftChange(member.id, event.target.value)}
                          >
                            <option value="">未绑定角色卡</option>
                            {props.pcs.map((pc) => <option key={pc.id} value={pc.id}>{pc.name}</option>)}
                          </select>
                          <div className="inline-actions">
                            <button className="ghost-button" onClick={() => props.onAssignMemberPc(member.id)} type="button">绑定角色</button>
                            <button className="secondary-button" onClick={() => props.onRevokeMember(member.id)} type="button">撤销凭证</button>
                          </div>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              </details>
            </>
          )}
        </>
      ) : (
        <>
          <div className="seat-claim-intro">
            <ShieldCheck size={22} />
            <div>
              <strong>使用 KP 发给你的专属席位邀请码</strong>
              <small>首次认领会在此浏览器建立长期玩家身份，以后可直接恢复。</small>
            </div>
          </div>
          <form className="seat-claim-form" onSubmit={props.onClaimSeat}>
            <label>
              席位邀请码
              <input
                autoComplete="off"
                placeholder="XXXX-XXXX-XXXX"
                value={props.seatInvitationInput}
                onChange={(event) => props.onSeatInvitationInputChange(event.target.value)}
              />
            </label>
            <label>
              玩家显示名
              <input value={props.playerDisplayName} onChange={(event) => props.onPlayerDisplayNameChange(event.target.value)} />
            </label>
            <button className="primary-button" type="submit">认领并进入席位</button>
          </form>

          <div className="recoverable-seats">
            <div className="session-section-heading">
              <div><h3>我的历史席位</h3><small>只显示此浏览器长期身份拥有的席位。</small></div>
              <button className="icon-button" onClick={props.onRefreshRecoverableSeats} title="刷新历史席位" type="button"><RefreshCw size={15} /></button>
            </div>
            {props.recoverableSeats.filter((seat) => seat.status === "claimed" && seat.session_status === "active").map((seat) => (
              <button className="recover-seat-card" key={seat.id} onClick={() => props.onRecoverSeat(seat.id)} type="button">
                <span><strong>{seat.campaign_title}</strong><small>{seat.label} · {seat.session_title}</small></span>
                <span>恢复进入</span>
              </button>
            ))}
          </div>

          <details className="legacy-session-tools">
            <summary>创建 KP 会话或使用旧版共享码</summary>
            <label>
              KP 显示名
              <input value={props.kpDisplayName} onChange={(event) => props.onKpDisplayNameChange(event.target.value)} />
            </label>
            <button className="secondary-button" disabled={!props.activeCampaignPresent} onClick={props.onStartSession} type="button">为当前团开启 KP 会话</button>
            <button className="ghost-button" disabled={!props.activeCampaignPresent} onClick={props.onRecoverKp} type="button">
              重签并恢复当前团 KP
            </button>
            <small className="permission-hint">
              仅限本机管理员。重签会使该 KP 的旧访问令牌失效，但不会关闭会话或删除团数据。
            </small>
            <div className="session-divider">旧版玩家加入</div>
            <form onSubmit={props.onJoinSession}>
              <label>
                共享加入码
                <input autoComplete="off" value={props.joinCodeInput} onChange={(event) => props.onJoinCodeInputChange(event.target.value)} />
              </label>
              <label>
                角色卡 ID（可留空）
                <input value={props.joinPcId} onChange={(event) => props.onJoinPcIdChange(event.target.value)} />
              </label>
              <button className="secondary-button" type="submit">使用共享码加入</button>
            </form>
          </details>
        </>
      )}
    </section>
  );
}
