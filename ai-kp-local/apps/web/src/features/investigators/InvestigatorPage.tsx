import {
  CheckCircle2,
  FileSpreadsheet,
  Minus,
  Plus,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Sparkles,
  UserRound,
  XCircle
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  credentialBridge,
  isApiError,
  requestFile,
  requestJson
} from "../../api/client";
import type {
  AuthIdentity,
  Campaign,
  CampaignInvestigator,
  CharacterSheet,
  CharacterSkillCatalogItem,
  CharacterSkillRecommendation,
  Investigator,
  InvestigatorCharacterTimeline,
  InvestigatorImportPreview,
  InvestigatorManualPreview,
  InvestigatorPermanentChange,
  PlayerProfile,
  PlayerProfileBundle,
  SessionMember
} from "../../api/types";
import { readPlayerProfileToken, writePlayerProfileToken } from "../../session/session-storage";
import {
  CharacterTimelinePanel,
  EMPTY_PERMANENT_CHANGE,
  KpTimelineTools,
  type PermanentChangeDraft
} from "./CharacterTimelinePanel";

const attributeFields = ["str", "con", "siz", "dex", "app", "int", "pow", "edu", "luck"];
const backgroundFieldKeys = [
  "appearance",
  "beliefs",
  "significant_people",
  "significant_places",
  "treasured_possessions",
  "traits",
  "injuries_scars",
  "phobias_manias",
  "secret"
] as const;
const BACKGROUND_LABEL_KEY: Record<string, string> = {
  appearance: "appearance",
  beliefs: "beliefs",
  significant_people: "significantPeople",
  significant_places: "significantPlaces",
  treasured_possessions: "treasuredPossessions",
  traits: "traits",
  injuries_scars: "injuriesScars",
  phobias_manias: "phobiasManias",
  secret: "secret"
};
function backgroundLabel(t: (key: string) => string, field: string): string {
  return t(`investigators.background.${BACKGROUND_LABEL_KEY[field] ?? field}`);
}

type ManualDraft = {
  name: string;
  playerName: string;
  occupation: string;
  era: string;
  age: string;
  gender: string;
  residence: string;
  birthplace: string;
  occupationFormula: "edu4" | "edu2_app2" | "edu2_dex2" | "edu2_pow2" | "edu2_str2";
  attributes: Record<string, string>;
  skills: ManualSkillDraft[];
  weaponsText: string;
  itemsText: string;
  currency: string;
  cash: string;
  background: Record<string, string>;
};

type ManualSkillDraft = CharacterSkillCatalogItem & {
  specialization: string;
  occupationPoints: number;
  interestPoints: number;
  developmentPoints: number;
};

function skillDraftsFromCatalog(catalog: CharacterSkillCatalogItem[]): ManualSkillDraft[] {
  return catalog.map((skill) => ({
    ...skill,
    specialization: skill.default_specialization || "",
    occupationPoints: 0,
    interestPoints: 0,
    developmentPoints: 0
  }));
}

function createEmptyDraft(catalog: CharacterSkillCatalogItem[] = []): ManualDraft {
  return {
    name: "",
    playerName: "",
    occupation: "",
    era: "1920s",
    age: "",
    gender: "",
    residence: "",
    birthplace: "",
    occupationFormula: "edu4",
    attributes: Object.fromEntries(attributeFields.map((key) => [key, ""])),
    skills: skillDraftsFromCatalog(catalog),
    weaponsText: "",
    itemsText: "",
    currency: "美元",
    cash: "",
    background: Object.fromEntries(backgroundFieldKeys.map((key) => [key, ""]))
  };
}

type Props = {
  campaign: Campaign | null;
  identity: AuthIdentity | null;
};

const STATUS_KEY: Record<string, string> = {
  draft: "draft",
  submitted: "submitted",
  changes_requested: "changesRequested",
  approved: "approved",
  withdrawn: "withdrawn"
};
function investigatorStatusLabel(t: (key: string) => string, status: string): string {
  return t(`investigators.status.${STATUS_KEY[status] ?? status}`);
}
function payloadFromPreview(preview: InvestigatorImportPreview) {
  return {
    canonical_sheet: preview.canonical_sheet,
    source_type: "xlsx",
    source_hash: preview.source_hash,
    source_filename: preview.source_filename,
    template_id: preview.template_id,
    parser_version: preview.parser_version,
    warnings: preview.warnings
  };
}

function number(value: string) {
  return Number(value || 0);
}

function resolvedSkillBase(skill: ManualSkillDraft, attributes: Record<string, string>) {
  if (skill.base_formula === "dex_half") return Math.floor(number(attributes.dex) / 2);
  if (skill.base_formula === "edu") return number(attributes.edu);
  return skill.base_value;
}

function skillCurrentValue(skill: ManualSkillDraft, attributes: Record<string, string>) {
  return resolvedSkillBase(skill, attributes)
    + skill.occupationPoints
    + skill.interestPoints
    + skill.developmentPoints;
}

function serializeSkills(manual: ManualDraft) {
  return manual.skills.map((skill) => ({
    skill_key: skill.skill_key,
    display_name: skill.display_name,
    specialization: skill.specialization.trim() || null,
    base_value: resolvedSkillBase(skill, manual.attributes),
    occupation_points: skill.occupationPoints,
    interest_points: skill.interestPoints,
    development_points: skill.developmentPoints,
    growth_mark: false
  }));
}

function parseWeapons(text: string) {
  return text.split("\n").map((line) => {
    const [name = "", skill = "", damage = "", range = "", attacks = "", ammo = "", malfunction = ""] = line.split("|");
    return { name: name.trim(), skill: skill.trim(), damage: damage.trim(), range: range.trim(), attacks, ammo, malfunction };
  }).filter((weapon) => weapon.name);
}

function parseItems(text: string) {
  return text.split("\n").map((line) => {
    const [name = "", location = "", visibility = "本人"] = line.split("|");
    return { name: name.trim(), location: location.trim() || null, visibility: visibility.trim() || "本人" };
  }).filter((item) => item.name);
}

function buildManualSheet(manual: ManualDraft): CharacterSheet {
  const creditRatingSkill = manual.skills.find(
    (skill) => skill.skill_key === "coc7.credit_rating"
      || skill.display_name === "信用评级"
  );
  return {
    schema_version: "coc7-investigator-v1",
    ruleset_id: "coc7-keeper-cn-2002c",
    identity: {
      name: manual.name.trim(),
      player_name: manual.playerName.trim(),
      occupation: manual.occupation.trim(),
      era: manual.era.trim(),
      age: number(manual.age),
      gender: manual.gender.trim(),
      residence: manual.residence.trim(),
      birthplace: manual.birthplace.trim()
    },
    characteristics: Object.fromEntries(
      attributeFields.map((key) => [key, number(manual.attributes[key])])
    ),
    derived: {
      max_hp: 0,
      max_mp: 0,
      initial_san: 0,
      max_san: 99,
      mov: 0,
      damage_bonus: "0",
      build: 0,
      dodge: 0
    },
    skills: serializeSkills(manual),
    combat: { weapons: parseWeapons(manual.weaponsText) },
    assets: {
      currency: manual.currency.trim(),
      cash: number(manual.cash),
      credit_rating: creditRatingSkill
        ? skillCurrentValue(creditRatingSkill, manual.attributes)
        : 0,
      items: parseItems(manual.itemsText)
    },
    background: manual.background,
    provenance: {
      source_type: "manual",
      occupation_point_formula: manual.occupationFormula
    }
  } as unknown as CharacterSheet;
}

function manualFromSheet(
  sheet: CharacterSheet,
  catalog: CharacterSkillCatalogItem[]
): ManualDraft {
  const raw = sheet as CharacterSheet & {
    combat?: { weapons?: Array<Record<string, unknown>> };
    assets?: Record<string, unknown> & { items?: Array<Record<string, unknown>> };
    background?: Record<string, unknown>;
    provenance?: Record<string, unknown>;
  };
  const identity = raw.identity || { name: "" };
  const assets = raw.assets || {};
  const background = raw.background || {};
  const loadedSkills = raw.skills || [];
  const matchedLoadedIndexes = new Set<number>();
  const skills = skillDraftsFromCatalog(catalog).map((catalogSkill) => {
    const loadedIndex = loadedSkills.findIndex((loaded, index) => (
      !matchedLoadedIndexes.has(index)
      && (loaded.skill_key === catalogSkill.skill_key || loaded.display_name === catalogSkill.display_name)
    ));
    if (loadedIndex < 0) return catalogSkill;
    matchedLoadedIndexes.add(loadedIndex);
    const loaded = loadedSkills[loadedIndex];
    return {
      ...catalogSkill,
      specialization: loaded.specialization || catalogSkill.specialization,
      occupationPoints: loaded.occupation_points || 0,
      interestPoints: loaded.interest_points || 0,
      developmentPoints: loaded.development_points || 0
    };
  });
  loadedSkills.forEach((loaded, index) => {
    if (matchedLoadedIndexes.has(index)) return;
    skills.push({
      skill_key: loaded.skill_key || `loaded.${index + 1}`,
      display_name: loaded.display_name,
      default_specialization: loaded.specialization || null,
      specialization: loaded.specialization || "",
      base_value: loaded.base_value || 0,
      base_formula: "fixed",
      specialization_editable: true,
      creation_points_allowed: !loaded.display_name.includes("克苏鲁神话"),
      description: `自定义或历史版本技能：${loaded.display_name}。具体用途由 KP 裁定。`,
      order: catalog.length + index + 1,
      occupationPoints: loaded.occupation_points || 0,
      interestPoints: loaded.interest_points || 0,
      developmentPoints: loaded.development_points || 0
    });
  });
  return {
    name: identity.name || "",
    playerName: identity.player_name || "",
    occupation: identity.occupation || "",
    era: identity.era || "1920s",
    age: identity.age ? String(identity.age) : "",
    gender: identity.gender || "",
    residence: identity.residence || "",
    birthplace: identity.birthplace || "",
    occupationFormula: (raw.provenance?.occupation_point_formula as ManualDraft["occupationFormula"]) || "edu4",
    attributes: Object.fromEntries(attributeFields.map((key) => [key, String(raw.characteristics[key] ?? "")])),
    skills,
    weaponsText: (raw.combat?.weapons || []).map((weapon) => [weapon.name, weapon.skill, weapon.damage, weapon.range, weapon.attacks, weapon.ammo, weapon.malfunction].map((value) => value ?? "").join("|")).join("\n"),
    itemsText: (assets.items || []).map((item) => [item.name, item.location, item.visibility].map((value) => value ?? "").join("|")).join("\n"),
    currency: String(assets.currency || "美元"),
    cash: assets.cash == null ? "" : String(assets.cash),
    background: Object.fromEntries(backgroundFieldKeys.map((key) => [key, String(background[key] || "")]))
  };
}

function occupationBudget(manual: ManualDraft) {
  const edu = number(manual.attributes.edu);
  const secondaryKey = manual.occupationFormula.split("_")[1]?.replace("2", "") ?? "";
  if (manual.occupationFormula === "edu4") return edu * 4;
  return edu * 2 + number(manual.attributes[secondaryKey]) * 2;
}

function DelayedSkillTooltip({ skill }: { skill: ManualSkillDraft }) {
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tooltipId = `skill-help-${skill.skill_key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;

  function cancelTimer() {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }

  function scheduleOpen() {
    cancelTimer();
    timer.current = setTimeout(() => {
      setOpen(true);
      timer.current = null;
    }, 3000);
  }

  function closeTooltip() {
    cancelTimer();
    setOpen(false);
  }

  useEffect(() => () => cancelTimer(), []);

  return <span
    aria-describedby={open ? tooltipId : undefined}
    className="skill-name-tooltip"
    onBlur={closeTooltip}
    onFocus={scheduleOpen}
    onMouseEnter={scheduleOpen}
    onMouseLeave={closeTooltip}
    tabIndex={0}
  >
    <span className="skill-name-tooltip-label">{skill.display_name}</span>
    {open && <span className="skill-tooltip-popup" id={tooltipId} role="tooltip"><strong>{skill.display_name}</strong><span>{skill.description}</span></span>}
  </span>;
}

export function InvestigatorPage({ campaign, identity }: Props) {
  const { t } = useTranslation();
  const [profile, setProfile] = useState<PlayerProfile | null>(null);
  const [displayName, setDisplayName] = useState(t("common.playerRole"));
  const [investigators, setInvestigators] = useState<Investigator[]>([]);
  const [campaignRecords, setCampaignRecords] = useState<CampaignInvestigator[]>([]);
  const [members, setMembers] = useState<SessionMember[]>([]);
  const [skillCatalog, setSkillCatalog] = useState<CharacterSkillCatalogItem[]>([]);
  const [skillQuery, setSkillQuery] = useState("");
  const [showAllocatedSkillsOnly, setShowAllocatedSkillsOnly] = useState(false);
  const [preview, setPreview] = useState<InvestigatorImportPreview | null>(null);
  const [manualPreview, setManualPreview] = useState<InvestigatorManualPreview | null>(null);
  const [skillRecommendation, setSkillRecommendation] = useState<CharacterSkillRecommendation | null>(null);
  const [targetInvestigatorId, setTargetInvestigatorId] = useState("");
  const [manualTargetId, setManualTargetId] = useState("");
  const [manual, setManual] = useState<ManualDraft>(() => createEmptyDraft());
  const [reviewComments, setReviewComments] = useState<Record<string, string>>({});
  const [bindingMembers, setBindingMembers] = useState<Record<string, string>>({});
  const [stateDrafts, setStateDrafts] = useState<Record<string, Record<string, string>>>({});
  const [timelines, setTimelines] = useState<Record<string, InvestigatorCharacterTimeline>>({});
  const [permanentChanges, setPermanentChanges] = useState<Record<string, InvestigatorPermanentChange[]>>({});
  const [branchSelections, setBranchSelections] = useState<Record<string, string>>({});
  const [branchLabels, setBranchLabels] = useState<Record<string, string>>({});
  const [decisionReasons, setDecisionReasons] = useState<Record<string, string>>({});
  const [changeDrafts, setChangeDrafts] = useState<Record<string, PermanentChangeDraft>>({});
  const [message, setMessage] = useState(t("investigators.loadingProfile"));
  const [busy, setBusy] = useState(false);
  const libraryRequestVersion = useRef(0);
  const campaignRecordsRequestVersion = useRef(0);

  const previewSheet = preview?.canonical_sheet;
  const strongestSkills = useMemo(
    () => [...(previewSheet?.skills ?? [])].sort((left, right) => right.current_value - left.current_value).slice(0, 8),
    [previewSheet]
  );
  const previewBreakdown = useMemo(() => {
    if (!previewSheet) return null;
    const raw = previewSheet as CharacterSheet & {
      combat?: { weapons?: unknown[] };
      assets?: { items?: unknown[] };
      background?: Record<string, unknown>;
    };
    return {
      weapons: raw.combat?.weapons?.length ?? 0,
      items: raw.assets?.items?.length ?? 0,
      backgroundFields: Object.values(raw.background ?? {}).filter(Boolean).length
    };
  }, [previewSheet]);
  const occupationUsed = manual.skills.reduce((sum, skill) => sum + skill.occupationPoints, 0);
  const interestUsed = manual.skills.reduce((sum, skill) => sum + skill.interestPoints, 0);
  const currentOccupationBudget = occupationBudget(manual);
  const interestBudget = number(manual.attributes.int) * 2;
  const displayedSkills = useMemo(() => {
    const query = skillQuery.trim().toLocaleLowerCase();
    return manual.skills.filter((skill) => {
      const matchesQuery = !query || `${skill.display_name} ${skill.specialization}`.toLocaleLowerCase().includes(query);
      const matchesAllocation = !showAllocatedSkillsOnly || skill.occupationPoints > 0 || skill.interestPoints > 0;
      return matchesQuery && matchesAllocation;
    });
  }, [manual.skills, showAllocatedSkillsOnly, skillQuery]);
  const displayedSkillColumns = useMemo(() => {
    if (displayedSkills.length <= 12) return [displayedSkills];
    const midpoint = Math.ceil(displayedSkills.length / 2);
    return [displayedSkills.slice(0, midpoint), displayedSkills.slice(midpoint)];
  }, [displayedSkills]);
  const recommendationReady = Boolean(
    manual.occupation.trim()
    && manual.era.trim()
    && number(manual.age) >= 15
    && number(manual.age) <= 89
    && attributeFields.every((key) => number(manual.attributes[key]) > 0)
  );

  function patchManual(changes: Partial<ManualDraft>) {
    setManual((current) => ({ ...current, ...changes }));
    setManualPreview(null);
    if (["occupation", "era", "age", "occupationFormula"].some((key) => key in changes)) {
      setSkillRecommendation(null);
    }
  }

  async function applySkillRecommendation() {
    if (!recommendationReady) return;
    setBusy(true);
    try {
      const result = await requestJson<CharacterSkillRecommendation>("/investigator-skills/recommend", {
        method: "POST",
        body: JSON.stringify({
          occupation: manual.occupation.trim(),
          era: manual.era.trim(),
          age: number(manual.age),
          occupation_point_formula: manual.occupationFormula,
          characteristics: Object.fromEntries(
            attributeFields.map((key) => [key, number(manual.attributes[key])])
          )
        })
      });
      const allocations = new Map(result.allocations.map((allocation) => [allocation.skill_key, allocation]));
      setManual((current) => ({
        ...current,
        skills: current.skills.map((skill) => {
          const allocation = allocations.get(skill.skill_key);
          return {
            ...skill,
            occupationPoints: allocation?.occupation_points ?? 0,
            interestPoints: allocation?.interest_points ?? 0,
            specialization: allocation?.specialization || skill.specialization
          };
        })
      }));
      setSkillRecommendation(result);
      setManualPreview(null);
      setMessage(t("investigators.msgRecommendApplied", { name: result.profile_name }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  function patchSkill(skillKey: string, changes: Partial<ManualSkillDraft>) {
    setManual((current) => ({
      ...current,
      skills: current.skills.map((skill) => skill.skill_key === skillKey ? { ...skill, ...changes } : skill)
    }));
    setManualPreview(null);
  }

  function adjustSkillPoints(
    skill: ManualSkillDraft,
    field: "occupationPoints" | "interestPoints",
    delta: number
  ) {
    if (!skill.creation_points_allowed) return;
    patchSkill(skill.skill_key, { [field]: Math.max(0, skill[field] + delta) });
  }

  async function loadSkillCatalog() {
    try {
      const catalog = await requestJson<CharacterSkillCatalogItem[]>("/investigator-skills/catalog");
      setSkillCatalog(catalog);
      setManual((current) => current.skills.length
        ? current
        : { ...current, skills: skillDraftsFromCatalog(catalog) });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function loadLibrary(silent = false) {
    const playerToken = credentialBridge.snapshot().playerToken;
    if (!playerToken) return;
    const requestVersion = ++libraryRequestVersion.current;
    if (!silent) setBusy(true);
    try {
      const [nextProfile, library] = await Promise.all([
        requestJson<PlayerProfile>("/player-profile"),
        requestJson<Investigator[]>("/investigators")
      ]);
      if (
        libraryRequestVersion.current !== requestVersion ||
        credentialBridge.snapshot().playerToken !== playerToken
      ) return;
      setProfile(nextProfile);
      setInvestigators(library);
      setMessage(library.length ? t("investigators.msgLoaded", { count: library.length }) : t("investigators.libraryReady"));
    } catch (error) {
      if (
        libraryRequestVersion.current !== requestVersion ||
        credentialBridge.snapshot().playerToken !== playerToken
      ) return;
      if (isApiError(error, 401)) {
        credentialBridge.player("");
        writePlayerProfileToken("");
        setProfile(null);
        setInvestigators([]);
      }
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (!silent && libraryRequestVersion.current === requestVersion) setBusy(false);
    }
  }

  async function loadCampaignRecords(silent = false) {
    const requestVersion = ++campaignRecordsRequestVersion.current;
    if (!campaign || !identity) {
      setCampaignRecords([]);
      setMembers([]);
      return;
    }
    if (!silent) setBusy(true);
    try {
      if (identity.role === "kp") {
        const [records, sessionMembers] = await Promise.all([
          requestJson<CampaignInvestigator[]>(`/campaigns/${campaign.id}/investigator-submissions`),
          requestJson<SessionMember[]>(`/sessions/${identity.session_id}/members`)
        ]);
        if (campaignRecordsRequestVersion.current !== requestVersion) return;
        setCampaignRecords(records);
        setMembers(sessionMembers.filter((member) => member.role === "player" && !member.revoked_at));
        setStateDrafts(Object.fromEntries(records.filter((record) => record.campaign_state).map((record) => [
          record.investigator_id,
          {
            current_hp: String(record.campaign_state?.current_hp ?? ""),
            current_san: String(record.campaign_state?.current_san ?? ""),
            current_mp: String(record.campaign_state?.current_mp ?? ""),
            current_luck: String(record.campaign_state?.current_luck ?? "")
          }
        ])));
      } else if (profile) {
        const records = await requestJson<CampaignInvestigator[]>(
          `/campaigns/${campaign.id}/my-investigators`
        );
        if (campaignRecordsRequestVersion.current !== requestVersion) return;
        setCampaignRecords(records);
      }
    } catch (error) {
      if (campaignRecordsRequestVersion.current === requestVersion) {
        setMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (!silent && campaignRecordsRequestVersion.current === requestVersion) setBusy(false);
    }
  }

  useEffect(() => {
    void loadSkillCatalog();
    const stored = readPlayerProfileToken();
    credentialBridge.player(stored);
    if (stored) void loadLibrary();
    else setMessage(t("investigators.noTokenHint"));
  }, []);

  useEffect(() => {
    void loadCampaignRecords(true);
    return () => {
      campaignRecordsRequestVersion.current += 1;
    };
  }, [campaign?.id, identity?.member_id, profile?.id]);

  async function createProfile(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      credentialBridge.player("");
      const bundle = await requestJson<PlayerProfileBundle>("/player-profiles", {
        method: "POST",
        body: JSON.stringify({ display_name: displayName.trim() })
      });
      credentialBridge.player(bundle.player_token);
      writePlayerProfileToken(bundle.player_token);
      setProfile(bundle.profile);
      setMessage(t("investigators.profileCreated"));
      await loadLibrary(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function previewExcel(file: File | undefined) {
    if (!file || !profile) return;
    setBusy(true);
    setPreview(null);
    try {
      const result = await requestFile<InvestigatorImportPreview>("/investigator-imports/preview", file);
      setPreview(result);
      setMessage(t("investigators.msgExcelRead", { file: file.name, count: result.ignored_formula_cells }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function savePreview() {
    if (!preview) return;
    setBusy(true);
    try {
      const endpoint = targetInvestigatorId ? `/investigators/${targetInvestigatorId}/revisions` : "/investigators";
      const saved = await requestJson<Investigator>(endpoint, {
        method: "POST",
        body: JSON.stringify(payloadFromPreview(preview))
      });
      setMessage(t("investigators.msgSavedDraft", { name: saved.name, revision: saved.current_revision.revision_no }));
      setPreview(null);
      setTargetInvestigatorId("");
      await loadLibrary(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function validateManual() {
    setBusy(true);
    try {
      const result = await requestJson<InvestigatorManualPreview>("/investigators/preview", {
        method: "POST",
        body: JSON.stringify({ canonical_sheet: buildManualSheet(manual), source_type: "manual" })
      });
      setManualPreview(result);
      setMessage(t("investigators.msgRulePreview", { count: result.warnings.length }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function saveManual(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const endpoint = manualTargetId ? `/investigators/${manualTargetId}/revisions` : "/investigators";
      const saved = await requestJson<Investigator>(endpoint, {
        method: "POST",
        body: JSON.stringify({ canonical_sheet: buildManualSheet(manual), source_type: "manual" })
      });
      setMessage(t("investigators.msgSavedVersion", { name: saved.name, revision: saved.current_revision.revision_no }));
      setManual(createEmptyDraft(skillCatalog));
      setManualPreview(null);
      setSkillRecommendation(null);
      setManualTargetId("");
      await loadLibrary(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  function editInvestigator(investigator: Investigator) {
    setManual(manualFromSheet(investigator.current_revision.canonical_sheet, skillCatalog));
    setManualTargetId(investigator.id);
    setManualPreview(null);
    setSkillRecommendation(null);
    setMessage(t("investigators.msgLoadedEditor", { name: investigator.name, revision: investigator.current_revision.revision_no }));
  }

  async function submitInvestigator(investigator: Investigator) {
    if (!campaign || identity?.role !== "player") return;
    setBusy(true);
    try {
      const record = await requestJson<CampaignInvestigator>(
        `/campaigns/${campaign.id}/investigators/${investigator.id}/submit`,
        {
          method: "POST",
          body: JSON.stringify({
            revision_id: investigator.current_revision_id,
            timeline_branch_id: branchSelections[investigator.id] || undefined
          })
        }
      );
      setMessage(t("investigators.msgSubmitted", { name: investigator.name, revision: record.submitted_revision?.revision_no }));
      await loadCampaignRecords(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function loadTimeline(investigatorId: string) {
    setBusy(true);
    try {
      const path = identity?.role === "kp" && campaign
        ? `/campaigns/${campaign.id}/investigators/${investigatorId}/timeline`
        : `/investigators/${investigatorId}/timeline`;
      const timeline = await requestJson<InvestigatorCharacterTimeline>(path);
      setTimelines((current) => ({ ...current, [investigatorId]: timeline }));
      if (profile && identity?.role !== "kp") {
        const changes = await requestJson<InvestigatorPermanentChange[]>(
          `/investigators/${investigatorId}/permanent-changes`
        );
        setPermanentChanges((current) => ({ ...current, [investigatorId]: changes }));
      }
      setBranchSelections((current) => ({
        ...current,
        [investigatorId]: current[investigatorId]
          || timeline.branches.find((branch) => branch.is_primary)?.id
          || timeline.branches[0]?.id
          || ""
      }));
      setMessage(t("investigators.msgTimelineLoaded"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function createTimelineBranch(investigatorId: string) {
    const label = (branchLabels[investigatorId] || "").trim();
    if (!label) return;
    setBusy(true);
    try {
      const branch = await requestJson<{ id: string }>(
        `/investigators/${investigatorId}/timeline-branches`,
        { method: "POST", body: JSON.stringify({ label }) }
      );
      setBranchSelections((current) => ({ ...current, [investigatorId]: branch.id }));
      setBranchLabels((current) => ({ ...current, [investigatorId]: "" }));
      await loadTimeline(investigatorId);
      setMessage(t("investigators.msgBranchCreated"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function decidePermanentChange(
    investigator: Investigator,
    proposal: InvestigatorPermanentChange,
    action: "accepted" | "rejected"
  ) {
    const reason = (decisionReasons[proposal.id] || "").trim();
    if (!reason) {
      setMessage(t("investigators.msgDecisionReasonRequired"));
      return;
    }
    setBusy(true);
    try {
      await requestJson(`/investigator-permanent-changes/${proposal.id}/decision`, {
        method: "POST",
        body: JSON.stringify({
          action,
          reason,
          expected_revision_id: investigator.current_revision_id
        })
      });
      await loadLibrary(true);
      await loadTimeline(investigator.id);
      setMessage(action === "accepted"
        ? t("investigators.msgAccepted")
        : t("investigators.msgRejected"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function proposePermanentChange(record: CampaignInvestigator) {
    if (!campaign) return;
    const draft = changeDrafts[record.investigator_id];
    if (!draft) return;
    const change = draft.kind === "characteristic"
      ? { key: draft.targetKey.trim().toLowerCase(), new_value: number(draft.newValue) }
      : draft.kind === "skill"
        ? { skill_key: draft.targetKey.trim(), new_value: number(draft.newValue) }
        : { text: draft.text.trim() };
    setBusy(true);
    try {
      await requestJson(
        `/campaigns/${campaign.id}/investigators/${record.investigator_id}/permanent-changes`,
        {
          method: "POST",
          body: JSON.stringify({
            kind: draft.kind,
            summary: draft.summary.trim(),
            change,
            source_event_id: draft.sourceEventId.trim(),
            rationale: draft.rationale.trim()
          })
        }
      );
      setChangeDrafts((current) => {
        const next = { ...current };
        delete next[record.investigator_id];
        return next;
      });
      setMessage(t("investigators.msgProposedChange"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function review(record: CampaignInvestigator, action: "approved" | "changes_requested") {
    if (!campaign || identity?.role !== "kp") return;
    setBusy(true);
    try {
      await requestJson(`/campaigns/${campaign.id}/investigators/${record.investigator_id}/review`, {
        method: "POST",
        body: JSON.stringify({ action, comment: reviewComments[record.investigator_id] || null })
      });
      setMessage(action === "approved" ? t("investigators.msgApproved", { name: record.name }) : t("investigators.msgReturned", { name: record.name }));
      await loadCampaignRecords(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function bindInvestigator(record: CampaignInvestigator) {
    if (!identity || !bindingMembers[record.investigator_id]) return;
    setBusy(true);
    try {
      await requestJson(`/sessions/${identity.session_id}/members/${bindingMembers[record.investigator_id]}/assign-investigator`, {
        method: "POST",
        body: JSON.stringify({ investigator_id: record.investigator_id })
      });
      setMessage(t("investigators.msgBound", { name: record.name }));
      await loadCampaignRecords(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function updateRuntimeState(record: CampaignInvestigator) {
    if (!campaign || !record.campaign_state) return;
    const draft = stateDrafts[record.investigator_id] || {};
    setBusy(true);
    try {
      await requestJson(`/campaigns/${campaign.id}/investigators/${record.investigator_id}/state`, {
        method: "PATCH",
        body: JSON.stringify({
          expected_version: record.campaign_state.state_version,
          current_hp: number(draft.current_hp),
          current_san: number(draft.current_san),
          current_mp: number(draft.current_mp),
          current_luck: number(draft.current_luck)
        })
      });
      setMessage(t("investigators.msgStateUpdated", { name: record.name }));
      await loadCampaignRecords(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="investigator-page" id="investigator-section">
      <section className="investigator-hero">
        <div>
          <p className="eyebrow">{t("investigators.playerCenter")}</p>
          <h2>{t("investigators.heroTitle")}</h2>
          <p>{profile ? t("investigators.msgProfileHint", { name: profile.display_name }) : t("investigators.profileSetupHint")}</p>
        </div>
        <div className="investigator-flow" aria-label={t("investigators.playerCenter")}>
          <span><b>01</b>{t("investigators.flowImport")}</span><span><b>02</b>{t("investigators.flowSave")}</span><span><b>03</b>{t("investigators.flowSubmit")}</span>
        </div>
      </section>
      {!profile ? (
        <section className="page-card investigator-profile-setup">
          <div className="page-intro"><div><p className="eyebrow">{t("investigators.standalonePage")}</p><h2>{t("investigators.createProfile")}</h2></div><UserRound size={24} /></div>
          <p>{t("investigators.profileIntro")}</p>
          <form onSubmit={createProfile}><label>{t("investigators.displayName")}<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label><button className="primary-button" disabled={busy || !displayName.trim()} type="submit"><Plus size={16} />{t("investigators.createProfileBtn")}</button></form>
        </section>
      ) : (
        <>
          <section className="page-card investigator-library">
            <div className="page-intro"><div><p className="eyebrow">{t("investigators.localLibrary", { name: profile.display_name })}</p><h2>{t("investigators.revisionTitle")}</h2></div><button className="ghost-button" disabled={busy} onClick={() => void loadLibrary()} type="button"><RefreshCw size={15} />{t("common.refresh")}</button></div>
            <div className="investigator-list">
              {investigators.length ? investigators.map((investigator) => {
                const record = campaignRecords.find((item) => item.investigator_id === investigator.id);
                return <article className="investigator-record" key={investigator.id}>
                  <div><strong>{investigator.name}</strong><span>{String(investigator.current_revision.public_summary.occupation || t("investigators.noOccupation"))}</span></div>
                  <small>{t("investigators.immutableRevision", { revision: investigator.current_revision.revision_no, source: investigator.current_revision.source_type })}</small>
                  {record && <span className={`proposal-status ${record.status}`}>{investigatorStatusLabel(t, record.status)}</span>}
                  {record?.review_comment && <p className="permission-hint">{t("investigators.kpComment")}：{record.review_comment}</p>}
                  <details className="character-sheet-details"><summary>{t("investigators.viewSummary")}</summary><div className="derived-grid"><span>HP <strong>{investigator.current_revision.canonical_sheet.derived.max_hp}</strong></span><span>SAN <strong>{investigator.current_revision.canonical_sheet.derived.initial_san}</strong></span><span>MP <strong>{investigator.current_revision.canonical_sheet.derived.max_mp}</strong></span><span>MOV <strong>{investigator.current_revision.canonical_sheet.derived.mov}</strong></span><span>DB <strong>{investigator.current_revision.canonical_sheet.derived.damage_bonus}</strong></span><span>{t("investigators.build")} <strong>{investigator.current_revision.canonical_sheet.derived.build}</strong></span></div><div className="skill-preview">{[...investigator.current_revision.canonical_sheet.skills].sort((left, right) => right.current_value - left.current_value).slice(0, 8).map((skill) => <span key={skill.skill_key}>{skill.display_name} {skill.current_value}</span>)}</div></details>
                  <div className="inline-actions">
                    <button className="ghost-button" disabled={busy} onClick={() => editInvestigator(investigator)} type="button">{t("investigators.loadEditor")}</button>
                    <button className="ghost-button" disabled={busy} onClick={() => void loadTimeline(investigator.id)} type="button">{t("investigators.timeline")}</button>
                    {campaign && identity?.role === "player" && <button className="secondary-button" disabled={busy || record?.status === "submitted"} onClick={() => void submitInvestigator(investigator)} type="button">{t("investigators.submitVersion", { campaign: campaign.title })}</button>}
                  </div>
                  {timelines[investigator.id] && <CharacterTimelinePanel
                    branchId={branchSelections[investigator.id] || ""}
                    branchLabel={branchLabels[investigator.id] || ""}
                    busy={busy}
                    decisionReasons={decisionReasons}
                    investigatorName={investigator.name}
                    onBranchChange={(branchId) => setBranchSelections((current) => ({ ...current, [investigator.id]: branchId }))}
                    onBranchLabelChange={(label) => setBranchLabels((current) => ({ ...current, [investigator.id]: label }))}
                    onCreateBranch={() => void createTimelineBranch(investigator.id)}
                    onDecision={(proposal, action) => void decidePermanentChange(investigator, proposal, action)}
                    onDecisionReasonChange={(proposalId, reason) => setDecisionReasons((current) => ({ ...current, [proposalId]: reason }))}
                    proposals={permanentChanges[investigator.id] || []}
                    timeline={timelines[investigator.id]}
                  />}
                </article>;
              }) : <p className="empty-copy">{t("investigators.noInvestigators")}</p>}
            </div>
          </section>

          <section className="page-card excel-import-card">
            <div className="page-intro"><div><p className="eyebrow">{t("investigators.excelImport")}</p><h2>{t("investigators.importSheet")}</h2><p>{t("investigators.excelIntro")}</p></div><span className="excel-heading-icon"><FileSpreadsheet size={27} /></span></div>
            <div className={`excel-import-layout ${previewSheet ? "has-preview" : ""}`}>
              <label className="file-drop">
                <span className="file-drop-illustration"><FileSpreadsheet size={32} /></span>
                <strong>{preview?.source_filename || t("investigators.dropOrSelect")}</strong>
                <span>{t("investigators.templateHint")}</span>
                <input accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" disabled={busy} onChange={(event) => void previewExcel(event.target.files?.[0])} type="file" />
                <span className="file-drop-action">{t("investigators.chooseExcel")}</span>
                <small>{t("investigators.excelSafeHint")}</small>
              </label>
              {!previewSheet && <aside className="excel-import-guide">
                <strong>{t("investigators.fullPreview")}</strong>
                <div><span><CheckCircle2 size={15} />{t("investigators.identityNineAttrs")}</span><span><CheckCircle2 size={15} />{t("investigators.allSkillsSpecializations")}</span><span><CheckCircle2 size={15} />{t("investigators.weaponsItemsBg")}</span><span><CheckCircle2 size={15} />{t("investigators.derivedPreview")}</span></div>
                <p>{t("investigators.previewConfirmHint")}</p>
              </aside>}
            </div>
            {previewSheet && preview && <div className="import-preview excel-character-preview">
              <div className="excel-preview-banner">
                <div><span className="excel-preview-status"><ShieldCheck size={15} />{t("investigators.parsed")}</span><h3>{previewSheet.identity.name || t("investigators.unnamed")}</h3><p>{previewSheet.identity.occupation || t("investigators.noOccupation")} · {previewSheet.identity.era || t("investigators.eraMissing")}</p></div>
                <div className="excel-import-stats"><span><strong>{previewSheet.skills.length}</strong>{t("investigators.skillsLabel")}</span><span><strong>{previewBreakdown?.weapons ?? 0}</strong>{t("investigators.weaponsLabel")}</span><span><strong>{previewBreakdown?.items ?? 0}</strong>{t("investigators.itemsLabel")}</span><span><strong>{preview.ignored_formula_cells}</strong>{t("investigators.formulasIgnored")}</span></div>
              </div>
              <section className="excel-preview-section"><div className="excel-section-heading"><strong>{t("investigators.basicInfo")}</strong><span>{t("investigators.fromWorkbook")}</span></div><div className="excel-identity-grid"><span>{t("investigators.player")}<strong>{previewSheet.identity.player_name || "—"}</strong></span><span>{t("investigators.age")}<strong>{previewSheet.identity.age || "—"}</strong></span><span>{t("investigators.gender")}<strong>{previewSheet.identity.gender || "—"}</strong></span><span>{t("investigators.residence")}<strong>{previewSheet.identity.residence || "—"}</strong></span><span>{t("investigators.birthplace")}<strong>{previewSheet.identity.birthplace || "—"}</strong></span><span>{t("investigators.bgFieldsCount")}<strong>{previewBreakdown?.backgroundFields ?? 0} {t("investigators.bgFieldsCountUnit")}</strong></span></div></section>
              <section className="excel-preview-section"><div className="excel-section-heading"><strong>{t("investigators.nineAttributes")}</strong><span>{t("investigators.rawValues")}</span></div><div className="excel-characteristics-grid">{attributeFields.map((key) => <span key={key}><b>{key.toUpperCase()}</b><strong>{previewSheet.characteristics[key] ?? "—"}</strong></span>)}</div></section>
              <section className="excel-preview-section"><div className="excel-section-heading"><strong>{t("investigators.derivedValues")}</strong><span>{t("investigators.engineRecalc")}</span></div><div className="derived-grid excel-derived-grid"><span>HP <strong>{previewSheet.derived.max_hp}</strong></span><span>SAN <strong>{previewSheet.derived.initial_san}</strong></span><span>MP <strong>{previewSheet.derived.max_mp}</strong></span><span>MOV <strong>{previewSheet.derived.mov}</strong></span><span>DB <strong>{previewSheet.derived.damage_bonus}</strong></span><span>{t("investigators.build")} <strong>{previewSheet.derived.build}</strong></span><span>{t("investigators.dodge")} <strong>{previewSheet.derived.dodge}</strong></span><span>{t("investigators.maxSan")} <strong>{previewSheet.derived.max_san}</strong></span></div></section>
              <section className="excel-preview-section"><div className="excel-section-heading"><strong>{t("investigators.strongestSkills")}</strong><span>{t("investigators.sortedByValue")}</span></div><div className="skill-preview excel-strongest-skills">{strongestSkills.map((skill) => <span key={skill.skill_key}>{skill.display_name}{skill.specialization ? `（${skill.specialization}）` : ""}<strong>{skill.current_value}</strong></span>)}</div><details className="excel-all-skills"><summary>{t("investigators.allParsedSkills", { count: previewSheet.skills.length })}</summary><div>{previewSheet.skills.map((skill) => <span key={skill.skill_key}><b>{skill.display_name}{skill.specialization ? `（${skill.specialization}）` : ""}</b><strong>{skill.current_value}</strong><small>{skill.base_value}+{skill.occupation_points}+{skill.interest_points}+{skill.development_points}</small></span>)}</div></details></section>
              {preview.warnings.length > 0 ? <details className="excel-warning-panel" open><summary>{t("investigators.needsConfirmation", { count: preview.warnings.length })}</summary><ul>{preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></details> : <p className="excel-clean-result"><CheckCircle2 size={16} />{t("investigators.noWarnings")}</p>}
              <div className="excel-save-bar"><label>{t("investigators.saveAs")}<select value={targetInvestigatorId} onChange={(event) => setTargetInvestigatorId(event.target.value)}><option value="">{t("investigators.saveAsNew")}</option>{investigators.map((investigator) => <option key={investigator.id} value={investigator.id}>{t("investigators.asNewVersion", { name: investigator.name })}</option>)}</select></label><button className="primary-button" disabled={busy || !previewSheet.identity.name} onClick={() => void savePreview()} type="button"><Save size={16} />{t("investigators.confirmSaveDraft")}</button></div>
            </div>}
          </section>

          <section className="page-card manual-character-card">
            <div className="page-intro"><div><p className="eyebrow">{t("investigators.manualBuild")}</p><h2>{t("investigators.editorTitle")}</h2></div><UserRound size={24} /></div>
            <form onSubmit={saveManual}>
              <fieldset className="manual-character-fields" disabled={busy}>
              <div className="form-grid compact-fields">
                <label>{t("investigators.name")}<input required value={manual.name} onChange={(event) => patchManual({ name: event.target.value })} /></label><label>{t("investigators.playerName")}<input value={manual.playerName} onChange={(event) => patchManual({ playerName: event.target.value })} /></label><label>{t("investigators.occupation")}<input value={manual.occupation} onChange={(event) => patchManual({ occupation: event.target.value })} /></label><label>{t("investigators.era")}<input value={manual.era} onChange={(event) => patchManual({ era: event.target.value })} /></label><label>{t("investigators.age")}<input max="89" min="15" type="number" value={manual.age} onChange={(event) => patchManual({ age: event.target.value })} /></label><label>{t("investigators.genderDesc")}<input value={manual.gender} onChange={(event) => patchManual({ gender: event.target.value })} /></label><label>{t("investigators.residence")}<input value={manual.residence} onChange={(event) => patchManual({ residence: event.target.value })} /></label><label>{t("investigators.birthplace")}<input value={manual.birthplace} onChange={(event) => patchManual({ birthplace: event.target.value })} /></label>
              </div>
              <div className="attribute-entry-grid">{attributeFields.map((key) => <label key={key}>{key.toUpperCase()}<input min="0" type="number" value={manual.attributes[key]} onChange={(event) => { setManual((current) => ({ ...current, attributes: { ...current.attributes, [key]: event.target.value } })); setManualPreview(null); setSkillRecommendation(null); }} /></label>)}</div>
              <label>{t("investigators.occupationFormula")}<select value={manual.occupationFormula} onChange={(event) => patchManual({ occupationFormula: event.target.value as ManualDraft["occupationFormula"] })}><option value="edu4">{t("investigators.formulaEdu4")}</option><option value="edu2_app2">{t("investigators.formulaEdu2App2")}</option><option value="edu2_dex2">{t("investigators.formulaEdu2Dex2")}</option><option value="edu2_pow2">{t("investigators.formulaEdu2Pow2")}</option><option value="edu2_str2">{t("investigators.formulaEdu2Str2")}</option></select></label>
              <div className="derived-grid skill-budget-summary"><span className={occupationUsed > currentOccupationBudget ? "budget-over" : ""}>{t("investigators.budgetUsed")} <strong>{occupationUsed}/{currentOccupationBudget}</strong></span><span className={occupationUsed > currentOccupationBudget ? "budget-over" : ""}>{t("investigators.budgetRemaining")} <strong>{currentOccupationBudget - occupationUsed}</strong></span><span className={interestUsed > interestBudget ? "budget-over" : ""}>{t("investigators.interestUsed")} <strong>{interestUsed}/{interestBudget}</strong></span><span className={interestUsed > interestBudget ? "budget-over" : ""}>{t("investigators.interestRemaining")} <strong>{interestBudget - interestUsed}</strong></span></div>
              <section className="skill-recommendation-panel">
                <div>
                  <p className="eyebrow">{t("investigators.localRuleAdvice")}</p>
                  <h3>{t("investigators.recommendTitle")}</h3>
                  <small>{recommendationReady ? t("investigators.recommendHintReady") : t("investigators.recommendHintNotReady")}</small>
                </div>
                <button className="recommend-skill-button" disabled={busy || !recommendationReady} onClick={() => void applySkillRecommendation()} type="button"><Sparkles size={17} />{skillRecommendation ? t("investigators.recommendRegenerate") : t("investigators.recommendGenerate")}</button>
                <p className="permission-hint">{t("investigators.recommendAppliedHint")}</p>
                {skillRecommendation && <div className="recommendation-result">
                  <div><strong>{skillRecommendation.profile_name}</strong><span>{t("investigators.budgetUsed")} {skillRecommendation.occupation_spent}/{skillRecommendation.occupation_budget} · {t("investigators.interestUsed")} {skillRecommendation.interest_spent}/{skillRecommendation.interest_budget}</span></div>
                  <ul>{skillRecommendation.rationale.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                </div>}
              </section>
              <section className="skill-allocation-editor">
                <div className="skill-allocation-heading">
                  <div><p className="eyebrow">{t("investigators.referenceTemplate")}</p><h3>{t("investigators.fullSkills")}</h3><small>{t("investigators.skillsCountHint", { count: manual.skills.length })}</small></div>
                  <div className="skill-allocation-filters">
                    <label className="skill-search"><Search size={15} /><input aria-label={t("investigators.searchSkillLabel")} placeholder={t("investigators.searchPlaceholder")} value={skillQuery} onChange={(event) => setSkillQuery(event.target.value)} /></label>
                    <label className="skill-allocation-toggle"><input checked={showAllocatedSkillsOnly} onChange={(event) => setShowAllocatedSkillsOnly(event.target.checked)} type="checkbox" />{t("investigators.onlyAllocated")}</label>
                  </div>
                </div>
                <div className="skill-table-layout">
                  {displayedSkillColumns.map((skillColumn, columnIndex) => <div className="skill-table-panel" key={skillColumn[0]?.skill_key || `empty-${columnIndex}`}>
                  <table className="skill-allocation-table">
                    <thead><tr><th>{t("investigators.name")}</th><th>{t("investigators.specializationLabel")}</th><th>{t("investigators.baseLabel")}</th><th>{t("investigators.occupationPoints")}</th><th>{t("investigators.interestPoints")}</th><th>{t("investigators.regularLabel")}</th><th>{t("investigators.hardLabel")}</th><th>{t("investigators.extremeLabel")}</th></tr></thead>
                    <tbody>{skillColumn.map((skill) => {
                      const currentValue = skillCurrentValue(skill, manual.attributes);
                      const baseValue = resolvedSkillBase(skill, manual.attributes);
                      return <tr className={!skill.creation_points_allowed ? "skill-points-locked" : currentValue > 75 ? "skill-over-75" : ""} key={skill.skill_key}>
                        <th scope="row"><DelayedSkillTooltip skill={skill} />{!skill.creation_points_allowed && <small>{t("investigators.lockedSkill")}</small>}</th>
                        <td>{skill.specialization_editable ? <input aria-label={t("investigators.specializationAria", { skill: skill.display_name })} placeholder={t("investigators.specializationPlaceholder")} value={skill.specialization} onChange={(event) => patchSkill(skill.skill_key, { specialization: event.target.value })} /> : <span>{skill.specialization || "—"}</span>}</td>
                        <td><strong>{baseValue}</strong>{skill.base_formula !== "fixed" && <small>{skill.base_formula === "dex_half" ? "DEX÷2" : "EDU"}</small>}</td>
                        <td><div className="skill-point-stepper"><button aria-label={t("investigators.decreaseOccupation", { skill: skill.display_name })} disabled={!skill.creation_points_allowed || skill.occupationPoints <= 0} onClick={() => adjustSkillPoints(skill, "occupationPoints", -1)} type="button"><Minus size={13} /></button><input aria-label={t("investigators.occupationPointFor", { skill: skill.display_name })} disabled={!skill.creation_points_allowed} min="0" type="number" value={skill.occupationPoints} onChange={(event) => patchSkill(skill.skill_key, { occupationPoints: Math.max(0, number(event.target.value)) })} /><button aria-label={t("investigators.increaseOccupation", { skill: skill.display_name })} disabled={!skill.creation_points_allowed} onClick={() => adjustSkillPoints(skill, "occupationPoints", 1)} type="button"><Plus size={13} /></button></div></td>
                        <td><div className="skill-point-stepper"><button aria-label={t("investigators.decreaseInterest", { skill: skill.display_name })} disabled={!skill.creation_points_allowed || skill.interestPoints <= 0} onClick={() => adjustSkillPoints(skill, "interestPoints", -1)} type="button"><Minus size={13} /></button><input aria-label={t("investigators.interestPointFor", { skill: skill.display_name })} disabled={!skill.creation_points_allowed} min="0" type="number" value={skill.interestPoints} onChange={(event) => patchSkill(skill.skill_key, { interestPoints: Math.max(0, number(event.target.value)) })} /><button aria-label={t("investigators.increaseInterest", { skill: skill.display_name })} disabled={!skill.creation_points_allowed} onClick={() => adjustSkillPoints(skill, "interestPoints", 1)} type="button"><Plus size={13} /></button></div></td>
                        <td><strong>{currentValue}</strong></td><td>{Math.floor(currentValue / 2)}</td><td>{Math.floor(currentValue / 5)}</td>
                      </tr>;
                    })}</tbody>
                  </table>
                  </div>)}
                </div>
                {!displayedSkills.length && <p className="empty-copy skill-empty-copy">{t("investigators.noFilterMatch")}</p>}
                <p className="permission-hint">{t("investigators.skillOver75Hint")}</p>
              </section>
              <label>{t("investigators.weaponsFormat")}<textarea rows={4} value={manual.weaponsText} onChange={(event) => patchManual({ weaponsText: event.target.value })} /></label>
              <div className="form-grid compact-fields"><label>{t("investigators.currency")}<input value={manual.currency} onChange={(event) => patchManual({ currency: event.target.value })} /></label><label>{t("investigators.cash")}<input min="0" type="number" value={manual.cash} onChange={(event) => patchManual({ cash: event.target.value })} /></label></div><p className="permission-hint">{t("investigators.creditRatingHint")}</p>
              <label>{t("investigators.itemsFormat")}<textarea rows={4} value={manual.itemsText} onChange={(event) => patchManual({ itemsText: event.target.value })} /></label>
              <div className="form-grid background-fields">{backgroundFieldKeys.map((key) => <label key={key}>{backgroundLabel(t, key)}<textarea rows={3} value={manual.background[key]} onChange={(event) => { setManual((current) => ({ ...current, background: { ...current.background, [key]: event.target.value } })); setManualPreview(null); }} /></label>)}</div>
              {manualPreview && <div className="import-preview"><div className="derived-grid"><span>HP <strong>{manualPreview.canonical_sheet.derived.max_hp}</strong></span><span>SAN <strong>{manualPreview.canonical_sheet.derived.initial_san}</strong></span><span>MP <strong>{manualPreview.canonical_sheet.derived.max_mp}</strong></span><span>MOV <strong>{manualPreview.canonical_sheet.derived.mov}</strong></span><span>DB <strong>{manualPreview.canonical_sheet.derived.damage_bonus}</strong></span><span>{t("investigators.build")} <strong>{manualPreview.canonical_sheet.derived.build}</strong></span></div>{manualPreview.warnings.length > 0 && <ul>{manualPreview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}</div>}
              <label>{t("investigators.saveAs")}<select value={manualTargetId} onChange={(event) => setManualTargetId(event.target.value)}><option value="">{t("investigators.saveAsNew")}</option>{investigators.map((investigator) => <option key={investigator.id} value={investigator.id}>{t("investigators.asNewVersion", { name: investigator.name })}</option>)}</select></label>
              <div className="inline-actions"><button className="secondary-button" disabled={busy || !manual.name.trim()} onClick={() => void validateManual()} type="button"><ShieldCheck size={16} />{t("investigators.rulePreview")}</button><button className="primary-button" disabled={busy || !manual.name.trim()} type="submit"><Save size={16} />{t("investigators.saveImmutableDraft")}</button></div>
              </fieldset>
            </form>
          </section>
        </>
      )}

      {campaign && identity?.role === "kp" && <section className="page-card investigator-review-card">
        <div className="page-intro"><div><p className="eyebrow">{campaign.title}</p><h2>{t("investigators.kpReview")}</h2></div><button className="ghost-button" disabled={busy} onClick={() => void loadCampaignRecords()} type="button"><RefreshCw size={15} />{t("common.refresh")}</button></div>
        {campaignRecords.length ? campaignRecords.map((record) => <article className="investigator-review-record" key={record.investigator_id}>
          <div className="preview-heading"><div><strong>{record.name} · v{record.submitted_revision?.revision_no}</strong><span>{investigatorStatusLabel(t, record.status)} · {record.submitted_revision?.canonical_sheet.identity.occupation || t("investigators.noOccupation")}</span></div><span className={`proposal-status ${record.status}`}>{investigatorStatusLabel(t, record.status)}</span></div>
          {record.submitted_revision?.warnings.length ? <details open><summary>{t("investigators.ruleWarnings", { count: record.submitted_revision.warnings.length })}</summary><ul>{record.submitted_revision.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></details> : <p className="permission-hint">{t("investigators.noRuleWarnings")}</p>}
          {record.diff.length > 0 && <details><summary>{t("investigators.diffSummary", { count: record.diff.length })}</summary><ul className="diff-list">{record.diff.slice(0, 30).map((item) => <li key={item.path}><code>{item.path}</code><span>{JSON.stringify(item.before) ?? "∅"} → {JSON.stringify(item.after) ?? "∅"}</span></li>)}</ul></details>}
          {record.status === "submitted" && <><label>{t("investigators.reviewComment")}<textarea rows={3} value={reviewComments[record.investigator_id] || ""} onChange={(event) => setReviewComments((current) => ({ ...current, [record.investigator_id]: event.target.value }))} /></label><div className="inline-actions"><button className="primary-button" disabled={busy} onClick={() => void review(record, "approved")} type="button"><CheckCircle2 size={16} />{t("investigators.approveCurrent")}</button><button className="secondary-button" disabled={busy || !(reviewComments[record.investigator_id] || "").trim()} onClick={() => void review(record, "changes_requested")} type="button"><XCircle size={16} />{t("investigators.returnChanges")}</button></div></>}
          {record.approved_revision_id && <div className="approval-tools"><label>{t("investigators.bindToSeat")}<select value={bindingMembers[record.investigator_id] || ""} onChange={(event) => setBindingMembers((current) => ({ ...current, [record.investigator_id]: event.target.value }))}><option value="">{t("investigators.selectPlayer")}</option>{members.map((member) => <option key={member.id} value={member.id}>{member.display_name}{member.pc_id ? t("investigators.hasRole") : ""}</option>)}</select></label><button className="secondary-button" disabled={busy || !bindingMembers[record.investigator_id]} onClick={() => void bindInvestigator(record)} type="button">{t("investigators.bindApproved")}</button></div>}
          {record.campaign_state && <div className="runtime-state-editor"><strong>{t("investigators.runtimeState", { version: record.campaign_state.state_version })}</strong><div className="attribute-entry-grid">{(["current_hp", "current_san", "current_mp", "current_luck"] as const).map((key) => <label key={key}>{key.replace("current_", "").toUpperCase()}<input min="0" type="number" value={stateDrafts[record.investigator_id]?.[key] ?? ""} onChange={(event) => setStateDrafts((current) => ({ ...current, [record.investigator_id]: { ...current[record.investigator_id], [key]: event.target.value } }))} /></label>)}</div><button className="ghost-button" disabled={busy} onClick={() => void updateRuntimeState(record)} type="button">{t("investigators.saveState")}</button></div>}
          {record.status === "approved" && <KpTimelineTools
            busy={busy}
            draft={changeDrafts[record.investigator_id] || EMPTY_PERMANENT_CHANGE}
            onDraftChange={(changes) => setChangeDrafts((current) => ({
              ...current,
              [record.investigator_id]: {
                ...(current[record.investigator_id] || EMPTY_PERMANENT_CHANGE),
                ...changes
              }
            }))}
            onLoad={() => void loadTimeline(record.investigator_id)}
            onPropose={() => void proposePermanentChange(record)}
            timeline={timelines[record.investigator_id]}
          />}
        </article>) : <p className="empty-copy">{t("investigators.noSubmissions")}</p>}
      </section>}
      <p className="inline-message investigator-global-message">{message}</p>
    </div>
  );
}
