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
    requirement_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="session_roles",
        label="团会话与权限",
        status="available",
        phase="MVP",
        audience="all",
        summary="KP、玩家与只读 Observer 加入、角色绑定、凭证撤销与会话关闭已可用。",
        acceptance=("KP、玩家和 Observer 只能访问各自授权资源。",),
    ),
    Capability(
        id="session_zero_safety",
        label="Session 0 与桌面安全约定",
        status="available",
        phase="R1",
        audience="all",
        summary=(
            "已提供可版本化的 Campaign 设置、公开/私密风格、Lines/Veils、"
            "全桌逐成员确认和无需解释的即时安全工具。产品建团默认在正式行动前"
            "强制完成最低协议；AI 仅接收匿名聚合边界，私密原文与成员身份留在 SQLite。"
        ),
        dependencies=("session_roles",),
        acceptance=(
            "建团后可记录世界观、规则、团风格、House Rules 与玩家预期。",
            "玩家可以私密提交 Lines/Veils 并在游戏中一键触发安全暂停，无需公开解释。",
            "约定变更有确认与审计，AI/KP 生成内容前必须读取安全投影。",
        ),
        requirement_ids=("FR-16",),
        evidence_refs=(
            "src/ai_kp/infrastructure/database/session_zero.py",
            "src/ai_kp/director/session_safety_policy.py",
            "tests/test_session_zero.py",
            "apps/web/e2e/session-zero-safety.e2e.ts",
        ),
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
        id="observer_access",
        label="旁观者与后加入角色",
        status="available",
        phase="R1",
        audience="all",
        summary=(
            "已有独立 Observer 身份、只读工作台、公开投影和实时事件白名单；"
            "Observer 可通过正常席位流程建立新的玩家身份，私密消息从加入边界开始。"
            "Session Continuity 会向新席位投影当前公开事件、任务、已遇 NPC 和"
            "关键物品，不会暴露加入前私信、KP 事件、未遇 NPC 或隐藏物品属性。"
        ),
        dependencies=("session_roles", "realtime_sync"),
        acceptance=(
            "旁观者无需绑定角色，只能读取公开/全桌内容且不能提交行动。",
            "旁观者后加入时通过正常席位邀请、建卡和审批流转，不获得历史秘密。",
        ),
        requirement_ids=("FR-17",),
        evidence_refs=(
            "src/ai_kp/application/table_message_service.py",
            "tests/test_table_messages.py",
            "apps/web/e2e/observer-communications.e2e.ts",
            "tests/test_session_continuity.py",
            "apps/web/e2e/session-continuity.e2e.ts",
        ),
    ),
    Capability(
        id="table_communications",
        label="桌面消息与私密通信",
        status="available",
        phase="R1",
        audience="all",
        summary=(
            "不可变消息账本已提供全桌、Party、KP 公告、KP↔成员私信与可配置的"
            "玩家私信；列表、实时通知和 Observer UI 使用同一服务端可见性边界。"
        ),
        dependencies=("session_roles", "realtime_sync"),
        acceptance=(
            "全桌、KP 公告和私信使用显式可见性，重连后按权限恢复且不重复。",
            "私信、暗骰和 KP 秘密不得经通知摘要、未读计数或旁观者投影泄露。",
        ),
        requirement_ids=("FR-17",),
        evidence_refs=(
            "src/ai_kp/infrastructure/database/table_messages.py",
            "tests/test_table_messages.py",
            "apps/web/src/features/communications/TableCommunicationPanel.tsx",
            "apps/web/e2e/observer-communications.e2e.ts",
        ),
    ),
    Capability(
        id="proposal_approval",
        label="AI 草稿与人类 KP 审批",
        status="available",
        phase="MVP",
        audience="kp",
        summary="结构化草稿、上下文快照、批准、拒绝和原子落库已可用。",
        acceptance=(
            "草稿未批准前不改变正式世界状态。",
            "AI KP 的直接结算、技能检定和 RP/澄清裁定在玩家确认前不执行。",
        ),
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
            "独立事实工作台与 AI/人工 proposed_facts 已接入同一原子审批事务，玩家响应"
            "使用不含 KP 来源元数据的安全投影；尚待真人团验证工作台负担与默认筛选。"
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
        summary=(
            "OpenAI-compatible 模型发现、持久配置与结构化 KP 回合已验证；常驻 Auto KP、"
            "模组知识和契约 worker 每次领取任务读取版本化配置，切换时拒绝旧世代落库，"
            "契约分区不会跨模型世代混合。"
        ),
        acceptance=(
            "用提供者返回的真实 model ID 完成一次草稿并通过校验。",
            "模型切换后所有新 Agent 任务使用新配置，旧世代结果不能成为权威状态。",
        ),
        evidence_refs=(
            "src/ai_kp/infrastructure/llm/model_execution.py",
            "src/ai_kp/infrastructure/scenario_contract_worker.py",
            "tests/test_model_execution.py",
            "tests/test_scenario_contract_jobs.py",
        ),
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
            "每团一个显式活动模组及场景/剧透/运行态上下文。KP 已可生成或人工编译"
            "ScenarioContract、查看问题、发布版本并绑定运行；来源指纹变化和截断窗口"
            "均会拒绝自动发布。已用分区 Scenario IR 替代单次整契约生成，并持久化"
            "每个分区结果；后台任务可从最后完成分区恢复、重试覆盖率补写和"
            "独立语义审核。服务器负责来源绑定、路径归一化、去重、引用修复和安全"
            "降级；所有发布都要求来源、可玩性和因果分支通过，证据约束的"
            "AI 自动发布还额外要求来源覆盖率和独立审核通过。"
        ),
        dependencies=("proposal_approval",),
        acceptance=(
            "KP 可导入、预览、标记剧透范围，玩家端不可读取未揭示章节。",
            "同一团只有一个活动模组；重复开始不会清空进度，场景、剧透标签和运行态重启后仍进入 AI 上下文。",
            "两个 KP 客户端用版本号检测并发修改；旧版本不能覆盖新进度。",
            "知识候选、实体和关系不能把来源的可见性或剧透范围降级。",
        ),
        requirement_ids=("FR-05",),
        evidence_refs=(
            "src/ai_kp/infrastructure/scenario_contract_worker.py",
            "src/ai_kp/application/scenario_contract_service.py",
            "tests/test_scenario_contract_jobs.py",
            "tests/test_scenario_contract_service.py",
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
        id="scene_director",
        label="场景导演与调查推进",
        status="partial",
        phase="F2",
        audience="kp",
        summary=(
            "已实现活动模组的显式场景转换、自由/结构化/休整节奏、来源图实体的"
            "逐团发现状态、追加式审计和不写状态的玩家意图分析；已加入持久安全暂停、"
            "人类 KP 接管/交还、三档自动化强度、自动 KP 后台任务状态机、玩家资料揭示"
            "及指纹化检定后果回接。已有契约驱动的通用世界后果信号，按全桌/"
            "仅 KP 可见性与精确/阶段/叙事精度投影已提交状态，玩家投影不包含秘密"
            "路径或真实状态版本。大型操作目录会先按多语言意图提示、中文字符组和"
            "当前前置状态确定性排名，再向小/大模型提供有界目录；排序不获得结算权。"
            "桌面语义协议对可见世界提问、结果宣称和明确交涉使用窄确定性护栏；两次"
            "坏结构后只恢复显式有序计划，其余安全澄清，且该路由不获得状态权限。"
            "导演页已接入契约生成、编译报告、审核发布和运行绑定，AI KP 仅对"
            "证据完整的安全类别自动发布。"
            "独立席位、弱模型失败、实时同步、后台行动与"
            "检定后果已经浏览器 replay；尚待多人真人桌面负载下调优。"
        ),
        dependencies=(
            "module_library",
            "proposal_approval",
            "check_resolution",
        ),
        acceptance=(
            "场景与线索状态通过版本号防止两个 KP 客户端静默覆盖。",
            "只读分析只能使用当前活动模组与已解锁剧透，并明确报告零写入。",
            "玩家偏离模组路线时先返回已有答案、剧透阻断或世界缺口，不直接创造事实。",
            "重启后当前场景、节奏、线索状态和完整变更审计保持一致。",
            "通用后果信号能表达时钟与非时钟压力；玩家只看到契约公开阶段，KP 保留权威诊断。",
            "至少 40 个操作时，小模型仍能在 12 项有界目录中获得相关中文行动候选。",
        ),
    ),
    Capability(
        id="world_expansion",
        label="受约束世界补全与动态支线",
        status="partial",
        phase="F3",
        audience="kp",
        summary=(
            "已支持只读世界缺口分类，以及绑定活动模组版本、来源哈希、World Fact "
            "head 哈希和上下文快照的 AI 补全草稿；草稿包含理由、假设、冲突与替代方案，"
            "并复用现有 KP 审批流。已批准候选可在玩家实际接触后，以幂等原子事务落地"
            "严格事实、跨团 NPC 身份与已审核地点上的地图棋子。跨团 NPC 已有确定性"
            "图可达性保护和追加式进度审计；平衡/AI KP 可对低副作用环境候选自动"
            "生成 materialize 方案；玩家行动中的当前 world gap 会自动进入该链路，"
            "但必须由玩家确认后才原子落地并解决行动；低置信、冲突和支线候选改为"
            "要求玩家补充或修改行动，不会在弱模型降级时默认放行。"
            "模板实体已有按原型维度约束的当前状态和审计历史；真人 KP 与获批 AI "
            "候选共用版本化提交服务。已审核合同的显式状态命令现已通过单人/并行共享"
            "事务写入实体账本，并以规则来源收据保护重试；模型编制已有按来源分区的实体/维度"
            "目录、目录指纹与选择校验，但条件回读及模型选择可靠性仍待完成。"
            "状态效果可通过精简 IR 记录由服务器生成命令，结果分支和预算经过校验。"
            "规则实体可显式绑定已确认的模组图实体；KP 可通过选择表单创建身份关联草稿，"
            "当前运行按来源 ID 解析实际世界实体，同名对象不会自动合并。"
        ),
        dependencies=(
            "scene_director",
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
        requirement_ids=("FR-06",),
        evidence_refs=(
            "src/ai_kp/application/scenario_effect_service.py",
            "src/ai_kp/application/world_entity_state_service.py",
            "tests/test_scenario_world_effects.py",
            "tests/test_authoring_entity_catalog.py",
            "tests/test_world_state_effect_ir.py",
            "tests/test_world_entity_states.py",
            "docs/WORLD_ENTITY_STATE_EFFECTS.md",
        ),
    ),
    Capability(
        id="party_route_planning",
        label="分队路线规划",
        status="available",
        phase="F2",
        audience="all",
        summary="玩家可提交本人路线，KP 可批准多棋子分队方案；实际移动逐段核销且不互相覆盖。",
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
            "指纹化 AI 二阶段草稿；对抗检定已持久化双方结果并接入 API/UI 与后果快照。"
            "新投骰同时保存规则无关的 dice-roll.v1 骰面证据和完整性指纹，CoC7 仍单独"
            "解释百分骰候选与成功等级；旧结果可迁移并保持原投骰投影。"
        ),
        dependencies=("proposal_approval", "character_sheets"),
        acceptance=(
            "掷骰前不得写入只有成功时才成立的事件或记忆。",
            "每次判定保存规则集版本、章节页码、原始骰值、难度、奖惩骰、结果和角色状态版本。",
            "KP 覆盖必须记录理由；规则判定在关闭模型并重启后仍可重放得到相同结果。",
            "暗骰的技能、目标值、骰面和成功等级不得进入公开叙述；契约授权且角色可观察的线索、效果与已接受代价必须交付。",
        ),
    ),
    Capability(
        id="parallel_action_resolution",
        label="多人并行行动统一结算",
        status="partial",
        phase="R0",
        audience="all",
        summary=(
            "已将同一收集窗的多玩家机械行动绑定到一个可恢复批次："
            "每个玩家只看本人裁定并独立确认方法，各自完成可见检定或由"
            "Full AI 恢复式处理不可见检定，最后在同一 state version 上检查"
            "冲突并原子提交。对话、信息询问、澄清或世界补全等不能进入"
            "确定性并行内核的混合行动，会在零部分写入后通用回退为各自的"
            "三态裁定，不会卡住无 KP 团。修改行动后会按原参与者精确集合进入"
            "持久化重新集结，逐人重提并重新同意；Full AI 遇到 run/state 漂移时"
            "只在无已知骰果时审计式退休旧批并清除旧同意。真实 gpt-oss-20b 已"
            "连续三轮完成 4 人通用机械回合；尚未完成4人长 Session 和更多规则系统验证。"
        ),
        dependencies=(
            "session_roles",
            "realtime_sync",
            "proposal_approval",
            "check_resolution",
        ),
        acceptance=(
            "每个行动所有者只能确认或修改本人方法；Full AI 不能代替玩家选技能、推动或永久代价。",
            "批次固定团、Session、模组运行版本、场景状态和每个 preview 指纹；任一漂移或冲突必须整批失败关闭。",
            "可见检定、暗骰和推动链全部终态后才允许一次原子提交；崩溃重试不重骰、不重复执行代价。",
            "玩家投影只包含本人裁定与有权检定，不含他人行动、暗骰、算子、命令、结果指纹或 KP 诊断。",
            "非机械混合批次只能在未写世界状态时拆分回已有单行动流程，不得部分提交或重新进入同一收集循环。",
            "只有行动所有者明确请求 Auto KP 的在途行动可以合批；真人 KP 行动不得被另一玩家的自动化选择接管。",
            "弱模型或上游失败必须降级成零世界写入的单行动澄清，不能把父批次或拆分后的子行动留在重试死锁。",
            "修改后的原参与者必须通过持久化屏障逐人重提；刷新、重启和任意提交间隔不能丢失成员集合或旧同意处置。",
            "run/state 漂移不得沿用旧 preview 或旧玩家同意；有已知骰果时不得自动放弃以规避结果。",
        ),
        requirement_ids=("FR-09",),
        evidence_refs=(
            "src/ai_kp/application/parallel_action_workflow_service.py",
            "src/ai_kp/application/parallel_action_regather_service.py",
            "src/ai_kp/infrastructure/auto_kp_worker.py",
            "src/ai_kp/infrastructure/auto_kp_parallel_recovery.py",
            "src/ai_kp/infrastructure/database/parallel_action_regathers.py",
            "tests/test_parallel_action_api.py",
            "tests/test_auto_kp_job_authority.py",
            "tests/test_parallel_action_workflow_service.py",
            "apps/web/e2e/parallel-full-ai.e2e.ts",
            "scripts/parallel_small_model_realcase.py",
        ),
    ),
    Capability(
        id="ruleset_plugins",
        label="规则系统插件",
        status="partial",
        phase="F3",
        audience="all",
        summary=(
            "已有 v1 Manifest、支持等级、能力/UI/地图模式声明、团级规则/角色/事件版本"
            "钉住、显式规则注册表和 CoC7 应用端口，并具备可追溯 PDF 原文块、MiniRAG 本地召回、"
            "JSON 规则候选、来源/引用/冲突校验、KP golden case 审核和封闭 DSL 执行器；"
            "行动理解、NPC 表演、场景导演、世界补全和输出安全复核已作为"
            "打包、哈希、版本化的 proposal-only AI Skill 组合进入单次模型调用；"
            "检定后果与团后总结也保留显式 Skill 契约。规则书"
            "可提取术语、资源、状态、行动经济、成长与 reference-only Skill 指南候选。"
            "AI 置信度不能自行发布规则，历史缺少审核证据的规则会 fail-closed。当前仅注册 CoC7，"
            "尚未完成全部 CoC7 规则对象或任何第二系统插件。"
        ),
        dependencies=("check_resolution",),
        acceptance=(
            "切换规则系统不会改写既有事件，检定结果可由对应插件重放验证。",
            "核心规则、书中可选规则、团规、临场 KP 裁定和平台策略使用不同来源标签。",
            "AI 不能绕过规则插件直接提交依赖检定的伤害、理智、成长或其它状态变化。",
            "上传规则书和提取 Skill 指南不会自动出现在已安装规则/Skill 清单。",
        ),
    ),
    Capability(
        id="combat_encounters",
        label="战斗、追逐与回合管理",
        status="available",
        phase="R1",
        audience="all",
        summary=(
            "已有 CoC7 专用的持久战斗/追逐状态机：KP 可创建遭遇、按敏捷排序、"
            "推进轮次，结算近战、射击、战技、伤害和追逐危险，并用版本号和"
            "幂等 command ID 同步调查员生命状态。玩家已有本人回合的预览/修改/确认"
            "工作流，规则外行动由小上下文意图 Agent 只能选择规则目录、提出有限战技或"
            "具体追问；版本化敌方回合 Agent 只能选择规则允许的动作/目标，骰值在状态提交"
            "前单独持久化。Session 0 可配置等待、跳过、防御、代管或暂停及超时，安全工具"
            "会阻断自动回合；失能方会确定性结束战斗，既有 NPC 物品进入权威战利品。"
            "真实玩家浏览器已覆盖使用物品、修改并确认攻击、NPC 失能、拾取战利品和断线重连；"
            "后端以关闭并重开数据库验证随机证据和伤害不会重复。第二规则系统的遭遇适配属于"
            "规则生态扩展目标，不影响当前 CoC7 Must 旅程的完成状态。"
        ),
        dependencies=("ruleset_plugins", "character_sheets", "realtime_sync"),
        acceptance=(
            "发起遭遇、回合顺序、行动资源、反应、移动与状态由当前规则插件解释，不假设所有 TRPG 都是 D&D。",
            "玩家只能提交自己当前可用的行动，系统不会代替玩家选择目标、法术或稀缺资源。",
            "断线、超时、KP override 和规则外行动不损坏遭遇状态，重连后可从同一回合继续。",
            "敌方秘密属性、暗骰和未公开效果不得出现在玩家或旁观者投影。",
        ),
        requirement_ids=("FR-19",),
        evidence_refs=(
            "src/ai_kp/application/gameplay_service.py",
            "src/ai_kp/api/routers/gameplay.py",
            "tests/test_coc7_gameplay_api.py",
            "tests/test_encounter_intent_agent.py",
            "tests/test_encounter_automation.py",
            "src/ai_kp/director/skills/bundles/select-enemy-turn/SKILL.md",
            "apps/web/src/features/gameplay/GameplayWorkbench.test.tsx",
            "apps/web/e2e/player-encounter-turn.e2e.ts",
        ),
    ),
    Capability(
        id="inventory_economy",
        label="物品、经济与战利品循环",
        status="available",
        phase="R2",
        audience="all",
        summary=(
            "已建立统一权威物品、货币、转交、商店和制作账本；角色审批会幂等导入"
            "结构化初始资产，旧文本资金保留为不可猜值的资产记录。拾取、拆分、装备、"
            "消耗、规则物品效果、购买/出售、制作、损坏和丢失均受所有权、版本、来源"
            "与短写事务保护；NPC/地点持有人必须引用当前团权威实体。玩家 UI 可查看、"
            "拾取、装备、使用、转交和购买，隐藏属性只向 KP 或已揭示成员投影；Session "
            "End 同一状态生成角色隔离快照并已验证服务重启恢复。"
        ),
        dependencies=("character_sheets", "ruleset_plugins", "world_fact_ledger"),
        acceptance=(
            "获得、转移、使用、装备、消耗、丢弃和购买使用同一幂等事务账本。",
            "重量、负重、货币和物品效果由规则插件或明示团规解释，AI 不直接改写数值。",
            "诅咒、未鉴定或隐藏属性只对 KP/有权限角色可见，交易和摘要不得泄密。",
            "Session End 和 Continue 能准确恢复当前持有人、数量、已消耗状态与未分配战利品。",
        ),
        requirement_ids=("FR-20",),
        evidence_refs=(
            "src/ai_kp/infrastructure/database/investigators.py",
            "src/ai_kp/application/inventory_service.py",
            "src/ai_kp/application/inventory_item_effect_service.py",
            "src/ai_kp/infrastructure/database/inventory_items.py",
            "src/ai_kp/infrastructure/database/inventory_economy.py",
            "src/ai_kp/api/routers/inventory.py",
            "apps/web/src/features/inventory/InventoryPanel.tsx",
            "tests/test_inventory_api.py",
            "tests/test_session_continuity.py",
        ),
    ),
    Capability(
        id="character_timeline",
        label="跨本角色身份与时间线",
        status="available",
        phase="F2",
        audience="all",
        summary=(
            "已区分稳定调查员身份、不可变角色卡版本、现实团会话、世界时间和团内"
            "临时状态；同一时间线只允许一个进行中团，玩家可显式建立平行分支。"
            "旧团安全记忆、NPC 接触与参团履历作为可重建读取投影提供，KP 可从已落地"
            "事件提出永久变化，玩家接受后才生成带乐观并发保护的里程碑版本。"
        ),
        dependencies=("character_sheets", "memory_foundation"),
        acceptance=(
            "同一角色在本 A/B 的永久经历连续，临时状态不会错误覆盖。",
            "同一分支不得同时参与两个进行中团；平行经历必须由玩家显式建立分支。",
            "跨团投影不暴露旧团 KP/秘密事件、隐藏记忆或永久变化内部裁定依据。",
        ),
    ),
    Capability(
        id="character_lifecycle",
        label="成长、伤病、死亡与角色替换",
        status="available",
        phase="R2",
        audience="all",
        summary=(
            "CoC7 确定性机制已覆盖伤害、重伤、濒死/死亡、急救、医学、"
            "自然恢复与技能成长；成长结果进入玩家确认的永久变化和不可变里程碑。"
            "生命周期状态机把规则伤亡、Observer、已审核备用角色、退役、暂离、"
            "主持方代管和回归分成版本化请求、玩家确认、控制权与追加事件；死亡不会"
            "结束 Campaign。当前 CoC7 未声明复活能力时明确失败关闭。三角色 UI 与"
            "Session End/Continue 冻结快照读取同一权威状态；死亡、失能或离席状态会在"
            "服务端阻止新行动，并由玩家 UI 解释恢复或换角路径。"
            "20 Session 耐久运行现在调用同一生产服务，真实执行两次死亡、"
            "两次玩家确认换角、暂离/回归和中途新成员入团，并把相关账本纳入跨重启指纹。"
        ),
        dependencies=("character_timeline", "combat_encounters"),
        acceptance=(
            "成长、永久伤病、死亡、复活与退团都是可追溯状态迁移，不靠叙事文本暗示。",
            "玩家死亡不会终止 Campaign；可选择旁观、等待复活或提交新角色，重要选择不由 AI 代做。",
            "新角色、新玩家与回归玩家只获得当前允许的 Campaign 回顾、Party 信息和角色知识。",
            "会话跨天恢复后，生命、理智、状态、成长待办和角色归属与上次结束一致。",
        ),
        requirement_ids=("FR-21",),
        evidence_refs=(
            "src/ai_kp/application/gameplay_service.py",
            "src/ai_kp/application/character_timeline_service.py",
            "src/ai_kp/application/character_lifecycle_service.py",
            "src/ai_kp/evaluation/long_campaign_lifecycle.py",
            "src/ai_kp/infrastructure/database/character_lifecycle.py",
            "src/ai_kp/api/routers/character_lifecycle.py",
            "apps/web/src/features/characters/CharacterLifecyclePanel.tsx",
            "tests/test_coc7_gameplay_mechanics.py",
            "tests/test_character_timelines.py",
            "tests/test_character_lifecycle.py",
            "tests/test_long_campaign_durability.py",
            "apps/web/e2e/character-lifecycle.e2e.ts",
        ),
    ),
    Capability(
        id="npc_reappearance",
        label="NPC 档案与跨本再出现",
        status="partial",
        phase="F3",
        audience="kp",
        summary=(
            "已有全局 NPC、Campaign 关系、团内候选评分，以及由稳定调查员接触证据"
            "授权的跨本安全复现；KP 可配置生命周期、活动年代、地点/职业标签和每团"
            "预算。候选会解释通过或待复核原因，最终事务会重新检查并追加出场凭据；"
            "独立 NPC 工作台和团级加权地点图谱已提供档案编辑、策略管理及可解释最短"
            "旅行时间；KP 私密暗骰会在确定性门控后按需解析是否出现及合法地点，"
            "多目的地只计算一次且幂等重试不会重骰。KP 可预览并确认导入已审核"
            "模组地点/路线；模组 NPC 只能按规范化名称匹配到已存在且已关联本团的"
            "全局 NPC，再更新来源可追溯的地点/年代可用性约束。未匹配或未关联 NPC "
            "会显式跳过，不会自动建档或入团；尚缺该手动链接流程与 NPC 时间线视图。"
        ),
        dependencies=("character_timeline", "memory_foundation"),
        acceptance=(
            "不可能出现的 NPC 被排除，合理候选在当前模组最多自然触发一次。",
            "暗骰不能绕过硬门控、写入未接触事实或向玩家暴露私密回执。",
            "模组 NPC 未匹配到已存在且已关联本团的档案时必须跳过，不得隐式创建或关联。",
        ),
        requirement_ids=("FR-11",),
        evidence_refs=(
            "src/ai_kp/application/module_campaign_import_service.py",
            "tests/test_module_campaign_import.py",
        ),
    ),
    Capability(
        id="memory_workspace",
        label="主要/支线事件记忆工作台",
        status="available",
        phase="F3",
        audience="all",
        summary=(
            "已有独立的团内调查员时间线：玩家按已批准角色隔离查看主要/支线、"
            "NPC、线索记忆及可见来源；KP 可筛选、检查证据，并以追加动作改类、"
            "调整重要性、隐藏或恢复，陈旧编辑会冲突且不改写原始历史。"
            "团后摘要会按冻结事件窗口幂等生成带来源草稿，经 KP 逐条审核后才写入"
            "正式记忆；跨 Campaign 角色投影、显式平行分支和永久里程碑版本也已接通。"
        ),
        dependencies=("memory_foundation", "world_fact_ledger", "character_timeline"),
        acceptance=(
            "玩家可查看自己的主要/支线事件及其允许公开的原始事件来源。",
            "KP 校正有追加式审计和并发保护，隐藏记忆不会删除来源事实。",
        ),
    ),
    Capability(
        id="session_continuity",
        label="Session End、公开回顾与下次继续",
        status="available",
        phase="R1",
        audience="all",
        summary=(
            "已有独立 Campaign episode 状态机、固定事件 rowid 窗口、幂等 Session End/"
            "Continue、未完成桌面工作阻塞门、重启恢复和 KP/player/Observer 三份隔离投影。"
            "权威任务账本、已遇 NPC、位置、队伍/角色状态、伤亡、物品与货币均进入同一"
            "冻结快照。Full AI 使用确定性可见性策略自动发布安全 Previously on；"
            "人类 KP 另可对来源绑定的记忆候选逐条编辑、批准或拒绝。"
            "三身份真实浏览器已验证 End→重载→Continue 且秘密不泄露。"
        ),
        dependencies=("session_roles", "memory_workspace", "scene_director"),
        acceptance=(
            "Session End 在同一冻结事件窗口上生成玩家可见摘要和 KP 私密摘要，两者不可交叉泄密。",
            "下次打开可从 Continue Campaign 恢复已确认的世界、角色、Party、任务、NPC、地点和物品状态。",
            "Previously on 只显示该用户有权看到的重要决定、未解决问题和当前可行动项。",
            "回顾生成失败不得阻断会话关闭或下次继续；可从确定性投影生成降级摘要。",
        ),
        requirement_ids=("FR-18",),
        evidence_refs=(
            "src/ai_kp/application/session_continuity_service.py",
            "src/ai_kp/application/campaign_objective_service.py",
            "src/ai_kp/infrastructure/database/session_continuity.py",
            "src/ai_kp/api/routers/continuity.py",
            "src/ai_kp/application/session_recap_service.py",
            "apps/web/src/features/sessions/SessionContinuityPanel.tsx",
            "apps/web/src/features/sessions/CampaignObjectivePanel.tsx",
            "tests/test_session_continuity.py",
            "apps/web/e2e/session-continuity.e2e.ts",
            "tests/test_session_recaps.py",
        ),
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
            "KP 已可验证并保存地点/路线后续 revision，并预览当前与上次发布的差异。"
        ),
        dependencies=("map_workspace",),
        acceptance=("关闭模型并重启后仍能打开原图，编辑产生新 revision 而不覆盖旧版。",),
    ),
    Capability(
        id="map_reveal_editor",
        label="地图揭示与路线编辑",
        status="partial",
        phase="F4",
        audience="kp",
        summary=(
            "已支持地点/路线新 revision、独立自由多边形雾层、比例继承、删除与显式揭开，"
            "以及发布差异预览；尚待小队级揭示和真人桌面验证。"
        ),
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
            "或提交本机 MLX 模型目录并管理本地服务进程。文本模型配置持久单调世代；"
            "所有游戏 Agent 调用在开始时冻结该世代，保存新模型后会拒绝旧模型的"
            "在途返回落库。"
        ),
        dependencies=("model_adapter",),
        acceptance=(
            "远程模式不猜测模型文件名，只使用提供者实际暴露的 model ID。",
            "本地目录经过结构校验，启动参数不经过 Shell，服务只监听 127.0.0.1。",
            "API Key 不回传前端，配置在重启后恢复并立即用于新的 AI 请求。",
            "模型切换不改写 Campaign 权威状态，切换前返回的 Agent 输出不得落库。",
        ),
        evidence_refs=(
            "src/ai_kp/application/ai_control_service.py",
            "tests/test_ai_control_gate.py",
            "tests/test_long_campaign_durability.py",
            "apps/web/src/features/models/ModelSettingsPage.tsx",
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
            "不实现量化产物的运行时转换、微调或自动基准切换。已保留手动"
            "配置切换与旧世代输出失效边界。"
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
        status="available",
        phase="F5",
        audience="kp",
        summary=(
            "已有草稿裁定、持久安全暂停、人类完全接管与显式交还；回合、检定后果、"
            "世界补全和团后摘要共用应用层控制快照，非 AI 控制态拒绝新调用、取消"
            "同进程在途调用，并在落库前重验。接管后继续复用角色、骰子、事实、NPC、"
            "地图、Session 0、物品和连续性的 typed 权威服务；交还时从已提交事实重建"
            "上下文，不恢复旧响应或猜测未记录的口头决定。AI 与真人 KP 的 primitive "
            "选择统一进入来源无关准备器、同一 ActionResolutionKernel、玩家确认和原子"
            "提交链路；计划首步/后续步及并行准备、改技能和持久化重验也不绕行；新手 KP "
            "可使用受约束候选目录与只读、带来源的 Need Help。AI 选择、真人目录与帮助面板"
            "共用冻结的 ScenarioActionCatalog 候选、技能白名单和契约 evidence ID；模组/规则书"
            "原文仍只作为补充上下文，不能升级为执行权限。单行动 proposal 还会冻结"
            "合同与状态权限依据，提交时原子重验并写入回执；规则准备只接收 operator/技能"
            "最小 DTO，不依赖模型 route 或 confidence。单行动与并行提交还共用权限依据验证和"
            "resolved-action 命令投影，同时保留不同的事务范围。"
        ),
        dependencies=("proposal_approval", "check_resolution"),
        acceptance=(
            "人类 KP 接管后 AI 停止推进，交还时 AI 从已确认事实继续。",
            "同一 primitive 选择不因 AI 或真人 KP 来源改变 preview hash、技能权限或效果上限。",
            "task method 与并行行动的每个实际 primitive 都通过同一来源无关准备器。",
            "AI 选择、真人目录和 Need Help 对同一候选使用相同技能白名单与契约证据 ID。",
            "缺少当前权限依据的历史单行动裁决必须重新计算，不能借用新合同或新状态提交。",
            "单行动与并行回执冻结同形权限依据，并产生同形的 resolved-action 审计事件。",
            (
                "Need Help 建议可追溯且不能自动批准提案、执行行动或修改权威状态；开放请求在"
                "重启后追加 request_abandoned 终态，LAN 入口受敏感操作限流。"
            ),
        ),
        requirement_ids=("FR-13A",),
        evidence_refs=(
            "docs/GM_OVERRIDE_AUTHORITY.md",
            "tests/test_ai_control_gate.py",
            "tests/test_module_runs.py",
            "src/ai_kp/platform/resolution/selected_action.py",
            "src/ai_kp/platform/resolution/action_catalog.py",
            "src/ai_kp/platform/resolution/authority_basis.py",
            "tests/test_selected_kernel_action.py",
            "tests/test_scenario_action_catalog.py",
            "src/ai_kp/platform/resolution/settlement.py",
            "tests/test_resolution_settlement.py",
            "tests/test_kernel_action_service.py",
            "tests/test_director_help_audit_service.py",
            "tests/test_director_help_audits.py",
            "tests/test_director_help_history_api.py",
            "tests/test_http_security.py",
            "apps/web/e2e/human-kp-handoff.e2e.ts",
        ),
    ),
    Capability(
        id="player_handouts",
        label="玩家手册与线索揭示",
        status="partial",
        phase="F5",
        audience="all",
        summary=(
            "已实现草稿、揭示、撤回、固定、事实/NPC/地图/地点/模组实体关联和逐席"
            "幂等已读；玩家只接收服务端安全投影，尚待图片附件与真人团呈现验证。"
        ),
        dependencies=("session_roles", "proposal_approval"),
        acceptance=("未揭示资料和其他席位已读信息不能进入玩家响应。",),
    ),
    Capability(
        id="simulated_campaign_evaluation",
        label="可重放模拟团评测",
        status="partial",
        phase="F5",
        audience="kp",
        summary=(
            "除轻量 contract runner 外，产品服务回放会在回滚沙盒中实际调用地图、路线、"
            "提案、线索和 CoC7 遭遇服务并保存轨迹；无 KP 核心流程已覆盖实时浏览器"
            "replay，尚待完整 golden campaign、模型对比和外部依赖故障库。"
        ),
        dependencies=("proposal_approval", "memory_foundation"),
        acceptance=("同一定义和 runner 版本重放得到相同指纹，秘密可见性失败必须告警。",),
    ),
    Capability(
        id="long_campaign_validation",
        label="长期真人团全流程验证",
        status="partial",
        phase="R3",
        audience="all",
        summary=(
            "已有 fail-closed AC-LONG 证据门禁、20 Session/3000 分钟等价状态规模、"
            "20 次进程重启、持久模型世代切换、FTS 重建、权威指纹与 50 条独立"
            "edge-case 回归证据；耐久运行已把多次死亡、换角、暂离/回归与"
            "中途入团改为生产生命周期服务的真实状态迁移，不再只记录里程碑标签。"
            "门禁 v2 还会拒绝 persona/RPS 字符串自报，要求绑定 commit、UI surface、"
            "观察 ID 与证据 SHA-256 的逐项来源；14 类脚本旅程覆盖也必须由玩家 Agent 轨迹"
            "与检定、战斗、物品、生命周期等权威 ID 交叉重建。RPS-01..12 候选生成器已绑定"
            "长团 UI 轨迹与独立模型故障 UI 轨迹；最终装配器还会读取每个引用文件，"
            "验证实际 SHA-256、源码提交一致性和三轮重测证据后才允许发布。"
            "这些仍不是从真实玩家 UI 完成的长期 Campaign：八类 persona、三轮完整重测、"
            "RPS 观察与跨多种通用模组证据尚未闭合，因此不得标记为可用。"
        ),
        dependencies=(
            "simulated_campaign_evaluation",
            "session_continuity",
            "combat_encounters",
            "inventory_economy",
            "character_lifecycle",
        ),
        acceptance=(
            "真实浏览器流程完成建团、4 名角色、Session 0、探索、对话、检定、失败后继续、战斗、战利品、Session End 与 Continue。",
            "10 Session 脚本覆盖重大选择、开放世界偏离、成长、死亡、替代角色、新人入团和玩家离团。",
            "AC-LONG 以 20 Session 或 50 小时测试记录证明世界/角色记忆不丢失、可见性不泄漏、恢复不重复且性能无失控增长。",
            "验收同时记录 KP/玩家双视图轨迹、模型/规则版本、故障分类和可重放种子。",
        ),
        requirement_ids=("NFR-05", "AC-LONG"),
        evidence_refs=(
            "src/ai_kp/evaluation/long_campaign_evidence.py",
            "src/ai_kp/evaluation/long_campaign_experience.py",
            "src/ai_kp/evaluation/long_campaign_release.py",
            "src/ai_kp/evaluation/long_campaign_durability.py",
            "src/ai_kp/evaluation/long_campaign_lifecycle.py",
            "src/ai_kp/evaluation/edge_case_catalog.py",
            "scripts/long_campaign_acceptance.py",
            "scripts/assemble_long_campaign_release.py",
            "scripts/verify_real_long_ui_journey.py",
            "apps/web/e2e/full-ai-long-campaign.realcase.e2e.ts",
            "apps/web/e2e/no-kp-play.e2e.ts",
            "apps/web/e2e/long-campaign-membership.e2e.ts",
            "apps/web/e2e/long-campaign-combat.e2e.ts",
            "apps/web/e2e/long-campaign-inventory.e2e.ts",
            "apps/web/e2e/support/longCampaignCombat.ts",
            "apps/web/e2e/support/longCampaignGm.ts",
            "apps/web/e2e/support/longCampaignGrowth.ts",
            "apps/web/e2e/support/longCampaignInventory.ts",
            "apps/web/e2e/support/longCampaignLifecycle.ts",
            "tests/test_long_campaign_evidence.py",
            "tests/test_long_campaign_experience.py",
            "tests/test_long_campaign_durability.py",
            "tests/test_edge_case_catalog.py",
            "docs/evidence/AC_LONG_FOUNDATION_2026-08-22.md",
            "docs/evidence/AC_LONG_FOUNDATION_V3_2026-08-22.md",
            "docs/evidence/AC_LONG_FOUNDATION_V3_2026-08-22.json",
            "docs/evidence/AC_LONG_FOUNDATION_V4_2026-09-01.md",
            "docs/evidence/AC_LONG_FOUNDATION_V4_2026-09-01.json",
        ),
    ),
    Capability(
        id="operational_safety",
        label="运行监测与安全回归",
        status="partial",
        phase="F5",
        audience="kp",
        summary=(
            "Debug 台已汇总请求延迟、SQLite 写锁等待、检索 recall/MRR/禁入命中与"
            "秘密泄露告警；已有授权矩阵、跨团污染与事务故障注入，尚待 WAL/恢复"
            "基线、WebSocket 侧信道和更多外部依赖故障。"
        ),
        dependencies=("session_roles", "simulated_campaign_evaluation"),
        acceptance=("跨团对象替换不得泄露资源，事务中途失败不得留下部分写入。",),
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
        for field_name, values in (
            ("requirement_ids", capability.requirement_ids),
            ("evidence_refs", capability.evidence_refs),
        ):
            if len(values) != len(set(values)):
                raise ValueError(
                    f"Capability {capability.id} has duplicate {field_name}"
                )
            if any(not value.strip() for value in values):
                raise ValueError(
                    f"Capability {capability.id} has an empty {field_name} entry"
                )


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
