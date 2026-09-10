import { AlertCircle } from "lucide-react";
import type { Dispatch, FormEventHandler, SetStateAction } from "react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  AuthIdentity,
  ActionAdjudicationView,
  AutoKpJob,
  Campaign,
  CampaignInvestigator,
  ContextAssembly,
  CreateOpposedCheckInput,
  CreateSkillCheckInput,
  MapToken,
  ModulePlayState,
  NpcReappearanceCandidate,
  OpposedCheck,
  ParallelActionPlayerBatch,
  ParallelActionPlayerRegather,
  PlayerActionRecord,
  PublicTurn,
  PlayerCharacter,
  SavedMap,
  SessionMember,
  SkillCheck,
  TurnProposal,
  VisibleSkillCheck,
  WorldExpansionEncounterInput
} from "../../api/types";
import { ActionPanel } from "../../features/actions/ActionPanel";
import { ConsequenceSignalPanel } from "../../features/actions/ConsequenceSignalPanel";
import { ParallelActionAttentionPanel } from "../../features/actions/ParallelActionAttentionPanel";
import { CheckPanel } from "../../features/checks/CheckPanel";
import { TableCommunicationPanel } from "../../features/communications/TableCommunicationPanel";
import { GameplayWorkbench } from "../../features/gameplay/GameplayWorkbench";
import { InventoryPanel } from "../../features/inventory/InventoryPanel";
import { CharacterLifecyclePanel } from "../../features/characters/CharacterLifecyclePanel";
import { AwarenessMap } from "../../features/maps/AwarenessMap";
import { MapStage } from "../../features/maps/MapStage";
import { RoutePlanPanel } from "../../features/maps/RoutePlanPanel";
import { TokenPanel } from "../../features/maps/TokenPanel";
import { DirectorHelpPanel } from "../../features/director/DirectorHelpPanel";
import { ProposalPanel } from "../../features/proposals/ProposalPanel";
import { SessionZeroPanel } from "../../features/sessions/SessionZeroPanel";
import { SessionContinuityPanel } from "../../features/sessions/SessionContinuityPanel";
import { CampaignObjectivePanel } from "../../features/sessions/CampaignObjectivePanel";
import { useParallelActionBatch } from "../hooks/useParallelActionBatch";
import { useParallelActionRegather } from "../hooks/useParallelActionRegather";
import { useSessionContinuity } from "../hooks/useSessionContinuity";
import { useSessionZero } from "../hooks/useSessionZero";

type KpTab = "player-view" | "checks" | "director" | "table-log";
type PlayerTab = "actions" | "checks";

type CommonPlayProps = {
  activeCampaign: Campaign | null;
  activeMap: SavedMap | null;
  activePc: PlayerCharacter | null;
  authIdentity: AuthIdentity;
  characterExpanded: boolean;
  loading: boolean;
  maps: SavedMap[];
  movableTokens: MapToken[];
  moveTarget: string;
  onMoveTargetChange: (value: string) => void;
  onMoveToken: FormEventHandler<HTMLFormElement>;
  onOpenMap: (mapId: string) => void;
  onPlaceToken: FormEventHandler<HTMLFormElement>;
  onRefreshMaps: () => void;
  onRefreshIdentity: () => void | Promise<void>;
  onSetCharacterExpanded: Dispatch<SetStateAction<boolean>>;
  onSetMapPublished: (published: boolean) => void;
  onSelectedTokenIdChange: (value: string) => void;
  onTokenActorIdChange: (value: string) => void;
  onTokenLabelChange: (value: string) => void;
  onTokenLocationChange: (value: string) => void;
  otherPcs: PlayerCharacter[];
  pcs: PlayerCharacter[];
  selectedToken: MapToken | null;
  selectedTokenId: string;
  tokenActorId: string;
  tokenLabel: string;
  tokenLocation: string;
};

type ActionDeskProps = {
  adjudication: ActionAdjudicationView | null;
  autoKpEnabled: boolean;
  autoKpJobs: AutoKpJob[];
  parallelBatch?: ParallelActionPlayerBatch | null;
  parallelBatchError?: string;
  parallelRegather?: ParallelActionPlayerRegather | null;
  parallelRegatherError?: string;
  playBlockedReason?: string;
  modulePlayState?: ModulePlayState | null;
  onAutoKpEnabledChange: (value: boolean) => void;
  onCreateProposal: () => void;
  onPrepareManualKernel: (operatorId: string, skillKey: string | null) => void;
  onGenerateAiProposal: () => void;
  onPlayerActionChange: (value: string) => void;
  onProposalTextChange: (value: string) => void;
  onRefreshPlayerActions: () => void;
  onRetryAutoKpJob: (jobId: string) => void;
  onSearchMemory: () => void;
  onSelectPlayerAction: (action: PlayerActionRecord) => void;
  onSubmitPlayerAction: () => void;
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
  playerAction: string;
  playerActions: PlayerActionRecord[];
  publicTurns: PublicTurn[];
  proposalText: string;
  selectedPlayerActionId: string;
};

type CheckDeskProps = {
  checks: VisibleSkillCheck[];
  ownedCheckIds?: ReadonlySet<string>;
  members: SessionMember[];
  onCancel: (checkId: string, reason: string) => void;
  onCreate: (input: CreateSkillCheckInput) => void;
  onCreateOpposed: (input: CreateOpposedCheckInput) => void;
  onGenerateConsequence: (checkId: string) => void;
  onOverride: (
    checkId: string,
    successLevel: NonNullable<SkillCheck["success_level"]>,
    passed: boolean,
    reason: string
  ) => void;
  onPush: (checkId: string, reason: string) => void;
  onDeclinePush: (checkId: string, reason: string) => void;
  onRefresh: () => void;
  onReplay: (checkId: string) => void;
  onResolveDigital: (checkId: string) => void;
  onResolveOpposed: (opposedCheckId: string) => void;
  onResolvePhysical: (checkId: string, onesDigit: number, tensDigits: number[]) => void;
  onRerollOpposed: (opposedCheckId: string) => void;
  opposedChecks: OpposedCheck[];
};

type ProposalDeskProps = {
  activeProposal: TurnProposal | null;
  campaignTime: string;
  contactInvestigators: CampaignInvestigator[];
  npcReappearanceCandidates: NpcReappearanceCandidate[];
  onApprove: () => void;
  onConfirmWorldExpansion: (input: WorldExpansionEncounterInput) => void;
  onInspectContext: () => void;
  onOverrideTextChange: (value: string) => void;
  onRefresh: () => void;
  onReject: () => void;
  onSelectProposal: (proposalId: string) => void;
  overrideText: string;
  proposalContext: ContextAssembly | null;
  proposals: TurnProposal[];
};

type KpWorkspaceProps = CommonPlayProps &
  ActionDeskProps &
  CheckDeskProps &
  ProposalDeskProps & {
    kpActionTab: KpTab;
    log: string;
    onKpActionTabChange: (tab: KpTab) => void;
  };

type PlayerWorkspaceProps = CommonPlayProps &
  ActionDeskProps &
  CheckDeskProps & {
    onPlayerActionTabChange: (tab: PlayerTab) => void;
    playerActionTab: PlayerTab;
  };

type ObserverWorkspaceProps = {
  activeCampaign: Campaign | null;
  authIdentity: AuthIdentity;
  modulePlayState: ModulePlayState | null;
  publicTurns: PublicTurn[];
  onRefreshIdentity: () => void | Promise<void>;
};

function stringifyForLog(value: unknown) {
  return JSON.stringify(value, null, 2) ?? String(value);
}

function publicPcSummary(
  pc: PlayerCharacter
): NonNullable<PlayerCharacter["public_summary"]> {
  return pc.public_summary ?? {};
}

function PlayHero(props: {
  activeMap: SavedMap | null;
  activePc: PlayerCharacter | null;
  investigatorCount: number;
  pendingCount: number;
  role: AuthIdentity["role"];
}) {
  const { t } = useTranslation();
  return (
    <section className="play-hero-card">
      <div>
        <p className="eyebrow">
          {props.role === "kp" ? t("play.heroKpEyebrow") : t("play.heroPlayerEyebrow")}
        </p>
        <h2>{props.role === "kp" ? t("play.heroKpTitle") : t("play.heroPlayerTitle")}</h2>
        <p>
          {props.role === "kp"
            ? t("play.heroKpHint")
            : t("play.heroPlayerHint")}
        </p>
      </div>
      <div className="play-hero-metrics" aria-label={t("play.deskStatus")}>
        <span>
          <small>{props.role === "kp" ? t("play.metricParty") : t("play.metricCharacter")}</small>
          <strong>{props.role === "kp" ? t("play.investigatorCount", { count: props.investigatorCount }) : props.activePc?.name ?? t("play.unbound")}</strong>
        </span>
        <span>
          <small>{t("play.metricMap")}</small>
          <strong>{props.activeMap?.title ?? t("play.notOpen")}</strong>
        </span>
        <span>
          <small>{props.role === "kp" ? t("play.metricPendingActions") : t("play.metricPendingChecks")}</small>
          <strong>{props.pendingCount}</strong>
        </span>
      </div>
    </section>
  );
}

function CharacterSidebar(props: CommonPlayProps) {
  const { t } = useTranslation();
  const isKp = props.authIdentity.role === "kp";
  return (
    <aside className={`play-character-card ${props.characterExpanded ? "expanded" : ""}`}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{isKp ? t("play.kpPerspective") : t("play.currentInvestigator")}</p>
          <h2>{isKp ? t("play.kpDirectorView") : props.activePc?.name ?? t("play.noBoundCharacter")}</h2>
        </div>
        {!isKp && <button
          className="ghost-button"
          onClick={() => props.onSetCharacterExpanded((value) => !value)}
          type="button"
        >
          {props.characterExpanded ? t("play.collapse") : t("play.expandFull")}
        </button>}
      </div>
      {props.activePc ? (
        <>
          <div className="mini-sheet-grid">
            {Object.entries(props.activePc.sheet ?? {})
              .slice(0, props.characterExpanded ? 24 : 8)
              .map(([key, value]) => (
                <span key={key}>
                  <small>{key}</small>
                  <strong>{typeof value === "object" ? "…" : String(value)}</strong>
                </span>
              ))}
          </div>
          {props.characterExpanded && (
            <pre>{stringifyForLog(props.activePc.sheet ?? {})}</pre>
          )}
        </>
      ) : !isKp ? (
        <p className="permission-hint">
          {t("play.bindHint")}
        </p>
      ) : null}
      <div className="party-summary">
        <div className="party-summary-heading">
          <strong>{props.authIdentity.role === "kp" ? t("play.allPlayers") : t("play.allyInfo")}</strong>
          <small>{props.authIdentity.role === "kp" ? t("play.publicSummary") : t("play.briefInfo")}</small>
        </div>
        {props.otherPcs.length ? (
          props.otherPcs.map((pc) => {
            const location = props.activeMap?.tokens?.find(
              (token) => token.actor_type === "pc" && token.actor_id === pc.id
            )?.location_name;
            const summary = publicPcSummary(pc);
            const attributes = summary.attributes ?? {};
            return (
              <article className="party-member-mini" key={pc.id}>
                <div>
                  <strong>{pc.name}</strong>
                  <span>{location ?? t("play.locationUnknown")}</span>
                </div>
                {props.authIdentity.role === "kp" && (
                  <small>
                    {summary.cash !== undefined ? `${t("play.cash")} ${summary.cash}` : t("play.cashHidden")}
                    {Object.keys(attributes).length
                      ? ` · ${Object.entries(attributes)
                          .slice(0, 3)
                          .map(([key, value]) => `${key.toUpperCase()} ${value}`)
                          .join(" / ")}`
                      : t("play.attributesHidden")}
                  </small>
                )}
              </article>
            );
          })
        ) : (
          <small>{t("play.noOtherInvestigators")}</small>
        )}
      </div>
      {props.authIdentity.role === "kp" && (
        <TokenPanel
          activeMap={props.activeMap}
          identity={props.authIdentity}
          movableTokens={props.movableTokens}
          moveTarget={props.moveTarget}
          onMoveTargetChange={props.onMoveTargetChange}
          onMoveToken={props.onMoveToken}
          onPlaceToken={props.onPlaceToken}
          onSelectedTokenIdChange={props.onSelectedTokenIdChange}
          onTokenActorIdChange={props.onTokenActorIdChange}
          onTokenLabelChange={props.onTokenLabelChange}
          onTokenLocationChange={props.onTokenLocationChange}
          pcs={props.pcs}
          selectedToken={props.selectedToken}
          selectedTokenId={props.selectedTokenId}
          tokenActorId={props.tokenActorId}
          tokenLabel={props.tokenLabel}
          tokenLocation={props.tokenLocation}
        />
      )}
    </aside>
  );
}

function PlayMapColumn(props: CommonPlayProps) {
  return (
    <div className="play-map-column">
      {props.authIdentity.role === "player" ? (
        <AwarenessMap
          activeMap={props.activeMap}
          loading={props.loading}
          onRefresh={props.onRefreshMaps}
        />
      ) : (
        <MapStage
          activeMap={props.activeMap}
          hasIdentity={Boolean(props.authIdentity)}
          loading={props.loading}
          maps={props.maps}
          onOpenMap={props.onOpenMap}
          onRefresh={props.onRefreshMaps}
          onSetPublished={props.onSetMapPublished}
          role={props.authIdentity.role}
          showReviewControls={false}
        />
      )}
      <RoutePlanPanel map={props.activeMap} identity={props.authIdentity} />
    </div>
  );
}

function ActionDesk(props: CommonPlayProps & ActionDeskProps) {
  const signalRefreshKey = props.autoKpJobs
    .map((job) => `${job.id}:${job.status}:${job.updated_at}`)
    .join("|");
  return (
    <>
      <GameplayWorkbench campaign={props.activeCampaign} identity={props.authIdentity} />
      <ConsequenceSignalPanel
        campaignId={props.activeCampaign?.id ?? ""}
        refreshKey={signalRefreshKey}
        role={props.authIdentity.role}
      />
      <ActionPanel
        adjudication={props.adjudication}
        autoKpEnabled={props.autoKpEnabled}
        autoKpJobs={props.autoKpJobs}
        identity={props.authIdentity}
        loading={props.loading}
        playBlockedReason={props.playBlockedReason}
        modulePlayState={props.modulePlayState}
        parallelBatch={props.parallelBatch}
        parallelBatchError={props.parallelBatchError}
        parallelRegather={props.parallelRegather}
        parallelRegatherError={props.parallelRegatherError}
        onAutoKpEnabledChange={props.onAutoKpEnabledChange}
        onCreateProposal={props.onCreateProposal}
        onPrepareManualKernel={props.onPrepareManualKernel}
        onConfirmAdjudication={props.onConfirmAdjudication}
        onGenerateAiProposal={props.onGenerateAiProposal}
        onOpenParallelChecks={props.onOpenParallelChecks}
        onPlayerActionChange={props.onPlayerActionChange}
        onProposalTextChange={props.onProposalTextChange}
        onRefreshPlayerActions={props.onRefreshPlayerActions}
        onRetryAutoKpJob={props.onRetryAutoKpJob}
        onReviseAdjudication={props.onReviseAdjudication}
        onSearchMemory={props.onSearchMemory}
        onSelectPlayerAction={props.onSelectPlayerAction}
        onSubmitPlayerAction={props.onSubmitPlayerAction}
        playerAction={props.playerAction}
        playerActions={props.playerActions}
        publicTurns={props.publicTurns}
        proposalText={props.proposalText}
        selectedPlayerActionId={props.selectedPlayerActionId}
      />
    </>
  );
}

function CheckDesk(props: CommonPlayProps & CheckDeskProps) {
  return (
    <CheckPanel
      checks={props.checks}
      ownedCheckIds={props.ownedCheckIds}
      opposedChecks={props.opposedChecks}
      identity={props.authIdentity}
      loading={props.loading}
      members={props.members}
      onCancel={props.onCancel}
      onCreate={props.onCreate}
      onCreateOpposed={props.onCreateOpposed}
      onGenerateConsequence={props.onGenerateConsequence}
      onOverride={props.onOverride}
      onPush={props.onPush}
      onDeclinePush={props.onDeclinePush}
      onRefresh={props.onRefresh}
      onReplay={props.onReplay}
      onResolveDigital={props.onResolveDigital}
      onResolvePhysical={props.onResolvePhysical}
      onResolveOpposed={props.onResolveOpposed}
      onRerollOpposed={props.onRerollOpposed}
    />
  );
}

export function KpWorkspace(props: KpWorkspaceProps) {
  const { t } = useTranslation();
  const sessionZero = useSessionZero(
    props.activeCampaign?.id ?? "",
    props.authIdentity
  );
  const continuity = useSessionContinuity(
    props.activeCampaign?.id ?? "",
    props.authIdentity,
    props.autoKpJobs.map((job) => `${job.id}:${job.status}:${job.updated_at}`).join("|")
  );
  return (
    <div className="play-page kp-layout">
      {props.activeCampaign && (
        <SessionZeroPanel
          campaign={props.activeCampaign}
          error={sessionZero.error}
          identity={props.authIdentity}
          loading={sessionZero.loading}
          onChanged={() => void sessionZero.refresh()}
          view={sessionZero.view}
        />
      )}
      {props.activeCampaign && (
        <SessionContinuityPanel
          busy={continuity.busy}
          error={continuity.error}
          identity={props.authIdentity}
          onContinue={() => void continuity.continueCampaign()}
          onEnd={() => void continuity.end()}
          onRefresh={() => void continuity.refresh()}
          onTransition={(target, expectedVersion) => void continuity.transition(target, expectedVersion)}
          view={continuity.view}
        />
      )}
      {props.activeCampaign && <CampaignObjectivePanel campaignId={props.activeCampaign.id} identity={props.authIdentity} refreshKey={props.autoKpJobs.map((job) => job.updated_at).join("|")} />}
      <PlayHero
        activeMap={props.activeMap}
        activePc={props.activePc}
        investigatorCount={props.pcs.length}
        pendingCount={props.playerActions.filter((item) => item.status === "submitted").length}
        role="kp"
      />
      {props.activeCampaign && <DirectorHelpPanel campaignId={props.activeCampaign.id} />}
      <CharacterSidebar {...props} />
      {props.activeCampaign && <CharacterLifecyclePanel campaignId={props.activeCampaign.id} identity={props.authIdentity} onIdentityChanged={props.onRefreshIdentity} refreshKey={props.autoKpJobs.map((job) => job.updated_at).join("|")} />}
      {props.activeCampaign && <InventoryPanel campaignId={props.activeCampaign.id} identity={props.authIdentity} refreshKey={props.autoKpJobs.map((job) => job.updated_at).join("|")} />}
      <PlayMapColumn {...props} />
      <div className="play-action-column">
        <TableCommunicationPanel
          campaign={props.activeCampaign}
          identity={props.authIdentity}
          refreshKey={props.autoKpJobs.map((job) => `${job.id}:${job.updated_at}`).join("|")}
        />
        <ParallelActionAttentionPanel
          campaignId={props.activeCampaign?.id ?? ""}
          identity={props.authIdentity}
          jobs={props.autoKpJobs}
          onChanged={props.onRefreshPlayerActions}
        />
        <div className="action-tabs" role="tablist" aria-label={t("play.kpConsoleLabel")}>
          {(["player-view", "checks", "director", "table-log"] as const).map((tab) => (
            <button
              aria-selected={props.kpActionTab === tab}
              className={`tab-btn ${props.kpActionTab === tab ? "active" : ""}`}
              key={tab}
              onClick={() => props.onKpActionTabChange(tab)}
              role="tab"
              type="button"
            >
              {{ "player-view": t("play.tabPlayerView"), checks: t("play.tabChecks"), director: t("play.tabDirector"), "table-log": t("play.tabTableLog") }[tab]}
            </button>
          ))}
        </div>
        <div className="action-tab-content">
          {props.kpActionTab === "player-view" && <ActionDesk {...props} />}
          {props.kpActionTab === "checks" && <CheckDesk {...props} />}
          {props.kpActionTab === "director" && (
            <ProposalPanel
              activeMap={props.activeMap}
              activeProposal={props.activeProposal}
              campaignTime={props.campaignTime}
              contactInvestigators={props.contactInvestigators}
              loading={props.loading}
              onApprove={props.onApprove}
              onConfirmWorldExpansion={props.onConfirmWorldExpansion}
              onInspectContext={props.onInspectContext}
              npcReappearanceCandidates={props.npcReappearanceCandidates}
              onOverrideTextChange={props.onOverrideTextChange}
              onRefresh={props.onRefresh}
              onReject={props.onReject}
              onSelectProposal={props.onSelectProposal}
              overrideText={props.overrideText}
              proposalContext={props.proposalContext}
              proposals={props.proposals}
            />
          )}
          {props.kpActionTab === "table-log" && (
            <section className="response-panel table-log-panel">
              <div className="panel-heading">
                <h2>{t("play.tabTableLog")}</h2>
                <AlertCircle size={18} />
              </div>
              <pre>{props.log}</pre>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

export function PlayerWorkspace(props: PlayerWorkspaceProps) {
  const { t } = useTranslation();
  const [lifecycleBlockedReason, setLifecycleBlockedReason] = useState(
    "正在确认当前角色是否可以行动。"
  );
  const parallelRefreshKey = useMemo(
    () => [
      ...props.autoKpJobs.map((job) => `${job.id}:${job.status}:${job.updated_at}`),
      ...props.checks.map((check) => `${check.id}:${check.status}:${check.resolved_at ?? ""}`)
    ].join("|"),
    [props.autoKpJobs, props.checks]
  );
  const {
    batch: parallelBatch,
    error: parallelBatchError,
    refresh: refreshParallelBatch
  } = useParallelActionBatch(
    props.activeCampaign?.id ?? "",
    props.authIdentity,
    parallelRefreshKey
  );
  const {
    regather: parallelRegather,
    error: parallelRegatherError,
    refresh: refreshParallelRegather
  } = useParallelActionRegather(
    props.activeCampaign?.id ?? "",
    props.authIdentity,
    parallelRefreshKey
  );
  const sessionZero = useSessionZero(
    props.activeCampaign?.id ?? "",
    props.authIdentity
  );
  const continuity = useSessionContinuity(
    props.activeCampaign?.id ?? "",
    props.authIdentity,
    parallelRefreshKey
  );
  const actionBlockedReason = sessionZero.view && !sessionZero.view.ready
    ? "请先完成并确认当前 Session 0，再提交正式行动。"
    : continuity.view && !continuity.view.accepts_actions
      ? continuity.view.current_episode?.status === "ended"
        ? "本次 Session 已结束，等待 KP 开始下一次游戏。"
        : "本次 Session 当前已暂停，等待 KP 恢复后再继续行动。"
      : lifecycleBlockedReason || props.playBlockedReason;
  const adjudication = parallelBatch
    ? parallelBatch.own_item.adjudication.status === "pending"
      ? parallelBatch.own_item.adjudication
      : null
    : props.adjudication;
  const checks = useMemo<VisibleSkillCheck[]>(() => {
    if (!parallelBatch) return props.checks;
    const ownedIds = new Set(
      parallelBatch.own_item.checks.map((check) => check.id)
    );
    return [
      ...parallelBatch.own_item.checks,
      ...props.checks.filter((check) => !ownedIds.has(check.id))
    ];
  }, [parallelBatch, props.checks]);
  const ownedCheckIds = useMemo(
    () => new Set(parallelBatch?.own_item.checks.map((check) => check.id) ?? []),
    [parallelBatch]
  );

  useEffect(() => {
    if (
      parallelBatch?.self_phase === "awaiting_check"
      || parallelBatch?.self_phase === "awaiting_push_decision"
    ) {
      props.onPlayerActionTabChange("checks");
    }
  }, [parallelBatch?.id, parallelBatch?.self_phase, props.onPlayerActionTabChange]);

  return (
    <div className="play-page player-layout">
      {props.activeCampaign && (
        <SessionZeroPanel
          campaign={props.activeCampaign}
          error={sessionZero.error}
          identity={props.authIdentity}
          loading={sessionZero.loading}
          onChanged={() => void sessionZero.refresh()}
          view={sessionZero.view}
        />
      )}
      {props.activeCampaign && (
        <SessionContinuityPanel
          busy={continuity.busy}
          error={continuity.error}
          identity={props.authIdentity}
          onContinue={() => void continuity.continueCampaign()}
          onEnd={() => void continuity.end()}
          onRefresh={() => void continuity.refresh()}
          onTransition={(target, expectedVersion) => void continuity.transition(target, expectedVersion)}
          view={continuity.view}
        />
      )}
      {props.activeCampaign && <CampaignObjectivePanel campaignId={props.activeCampaign.id} identity={props.authIdentity} refreshKey={parallelRefreshKey} />}
      <PlayHero
        activeMap={props.activeMap}
        activePc={props.activePc}
        investigatorCount={props.pcs.length}
        pendingCount={checks.filter((item) => item.status === "requested").length}
        role="player"
      />
      <CharacterSidebar {...props} />
      {props.activeCampaign && <CharacterLifecyclePanel campaignId={props.activeCampaign.id} identity={props.authIdentity} onIdentityChanged={props.onRefreshIdentity} onPlayerActionBlockChanged={setLifecycleBlockedReason} refreshKey={parallelRefreshKey} />}
      {props.activeCampaign && <InventoryPanel campaignId={props.activeCampaign.id} identity={props.authIdentity} refreshKey={parallelRefreshKey} />}
      <PlayMapColumn {...props} />
      <div className="play-action-column">
        <TableCommunicationPanel
          campaign={props.activeCampaign}
          identity={props.authIdentity}
          refreshKey={parallelRefreshKey}
        />
        <div className="action-tabs" role="tablist" aria-label={t("play.playerConsoleLabel")}>
          {(["actions", "checks"] as const).map((tab) => (
            <button
              aria-selected={props.playerActionTab === tab}
              className={`tab-btn ${props.playerActionTab === tab ? "active" : ""}`}
              key={tab}
              onClick={() => props.onPlayerActionTabChange(tab)}
              role="tab"
              type="button"
            >
              {tab === "actions" ? t("play.tabActions") : t("play.tabChecks")}
            </button>
          ))}
        </div>
        <div className="action-tab-content">
          {props.playerActionTab === "actions" && (
            <ActionDesk
              {...props}
              adjudication={adjudication}
              onConfirmAdjudication={async (skill, batchVersion, ruling) => {
                await props.onConfirmAdjudication(skill, batchVersion, ruling);
                await refreshParallelBatch();
              }}
              onOpenParallelChecks={() => props.onPlayerActionTabChange("checks")}
              onReviseAdjudication={async (batchVersion, ruling) => {
                await props.onReviseAdjudication(batchVersion, ruling);
                await refreshParallelBatch();
                await refreshParallelRegather();
              }}
              parallelBatch={parallelBatch}
              parallelBatchError={parallelBatchError}
              parallelRegather={parallelBatch ? null : parallelRegather}
              parallelRegatherError={parallelRegatherError}
              playBlockedReason={actionBlockedReason}
            />
          )}
          {props.playerActionTab === "checks" && (
            <CheckDesk
              {...props}
              checks={checks}
              ownedCheckIds={ownedCheckIds}
            />
          )}
        </div>
      </div>
    </div>
  );
}

/** Read-only observer surface; it never receives player or KP authority props. */
export function ObserverWorkspace(props: ObserverWorkspaceProps) {
  const continuity = useSessionContinuity(
    props.activeCampaign?.id ?? "",
    props.authIdentity,
    props.publicTurns[props.publicTurns.length - 1]?.id ?? ""
  );
  return (
    <div className="play-page observer-layout">
      <section className="play-hero-card observer-hero">
        <div>
          <p className="eyebrow">观战席</p>
          <h2>{props.activeCampaign?.title ?? "当前团"}</h2>
          <p>这里仅展示公开叙事与全桌消息。观战者不能行动、掷骰或查看队伍秘密。</p>
        </div>
        <div className="play-hero-metrics">
          <span><small>模组状态</small><strong>{props.modulePlayState?.status ?? "none"}</strong></span>
          <span><small>公开回合</small><strong>{props.publicTurns.length}</strong></span>
        </div>
      </section>
      {props.activeCampaign && (
        <SessionContinuityPanel
          busy={continuity.busy}
          error={continuity.error}
          identity={props.authIdentity}
          onContinue={() => void continuity.continueCampaign()}
          onEnd={() => void continuity.end()}
          onRefresh={() => void continuity.refresh()}
          onTransition={(target, expectedVersion) => void continuity.transition(target, expectedVersion)}
          view={continuity.view}
        />
      )}
      {props.activeCampaign && <CampaignObjectivePanel campaignId={props.activeCampaign.id} identity={props.authIdentity} />}
      <TableCommunicationPanel
        campaign={props.activeCampaign}
        identity={props.authIdentity}
        refreshKey={props.publicTurns[props.publicTurns.length - 1]?.id ?? ""}
      />
      {props.activeCampaign && <CharacterLifecyclePanel campaignId={props.activeCampaign.id} identity={props.authIdentity} onIdentityChanged={props.onRefreshIdentity} refreshKey={props.publicTurns[props.publicTurns.length - 1]?.id ?? ""} />}
      <section className="tool-panel observer-public-turns">
        <div className="panel-heading"><h2>公开叙事</h2><AlertCircle size={18} /></div>
        {props.modulePlayState?.opening_narration && (
          <article className="public-turn-card"><p>{props.modulePlayState.opening_narration}</p></article>
        )}
        {props.publicTurns.length ? props.publicTurns.map((turn) => (
          <article className="public-turn-card" key={turn.id}>
            {turn.player_action && <small>{turn.player_action}</small>}
            <p>{turn.public_narration}</p>
          </article>
        )) : <p className="empty-note">尚无公开回合。</p>}
      </section>
    </div>
  );
}
