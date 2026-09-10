import { Brain, MessageSquare, RefreshCw, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { listManualKernelCandidates } from "../../api/client";
import type { ActionAdjudication, ActionAdjudicationView, AuthIdentity, AutoKpJob, ManualKernelCandidate, ModulePlayState, ParallelActionPlayerBatch, ParallelActionPlayerRegather, PlayerActionRecord, PublicTurn } from "../../api/types";
import { ActorExecutionTraceList } from "../../shared/ActorExecutionTraceList";
import { statusLabel } from "../../ui/statusLabels";
import { ParallelActionBatchStatus } from "./ParallelActionBatchStatus";
import { ParallelActionRegatherStatus } from "./ParallelActionRegatherStatus";

type Props = {
  identity: AuthIdentity | null;
  loading: boolean;
  playBlockedReason?: string;
  modulePlayState?: ModulePlayState | null;
  playerAction: string;
  proposalText: string;
  playerActions: PlayerActionRecord[];
  publicTurns: PublicTurn[];
  selectedPlayerActionId: string;
  autoKpEnabled: boolean;
  autoKpJobs: AutoKpJob[];
  adjudication: ActionAdjudicationView | null;
  parallelBatch?: ParallelActionPlayerBatch | null;
  parallelBatchError?: string;
  parallelRegather?: ParallelActionPlayerRegather | null;
  parallelRegatherError?: string;
  onPlayerActionChange: (value: string) => void;
  onProposalTextChange: (value: string) => void;
  onAutoKpEnabledChange: (value: boolean) => void;
  onSelectPlayerAction: (action: PlayerActionRecord) => void;
  onSearchMemory: () => void;
  onSubmitPlayerAction: () => void;
  onRefreshPlayerActions: () => void;
  onRetryAutoKpJob: (jobId: string) => void;
  onCreateProposal: () => void;
  onPrepareManualKernel: (operatorId: string, skillKey: string | null) => void;
  onGenerateAiProposal: () => void;
  onConfirmAdjudication: (
    selectedSkill: string | null,
    expectedBatchVersion?: number,
    adjudication?: ActionAdjudicationView
  ) => void | Promise<void>;
  onReviseAdjudication: (
    expectedBatchVersion?: number,
    adjudication?: ActionAdjudicationView
  ) => void | Promise<void>;
  onOpenParallelChecks?: () => void;
};

export function ActionPanel(props: Props) {
  const { t } = useTranslation();
  const [selectedSkill, setSelectedSkill] = useState("");
  const [kernelCandidates, setKernelCandidates] = useState<ManualKernelCandidate[]>([]);
  const [selectedOperator, setSelectedOperator] = useState("");
  const [selectedKernelSkill, setSelectedKernelSkill] = useState("");
  const [kernelCatalogMessage, setKernelCatalogMessage] = useState("");
  const [adjudicationRequest, setAdjudicationRequest] = useState<"confirm" | "revise" | null>(null);
  const adjudicationRequestToken = useRef(0);
  const adjudicationRequestActive = useRef(false);
  useEffect(() => {
    setSelectedSkill(props.adjudication?.selected_skill ?? "");
  }, [props.adjudication?.id, props.adjudication?.selected_skill]);
  useEffect(() => {
    adjudicationRequestToken.current += 1;
    adjudicationRequestActive.current = false;
    setAdjudicationRequest(null);
  }, [props.adjudication?.id]);
  useEffect(() => {
    if (props.identity?.role !== "kp" || !props.selectedPlayerActionId) {
      setKernelCandidates([]);
      setSelectedOperator("");
      setSelectedKernelSkill("");
      setKernelCatalogMessage("");
      return;
    }
    let active = true;
    setKernelCatalogMessage("正在读取契约候选…");
    void listManualKernelCandidates(props.selectedPlayerActionId).then((result) => {
      if (!active) return;
      setKernelCandidates(result.candidates);
      const first = result.candidates.find((item) => item.available) ?? result.candidates[0];
      setSelectedOperator(first?.candidate_id ?? "");
      setSelectedKernelSkill(first?.skill_choices[0]?.skill_key ?? "");
      setKernelCatalogMessage(result.candidates.length ? "" : "当前契约没有可选行动。");
    }).catch((error) => {
      if (!active) return;
      setKernelCandidates([]);
      setSelectedOperator("");
      setSelectedKernelSkill("");
      setKernelCatalogMessage(error instanceof Error ? error.message : String(error));
    });
    return () => { active = false; };
  }, [props.identity?.role, props.selectedPlayerActionId]);
  const selectedKernelOperator = kernelCandidates.find(
    (item) => item.candidate_id === selectedOperator
  );
  const selectedOption = props.adjudication?.skill_options.find(
    (option) => option.skill_name === selectedSkill
  );
  const parallelBatchActive = Boolean(
    props.parallelBatch
      && !["settled", "superseded"].includes(props.parallelBatch.status)
  );
  const canReviseParallelAction = Boolean(
    props.parallelBatch?.self_phase === "awaiting_confirmation"
      && props.adjudication?.status === "pending"
  );
  const regatherBlocksInput = Boolean(
    props.parallelRegather
      && props.parallelRegather.self_phase !== "awaiting_submission"
  );
  const actionInputBlocked = Boolean(props.playBlockedReason)
    || (parallelBatchActive && !canReviseParallelAction)
    || regatherBlocksInput;
  async function runAdjudicationRequest(
    kind: "confirm" | "revise",
    request: () => void | Promise<void>
  ) {
    if (adjudicationRequestActive.current) return;
    const token = ++adjudicationRequestToken.current;
    adjudicationRequestActive.current = true;
    setAdjudicationRequest(kind);
    try {
      await request();
    } finally {
      if (adjudicationRequestToken.current === token) {
        adjudicationRequestActive.current = false;
        setAdjudicationRequest(null);
      }
    }
  }

  async function confirmAdjudication() {
    if (!props.adjudication) return;
    await runAdjudicationRequest("confirm", () => props.parallelBatch
      ? props.onConfirmAdjudication(
        selectedSkill || null,
        props.parallelBatch.version,
        props.adjudication ?? undefined
      )
      : props.onConfirmAdjudication(selectedSkill || null));
  }

  async function reviseAdjudication() {
    if (!props.adjudication) return;
    await runAdjudicationRequest("revise", () => props.parallelBatch
      ? props.onReviseAdjudication(
        props.parallelBatch.version,
        props.adjudication ?? undefined
      )
      : props.onReviseAdjudication());
  }
  return (
    <section className="tool-panel action-panel" id="memory-section">
      <div className="panel-heading">
        <h2>{t("actions.chatTitle")}</h2>
        <Brain size={18} />
      </div>
      {props.identity?.role === "player" && props.parallelBatch && (
        <ParallelActionBatchStatus
          batch={props.parallelBatch}
          error={props.parallelBatchError}
          onOpenChecks={props.onOpenParallelChecks}
        />
      )}
      {props.identity?.role === "player" && props.parallelRegather && (
        <ParallelActionRegatherStatus
          regather={props.parallelRegather}
          error={props.parallelRegatherError}
        />
      )}
      <label>
        {t("actions.playerActionLabel")}
        <textarea
          disabled={actionInputBlocked}
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
          {props.modulePlayState?.status === "completed" ? (
            <section className="story-ending-card" aria-label="本次故事已结束">
              <small>本次故事已结束</small>
              <h3>{props.modulePlayState.ending_title ?? "结局已达成"}</h3>
              <p>《{props.modulePlayState.module_title ?? "当前模组"}》已完成，共记录 {props.publicTurns.length} 个公开回合。</p>
              {props.modulePlayState.completed_at && (
                <time dateTime={props.modulePlayState.completed_at}>
                  完成于 {new Date(props.modulePlayState.completed_at).toLocaleString()}
                </time>
              )}
              <details>
                <summary>查看关键选择与完整回放</summary>
                <div className="public-turn-feed">
                  {props.publicTurns.length ? props.publicTurns.map((turn) => (
                    <article className="public-turn-card" key={`ending-${turn.id}`}>
                      {turn.player_action && <small>{turn.player_action}</small>}
                      <p>{turn.public_narration}</p>
                      <ActorExecutionTraceList traces={turn.actor_traces} />
                    </article>
                  )) : <small>{t("actions.noPublicTurns")}</small>}
                </div>
              </details>
            </section>
          ) : props.playBlockedReason ? (
            <div className="permission-hint" role="status">{props.playBlockedReason}</div>
          ) : null}
          {props.modulePlayState?.status !== "completed" && (
            <div className="public-turn-feed" aria-label={t("actions.publicTurnFeed")}>
              <div className="queue-heading">
                <strong>{t("actions.publicTurnFeed")}</strong>
              </div>
              {props.modulePlayState?.opening_narration && (
                <article className="public-turn-card story-opening-card">
                  <small>开场</small>
                  {props.modulePlayState.opening_narration.split(/\n{2,}/).map((paragraph, index) => (
                    <p key={`opening-${index}`}>{paragraph}</p>
                  ))}
                </article>
              )}
              {props.publicTurns.length ? props.publicTurns.map((turn) => (
                <article className="public-turn-card" key={turn.id}>
                  {turn.player_action && <small>{turn.player_action}</small>}
                  <p>{turn.public_narration}</p>
                  <ActorExecutionTraceList traces={turn.actor_traces} />
                </article>
              )) : <small>{t("actions.noPublicTurns")}</small>}
            </div>
          )}
          <label className="checkbox-row">
            <input
              checked={props.autoKpEnabled}
              onChange={(event) => props.onAutoKpEnabledChange(event.target.checked)}
              type="checkbox"
            />
            {t("actions.autoKpLabel")}
          </label>
          <button
            className="primary-button"
            disabled={
              props.loading ||
              Boolean(props.playBlockedReason) ||
              parallelBatchActive ||
              regatherBlocksInput ||
              Boolean(props.parallelRegather && !props.autoKpEnabled) ||
              props.adjudication?.status === "pending"
            }
            onClick={props.onSubmitPlayerAction}
            type="button"
          >
            <Send size={16} />
            {props.autoKpEnabled ? t("actions.submitAndAdvance") : t("actions.submitToKp")}
          </button>
          {props.modulePlayState?.status !== "completed" && props.adjudication?.status === "pending" && (
            <article className={`action-ruling-card ${props.adjudication.mode}`}>
              <div className="action-ruling-heading">
                <strong>{modeLabel(t, props.adjudication.mode)}</strong>
                <small>{t("actions.initialRuling")}</small>
              </div>
              {props.adjudication.source_model.startsWith("kernel:") && (
                <div className="kernel-authority-badge" role="status">
                  <strong>{t("actions.kernelAuthority")}</strong>
                  <small>{t("actions.kernelAuthorityHint")}</small>
                </div>
              )}
              {props.adjudication.tabletop_turn && (
                <div className="tabletop-turn-frame" role="status">
                  <strong>{tabletopRouteLabel(props.adjudication.tabletop_turn.route)}</strong>
                  <small>
                    {tabletopActLabel(props.adjudication.tabletop_turn.frame.kind)}
                    {props.adjudication.tabletop_turn.frame.confidence !== "high"
                      ? ` · 识别置信度 ${props.adjudication.tabletop_turn.frame.confidence}`
                      : ""}
                  </small>
                  {props.adjudication.tabletop_turn.frame.ambiguity && (
                    <p>{props.adjudication.tabletop_turn.frame.ambiguity.why_material}</p>
                  )}
                  <ActorExecutionTraceList
                    traces={props.adjudication.tabletop_turn.response?.actor_traces}
                  />
                </div>
              )}
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
                  {selectedOption && (
                    <div className="check-plan-preview" aria-label="检定计划">
                      <small>{selectedOption.reason}</small>
                      <dl className="action-ruling-details">
                        {selectedOption.scope && <><dt>检定范围</dt><dd>{selectedOption.scope}</dd></>}
                        <dt>骰池修正</dt>
                        <dd>{formatBonusDice(selectedOption.bonus_dice ?? 0)}</dd>
                        {Boolean(selectedOption.supporting_factors?.length) && (
                          <><dt>有利条件</dt><dd>{selectedOption.supporting_factors?.join("；")}</dd></>
                        )}
                        {Boolean(selectedOption.automatic_information?.length) && (
                          <><dt>无需检定即可获得</dt><dd>{selectedOption.automatic_information?.join("；")}</dd></>
                        )}
                        {selectedOption.failure_stakes && <><dt>失败风险</dt><dd>{selectedOption.failure_stakes}</dd></>}
                        {selectedOption.allow_push !== false && selectedOption.pushed_failure_stakes && (
                          <><dt>推动失败风险</dt><dd>{selectedOption.pushed_failure_stakes}</dd></>
                        )}
                      </dl>
                    </div>
                  )}
                </label>
              )}
              {props.adjudication.mode !== "roleplay_or_clarification" && props.adjudication.prompt && (
                <p>{props.adjudication.prompt}</p>
              )}
              {props.adjudication.mode === "roleplay_or_clarification" && (
                <div className="rp-request">
                  <strong>{t("actions.rpRequestTitle")}</strong>
                  {props.adjudication.prompt && (
                    <p className="rp-request-detail">{props.adjudication.prompt}</p>
                  )}
                  <p className="permission-hint">{t("actions.roleplayHint")}</p>
                </div>
              )}
              <div className="action-ruling-actions">
                {props.adjudication.mode !== "roleplay_or_clarification" && (
                  <button
                    className="primary-button"
                    disabled={Boolean(adjudicationRequest) || (props.adjudication.mode === "skill_check" && !selectedSkill)}
                    onClick={confirmAdjudication}
                    type="button"
                  >
                    {t("actions.confirmRuling")}
                  </button>
                )}
                <button
                  className="secondary-button"
                  disabled={Boolean(adjudicationRequest)}
                  onClick={reviseAdjudication}
                  type="button"
                >
                  {t("actions.reviseRuling")}
                </button>
              </div>
            </article>
          )}
          {props.modulePlayState?.status !== "completed" && props.autoKpJobs.length > 0 && (
            <div aria-label={t("actions.autoKpTaskLabel")} className="auto-kp-player-status">
              <strong>{t("actions.autoKpStatus")}</strong>
              <ul className="module-job-list">
                {props.autoKpJobs.slice(0, 3).map((job) => (
                  <li className={`module-job ${job.status}`} key={job.id}>
                    <div>
                      <strong>{job.job_type === "check_consequence" ? t("actions.jobCheckConsequence") : t("actions.jobPlayerAction")}</strong>
                      <span>{playerJobStatusLabel(job)}</span>
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
          {props.selectedPlayerActionId && (
            <section className="manual-kernel-selection" aria-label="规则内核手工裁定">
              <strong>规则内核手工裁定</strong>
              <small>不调用模型。选择契约允许的行动与技能，玩家仍可确认或修改技能。</small>
              {kernelCatalogMessage && <p className="permission-hint">{kernelCatalogMessage}</p>}
              {kernelCandidates.length > 0 && (
                <>
                  <label>契约行动
                    <select
                      aria-label="契约行动"
                      value={selectedOperator}
                      onChange={(event) => {
                        const next = kernelCandidates.find((item) => item.candidate_id === event.target.value);
                        setSelectedOperator(event.target.value);
                        setSelectedKernelSkill(next?.skill_choices[0]?.skill_key ?? "");
                      }}
                    >
                      {kernelCandidates.map((candidate) => (
                        <option key={candidate.candidate_id} value={candidate.candidate_id}>
                          {candidate.title}{candidate.available ? "" : " · 当前不可用"}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!!selectedKernelOperator?.skill_choices.length && (
                    <label>建议技能
                      <select aria-label="建议技能" value={selectedKernelSkill} onChange={(event) => setSelectedKernelSkill(event.target.value)}>
                        {selectedKernelOperator.skill_choices.map((choice) => (
                          <option key={choice.skill_key} value={choice.skill_key}>{choice.skill_key} · {choice.difficulty}</option>
                        ))}
                      </select>
                      <small>{selectedKernelOperator.skill_choices.find((item) => item.skill_key === selectedKernelSkill)?.reason}</small>
                    </label>
                  )}
                  <button
                    className="primary-button"
                    disabled={props.loading || !selectedOperator || !selectedKernelOperator?.available}
                    onClick={() => props.onPrepareManualKernel(selectedOperator, selectedKernelSkill || null)}
                    type="button"
                  >
                    <MessageSquare size={16} />生成 Kernel 裁定
                  </button>
                </>
              )}
            </section>
          )}
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

function formatBonusDice(value: number): string {
  if (value > 0) return `奖励骰 +${value}`;
  if (value < 0) return `惩罚骰 ${value}`;
  return "无修正";
}

function modeLabel(t: (key: string) => string, mode: ActionAdjudication["mode"]) {
  if (mode === "direct_resolution") return t("actions.modeDirect");
  if (mode === "skill_check") return t("actions.modeSkillCheck");
  return t("actions.modeRoleplay");
}

function tabletopRouteLabel(route: NonNullable<ActionAdjudication["tabletop_turn"]>["route"]) {
  if (route === "conversation") return "桌外交流";
  if (route === "information") return "直接描述可感知信息";
  if (route === "roleplay") return "角色扮演回应";
  if (route === "clarification") return "需要一个关键澄清";
  return "进入规则裁定";
}

function tabletopActLabel(kind: NonNullable<ActionAdjudication["tabletop_turn"]>["frame"]["kind"]) {
  const labels = {
    out_of_character: "桌外发言",
    world_question: "询问当前场景",
    npc_dialogue: "对 NPC 说话",
    action: "具体行动",
    multi_step_action: "多步骤计划",
    time_advance: "时间推进",
    result_assertion: "尚未说明做法的结果声明"
  } as const;
  return labels[kind];
}

function difficultyLabel(t: (key: string) => string, value: string) {
  const key = `gamedata.difficulties.${value}`;
  const translated = t(key);
  if (translated !== key) return translated;
  if (value === "opposed") return t("actions.difficultyOpposed");
  return value;
}

function playerJobStatusLabel(job: AutoKpJob) {
  const resultStatus = typeof job.result?.status === "string" ? job.result.status : "";
  if (resultStatus === "awaiting_confirmation") return "等待你确认裁定";
  if (resultStatus === "awaiting_roll") return "等待你完成检定";
  if (resultStatus === "completed") return "行动已结算";
  const labels: Record<AutoKpJob["status"], string> = {
    queued: "已排队，等待 AI KP",
    running: "AI KP 正在裁定",
    retry_wait: "服务暂不可用，等待自动重试",
    succeeded: "裁定流程已完成",
    failed: "自动裁定失败",
    needs_attention: "需要你处理",
    cancelled: "模组结束，任务已取消"
  };
  return labels[job.status];
}
