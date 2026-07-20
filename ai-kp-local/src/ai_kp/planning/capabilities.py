from dataclasses import asdict, dataclass
from typing import Literal


CapabilityStatus = Literal["available", "partial", "planned"]
CapabilityAudience = Literal["all", "kp", "player"]


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    status: CapabilityStatus
    phase: str
    audience: CapabilityAudience
    summary: str
    dependencies: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="session_roles",
        label="团会话与权限",
        status="available",
        phase="MVP",
        audience="all",
        summary="KP/玩家加入、角色绑定、凭证撤销与会话关闭已可用。",
        acceptance=("KP 和玩家只能访问各自授权资源。",),
    ),
    Capability(
        id="map_workspace",
        label="地图、路线与棋子",
        status="available",
        phase="MVP",
        audience="all",
        summary="结构化地图、SVG、发布边界、棋子位置和移动版本已持久化。",
        acceptance=("重启后地图与棋子可恢复，玩家不可见隐藏地点。",),
    ),
    Capability(
        id="realtime_sync",
        label="多人实时同步",
        status="available",
        phase="MVP",
        audience="all",
        summary="单次票据、WebSocket、SQLite outbox、断线重连与角色可见性已可用。",
        acceptance=("进程重启后能按 cursor 续传，不泄露 KP 事件。",),
    ),
    Capability(
        id="proposal_approval",
        label="AI 草稿与人类 KP 审批",
        status="available",
        phase="MVP",
        audience="kp",
        summary="结构化草稿、上下文快照、批准、拒绝和原子落库已可用。",
        acceptance=("草稿未批准前不改变正式世界状态。",),
    ),
    Capability(
        id="memory_foundation",
        label="事件与长期记忆基础",
        status="available",
        phase="MVP",
        audience="all",
        summary="事件、主要/支线/NPC/线索记忆与角色隔离检索已可用。",
        acceptance=("重启后记忆可检索，玩家不能读取其他角色私有记忆。",),
    ),
    Capability(
        id="model_adapter",
        label="本地模型适配",
        status="available",
        phase="MVP",
        audience="kp",
        summary="OpenAI-compatible 模型列表发现与结构化 KP 回合已验证。",
        acceptance=("用提供者返回的真实 model ID 完成一次草稿并通过校验。",),
    ),
    Capability(
        id="character_sheets",
        label="完整角色卡创建与阅读",
        status="partial",
        phase="F2",
        audience="all",
        summary="已能创建空白 PC 并绑定玩家；缺少规则化编辑、导入、校验和展示 UI。",
        dependencies=("session_roles",),
        acceptance=("玩家可本地编辑自己的角色卡，其他玩家只看到公开摘要。",),
    ),
    Capability(
        id="seat_invitations",
        label="逐席邀请与稳定玩家身份",
        status="planned",
        phase="F1",
        audience="kp",
        summary="以单次、可撤销、可绑定角色的邀请替代共享加入码，并为长期角色建立稳定玩家身份。",
        dependencies=("session_roles",),
        acceptance=("撤销某一席位不会影响其他玩家，旧邀请不能被再次使用。",),
    ),
    Capability(
        id="module_library",
        label="KP 本库与模组阅读",
        status="partial",
        phase="F2",
        audience="kp",
        summary="纯文本切块和剧透边界已有 API；缺少导入 UI、章节树、搜索和多格式解析。",
        dependencies=("proposal_approval",),
        acceptance=("KP 可导入、预览、标记剧透范围，玩家端不可读取未揭示章节。",),
    ),
    Capability(
        id="module_document_import",
        label="多格式 KP 本导入",
        status="planned",
        phase="F2",
        audience="kp",
        summary="在纯文本之外解析 PDF、DOCX、图片 OCR，并保留页码、章节和原文证据。",
        dependencies=("module_library",),
        acceptance=("导入后每个切块都能追溯到原文件位置，秘密内容不会进入玩家视图。",),
    ),
    Capability(
        id="party_route_planning",
        label="分队路线规划",
        status="planned",
        phase="F2",
        audience="all",
        summary="为多个角色规划各自路线、时间与冲突，并保持线下跑团式棋子操作。",
        dependencies=("map_workspace", "character_sheets"),
        acceptance=("两名玩家可同时规划不同路线，不会相互覆盖棋子状态。",),
    ),
    Capability(
        id="check_resolution",
        label="待检定与掷骰状态机",
        status="planned",
        phase="F1",
        audience="all",
        summary="支持玩家掷骰、实体骰录入、暗骰、KP 覆盖和检定后结果草稿。",
        dependencies=("proposal_approval", "character_sheets"),
        acceptance=("掷骰前不得写入只有成功时才成立的事件或记忆。",),
    ),
    Capability(
        id="ruleset_plugins",
        label="规则系统插件",
        status="planned",
        phase="F3",
        audience="all",
        summary="把 CoC7 检定、伤害、成长等规则从通用回合流程中分离，并允许替换其他跑团系统。",
        dependencies=("check_resolution",),
        acceptance=("切换规则系统不会改写既有事件，检定结果可由对应插件重放验证。",),
    ),
    Capability(
        id="character_timeline",
        label="跨本角色身份与时间线",
        status="planned",
        phase="F2",
        audience="all",
        summary="区分现实团次时间、游戏世界时间、全局角色身份和各模组临时状态。",
        dependencies=("character_sheets", "memory_foundation"),
        acceptance=("同一角色在本 A/B 的永久经历连续，临时状态不会错误覆盖。",),
    ),
    Capability(
        id="npc_reappearance",
        label="NPC 档案与跨本再出现",
        status="partial",
        phase="F3",
        audience="kp",
        summary="已有全局 NPC、Campaign 关系与候选评分；缺少硬过滤解释、档案 UI 和每本一次的重现预算。",
        dependencies=("character_timeline", "memory_foundation"),
        acceptance=("不可能出现的 NPC 被排除，合理候选在当前模组最多自然触发一次。",),
    ),
    Capability(
        id="memory_workspace",
        label="主要/支线事件记忆工作台",
        status="partial",
        phase="F3",
        audience="all",
        summary="已有后端记忆类型与检索；缺少可解释时间线、人工修正、证据链和团后摘要 UI。",
        dependencies=("memory_foundation", "character_timeline"),
        acceptance=("玩家可查看自己的主要/支线事件及其原始事件来源。",),
    ),
    Capability(
        id="semantic_memory_search",
        label="本地语义记忆检索",
        status="planned",
        phase="F3",
        audience="all",
        summary="在现有词法检索之外增加可选的本地 FTS/向量索引、重建和可解释召回。",
        dependencies=("memory_foundation", "memory_workspace"),
        acceptance=("关闭模型并重启后索引可重建，召回结果显示来源且继续遵守角色可见性。",),
    ),
    Capability(
        id="map_asset_revisions",
        label="地图图片资产与版本缓存",
        status="planned",
        phase="F4",
        audience="kp",
        summary="把结构化地图、渲染文件、生成参数和 revision 分离，用内容哈希持久复用。",
        dependencies=("map_workspace",),
        acceptance=("关闭模型并重启后仍能打开原图，编辑产生新 revision 而不覆盖旧版。",),
    ),
    Capability(
        id="map_reveal_editor",
        label="地图揭示与路线编辑",
        status="planned",
        phase="F4",
        audience="kp",
        summary="让 KP 编辑地点、路线和雾区，并以显式操作逐步向玩家揭示。",
        dependencies=("map_workspace",),
        acceptance=("揭示前玩家无法从列表、SVG、移动历史或实时事件推断隐藏地点。",),
    ),
    Capability(
        id="image_map_generation",
        label="AI 图片地图生成",
        status="planned",
        phase="F4",
        audience="kp",
        summary="接入可替换的本地图像生成器，同时保留结构化地点、路线和可见性层。",
        dependencies=("map_asset_revisions", "model_adapter"),
        acceptance=("生成失败不破坏旧地图，背景图与棋子状态彼此独立。",),
    ),
    Capability(
        id="model_management",
        label="本地模型发现与配置 UI",
        status="planned",
        phase="F4",
        audience="kp",
        summary="在页面中检测 /v1/models、选择真实 model ID、测试连接并保存本地配置。",
        dependencies=("model_adapter",),
        acceptance=("不猜测模型文件名，只使用提供者实际暴露的 model ID。",),
    ),
    Capability(
        id="campaign_backup_restore",
        label="本地备份、导出与恢复",
        status="planned",
        phase="F4",
        audience="kp",
        summary="打包 SQLite、地图资产、模组来源和版本信息，支持校验、恢复与迁移到另一台本地设备。",
        dependencies=("session_roles", "map_workspace", "memory_foundation"),
        acceptance=("从备份恢复后地图、角色、NPC、时间线和审批审计一致，损坏备份不会覆盖现有数据。",),
    ),
    Capability(
        id="human_kp_modes",
        label="完整人类 KP 接管模式",
        status="partial",
        phase="F5",
        audience="kp",
        summary="已有草稿批准/拒绝；缺少 AI 暂停、人类完全接管、暗骰、揭示与交还控制。",
        dependencies=("proposal_approval", "check_resolution"),
        acceptance=("人类 KP 接管后 AI 停止推进，交还时 AI 从已确认事实继续。",),
    ),
    Capability(
        id="voice_companion",
        label="本地语音伴侣",
        status="planned",
        phase="F6",
        audience="all",
        summary="支持按键说话、本地 STT、说话人归属、文本确认和可选 TTS。",
        dependencies=("session_roles", "realtime_sync", "check_resolution"),
        acceptance=("中间转写不触发 AI，只有玩家确认的最终文本会创建一次行动。",),
    ),
    Capability(
        id="webrtc_rooms",
        label="平台内语音房间",
        status="planned",
        phase="F6+",
        audience="all",
        summary="在语音伴侣稳定后增加 WebRTC 房间；游戏 WebSocket 只承担信令和状态。",
        dependencies=("voice_companion",),
        acceptance=("通话断开不损坏团状态，重连不重复创建玩家行动。",),
    ),
)


def validate_capabilities(capabilities: tuple[Capability, ...] = CAPABILITIES) -> None:
    ids = [capability.id for capability in capabilities]
    if len(ids) != len(set(ids)):
        raise ValueError("Capability IDs must be unique")
    known_ids = set(ids)
    for capability in capabilities:
        missing = set(capability.dependencies) - known_ids
        if missing:
            raise ValueError(
                f"Capability {capability.id} has unknown dependencies: {sorted(missing)}"
            )
        if capability.id in capability.dependencies:
            raise ValueError(f"Capability {capability.id} cannot depend on itself")
        if not capability.acceptance:
            raise ValueError(f"Capability {capability.id} needs an acceptance criterion")

    dependency_map = {
        capability.id: capability.dependencies for capability in capabilities
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(capability_id: str) -> None:
        if capability_id in visiting:
            raise ValueError(f"Capability dependency cycle includes {capability_id}")
        if capability_id in visited:
            return
        visiting.add(capability_id)
        for dependency_id in dependency_map[capability_id]:
            visit(dependency_id)
        visiting.remove(capability_id)
        visited.add(capability_id)

    for capability_id in ids:
        visit(capability_id)


def list_capabilities(*, include_available: bool = True) -> list[dict]:
    return [
        capability.to_dict()
        for capability in CAPABILITIES
        if include_available or capability.status != "available"
    ]


validate_capabilities()
