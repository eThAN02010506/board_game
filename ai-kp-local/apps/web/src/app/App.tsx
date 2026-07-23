import {
  AlertCircle,
  Brain
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  credentialBridge,
  fetchCapabilities,
  requestJson,
  requestJsonWithAccessToken
} from "../api/client";
import type {
  AuthIdentity,
  Campaign,
  Capability,
  CreateSkillCheckInput,
  ContextAssembly,
  MapToken,
  PlayerActionRecord,
  PlayerCharacter,
  SavedMap,
  SessionBundle,
  SessionInfo,
  SessionMember,
  SessionSeat,
  SkillCheck,
  TurnProposal
} from "../api/types";
import { ActionPanel } from "../features/actions/ActionPanel";
import { CampaignPanel } from "../features/campaigns/CampaignPanel";
import { CheckPanel } from "../features/checks/CheckPanel";
import { InvestigatorPage } from "../features/investigators/InvestigatorPage";
import { MapGeneratorPanel } from "../features/maps/MapGeneratorPanel";
import { MapStage } from "../features/maps/MapStage";
import { ModelSettingsPage } from "../features/models/ModelSettingsPage";
import { TokenPanel } from "../features/maps/TokenPanel";
import { PlanningPanel } from "../features/planning/PlanningPanel";
import { ProposalPanel } from "../features/proposals/ProposalPanel";
import { RulebookPage } from "../features/rules/RulebookPage";
import { SessionPanel } from "../features/sessions/SessionPanel";
import { useWorkspaceRealtime } from "../realtime/provider";
import { AppLayout } from "./layout/AppLayout";
import { useWorkspaceRoute } from "./router";
import {
  listStoredCampaignTokens,
  readActiveMapId,
  readAdminToken,
  readCampaignToken,
  readPlayerProfileToken,
  removeCampaignToken,
  writeActiveMapId,
  writeAdminToken,
  writeCampaignToken,
  writePlayerProfileToken
} from "../session/session-storage";
import "../styles.css";

const defaultLocations = "旧码头, 废弃仓库, 报社, 警局";
const defaultRoutes = "旧码头>废弃仓库\n旧码头>报社\n报社>警局";

function splitList(value: string) {
  return value
    .split(/[,\n，、]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseRoutes(value: string): [string, string][] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.split(/>|->|—|-/).map((item) => item.trim()))
    .filter((items) => items.length >= 2 && items[0] && items[1])
    .map((items) => [items[0], items[1]]);
}

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


const initialAdminToken = readAdminToken();
const initialPlayerProfileToken = readPlayerProfileToken();
credentialBridge.admin(initialAdminToken);
credentialBridge.player(initialPlayerProfileToken);

export default function App() {
  const { activePage: activeNav, navigate, route: currentPage } = useWorkspaceRoute();
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
  const [recoverableSeats, setRecoverableSeats] = useState<SessionSeat[]>([]);
  const [visibleSeatInvites, setVisibleSeatInvites] = useState<Record<string, string>>({});
  const [pcs, setPcs] = useState<PlayerCharacter[]>([]);
  const [playerActions, setPlayerActions] = useState<PlayerActionRecord[]>([]);
  const [selectedPlayerActionId, setSelectedPlayerActionId] = useState("");
  const [visibleJoinCode, setVisibleJoinCode] = useState("");
  const [selectedTokenId, setSelectedTokenId] = useState("");
  const [log, setLog] = useState("准备就绪。先连接后端，或直接创建一个测试团。");
  const [loading, setLoading] = useState(false);
  const [characterExpanded, setCharacterExpanded] = useState(false);
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [capabilitiesLoading, setCapabilitiesLoading] = useState(true);
  const [capabilitiesError, setCapabilitiesError] = useState("");
  const activeCampaignIdRef = useRef("");
  const activeSessionIdRef = useRef("");
  const activeMapIdRef = useRef("");
  const campaignSelectionVersion = useRef(0);
  const campaignListRequestVersion = useRef(0);
  const capabilityRequestVersion = useRef(0);
  const mapRequestVersion = useRef(0);
  const pendingRequestCount = useRef(0);

  const [campaignTitle, setCampaignTitle] = useState("雾港 1928");
  const [campaignTime, setCampaignTime] = useState("1928-10-03 19:30");
  const [adminToken, setAdminToken] = useState(initialAdminToken);
  const [mapTitle, setMapTitle] = useState("旧码头区域图");
  const [mapPrompt, setMapPrompt] = useState("旧码头、废弃仓库、报社、警局");
  const [locationsText, setLocationsText] = useState(defaultLocations);
  const [routesText, setRoutesText] = useState(defaultRoutes);
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
  const [seatPcId, setSeatPcId] = useState("");
  const [playerDisplayName, setPlayerDisplayName] = useState("玩家");
  const [joinPcId, setJoinPcId] = useState("");
  const [pcName, setPcName] = useState("林若川");
  const [memberPcDrafts, setMemberPcDrafts] = useState<Record<string, string>>({});

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

  const activeMapImage = useMemo(
    () =>
      activeMap?.svg_text
        ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(activeMap.svg_text)}`
        : "",
    [activeMap?.svg_text]
  );

  async function run<T>(label: string, action: () => Promise<T>): Promise<T | undefined> {
    pendingRequestCount.current += 1;
    setLoading(true);
    setLog(`${label}...`);
    try {
      const result = await action();
      setLog(stringifyForLog(result));
      return result;
    } catch (error) {
      setLog(error instanceof Error ? error.message : String(error));
      return undefined;
    } finally {
      pendingRequestCount.current = Math.max(0, pendingRequestCount.current - 1);
      setLoading(pendingRequestCount.current > 0);
    }
  }

  async function perform<T>(
    label: string,
    action: () => Promise<T>,
    silent = false
  ): Promise<T | undefined> {
    if (!silent) return run(label, action);
    try {
      return await action();
    } catch {
      reportRealtimeRefreshFailure(label);
      return undefined;
    }
  }

  function rememberActiveMap(campaignId: string, mapId: string) {
    activeMapIdRef.current = mapId;
    writeActiveMapId(campaignId, credentialBridge.snapshot().role, mapId);
  }

  function applyAdminToken() {
    const nextToken = adminToken.trim();
    credentialBridge.admin(nextToken);
    writeAdminToken(nextToken);
    setLog(nextToken ? "管理员口令已仅保存在当前浏览器会话。" : "管理员口令已清除。");
  }

  function navigateWorkspace(id: typeof activeNav) {
    navigate(id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function loadCapabilities() {
    const requestVersion = ++capabilityRequestVersion.current;
    setCapabilitiesLoading(true);
    setCapabilitiesError("");
    try {
      const result = await fetchCapabilities();
      if (capabilityRequestVersion.current !== requestVersion) return;
      setCapabilities(result);
    } catch {
      if (capabilityRequestVersion.current !== requestVersion) return;
      setCapabilities([]);
      setCapabilitiesError("无法读取后端能力目录。请确认本地后端已启动，然后重试。");
    } finally {
      if (capabilityRequestVersion.current === requestVersion) setCapabilitiesLoading(false);
    }
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
    setVisibleSeatInvites({});
    setPcs([]);
    setTokenActorId("");
    setMemberPcDrafts({});
    setPlayerActions([]);
    setSelectedPlayerActionId("");
    setVisibleJoinCode("");
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
      credentialBridge.player(bundle.player_token);
      writePlayerProfileToken(bundle.player_token);
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
    const identity = await run("恢复团会话", () => requestJson<AuthIdentity>("/auth/me"));
    if (
      activeCampaignIdRef.current !== campaign.id ||
      campaignSelectionVersion.current !== version
    ) return;
    if (!identity || identity.campaign_id !== campaign.id) {
      removeCampaignToken(campaign.id);
      credentialBridge.session("");
      return;
    }
    credentialBridge.session(storedToken, identity.role, identity.pc_id);
    const session = await run("读取会话", () =>
      requestJson<SessionInfo>(`/sessions/${identity.session_id}`)
    );
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
      setLog("请先创建或选择一个团。");
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

  async function joinSession(event: FormEvent) {
    event.preventDefault();
    credentialBridge.session("");
    const bundle = await run("玩家加入团会话", () =>
      requestJson<SessionBundle>("/sessions/join", {
        method: "POST",
        body: JSON.stringify({
          join_code: joinCodeInput,
          display_name: playerDisplayName,
          pc_id: joinPcId.trim() || null
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
          body: JSON.stringify({ label: seatLabel, pc_id: seatPcId || null })
        }
      )
    );
    if (result && activeSessionIdRef.current === sessionId) {
      setVisibleSeatInvites((items) => ({
        ...items,
        [result.seat.id]: result.invitation_code
      }));
      setSeatLabel(`玩家席位 ${sessionSeats.length + 2}`);
      setSeatPcId("");
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

  async function assignSessionSeatPc(seatId: string, pcId: string) {
    if (!activeSession || credentialBridge.snapshot().role !== "kp") return;
    const sessionId = activeSession.id;
    const result = await run("更新席位角色", () =>
      requestJson<SessionSeat>(`/sessions/${sessionId}/seats/${seatId}/pc`, {
        method: "PATCH",
        body: JSON.stringify({ pc_id: pcId || null })
      })
    );
    if (result && activeSessionIdRef.current === sessionId) {
      void loadSessionSeats(sessionId);
      void loadSessionMembers(sessionId);
      void loadPcs(activeCampaign);
    }
  }

  async function recoverSessionSeat(seatId: string) {
    if (!credentialBridge.snapshot().playerToken) {
      setLog("当前浏览器没有可恢复的长期玩家身份。");
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

  async function createPc(event: FormEvent) {
    event.preventDefault();
    if (!activeCampaign || credentialBridge.snapshot().role !== "kp") return;
    const campaignId = activeCampaign.id;
    const pc = await run("创建玩家角色", () =>
      requestJson<PlayerCharacter>(`/campaigns/${campaignId}/pcs`, {
        method: "POST",
        body: JSON.stringify({ name: pcName, sheet: {} })
      })
    );
    if (pc && activeCampaignIdRef.current === campaignId) {
      setPcs((items) => [...items, pc]);
      setTokenActorId(pc.id);
      setTokenLabel(pc.name);
    }
  }

  async function assignMemberPc(memberId: string) {
    if (!activeSession || credentialBridge.snapshot().role !== "kp") return;
    const pcId = memberPcDrafts[memberId];
    if (!pcId) {
      setLog("请先为该玩家选择一张角色卡。");
      return;
    }
    const member = await run("绑定玩家角色", () =>
      requestJson<SessionMember>(`/sessions/${activeSession.id}/members/${memberId}/assign-pc`, {
        method: "POST",
        body: JSON.stringify({ pc_id: pcId })
      })
    );
    if (member) void loadSessionMembers(activeSession.id);
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
      setLog("只有已加入团会话的玩家可以提交行动。");
      return;
    }
    await run("提交玩家行动", () =>
      requestJson<PlayerActionRecord>(`/campaigns/${activeCampaign.id}/actions`, {
        method: "POST",
        body: JSON.stringify({
          action_text: playerAction,
          token_id: selectedToken?.id ?? null,
          map_id: activeMap?.id ?? null,
          client_action_id: crypto.randomUUID()
        })
      })
    );
  }

  async function loadSkillChecks(campaign = activeCampaign, silent = false) {
    if (!campaign || !credentialBridge.snapshot().role) return;
    const checks = await perform(
      "读取待检定",
      () => requestJson<SkillCheck[]>(`/campaigns/${campaign.id}/checks`),
      silent
    );
    if (checks && activeCampaignIdRef.current === campaign.id) setSkillChecks(checks);
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
    const result = await run(inputMethod === "digital" ? "投掷数字骰" : "录入实体骰", () =>
      requestJson<SkillCheck>(`/checks/${checkId}/resolve`, {
        method: "POST",
        body: JSON.stringify({
          input_method: inputMethod,
          ones_digit: inputMethod === "physical" ? onesDigit : null,
          tens_digits: inputMethod === "physical" ? tensDigits : []
        })
      })
    );
    if (result) {
      setSkillChecks((items) => items.map((item) => item.id === result.id ? result : item));
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
    let result = await run("以本机管理员读取团列表", () =>
      requestJsonWithAccessToken<Campaign[]>("/campaigns", "")
    );
    if (campaignListRequestVersion.current !== listRequest) return;
    for (const stored of storedTokens) {
      if (result) break;
      result = await run("以团会话读取团", () =>
        requestJsonWithAccessToken<Campaign[]>("/campaigns", stored.token)
      );
      if (campaignListRequestVersion.current !== listRequest) return;
      if (!result) removeCampaignToken(stored.campaignId);
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

  async function generateMap(event: FormEvent) {
    event.preventDefault();
    if (!activeCampaign || credentialBridge.snapshot().role !== "kp") {
      setLog("请先以 KP 身份开启或恢复该团会话。");
      return;
    }
    const campaignId = activeCampaign.id;
    const version = campaignSelectionVersion.current;
    const result = await run("生成并保存地图", () =>
      requestJson<SavedMap>(`/campaigns/${campaignId}/maps/generate`, {
        method: "POST",
        body: JSON.stringify({
          title: mapTitle,
          prompt: mapPrompt,
          locations: splitList(locationsText),
          routes: parseRoutes(routesText)
        })
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
    const result = await run(published ? "发布地图" : "收回地图", () =>
      requestJson<SavedMap>(`/maps/${mapId}/${published ? "publish" : "unpublish"}`, {
        method: "POST"
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
      setLog("请先以 KP 身份打开一张地图。");
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
      setLog("请先选择一个棋子。");
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
      setLog("请先加入或恢复团会话。");
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
      setLog("只有 KP 可以创建草稿。");
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
    }
  }

  async function generateAiProposal() {
    if (!activeCampaign || credentialBridge.snapshot().role !== "kp") {
      setLog("只有 KP 可以调用 AI KP。");
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
      setLog("没有可审批草稿。");
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

  async function rejectProposal() {
    if (
      credentialBridge.snapshot().role !== "kp" ||
      !activeProposal ||
      activeProposal.status !== "draft"
    ) {
      setLog("没有可拒绝草稿。");
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
      setLog("没有可检查的草稿。");
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
    if (result === null) setLog("这是手工草稿，没有 AI 上下文快照。");
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

  useEffect(() => {
    void loadCampaigns();
    void loadCapabilities();
    void loadRecoverableSeats(true);
  }, []);

  const activePc = pcs.find((pc) => pc.id === authIdentity?.pc_id) ?? null;
  const otherPcs = pcs.filter((pc) => pc.id !== authIdentity?.pc_id);

  return (
    <AppLayout
      activePage={activeNav}
      campaignTitle={activeCampaign?.title ?? "AI KP Local"}
      currentRoute={currentPage}
      identity={authIdentity}
      loading={loading}
      onNavigate={navigateWorkspace}
      onRefresh={() => void loadCampaigns()}
      realtimeNote={realtimeNote}
      realtimeStatus={realtimeStatus}
    >

        {activeNav === "investigators" && (
          <InvestigatorPage campaign={activeCampaign} identity={authIdentity} />
        )}

        {activeNav === "rules" && <RulebookPage identity={authIdentity} />}

        {activeNav === "models" && <ModelSettingsPage />}

        {activeNav === "campaigns" && <div className="page-grid campaign-page-grid">
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
            joinPcId={joinPcId}
            kpDisplayName={kpDisplayName}
            memberPcDrafts={memberPcDrafts}
            members={sessionMembers}
            seats={sessionSeats}
            recoverableSeats={recoverableSeats}
            visibleSeatInvites={visibleSeatInvites}
            seatInvitationInput={seatInvitationInput}
            seatLabel={seatLabel}
            seatPcId={seatPcId}
            onAssignMemberPc={(memberId) => void assignMemberPc(memberId)}
            onAssignSeatPc={(seatId, pcId) => void assignSessionSeatPc(seatId, pcId)}
            onCloseSession={() => void closeSession()}
            onCreatePc={createPc}
            onCreateSeat={createSessionSeat}
            onClaimSeat={claimSessionSeat}
            onJoinCodeInputChange={setJoinCodeInput}
            onJoinPcIdChange={setJoinPcId}
            onJoinSession={joinSession}
            onKpDisplayNameChange={setKpDisplayName}
            onMemberPcDraftChange={(memberId, pcId) =>
              setMemberPcDrafts((drafts) => ({ ...drafts, [memberId]: pcId }))
            }
            onPcNameChange={setPcName}
            onPlayerDisplayNameChange={setPlayerDisplayName}
            onSeatInvitationInputChange={setSeatInvitationInput}
            onSeatLabelChange={setSeatLabel}
            onSeatPcIdChange={setSeatPcId}
            onRefreshIdentity={() => void refreshIdentity()}
            onRefreshMembers={() => void loadSessionMembers()}
            onRefreshSeats={() => void loadSessionSeats()}
            onRefreshRecoverableSeats={() => void loadRecoverableSeats()}
            onRecoverSeat={(seatId) => void recoverSessionSeat(seatId)}
            onReissueSeat={(seatId) => void reissueSessionSeat(seatId)}
            onRevokeMember={(memberId) => void revokeMember(memberId)}
            onRevokeSeat={(seatId) => void revokeSessionSeat(seatId)}
            onRotateJoinCode={() => void rotateJoinCode()}
            onStartSession={() => void startCampaignSession()}
            pcName={pcName}
            pcs={pcs}
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

        {activeNav === "maps" && <div className="page-grid map-page-grid">
          {authIdentity?.role === "kp" && (
            <MapGeneratorPanel
              locationsText={locationsText}
              onGenerate={generateMap}
              onLocationsTextChange={setLocationsText}
              onPromptChange={setMapPrompt}
              onRoutesTextChange={setRoutesText}
              onTitleChange={setMapTitle}
              prompt={mapPrompt}
              routesText={routesText}
              title={mapTitle}
            />
          )}

          <MapStage
            activeMap={activeMap}
            activeMapImage={activeMapImage}
            hasIdentity={Boolean(authIdentity)}
            maps={maps}
            onOpenMap={(mapId) => void openMap(mapId)}
            onRefresh={() => void loadMaps()}
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

          <section className="response-panel page-log-panel">
            <div className="panel-heading"><h2>地图操作记录</h2><AlertCircle size={18} /></div>
            <pre>{log}</pre>
          </section>
        </div>}

        {activeNav === "play" && <div className="play-page">
          <aside className={`play-character-card ${characterExpanded ? "expanded" : ""}`}>
            <div className="panel-heading">
              <div><p className="eyebrow">当前调查员</p><h2>{activePc?.name ?? "尚未绑定角色"}</h2></div>
              <button className="ghost-button" onClick={() => setCharacterExpanded((value) => !value)} type="button">
                {characterExpanded ? "收起" : "放大"}
              </button>
            </div>
            {activePc ? (
              <>
                <div className="mini-sheet-grid">
                  {Object.entries(activePc.sheet ?? {}).slice(0, characterExpanded ? 24 : 8).map(([key, value]) => (
                    <span key={key}><small>{key}</small><strong>{typeof value === "object" ? "…" : String(value)}</strong></span>
                  ))}
                </div>
                {characterExpanded && <pre>{stringifyForLog(activePc.sheet ?? {})}</pre>}
              </>
            ) : <p className="permission-hint">先在“团与权限”页加入会话并绑定已批准角色。</p>}
            <div className="party-summary">
              <div className="party-summary-heading"><strong>其他调查员</strong><small>公开摘要</small></div>
              {otherPcs.length ? otherPcs.map((pc) => {
                const location = activeMap?.tokens?.find(
                  (token) => token.actor_type === "pc" && token.actor_id === pc.id
                )?.location_name;
                const summary = publicPcSummary(pc);
                const attributes = summary.attributes ?? {};
                return (
                  <article className="party-member-mini" key={pc.id}>
                    <div><strong>{pc.name}</strong><span>{location ?? "位置未知"}</span></div>
                    <small>
                      {summary.cash !== undefined ? `现金 ${summary.cash}` : "现金未公开"}
                      {Object.keys(attributes).length ? ` · ${Object.entries(attributes).slice(0, 3).map(([key, value]) => `${key.toUpperCase()} ${value}`).join(" / ")}` : " · 属性未公开"}
                    </small>
                  </article>
                );
              }) : <small>目前没有其他已加入的调查员。</small>}
            </div>
            <TokenPanel
              activeMap={activeMap}
              identity={authIdentity}
              movableTokens={movableTokens}
              moveTarget={moveTarget}
              onMoveTargetChange={setMoveTarget}
              onMoveToken={moveToken}
              onPlaceToken={placeToken}
              onSelectedTokenIdChange={setSelectedTokenId}
              onTokenActorIdChange={setTokenActorId}
              onTokenLabelChange={setTokenLabel}
              onTokenLocationChange={setTokenLocation}
              pcs={pcs}
              selectedToken={selectedToken}
              selectedTokenId={selectedTokenId}
              tokenActorId={tokenActorId}
              tokenLabel={tokenLabel}
              tokenLocation={tokenLocation}
            />
          </aside>

          <div className="play-map-column">
            <MapStage
              activeMap={activeMap}
              activeMapImage={activeMapImage}
              hasIdentity={Boolean(authIdentity)}
              maps={maps}
              onOpenMap={(mapId) => void openMap(mapId)}
              onRefresh={() => void loadMaps()}
              onSetPublished={(published) => void setMapPublished(published)}
              role={authIdentity?.role}
            />
          </div>

          <div className="play-action-column">
          <CheckPanel
            checks={skillChecks}
            identity={authIdentity}
            loading={loading}
            members={sessionMembers}
            onCancel={(checkId, reason) => void decideSkillCheck(checkId, "cancel", reason)}
            onCreate={(input) => void createSkillCheck(input)}
            onOverride={(checkId, successLevel, passed, reason) => void overrideSkillCheck(checkId, successLevel, passed, reason)}
            onPush={(checkId, reason) => void decideSkillCheck(checkId, "push", reason)}
            onRefresh={() => void loadSkillChecks()}
            onReplay={(checkId) => void replaySkillCheck(checkId)}
            onResolveDigital={(checkId) => void resolveSkillCheck(checkId, "digital")}
            onResolvePhysical={(checkId, onesDigit, tensDigits) => void resolveSkillCheck(checkId, "physical", onesDigit, tensDigits)}
          />
          <ActionPanel
            identity={authIdentity}
            loading={loading}
            onCreateProposal={() => void createProposal()}
            onGenerateAiProposal={() => void generateAiProposal()}
            onPlayerActionChange={setPlayerAction}
            onProposalTextChange={setProposalText}
            onRefreshPlayerActions={() => void loadPlayerActions()}
            onSearchMemory={() => void searchMemory()}
            onSelectPlayerAction={(action) => {
              setSelectedPlayerActionId(action.id);
              setPlayerAction(action.action_text);
            }}
            onSubmitPlayerAction={() => void submitPlayerAction()}
            playerAction={playerAction}
            playerActions={playerActions}
            proposalText={proposalText}
            selectedPlayerActionId={selectedPlayerActionId}
          />

          {authIdentity?.role === "kp" && (
            <ProposalPanel
              activeProposal={activeProposal}
              loading={loading}
              onApprove={() => void approveProposal()}
              onInspectContext={() => void inspectProposalContext()}
              onOverrideTextChange={setOverrideText}
              onRefresh={() => void loadProposals()}
              onReject={() => void rejectProposal()}
              onSelectProposal={(proposalId) => {
                setActiveProposalId(proposalId);
                setOverrideText("");
                setProposalContext(null);
              }}
              overrideText={overrideText}
              proposalContext={proposalContext}
              proposals={proposals}
            />
          )}
          <section className="response-panel">
            <div className="panel-heading">
              <h2>桌面记录</h2>
              <AlertCircle size={18} />
            </div>
            <pre>{log}</pre>
          </section>
          </div>
        </div>
        }

        {activeNav === "memory" && <section className="page-card memory-page">
          <div className="page-intro">
            <div><p className="eyebrow">角色时间线与召回</p><h2>角色记忆</h2></div>
            <Brain size={24} />
          </div>
          <label>寻找事件、人物或线索<textarea value={playerAction} onChange={(event) => setPlayerAction(event.target.value)} /></label>
          <button className="primary-button" disabled={!authIdentity} onClick={() => void searchMemory()} type="button"><Brain size={16} />检索当前角色记忆</button>
          <pre>{log}</pre>
        </section>}

        <PlanningPanel
          activeNav={activeNav}
          capabilities={capabilities}
          error={capabilitiesError}
          loading={capabilitiesLoading}
          onRetry={() => void loadCapabilities()}
        />
    </AppLayout>
  );
}
