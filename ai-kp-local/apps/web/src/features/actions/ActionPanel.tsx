import { Brain, MessageSquare, RefreshCw, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ActionAdjudication, AuthIdentity, AutoKpJob, PlayerActionRecord } from "../../api/types";
import { statusLabel } from "../../ui/statusLabels";

type Props = {
  identity: AuthIdentity | null;
  loading: boolean;
  playerAction: string;
  proposalText: string;
  playerActions: PlayerActionRecord[];
  selectedPlayerActionId: string;
  autoKpEnabled: boolean;
  autoConfirmAdjudication: boolean;
  autoKpJobs: AutoKpJob[];
  adjudication: ActionAdjudication | null;
  onPlayerActionChange: (value: string) => void;
  onProposalTextChange: (value: string) => void;
  onAutoConfirmAdjudicationChange: (value: boolean) => void;
  onAutoKpEnabledChange: (value: boolean) => void;
  onSelectPlayerAction: (action: PlayerActionRecord) => void;
  onSearchMemory: () => void;
  onSubmitPlayerAction: () => void;
  onRefreshPlayerActions: () => void;
  onRetryAutoKpJob: (jobId: string) => void;
  onCreateProposal: () => void;
  onGenerateAiProposal: () => void;
  onConfirmAdjudication: (selectedSkill: string | null) => void;
  onReviseAdjudication: () => void;
};

export function ActionPanel(props: Props) {
  const { t } = useTranslation();
  const [selectedSkill, setSelectedSkill] = useState("");
  useEffect(() => {
    setSelectedSkill(props.adjudication?.selected_skill ?? "");
  }, [props.adjudication?.id, props.adjudication?.selected_skill]);
  const selectedOption = props.adjudication?.skill_options.find(
    (option) => option.skill_name === selectedSkill
  );
  return (
    <section className="tool-panel action-panel" id="memory-section">
      <div className="panel-heading">
        <h2>{t("actions.chatTitle")}</h2>
        <Brain size={18} />
      </div>
      <label>
        {t("actions.playerActionLabel")}
        <textarea
          value={props.playerAction}
          onChange={(event) => props.onPlayerActionChange(event.target.value)}
        />
      </label>
      <button
        className="secondary-button"
        disabled={!props.identity}
        onClick={props.onSearchMemory}
        type="button"
      >
        <Brain size={16} />
        {t("actions.searchMemory")}
      </button>
      {props.identity?.role === "player" && (
        <>
          <label className="checkbox-row">
            <input
              checked={props.autoKpEnabled}
              onChange={(event) => props.onAutoKpEnabledChange(event.target.checked)}
              type="checkbox"
            />
            {t("actions.autoKpLabel")}
          </label>
          {props.autoKpEnabled && (
            <label className="checkbox-row">
              <input
                checked={props.autoConfirmAdjudication}
                onChange={(event) => props.onAutoConfirmAdjudicationChange(event.target.checked)}
                type="checkbox"
              />
              {t("actions.autoConfirmLabel")}
            </label>
          )}
          <button
            className="primary-button"
            disabled={props.loading}
            onClick={props.onSubmitPlayerAction}
            type="button"
          >
            <Send size={16} />
            {props.autoKpEnabled ? t("actions.submitAndAdvance") : t("actions.submitToKp")}
          </button>
          {props.adjudication?.status === "pending" && (
            <article className={`action-ruling-card ${props.adjudication.mode}`}>
              <div className="action-ruling-heading">
                <strong>{modeLabel(t, props.adjudication.mode)}</strong>
                <small>{t("actions.initialRuling")}</small>
              </div>
              <p>{props.adjudication.reason}</p>
              {(props.adjudication.ruling.goal || props.adjudication.ruling.method) && (
                <dl className="action-ruling-details">
                  {props.adjudication.ruling.goal && <><dt>{t("proposals.goal")}</dt><dd>{props.adjudication.ruling.goal}</dd></>}
                  {props.adjudication.ruling.method && <><dt>{t("proposals.method")}</dt><dd>{props.adjudication.ruling.method}</dd></>}
                  {props.adjudication.ruling.target && <><dt>{t("proposals.target")}</dt><dd>{props.adjudication.ruling.target}</dd></>}
                  {props.adjudication.ruling.maximum_effect && <><dt>{t("proposals.maximumEffect")}</dt><dd>{props.adjudication.ruling.maximum_effect}</dd></>}
                </dl>
              )}
              {props.adjudication.source_error && (
                <p className="permission-hint">{t("actions.validationBlocked")}</p>
              )}
              {props.adjudication.mode === "skill_check" && (
                <label>
                  {t("actions.selectSkill")}
                  <select
                    aria-label={t("actions.selectSkill")}
                    value={selectedSkill}
                    onChange={(event) => setSelectedSkill(event.target.value)}
                  >
                    {props.adjudication.skill_options.map((option) => (
                      <option key={option.skill_key} value={option.skill_name}>
                        {option.skill_name} · {option.target} · {difficultyLabel(t, option.difficulty)}
                      </option>
                    ))}
                  </select>
                  {selectedOption && <small>{selectedOption.reason}</small>}
                </label>
              )}
              {props.adjudication.prompt && <p>{props.adjudication.prompt}</p>}
              {props.adjudication.mode === "roleplay_or_clarification" && (
                <p className="permission-hint">{t("actions.roleplayHint")}</p>
              )}
              <div className="action-ruling-actions">
                {props.adjudication.mode !== "roleplay_or_clarification" && (
                  <button
                    className="primary-button"
                    disabled={props.loading || (props.adjudication.mode === "skill_check" && !selectedSkill)}
                    onClick={() => props.onConfirmAdjudication(selectedSkill || null)}
                    type="button"
                  >
                    {t("actions.confirmRuling")}
                  </button>
                )}
                <button className="secondary-button" disabled={props.loading} onClick={props.onReviseAdjudication} type="button">
                  {t("actions.reviseRuling")}
                </button>
              </div>
            </article>
          )}
          {props.autoKpJobs.length > 0 && (
            <div aria-label={t("actions.autoKpTaskLabel")} className="auto-kp-player-status">
              <strong>{t("actions.autoKpStatus")}</strong>
              <ul className="module-job-list">
                {props.autoKpJobs.slice(0, 3).map((job) => (
                  <li className={`module-job ${job.status}`} key={job.id}>
                    <div>
                      <strong>{job.job_type === "check_consequence" ? t("actions.jobCheckConsequence") : t("actions.jobPlayerAction")}</strong>
                      <span>{job.status} · {job.stage}</span>
                    </div>
                    <small>{t("actions.attempt", { current: job.attempt_count, max: job.max_attempts })}</small>
                    {job.status === "failed" && (
                      <button
                        className="ghost-button"
                        disabled={props.loading}
                        onClick={() => props.onRetryAutoKpJob(job.id)}
                        type="button"
                      >
                        <RefreshCw size={14} />{t("actions.retry")}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      {props.identity?.role === "kp" && (
        <>
          <div className="queue-heading">
            <strong>{t("actions.playerQueue")}</strong>
            <button className="ghost-button" onClick={props.onRefreshPlayerActions} type="button">
              <RefreshCw size={15} />
              {t("common.refresh")}
            </button>
          </div>
          <div className="list-stack compact-list">
            {props.playerActions.length ? (
              props.playerActions.map((action) => (
                <button
                  className={`record-button ${action.id === props.selectedPlayerActionId ? "selected" : ""}`}
                  disabled={action.status !== "submitted"}
                  key={action.id}
                  onClick={() => props.onSelectPlayerAction(action)}
                  type="button"
                >
                  <span>
                    {action.display_name ?? action.pc_id ?? t("actions.unboundPlayer")} ·{" "}
                    {statusLabel(action.status)}
                  </span>
                  <small>{action.action_text}</small>
                </button>
              ))
            ) : (
              <small>{t("actions.noSubmissions")}</small>
            )}
          </div>
          <label>
            {t("actions.proposalLabel")}
            <textarea
              value={props.proposalText}
              onChange={(event) => props.onProposalTextChange(event.target.value)}
            />
          </label>
          <button className="primary-button" onClick={props.onCreateProposal} type="button">
            <MessageSquare size={16} />
            {t("actions.createManualDraft")}
          </button>
          <button
            className="primary-button"
            disabled={props.loading}
            onClick={props.onGenerateAiProposal}
            type="button"
          >
            <Send size={16} />
            {t("actions.callAi")}
          </button>
        </>
      )}
      {!props.identity && <p className="permission-hint">{t("actions.joinHint")}</p>}
    </section>
  );
}

function modeLabel(t: (key: string) => string, mode: ActionAdjudication["mode"]) {
  if (mode === "direct_resolution") return t("actions.modeDirect");
  if (mode === "skill_check") return t("actions.modeSkillCheck");
  return t("actions.modeRoleplay");
}

function difficultyLabel(t: (key: string) => string, value: string) {
  const key = `gamedata.difficulties.${value}`;
  const translated = t(key);
  if (translated !== key) return translated;
  if (value === "opposed") return t("actions.difficultyOpposed");
  return value;
}
