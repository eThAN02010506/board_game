# PRD 实现审计与证据索引

> 文档性质：非规范、时间点快照
> 对照 PRD：[`PRODUCT_REQUIREMENTS.md`](PRODUCT_REQUIREMENTS.md)
> 审计基线：本文件所在提交
> 最后审计：2026-09-08

## 1. 用途与事实边界

本文件回答“主 PRD 的要求已经实现到什么程度、证据在哪里、下一处缺口是什么”。它不定义
产品要求，也不拥有运行时交付状态：

- 唯一规范产品源是 [`PRODUCT_REQUIREMENTS.md`](PRODUCT_REQUIREMENTS.md)；
- 唯一 `available / partial / planned` 状态源是
  [`src/ai_kp/planning/capabilities.py`](../src/ai_kp/planning/capabilities.py)；
- 本文件只是绑定到明确提交的人工审计。代码、状态目录或证据变化后，结论可能过期；
- README、提交说明、测试数量和单次 real-case 都不能自动把能力提升为 `available`。

这里使用以下证据强度：

1. **代码存在**：只能证明有实现入口；
2. **自动测试**：证明指定输入的不变量，不代表真实用户旅程；
3. **产品服务回放**：证明跨服务状态链，但可能绕过 UI 或真实多人交互；
4. **玩家 UI E2E**：证明用户能从界面完成指定路径；
5. **真实多人/长期团**：才可证明 `AC-LONG` 与完整 Real Player Standard。

## 2. 当前总体判断

项目已经拥有很强的确定性基础：本地数据、席位权限、调查员、CoC7 检定与部分玩法状态机、
模组导入、来源绑定契约、地图、事实与记忆、受约束世界补全、多 Agent 候选链、玩家确认、
后台恢复及可玩性发布门禁。`835d3b7` 进一步让来源缺失、无权威失败后果、错误 effect 分支和
语义审核失败关闭，并移除了玩家裁定自动确认；`681b337` 将多人机械行动的真实 API、后台
worker、玩家确认/检定 UI 和原子 Kernel 提交接成可恢复产品链路，但该能力仍保持 `partial`。
本轮又加入不可变多区域 Setting Profile、运行级精确版本/聚落固定、模组地点来源绑定，以及
七类规则集无关实体原型。AI KP 与人类 KP 共享同一不可变 Profile；实体候选的类型、标签、职位、
局部引用、关系目标和基数都在审批前确定性校验。模组已有实体可按来源 ID 绑定到全局或
具体聚落功能，AI 结构化输出显式声明复用项并拒绝未知来源 ID。AI 实际提示使用该不可变
Profile 的意图相关紧凑投影，人类 KP 仍查看完整分析，避免为小模型重复注入无关槽位。
旧的整模组单聚落分析入口已经删除。

但“底层组件很多”不等于“长期 TRPG App 已完成”。独立 UI 回归已覆盖
Session 0、通信、战斗、物品、生命周期与 End/Continue；V4 非 UI 基础也已完成
20 Session/3,000 分钟、50 条 edge cases 和真实权威生命周期迁移。目前仍不能诚实宣称：

- 真实 4 玩家 + Full AI/本地 20B 从 UI 连续完成 10 Session 并覆盖全部必需旅程；
- 八类 persona 和 `RPS-01..12` 都有同一长团中的玩家可观察证据；
- 对真实 UI/模型发现的问题完成三轮“修改 → 完整重测 → 再修改”。

因此 `AC-LONG` **未通过**，`RPS-01..12` 也尚无完整端到端证据。

## 3. FR 实现矩阵

下表状态是本次审计判断；实际 UI 显示必须读取 capability catalogue。

| Requirement | 审计判断 | 已有证据 | 关键缺口 |
|---|---|---|---|
| FR-01 | available/partial 组合 | OpenAI-compatible 模型发现、配置与结构化调用；本机 MLX 管理入口；Auto KP、模组知识和契约后台任务按领取时的持久模型世代执行，切换后旧结果失败关闭且契约派生分区不会跨世代混合 | 量化比较仍 planned；真实 provider 需在目标机器复验 |
| FR-02 | partial | KP/player/Observer 身份、一次性邀请、撤销、WebSocket outbox 与重连测试；V4 耐久运行实际执行中途入团、暂离与跨重启回归 | 长期真实 UI 下的加入/离开体验仍由 AC-LONG 验证 |
| FR-03 | available（当前 CoC7 范围） | 手工建卡、Excel 预览、不可变版本、退回/批准、团内状态与已审核备用角色替换 | 跨规则集角色创建属第二插件扩展；当前 CoC7 Must 已交付 |
| FR-04 | partial | 原文块、MiniRAG、规则候选、CoC7 确定性插件和 golden tests | 规则候选生产线与第二规则系统未验证；部分 CoC7 能力仍需 GM |
| FR-05 | partial | MinerU-first 导入、分区任务、恢复/重试、来源覆盖、语义审核、可玩性与 provenance 门禁；模型世代切换会清除派生分区并从不可变证据重启 | 多种真实模组、弱/强模型和人工输入契约尚无重复发布证明；不能由单本成功升级 |
| FR-06 | partial | 通用 `ActionIntentPlan`、三态可行性、受约束 overlay、压力信号与动态计划；不可变多区域 Setting Profile 将地点绑定到功能槽，运行固定精确版本与当前聚落；人物、组织、物件、文档、交通、事件、线索载体共用带关系基数的实体原型，世界扩展只可选择快照候选；获批候选由 AI/真人 KP 共用物化器原子写入类型化实体和关系账本，并按可见性召回 | 连续多 overlay、不同类型模组和真实多人偏航仍未完整验证；本轮未执行真实 UI 长团 |
| FR-07 | partial | MapSpec、版本、路线、棋子、雾层、发布与权限投影 | Theater of the Mind 自适应 UI、小队级揭示和复杂地图未闭环 |
| FR-08 | partial | CoC7 百分骰、奖惩骰、实体骰、可见性、推动、override、重放 | 通用骰式覆盖与完整规则插件解释尚不足；暗骰/多规则集 real-case 不完整 |
| FR-09 | partial | `681b337` 建立 durable 2–12 人权威批次；本次增量又加入原参与者精确集合的持久化 regather、逐玩家重新同意、刷新/重启恢复，以及 Full AI 在无已知骰果时遇到 run/state 漂移后“退休旧批→清除旧同意→全员重提”的审计流程。玩家/KP 安全投影、恢复 UI、双浏览器 E2E 均已接通；`scripts/parallel_small_model_realcase.py` 以真实 `gpt-oss-20b` 连续三轮完成 4 人候选选择、逐人确认和一次原子提交 | 浏览器 E2E 的模型准备仍使用 deterministic seam；真实 20B 证据目前是一轮通用机械场景的三次重复，不等于完整长 Session；仍缺多类行动/检定/失败的 4 人 UI playthrough 与第二规则系统证明 |
| FR-10 | partial | 事件、事实、记忆工作台、角色时间线、重启恢复 | 长期压缩与 20 Session/50h 召回尚未通过 `AC-LONG` |
| FR-11 | partial | NPC 稳定身份、接触证据、年代/地点门控、暗骰和模组来源约束导入 | 手动链接流程、NPC 时间线和跨多本真人团仍缺 |
| FR-12 | partial | 玩家/KP/Observer 分离工作台、角色/行动/地图组合、Session 0/安全工具、终局卡、玩家遭遇回合、权威物品资产与死亡/换角面板 | Theater of the Mind 无地图自适应 UI 仍未闭环 |
| FR-13A | available | 持久暂停、完全接管、同进程取消与落库前重验覆盖所有游戏导演入口；AI/真人 KP 的 primitive 选择统一进入来源无关准备器和同一 ActionResolutionKernel、玩家确认与原子提交链路；新手 KP 可使用受约束候选目录及只读、带来源、追加审计的 Need Help；Need Help 终态/错误码为闭集，开放 requested 在重启时幂等追加 `failed/request_abandoned`，LAN 入口受敏感操作限流；接管后复用角色、检定、事实/NPC、地图、Session 0、物品和连续性 typed 权威服务，纠正保留原始审计；浏览器完成接管→追加事实→交还，后端证明新模型上下文读取该事实且不恢复旧响应 | 长期真人团对候选目录与 Need Help 操作负担的反馈继续进入 AC-LONG，不降级权威边界；本轮没有执行真实 UI 长团 |
| FR-13B | planned | 外部语音可与当前桌面并用 | STT/TTS、说话人确认和 WebRTC 尚未交付 |
| FR-14 | partial | 通用平台/CoC7 插件边界、版本钉住、能力声明和架构测试 | 只有 CoC7 可执行；仍需用真实第二系统验证边界但不能因此放松当前中立性 |
| FR-15 | partial | 项目内置版本化 proposal-only Skill、多 Agent 角色与结构化验证 | 候选规则包完整隔离、签名安装/卸载和跨系统 Skill 评测未完成 |
| FR-16 | available | 产品建团自动创建版本化 Session 0；KP 可配置世界观、主持方式、风格、团规、内容预警、Lines/Veils 与挂机策略；玩家可保存公开/私密偏好并逐人确认；私密边界只以匿名聚合投影进入所有 AI Agent；即时暂停/淡出/改写/回退不接收原因字段并会暂停活动 AI run。后端、身份竞态、旧版本确认拒绝和双浏览器 UI 流程均有回归 | 当前交付满足 FR-16；长期真实团对边界表单措辞和使用摩擦的反馈仍属于 AC-LONG，而非重新降级本能力 |
| FR-17 | available | 独立只读 Observer、显式路由白名单、全桌/Party/KP 公告/私信不可变账本、加入后私密历史边界、服务端与 UI 投影及三身份浏览器验收；中途加入只获得当前公开事件、任务、已遇 NPC 和关键物品摘要 | 当前 Must 边界已交付；语音、表情和回合通知仍是可选增强 |
| FR-18 | available | 持久 Campaign episode 状态机、rowid 冻结窗口、未完成工作阻塞、幂等 End/Continue、确定性 Full-AI 安全 Previously on、人类 KP 来源绑定记忆审核、KP/player/Observer 隔离投影；权威任务、已遇 NPC、物品资金、伤亡换角与成员 presence 进入同一冻结快照；三身份 UI 完成 End→关闭/重载→Continue | 当前 Must 功能已交付；20 Session 与模型世代切换已通过非 UI 耐久验证，完整真人长团证据仍由 AC-LONG 独立阻塞发布 |
| FR-19 | available | CoC7 持久战斗/追逐、玩家本人预览/修改/确认、小上下文规则外意图 Agent、版本化敌方回合 Agent、先于状态写入的骰值 checkpoint、Session 0 掉线策略、安全工具暂停、过期任务失效、确定性终战及 NPC 物品战利品化均已接通。`player-encounter-turn.e2e.ts` 已从真实玩家 UI 消耗物品、修改并确认攻击、使 NPC 失能、拾取战利品并断线重连；`test_encounter_automation.py` 关闭并重开数据库后复用同一骰据，不重复行动、骰子或伤害 | 第二规则系统的遭遇适配仍是规则生态 Should；不阻塞当前 CoC7 Must 旅程 |
| FR-20 | available | v77 权威物品/货币/转交/制作账本；初始资产幂等导入；拾取、装备、消费+规则效果、买卖、制作、损坏、丢失与隐藏属性按身份投影；同一唯一物品双连接争用恰一成功；玩家 UI 支持查看、拾取、装备、消费、转交和公开商店购买；Session End 与重启读取同一状态 | 当前交付满足 FR-20；更多规则系统的负重语义和真人长期经济平衡属于 AC-LONG/第二插件验证，不降级本能力 |
| FR-21 | available（当前 CoC7 能力范围） | 规则伤亡自动同步生命周期；永久成长沿用事件引用、玩家确认与不可变里程碑；死亡不结束 Campaign，Observer、已审核备用角色、退役、暂离、代管与回归均有版本化请求、玩家/KP 权限、追加事件、双 UI 和 Continue 快照；V4 耐久运行真实执行两次死亡、两次换角、暂离/回归与中途入团，并将相关账本纳入 20 次跨重启权威指纹；CoC7 不支持复活时显式 fail-closed | 当前 CoC7 Must 已交付；真人长团体验与未来声明复活能力的插件仍属 AC-LONG/扩展验证 |

## 4. RPS 与长期验收审计

| Standard | 当前证据 | 未通过原因 |
|---|---|---|
| RPS-01..03 | Session 0 版本/确认/安全状态、三角色工作台、行动卡、检定、多人批次、自动 KP、恢复卡、终局卡、玩家与敌方遭遇回合、物品资产、角色生命周期和 End/Continue/Previously on 均已有 UI | 仍需真实长 Session 证明玩家全程清楚局势、选项和下一步 |
| RPS-04 | 自由行动、世界补全、规则外方法与反关键词特判测试；多区域 Profile、来源地点绑定、实体原型选择与关系校验已有领域/API/UI 回归 | 多模组、多玩家、长时间发散尚未 real-case |
| RPS-05 | 玩家确认技能/做法/推动；`835d3b7` 移除自动确认；`681b337` 在 Full AI 多人批次中也逐所有者确认；死亡后 Observer/换角、永久成长和物品操作均有玩家确认 UI | 仍需在同一真实长团中验证这些决定不会被模型或恢复流程绕过 |
| RPS-06 | 人类 KP 暂停、接管、typed override、追加纠正与安全交还已有服务端和浏览器证据 | 仍需新手/资深 GM persona 在长期 UI 团中验证可发现性与操作负担 |
| RPS-07 | Schema、权限、来源、可玩性、事务与 verifier 已有大量回归；`681b337` 增加 job 入队/执行双重 campaign-session-run 绑定、成员白名单投影、身份切换竞态隔离、legacy proposal/check API 防旁路及暗骰重试不重骰；`f1140c4` 又从真实玩家浏览器注入模型不可达故障，证明 UI 显示尚未执行、没有确认入口、行动只停在 reviewed/pending，且公开权威回合前后不变，并生成独立可哈希 RPS-07 候选 | 已证明玩家行动 Auto KP 的网络失败边界；仍需最终同 commit 长团/三轮重测及其他 AI 入口综合缺陷审计，不能提前宣称完整 RPS-07 通过 |
| RPS-08 | 玩家可见角色卡、规则资源、检定详情、本人遭遇行动、敌方已结算行动公告与角色隔离物品/货币投影 | 完整条件、成长和死亡状态仍需长期 UI 验收 |
| RPS-09 | 事件、事实、记忆、NPC、地图与时间线可持久化；V4 已运行 20 Session/3,000 分钟、20 次重启、模型切换与索引重建 | 仍需真实 UI/模型长团的召回可用性观察 |
| RPS-10 | 普通/推动失败 stakes、实际命令与可恢复线索发布门禁；多人暗骰会隐藏骰值/技能/成功级别但交付契约授权的可观察线索或已接受代价 | 真实成功/失败分支在多本完整 playthrough 中尚未证明 |
| RPS-11 | 建卡、邀请和自然语言行动已有 UI | 新手从首次打开到 Session End/Continue 的无帮助 E2E 不存在 |
| RPS-12 | 可改技能/方法、可看骰值/来源、GM 可接管 | 第二规则系统、复杂战斗和开放世界长期玩法不足 |
| AC-LONG | partial，未通过 | V4 已执行 20 Session/3000 分钟、20 次重启、模型 A→B 持久切换、FTS 重建、权威指纹、50 条 edge cases，以及两次真实生命周期死亡/换角、暂离回归与中途入团；仍缺真实 UI 十幕旅程、四玩家 Full AI、八类 persona、RPS-01..12 观察证据及三轮完整迭代 |

长程真实 UI runner 已接入同一 Campaign 的死亡换角、暂离/回归、中途邀请入团和原玩家转
Observer，并以独立浏览器回归验证最终保持四名可行动玩家；该 wiring 尚未随真实 8001 十幕长跑
取证，因此只减少脚本缺口，不改变上表 `partial` 判定。规则插件战斗和物品/战利品也已通过
KP/玩家 UI 接入同一 runner，并分别有不依赖模型的真实浏览器回归；成长已限定为真实成功检定
标记→KP 规则结算→所有者确认永久变化，三类 GM persona 也分别绑定 Session 0、资深权威工具和
接管/交还数据库事件。所有 wiring 最终仍需 8001 十幕取证，未执行前不改变 `partial`。

`f1140c4` 后，长程校验器可从真实 UI 与 SQLite 权威记录生成 RPS-01..06、08..12 候选，并从
独立模型故障 UI 文件生成 RPS-07。最终发布装配器进一步要求 foundation、长团重建和重测报告
使用同一 source commit，实际读取所有引用文件重算 SHA-256，拒绝不存在、被修改、重复或跨
commit 的证据。历史 V4 foundation 因提交不同不能与未来长团直接拼接；最终取证前必须重跑。
这些是门禁可靠性改进，不是 AC-LONG 已通过的声明。

## 5. 已迁出的历史实现记录

以下内容原先混在 PRD 中，现在只作为有版本边界的历史证据：

### 5.1 确定性流程基础（2026-07-30 后持续演进）

- World Fact 工作台与 proposed facts 审批、持久对抗检定、统一人类 KP 控制闸门、玩家资料
  揭示、地图 revision/雾区以及模组实体约束导入均已有不同程度实现；最新状态必须查目录。
- CoC7 后端状态机覆盖战斗轮、战技、伤害/治疗、理智、追逐和成长；玩家 Full AI 战斗 UI
  已覆盖物品、失能、战利品、断线和服务重启边界。更多武器细项及第二规则插件属于扩展范围。

### 5.2 模拟团与可观测性

- `product-service-replay.v4` 能在 savepoint 沙盒中调用调查员、规则状态、地图/路线、提案、
  线索和遭遇服务并保存指纹；这属于产品服务回放，不等于真实 UI 或多人 Campaign。
- 已有请求延迟、SQLite 锁等待、检索质量、秘密泄露告警、授权矩阵和事务故障注入基础；
  WAL/备份恢复基线、WebSocket 侧信道和更多外部故障仍需持续验证。

### 5.3 真实 UI 终局记录的正确解释

- 2026-08-17 的局域网 `gpt-oss-20b` 运行证明了长模组作者化、玩家动作提交、手动确认、
  CoC7 投骰、终局卡和输入锁定这一条技术通路。
- 当时最后结案陈述匹配了无前置自动结局 operator；因此它**不构成语义上“从正常跑团玩到
  结局”**的验收。后续 `207da1e` 加入因果/可玩性证明，`835d3b7` 再加入来源、失败命令与
  release gate 收紧，但这些修复仍需新的发布后完整 playthrough 证明。
- `681b337` 的双浏览器回归真实经过玩家行动提交后的独立确认、实体骰输入、后台原子提交、
  实时刷新与双方公开结局投影；为保证 CI 可重复，模型理解/机械准备由 deterministic fixture
  注入，因此不能被描述成真实小模型的完整四玩家团，也不能替代 `AC-LONG`。
- 《常暗之厢》等命名回放只可作为测试夹具，不得进入生产裁定、prompt、UI 默认值或
  通用算法。详见 [`REALCASE_CHANG_AN_ZHI_XIANG.md`](REALCASE_CHANG_AN_ZHI_XIANG.md)。

### 5.4 `681b337` 自动化与浏览器证据

- 后端全量：1493 passed，另有 53 个 subtests；Ruff 与 `git diff --check` 通过；
- 前端全量：168 passed；TypeScript 与 Vite production build 通过；
- Playwright：5 条浏览器旅程通过，包括模型不可用时的安全降级、双玩家 Full AI 原子
  结算、主工作台导航、未知路由回退和窄屏重排；
- 本地桌面/390×844 视口人工视觉 smoke 无空白页或控制台错误；该检查未登录玩家席位，
  不能替代上面的双浏览器鉴权 E2E。

### 5.5 持久化重新集结与小模型证据（2026-08-22）

- 后端全量：1505 passed，另有 53 个 subtests；Ruff 与 `git diff --check` 通过；
- 前端全量：173 passed；TypeScript、Vite production build 与 5 条 Playwright 旅程通过；
- `gpt-oss-20b` 连续三轮完成同一通用四玩家机械回合：四项自由中文行动均经桌面理解
  Agent、受约束选择 Agent 和确定性 Kernel，逐玩家确认后整批 `settled`，每轮场景状态只
  提交一次；这证明小模型可完成该受限流程，不等于完整 Session 或 `AC-LONG`；
- 初始真实运行暴露“中文动宾词被代词/修饰语隔开后候选漏召回”。修复只作用于契约显式
  `intent_hints` 的高覆盖算法，并把提示证据传给选择 Agent；另以 small-profile 专用覆盖
  审计 Agent 处理过度澄清。目录外、不可用、无效或 none 仍失败关闭，没有模组名、NPC、
  地点或示例动作分支。

### 5.6 AC-LONG 非 UI 基础（2026-09-01 V4）

- 新门禁独立检查 10/20 Session、3000 分钟、50 个 edge cases、八类 persona、
  `RPS-01..12`、真实 UI、四玩家 Full AI、模型切换、重启、索引重建、关闭模型重放、
  权威指纹、三轮完整重测及 P0/P1；缺字段不会被默认成通过；
- 门禁 schema v2 不再接受 persona/RPS 字符串名单：每项必须带匹配当前 source commit 的
  UI surface、不可重复观察 ID、交互次数（persona）、证据文件引用及 SHA-256；五类玩家只能由
  player UI 证明，三类 GM 只能由 GM UI 证明，`RPS-06` 也必须来自 GM 接管界面；
- 长程 UI 只读校验器仅从 `driver_source=model`、有玩家可见状态指纹且到达终态的行动生成玩家
  persona 候选；固定多人锚点和 fallback 不计，且不会从 Full AI 轨迹推断 GM persona；
- schema v2 新增 14 类脚本旅程覆盖门禁；只读校验器把玩家 Agent 的 exploration/dialogue/
  investigation 与 UI 观察、check/encounter/inventory/lifecycle/module-run 权威 ID 交叉重建，
  不接受 runner 自报。当前尚未接入或实际发生的成长、规则外遭遇动作会继续明确阻塞；
- 产品服务耐久运行完成 20 个 Session、20 次数据库关闭/重开、20 个幂等 Session End、
  20 个唯一 episode/窗口、60 条来源记忆、模型世代切换和 FTS 重建；V4 又通过稳定席位、已审核角色和生产生命周期服务执行两次死亡/换角、暂离/回归与中途入团，所有相关账本纳入跨重启权威指纹；
- 版本化目录中的 50 条安全、通信、连续性、生命周期、物品、战斗、多人原子结算、
  安全与弱模型回归全部通过；
- 最新证据与哈希见 [`evidence/AC_LONG_FOUNDATION_V4_2026-09-01.md`](evidence/AC_LONG_FOUNDATION_V4_2026-09-01.md)。
  门禁仍为 `ready=false`，因此这不能替代最终真实 UI 和三轮完整重测。

### 5.7 AI/真人 KP 共用执行边界与 Need Help 恢复（2026-09-07）

- AI 与手动选择、task method 首步/后续步、并行初始准备、内存/重启后改技能以及并行入库前
  权威重验均调用 `prepare_selected_kernel_action`，并由同一 `ActionResolutionKernel` 产生
  preview 与确定性叙事；该纯函数已下沉至 platform，事务内的持久化改技能重验也不再独立
  构造 `ActionIntent`/preview/叙事。规则输入收窄为冻结的 `SelectedOperator`（operator ID 与
  可选技能），模型 route/confidence 在显式语义适配后被丢弃；契约外 operator/技能失败关闭；
- legacy proposal 的只读 shadow 也通过该准备器生成 `legacy_projection`；其临时合同明确标记为
  `legacy-shadow`、不绑定 run 且没有 commit 路径，因此只消除对照漂移，不把旧模型输出升级为权威；
- 单行动 Auto KP、持久并行流与重启权威检查直接复用 `exact_kernel_outcome`，只选择 preview
  已声明的结果分支；已删除 AutoTurn 私有转发包装及其测试耦合。单行动进入
  `ResolutionTransactionService`，并行行动保留组合批次原子事务，二者共享命令权威但不把
  多玩家事务错误拆成多次单行动提交；
- AI 与真人 KP 新生成的单行动 proposal 均为 `kernel-resolution.v3`，并冻结 run ID、合同
  version/hash、状态版本和 preview hash；仓储在同一事务内对当前 binding、snapshot 与 preview
  重验，并把相同 basis 写入 action 回执。旧 v2 待提交载荷明确要求重新裁决，合同哈希、preview
  哈希或 run ID 任一被篡改都零写入；并行批次继续使用既有 batch authority 与组合事务；
- Need Help 的失败终态只接受按 outcome 分组的稳定错误码；开放 requested 会在下次
  `create_app` 时以原子、幂等的追加事件关闭为 `failed/request_abandoned`，已完成终态不变；
- 冻结的 `ScenarioAuthorityContext` 与 `require/initialize/ephemeral` 三种策略现由单行动、
  并行规划/结算、真人手动候选和 Need Help 共用。AI 对话在模型等待期间遇到 state 漂移会在
  proposal 写入前失败关闭；Need Help 构建初始 snapshot 时仍保持零持久写入；
- 新增纯 `ScenarioActionCatalog`，AI 语义选择、真人完整候选目录与 Need Help brief 共同适配
  同一候选、Kernel 可用性、技能白名单和契约 evidence ID；外部模组/规则书命中继续标记为
  `source_context_only`，不参与权限提升。跨入口契约测试锁定这些字段；
- 单行动与并行结算现在都在 command-batch 回执保存 `KernelAuthorityBasis`，共享校验器在写锁内
  绑定 run、有效合同 hash、state version 和 preview/settlement hash；两条路径还共用纯
  `resolved_action_commands` 生成 primitive 结算事件和 outcome 命令。单行动在同一事务中从
  权威 player action 解析 actor，并复核 action/proposal/campaign 绑定；并行仍保留组合事务；
- LAN 敏感操作限流覆盖 Need Help POST；KP-only 历史读取、稳定游标与刷新恢复均有后端及
  前端回归；
- 桌面语义协议新增窄确定性护栏：可见世界提问、已完成结果宣称和明确第一人称交涉不再
  依赖弱模型猜 route；两次坏结构后只恢复显式有序计划。五个新增测试锁定零模型调用、
  结果宣称优先级和安全降级，护栏不接触 operator/outcome/state；
- 后端有效结果为 2302 passed、1 skipped（其中唯一沙箱本地监听失败在获准的本地回环环境
  单项通过）；Ruff 通过。前端有效结果为 253 passed，TypeScript 和 Vite production build
  通过；构建仍提示主包约 500.22 kB，需要后续桌面客户端性能轮处理；
- 清理了上述 application service 中重复的 `_preview` 方法、Kernel/ActionIntent 直连与确定性
  叙事直连、持久 rebinder 的第二套 preview/叙事构造、旧 application 准备器文件、本轮重复
  架构断言、三份测试 basis 构造逻辑、legacy shadow 的独立 preview 构造，以及
  `DirectorBriefProjector` 内重复的 scene/operator/clue/obligation 证据装配；其余功能域零消费者扫描与直接依赖
  映射未发现可安全删除的生产代码或声明依赖；`httpx2` 虽无业务源码直接 import，但当前
  FastAPI/Starlette 测试客户端明确将其作为迁移依赖，故保留。`nano-vectordb` 及 lock 中的重型 Transformer
  栈属于固定 MiniRAG 提交的显式运行时补齐，Steam 基础包应不安装 `rulebook` extra 或未来
  更换索引适配器，不能只删除声明/锁条目来制造表面减包。
- 本地 8001 的真实 `gpt-oss-20b` 已通过 `kernel_model_realcase.py`：单行动在首轮选择，
  task method 在去除目录外字段后第二轮选择，结果叙事两轮坏结构时安全落到确定性提示；
  `tabletop_protocol_realcase.py` 也通过对话、机械、信息、结果宣称与有序计划五类路由，且
  无秘密泄漏。两项证明受限协议可执行，不等于完整 Session、`AC-LONG` 或 Steam 发布验收。

### 5.8 本地 20B 四玩家真实 UI 纵向检查（2026-09-08）

- 使用四个独立 Chromium context 和真实《鬼屋》DOCX，从 UI 完成建团、四个逐席邀请、
  四名玩家认领、四张调查员卡提交/批准/绑定，以及 Session 0 发布和四人确认；SQLite 留存
  1 个 Campaign、1 个 Session、4 个席位和 4 条 approved 调查员参与记录；
- 模组导入完成 351/351，合同任务读取 331 个来源块，21 个初始分区全部完成；随后执行
  105 个来源覆盖补写（cycle 0..3），最终结果约 533 KB。若原文没有规则插件可解析的技能词，
  非阻塞补写明确失败而不猜技能；
- 本地 GPT-OSS-20B MXFP4 的整本首次编译约 55 分钟，最终为
  `status=succeeded / stage=review_rejected`。这里的 succeeded 仅表示后台任务完整结束；独立
  审核拒绝意味着没有自动发布/绑定，四玩家也没有进入行动结算。因此纵向 smoke 与
  `AC-LONG` 均未通过；结论是该未微调模型适合受限回合协议，但当前不适合临场编译整本合同，
  应在开团前预编译/缓存，并优先评测更强或专门微调的合同生成模型；
- 后台终态之后，原测试前端的 5174 端口被无关音乐项目 Vite 接管，runner 因而没有进入其
  第 2/3 次语义重试。AIKP runner/8012 已清理，未终止或修改该无关进程。端口干扰与模型审核
  拒绝是两个独立事实；即使没有前者，本轮也不能被记为可玩通过。

### 5.9 v7 失败 checkpoint 的确定性复盘（2026-09-08）

- 最终日志只显示第一个 `canonical_travel_conflict`，但 checkpoint 原样重放证明实际有两个
  冲突的保留 travel ID。编译器为避免返回半物化合同而逐个报告冲突；旧审查修复只收缩一次，
  随后把第二个同类冲突误送入拓扑修复。现改为每次仅删除由现有 location link 和编译器消息
  共同证明的占位记录，再重新编译，直到没有同类冲突；普通 operator 与来源记录不受影响；
- 新增连续冲突回归测试，并把 authoring authority revision 提升到 45，使旧的未发布审查
  checkpoint 获得新的有界续跑 epoch。相关 authoring/compiler/job 测试 207 项通过；全量后端
  有效结果为 2302 passed、1 skipped、53 subtests，唯一沙箱回环监听失败在非沙箱单项通过；
  Ruff、compileall 与 Vulture 100% 置信度生产代码扫描均通过；
- 本地 8001 的 GPT-OSS-20B 在 228-token 受限拓扑样例中一次返回了完全正确的初始地点、两条
  路线及逐条证据 ID。对 v7 真实 checkpoint 的只读修复重放越过了两个 travel 冲突，并完成
  13 个分批拓扑槽位调用；所得合同 schema/确定性编译有效，但仍为 `release_ready=false`：
  不可达地点、场景可达性、来源内容交付和核心线索发现四项 playability 门禁仍未闭合；
- 剩余问题的主要证据不是“再多给一次审查即可”：21 个 location 中混入了 `文字材料 6`、
  `MP: 18`、`扮演须知`、线索描述和房间效果。两个标题相同的“1 号房间：储藏室”实际分别位于
  一楼和地下室，必须按来源父级保持分离，不能误作重复记录。下一实现目标应在 IR 进入场景图前
  增加来源角色分类、地点身份归一与可解释拒绝；在该层完成前不重复 55 分钟 UI 全量导入，也不把
  本次只读重放记为纵向验收通过。

### 5.10 来源场景权威与小模型 topology 恢复（2026-09-08）

- 新增确定性来源场景物化：只有服务器标为 `scene`、且块文本本身可解析为与 `scene_key` 相同的
  编号场景标题才生成 IR location。它在模型 action slot 解析完成后加入，再由既有地点身份器与
  模型地点合并，因此不会移动分区内 slot，也不会把普通 heading/text、展示材料或继承标签正文
  升格为场景；13 个以上场景按 IR 的 12 地点批次上限拆分；
- 地点角色门禁要求来源标题/section/scene key 精确对齐，或同一非展示/数据块正文明确把该名称作为
  空间地点陈述。删除不受支持地点时，统一地点引用协议会保守收缩引用该地点的 operator、link、
  task method、response obligation、reactive/trigger rule、signal band 和 ending；不会把普通
  `facts.favorite_scene` 中恰好相同的字符串误删。v7 重放的悬空地点条件由 1 降为 0；
- v7 从 21 个含误分类地点的旧候选重装配为 16 个来源支持地点：九个显式 scene 均有稳定来源
  location；“场景 1: 介绍”与“介绍”合并；一楼/地下室同名储藏室保持分离。合同 schema 有效，
  但仍因场景可达性、来源内容交付、核心线索和结局/规则效果覆盖不足而拒绝发布；
- 本地 8001 GPT-OSS-20B（每次 1600 token 上限）对两个 topology 证据批次的多次只读对比中，
  能稳定返回 envelope JSON，却曾伪造/缩短 `source_block_id`、重复已有双向边，或选择正文没有
  证明的路线。服务端均零提交。prompt 现明确要求逐字复制本批 source ID、不得重复既有连接，
  schema 拒绝重复双向边；每批统一为最多两次尝试，第一次 JSON/schema 或来源/语义错误会作为
  精确反馈返回，第二次仍失败则安全放弃。该模型仍不能保证完成整本 topology；
- 本轮有效后端结果为 2339 passed、1 skipped、53 subtests（全量沙箱内 2337 passed，两个环境
  用例分别在隔离重跑通过）；前端 51 个文件 253 项测试通过，端口管理 5 项在允许 loopback 环境
  通过，TypeScript/Vite 构建、Ruff、compileall、Vulture 90% 和 `pip check` 均通过。依赖审计未发现
  有证据可安全删除的直接依赖；MinerU/MiniRAG 重依赖仍隔离在可选作者工具/`rulebook` 组。

### 5.11 可执行可达性与来源文字材料交付（2026-09-08）

- 修正编译器只遍历 `location_links`、却把合法 `set_scene` operator 误报为不可达的双重算法。
  `PlayabilityReport` 现在公开同一次有界状态探索实际到达的 scene ID，编译器直接复用它生成
  reachable/unreachable 列表；条件未满足或运行时会拒绝的跳转仍不会计入；
- 调查选择物化器若能从服务器 `scene_key` 唯一匹配现有来源场景，就直接复用该场景为入口，
  不再额外制造一个与“介绍”并存的系统地点。没有来源场景可复用时仍保留通用系统 phase；
- 新增确定性文字材料交付边界：仅当同一来源块明确写出“检定成功后交付编号文字材料”，且同一
  结构场景下存在唯一对应材料正文与既有检定 operator 时，服务器才绑定成功分支、公开逐字正文
  和稳定事实路径。重复的具体/汇总交付说明合并为一个结构化 handout clue；模型不能选择事实路径、
  改写公开内容或绕过成功条件；
- v7 真实来源离线重放从 16 个地点收敛为 15 个（删除重复调查 phase），可达地点从 6 个提升到
  7 个、不可达从 9 个降到 8 个；文字材料 7 成功交付后可执行地解锁高等法院/中央警察局，文字
  材料 8 的多种成功检定合并为一个 handout clue。合同结构有效但仍无结局，故未发布、未启动完整
  UI 跑团。authoring authority revision 提升到 46，以便旧未发布 checkpoint 重走新权威边界。
- 本轮全量沙箱回归为 2344 passed、1 skipped、53 subtests；唯一失败是环境禁止真实 HTTP 图片
  provider 绑定 localhost。本轮两次隔离复跑申请均在权限审核阶段超时、测试未启动，因此仅保留
  上一轮该未修改用例在允许回环环境通过的历史证据，不将其计作本轮新通过。Ruff、compileall、
  Vulture 90% 扫描及 `pip check` 均通过。

### 5.12 来源实体模板绑定与小模型语义修复（2026-09-08）

- 不可变 Setting Profile 现在可把模组已有 NPC、组织、物件、线索和事件绑定到全局或具体
  聚落槽位的实体原型；人类 KP UI 展示未绑定实体、按种类/槽位过滤合法原型，并保存新版本，
  不复制来源实体或修改 Canon；
- 世界补全候选通过 `source_entity_ids` 复用来源实体，新实体只使用候选包局部引用。服务端在
  模型调用后的写事务之外以及持久化之前复核建筑、职位、实体种类、标签、关系方向、目标原型
  和基数；结构正确但语义错误也进入同一个最多一次修复的有界 Agent 流程；
- AI 与人类 KP 共享相同 Profile、候选 Schema、校验器与提交边界。人类 KP 保留完整分析，
  AI 上下文则按玩家意图投影最多四个相关已选槽位，并额外保留已绑定来源实体所在槽位，避免
  小模型读取整座聚落的无关模板；
- 局域网 8001 的 GPT-OSS-20B MXFP4 在 2200 token 输出上限下，第一次把 `agency` 关系错误挂到
  机构候选，确定性错误指出该原型 `allowed=none` 后一次修复成功：复用了真实来源物证 ID，
  生成警长办公室和巡警候选，并把巡警的 `agency` 正确指向机构。该证据只证明受限原子流程
  可执行，不是完整跑团或真实 UI 验收；
- 有效全量结果为后端 2380 passed、1 skipped、53 subtests（2379 项在沙箱内通过，唯一 localhost
  图片 provider 用例在允许 loopback 后隔离通过）；前端 257/257（3 项端口生命周期用例在允许
  loopback 后通过）。Ruff、compileall、TypeScript、Vite production build、Vulture 90% 与
  `pip check` 均通过；未发现有证据可安全删除的生产死代码或损坏依赖。

### 5.13 共用类型化实体物化与本地模型纵向验证（2026-09-08）

- 新增 schema v88 的 `campaign_world_entities`、`campaign_world_entity_relations` 与落地收据关联表。
  获批候选中的 NPC、组织、物件、文档、交通工具、事件和线索载体现在由同一个
  `WorldEntityMaterializer` 落地；AI KP 自动化与真人 KP 表单共享候选复核、事务、幂等键、关系、
  NPC/地图投影和收据，不再各自维护实体生成逻辑；
- 调用方必须在推理前选定 `requested_expansion_kind`，该值进入草稿指纹和不可变分析快照。
  模型不能把普通环境补全擅自升级为需要 `branch_plan` 的支线或锚点桥接。为适配较小模型，
  输出示例渲染唯一合法类型；窄传输编译器只规范局部引用、引用来源和未授权 source ID，目录外
  建筑、职位、原型、标签及关系语义仍由确定性校验拒绝并最多反馈修复一次；
- 新增角色过滤的世界实体图 API 与前端逐实体具体化表单。上下文检索使用已批准候选 subject/
  proposal 作为别名来源，命中后扩展可见的一跳关系；SQL 同时过滤关系两端可见性，玩家不会从
  公开 NPC 的关系摘要看到 KP/secret 机构名称；
- 局域网 8001 的 GPT-OSS-20B MXFP4 在 2200 token 输出上限下完成真实纵向采样：第一次遗漏
  必需机构并把关系指向自身，精确语义错误反馈后生成“警探 → agency → 小镇警察局”。审批、
  两实体与关系原子落地以及下一回合双实体召回均通过。该采样证明受限流程可执行，不代表模型
  单次成功率、整本模组、多人 Session 或真实 UI `AC-LONG` 已通过；
- 本轮有效后端结果为 2389 passed、1 skipped、53 subtests（全量沙箱 2388 项通过，唯一图片
  provider localhost 用例在允许 loopback 后隔离通过）；前端 52 文件 259/259 在允许 loopback
  的单 worker 环境通过，TypeScript 与 Vite production build 通过。Ruff、compileall、Vulture
  80% 与 `pip check` 均通过；直接依赖均有实际运行边界，MiniRAG 重依赖继续隔离在可选
  `rulebook` extra，项目仍不 import 或捆绑 MinerU 本体。本轮未发现可安全删除的生产死代码。

### 5.14 模板实体状态与共享审批（2026-09-08）

- v89/v90 增加实体当前状态、不可变变更收据及回合状态候选；原型声明合法维度。
  真人 KP API/网页与 AI 提案审批共用 `WorldEntityStateService`，保留幂等、会话权限、
  版本冲突和按可见性过滤的模型上下文。旧实体没有维度时拒绝猜测性写入。
- AI 只能选择提示中存在的实体和维度；服务器从原提示快照绑定版本，不使用模型计算的
  新版本，也不在审批时刷新成当前数据库版本。缺乏权威内核的自动 KP 清空新字段，
  候选经真人审核提交可用；尚未实现确定性规则效果到通用实体维度的自动映射。
- 审批应用层增加整体 savepoint，后台任务即使捕获错误后提交任务记录，失败审批的
  叙事、事实、状态和 outbox 也不会部分保留。参考 SQLite 官方
  [嵌套 savepoint](https://www.sqlite.org/lang_savepoint.html) 与
  [事务语义](https://www.sqlite.org/lang_transaction.html)，复用当前事务架构。
- 状态从秘密改为公开时，历史事件及收据采用旧值/新值中更严格的可见性，避免泄露旧值。
- 本地 8001 GPT-OSS-20B 两次 900 token 上限探针：正例选择正确实体与维度，但将版本 3
  误写为 4；反例把玩家猜测错误当成合作事实。因此版本改为服务器绑定，仍保留自动模式
  清空未授权效果的边界。该模型样本不构成完整回合或真实 UI 验收通过证据。
- 本轮未新增运行依赖；Ruff、Vulture 80% 和 pip check 通过，未找到可有把握删除的
  生产死代码。前端 262 项测试（本机端口用例隔离重测）、TypeScript 和生产构建通过。
  新增/相关后端 58 项测试通过，包括版本冲突后提交外层事务仍无部分写入的回归。
  全量后端 2397 项通过、1 项跳过；唯一受 localhost 绑定限制的图片接口测试在允许本机
  端口后单独通过，有效合计 2398 项通过及 53 subtests。

### 5.15 共享结算收据与跨模组重试（2026-09-08）

- 审计确定性效果到模板状态的接入时，发现规则合同 EntitySpec 与模组图实体仍缺少
  经过确认的身份映射；自动同步保持未完成，不能靠标题或自由文本推断身份与状态维度。
- 四个新回归先在旧实现复现失败：并行仓储合法重试被旧版本拒绝；单人重用同一键时
  改变行动者、结果或保留旧 hash 篡改预览仍被当作原请求。现在先核对完整已提交预览、
  批次类型、authority basis，以及单人 action_resolved 事件中的结果/行动者，再返回原收据。
  只有新提交检查当前快照版本；没有新增表或依赖。
- 跨数据库连接测试进一步复现：已结束模组的旧收据被重放时，结束清理会拒绝下一轮
  新提交的玩家行动。重放现在只返回原收据，结束清理仍留在首次提交事务中。
- 验证覆盖单人/并行结算、API、结束处理、世界反应与篡改重试；这些是确定性内核测试，
  不构成本地模型语义评估或完整 UI 跑团验收。

### 5.17 共享赋值冲突检查（2026-09-08）

后续进展见 5.18：模板状态的事务写入现已接通，双向条件读取与模型编制目录仍未完成。

- 合同编译与并行结算共用 `command_writes.exclusive_assignments`，删除两套重复的目标判断；不新增依赖或数据库迁移。
- 实体运行状态按属性检查：同属性不同值拒绝、不同属性可合并；记忆追加和资源增减继续保留各自效果。
- 事实删除与赋值分开编码，避免 `__removed__` 字符串误判；JSON 类型比较避免布尔值与整数被 Python 相等判断混同。
- 新增编译/执行一致性参数测试，覆盖冲突时快照不变及记忆追加不丢失。
- 验证：90 项相关后端测试通过；Ruff 与 Vulture（80% 置信度）通过。未调用模型，未进行真实 UI 验收。
- 本节只处理精确目标的赋值冲突，不声称解决父子事实路径重叠或跨属性业务约束；规则效果到模板维度的事务写入仍待接通。
- 方案比较：数据库锁管理实际事务并发，但同一批玩家意图的语义冲突仍由领域层检查；没有为此引入新数据库。[PostgreSQL 并发控制文档](https://www.postgresql.org/docs/18/transaction-iso.html)。

### 5.16 规则合同与来源实体的显式身份（2026-09-08）

- EntitySpec 增加可选 `module_entity_id`，引用当前模组已确认实体图中的准确 ID；
  手工编译、模型候选认证、发布复核和运行绑定均校验引用。重复将一个来源实体绑定为
  多个规则身份会阻止发布。缺省字段不序列化，保持旧合同的存储与哈希语义。
- KP 网页新增逐实体来源选择表单，保留未关联选项并保留其他完整合同字段；提交后创建
  待审核草稿，已绑定运行的编辑入口禁用。关联不会根据名称相似度自动选择。
- KP 世界实体视图按当前运行的合同 hash 和 `module_source` 来源 ID 解析规则身份；
  来源失效则不显示有效关联，同名的世界补全实体不会误关联，玩家投影不暴露内部绑定。
- 这是明确身份链路的实现。确定性规则状态到模板维度的转换，以及 AI 生成阶段的来源
  实体目录选择还未接通，不能将本轮描述为全自动实体状态同步。
- 相关后端 293 项、前端组件 20 项测试通过；TypeScript、Vite 构建、Ruff 与 Vulture
  80% 检查通过。未新增依赖、迁移或删除生产代码；本轮没有进行完整 UI 跑团验收。

### 5.18 规则效果写入模板实体共享账本（2026-09-08）

- 增加封闭命令 `set_world_entity_state`；要求合同实体显式绑定模组来源，维度和可见性必须明确。编译和并行冲突检查共用精确目标语义，不做名称猜测。
- `ScenarioEffectService` 验证持久化批次，统一消费角色规则效果与世界实体效果；单人/并行入口共用保存点，错误可回滚规则快照、收据、实体状态与 outbox。
- 复用 `WorldEntityStateService`；v91 增加 `rules_kernel` 来源。无变化的规则赋值也保留一次版本化收据，重放沿用收据原版本，避免覆盖后来的人类 KP 修改。历史迁移保留原记录与外键。
- 前端沿用当前状态面板，不增加第二套实体状态 UI；新增来源类型兼容。清理状态标准化函数中不再需要的类方法参数，无新增依赖。
- 新增端到端测试涵盖单人/并行写入、无变化收据、后续人工修改后的重试、失败回滚、私密投影、值/可见性并行冲突和旧库迁移。边界说明见 [规则与模板状态](WORLD_ENTITY_STATE_EFFECTS.md)。
- 验证：完整后端回归 2417 通过、1 跳过、53 子测试通过；唯一失败为沙箱禁止本地端口，该 localhost 模拟图片接口测试单独重跑通过。后加的两项并行值/可见性冲突测试在新测试文件 9 项全通过中验证。前端相关 4 项、TypeScript、生产构建、Ruff、Vulture（80%）和依赖一致性检查通过；构建仍提示主包约 504 KB，未在本轮顺带拆包。未调用付费或本地模型，未执行真实 UI 跑团验收。
- 尚未完成：模型编制的维度/来源目录与条件回读；动态 IR 仍未开放此命令。完整 UI 跑团验收不由这些确定性测试替代。

### 5.19 编制实体目录与小预算本地探针（2026-09-08）

- 生成前投影已确认来源实体与明确绑定的原型维度到证据目录；支持已选固定版本 Setting Profile 和实际物化账本。不读取状态值，不自动选择未绑定模板、不物化世界对象。
- IR 保留 `module_entity_id`，已绑定实体禁止按名称合并到其他来源或未绑定对象；候选组装、审核替换与任务恢复检查精确引用、维度和可见性。目录纳入生成指纹，工作进程编制前后验证；空目录保留旧指纹。
- 新增目录预算与过期保护测试；303 项相关回归通过。未新增依赖或迁移。静态检查针对新增目录模块无高置信度死代码。
- 本地模型为 8001 的 GPT-OSS-20B MXFP4，使用生产编制提示与合成片段。初始探针把 reasoning_effort 放在顶层，900/1800 token 均耗尽且无最终 JSON；这两次不作为生产客户端缺陷证据。改用项目已有的 chat_template_kwargs 后，900 token 上限返回 855 token（提示 6911 token）的有效 IR JSON，但仍重复记录，并将状态变化写成 set_fact。探针 temperature=0，不等同生产客户端 GPT-OSS 的 temperature=1，也不是完整集成验收。
- 据实测补充了模板状态影子事实拒绝规则和回归测试；没有静默把错误模型命令升级成正确效果，也不把模型通过 JSON 校验当作语义通过。后续仍需细化模型步骤、完成条件回读与真实 UI 完整跑团。

### 5.20 精简状态效果编制记录（2026-09-08）

- 新增 `IrWorldStateEffect`：模型只提供实体、维度、值、可见性和结果时机，服务器将其转成已有 `set_world_entity_state`。保留原命令兼容，不新增运行时命令、依赖或数据库迁移。
- 在动作分析前显式转换；不修改输入，转换后清空短记录防止重放重复添加。严格值类型沿用命令约束，布尔/整数不混同，超过原分支预算拒绝而非截断。新增 11 项测试，含四种时机、坏值、幂等、预算和真实合同组装；相关回归共 247 项通过，Ruff/Vulture（80%）通过。
- 本地 GPT-OSS-20B MXFP4 使用**实际生产客户端**和生产编制提示，temperature=1、max_tokens=900：提示 7196 token，输出 250 token 后正常停止，但混用 raw command 与短记录字段，漏 policy/地点/实体等信息，IR 校验不通过。只发送合成档案室片段，没有游戏写入；这是失败样本，不是模型能力验收。仅加入短格式不足以证明生成可靠，后续应拆分带明确候选和边界的编制阶段。
- 状态条件回读、完整真实 UI 跑团和长期 PRD 目标仍未完成。

## 6. 当前优先缺口

按 P0/P1 风险和依赖顺序：

1. 用真实小模型从玩家 UI 完成至少 4 玩家、含对话/检定/失败/并行冲突的完整 Session；
   当前 durable regather 与无 KP 漂移后重新同意已经具备；四玩家 UI-only 长期驱动及安全
   取证手册见 [`AC_LONG_REAL_UI_RUNBOOK.md`](AC_LONG_REAL_UI_RUNBOOK.md)。驱动会记录玩家可见
   公开叙事、澄清、确认、技能、投骰和推动轨迹；短模组提前结束时只能从显式提供的下一份
   不同模组继续累计 Campaign，不会重复同一本或伪造 Session。2026-09-08 的本地 20B 纵向
   运行完成四玩家前置与整本编译，但独立审核拒绝自动发布，故仍不是完整 Session 或长期
   Campaign 证据；来源角色分类与地点身份归一现已完成第一轮确定性收口，下一轮应补齐显式场景
   之间可证路线、来源内容交付和结局/规则效果覆盖，再对比更强或微调的合同生成模型，并使用
   专属测试端口；
2. 使用真实长团轨迹逐项标注八类 persona 与 `RPS-01..12`，并完成三轮完整修复重测；
3. 合并上述证据后重新执行 `AC-LONG`，在所有门禁闭合前保持长团验证 capability 为 `partial`。

每项实现后都要同步 capability catalogue 的 `requirement_ids`、`evidence_refs`、summary 和
状态，并在本文件记录新的审计提交。不得只更新 README 中的“现在能做什么”。
