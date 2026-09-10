# 受约束世界补全与动态支线

## 目标

KP 本定义故事不能失去的内容，但不是完整世界数据库。玩家离开模组列出的路线时，
AI KP 应能补全符合时代与地点的公共设施、普通 NPC、反应性支线和替代调查路径，
同时不得改写真相、泄露未来章节或把推测冒充成已经发生的事实。

## 四层权威

| 层级 | 存储位置 | 含义 | 变更方式 |
| --- | --- | --- | --- |
| `module_canon` | 模组知识层 | 原本明确给出的真相、人物、地点和约束 | 只随模组版本和 KP 审核变化 |
| `module_anchor` | 模组知识层 | 必须保持可达的线索、冲突或叙事功能 | 可改变传递方式，不可静默删除 |
| World Fact | 追加式事件账本 | 游戏中已经确认成立的公开或 KP 秘密事实 | 追加断言或 retcon 事件 |
| Generated Candidate | 回合草稿 | AI 为当前玩家意图提出的世界补全 | 审批前不是事实 |

现有 `canonical_fact` 表示桌面已经公开确认的运行事实，不等于 `module_canon`。
模组真相可能仍是 KP 秘密，因此两者不能合并成同一个类别。

## 运行流程

```text
玩家意图
  → 按团、剧透阶段和可见性检索模组 chunk
  → 读取 module_canon / module_anchor
  → 读取当前 World Fact head、时间、地点和 NPC 状态
  → 判断玩家要求是否已由现有世界回答
  → 若存在缺口，生成补全候选
  → 时代/地域/规模合理性检查
  → Canon/Anchor/World Fact 冲突检查
  → 剧透与权限检查
  → 草稿审批
  → 玩家实际接触或 KP 确认后追加 World Fact
```

行动契约的动态补全使用分层的模型/算法边界：

```text
玩家原文 → ActionIntentPlan → 确定性状态与依赖校验
           → 独立语义完整性审计
           → 冻结计划作者化 → effect/WorldCommand 双向校验
           → overlay 审批/自动拒绝 → 玩家实际执行
```

其中模型只负责语义候选和表达，不能把玩家没有选择的资源从当前状态中自动挑出。
无前置条件的正常行动允许空 requirements，不得为填充 Schema 虚构占位位置或事实。
只读观察不是状态变更；它读取前序结算结果，不得用 `set_fact` 伪造“观察成功”。

临时补全只能产生低权限、命名空间内、有界的新事实和显式成本。切换场景、移动玩家、结束运行、
删除状态、改写既有对象或激活新 overlay 等高权限命令不能由它自动创造；它们必须来自已编译模组或规则插件。

检索必须先做权限、当前模组、章节/剧透阶段和已确认事实过滤，再做词法、向量或实体关系
排序。不能因为语义相似就把未来章节或秘密图片送入玩家视角。

## 剧情锚点

锚点表达叙事功能，而不是固定过场：

- “关键真相必须仍可发现”不等于“玩家必须进入指定房间”。
- “冲突必须有机会发生”不等于“在固定时间强制播放”。
- “NPC 必须发挥某种作用”不等于“NPC 永远不能死亡或被绕过”。

替代路径可以提供同一锚点的部分信息，但必须记录信息完整度、代价、风险和已经使用的
交付方式。锚点可达性检查不得把新的替代路径写回原始模组来源。

## 世界合理性

补全不能使用“地点类型必然存在”的硬编码。例如小镇可能有警察局，也可能只有治安官
办公室、临时巡警驻点、邻镇辖区或没有常驻警力。候选至少考虑：

- 年代、国家、地区和法律制度；
- 聚落人口、行政等级、经济与交通条件；
- 战争、灾害、超自然影响等当前状态；
- 已确认地点、人物、组织和历史事实；
- 玩家要求的服务，而不只是玩家使用的现代名称。

合理性结果需要保存候选结论、理由、置信度、使用的来源和被排除的替代项。置信度不能
覆盖硬冲突；与 Canon、Anchor 或 World Fact 冲突时必须拒绝。

## 通用时代与世界模板

模板分成两条正交轴，不能把某一个示例拓扑写成世界规律：

- **区域模式**描述有界、连通、可替换的聚落图。例如城市与卫星聚落、县城与乡村腹地、
  铁路走廊、孤立乡村、港口与内陆。`城市—近郊小镇—远方小镇—乡村` 只是其中一种；
  每种模式声明适用条件、节点类型、数量上下限、距离带和当时可用的交通方式。
- **聚落功能模板**描述城市、小镇、村落或乡村通常需要的社会功能，不断言独立建筑一定
  存在。每个功能槽位包含建筑表现候选、内部区域、职位、服务、记录媒介、进入方式、调查
  面和事件种子，并区分 `core`、`typical`、`conditional`。
- **实体原型**描述人物、组织、物件、文档、交通工具、事件和线索载体可承担的能力、可变
  状态维度与带种类/数量约束的关系槽位。NPC 原型引用已有职位 ID；原型本身没有姓名、
  数值、秘密或线索答案，也不能直接成为世界实体。

时代包是规则集无关、带 `schema_version`、语义版本、内容边界和来源说明的严格数据契约。
内置 `us.1920s` 只含通用历史与社会功能，不含商业规则书或冒险模组文本。未来年代、国家、
奇幻世界或 Steam DLC 可以提供新的 JSON 包；2 MiB 尺寸上限、未知字段拒绝、跨引用完整性、
区域连通性和规范化 SHA-256 在载入时验证。规则插件决定检定和数值，时代包不能定义可执行
规则，因此新增规则书无需复制世界生成器。

读入模组后的顺序固定为：

1. 从已审核、带来源的模组实体读取地点名称、上下文和原始块/图片引用。
2. 对当前选择的时代包、区域模式和聚落类型执行确定性匹配。
3. 高置信且显著领先其他候选时标为 `matched`；竞争候选保留为 `ambiguous`；无法解释的
   原创地点保持 `unmatched`，不会被模板吞掉。
4. 计算已覆盖功能、未覆盖核心功能、常见候选和条件候选。缺项只表示 KP 可考虑的社会
   功能，不等于应新增建筑。
5. 人类 KP 可把模组已有 NPC、组织、物件、事件和线索显式绑定到全局或某个聚落功能的
   实体原型。绑定保留原 `module_entity_id`，不复制实体，也不改变来源、剧透或 Canon。
6. AI 先通过 `source_entity_ids` 复用当前 Profile 快照中的来源实体，再从获准槽位选择缺少的
   实体原型；新候选建立局部引用，并只填写原型允许的标签变体、职位与关系槽位。歧义匹配
   和未满足的必需关系阻止落地。
7. 确定性校验拒绝模型发明目录外建筑、职位、实体原型、来源实体 ID、关系或不可解析目标。AI KP 与
   人类 KP 使用同一不可变 Profile、候选、校验和提交接口；AI 输入只投影当前意图相关的已选
   槽位与校验字段，人类 KP 分析页仍保留完整模板。
8. 调用方在模型运行前选择 `environment / reactive_branch / anchor_bridge` 工作流，选择进入
   请求指纹和不可变分析快照；模型不得自行升级或降级工作流。输出示例会渲染为该唯一值，避免
   小模型照抄枚举占位符。
9. 审批前保持只读；审批只认可候选，玩家实际接触或 KP 明确确认后才通过共用原子事务
   加入 World Fact、类型化世界实体、NPC、关系或地图棋子。

当前 API 提供只读目录、区域实例和模组覆盖分析：`GET /setting-catalogs`、
`GET /setting-catalogs/{pack}/regions/{pattern}`、
`GET /setting-catalogs/{pack}/settlements/{kind}`、模块级 Setting Profile 版本接口、
`GET /setting-profiles/{profile_id}/settlements/{settlement_id}/analysis`。场景导演 UI 使用
Profile 分析向新手 KP 展示“已覆盖 / 歧义 / 核心缺项候选”，而世界补全模型从运行固定的
Profile 版本读取一个意图相关的紧凑投影，复用同一来源实体绑定、实体原型与校验器。结构化模型输出中的新实体只用局部引用；
关系目标必须指向同一候选包或快照已有实体，并满足目标种类和基数。旧的“把整本模组当作一个聚落”分析入口已删除，
避免多区域模组产生错误覆盖。

## 扩展类型

1. 环境补全：公共设施、商店、交通、普通居民和时代物件。
2. 反应性支线：由举报、求医、查档、找同行或追踪某人自然产生。
3. 锚点桥接：玩家避开原路线后，为关键线索提供新的合理可达方式。

环境补全可以较低风险自动化；改变主线信息流、创建关键 NPC 或提供锚点桥接属于高影响
候选，必须接受更严格检查。

## 审批模式

- 保守：所有补全候选由人类 KP 明确批准。
- 平衡：无冲突的环境补全可自动采用；支线和锚点桥接需要 KP 批准。
- AI KP：通过全部确定性检查的低副作用候选可进入自动审核流，但玩家行动触发的
  补全仍要求该玩家确认，并生成与人工审批相同的审计事件。

模式只改变审批强度，不改变事实权威、规则判定、剧透过滤、来源追踪或回滚方式。
自动批准绝不能直接修改原始模组 chunk。候选在确认前不会物化为地图、NPC、记忆或
World Fact；模型失败或策略拒绝时也不得使用“默认合理”落地。

## 分阶段实现

### A. 模组知识候选

从 PDF/DOC/DOCX 的文本、表格和图片中抽取带来源的 Canon、Anchor、实体与关系候选，KP
审核后保存。先实现人工修正和版本化，不自动运行剧情。

当前已完成这一阶段的确定性基础：

- 实体限定为 NPC、地点、线索、组织、物品、事件和剧情锚点；
- 每个实体和关系必须引用同模组的已批准知识候选，剧情锚点必须引用
  `module_anchor`；
- `reveals`、`leads_to`、`provides_access_to` 和 `same_as` 参与有向可达性遍历；
- `blocks` 与 `contradicts` 不参与“绕过去”的遍历，而是作为显式冲突报告；
- 玩家不能访问实体图作者接口。当前图是 KP 的来源整理工具，不是玩家公开百科。

### B. 只读缺口分析

输入玩家意图和当前世界，返回“已有答案 / 可以补全 / 存在冲突 / 信息不足”及理由；
不创建地点、NPC、事件或事实。

当前场景导演已经实现只读分类。只有 `world_gap` 可以进入下一阶段；当前剧透范围内存在
原文答案、未来剧透命中或实体图存在显式冲突时，不得生成补全草稿。

### C. 草稿补全

把地点、NPC、支线和锚点桥接放入现有 proposal 审批流。批准前不得进入地图、NPC
档案、记忆或世界事实。

当前已实现第一条安全纵向切片：

- `POST /module-runs/{run_id}/director/world-expansion-proposals` 只允许当前团 KP 调用；
- 模型必须返回补全类型、建议、时代/场景理由、置信度、假设、潜在冲突和至少两个替代
  方案；
- 模型调用前先做确定性 `world_gap` 检查，调用后在 `BEGIN IMMEDIATE` 内重新检查 KP
  会话、活动模组 ID、来源哈希、运行版本、缺口结论和当前 World Fact head 哈希；
- 同一运行版本、事实快照和标准化玩家意图使用 SHA-256 指纹复用已有草稿；
- `proposal_actions.world_expansion_basis` 保存来源和候选快照，`context_assemblies` 保存
  实际模型上下文；
- 草稿本身不携带事件、记忆、NPC 更新或地图移动。批准仅采用 KP 可覆写的公开叙述；
  把接触结果提升为严格 World Fact 或世界实体属于下一阶段；
- `requested_expansion_kind` 默认为 `environment`，动态支线与锚点桥接必须由调用方显式选择。
  模型若返回不同类型会在持久化前被拒绝；窄传输编译器只可规范化局部引用、纠正引用来源标记、
  删除未授权 source ID 或用已有 proposal 补空叙述，不能替模型选择目录内容。

审批时会再次核对模组运行版本、模组来源哈希和 World Fact head 哈希。任何一项变化都会
返回冲突，要求 KP 拒绝旧草稿并重新分析。

### D. 事实落地与可达性

批准并实际接触后，以追加事件写入 World Fact，并更新锚点可达性投影。重启后从模组
版本和事件流重建，不依赖模型“记忆”。

当前已实现第一条事实落地纵向切片：

- `POST /kp/proposals/{proposal_id}/world-expansion-encounters` 只接受当前团的活动 KP，
  且候选必须已经批准；
- 请求必须携带稳定幂等键、一至八条严格事实和实际接触摘要；可同时创建 NPC，或引用
  已经与当前团关联的全局 NPC；
- 获批 `entity_bindings` 必须逐项填写具体名称、描述和可见性；请求不能改变原型、种类、职位或
  关系。`WorldEntityMaterializer` 同时服务 AI KP 与真人 KP，并把来源实体和新生成实体投影到
  `campaign_world_entities`，把关系写入 `campaign_world_entity_relations`；
- NPC 是跨团可复用的 Actor；地图棋子只是该 NPC 在某张地图上的 Token，不会复制
  NPC 身份；
- 地图放置只能引用同团、当前已审核 `MapSpec` 中存在的地点，不能借“实际接触”接口
  暗中创建新地点或绕过地图 revision；
- 接触事件、事实账本、NPC 团关系、地图棋子、落地收据和审批 action 在
  `BEGIN IMMEDIATE + SAVEPOINT` 内原子写入；任一步失败全部回滚；
- `(campaign_id, idempotency_key)` 与 `proposal_id` 都有唯一约束。同一命令重试返回同一
  收据；相同键对应不同内容或同一候选换键重放会冲突；
- 每条事实直接引用 `world_expansion.encountered` 事件，并保存候选、模组版本、来源
  哈希和运行 ID，能够回答“这条事实为什么存在”；
- 前端对已批准候选显示第二次确认表单；只有此时才填写桌面实际发生内容、NPC 和已有
  地图地点。类型化 NPC 可直接作为地图棋子和调查员接触对象，非 NPC 实体不会被伪装成 Actor；
  完成后显示不可覆盖的落地收据；
- `GET /campaigns/{campaign_id}/world-entities` 返回按身份过滤的实体图。后续模型上下文以玩家
  行动为种子召回至多八个实体及可见的一跳邻居；关系 SQL 要求两端均对当前视角可见，避免从
  公开实体的关系摘要泄露隐藏实体。

跨团 NPC 安全复现纵向切片现已实现：

- 接触确认可附带当前团已批准的稳定调查员 ID 和“他们一起做了什么”的简短摘要；
- `investigator_npc_encounters` 追加保存调查员、NPC、来源事件、团内时间和落地收据，
  不把经历塞进可覆盖 JSON 或模型记忆；
- KP 候选接口只返回当前团已批准调查员亲自接触过、且尚未加入本团的 NPC；只暴露人物
  公共身份和该调查员自己的接触摘要；
- 旧 NPC 的最终选择会在写事务内重新核对关系证据。猜到另一团 NPC ID、同一玩家换了
  另一个调查员，或前端伪造参与者，都不能越过该检查；
- 复现成功后，当前团 NPC 关系、新接触经历、事实、收据和可选地图棋子一起提交；重试
  不会重复经历，地图失败会全部回滚。

确定性 NPC 出场门控纵向切片现已实现：

- KP 可为已关联 NPC 配置生命周期、出生/死亡年份、活动年代、地点别名和职业标签；
- 候选会用团时间先排除年代上不可能出现的人物。资料缺失不会由 AI 编造，而会标为
  `needs_review` 并向 KP 解释缺少什么；
- 每团默认只有一个回归 NPC 名额，可在 `0..50` 范围内明确调整；地点和职业匹配可分别
  设置为严格模式；
- 候选接口返回可读理由、警告和剩余名额，但最终接触仍会在 `BEGIN IMMEDIATE` 后重新
  校验关系、年代、实际地图地点、职业上下文和预算；
- 成功后 `npc_reappearance_appearances` 与事实、NPC 团关系、地图棋子和接触经历在同一
  事务提交。失败或并发超额不会消耗名额。

当前地点使用 KP 明确填写的规范化别名，不从自由文本伪造经纬度或“精确距离”。未来若
加入地理坐标，应采用 RFC 7946 GeoJSON。团级地点图谱现已用明确的正整数分钟、交通
方式、方向和开放状态表达路线；Dijkstra 计算会给出完整最短路径。没有路线时不会用
地图像素或语言模型猜测距离。

NPC 独立工作台现可编辑人物可用性、复现策略、地点节点、路线和最短路径预检。锚点
可达性自动重算和事实修订 UI 仍属于后续切片。

地点图谱不持续模拟或向玩家公开 NPC 的每一步移动。现有“NPC 暗骰”先用这里的确定性
门控生成可达地点集合，再在 KP 可见范围内决定是否出现及最终地点：

- `POST /campaigns/{campaign_id}/npc-hidden-appearances` 只允许当前团 KP 调用；
- 最多二十个候选地点只执行一次多源 Dijkstra，而不是每个地点重复算路；
- 出现骰使用服务端 `secrets.randbelow` 生成一次 d100；只有出现且有多个合法地点时才
  额外生成一次权重选择；
- `(campaign_id, idempotency_key)` 与命令哈希保证网络重试不会重骰，同键改参数会拒绝；
- 私密收据保存原始骰、阈值、合法地点和触发者，玩家端没有读取接口；可公开投影只有
  `appears` 和最终地点；
- 暗骰不得绕过年代、生命周期、关系、职业和旅行上限，也不消耗回归 NPC 预算；
- 暗骰结果不是正式遭遇，不移动棋子、不生成 NPC、不写 World Fact。只有玩家实际接触
  后，才使用既有接触落地接口原子写入事实。

这套机制适用于偶遇 NPC；环境遭遇表、可选支线时机和纯气氛变体以后可以复用同一
私密解析边界。权限、CoC 检定数学、线索可用性、地图移动合法性、模组锚点与正式事实
禁止被这种随机层替代。

## Real-case 验收

使用一份只描述“小镇”但没有描述警务设施的 1920 年代模组：

1. 玩家询问警察局时，系统先检索 Canon、Anchor 和当前事实。
2. 系统根据地区和规模提出警察局、治安官办公室或邻镇辖区等候选，而非固定生成。
3. 候选包含理由、来源、时代条件、可能 NPC、可用服务和冲突报告。
4. 若模组已说明当地没有警力，候选被拒绝且不会写入任何正式状态。
5. 若候选获批但玩家未前往，只保留审批记录，不宣称已经遇见相关 NPC。
6. 玩家实际到达后，地点和接触事件成为追加式世界事实，重启后保持一致。
7. 同一关键线索可通过原路线或替代路径保持可达，但不会重复发放或提前泄露真相。
8. 获批机构与值班人员在接触时成为独立类型化实体并建立所属关系；下一回合使用同义表达时，
   可通过已批准候选语义与一跳关系召回两者，玩家视角仍看不到 KP/secret 邻居。

使用已配置真实模型的隔离后端时，可运行：

```bash
.venv/bin/python scripts/scene_director_realcase.py \
  --base-url http://127.0.0.1:8003 \
  --world-expansion
```

## 设计依据

- [Foundry VTT Scenes](https://foundryvtt.com/article/scenes/) 将当前活动场景与世界中可探索
  区域分开管理；本项目同样把场景游标与生成候选分离。
- [Foundry VTT Journal Entries](https://foundryvtt.com/article/journal/) 使用独立页面、权限
  和场景链接组织 GM 信息；补全候选因此保留独立审计与展示边界，不写回模组正文。
- [Unstructured Partitioning](https://docs.unstructured.io/open-source/core-functionality/partitioning)
  区分 PDF/DOCX 文档元素，并支持 PDF OCR、布局和图片块提取；旧 DOC 会先在隔离
  进程内转换为 DOCX，再进入同一抽取边界。
- [Unstructured Chunking](https://docs.unstructured.io/open-source/core-functionality/chunking)
  说明按标题保持章节边界，并保留 chunk 对原始元素、页码、坐标和图片的引用。
- [Microsoft GraphRAG indexing](https://microsoft.github.io/graphrag/index/overview/)
  将实体、关系和主张抽取与向量索引作为可配置的派生管线。
- [AWS Event Sourcing pattern](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/event-sourcing-pattern.html)
  使用不可变追加事件作为单一事实来源，并通过投影或重放恢复当前状态。
- [Azure Event Sourcing pattern](https://learn.microsoft.com/azure/architecture/patterns/event-sourcing)
  要求派生投影可幂等重放；本项目因此将落地收据与命令哈希分开保存。
- [W3C PROV-O](https://www.w3.org/TR/prov-o/)
  用 Entity、Activity、Agent 及派生关系表达来源；接触事件在这里是事实产生的
  Activity，候选和模组来源是派生依据。
- [Foundry VTT Actor](https://foundryvtt.com/api/v11/classes/client.Actor.html)
  将长期 Actor 与 Scene 内嵌 Token 区分；本项目同样让 NPC 身份独立于地图棋子。
- [SQLite Transactions](https://www.sqlite.org/lang_transaction.html)
  `BEGIN IMMEDIATE` 在写入前取得写事务，配合唯一约束和 savepoint 实现串行化确认。
- [OpenFGA Concepts](https://openfga.dev/docs/concepts)
  将授权表达为主体、关系与对象的显式元组；这里对应“稳定调查员曾接触全局 NPC”。
- [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
  要求默认拒绝、逐请求对象级校验，且不能把难猜 ID 当成权限控制。
- [Foundry VTT Basic Dice](https://foundryvtt.com/article/dice/)
  将 Blind Roll 定义为仅 GM 可见的可见性模式；暗骰不应另造一套游戏规则。
- [Python `secrets`](https://docs.python.org/3/library/secrets.html)
  提供操作系统随机源和无偏的 `randbelow`；服务端暗骰不接受客户端种子。
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
  要求高敏感级别数据不得进入权限不足的日志；原始暗骰因此留在 KP 私密收据中。
- [SQLite recursive CTE](https://www.sqlite.org/lang_with.html)
  使用递归公共表表达式遍历树或图；当前锚点检查因此不需要额外图数据库。
- [W3C PROV Model Primer](https://www.w3.org/TR/prov-primer/)
  派生实体保留来源与生成责任链；本项目将已批准知识候选作为图节点和边的直接来源。
