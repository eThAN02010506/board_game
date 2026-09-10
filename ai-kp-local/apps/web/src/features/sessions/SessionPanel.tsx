import { Copy, RefreshCw, ShieldCheck, UserPlus, Users } from "lucide-react";
import type { FormEventHandler } from "react";
import type {
  AuthIdentity,
  SessionInfo,
  SessionMember,
  SessionSeat
} from "../../api/types";
import type { RealtimeStatus } from "../../realtime";
import { useTranslation } from "react-i18next";
import { statusLabel } from "../../ui/statusLabels";

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
  playerDisplayName: string;
  joinRole: "player" | "observer";
  members: SessionMember[];
  seats: SessionSeat[];
  recoverableSeats: SessionSeat[];
  onKpDisplayNameChange: (value: string) => void;
  onJoinCodeInputChange: (value: string) => void;
  onSeatInvitationInputChange: (value: string) => void;
  onSeatLabelChange: (value: string) => void;
  onPlayerDisplayNameChange: (value: string) => void;
  onJoinRoleChange: (value: "player" | "observer") => void;
  onRefreshIdentity: () => void;
  onSwitchIdentity: () => void;
  onRotateJoinCode: () => void;
  onRefreshMembers: () => void;
  onRefreshSeats: () => void;
  onRefreshRecoverableSeats: () => void;
  onCloseSession: () => void;
  onCreateSeat: FormEventHandler<HTMLFormElement>;
  onClaimSeat: FormEventHandler<HTMLFormElement>;
  onReissueSeat: (seatId: string) => void;
  onRevokeSeat: (seatId: string) => void;
  onRecoverSeat: (seatId: string) => void;
  onRevokeMember: (memberId: string) => void;
  onStartSession: () => void;
  onRecoverKp: () => void;
  onJoinSession: FormEventHandler<HTMLFormElement>;
  onOpenInvestigators: () => void;
};

function seatStatusLabel(t: (key: string) => string, status: SessionSeat["status"]) {
  if (status === "open") return t("sessions.statusOpen");
  if (status === "claimed") return t("sessions.statusClaimed");
  return t("sessions.statusRevoked");
}

export function SessionPanel(props: Props) {
  const { t } = useTranslation();
  return (
    <section className="tool-panel session-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{t("sessions.eyebrow")}</p>
          <h2>{t("sessions.title")}</h2>
        </div>
        <Users size={18} />
      </div>
      {props.identity && props.session ? (
        <>
          <div className="identity-card">
            <span className={`role-badge ${props.identity.role}`}>
              {props.identity.role === "kp" ? "KP" : props.identity.role === "observer" ? "观战者" : t("common.playerRole")}
            </span>
            <strong>{props.identity.display_name}</strong>
            <small>
              {t("sessions.sessionLabel")} {statusLabel(props.session.status)} · {t("sessions.investigatorLabel")}
              {props.identity.role === "observer"
                ? "只读席位"
                : props.identity.pc_id ? t("sessions.pcBound") : t("sessions.pcPending")}
            </small>
            {props.identity.seat_id && <small>{t("sessions.stableSeat")} {props.identity.seat_id}</small>}
            <small className={`realtime-note ${props.realtimeStatus}`}>{props.realtimeNote}</small>
          </div>
          <button className="ghost-button" onClick={props.onRefreshIdentity} type="button">
            <RefreshCw size={16} />
            {t("sessions.refreshIdentity")}
          </button>
          <button className="ghost-button" onClick={props.onSwitchIdentity} type="button">
            {t("sessions.switchIdentity")}
          </button>
          {props.identity.role !== "observer" && (
            <button className="ghost-button" onClick={props.onOpenInvestigators} type="button">
              {props.identity.role === "kp" ? t("sessions.goReviewBind") : t("sessions.goCreateSubmit")}
            </button>
          )}

          {props.identity.role === "kp" && (
            <>
              <div className="session-section-heading">
                <div>
                  <h3>{t("sessions.seatSectionTitle")}</h3>
                  <small>
                    {t("sessions.seatSectionHint")}
                  </small>
                </div>
                <button className="icon-button" onClick={props.onRefreshSeats} title={t("sessions.refreshSeats")} type="button">
                  <RefreshCw size={15} />
                </button>
              </div>

              <form className="seat-create-form" onSubmit={props.onCreateSeat}>
                <label>
                  {t("sessions.seatNameLabel")}
                  <input
                    placeholder={t("sessions.seatNamePlaceholder")}
                    value={props.seatLabel}
                    onChange={(event) => props.onSeatLabelChange(event.target.value)}
                  />
                </label>
                <button className="primary-button" type="submit">
                  <UserPlus size={16} />
                  {t("sessions.createSeatInvite")}
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
                          <small>{seatStatusLabel(t, seat.status)} · {seat.profile_display_name ?? t("sessions.noPlayerYet")}</small>
                        </div>
                        <span className={`seat-status ${seat.status}`}>{seatStatusLabel(t, seat.status)}</span>
                      </div>
                      {invitation && (
                        <div className="seat-invitation">
                          <code>{invitation}</code>
                          <button
                            className="icon-button"
                            onClick={() => void navigator.clipboard?.writeText(invitation)}
                            title={t("sessions.copyInvite")}
                            type="button"
                          >
                            <Copy size={14} />
                          </button>
                        </div>
                      )}
                      {seat.status === "open" && !invitation && (
                        <small className="permission-hint">{t("sessions.inviteNotStored")}</small>
                      )}
                      {seat.status !== "revoked" && (
                        <small className="permission-hint">
                          {seat.assigned_pc_id
                            ? `${t("sessions.seatBoundPc")}：${seat.pc_name ?? seat.assigned_pc_id}`
                            : t("sessions.seatUnboundHint")}
                        </small>
                      )}
                      <div className="inline-actions">
                        {seat.status === "open" && (
                          <button className="ghost-button" onClick={() => props.onReissueSeat(seat.id)} type="button">
                            {t("sessions.reissue")}
                          </button>
                        )}
                        {seat.status !== "revoked" && (
                          <button className="secondary-button" onClick={() => props.onRevokeSeat(seat.id)} type="button">
                            {t("sessions.revokeSeat")}
                          </button>
                        )}
                      </div>
                    </article>
                  );
                }) : <p className="empty-note">{t("sessions.noSeats")}</p>}
              </div>

              <details className="legacy-session-tools">
                <summary>{t("sessions.legacyToolsTitle")}</summary>
                <div className="join-code-card">
                  <small>{t("sessions.legacyJoinCodeLabel")}</small>
                  <strong>{props.visibleJoinCode || t("sessions.joinCodeNotStored")}</strong>
                </div>
                <div className="inline-actions">
                  <button className="ghost-button" onClick={props.onRotateJoinCode} type="button">{t("sessions.rotateJoinCode")}</button>
                  <button className="ghost-button" onClick={props.onRefreshMembers} type="button">{t("sessions.refreshMembers")}</button>
                  <button className="secondary-button" onClick={props.onCloseSession} type="button">{t("sessions.closeSession")}</button>
                </div>

                <div className="member-list">
                  {props.members.map((member) => (
                    <div className={`member-card ${member.revoked_at ? "revoked" : ""}`} key={member.id}>
                      <div>
                        <strong>{member.display_name}</strong>
                        <small>{member.role} · {member.revoked_at ? t("sessions.memberRevoked") : t("sessions.memberValid")}</small>
                      </div>
                      {member.role === "player" && !member.revoked_at && (
                        <div className="inline-actions">
                          <small className="permission-hint">
                            {member.pc_id ? t("sessions.memberBound") : t("sessions.memberPcPending")}
                          </small>
                          <button className="secondary-button" onClick={() => props.onRevokeMember(member.id)} type="button">{t("sessions.revokeCredential")}</button>
                        </div>
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
              <strong>{t("sessions.claimIntroTitle")}</strong>
              <small>{t("sessions.claimIntroHint")}</small>
            </div>
          </div>
          <form className="seat-claim-form" onSubmit={props.onClaimSeat}>
            <label>
              {t("sessions.seatInviteCodeLabel")}
              <input
                autoComplete="off"
                placeholder="XXXX-XXXX-XXXX"
                value={props.seatInvitationInput}
                onChange={(event) => props.onSeatInvitationInputChange(event.target.value)}
              />
            </label>
            <label>
              {t("sessions.playerDisplayNameLabel")}
              <input value={props.playerDisplayName} onChange={(event) => props.onPlayerDisplayNameChange(event.target.value)} />
            </label>
            <button className="primary-button" type="submit">{t("sessions.claimEnter")}</button>
          </form>

          <div className="recoverable-seats">
            <div className="session-section-heading">
              <div><h3>{t("sessions.mySeatsTitle")}</h3><small>{t("sessions.mySeatsHint")}</small></div>
              <button className="icon-button" onClick={props.onRefreshRecoverableSeats} title={t("sessions.refreshHistoryTitle")} type="button"><RefreshCw size={15} /></button>
            </div>
            {props.recoverableSeats.filter((seat) => seat.status === "claimed" && seat.session_status === "active").map((seat) => (
              <button className="recover-seat-card" key={seat.id} onClick={() => props.onRecoverSeat(seat.id)} type="button">
                <span><strong>{seat.campaign_title}</strong><small>{seat.label} · {seat.session_title}</small></span>
                <span>{t("sessions.recoverEnter")}</span>
              </button>
            ))}
          </div>

          <details className="legacy-session-tools">
            <summary>{t("sessions.legacyJoinDivider")}</summary>
            <label>
              {t("sessions.kpDisplayNameLabel")}
              <input value={props.kpDisplayName} onChange={(event) => props.onKpDisplayNameChange(event.target.value)} />
            </label>
            <button className="secondary-button" disabled={!props.activeCampaignPresent} onClick={props.onStartSession} type="button">{t("sessions.startKpSession")}</button>
            <button className="ghost-button" disabled={!props.activeCampaignPresent} onClick={props.onRecoverKp} type="button">
              {t("sessions.recoverKp")}
            </button>
            <small className="permission-hint">
              {t("sessions.kpRecoveryHint")}
            </small>
            <div className="session-divider">{t("sessions.legacyJoinDivider")}</div>
            <form onSubmit={props.onJoinSession}>
              <label>
                加入身份
                <select
                  value={props.joinRole}
                  onChange={(event) => props.onJoinRoleChange(event.target.value as "player" | "observer")}
                >
                  <option value="player">玩家</option>
                  <option value="observer">观战者（只读）</option>
                </select>
              </label>
              <label>
                {t("sessions.joinCodeLabel")}
                <input autoComplete="off" value={props.joinCodeInput} onChange={(event) => props.onJoinCodeInputChange(event.target.value)} />
              </label>
              <button className="secondary-button" type="submit">{t("sessions.joinWithCode")}</button>
            </form>
            <small className="permission-hint">
              {t("sessions.legacyJoinHint")}
            </small>
          </details>
        </>
      )}
    </section>
  );
}
