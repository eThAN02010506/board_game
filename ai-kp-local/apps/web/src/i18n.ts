import i18n from "i18next";
import { initReactI18next } from "react-i18next";

// 检测浏览器语言：zh-* 视为中文，其余视为英文。
export function detectUiLanguage(): string {
  if (typeof navigator === "undefined") return "zh-CN";
  const language = (navigator.language ?? "").toLowerCase();
  return language.startsWith("zh") ? "zh-CN" : "en-US";
}

const en = {
  common: {
    appName: "AI KP Local",
    skipToContent: "Skip to main content",
    connect: "Connect Backend",
    loading: "Loading",
    playerRole: "Player",
    noSession: "Not in a session",
    navLabel: "Workspace pages",
    planned: "Planned",
    opened: "Opened",
    refresh: "Refresh",
    save: "Save",
    cancel: "Cancel",
    confirm: "Confirm",
    back: "Back",
    delete: "Delete",
    workspace: {
      kp: "Keeper Studio",
      player: "Player Table",
      local: "Local Narrative Workbench"
    },
    realtime: {
      live: "Live sync",
      connecting: "Connecting",
      retrying: "Reconnecting",
      offline: "Offline"
    },
    nav: {
      play: "Play",
      campaigns: "Campaigns",
      investigators: "Investigators",
      maps: "Maps & Tokens",
      memory: "Memory",
      facts: "World Facts",
      handouts: "Handouts",
      npcs: "NPCs",
      rules: "Rules",
      modules: "KP Books",
      models: "Model Settings",
      evaluations: "Simulation",
      planning: "Planning"
    }
  },
  checks: {
    title: "Checks & Rolls",
    createCheck: "Create Check",
    skillOrAttribute: "Skill or Attribute",
    player: "Player",
    difficulty: "Difficulty",
    targetValue: "Target Value",
    publishCheck: "Publish Check",
    refreshChecks: "Refresh Checks",
    allowPush: "Allow pushed roll",
    createOpposed: "Create Opposed Check",
    opposeTitle: "Opposed Checks",
    publishBoth: "Publish Both Checks",
    digitalRoll: "Digital Roll",
    physicalRoll: "Enter Physical Dice",
    confirmPhysical: "Confirm Physical",
    replayVerify: "Replay Verify",
    pendingRoll: "Pending roll",
    resolved: "Resolved"
  },
  play: {
    noSession: "Not in a session",
    joinFirst: "Join a session from the Campaigns page first, then enter the play table."
  },
  actions: {
    title: "Action Confirmation",
    playerActionLabel: "Player Action",
    searchMemory: "Search Memory",
    submitToKp: "Submit to KP",
    initialRuling: "AI preliminary ruling · not executed",
    confirmRuling: "Confirm Ruling",
    reviseRuling: "Revise Action"
  },
  sessions: {
    title: "Campaigns",
    eyebrow: "Campaign sessions & seats",
    joinCodeLabel: "Join Code",
    joinWithCode: "Join with Code",
    playerDisplayNameLabel: "Player Display Name",
    startKpSession: "Start KP Session",
    kpDisplayNameLabel: "KP Display Name",
    identityMeta: "Current identity",
    stableSeat: "Stable seat",
    pcBound: "Investigator bound",
    pcPending: "Investigator pending review",
    seatSectionTitle: "Seat Invites",
    seatSectionHint: "Create one-time invite codes for players; they become stable identities after claiming.",
    seatNameLabel: "Seat Name",
    createSeatInvite: "Create Seat Invite",
    statusOpen: "Open",
    statusClaimed: "Claimed",
    statusRevoked: "Revoked",
    noPlayerYet: "No one claimed yet",
    noSeats: "No seats yet.",
    revokeSeat: "Revoke Seat",
    reissue: "Reissue",
    memberValid: "Valid",
    memberRevoked: "Revoked",
    refreshMembers: "Refresh Members",
    closeSession: "Close Session",
    legacyJoinHint: "Enter a legacy shared invite code to join."
  },
  proposals: {
    title: "KP Approval",
    refreshDrafts: "Refresh Drafts",
    checkConsequenceSuffix: " · Check consequence",
    worldExpansionSuffix: " · World expansion",
    actionRuling: "Action Ruling",
    feasiblePossible: "Possible",
    feasiblePartial: "Partially feasible",
    feasibleImpossible: "Impossible",
    resolutionAutomatic: "Automatic",
    resolutionCheck: "Needs check",
    resolutionOpposed: "Needs opposed",
    resolutionNoRoll: "No roll",
    goal: "Goal",
    method: "Method",
    target: "Target",
    reason: "Reason",
    maximumEffect: "Max effect",
    assumptions: "Assumptions",
    conflicts: "Conflicts",
    alternatives: "Alternatives",
    effectChecks: "Checks",
    effectEvents: "Events",
    effectMemories: "Memories",
    effectNpcUpdates: "NPC updates",
    effectMapMoves: "Map moves",
    effectFacts: "Strict world facts",
    noDrafts: "No drafts yet.",
    overrideText: "Override public narration",
    inspectContext: "Inspect AI context",
    approve: "Approve",
    reject: "Reject",
    contextSnapshot: "Context snapshot",
    finalPrompt: "Final prompt",
    includedSources: "Included sources",
    excludedSources: "Excluded sources"
  },
  investigators: {
    title: "Investigators",
    newSheet: "New Investigator",
    importExcel: "Import Excel",
    submit: "Submit",
    approve: "Approve",
    returnChanges: "Request Changes",
    attributes: "Attributes",
    occupationPoints: "Occupation Points",
    interestPoints: "Interest Points",
    save: "Save",
    name: "Name",
    occupation: "Occupation",
    age: "Age",
    era: "Era",
    gender: "Gender",
    residence: "Residence",
    birthplace: "Birthplace",
    heroTitle: "Create, import and keep investigators long-term",
    noInvestigators: "No investigators yet.",
    occupationFormula: "Occupation point formula"
  },
  gameplay: {
    title: "Combat, Chases & State",
    refresh: "Refresh",
    combat: "Combat",
    chase: "Chase",
    type: "Type",
    encounterTitle: "Title",
    locationChain: "Location chain (comma separated)",
    createEncounter: "Create Encounter",
    advanceTurn: "Advance Turn",
    endEncounter: "End Encounter",
    locationUnknown: "Unknown location",
    damage: "Damage",
    applyDamage: "Apply Damage",
    attacker: "Attacker",
    target: "Target",
    attackSkill: "Attack Skill",
    defenseSkill: "Defense Skill",
    defense: "Defense",
    dodge: "Dodge",
    fightBack: "Fight Back",
    resolveMelee: "Resolve Melee",
    resolveManeuver: "Resolve Maneuver",
    resolveFirearm: "Resolve Firearm",
    move: "Move",
    version: "Version"
  },
  gamedata: {
    attributes: {
      str: "Strength",
      con: "Constitution",
      siz: "Size",
      dex: "Dexterity",
      app: "Appearance",
      int: "Intelligence",
      pow: "Power",
      edu: "Education",
      luck: "Luck"
    },
    difficulties: {
      regular: "Regular",
      hard: "Hard",
      extreme: "Extreme"
    },
    levels: {
      critical: "Critical",
      extreme: "Extreme Success",
      hard: "Hard Success",
      regular: "Regular Success",
      failure: "Failure",
      fumble: "Fumble"
    },
    visibility: {
      public: "Public (Table)",
      private: "Roller & Keeper",
      blind: "Keeper Only"
    }
  }
};

const zh = {
  common: {
    appName: "AI KP Local",
    skipToContent: "跳到主内容",
    connect: "连接后端",
    loading: "请求中",
    playerRole: "玩家",
    noSession: "未加入会话",
    navLabel: "工作台页面",
    planned: "规划",
    opened: "已打开",
    refresh: "刷新",
    save: "保存",
    cancel: "取消",
    confirm: "确认",
    back: "返回",
    delete: "删除",
    workspace: {
      kp: "KP 导演台",
      player: "玩家游玩台",
      local: "本地叙事工作台"
    },
    realtime: {
      live: "实时同步",
      connecting: "正在连接",
      retrying: "重新连接",
      offline: "同步离线"
    },
    nav: {
      play: "游玩桌面",
      campaigns: "团与权限",
      investigators: "调查员",
      maps: "地图棋子",
      memory: "角色记忆",
      facts: "世界事实",
      handouts: "手册线索",
      npcs: "NPC",
      rules: "规则知识",
      modules: "KP 本",
      models: "模型设置",
      evaluations: "模拟团评测",
      planning: "功能规划"
    }
  },
  checks: {
    title: "待检定与掷骰",
    createCheck: "创建检定",
    skillOrAttribute: "技能或属性",
    player: "玩家",
    difficulty: "难度",
    targetValue: "目标值",
    publishCheck: "发布检定",
    refreshChecks: "刷新检定",
    allowPush: "允许孤注一掷",
    createOpposed: "创建对抗检定",
    opposeTitle: "对抗检定",
    publishBoth: "发布双方检定",
    digitalRoll: "数字骰",
    physicalRoll: "录入实体骰",
    confirmPhysical: "确认实体骰",
    replayVerify: "重放校验",
    pendingRoll: "待掷",
    resolved: "已结算"
  },
  play: {
    noSession: "未加入会话",
    joinFirst: "请先在“团与权限”页加入会话，再进入游玩台。"
  },
  actions: {
    title: "行动确认",
    playerActionLabel: "玩家行动",
    searchMemory: "检索记忆",
    submitToKp: "提交给 KP",
    initialRuling: "AI 初步裁定 · 尚未执行",
    confirmRuling: "确认此裁定",
    reviseRuling: "修改行动"
  },
  sessions: {
    title: "团与权限",
    eyebrow: "团会话与席位",
    joinCodeLabel: "邀请码",
    joinWithCode: "用邀请码加入",
    playerDisplayNameLabel: "玩家显示名",
    startKpSession: "开始 KP 会话",
    kpDisplayNameLabel: "KP 显示名",
    identityMeta: "当前身份",
    stableSeat: "稳定席位",
    pcBound: "已绑定调查员",
    pcPending: "调查员待审核",
    seatSectionTitle: "席位邀请",
    seatSectionHint: "为玩家创建一次性邀请码，认领后转为稳定身份。",
    seatNameLabel: "席位名称",
    createSeatInvite: "创建席位邀请",
    statusOpen: "等待认领",
    statusClaimed: "已认领",
    statusRevoked: "已撤销",
    noPlayerYet: "尚未有人认领",
    noSeats: "暂无席位。",
    revokeSeat: "撤销席位",
    reissue: "重新签发",
    memberValid: "有效",
    memberRevoked: "已撤销",
    refreshMembers: "刷新成员",
    closeSession: "关闭会话",
    legacyJoinHint: "输入旧版共享邀请码加入会话。"
  },
  proposals: {
    title: "人类 KP 审批",
    refreshDrafts: "刷新草稿",
    checkConsequenceSuffix: " · 检定后果",
    worldExpansionSuffix: " · 世界补全",
    actionRuling: "行动裁定",
    feasiblePossible: "可行",
    feasiblePartial: "部分可行",
    feasibleImpossible: "目标不可行",
    resolutionAutomatic: "自动结算",
    resolutionCheck: "需要检定",
    resolutionOpposed: "需要对抗",
    resolutionNoRoll: "不掷骰",
    goal: "目标",
    method: "手段",
    target: "对象",
    reason: "理由",
    maximumEffect: "成功上限",
    assumptions: "待确认假设",
    conflicts: "潜在冲突",
    alternatives: "替代方案",
    effectChecks: "待检定",
    effectEvents: "事件",
    effectMemories: "长期记忆",
    effectNpcUpdates: "NPC 变更",
    effectMapMoves: "地图移动",
    effectFacts: "严格世界事实",
    noDrafts: "暂无草稿。",
    overrideText: "覆写公开描述",
    inspectContext: "检查 AI 上下文",
    approve: "批准落库",
    reject: "拒绝",
    contextSnapshot: "上下文快照",
    finalPrompt: "最终提示词",
    includedSources: "纳入来源",
    excludedSources: "排除来源"
  },
  investigators: {
    title: "调查员",
    newSheet: "新建调查员",
    importExcel: "导入 Excel",
    submit: "提交",
    approve: "批准",
    returnChanges: "退回修改",
    attributes: "属性",
    occupationPoints: "职业点",
    interestPoints: "兴趣点",
    save: "保存",
    name: "姓名",
    occupation: "职业",
    age: "年龄",
    era: "时代",
    gender: "性别",
    residence: "居住地",
    birthplace: "出生地",
    heroTitle: "创建、导入并长期保存调查员",
    noInvestigators: "还没有调查员。",
    occupationFormula: "职业点公式"
  },
  gameplay: {
    title: "战斗、追逐与状态",
    refresh: "刷新",
    combat: "战斗",
    chase: "追逐",
    type: "类型",
    encounterTitle: "标题",
    locationChain: "地点链（逗号分隔）",
    createEncounter: "创建遭遇",
    advanceTurn: "推进回合",
    endEncounter: "结束遭遇",
    locationUnknown: "位置未知",
    damage: "伤害",
    applyDamage: "应用伤害",
    attacker: "攻击者",
    target: "目标",
    attackSkill: "攻击技能",
    defenseSkill: "防御技能",
    defense: "防御",
    dodge: "闪避",
    fightBack: "反击",
    resolveMelee: "裁决近战",
    resolveManeuver: "裁决战技",
    resolveFirearm: "裁决枪械",
    move: "移动",
    version: "版本"
  },
  gamedata: {
    attributes: {
      str: "力量",
      con: "体质",
      siz: "体型",
      dex: "敏捷",
      app: "外貌",
      int: "智力",
      pow: "意志",
      edu: "教育",
      luck: "幸运"
    },
    difficulties: {
      regular: "常规",
      hard: "困难",
      extreme: "极难"
    },
    levels: {
      critical: "大成功",
      extreme: "极难成功",
      hard: "困难成功",
      regular: "常规成功",
      failure: "失败",
      fumble: "大失败"
    },
    visibility: {
      public: "全桌公开",
      private: "掷骰者与 KP",
      blind: "仅 KP"
    }
  }
};

i18n.use(initReactI18next).init({
  lng: detectUiLanguage(),
  fallbackLng: "zh-CN",
  resources: {
    "zh-CN": { translation: zh },
    "en-US": { translation: en }
  },
  interpolation: {
    escapeValue: false
  },
  // 防止控制台"missing key"噪音，缺键时回退中文。
  returnEmptyString: false,
  returnNull: false
});

/** 游戏术语（专有名词）代码映射：属性短键 → 当前语言显示名。 */
export function attributeLabel(key: string): string {
  const t = i18n.t;
  return t(`gamedata.attributes.${key}`) !== `gamedata.attributes.${key}`
    ? t(`gamedata.attributes.${key}`)
    : key.toUpperCase();
}

export default i18n;
