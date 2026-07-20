import { Plus, RefreshCw, Users } from "lucide-react";
import type { FormEventHandler } from "react";
import type {
  AuthIdentity,
  PlayerCharacter,
  SessionInfo,
  SessionMember
} from "../../api/types";
import type { RealtimeStatus } from "../../realtime";

type Props = {
  identity: AuthIdentity | null;
  session: SessionInfo | null;
  activeCampaignPresent: boolean;
  realtimeStatus: RealtimeStatus;
  realtimeNote: string;
  visibleJoinCode: string;
  kpDisplayName: string;
  joinCodeInput: string;
  playerDisplayName: string;
  joinPcId: string;
  pcName: string;
  members: SessionMember[];
  pcs: PlayerCharacter[];
  memberPcDrafts: Record<string, string>;
  onKpDisplayNameChange: (value: string) => void;
  onJoinCodeInputChange: (value: string) => void;
  onPlayerDisplayNameChange: (value: string) => void;
  onJoinPcIdChange: (value: string) => void;
  onPcNameChange: (value: string) => void;
  onMemberPcDraftChange: (memberId: string, pcId: string) => void;
  onRefreshIdentity: () => void;
  onRotateJoinCode: () => void;
  onRefreshMembers: () => void;
  onCloseSession: () => void;
  onCreatePc: FormEventHandler<HTMLFormElement>;
  onAssignMemberPc: (memberId: string) => void;
  onRevokeMember: (memberId: string) => void;
  onStartSession: () => void;
  onJoinSession: FormEventHandler<HTMLFormElement>;
};

export function SessionPanel(props: Props) {
  return (
    <section className="tool-panel session-panel">
      <div className="panel-heading">
        <h2>团会话与权限</h2>
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
            <small className={`realtime-note ${props.realtimeStatus}`}>{props.realtimeNote}</small>
          </div>
          <button className="ghost-button" onClick={props.onRefreshIdentity} type="button">
            <RefreshCw size={16} />
            刷新身份与角色绑定
          </button>

          {props.identity.role === "kp" && (
            <>
              <div className="join-code-card">
                <small>玩家加入码（只显示本次创建/轮换结果）</small>
                <strong>{props.visibleJoinCode || "明文未保存，需轮换后重新分享"}</strong>
              </div>
              <div className="inline-actions">
                <button className="ghost-button" onClick={props.onRotateJoinCode} type="button">
                  轮换加入码
                </button>
                <button className="ghost-button" onClick={props.onRefreshMembers} type="button">
                  刷新成员
                </button>
                <button className="secondary-button" onClick={props.onCloseSession} type="button">
                  关闭会话
                </button>
              </div>

              <form onSubmit={props.onCreatePc}>
                <label>
                  新建角色卡名称
                  <input
                    value={props.pcName}
                    onChange={(event) => props.onPcNameChange(event.target.value)}
                  />
                </label>
                <button className="secondary-button" type="submit">
                  <Plus size={16} />
                  创建空白角色卡
                </button>
              </form>

              <div className="member-list">
                {props.members.map((member) => (
                  <div
                    className={`member-card ${member.revoked_at ? "revoked" : ""}`}
                    key={member.id}
                  >
                    <div>
                      <strong>{member.display_name}</strong>
                      <small>
                        {member.role} · {member.revoked_at ? "已撤销" : "在线凭证有效"}
                      </small>
                    </div>
                    {member.role === "player" && !member.revoked_at && (
                      <>
                        <select
                          aria-label={`为 ${member.display_name} 选择角色卡`}
                          value={props.memberPcDrafts[member.id] ?? member.pc_id ?? ""}
                          onChange={(event) =>
                            props.onMemberPcDraftChange(member.id, event.target.value)
                          }
                        >
                          <option value="">未绑定角色卡</option>
                          {props.pcs.map((pc) => (
                            <option key={pc.id} value={pc.id}>
                              {pc.name}
                            </option>
                          ))}
                        </select>
                        <div className="inline-actions">
                          <button
                            className="ghost-button"
                            onClick={() => props.onAssignMemberPc(member.id)}
                            type="button"
                          >
                            绑定角色
                          </button>
                          <button
                            className="secondary-button"
                            onClick={() => props.onRevokeMember(member.id)}
                            type="button"
                          >
                            撤销凭证
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      ) : (
        <>
          <label>
            KP 显示名
            <input
              value={props.kpDisplayName}
              onChange={(event) => props.onKpDisplayNameChange(event.target.value)}
            />
          </label>
          <button
            className="primary-button"
            disabled={!props.activeCampaignPresent}
            onClick={props.onStartSession}
            type="button"
          >
            为当前团开启 KP 会话
          </button>
          <div className="session-divider">或作为玩家加入</div>
          <form onSubmit={props.onJoinSession}>
            <label>
              加入码
              <input
                autoComplete="off"
                placeholder="XXXX-XXXX-XXXX"
                value={props.joinCodeInput}
                onChange={(event) => props.onJoinCodeInputChange(event.target.value)}
              />
            </label>
            <label>
              玩家显示名
              <input
                value={props.playerDisplayName}
                onChange={(event) => props.onPlayerDisplayNameChange(event.target.value)}
              />
            </label>
            <label>
              角色卡 ID（可留空，由 KP 分配）
              <input
                value={props.joinPcId}
                onChange={(event) => props.onJoinPcIdChange(event.target.value)}
              />
            </label>
            <button className="secondary-button" type="submit">
              加入团会话
            </button>
          </form>
        </>
      )}
    </section>
  );
}
