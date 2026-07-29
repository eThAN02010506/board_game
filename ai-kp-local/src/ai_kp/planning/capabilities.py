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
        summary="MapSpec 结构地图、分层 SVG、发布边界、棋子位置和移动版本已持久化。",
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
        id="world_fact_ledger",
        label="世界事实账本",
        status="partial",
        phase="F2",
        audience="all",
        summary=(
            "已用权威事件流实现 canonical fact、KP secret、角色认知、传闻与 AI 假设的"
            "严格分型、当前 head 投影、追加式纠错和 AI 上下文注入；"
            "尚缺事实工作台 UI 与 AI 草稿的 proposed_facts 审批接入。"
        ),
        dependencies=("memory_foundation", "proposal_approval"),
        acceptance=(
            "事实纠错只追加新事件，不覆盖历史；过期 head 不能形成并发分叉。",
            "玩家只能看到桌面事实和本人角色认知，KP secret 与 AI hypothesis 不得泄漏。",
            "AI 上下文明确区分已确认事实、角色认知、传闻和待验证假设。",
        ),
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
        label="玩家调查员创建与角色卡",
        status="available",
        phase="F2",
        audience="all",
        summary=(
            "玩家长期档案、独立调查员页面、完整手工建卡、安全 Excel 预览和不可变草稿版本已可用；"
            "按团提交、KP 退回/批准、字段差异、已批准版本绑定和团内运行状态均已可用；"
            "AI 上下文只读取该团批准的不可变版本与当前状态，不读取玩家后来尚未批准的编辑。"
        ),
        dependencies=("session_roles", "seat_invitations"),
        acceptance=(
            "玩家可在独立页面创建和编辑自己的调查员，KP 可查看，其他玩家只看到公开摘要。",
            "玩家可导入已识别版本的 Excel 角色卡，并在保存前查看字段映射、缺失项和校验警告。",
            "导入过程不执行工作簿公式或宏，并保留原文件哈希、模板版本和导入审计记录。",
            "Excel 只提供玩家输入；属性半值、生命值、魔法值、移动力、伤害加值和技能结果由确定性规则服务重新计算。",
            "角色卡按草稿、待审核、退回修改、已批准流转；KP 退回时必须填写玩家可见的修改意见。",
            "每个团的 KP 独立审核准确的角色卡版本；玩家修改已提交内容后必须重新提交，未批准版本不得进入该团或提供给 AI。",
            "同一玩家可把同一调查员用于不同团次，而无需重新创建或重复导入角色卡。",
            "已批准版本不可变；团内 HP、SAN、MP、临时状态和物品变化存入独立的团内运行状态。",
        ),
    ),
    Capability(
        id="seat_invitations",
        label="逐席邀请与稳定玩家身份",
        status="available",
        phase="F1",
        audience="all",
        summary=(
            "KP 可创建单次、可单独撤销、可预留角色的席位邀请；"
            "玩家使用本地长期身份跨团次认领和恢复自己的席位。"
        ),
        dependencies=("session_roles",),
        acceptance=(
            "撤销某一席位不会影响其他玩家，旧邀请不能被再次使用。",
            "玩家身份可跨团次恢复；撤销某次团的席位不会删除该玩家拥有的调查员。",
        ),
    ),
    Capability(
        id="module_library",
        label="KP 本库与模组阅读",
        status="partial",
        phase="F2",
        audience="kp",
        summary=(
            "内部纯文本剧透边界继续保留；独立 KP 本页面现可导入 PDF/DOC/DOCX、"
            "查看持久任务、章节正文和私密原图，并可做权限过滤检索、章节剧透编辑和"
            "带逐字证据的知识候选审核。已支持来源约束实体图谱、确定性锚点可达性、"
            "每团一个显式活动模组及场景/剧透/运行态上下文；仍缺自动结构校订与"
            "完整的受约束动态补全工作流。"
        ),
        dependencies=("proposal_approval",),
        acceptance=(
            "KP 可导入、预览、标记剧透范围，玩家端不可读取未揭示章节。",
            "同一团只有一个活动模组；重复开始不会清空进度，场景、剧透标签和运行态重启后仍进入 AI 上下文。",
            "两个 KP 客户端用版本号检测并发修改；旧版本不能覆盖新进度。",
            "知识候选、实体和关系不能把来源的可见性或剧透范围降级。",
        ),
    ),
    Capability(
        id="module_document_import",
        label="PDF/Word KP 本导入",
        status="partial",
        phase="F2",
        audience="kp",
        summary=(
            "已只接收 PDF/DOC/DOCX，并安全抽取正文、表格、照片等内嵌原图；旧 DOC "
            "仅在隔离子进程中经 LibreOffice 转换。链路保留页码/段落锚点、"
            "SQLite 持久队列、领取代次 CAS、崩溃恢复、失败重试和完整备份；现支持可选本地 "
            "Tesseract OCR 与 OpenAI-compatible 视觉摘要，中文 OCR 仍取决于本机语言数据。"
        ),
        dependencies=("module_library",),
        acceptance=(
            "上传入口拒绝 PDF/Word 以外的文件，并给出明确错误。",
            "每个文本块和图片都能追溯到原文件页码、段落或锚点。",
            "没有视觉模型时仍保存原图并标记待解析，不阻断整本导入。",
            "图片及其 OCR/视觉摘要继承所在章节的秘密级别，不会进入玩家视图。",
        ),
    ),
    Capability(
        id="world_expansion",
        label="受约束世界补全与动态支线",
        status="partial",
        phase="F3",
        audience="kp",
        summary=(
            "AI KP 已收到 Canon、剧情锚点、运行事实和生成候选的权威顺序约束，"
            "且模组候选必须通过逐字来源校验与 KP 审批；现已支持带来源的窄类型实体关系、"
            "显式冲突报告和确定性锚点可达性检查，尚缺合理性评分、动态桥接状态和"
            "三种自动化模式。"
        ),
        dependencies=(
            "module_library",
            "world_fact_ledger",
            "proposal_approval",
        ),
        acceptance=(
            "模组 Canon 不可被 AI 改写，剧情锚点保持可达但不强制固定场景或路线。",
            "AI 可按时代、地域、聚落规模和现有事实补全合理地点、NPC 与反应性支线。",
            "补全候选与模组真相、剧情锚点或已确认事实冲突时必须阻止并说明原因。",
            "生成内容在批准或玩家实际确认前不能升级为世界事实。",
            "保守、平衡和 AI KP 模式只改变审批强度，不改变事实与剧透边界。",
        ),
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
        status="partial",
        phase="F1",
        audience="all",
        summary=(
            "已按本地 CoC7 规则来源实现普通/困难/极难检定、数字骰、实体骰、"
            "奖惩骰、暗骰、孤注一掷、KP 覆盖、重放审计及检定结果驱动的"
            "指纹化 AI 二阶段草稿；已有纯对抗比较器，尚缺其持久化、API 与 UI。"
        ),
        dependencies=("proposal_approval", "character_sheets"),
        acceptance=(
            "掷骰前不得写入只有成功时才成立的事件或记忆。",
            "每次判定保存规则集版本、章节页码、原始骰值、难度、奖惩骰、结果和角色状态版本。",
            "KP 覆盖必须记录理由；规则判定在关闭模型并重启后仍可重放得到相同结果。",
            "暗骰后果不得进入公开叙述；过期结果草稿不得产生任何部分世界写入。",
        ),
    ),
    Capability(
        id="ruleset_plugins",
        label="规则系统插件",
        status="partial",
        phase="F3",
        audience="all",
        summary=(
            "已有显式规则注册表和 CoC7 应用端口，并具备可追溯 PDF 原文块、MiniRAG 本地召回、"
            "JSON 规则候选、来源/引用/冲突校验、KP golden case 审核和封闭 DSL 执行器；"
            "AI 置信度不能自行发布规则，历史缺少审核证据的规则会 fail-closed。当前仅注册 CoC7，"
            "尚未完成全部 CoC7 规则对象或任何第二系统插件。"
        ),
        dependencies=("check_resolution",),
        acceptance=(
            "切换规则系统不会改写既有事件，检定结果可由对应插件重放验证。",
            "核心规则、书中可选规则、团规、临场 KP 裁定和平台策略使用不同来源标签。",
            "AI 不能绕过规则插件直接提交依赖检定的伤害、理智、成长或其它状态变化。",
        ),
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
        dependencies=("memory_foundation", "world_fact_ledger", "character_timeline"),
        acceptance=("玩家可查看自己的主要/支线事件及其原始事件来源。",),
    ),
    Capability(
        id="semantic_memory_search",
        label="本地语义记忆检索",
        status="partial",
        phase="F3",
        audience="all",
        summary=(
            "已有同步更新且可重建的 SQLite FTS5 trigram 候选层，并在召回不足时回退到"
            "中文二元词检索；尚缺可选本地向量、reranker、召回解释 UI 和固定评测集。"
        ),
        dependencies=("memory_foundation", "memory_workspace"),
        acceptance=("关闭模型并重启后索引可重建，召回结果显示来源且继续遵守角色可见性。",),
    ),
    Capability(
        id="map_asset_revisions",
        label="地图图片资产与版本缓存",
        status="partial",
        phase="F4",
        audience="kp",
        summary=(
            "MapSpec revision、规范哈希、布局哈希、图片生成参数与内容寻址文件已经分离并可重启恢复；"
            "尚缺 KP 结构编辑产生后续 revision 的工作流。"
        ),
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
        status="partial",
        phase="F4",
        audience="kp",
        summary=(
            "已实现可持久化的独立 OpenAI-compatible 图片配置、玩家安全提示词投影、"
            "候选缓存、KP 选用与发布；尚缺 ComfyUI/ControlNet 适配器和真实模型审美验收。"
        ),
        dependencies=("map_asset_revisions", "model_adapter"),
        acceptance=(
            "图片 API Key 不回传前端，配置在后端重启后恢复。",
            "生成失败不破坏旧地图，背景图与棋子状态彼此独立。",
        ),
    ),
    Capability(
        id="model_management",
        label="本地模型发现与配置 UI",
        status="available",
        phase="F4",
        audience="kp",
        summary=(
            "在独立页面中检测 /v1/models、选择真实 model ID、保存运行时配置，"
            "或提交本机 MLX 模型目录并管理本地服务进程。"
        ),
        dependencies=("model_adapter",),
        acceptance=(
            "远程模式不猜测模型文件名，只使用提供者实际暴露的 model ID。",
            "本地目录经过结构校验，启动参数不经过 Shell，服务只监听 127.0.0.1。",
            "API Key 不回传前端，配置在重启后恢复并立即用于新的 AI 请求。",
        ),
    ),
    Capability(
        id="model_quantization_profiles",
        label="4/8 位模型量化方案",
        status="planned",
        phase="F4",
        audience="kp",
        summary=(
            "保留 4 位、8 位与混合 4/8 位本地模型方案；当前只保存基线模型，"
            "不实现运行时转换、微调或热切换。"
        ),
        dependencies=("model_management",),
        acceptance=(
            "使用同一套真实 AI KP 样例对 4 位、8 位和混合量化进行质量、内存、首字延迟和生成速度对比。",
            "量化产物与配置记录基座模型、精度、工具版本和内容哈希，且不进入 Git。",
            "切换模型不改写团会话、记忆、规则对象或地图资产。",
        ),
    ),
    Capability(
        id="campaign_backup_restore",
        label="本地备份、导出与恢复",
        status="available",
        phase="F4",
        audience="kp",
        summary=(
            "可在线快照 SQLite 并打包地图资产、规则索引、版本与逐文件 SHA-256；"
            "支持管理员下载/校验和停服后的安全恢复。"
        ),
        dependencies=("session_roles", "map_workspace", "memory_foundation"),
        acceptance=(
            "从备份恢复后地图、角色、NPC、时间线和审批审计一致，损坏备份不会覆盖现有数据。",
            "恢复必须先通过 ZIP 路径、文件数量、总大小、哈希、schema 版本与 SQLite 完整性校验。",
        ),
    ),
    Capability(
        id="human_kp_modes",
        label="完整人类 KP 接管模式",
        status="partial",
        phase="F5",
        audience="kp",
        summary="已有草稿批准/拒绝与暗骰裁定；缺少 AI 暂停、人类完全接管、分层揭示与交还控制。",
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


def _validate_capability_metadata(
    capabilities: tuple[Capability, ...],
    known_ids: set[str],
) -> None:
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


def _validate_dependency_cycles(
    dependency_map: dict[str, tuple[str, ...]],
) -> None:
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

    for capability_id in dependency_map:
        visit(capability_id)


def validate_capabilities(capabilities: tuple[Capability, ...] = CAPABILITIES) -> None:
    ids = [capability.id for capability in capabilities]
    if len(ids) != len(set(ids)):
        raise ValueError("Capability IDs must be unique")
    known_ids = set(ids)
    _validate_capability_metadata(capabilities, known_ids)
    _validate_dependency_cycles(
        {capability.id: capability.dependencies for capability in capabilities}
    )


def list_capabilities(*, include_available: bool = True) -> list[dict]:
    return [
        capability.to_dict()
        for capability in CAPABILITIES
        if include_available or capability.status != "available"
    ]


validate_capabilities()
