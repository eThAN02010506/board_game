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
