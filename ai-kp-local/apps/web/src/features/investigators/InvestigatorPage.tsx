import {
  CheckCircle2,
  FileSpreadsheet,
  Minus,
  Plus,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  UserRound,
  XCircle
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { credentialBridge, requestFile, requestJson } from "../../api/client";
import type {
  AuthIdentity,
  Campaign,
  CampaignInvestigator,
  CharacterSheet,
  CharacterSkillCatalogItem,
  Investigator,
  InvestigatorImportPreview,
  InvestigatorManualPreview,
  PlayerProfile,
  PlayerProfileBundle,
  SessionMember
} from "../../api/types";
import { readPlayerProfileToken, writePlayerProfileToken } from "../../session/session-storage";

const attributeFields = ["str", "con", "siz", "dex", "app", "int", "pow", "edu", "luck"];
const backgroundFields = [
  ["appearance", "外貌"],
  ["beliefs", "思想与信念"],
  ["significant_people", "重要之人"],
  ["significant_places", "意义非凡之地"],
  ["treasured_possessions", "宝贵之物"],
  ["traits", "特质"],
  ["injuries_scars", "伤口与疤痕"],
  ["phobias_manias", "恐惧症与躁狂症"],
  ["secret", "个人秘密（本人/KP）"]
] as const;

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
    background: Object.fromEntries(backgroundFields.map(([key]) => [key, ""]))
  };
}

type Props = {
  campaign: Campaign | null;
  identity: AuthIdentity | null;
};

const statusLabels: Record<CampaignInvestigator["status"], string> = {
  draft: "草稿",
  submitted: "待 KP 审核",
  changes_requested: "需修改",
  approved: "已批准",
  withdrawn: "已撤回"
};

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
    background: Object.fromEntries(backgroundFields.map(([key]) => [key, String(background[key] || "")]))
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
  const [profile, setProfile] = useState<PlayerProfile | null>(null);
  const [displayName, setDisplayName] = useState("玩家");
  const [investigators, setInvestigators] = useState<Investigator[]>([]);
  const [campaignRecords, setCampaignRecords] = useState<CampaignInvestigator[]>([]);
  const [members, setMembers] = useState<SessionMember[]>([]);
  const [skillCatalog, setSkillCatalog] = useState<CharacterSkillCatalogItem[]>([]);
  const [skillQuery, setSkillQuery] = useState("");
  const [showAllocatedSkillsOnly, setShowAllocatedSkillsOnly] = useState(false);
  const [preview, setPreview] = useState<InvestigatorImportPreview | null>(null);
  const [manualPreview, setManualPreview] = useState<InvestigatorManualPreview | null>(null);
  const [targetInvestigatorId, setTargetInvestigatorId] = useState("");
  const [manualTargetId, setManualTargetId] = useState("");
  const [manual, setManual] = useState<ManualDraft>(() => createEmptyDraft());
  const [reviewComments, setReviewComments] = useState<Record<string, string>>({});
  const [bindingMembers, setBindingMembers] = useState<Record<string, string>>({});
  const [stateDrafts, setStateDrafts] = useState<Record<string, Record<string, string>>>({});
  const [message, setMessage] = useState("正在读取本地玩家档案……");
  const [busy, setBusy] = useState(false);

  const previewSheet = preview?.canonical_sheet;
  const strongestSkills = useMemo(
    () => [...(previewSheet?.skills ?? [])].sort((left, right) => right.current_value - left.current_value).slice(0, 8),
    [previewSheet]
  );
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

  function patchManual(changes: Partial<ManualDraft>) {
    setManual((current) => ({ ...current, ...changes }));
    setManualPreview(null);
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
    if (!credentialBridge.snapshot().playerToken) return;
    if (!silent) setBusy(true);
    try {
      const [nextProfile, library] = await Promise.all([
        requestJson<PlayerProfile>("/player-profile"),
        requestJson<Investigator[]>("/investigators")
      ]);
      setProfile(nextProfile);
      setInvestigators(library);
      setMessage(library.length ? `已读取 ${library.length} 名调查员。` : "档案已就绪，可以建卡或导入 Excel。");
    } catch (error) {
      credentialBridge.player("");
      writePlayerProfileToken("");
      setProfile(null);
      setInvestigators([]);
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (!silent) setBusy(false);
    }
  }

  async function loadCampaignRecords(silent = false) {
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
        setCampaignRecords(await requestJson<CampaignInvestigator[]>(`/campaigns/${campaign.id}/my-investigators`));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (!silent) setBusy(false);
    }
  }

  useEffect(() => {
    void loadSkillCatalog();
    const stored = readPlayerProfileToken();
    credentialBridge.player(stored);
    if (stored) void loadLibrary();
    else setMessage("先建立本机玩家档案；角色卡会长期保存在本地数据库中。");
  }, []);

  useEffect(() => {
    void loadCampaignRecords(true);
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
      setMessage("本机玩家档案已建立。令牌只保存在当前浏览器。");
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
      setMessage(`已安全读取 ${file.name}；忽略 ${result.ignored_formula_cells} 个公式单元格。`);
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
      setMessage(`已保存 ${saved.name} 的第 ${saved.current_revision.revision_no} 版不可变草稿。`);
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
      setMessage(`规则预览完成，共 ${result.warnings.length} 项需确认内容。`);
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
      setMessage(`已保存 ${saved.name} 的第 ${saved.current_revision.revision_no} 版草稿。`);
      setManual(createEmptyDraft(skillCatalog));
      setManualPreview(null);
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
    setMessage(`已将 ${investigator.name} v${investigator.current_revision.revision_no} 载入编辑器；保存时会创建新版本。`);
  }

  async function submitInvestigator(investigator: Investigator) {
    if (!campaign || identity?.role !== "player") return;
    setBusy(true);
    try {
      const record = await requestJson<CampaignInvestigator>(
        `/campaigns/${campaign.id}/investigators/${investigator.id}/submit`,
        { method: "POST", body: JSON.stringify({ revision_id: investigator.current_revision_id }) }
      );
      setMessage(`${investigator.name} v${record.submitted_revision?.revision_no} 已提交给 KP。`);
      await loadCampaignRecords(true);
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
      setMessage(action === "approved" ? `已批准 ${record.name}。` : `已退回 ${record.name} 修改。`);
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
      setMessage(`已将 ${record.name} 绑定到选定玩家席位。`);
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
      setMessage(`已更新 ${record.name} 的团内状态。`);
      await loadCampaignRecords(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="investigator-page" id="investigator-section">
      {!profile ? (
        <section className="page-card investigator-profile-setup">
          <div className="page-intro"><div><p className="eyebrow">独立角色卡页面</p><h2>建立本机玩家档案</h2></div><UserRound size={24} /></div>
          <p>调查员属于玩家档案，不绑定某一个团；同一调查员可分别提交给不同 KP 审核。</p>
          <form onSubmit={createProfile}><label>玩家显示名<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label><button className="primary-button" disabled={busy || !displayName.trim()} type="submit"><Plus size={16} />建立档案</button></form>
        </section>
      ) : (
        <>
          <section className="page-card investigator-library">
            <div className="page-intro"><div><p className="eyebrow">{profile.display_name} 的本地调查员库</p><h2>角色卡版本</h2></div><button className="ghost-button" disabled={busy} onClick={() => void loadLibrary()} type="button"><RefreshCw size={15} />刷新</button></div>
            <div className="investigator-list">
              {investigators.length ? investigators.map((investigator) => {
                const record = campaignRecords.find((item) => item.investigator_id === investigator.id);
                return <article className="investigator-record" key={investigator.id}>
                  <div><strong>{investigator.name}</strong><span>{String(investigator.current_revision.public_summary.occupation || "未填写职业")}</span></div>
                  <small>不可变 v{investigator.current_revision.revision_no} · {investigator.current_revision.source_type}</small>
                  {record && <span className={`proposal-status ${record.status}`}>{statusLabels[record.status]}</span>}
                  {record?.review_comment && <p className="permission-hint">KP：{record.review_comment}</p>}
                  <details className="character-sheet-details"><summary>查看角色卡摘要</summary><div className="derived-grid"><span>HP <strong>{investigator.current_revision.canonical_sheet.derived.max_hp}</strong></span><span>SAN <strong>{investigator.current_revision.canonical_sheet.derived.initial_san}</strong></span><span>MP <strong>{investigator.current_revision.canonical_sheet.derived.max_mp}</strong></span><span>MOV <strong>{investigator.current_revision.canonical_sheet.derived.mov}</strong></span><span>DB <strong>{investigator.current_revision.canonical_sheet.derived.damage_bonus}</strong></span><span>体格 <strong>{investigator.current_revision.canonical_sheet.derived.build}</strong></span></div><div className="skill-preview">{[...investigator.current_revision.canonical_sheet.skills].sort((left, right) => right.current_value - left.current_value).slice(0, 8).map((skill) => <span key={skill.skill_key}>{skill.display_name} {skill.current_value}</span>)}</div></details>
                  <div className="inline-actions"><button className="ghost-button" disabled={busy} onClick={() => editInvestigator(investigator)} type="button">载入编辑器</button>{campaign && identity?.role === "player" && <button className="secondary-button" disabled={busy || record?.status === "submitted"} onClick={() => void submitInvestigator(investigator)} type="button">提交当前版本给 {campaign.title} KP</button>}</div>
                </article>;
              }) : <p className="empty-copy">还没有调查员。</p>}
            </div>
          </section>

          <section className="page-card excel-import-card">
            <div className="page-intro"><div><p className="eyebrow">参考 COC空白卡.xlsx</p><h2>导入 Excel 角色卡</h2></div><FileSpreadsheet size={24} /></div>
            <label className="file-drop">选择 .xlsx 文件<input accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" disabled={busy} onChange={(event) => void previewExcel(event.target.files?.[0])} type="file" /><small>只读取允许字段，不运行公式、宏或外部链接。</small></label>
            {previewSheet && preview && <div className="import-preview">
              <div className="preview-heading"><div><strong>{previewSheet.identity.name || "未命名调查员"}</strong><span>{previewSheet.identity.occupation || "未填写职业"} · {previewSheet.identity.era || "时代未填"}</span></div><ShieldCheck size={20} /></div>
              <div className="derived-grid"><span>HP <strong>{previewSheet.derived.max_hp}</strong></span><span>SAN <strong>{previewSheet.derived.initial_san}</strong></span><span>MP <strong>{previewSheet.derived.max_mp}</strong></span><span>MOV <strong>{previewSheet.derived.mov}</strong></span><span>DB <strong>{previewSheet.derived.damage_bonus}</strong></span><span>体格 <strong>{previewSheet.derived.build}</strong></span></div>
              <div className="skill-preview">{strongestSkills.map((skill) => <span key={skill.skill_key}>{skill.display_name}{skill.specialization ? `（${skill.specialization}）` : ""} {skill.current_value}</span>)}</div>
              {preview.warnings.length > 0 && <details open><summary>{preview.warnings.length} 项需要确认</summary><ul>{preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></details>}
              <label>保存方式<select value={targetInvestigatorId} onChange={(event) => setTargetInvestigatorId(event.target.value)}><option value="">保存为新调查员</option>{investigators.map((investigator) => <option key={investigator.id} value={investigator.id}>作为“{investigator.name}”的新版本</option>)}</select></label>
              <button className="primary-button" disabled={busy || !previewSheet.identity.name} onClick={() => void savePreview()} type="button"><Save size={16} />保存不可变草稿版本</button>
            </div>}
          </section>

          <section className="page-card manual-character-card">
            <div className="page-intro"><div><p className="eyebrow">完整手工建卡</p><h2>调查员编辑器</h2></div><UserRound size={24} /></div>
            <form onSubmit={saveManual}>
              <div className="form-grid compact-fields">
                <label>姓名<input required value={manual.name} onChange={(event) => patchManual({ name: event.target.value })} /></label><label>玩家名<input value={manual.playerName} onChange={(event) => patchManual({ playerName: event.target.value })} /></label><label>职业<input value={manual.occupation} onChange={(event) => patchManual({ occupation: event.target.value })} /></label><label>时代<input value={manual.era} onChange={(event) => patchManual({ era: event.target.value })} /></label><label>年龄<input min="1" type="number" value={manual.age} onChange={(event) => patchManual({ age: event.target.value })} /></label><label>性别描述<input value={manual.gender} onChange={(event) => patchManual({ gender: event.target.value })} /></label><label>居住地<input value={manual.residence} onChange={(event) => patchManual({ residence: event.target.value })} /></label><label>出生地<input value={manual.birthplace} onChange={(event) => patchManual({ birthplace: event.target.value })} /></label>
              </div>
              <div className="attribute-entry-grid">{attributeFields.map((key) => <label key={key}>{key.toUpperCase()}<input min="0" type="number" value={manual.attributes[key]} onChange={(event) => { setManual((current) => ({ ...current, attributes: { ...current.attributes, [key]: event.target.value } })); setManualPreview(null); }} /></label>)}</div>
              <label>职业点公式<select value={manual.occupationFormula} onChange={(event) => patchManual({ occupationFormula: event.target.value as ManualDraft["occupationFormula"] })}><option value="edu4">EDU×4</option><option value="edu2_app2">EDU×2 + APP×2</option><option value="edu2_dex2">EDU×2 + DEX×2</option><option value="edu2_pow2">EDU×2 + POW×2</option><option value="edu2_str2">EDU×2 + STR×2</option></select></label>
              <div className="derived-grid skill-budget-summary"><span className={occupationUsed > currentOccupationBudget ? "budget-over" : ""}>职业点 <strong>{occupationUsed}/{currentOccupationBudget}</strong></span><span className={occupationUsed > currentOccupationBudget ? "budget-over" : ""}>职业剩余 <strong>{currentOccupationBudget - occupationUsed}</strong></span><span className={interestUsed > interestBudget ? "budget-over" : ""}>兴趣点 <strong>{interestUsed}/{interestBudget}</strong></span><span className={interestUsed > interestBudget ? "budget-over" : ""}>兴趣剩余 <strong>{interestBudget - interestUsed}</strong></span></div>
              <section className="skill-allocation-editor">
                <div className="skill-allocation-heading">
                  <div><p className="eyebrow">参考 COC空白卡.xlsx</p><h3>完整技能与加点</h3><small>共 {manual.skills.length} 项。基础值、普通/困难/极限成功率会自动计算。</small></div>
                  <div className="skill-allocation-filters">
                    <label className="skill-search"><Search size={15} /><input aria-label="搜索技能" placeholder="搜索技能或专攻" value={skillQuery} onChange={(event) => setSkillQuery(event.target.value)} /></label>
                    <label className="skill-allocation-toggle"><input checked={showAllocatedSkillsOnly} onChange={(event) => setShowAllocatedSkillsOnly(event.target.checked)} type="checkbox" />只看已加点</label>
                  </div>
                </div>
                <div className="skill-table-scroll">
                  <table className="skill-allocation-table">
                    <thead><tr><th>技能</th><th>专攻</th><th>基础</th><th>职业点</th><th>兴趣点</th><th>普通</th><th>困难</th><th>极限</th></tr></thead>
                    <tbody>{displayedSkills.map((skill) => {
                      const currentValue = skillCurrentValue(skill, manual.attributes);
                      const baseValue = resolvedSkillBase(skill, manual.attributes);
                      return <tr className={!skill.creation_points_allowed ? "skill-points-locked" : currentValue > 75 ? "skill-over-75" : ""} key={skill.skill_key}>
                        <th scope="row"><DelayedSkillTooltip skill={skill} />{!skill.creation_points_allowed && <small>创建时不可加点</small>}</th>
                        <td>{skill.specialization_editable ? <input aria-label={`${skill.display_name}专攻`} placeholder="填写专攻" value={skill.specialization} onChange={(event) => patchSkill(skill.skill_key, { specialization: event.target.value })} /> : <span>{skill.specialization || "—"}</span>}</td>
                        <td><strong>{baseValue}</strong>{skill.base_formula !== "fixed" && <small>{skill.base_formula === "dex_half" ? "DEX÷2" : "EDU"}</small>}</td>
                        <td><div className="skill-point-stepper"><button aria-label={`减少${skill.display_name}职业点`} disabled={!skill.creation_points_allowed || skill.occupationPoints <= 0} onClick={() => adjustSkillPoints(skill, "occupationPoints", -1)} type="button"><Minus size={13} /></button><input aria-label={`${skill.display_name}职业点`} disabled={!skill.creation_points_allowed} min="0" type="number" value={skill.occupationPoints} onChange={(event) => patchSkill(skill.skill_key, { occupationPoints: Math.max(0, number(event.target.value)) })} /><button aria-label={`增加${skill.display_name}职业点`} disabled={!skill.creation_points_allowed} onClick={() => adjustSkillPoints(skill, "occupationPoints", 1)} type="button"><Plus size={13} /></button></div></td>
                        <td><div className="skill-point-stepper"><button aria-label={`减少${skill.display_name}兴趣点`} disabled={!skill.creation_points_allowed || skill.interestPoints <= 0} onClick={() => adjustSkillPoints(skill, "interestPoints", -1)} type="button"><Minus size={13} /></button><input aria-label={`${skill.display_name}兴趣点`} disabled={!skill.creation_points_allowed} min="0" type="number" value={skill.interestPoints} onChange={(event) => patchSkill(skill.skill_key, { interestPoints: Math.max(0, number(event.target.value)) })} /><button aria-label={`增加${skill.display_name}兴趣点`} disabled={!skill.creation_points_allowed} onClick={() => adjustSkillPoints(skill, "interestPoints", 1)} type="button"><Plus size={13} /></button></div></td>
                        <td><strong>{currentValue}</strong></td><td>{Math.floor(currentValue / 2)}</td><td>{Math.floor(currentValue / 5)}</td>
                      </tr>;
                    })}</tbody>
                  </table>
                  {!displayedSkills.length && <p className="empty-copy skill-empty-copy">没有符合当前筛选的技能。</p>}
                </div>
                <p className="permission-hint">技能超过 75 会标色并交由 KP 确认；克苏鲁神话在创建角色时不允许分配职业点或兴趣点。</p>
              </section>
              <label>武器（每行：名称|技能|伤害|射程|每轮次数|弹药|故障值）<textarea rows={4} value={manual.weaponsText} onChange={(event) => patchManual({ weaponsText: event.target.value })} /></label>
              <div className="form-grid compact-fields"><label>货币<input value={manual.currency} onChange={(event) => patchManual({ currency: event.target.value })} /></label><label>现金<input min="0" type="number" value={manual.cash} onChange={(event) => patchManual({ cash: event.target.value })} /></label></div><p className="permission-hint">信用评级由上方技能表统一加点，会同步用于资产摘要。</p>
              <label>物品（每行：名称|位置|可见性）<textarea rows={4} value={manual.itemsText} onChange={(event) => patchManual({ itemsText: event.target.value })} /></label>
              <div className="form-grid background-fields">{backgroundFields.map(([key, label]) => <label key={key}>{label}<textarea rows={3} value={manual.background[key]} onChange={(event) => { setManual((current) => ({ ...current, background: { ...current.background, [key]: event.target.value } })); setManualPreview(null); }} /></label>)}</div>
              {manualPreview && <div className="import-preview"><div className="derived-grid"><span>HP <strong>{manualPreview.canonical_sheet.derived.max_hp}</strong></span><span>SAN <strong>{manualPreview.canonical_sheet.derived.initial_san}</strong></span><span>MP <strong>{manualPreview.canonical_sheet.derived.max_mp}</strong></span><span>MOV <strong>{manualPreview.canonical_sheet.derived.mov}</strong></span><span>DB <strong>{manualPreview.canonical_sheet.derived.damage_bonus}</strong></span><span>体格 <strong>{manualPreview.canonical_sheet.derived.build}</strong></span></div>{manualPreview.warnings.length > 0 && <ul>{manualPreview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}</div>}
              <label>保存方式<select value={manualTargetId} onChange={(event) => setManualTargetId(event.target.value)}><option value="">保存为新调查员</option>{investigators.map((investigator) => <option key={investigator.id} value={investigator.id}>作为“{investigator.name}”的新版本</option>)}</select></label>
              <div className="inline-actions"><button className="secondary-button" disabled={busy || !manual.name.trim()} onClick={() => void validateManual()} type="button"><ShieldCheck size={16} />规则预览</button><button className="primary-button" disabled={busy || !manual.name.trim()} type="submit"><Save size={16} />保存不可变草稿</button></div>
            </form>
          </section>
        </>
      )}

      {campaign && identity?.role === "kp" && <section className="page-card investigator-review-card">
        <div className="page-intro"><div><p className="eyebrow">{campaign.title}</p><h2>KP 角色卡审核</h2></div><button className="ghost-button" disabled={busy} onClick={() => void loadCampaignRecords()} type="button"><RefreshCw size={15} />刷新</button></div>
        {campaignRecords.length ? campaignRecords.map((record) => <article className="investigator-review-record" key={record.investigator_id}>
          <div className="preview-heading"><div><strong>{record.name} · v{record.submitted_revision?.revision_no}</strong><span>{statusLabels[record.status]} · {record.submitted_revision?.canonical_sheet.identity.occupation || "未填职业"}</span></div><span className={`proposal-status ${record.status}`}>{statusLabels[record.status]}</span></div>
          {record.submitted_revision?.warnings.length ? <details open><summary>{record.submitted_revision.warnings.length} 项规则提示</summary><ul>{record.submitted_revision.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></details> : <p className="permission-hint">确定性角色规则校验未发现警告。</p>}
          {record.diff.length > 0 && <details><summary>与已批准版本相比：{record.diff.length} 项变更</summary><ul className="diff-list">{record.diff.slice(0, 30).map((item) => <li key={item.path}><code>{item.path}</code><span>{JSON.stringify(item.before) ?? "∅"} → {JSON.stringify(item.after) ?? "∅"}</span></li>)}</ul></details>}
          {record.status === "submitted" && <><label>给玩家的审核意见<textarea rows={3} value={reviewComments[record.investigator_id] || ""} onChange={(event) => setReviewComments((current) => ({ ...current, [record.investigator_id]: event.target.value }))} /></label><div className="inline-actions"><button className="primary-button" disabled={busy} onClick={() => void review(record, "approved")} type="button"><CheckCircle2 size={16} />批准当前版本</button><button className="secondary-button" disabled={busy || !(reviewComments[record.investigator_id] || "").trim()} onClick={() => void review(record, "changes_requested")} type="button"><XCircle size={16} />退回修改</button></div></>}
          {record.approved_revision_id && <div className="approval-tools"><label>绑定到玩家席位<select value={bindingMembers[record.investigator_id] || ""} onChange={(event) => setBindingMembers((current) => ({ ...current, [record.investigator_id]: event.target.value }))}><option value="">选择玩家</option>{members.map((member) => <option key={member.id} value={member.id}>{member.display_name}{member.pc_id ? "（已有角色）" : ""}</option>)}</select></label><button className="secondary-button" disabled={busy || !bindingMembers[record.investigator_id]} onClick={() => void bindInvestigator(record)} type="button">绑定已批准角色</button></div>}
          {record.campaign_state && <div className="runtime-state-editor"><strong>团内运行状态 · v{record.campaign_state.state_version}</strong><div className="attribute-entry-grid">{(["current_hp", "current_san", "current_mp", "current_luck"] as const).map((key) => <label key={key}>{key.replace("current_", "").toUpperCase()}<input min="0" type="number" value={stateDrafts[record.investigator_id]?.[key] ?? ""} onChange={(event) => setStateDrafts((current) => ({ ...current, [record.investigator_id]: { ...current[record.investigator_id], [key]: event.target.value } }))} /></label>)}</div><button className="ghost-button" disabled={busy} onClick={() => void updateRuntimeState(record)} type="button">保存团内状态</button></div>}
        </article>) : <p className="empty-copy">当前团还没有玩家提交调查员。</p>}
      </section>}
      <p className="inline-message investigator-global-message">{message}</p>
    </div>
  );
}
