import {
  AlertCircle
} from "lucide-react";
import { FormEvent, lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  credentialBridge,
  isApiError,
  listNpcReappearanceCandidates,
  materializeWorldExpansionEncounter,
  requestJson,
  requestJsonWithAccessToken
} from "../api/client";
import type {
  AuthIdentity,
  ActionAdjudication,
  AutoTurnResult,
  Campaign,
  CampaignInvestigator,
  CreateOpposedCheckInput,
  CreateSkillCheckInput,
  ContextAssembly,
  MapGenerationInput,
  MapToken,
  OpposedCheck,
  NpcReappearanceCandidate,
  PlayerActionRecord,
  PlayerCharacter,
  SavedMap,
  SessionBundle,
  SessionInfo,
  SessionMember,
  SessionSeat,
  SkillCheck,
  TurnProposal,
  WorldExpansionEncounterInput
} from "../api/types";
import { useCredentials } from "../auth/credentials";
import { ActionPanel } from "../features/actions/ActionPanel";
import { CampaignPanel } from "../features/campaigns/CampaignPanel";
import { CheckPanel } from "../features/checks/CheckPanel";
import { GameplayWorkbench } from "../features/gameplay/GameplayWorkbench";
import { MapGeneratorPanel } from "../features/maps/MapGeneratorPanel";
import { MapStage } from "../features/maps/MapStage";
import { RoutePlanPanel } from "../features/maps/RoutePlanPanel";
import { MapStructureEditor } from "../features/maps/MapStructureEditor";
import { TokenPanel } from "../features/maps/TokenPanel";
import { PlanningPanel } from "../features/planning/PlanningPanel";
import { useCapabilities } from "../features/planning/useCapabilities";
import { ProposalPanel } from "../features/proposals/ProposalPanel";
import { SessionPanel } from "../features/sessions/SessionPanel";
import { useWorkspaceRealtime } from "../realtime/provider";
import { AppLayout } from "./layout/AppLayout";
import { useAsyncTaskLog } from "./hooks/useAsyncTaskLog";
import { useAutoKpJobs } from "./hooks/useAutoKpJobs";
import { KpWorkspace, PlayerWorkspace } from "./workspaces/PlayWorkspaces";
import { resolveWorkspaceRoute, useWorkspaceRoute, type PageId } from "./router";
import {
  listStoredCampaignTokens,
  readActiveMapId,
  readCampaignToken,
  removeCampaignToken,
  writeActiveMapId,
  writeCampaignToken
} from "../session/session-storage";
import "../styles.css";

const InvestigatorPage = lazy(() =>
  import("../features/investigators/InvestigatorPage").then((module) => ({
    default: module.InvestigatorPage
  }))
);
const ModelSettingsPage = lazy(() =>
  import("../features/models/ModelSettingsPage").then((module) => ({
    default: module.ModelSettingsPage
  }))
);
const RulebookPage = lazy(() =>
  import("../features/rules/RulebookPage").then((module) => ({
    default: module.RulebookPage
  }))
);
const ModuleLibraryPage = lazy(() =>
  import("../features/modules/ModuleLibraryPage").then((module) => ({
    default: module.ModuleLibraryPage
  }))
);
const NpcWorkspace = lazy(() =>
  import("../features/npcs/NpcWorkspace").then((module) => ({
    default: module.NpcWorkspace
  }))
);
const MemoryWorkspace = lazy(() =>
  import("../features/memory/MemoryWorkspace").then((module) => ({
    default: module.MemoryWorkspace
  }))
);
const FactWorkspace = lazy(() =>
  import("../features/facts/FactWorkspace").then((module) => ({
    default: module.FactWorkspace
  }))
);
const HandoutWorkspace = lazy(() =>
  import("../features/handouts/HandoutWorkspace").then((module) => ({
    default: module.HandoutWorkspace
  }))
);
const SimulationWorkbench = lazy(() =>
  import("../features/evaluations/SimulationWorkbench").then((module) => ({
    default: module.SimulationWorkbench
  }))
);

function stringifyForLog(value: unknown) {
  const secretFields = new Set([
    "access_token",
    "token_hash",
    "join_code",
    "join_code_hash",
    "invitation_code",
    "player_token"
  ]);
  return (
    JSON.stringify(
      value,
      (key, item) => {
        if (secretFields.has(key)) return "[已隐藏]";
        if (key === "svg_text" && typeof item === "string") {
          return `[SVG 已保存 · ${item.length} 字符]`;
        }
        return item;
      },
      2
    ) ?? String(value)
  );
}

function publicPcSummary(pc: PlayerCharacter): NonNullable<PlayerCharacter["public_summary"]> {
  if (pc.public_summary) return pc.public_summary;
  const declared = pc.sheet?.public_summary;
  return declared && typeof declared === "object"
    ? (declared as NonNullable<PlayerCharacter["public_summary"]>)
    : {};
}

function mapImageSize(width: number, height: number): {
  width: number;
  height: number;
} {
  const ratio = width / height;
  if (ratio >= 1.15) return { width: 1536, height: 1024 };
  if (ratio <= 0.87) return { width: 1024, height: 1536 };
  return { width: 1024, height: 1024 };
}


export default function App() {
  const { activePage: activeNav, navigate, route: currentPage } = useWorkspaceRoute();
  const {
    adminToken,
    persistAdminToken,
    rememberPlayerToken,
    setAdminToken
  } = useCredentials();
  const {
    capabilities,
    error: capabilitiesError,
    loading: capabilitiesLoading,
    refresh: refreshCapabilities
  } = useCapabilities();

  useEffect(() => {
    document.title = `${currentPage.label} · AI KP Local`;
  }, [currentPage.label]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [activeCampaign, setActiveCampaign] = useState<Campaign | null>(null);
  const [maps, setMaps] = useState<SavedMap[]>([]);
  const [activeMap, setActiveMap] = useState<SavedMap | null>(null);
  const [proposals, setProposals] = useState<TurnProposal[]>([]);
  const [activeProposalId, setActiveProposalId] = useState("");
  const [proposalContext, setProposalContext] = useState<ContextAssembly | null>(null);
  const [authIdentity, setAuthIdentity] = useState<AuthIdentity | null>(null);
  const [activeSession, setActiveSession] = useState<SessionInfo | null>(null);
  const [sessionMembers, setSessionMembers] = useState<SessionMember[]>([]);
  const [sessionSeats, setSessionSeats] = useState<SessionSeat[]>([]);
  const [skillChecks, setSkillChecks] = useState<SkillCheck[]>([]);
  const [opposedChecks, setOpposedChecks] = useState<OpposedCheck[]>([]);
  const [recoverableSeats, setRecoverableSeats] = useState<SessionSeat[]>([]);
  const [visibleSeatInvites, setVisibleSeatInvites] = useState<Record<string, string>>({});
  const [pcs, setPcs] = useState<PlayerCharacter[]>([]);
  const [contactInvestigators, setContactInvestigators] = useState<
    CampaignInvestigator[]
  >([]);
  const [npcReappearanceCandidates, setNpcReappearanceCandidates] = useState<
    NpcReappearanceCandidate[]
  >([]);
  const [playerActions, setPlayerActions] = useState<PlayerActionRecord[]>([]);
  const [actionAdjudication, setActionAdjudication] = useState<ActionAdjudication | null>(null);
  const [selectedPlayerActionId, setSelectedPlayerActionId] = useState("");
  const [visibleJoinCode, setVisibleJoinCode] = useState("");
  const [selectedTokenId, setSelectedTokenId] = useState("");
  const [characterExpanded, setCharacterExpanded] = useState(false);
  const [kpActionTab, setKpActionTab] = useState<
    "player-view" | "checks" | "director" | "table-log"
  >("player-view");
  const [playerActionTab, setPlayerActionTab] = useState<"actions" | "checks">("actions");
  const [autoKpEnabled, setAutoKpEnabled] = useState(true);
  const {
    jobs: autoKpJobs,
    refresh: refreshAutoKpJobs,
    retry: retryAutoKpJob
  } = useAutoKpJobs(activeCampaign?.id ?? "", Boolean(authIdentity));
  const handledAdjudicationIds = useRef(new Set<string>());
  useEffect(() => {
    const latestResult = autoKpJobs.find(
      (job) => job.status === "succeeded" && job.result?.adjudication?.status === "pending"
    )?.result;
    const direct = latestResult?.adjudication;
    const parallelResult = autoKpJobs.find(
      (job) => job.status === "succeeded" && job.result?.adjudications?.length
    )?.result;
    const ownedParallelAction = parallelResult?.actions?.find(
      (action) => action.member_id === authIdentity?.member_id
    );
    const parallel = parallelResult?.adjudications?.find(
      (item) => item.action_id === ownedParallelAction?.id && item.status === "pending"
    );
    const latest = direct ?? parallel;
    if (latest && !handledAdjudicationIds.current.has(latest.id)) {
      setActionAdjudication(latest);
    }
  }, [autoKpJobs, authIdentity?.member_id]);
  useEffect(() => {
    const campaignId = activeCampaign?.id;
    if (!campaignId || authIdentity?.role !== "player") return;
    let active = true;
    void requestJson<ActionAdjudication[]>(
      `/campaigns/${campaignId}/action-adjudications/pending`
    ).then((items) => {
      const latest = items.find(
        (item) => !handledAdjudicationIds.current.has(item.id)
      );
      if (active && latest) setActionAdjudication(latest);
    }).catch(() => undefined);
    return () => {
      active = false;
    };
  }, [activeCampaign?.id, authIdentity?.member_id, authIdentity?.role, autoKpJobs]);
  const activeCampaignIdRef = useRef("");
  const activeSessionIdRef = useRef("");
  const activeMapIdRef = useRef("");
  const campaignSelectionVersion = useRef(0);
  const campaignListRequestVersion = useRef(0);
  const mapRequestVersion = useRef(0);
  const reportRealtimeRefreshFailureRef = useRef<(label: string) => void>(() => undefined);

  const [campaignTitle, setCampaignTitle] = useState("雾港 1928");
  const [campaignTime, setCampaignTime] = useState("1928-10-03 19:30");
  const [tokenLabel, setTokenLabel] = useState("林若川");
  const [tokenActorId, setTokenActorId] = useState("");
  const [tokenLocation, setTokenLocation] = useState("旧码头");
  const [moveTarget, setMoveTarget] = useState("废弃仓库");
  const [playerAction, setPlayerAction] = useState("我想找旧码头认识、行业内打过交道的人。");
  const [proposalText, setProposalText] = useState("你想起曾在旧码头听过一个报社线人的名字，但需要进一步确认他是否还在附近。");
  const [overrideText, setOverrideText] = useState("");
  const [kpDisplayName, setKpDisplayName] = useState("KP");
  const [joinCodeInput, setJoinCodeInput] = useState("");
  const [seatInvitationInput, setSeatInvitationInput] = useState("");
  const [seatLabel, setSeatLabel] = useState("玩家席位 1");
  const [playerDisplayName, setPlayerDisplayName] = useState("玩家");

  const selectedToken = useMemo(
    () => activeMap?.tokens?.find((token) => token.id === selectedTokenId) ?? null,
    [activeMap, selectedTokenId]
  );

  const movableTokens = useMemo(() => {
    const tokens = activeMap?.tokens ?? [];
    if (authIdentity?.role !== "player") return tokens;
    return tokens.filter(
      (token) => token.actor_type === "pc" && token.actor_id === authIdentity.pc_id
    );
  }, [activeMap, authIdentity]);

  const activeProposal = useMemo(
    () => proposals.find((proposal) => proposal.id === activeProposalId) ?? proposals[0] ?? null,
    [activeProposalId, proposals]
  );

  type RequestScope = {
    campaignSelection: number;
    campaignId: string;
    sessionId: string;
    credentials: ReturnType<typeof credentialBridge.snapshot>;
  };

  function captureRequestScope(): RequestScope {
    return {
      campaignSelection: campaignSelectionVersion.current,
      campaignId: activeCampaignIdRef.current,
      sessionId: activeSessionIdRef.current,
      credentials: credentialBridge.snapshot()
    };
  }

  function isCurrentRequestScope(scope: RequestScope): boolean {
    const current = credentialBridge.snapshot();
    return (
      scope.campaignSelection === campaignSelectionVersion.current &&
      scope.campaignId === activeCampaignIdRef.current &&
      scope.sessionId === activeSessionIdRef.current &&
      scope.credentials.accessToken === current.accessToken &&
      scope.credentials.adminToken === current.adminToken &&
      scope.credentials.role === current.role &&
      scope.credentials.pcId === current.pcId &&
      scope.credentials.playerToken === current.playerToken
    );
  }

  const { loading, log, perform, run, showLog } = useAsyncTaskLog({
    captureScope: captureRequestScope,
    isCurrentScope: isCurrentRequestScope,
    reportSilentFailure: (label) => reportRealtimeRefreshFailureRef.current(label),
    stringify: stringifyForLog
  });

  function isAutoTurnResult(value: unknown): value is AutoTurnResult {
    return Boolean(
      value &&
        typeof value === "object" &&
        "player_action" in value &&
        "status" in value &&
        "message" in value
    );
  }

  function mergeAutoTurnResult(result: AutoTurnResult, campaignId: string) {
    if (activeCampaignIdRef.current !== campaignId) return;
    if (result.proposal) {
      setProposals((items) => [
        result.proposal!,
        ...items.filter((item) => item.id !== result.proposal!.id)
      ]);
      setActiveProposalId(result.proposal.id);
      setProposalContext(null);
      setOverrideText("");
    }
    if (result.checks.length) {
      setSkillChecks((items) => [
        ...result.checks,
        ...items.filter((item) => !result.checks.some((check) => check.id === item.id))
      ]);
      setPlayerActionTab("checks");
    }
    if (result.adjudication) setActionAdjudication(result.adjudication);
    if (result.job) void refreshAutoKpJobs();
    showLog(result.message || "自动 KP 已推进。");
  }

  function rememberActiveMap(campaignId: string, mapId: string) {
    activeMapIdRef.current = mapId;
    writeActiveMapId(campaignId, credentialBridge.snapshot().role, mapId);
  }

  function applyAdminToken() {
    const nextToken = persistAdminToken();
    showLog(nextToken ? "管理员口令已仅保存在当前浏览器会话。" : "管理员口令已清除。");
  }

  function navigateWorkspace(id: typeof activeNav) {
    navigate(id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function activateCampaign(campaign: Campaign | null) {
    credentialBridge.session("");
    activeCampaignIdRef.current = campaign?.id ?? "";
    activeSessionIdRef.current = "";
    activeMapIdRef.current = "";
    campaignSelectionVersion.current += 1;
    mapRequestVersion.current += 1;
    setActiveCampaign(campaign);
    setMaps([]);
    setActiveMap(null);
    setSelectedTokenId("");
    setProposals([]);
    setActiveProposalId("");
    setProposalContext(null);
    setOverrideText("");
    setAuthIdentity(null);
    setActiveSession(null);
    setSessionMembers([]);
    setSessionSeats([]);
    setSkillChecks([]);
    setOpposedChecks([]);
    setVisibleSeatInvites({});
    setPcs([]);
    setContactInvestigators([]);
    setNpcReappearanceCandidates([]);
    setTokenActorId("");
    setPlayerActions([]);
    setActionAdjudication(null);
    handledAdjudicationIds.current.clear();
    setSelectedPlayerActionId("");
    setVisibleJoinCode("");
    setPlayerAction("");
    setProposalText("");
    showLog(
      campaign
        ? `已切换到《${campaign.title}》，正在读取当前身份可见的数据。`
        : "尚未选择团。"
    );
    resetRealtime();
  }

  function expireCurrentSession() {
    const campaign = activeCampaign;
    if (campaign) removeCampaignToken(campaign.id);
    activateCampaign(campaign);
  }

  function rememberSession(bundle: SessionBundle) {
    activateCampaign(bundle.campaign);
    if (bundle.player_token) {
      rememberPlayerToken(bundle.player_token);
    }
    credentialBridge.session(bundle.access_token, bundle.member.role, bundle.member.pc_id);
    writeCampaignToken(bundle.campaign.id, bundle.access_token);
    setAuthIdentity({
      member_id: bundle.member.id,
      session_id: bundle.member.session_id,
      campaign_id: bundle.member.campaign_id,
      role: bundle.member.role,
      display_name: bundle.member.display_name,
      pc_id: bundle.member.pc_id,
      player_profile_id: bundle.member.player_profile_id ?? bundle.profile?.id ?? null,
      seat_id: bundle.seat?.id ?? null
    });
    activeSessionIdRef.current = bundle.session.id;
    setActiveSession(bundle.session);
    setVisibleJoinCode(bundle.join_code ?? "");
    setCampaigns((items) => [bundle.campaign, ...items.filter((item) => item.id !== bundle.campaign.id)]);
    void loadMaps(bundle.campaign);
    void loadPcs(bundle.campaign);
    void loadSkillChecks(bundle.campaign);
    if (bundle.member.role === "kp") {
      void loadProposals(bundle.campaign);
      void loadNpcContactOptions(bundle.campaign, true);
      void loadSessionMembers(bundle.session.id);
      void loadSessionSeats(bundle.session.id);
      void loadPlayerActions(bundle.campaign);
    }
  }

  async function selectCampaign(campaign: Campaign) {
    activateCampaign(campaign);
    const version = campaignSelectionVersion.current;
    const storedToken = readCampaignToken(campaign.id);
    if (!storedToken) return;
    credentialBridge.session(storedToken);
    let identityError: unknown;
    const identity = await run(
      "恢复团会话",
      () => requestJson<AuthIdentity>("/auth/me"),
      (error) => {
        identityError = error;
      }
    );
    if (
      !identity &&
      activeCampaignIdRef.current === campaign.id &&
      campaignSelectionVersion.current === version &&
      isApiError(identityError, 401)
    ) {
      removeCampaignToken(campaign.id);
      credentialBridge.session("");
      return;
    }
    if (
      !identity ||
      activeCampaignIdRef.current !== campaign.id ||
      campaignSelectionVersion.current !== version
    ) return;
    if (identity.campaign_id !== campaign.id) {
      removeCampaignToken(campaign.id);
      credentialBridge.session("");
      return;
    }
    credentialBridge.session(storedToken, identity.role, identity.pc_id);
    let sessionError: unknown;
    const session = await run(
      "读取会话",
      () => requestJson<SessionInfo>(`/sessions/${identity.session_id}`),
      (error) => {
        sessionError = error;
      }
    );
    if (
      !session &&
      activeCampaignIdRef.current === campaign.id &&
      campaignSelectionVersion.current === version &&
      isApiError(sessionError, 401)
    ) {
      removeCampaignToken(campaign.id);
      credentialBridge.session("");
      return;
    }
    if (
      !session ||
      activeCampaignIdRef.current !== campaign.id ||
      campaignSelectionVersion.current !== version
    ) return;
    setAuthIdentity(identity);
    activeSessionIdRef.current = session.id;
    setActiveSession(session);
    void loadMaps(campaign);
    void loadPcs(campaign);
    void loadSkillChecks(campaign);
    if (identity.role === "kp") {
      void loadProposals(campaign);
      void loadNpcContactOptions(campaign, true);
      void loadSessionMembers(session.id);
      void loadSessionSeats(session.id);
      void loadPlayerActions(campaign);
    }
  }

  async function refreshIdentity(silent = false, refreshResources = true) {
    const accessToken = credentialBridge.snapshot().accessToken;
    const campaign = activeCampaign;
    const expectedSessionId = activeSessionIdRef.current;
    if (!accessToken || !campaign || !expectedSessionId) return;
    const identity = await perform(
      "刷新会话身份",
      () => requestJson<AuthIdentity>("/auth/me"),
      silent
    );
    if (
      !identity ||
      identity.campaign_id !== campaign.id ||
      identity.session_id !== expectedSessionId ||
      activeCampaignIdRef.current !== campaign.id ||
      activeSessionIdRef.current !== expectedSessionId
    ) return;
    credentialBridge.session(accessToken, identity.role, identity.pc_id);
    setAuthIdentity(identity);
    if (refreshResources) {
      void loadMaps(campaign, silent);
      void loadPcs(campaign, silent);
    }
  }

  async function startCampaignSession(campaign = activeCampaign) {
    if (!campaign) {
      showLog("请先创建或选择一个团。");
      return;
    }
    const version = campaignSelectionVersion.current;
    credentialBridge.session("");
    const bundle = await run("开启 KP 团会话", () =>
      requestJson<SessionBundle>(`/campaigns/${campaign.id}/sessions`, {
        method: "POST",
        body: JSON.stringify({ kp_display_name: kpDisplayName })
      })
    );
    if (
      bundle &&
      activeCampaignIdRef.current === campaign.id &&
      campaignSelectionVersion.current === version
    ) rememberSession(bundle);
  }

  async function recoverCampaignKp(campaign = activeCampaign) {
    if (!campaign) {
      showLog("请先选择需要恢复 KP 的团。");
      return;
    }
    const version = campaignSelectionVersion.current;
    credentialBridge.session("");
    const bundle = await run("重签并恢复 KP 凭证", () =>
      requestJson<SessionBundle>(`/campaigns/${campaign.id}/sessions/recover-kp`, {
        method: "POST",
        body: JSON.stringify({ kp_display_name: kpDisplayName })
      })
    );
    if (
      bundle &&
      activeCampaignIdRef.current === campaign.id &&
      campaignSelectionVersion.current === version
    ) rememberSession(bundle);
  }

  async function joinSession(event: FormEvent) {
    event.preventDefault();
    credentialBridge.session("");
    const bundle = await run("玩家加入团会话", () =>
      requestJson<SessionBundle>("/sessions/join", {
        method: "POST",
        body: JSON.stringify({
          join_code: joinCodeInput,
          display_name: playerDisplayName
        })
      })
    );
    if (bundle) rememberSession(bundle);
  }

  async function claimSessionSeat(event: FormEvent) {
    event.preventDefault();
    credentialBridge.session("");
    const bundle = await run("认领玩家席位", () =>
      requestJson<SessionBundle>("/session-seats/claim", {
        method: "POST",
        body: JSON.stringify({
          invitation_code: seatInvitationInput,
          display_name: playerDisplayName
        })
      })
    );
    if (bundle) {
      setSeatInvitationInput("");
      rememberSession(bundle);
      void loadRecoverableSeats();
    }
  }

  async function loadSessionSeats(sessionId = activeSession?.id, silent = false) {
    if (!sessionId || credentialBridge.snapshot().role !== "kp") return;
    const seats = await perform(
      "读取玩家席位",
      () => requestJson<SessionSeat[]>(`/sessions/${sessionId}/seats`),
      silent
    );
    if (seats && activeSessionIdRef.current === sessionId) setSessionSeats(seats);
  }

  async function loadRecoverableSeats(silent = false) {
    if (!credentialBridge.snapshot().playerToken) {
      setRecoverableSeats([]);
      return;
    }
    const seats = await perform(
      "读取我的历史席位",
      () => requestJson<SessionSeat[]>("/player-profile/session-seats"),
      silent
    );
    if (seats) setRecoverableSeats(seats);
  }

  async function createSessionSeat(event: FormEvent) {
    event.preventDefault();
    if (!activeSession || credentialBridge.snapshot().role !== "kp") return;
    const sessionId = activeSession.id;
    const result = await run("创建单席邀请", () =>
      requestJson<{ seat: SessionSeat; invitation_code: string }>(
        `/sessions/${sessionId}/seats`,
        {
          method: "POST",
          body: JSON.stringify({ label: seatLabel })
        }
      )
    );
    if (result && activeSessionIdRef.current === sessionId) {
      setVisibleSeatInvites((items) => ({
        ...items,
        [result.seat.id]: result.invitation_code
      }));
      setSeatLabel(`玩家席位 ${sessionSeats.length + 2}`);
      void loadSessionSeats(sessionId);
    }
  }

  async function reissueSessionSeat(seatId: string) {
    if (!activeSession || credentialBridge.snapshot().role !== "kp") return;
    const sessionId = activeSession.id;
    const result = await run("重新签发席位邀请", () =>
      requestJson<{ seat: SessionSeat; invitation_code: string }>(
        `/sessions/${sessionId}/seats/${seatId}/reissue`,
        { method: "POST" }
      )
    );
    if (result && activeSessionIdRef.current === sessionId) {
      setVisibleSeatInvites((items) => ({ ...items, [seatId]: result.invitation_code }));
      void loadSessionSeats(sessionId);
    }
  }

  async function revokeSessionSeat(seatId: string) {
    if (!activeSession || credentialBridge.snapshot().role !== "kp") return;
    const sessionId = activeSession.id;
    const result = await run("撤销玩家席位", () =>
      requestJson<SessionSeat>(`/sessions/${sessionId}/seats/${seatId}/revoke`, {
        method: "POST"
      })
    );
    if (result && activeSessionIdRef.current === sessionId) {
      setVisibleSeatInvites((items) => {
        const next = { ...items };
        delete next[seatId];
        return next;
      });
      void loadSessionSeats(sessionId);
      void loadSessionMembers(sessionId);
    }
  }

  async function recoverSessionSeat(seatId: string) {
    if (!credentialBridge.snapshot().playerToken) {
      showLog("当前浏览器没有可恢复的长期玩家身份。");
      return;
    }
    credentialBridge.session("");
    const bundle = await run("恢复玩家席位", () =>
      requestJson<SessionBundle>(`/session-seats/${seatId}/recover`, { method: "POST" })
    );
    if (bundle) rememberSession(bundle);
  }

  async function loadSessionMembers(sessionId = activeSession?.id, silent = false) {
    if (!sessionId || credentialBridge.snapshot().role !== "kp") return;
    const members = await perform(
      "读取会话成员",
      () => requestJson<SessionMember[]>(`/sessions/${sessionId}/members`),
      silent
    );
    if (members && activeSessionIdRef.current === sessionId) setSessionMembers(members);
  }

  async function loadPcs(campaign = activeCampaign, silent = false) {
    if (!campaign || !credentialBridge.snapshot().role) return;
    const result = await perform(
      "读取玩家角色",
      () => requestJson<PlayerCharacter[]>(`/campaigns/${campaign.id}/pcs`),
      silent
    );
    if (result && activeCampaignIdRef.current === campaign.id) {
      setPcs(result);
      setTokenActorId((current) => current || result[0]?.id || "");
    }
  }

  async function loadNpcContactOptions(
    campaign = activeCampaign,
    silent = false
  ) {
    if (!campaign || credentialBridge.snapshot().role !== "kp") return;
    const campaignId = campaign.id;
    const version = campaignSelectionVersion.current;
    const [investigators, candidates] = await Promise.all([
      perform(
        "读取 NPC 接触调查员",
        () =>
          requestJson<CampaignInvestigator[]>(
            `/campaigns/${campaignId}/investigator-submissions`
          ),
        silent
      ),
      perform(
        "读取可复现 NPC",
        () => listNpcReappearanceCandidates(campaignId),
        silent
      )
    ]);
    if (
      activeCampaignIdRef.current !== campaignId ||
      campaignSelectionVersion.current !== version
    ) return;
    if (investigators) {
      setContactInvestigators(
        investigators.filter((item) => item.status === "approved")
      );
    }
    if (candidates) setNpcReappearanceCandidates(candidates);
  }

  async function loadPlayerActions(campaign = activeCampaign, silent = false) {
    if (!campaign || credentialBridge.snapshot().role !== "kp") return;
    const actions = await perform(
      "读取玩家行动",
      () => requestJson<PlayerActionRecord[]>(`/campaigns/${campaign.id}/actions`),
      silent
    );
    if (actions && activeCampaignIdRef.current === campaign.id) {
      setPlayerActions(actions);
      setSelectedPlayerActionId((current) =>
        actions.some((action) => action.id === current && action.status === "submitted")
          ? current
          : ""
      );
    }
  }

  async function submitPlayerAction() {
    if (!activeCampaign || credentialBridge.snapshot().role !== "player") {
      showLog("只有已加入团会话的玩家可以提交行动。");
      return;
    }
    const campaignId = activeCampaign.id;
    const result = await run("提交玩家行动", () =>
      requestJson<PlayerActionRecord | AutoTurnResult>(`/campaigns/${campaignId}/actions`, {
        method: "POST",
        body: JSON.stringify({
          action_text: playerAction,
          token_id: selectedToken?.id ?? null,
          map_id: activeMap?.id ?? null,
          client_action_id: crypto.randomUUID(),
          auto_advance: autoKpEnabled,
          background: autoKpEnabled
        })
      })
    );
    if (!result || activeCampaignIdRef.current !== campaignId) return;
    if (isAutoTurnResult(result)) {
      mergeAutoTurnResult(result, campaignId);
    } else {
      showLog("行动已提交，等待 KP 处理。");
    }
    void loadSkillChecks(activeCampaign, true);
  }

  async function confirmActionAdjudication(selectedSkill: string | null) {
    if (!actionAdjudication) return;
    const campaignId = activeCampaign?.id ?? "";
    const handledId = actionAdjudication.id;
    const result = await run("确认 AI 裁定", () =>
      requestJson<AutoTurnResult>(
        `/player-actions/${actionAdjudication.action_id}/adjudication/confirm`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_version: actionAdjudication.version,
            selected_skill: selectedSkill
          })
        }
      )
    );
    if (!result) return;
    handledAdjudicationIds.current.add(handledId);
    mergeAutoTurnResult(result, campaignId);
    setActionAdjudication(null);
  }

  async function reviseActionAdjudication() {
    if (!actionAdjudication) return;
    const campaignId = activeCampaign?.id ?? "";
    const handledId = actionAdjudication.id;
    const result = await run("修改行动并重新裁定", () =>
      requestJson<AutoTurnResult>(
        `/player-actions/${actionAdjudication.action_id}/adjudication/revise`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_version: actionAdjudication.version,
            action_text: playerAction,
            background: true
          })
        }
      )
    );
    if (!result) return;
    handledAdjudicationIds.current.add(handledId);
    setActionAdjudication(null);
    mergeAutoTurnResult(result, campaignId);
  }

  async function retryFailedAutoKpJob(jobId: string) {
    const result = await run("重新排入 Auto KP 任务", () => retryAutoKpJob(jobId));
    if (result) showLog("任务已重新排队，后台将继续处理。");
  }

  async function loadSkillChecks(campaign = activeCampaign, silent = false) {
    if (!campaign || !credentialBridge.snapshot().role) return;
    const checks = await perform(
      "读取待检定",
      () => requestJson<SkillCheck[]>(`/campaigns/${campaign.id}/checks`),
      silent
    );
    if (checks && activeCampaignIdRef.current === campaign.id) setSkillChecks(checks);
    const contests = await perform(
      "读取对抗检定",
      () => requestJson<OpposedCheck[]>(`/campaigns/${campaign.id}/opposed-checks`),
      silent
    );
    if (contests && activeCampaignIdRef.current === campaign.id) setOpposedChecks(contests);
  }

  async function createOpposedCheck(input: CreateOpposedCheckInput) {
    if (!activeCampaign || credentialBridge.snapshot().role !== "kp") return;
    const result = await run("发布对抗检定", () =>
      requestJson<OpposedCheck>(`/campaigns/${activeCampaign.id}/opposed-checks`, {
        method: "POST",
        body: JSON.stringify(input)
      })
    );
    if (result) void loadSkillChecks(activeCampaign, true);
  }

  async function resolveOpposedCheck(opposedCheckId: string) {
    const result = await run("裁决对抗检定", () =>
      requestJson<OpposedCheck>(`/opposed-checks/${opposedCheckId}/resolve`, {
        method: "POST"
      })
    );
    if (result) setOpposedChecks((items) => items.map((item) => item.id === result.id ? result : item));
  }

  async function rerollOpposedCheck(opposedCheckId: string) {
    const result = await run("创建对抗重掷", () =>
      requestJson<OpposedCheck>(`/opposed-checks/${opposedCheckId}/reroll`, {
        method: "POST"
      })
    );
    if (result) void loadSkillChecks(activeCampaign, true);
  }

  async function createSkillCheck(input: CreateSkillCheckInput) {
    if (!activeCampaign || credentialBridge.snapshot().role !== "kp") return;
    const campaignId = activeCampaign.id;
    const result = await run("发布规则检定", () =>
      requestJson<SkillCheck>(`/campaigns/${campaignId}/checks`, {
        method: "POST",
        body: JSON.stringify(input)
      })
    );
    if (result && activeCampaignIdRef.current === campaignId) {
      setSkillChecks((items) => [result, ...items.filter((item) => item.id !== result.id)]);
    }
  }

  async function resolveSkillCheck(
    checkId: string,
    inputMethod: "digital" | "physical",
    onesDigit?: number,
    tensDigits: number[] = []
  ) {
    const campaignId = activeCampaign?.id ?? "";
    const result = await run(inputMethod === "digital" ? "投掷数字骰" : "录入实体骰", () =>
      requestJson<SkillCheck | AutoTurnResult>(`/checks/${checkId}/resolve`, {
        method: "POST",
        body: JSON.stringify({
          input_method: inputMethod,
          ones_digit: inputMethod === "physical" ? onesDigit : null,
          tens_digits: inputMethod === "physical" ? tensDigits : [],
          auto_advance: autoKpEnabled && credentialBridge.snapshot().role === "player",
          background: autoKpEnabled && credentialBridge.snapshot().role === "player"
        })
      })
    );
    if (result) {
      if (isAutoTurnResult(result)) {
        mergeAutoTurnResult(result, campaignId);
      } else {
        setSkillChecks((items) => items.map((item) => item.id === result.id ? result : item));
      }
      if (authIdentity?.role === "kp") void loadPlayerActions(activeCampaign, true);
    }
  }

  async function replaySkillCheck(checkId: string) {
    await run("重放检定校验", () =>
      requestJson(`/checks/${checkId}/replay`, { method: "POST" })
    );
  }

  async function overrideSkillCheck(
    checkId: string,
    successLevel: NonNullable<SkillCheck["success_level"]>,
    passed: boolean,
    reason: string
  ) {
    const result = await run("记录 KP 覆盖", () =>
      requestJson<SkillCheck>(`/checks/${checkId}/override`, {
        method: "POST",
        body: JSON.stringify({ success_level: successLevel, passed, reason })
      })
    );
    if (result) setSkillChecks((items) => items.map((item) => item.id === result.id ? result : item));
  }

  async function decideSkillCheck(
    checkId: string,
    action: "cancel" | "push",
    reason: string
  ) {
    const result = await run(action === "cancel" ? "取消检定" : "创建孤注一掷", () =>
      requestJson<SkillCheck>(`/checks/${checkId}/${action}`, {
        method: "POST",
        body: JSON.stringify({ reason })
      })
    );
    if (result) void loadSkillChecks(activeCampaign);
  }

  async function generateCheckConsequence(checkId: string) {
    if (!activeCampaign || credentialBridge.snapshot().role !== "kp") {
      showLog("只有 KP 可以生成检定后果草稿。");
      return;
    }
    const campaignId = activeCampaign.id;
    const result = await run("生成检定后果草稿", () =>
      requestJson<TurnProposal>(`/checks/${checkId}/consequence-proposal`, {
        method: "POST"
      })
    );
    if (result && activeCampaignIdRef.current === campaignId) {
      setProposals((items) => [result, ...items.filter((item) => item.id !== result.id)]);
      setActiveProposalId(result.id);
      setProposalContext(null);
      setOverrideText("");
    }
  }

  async function rotateJoinCode() {
    if (!activeSession || credentialBridge.snapshot().role !== "kp") return;
    const sessionId = activeSession.id;
    const result = await run("更换玩家加入码", () =>
      requestJson<{ session: SessionInfo; join_code: string }>(
        `/sessions/${sessionId}/rotate-join-code`,
        { method: "POST" }
      )
    );
    if (result && activeSessionIdRef.current === sessionId) setVisibleJoinCode(result.join_code);
  }

  async function revokeMember(memberId: string) {
    if (!activeSession || credentialBridge.snapshot().role !== "kp") return;
    const sessionId = activeSession.id;
    const result = await run("撤销玩家会话", () =>
      requestJson<{ member: SessionMember; join_code: string }>(
        `/sessions/${sessionId}/members/${memberId}/revoke`,
        { method: "POST" }
      )
    );
    if (result && activeSessionIdRef.current === sessionId) {
      setVisibleJoinCode(result.join_code);
      void loadSessionMembers(sessionId);
    }
  }

  async function closeSession() {
    if (!activeSession || !activeCampaign || credentialBridge.snapshot().role !== "kp") return;
    const sessionId = activeSession.id;
    const result = await run("关闭团会话", () =>
      requestJson<SessionInfo>(`/sessions/${sessionId}/close`, { method: "POST" })
    );
    if (!result || activeSessionIdRef.current !== sessionId) return;
    expireRealtime("closed");
  }

  async function loadCampaigns() {
    const listRequest = ++campaignListRequestVersion.current;
    const selectionVersion = campaignSelectionVersion.current;
    const storedTokens = listStoredCampaignTokens();
    let result = await run(
      "以本机管理员读取团列表",
      () => requestJsonWithAccessToken<Campaign[]>("/campaigns", "")
    );
    if (campaignListRequestVersion.current !== listRequest) return;
    for (const stored of storedTokens) {
      if (result) break;
      let storedTokenError: unknown;
      result = await run(
        "以团会话读取团",
        () => requestJsonWithAccessToken<Campaign[]>("/campaigns", stored.token),
        (error) => {
          storedTokenError = error;
        }
      );
      if (campaignListRequestVersion.current !== listRequest) return;
      if (!result && isApiError(storedTokenError, 401)) {
        removeCampaignToken(stored.campaignId);
      }
    }
    if (result) {
      setCampaigns(result);
      if (campaignSelectionVersion.current !== selectionVersion) return;
      const currentCampaign = result.find(
        (campaign) => campaign.id === activeCampaignIdRef.current
      );
      const firstCampaign =
        currentCampaign ??
        result.find((campaign) => readCampaignToken(campaign.id)) ??
        result[0] ??
        null;
      if (firstCampaign) void selectCampaign(firstCampaign);
      else activateCampaign(null);
    }
  }

  async function createCampaign(event: FormEvent) {
    event.preventDefault();
    const result = await run("创建测试团", () =>
      requestJson<Campaign>("/campaigns", {
        method: "POST",
        body: JSON.stringify({
          title: campaignTitle,
          system: "coc7",
          current_time: campaignTime
        })
      })
    );
    if (result) {
      setCampaigns((items) => [result, ...items]);
      activateCampaign(result);
      void startCampaignSession(result);
    }
  }

  async function loadMaps(campaign = activeCampaign, silent = false) {
    const role = credentialBridge.snapshot().role;
    if (!campaign || !role) return;
    const version = campaignSelectionVersion.current;
    const view = role === "player" ? "player" : "kp";
    const result = await perform(
      "读取地图列表",
      () => requestJson<SavedMap[]>(`/campaigns/${campaign.id}/maps?view=${view}`),
      silent
    );
    if (result && activeCampaignIdRef.current === campaign.id && campaignSelectionVersion.current === version) {
      setMaps(result);
      const visibleIds = new Set(result.map((savedMap) => savedMap.id));
      const currentMapId = activeMapIdRef.current;
      if (currentMapId && !visibleIds.has(currentMapId)) {
        activeMapIdRef.current = "";
        setActiveMap(null);
        setSelectedTokenId("");
      }
      const storedMapId = readActiveMapId(campaign.id, role);
      const mapToRestore =
        result.find((savedMap) => savedMap.id === currentMapId) ??
        result.find((savedMap) => savedMap.id === storedMapId) ??
        result[0];
      if (mapToRestore && mapToRestore.id !== currentMapId) {
        void openMap(mapToRestore.id, campaign.id, true);
      }
    }
  }

  async function generateMap(input: MapGenerationInput) {
    if (!activeCampaign || credentialBridge.snapshot().role !== "kp") {
      showLog("请先以 KP 身份开启或恢复该团会话。");
      return;
    }
    const campaignId = activeCampaign.id;
    const version = campaignSelectionVersion.current;
    const result = await run("生成并保存地图", () =>
      requestJson<SavedMap>(`/campaigns/${campaignId}/maps/generate`, {
        method: "POST",
        body: JSON.stringify(input)
      })
    );
    if (result && activeCampaignIdRef.current === campaignId && campaignSelectionVersion.current === version) {
      mapRequestVersion.current += 1;
      rememberActiveMap(campaignId, result.id);
      setActiveMap(result);
      setSelectedTokenId("");
      setMaps((items) => [result, ...items.filter((item) => item.id !== result.id)]);
      setTokenLocation(result.locations?.[0]?.name ?? tokenLocation);
      setMoveTarget(result.locations?.[1]?.name ?? moveTarget);
    }
  }

  async function generateMapBackground(seed: number | null) {
    if (!activeMap || credentialBridge.snapshot().role !== "kp") {
      showLog("请先以 KP 身份打开一张地图。");
      return;
    }
    const mapId = activeMap.id;
    const size = mapImageSize(activeMap.width, activeMap.height);
    const result = await run("生成玩家安全的时代背景候选", () =>
      requestJson(`/maps/${mapId}/image-assets/generate`, {
        method: "POST",
        body: JSON.stringify({ ...size, seed })
      })
    );
    if (result && activeMapIdRef.current === mapId) {
      await openMap(mapId);
    }
  }

  async function selectMapAsset(assetId: string) {
    if (!activeMap || credentialBridge.snapshot().role !== "kp") return;
    const mapId = activeMap.id;
    const result = await run("选用地图背景候选", () =>
      requestJson(`/maps/${mapId}/assets/${assetId}/select`, {
        method: "POST"
      })
    );
    if (result && activeMapIdRef.current === mapId) {
      await openMap(mapId);
    }
  }

  async function openMap(
    mapId: string,
    campaignId = activeCampaignIdRef.current,
    silent = false
  ): Promise<boolean> {
    const { role, pcId } = credentialBridge.snapshot();
    if (!role) return false;
    const version = campaignSelectionVersion.current;
    const mapRequest = ++mapRequestVersion.current;
    const view = role === "player" ? "player" : "kp";
    const result = await perform(
      "打开地图",
      () => requestJson<SavedMap>(`/maps/${mapId}?view=${view}`),
      silent
    );
    if (
      result &&
      result.campaign_id === campaignId &&
      activeCampaignIdRef.current === campaignId &&
      campaignSelectionVersion.current === version &&
      mapRequestVersion.current === mapRequest
    ) {
      rememberActiveMap(campaignId, result.id);
      setActiveMap(result);
      const preferredToken =
        role === "player"
          ? result.tokens?.find(
              (token) => token.actor_type === "pc" && token.actor_id === pcId
            )
          : result.tokens?.[0];
      setSelectedTokenId(preferredToken?.id ?? "");
      setTokenLocation(result.locations?.[0]?.name ?? "");
      setMoveTarget(result.locations?.[1]?.name ?? result.locations?.[0]?.name ?? "");
      return true;
    }
    return false;
  }

  async function setMapPublished(published: boolean) {
    if (!activeMap || credentialBridge.snapshot().role !== "kp") return;
    const mapId = activeMap.id;
    const campaignId = activeMap.campaign_id;
    const publishSnapshot =
      published && activeMap.revision_id
        ? {
            expected_revision_id: activeMap.revision_id,
            expected_selected_asset_id: activeMap.render?.selected_asset_id ?? null
          }
        : null;
    const result = await run(published ? "发布地图" : "收回地图", () =>
      requestJson<SavedMap>(`/maps/${mapId}/${published ? "publish" : "unpublish"}`, {
        method: "POST",
        body: publishSnapshot ? JSON.stringify(publishSnapshot) : undefined
      })
    );
    if (
      result &&
      activeCampaignIdRef.current === campaignId &&
      activeMapIdRef.current === mapId
    ) {
      setActiveMap(result);
      setMaps((items) => items.map((item) => (item.id === result.id ? result : item)));
    }
  }

  async function placeToken(event: FormEvent) {
    event.preventDefault();
    if (
      credentialBridge.snapshot().role !== "kp" ||
      !activeMap ||
      activeMap.campaign_id !== activeCampaignIdRef.current
    ) {
      showLog("请先以 KP 身份打开一张地图。");
      return;
    }
    const mapId = activeMap.id;
    const campaignId = activeMap.campaign_id;
    const result = await run("放置棋子", () =>
      requestJson<MapToken>(`/maps/${mapId}/tokens`, {
        method: "POST",
        body: JSON.stringify({
          label: tokenLabel,
          actor_type: "pc",
          actor_id: tokenActorId || null,
          location_name: tokenLocation,
          color: "#2f6db3"
        })
      })
    );
    if (
      result &&
      activeCampaignIdRef.current === campaignId &&
      activeMapIdRef.current === mapId
    ) {
      if (await openMap(mapId, campaignId, true)) setSelectedTokenId(result.id);
    }
  }

  async function moveToken(event: FormEvent) {
    event.preventDefault();
    if (!selectedToken || !activeMap || selectedToken.map_id !== activeMap.id) {
      showLog("请先选择一个棋子。");
      return;
    }
    const mapId = activeMap.id;
    const campaignId = activeMap.campaign_id;
    const result = await run("移动棋子", () =>
      requestJson<MapToken>(`/map-tokens/${selectedToken.id}/move`, {
        method: "POST",
        body: JSON.stringify({
          to_location_name: moveTarget,
          expected_version: selectedToken.version,
          note: "前端开发测试移动。"
        })
      })
    );
    if (
      result &&
      campaignId === activeCampaignIdRef.current &&
      activeMapIdRef.current === mapId
    ) {
      if (await openMap(mapId, campaignId, true)) setSelectedTokenId(result.id);
    }
  }

  async function searchMemory() {
    const role = credentialBridge.snapshot().role;
    if (!activeCampaign || !role) {
      showLog("请先加入或恢复团会话。");
      return;
    }
    const view = role === "player" ? "player" : "kp";
    await run("检索记忆", () =>
      requestJson(
        `/campaigns/${activeCampaign.id}/memory/search?q=${encodeURIComponent(playerAction)}&view=${view}`
      )
    );
  }

  async function loadProposals(campaign = activeCampaign, silent = false) {
    if (!campaign || credentialBridge.snapshot().role !== "kp") return;
    const version = campaignSelectionVersion.current;
    const result = await perform(
      "读取 AI 草稿",
      () => requestJson<TurnProposal[]>(`/campaigns/${campaign.id}/proposals`),
      silent
    );
    if (result && activeCampaignIdRef.current === campaign.id && campaignSelectionVersion.current === version) {
      setProposals(result);
      setActiveProposalId(result[0]?.id ?? "");
      setProposalContext(null);
    }
  }

  async function createProposal(event?: FormEvent) {
    event?.preventDefault();
    if (!activeCampaign || credentialBridge.snapshot().role !== "kp") {
      showLog("只有 KP 可以创建草稿。");
      return;
    }
    const campaignId = activeCampaign.id;
    const result = await run("创建审批草稿", () =>
      requestJson<TurnProposal>(`/campaigns/${campaignId}/proposals`, {
        method: "POST",
        body: JSON.stringify({
          player_action_id: selectedPlayerActionId || null,
          player_action: playerAction,
          public_narration: proposalText,
          kp_notes: "开发模式手动草稿。正式模式由 AI KP 生成，批准后才落库。",
          proposed_memories: [
            {
              scope: "campaign_fact",
              importance: 2,
              visibility: "table",
              text: `草稿记忆：${playerAction}`
            }
          ]
        })
      })
    );
    if (result && activeCampaignIdRef.current === campaignId) {
      setProposals((items) => [result, ...items.filter((item) => item.id !== result.id)]);
      setActiveProposalId(result.id);
      setSelectedPlayerActionId("");
      void loadPlayerActions(activeCampaign);
      void loadNpcContactOptions(activeCampaign, true);
    }
  }

  async function generateAiProposal() {
    if (!activeCampaign || credentialBridge.snapshot().role !== "kp") {
      showLog("只有 KP 可以调用 AI KP。");
      return;
    }
    const campaignId = activeCampaign.id;
    const result = await run("调用本地 AI 生成结构化草稿", () =>
      requestJson<TurnProposal>("/kp/turn", {
        method: "POST",
        body: JSON.stringify({
          campaign_id: campaignId,
          player_action_id: selectedPlayerActionId || null,
          player_action: playerAction,
          location: selectedToken?.location_name ?? null,
          map_id: activeMap?.id ?? null,
          active_spoiler_tags: []
        })
      })
    );
    if (result && activeCampaignIdRef.current === campaignId) {
      setProposals((items) => [result, ...items.filter((item) => item.id !== result.id)]);
      setActiveProposalId(result.id);
      setProposalContext(null);
      setOverrideText("");
      setSelectedPlayerActionId("");
      void loadPlayerActions(activeCampaign);
    }
  }

  async function approveProposal() {
    if (
      credentialBridge.snapshot().role !== "kp" ||
      !activeProposal ||
      activeProposal.status !== "draft"
    ) {
      showLog("没有可审批草稿。");
      return;
    }
    const proposalId = activeProposal.id;
    const campaignId = activeProposal.campaign_id;
    const result = await run("批准草稿并落库", () =>
      requestJson<TurnProposal>(`/kp/proposals/${proposalId}/approve`, {
        method: "POST",
        body: JSON.stringify({
          note: "前端开发台批准。",
          override_public_narration: overrideText.trim() || null
        })
      })
    );
    if (result && activeCampaignIdRef.current === campaignId) {
      setProposals((items) => items.map((item) => (item.id === result.id ? result : item)));
      setOverrideText("");
      void loadPlayerActions(activeCampaign);
    }
  }

  async function confirmWorldExpansionContact(
    input: WorldExpansionEncounterInput
  ) {
    if (
      credentialBridge.snapshot().role !== "kp" ||
      !activeProposal ||
      activeProposal.proposal_kind !== "world_expansion" ||
      activeProposal.status !== "approved"
    ) {
      showLog("只有已批准的世界补全候选可以确认实际接触。");
      return;
    }
    const proposalId = activeProposal.id;
    const campaignId = activeProposal.campaign_id;
    const result = await run("确认实际接触并写入世界", () =>
      materializeWorldExpansionEncounter(proposalId, input)
    );
    if (
      !result ||
      activeCampaignIdRef.current !== campaignId ||
      result.id !== proposalId
    ) return;
    setProposals((items) =>
      items.map((item) => (item.id === result.id ? result : item))
    );
    if (activeCampaign) {
      void loadMaps(activeCampaign);
      void loadNpcContactOptions(activeCampaign, true);
    }
  }

  async function rejectProposal() {
    if (
      credentialBridge.snapshot().role !== "kp" ||
      !activeProposal ||
      activeProposal.status !== "draft"
    ) {
      showLog("没有可拒绝草稿。");
      return;
    }
    const proposalId = activeProposal.id;
    const campaignId = activeProposal.campaign_id;
    const result = await run("拒绝草稿", () =>
      requestJson<TurnProposal>(`/kp/proposals/${proposalId}/reject`, {
        method: "POST",
        body: JSON.stringify({
          note: "前端开发台拒绝。"
        })
      })
    );
    if (result && activeCampaignIdRef.current === campaignId) {
      setProposals((items) => items.map((item) => (item.id === result.id ? result : item)));
      void loadPlayerActions(activeCampaign);
    }
  }

  async function inspectProposalContext() {
    if (credentialBridge.snapshot().role !== "kp" || !activeProposal) {
      showLog("没有可检查的草稿。");
      return;
    }
    const proposalId = activeProposal.id;
    const campaignId = activeProposal.campaign_id;
    const result = await run("读取 AI 上下文快照", () =>
      requestJson<ContextAssembly | null>(`/kp/proposals/${proposalId}/context`)
    );
    if (
      result === undefined ||
      activeCampaignIdRef.current !== campaignId ||
      (result !== null && result.proposal_id !== proposalId)
    ) return;
    setProposalContext(result);
    if (result === null) showLog("这是手工草稿，没有 AI 上下文快照。");
  }

  async function refreshRealtimeMaps() {
    const campaign = activeCampaign;
    if (!campaign) return;
    const mapIdBeforeRefresh = activeMapIdRef.current;
    await loadMaps(campaign, true);
    if (mapIdBeforeRefresh && activeMapIdRef.current === mapIdBeforeRefresh) {
      await openMap(mapIdBeforeRefresh, campaign.id, true);
    }
  }

  const {
    status: realtimeStatus,
    note: realtimeNote,
    reset: resetRealtime,
    reportRefreshFailure: reportRealtimeRefreshFailure,
    expireCurrentScope: expireRealtime
  } = useWorkspaceRealtime({
    campaign: activeCampaign,
    session: activeSession,
    identity: authIdentity,
    callbacks: {
      refreshIdentity: () => void refreshIdentity(true, false),
      refreshMembers: () => void loadSessionMembers(activeSession?.id, true),
      refreshSeats: () => void loadSessionSeats(activeSession?.id, true),
      refreshChecks: () => void loadSkillChecks(activeCampaign, true),
      refreshPcs: () => void loadPcs(activeCampaign, true),
      refreshActions: () => void loadPlayerActions(activeCampaign, true),
      refreshProposals: () => void loadProposals(activeCampaign, true),
      refreshMaps: () => void refreshRealtimeMaps(),
      expireSession: expireCurrentSession
    }
  });
  reportRealtimeRefreshFailureRef.current = reportRealtimeRefreshFailure;

  useEffect(() => {
    void loadCampaigns();
    void loadRecoverableSeats(true);
  }, []);

  const activePc = pcs.find((pc) => pc.id === authIdentity?.pc_id) ?? null;
  const otherPcs = pcs.filter((pc) => pc.id !== authIdentity?.pc_id);
  const renderedPage = resolveWorkspaceRoute(activeNav, authIdentity?.role);
  const renderedNav = renderedPage.id;

  return (
    <AppLayout
      activePage={renderedNav}
      campaignTitle={activeCampaign?.title ?? "AI KP Local"}
      currentRoute={renderedPage}
      identity={authIdentity}
      loading={loading}
      onNavigate={navigateWorkspace}
      onRefresh={() => void loadCampaigns()}
      realtimeNote={realtimeNote}
      realtimeStatus={realtimeStatus}
    >

        {renderedNav === "investigators" && (
          <Suspense fallback={<section className="page-card">正在载入调查员工作台……</section>}>
            <InvestigatorPage campaign={activeCampaign} identity={authIdentity} />
          </Suspense>
        )}

        {renderedNav === "rules" && (
          <Suspense fallback={<section className="page-card">正在载入规则知识……</section>}>
            <RulebookPage
              identity={authIdentity}
              key={`${authIdentity?.campaign_id ?? "none"}:${authIdentity?.member_id ?? "anonymous"}:${authIdentity?.role ?? "guest"}`}
            />
          </Suspense>
        )}

        {renderedNav === "modules" && (
          <Suspense fallback={<section className="page-card">正在载入 KP 本库……</section>}>
            <ModuleLibraryPage
              campaign={activeCampaign}
              identity={authIdentity}
              key={`${activeCampaign?.id ?? "none"}:${authIdentity?.member_id ?? "anonymous"}`}
            />
          </Suspense>
        )}

        {renderedNav === "models" && (
          <Suspense fallback={<section className="page-card">正在载入模型设置……</section>}>
            <ModelSettingsPage />
          </Suspense>
        )}

        {renderedNav === "npcs" && (
          <Suspense fallback={<section className="page-card">正在载入 NPC 工作台……</section>}>
            <NpcWorkspace campaign={activeCampaign} identity={authIdentity} />
          </Suspense>
        )}

        {renderedNav === "campaigns" && <div className="page-grid campaign-page-grid">
          <CampaignPanel
            activeCampaignId={activeCampaign?.id}
            adminToken={adminToken}
            campaigns={campaigns}
            campaignTime={campaignTime}
            campaignTitle={campaignTitle}
            onAdminTokenChange={setAdminToken}
            onApplyAdminToken={applyAdminToken}
            onCampaignTimeChange={setCampaignTime}
            onCampaignTitleChange={setCampaignTitle}
            onCreateCampaign={createCampaign}
            onSelectCampaign={(campaign) => void selectCampaign(campaign)}
            role={authIdentity?.role}
          />

          <SessionPanel
            activeCampaignPresent={Boolean(activeCampaign)}
            identity={authIdentity}
            joinCodeInput={joinCodeInput}
            kpDisplayName={kpDisplayName}
            members={sessionMembers}
            seats={sessionSeats}
            recoverableSeats={recoverableSeats}
            visibleSeatInvites={visibleSeatInvites}
            seatInvitationInput={seatInvitationInput}
            seatLabel={seatLabel}
            onCloseSession={() => void closeSession()}
            onCreateSeat={createSessionSeat}
            onClaimSeat={claimSessionSeat}
            onJoinCodeInputChange={setJoinCodeInput}
            onJoinSession={joinSession}
            onKpDisplayNameChange={setKpDisplayName}
            onPlayerDisplayNameChange={setPlayerDisplayName}
            onSeatInvitationInputChange={setSeatInvitationInput}
            onSeatLabelChange={setSeatLabel}
            onOpenInvestigators={() => navigateWorkspace("investigators")}
            onRefreshIdentity={() => void refreshIdentity()}
            onRefreshMembers={() => void loadSessionMembers()}
            onRefreshSeats={() => void loadSessionSeats()}
            onRefreshRecoverableSeats={() => void loadRecoverableSeats()}
            onRecoverSeat={(seatId) => void recoverSessionSeat(seatId)}
            onReissueSeat={(seatId) => void reissueSessionSeat(seatId)}
            onRevokeMember={(memberId) => void revokeMember(memberId)}
            onRevokeSeat={(seatId) => void revokeSessionSeat(seatId)}
            onRotateJoinCode={() => void rotateJoinCode()}
            onRecoverKp={() => void recoverCampaignKp()}
            onStartSession={() => void startCampaignSession()}
            playerDisplayName={playerDisplayName}
            realtimeNote={realtimeNote}
            realtimeStatus={realtimeStatus}
            session={activeSession}
            visibleJoinCode={visibleJoinCode}
          />

          <section className="response-panel page-log-panel">
            <div className="panel-heading"><h2>团管理记录</h2><AlertCircle size={18} /></div>
            <pre>{log}</pre>
          </section>
        </div>}

        {renderedNav === "maps" && <div className={`page-grid map-page-grid ${authIdentity?.role ?? "guest"}`}>
          {authIdentity?.role === "kp" && (
            <MapGeneratorPanel
              campaign={activeCampaign}
              loading={loading}
              onGenerate={generateMap}
            />
          )}

          <MapStage
            activeMap={activeMap}
            hasIdentity={Boolean(authIdentity)}
            loading={loading}
            maps={maps}
            onGenerateBackground={(seed) => void generateMapBackground(seed)}
            onOpenMap={(mapId) => void openMap(mapId)}
            onRefresh={() => void refreshRealtimeMaps()}
            onSelectAsset={(assetId) => void selectMapAsset(assetId)}
            onSetPublished={(published) => void setMapPublished(published)}
            role={authIdentity?.role}
          />

          <TokenPanel
            activeMap={activeMap}
            identity={authIdentity}
            movableTokens={movableTokens}
            moveTarget={moveTarget}
            onMoveTargetChange={setMoveTarget}
            onMoveToken={moveToken}
            onPlaceToken={placeToken}
            onSelectedTokenIdChange={setSelectedTokenId}
            onTokenActorIdChange={(nextId) => {
              setTokenActorId(nextId);
              const pc = pcs.find((item) => item.id === nextId);
              if (pc) setTokenLabel(pc.name);
            }}
            onTokenLabelChange={setTokenLabel}
            onTokenLocationChange={setTokenLocation}
            pcs={pcs}
            selectedToken={selectedToken}
            selectedTokenId={selectedTokenId}
            tokenActorId={tokenActorId}
            tokenLabel={tokenLabel}
            tokenLocation={tokenLocation}
          />

          <RoutePlanPanel map={activeMap} identity={authIdentity} />

          {authIdentity?.role === "kp" && (
            <MapStructureEditor
              map={activeMap}
              onSaved={() => activeMap && void openMap(activeMap.id)}
            />
          )}

          <section className="response-panel page-log-panel">
            <div className="panel-heading"><h2>地图操作记录</h2><AlertCircle size={18} /></div>
            <pre>{log}</pre>
          </section>
        </div>}

        {renderedNav === "play" && authIdentity?.role === "kp" && (
          <KpWorkspace
            adjudication={null}
            activeCampaign={activeCampaign}
            activeMap={activeMap}
            activePc={activePc}
            activeProposal={activeProposal}
            authIdentity={authIdentity}
            autoKpEnabled={autoKpEnabled}
            autoKpJobs={autoKpJobs}
            campaignTime={campaignTime}
            characterExpanded={characterExpanded}
            checks={skillChecks}
            contactInvestigators={contactInvestigators}
            kpActionTab={kpActionTab}
            loading={loading}
            log={log}
            maps={maps}
            members={sessionMembers}
            movableTokens={movableTokens}
            moveTarget={moveTarget}
            npcReappearanceCandidates={npcReappearanceCandidates}
            onApprove={() => void approveProposal()}
            onAutoKpEnabledChange={setAutoKpEnabled}
            onCancel={(checkId, reason) => void decideSkillCheck(checkId, "cancel", reason)}
            onConfirmWorldExpansion={(input) => void confirmWorldExpansionContact(input)}
            onCreate={(input) => void createSkillCheck(input)}
            onCreateOpposed={(input) => void createOpposedCheck(input)}
            onCreateProposal={() => void createProposal()}
            onConfirmAdjudication={(skill) => void confirmActionAdjudication(skill)}
            onGenerateAiProposal={() => void generateAiProposal()}
            onGenerateConsequence={(checkId) => void generateCheckConsequence(checkId)}
            onInspectContext={() => void inspectProposalContext()}
            onKpActionTabChange={setKpActionTab}
            onMoveTargetChange={setMoveTarget}
            onMoveToken={moveToken}
            onOpenMap={(mapId) => void openMap(mapId)}
            onOverride={(checkId, successLevel, passed, reason) => void overrideSkillCheck(checkId, successLevel, passed, reason)}
            onOverrideTextChange={setOverrideText}
            onPlaceToken={placeToken}
            onPlayerActionChange={setPlayerAction}
            onProposalTextChange={setProposalText}
            onPush={(checkId, reason) => void decideSkillCheck(checkId, "push", reason)}
            onRefresh={() => void loadProposals()}
            onRefreshMaps={() => void refreshRealtimeMaps()}
            onRefreshPlayerActions={() => void loadPlayerActions()}
            onRetryAutoKpJob={(jobId) => void retryFailedAutoKpJob(jobId)}
            onReviseAdjudication={() => void reviseActionAdjudication()}
            onReject={() => void rejectProposal()}
            onReplay={(checkId) => void replaySkillCheck(checkId)}
            onRerollOpposed={(opposedCheckId) => void rerollOpposedCheck(opposedCheckId)}
            onResolveDigital={(checkId) => void resolveSkillCheck(checkId, "digital")}
            onResolveOpposed={(opposedCheckId) => void resolveOpposedCheck(opposedCheckId)}
            onResolvePhysical={(checkId, onesDigit, tensDigits) => void resolveSkillCheck(checkId, "physical", onesDigit, tensDigits)}
            onSearchMemory={() => void searchMemory()}
            onSelectPlayerAction={(action) => {
              setSelectedPlayerActionId(action.id);
              setPlayerAction(action.action_text);
            }}
            onSelectProposal={(proposalId) => {
              setActiveProposalId(proposalId);
              setOverrideText("");
              setProposalContext(null);
            }}
            onSelectedTokenIdChange={setSelectedTokenId}
            onSetCharacterExpanded={setCharacterExpanded}
            onSetMapPublished={(published) => void setMapPublished(published)}
            onSubmitPlayerAction={() => void submitPlayerAction()}
            onTokenActorIdChange={setTokenActorId}
            onTokenLabelChange={setTokenLabel}
            onTokenLocationChange={setTokenLocation}
            opposedChecks={opposedChecks}
            otherPcs={otherPcs}
            overrideText={overrideText}
            pcs={pcs}
            playerAction={playerAction}
            playerActions={playerActions}
            proposalContext={proposalContext}
            proposals={proposals}
            proposalText={proposalText}
            selectedPlayerActionId={selectedPlayerActionId}
            selectedToken={selectedToken}
            selectedTokenId={selectedTokenId}
            tokenActorId={tokenActorId}
            tokenLabel={tokenLabel}
            tokenLocation={tokenLocation}
          />
        )}

        {renderedNav === "play" && authIdentity?.role === "player" && (
          <PlayerWorkspace
            adjudication={actionAdjudication}
            activeCampaign={activeCampaign}
            activeMap={activeMap}
            activePc={activePc}
            authIdentity={authIdentity}
            autoKpEnabled={autoKpEnabled}
            autoKpJobs={autoKpJobs}
            characterExpanded={characterExpanded}
            checks={skillChecks}
            loading={loading}
            maps={maps}
            members={sessionMembers}
            movableTokens={movableTokens}
            moveTarget={moveTarget}
            onAutoKpEnabledChange={setAutoKpEnabled}
            onCancel={(checkId, reason) => void decideSkillCheck(checkId, "cancel", reason)}
            onCreate={(input) => void createSkillCheck(input)}
            onCreateOpposed={(input) => void createOpposedCheck(input)}
            onCreateProposal={() => void createProposal()}
            onConfirmAdjudication={(skill) => void confirmActionAdjudication(skill)}
            onGenerateAiProposal={() => void generateAiProposal()}
            onGenerateConsequence={(checkId) => void generateCheckConsequence(checkId)}
            onMoveTargetChange={setMoveTarget}
            onMoveToken={moveToken}
            onOpenMap={(mapId) => void openMap(mapId)}
            onOverride={(checkId, successLevel, passed, reason) => void overrideSkillCheck(checkId, successLevel, passed, reason)}
            onPlaceToken={placeToken}
            onPlayerActionChange={setPlayerAction}
            onPlayerActionTabChange={setPlayerActionTab}
            onProposalTextChange={setProposalText}
            onPush={(checkId, reason) => void decideSkillCheck(checkId, "push", reason)}
            onRefresh={() => void loadSkillChecks()}
            onRefreshMaps={() => void refreshRealtimeMaps()}
            onRefreshPlayerActions={() => void loadPlayerActions()}
            onRetryAutoKpJob={(jobId) => void retryFailedAutoKpJob(jobId)}
            onReviseAdjudication={() => void reviseActionAdjudication()}
            onReplay={(checkId) => void replaySkillCheck(checkId)}
            onRerollOpposed={(opposedCheckId) => void rerollOpposedCheck(opposedCheckId)}
            onResolveDigital={(checkId) => void resolveSkillCheck(checkId, "digital")}
            onResolveOpposed={(opposedCheckId) => void resolveOpposedCheck(opposedCheckId)}
            onResolvePhysical={(checkId, onesDigit, tensDigits) => void resolveSkillCheck(checkId, "physical", onesDigit, tensDigits)}
            onSearchMemory={() => void searchMemory()}
            onSelectPlayerAction={(action) => {
              setSelectedPlayerActionId(action.id);
              setPlayerAction(action.action_text);
            }}
            onSelectedTokenIdChange={setSelectedTokenId}
            onSetCharacterExpanded={setCharacterExpanded}
            onSetMapPublished={(published) => void setMapPublished(published)}
            onSubmitPlayerAction={() => void submitPlayerAction()}
            onTokenActorIdChange={setTokenActorId}
            onTokenLabelChange={setTokenLabel}
            onTokenLocationChange={setTokenLocation}
            opposedChecks={opposedChecks}
            otherPcs={otherPcs}
            pcs={pcs}
            playerAction={playerAction}
            playerActions={playerActions}
            playerActionTab={playerActionTab}
            proposalText={proposalText}
            selectedPlayerActionId={selectedPlayerActionId}
            selectedToken={selectedToken}
            selectedTokenId={selectedTokenId}
            tokenActorId={tokenActorId}
            tokenLabel={tokenLabel}
            tokenLocation={tokenLocation}
          />
        )}

        {renderedNav === "play" && !authIdentity && (
          <section className="page-card permission-hint">请先在“团与权限”页加入会话，再进入游玩台。</section>
        )}

        {renderedNav === "memory" && (
          <Suspense fallback={<section className="page-card">正在载入角色记忆……</section>}>
            <MemoryWorkspace campaign={activeCampaign} identity={authIdentity} pcs={pcs} />
          </Suspense>
        )}

        {renderedNav === "facts" && (
          <Suspense fallback={<section className="page-card">正在载入世界事实……</section>}>
            <FactWorkspace campaign={activeCampaign} identity={authIdentity} pcs={pcs} />
          </Suspense>
        )}

        {renderedNav === "handouts" && (
          <Suspense fallback={<section className="page-card">正在载入玩家手册……</section>}>
            <HandoutWorkspace campaign={activeCampaign} identity={authIdentity} />
          </Suspense>
        )}

        {renderedNav === "evaluations" && (
          <Suspense fallback={<section className="page-card">正在载入模拟团评测……</section>}>
            <SimulationWorkbench campaign={activeCampaign} identity={authIdentity} />
          </Suspense>
        )}

        <PlanningPanel
          activeNav={renderedNav}
          capabilities={capabilities}
          error={capabilitiesError}
          loading={capabilitiesLoading}
          onRetry={() => void refreshCapabilities()}
        />
    </AppLayout>
  );
}
