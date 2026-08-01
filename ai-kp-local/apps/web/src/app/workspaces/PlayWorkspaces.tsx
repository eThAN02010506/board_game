import { AlertCircle } from "lucide-react";
import type { Dispatch, FormEventHandler, SetStateAction } from "react";

import type {
  AuthIdentity,
  ActionAdjudication,
  AutoKpJob,
  Campaign,
  CampaignInvestigator,
  ContextAssembly,
  CreateOpposedCheckInput,
  CreateSkillCheckInput,
  MapToken,
  NpcReappearanceCandidate,
  OpposedCheck,
  PlayerActionRecord,
  PlayerCharacter,
  SavedMap,
  SessionMember,
  SkillCheck,
  TurnProposal,
  WorldExpansionEncounterInput
} from "../../api/types";
import { ActionPanel } from "../../features/actions/ActionPanel";
import { CheckPanel } from "../../features/checks/CheckPanel";
import { GameplayWorkbench } from "../../features/gameplay/GameplayWorkbench";
import { MapStage } from "../../features/maps/MapStage";
import { RoutePlanPanel } from "../../features/maps/RoutePlanPanel";
import { TokenPanel } from "../../features/maps/TokenPanel";
import { ProposalPanel } from "../../features/proposals/ProposalPanel";

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
  adjudication: ActionAdjudication | null;
  autoKpEnabled: boolean;
  autoKpJobs: AutoKpJob[];
  onAutoKpEnabledChange: (value: boolean) => void;
  onCreateProposal: () => void;
  onGenerateAiProposal: () => void;
  onPlayerActionChange: (value: string) => void;
  onProposalTextChange: (value: string) => void;
  onRefreshPlayerActions: () => void;
  onRetryAutoKpJob: (jobId: string) => void;
  onSearchMemory: () => void;
  onSelectPlayerAction: (action: PlayerActionRecord) => void;
  onSubmitPlayerAction: () => void;
  onConfirmAdjudication: (selectedSkill: string | null) => void;
  onReviseAdjudication: () => void;
  playerAction: string;
  playerActions: PlayerActionRecord[];
  proposalText: string;
  selectedPlayerActionId: string;
};

type CheckDeskProps = {
  checks: SkillCheck[];
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
  pendingCount: number;
  role: AuthIdentity["role"];
}) {
  return (
    <section className="play-hero-card">
      <div>
        <p className="eyebrow">
          {props.role === "kp" ? "Keeper cockpit" : "Player table"}
        </p>
        <h2>{props.role === "kp" ? "导演控制室" : "你的行动桌面"}</h2>
        <p>
          {props.role === "kp"
            ? "统一查看玩家行动、地图、检定、审批和自动 KP 队列；适合掌控节奏与风险。"
            : "专注描述行动、查看角色与地图、完成检定；不会暴露 KP 草稿或后台控制。"}
        </p>
      </div>
      <div className="play-hero-metrics" aria-label="当前桌面状态">
        <span>
          <small>角色</small>
          <strong>{props.activePc?.name ?? "未绑定"}</strong>
        </span>
        <span>
          <small>地图</small>
          <strong>{props.activeMap?.title ?? "未打开"}</strong>
        </span>
        <span>
          <small>{props.role === "kp" ? "待审行动" : "待掷检定"}</small>
          <strong>{props.pendingCount}</strong>
        </span>
      </div>
    </section>
  );
}

function CharacterSidebar(props: CommonPlayProps) {
  return (
    <aside className={`play-character-card ${props.characterExpanded ? "expanded" : ""}`}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">当前调查员</p>
          <h2>{props.activePc?.name ?? "尚未绑定角色"}</h2>
        </div>
        <button
          className="ghost-button"
          onClick={() => props.onSetCharacterExpanded((value) => !value)}
          type="button"
        >
          {props.characterExpanded ? "收起" : "展开完整卡"}
        </button>
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
      ) : (
        <p className="permission-hint">
          先在“团与权限”页加入会话并绑定已批准角色。
        </p>
      )}
      <div className="party-summary">
        <div className="party-summary-heading">
          <strong>{props.authIdentity.role === "kp" ? "所有玩家" : "队友信息"}</strong>
          <small>{props.authIdentity.role === "kp" ? "公开摘要" : "精简信息"}</small>
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
                  <span>{location ?? "位置未知"}</span>
                </div>
                {props.authIdentity.role === "kp" && (
                  <small>
                    {summary.cash !== undefined ? `现金 ${summary.cash}` : "现金未公开"}
                    {Object.keys(attributes).length
                      ? ` · ${Object.entries(attributes)
                          .slice(0, 3)
                          .map(([key, value]) => `${key.toUpperCase()} ${value}`)
                          .join(" / ")}`
                      : " · 属性未公开"}
                  </small>
                )}
              </article>
            );
          })
        ) : (
          <small>目前没有其他已加入的调查员。</small>
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
      <RoutePlanPanel map={props.activeMap} identity={props.authIdentity} />
    </div>
  );
}

function ActionDesk(props: CommonPlayProps & ActionDeskProps) {
  return (
    <>
      <GameplayWorkbench campaign={props.activeCampaign} identity={props.authIdentity} />
      <ActionPanel
        adjudication={props.adjudication}
        autoKpEnabled={props.autoKpEnabled}
        autoKpJobs={props.autoKpJobs}
        identity={props.authIdentity}
        loading={props.loading}
        onAutoKpEnabledChange={props.onAutoKpEnabledChange}
        onCreateProposal={props.onCreateProposal}
        onConfirmAdjudication={props.onConfirmAdjudication}
        onGenerateAiProposal={props.onGenerateAiProposal}
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
  return (
    <div className="play-page kp-layout">
      <PlayHero
        activeMap={props.activeMap}
        activePc={props.activePc}
        pendingCount={props.playerActions.filter((item) => item.status === "submitted").length}
        role="kp"
      />
      <CharacterSidebar {...props} />
      <PlayMapColumn {...props} />
      <div className="play-action-column">
        <div className="action-tabs" role="tablist" aria-label="KP 导演控制台">
          {(["player-view", "checks", "director", "table-log"] as const).map((tab) => (
            <button
              aria-selected={props.kpActionTab === tab}
              className={`tab-btn ${props.kpActionTab === tab ? "active" : ""}`}
              key={tab}
              onClick={() => props.onKpActionTabChange(tab)}
              role="tab"
              type="button"
            >
              {{ "player-view": "玩家视角", checks: "检定", director: "导演", "table-log": "桌面记录" }[tab]}
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
                <h2>桌面记录</h2>
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
  return (
    <div className="play-page player-layout">
      <PlayHero
        activeMap={props.activeMap}
        activePc={props.activePc}
        pendingCount={props.checks.filter((item) => item.status === "requested").length}
        role="player"
      />
      <CharacterSidebar {...props} />
      <PlayMapColumn {...props} />
      <div className="play-action-column">
        <div className="action-tabs" role="tablist" aria-label="玩家游玩台">
          {(["actions", "checks"] as const).map((tab) => (
            <button
              aria-selected={props.playerActionTab === tab}
              className={`tab-btn ${props.playerActionTab === tab ? "active" : ""}`}
              key={tab}
              onClick={() => props.onPlayerActionTabChange(tab)}
              role="tab"
              type="button"
            >
              {tab === "actions" ? "行动" : "检定"}
            </button>
          ))}
        </div>
        <div className="action-tab-content">
          {props.playerActionTab === "actions" && <ActionDesk {...props} />}
          {props.playerActionTab === "checks" && <CheckDesk {...props} />}
        </div>
      </div>
    </div>
  );
}
