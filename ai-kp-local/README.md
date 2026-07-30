# AI KP Local

本地优先的 AI 跑团平台。它将团会话、调查员、地图、模组、NPC、长期记忆和规则知识
保存在本机，通过可替换的 OpenAI-compatible 模型让 AI 担任 KP，也允许人类 KP
随时审核、接管或覆写。

当前产品以 **CoC 第七版**为唯一可执行规则系统。架构已经隔离规则集，但上传其他
规则书只会建立独立知识库，不会自动获得一个可信的可执行规则插件。
系统目前按“通用平台内核 + 确定性规则插件 + proposal-only AI Skill + 模组内容包”
划分；团会固定精确规则、角色 Schema 和事件 Schema 版本。

> 项目仍处于本地开发阶段，适合单机或受信任局域网测试，不应直接暴露到公网。

## 现在能做什么

- 创建团、开启会话，并通过逐席单次邀请码建立稳定玩家身份。
- 玩家手工创建或从 Excel 安全导入调查员；KP 可退回修改或批准精确的不可变版本。
- 在游玩页同时查看本人角色卡、队友公开摘要、中央地图与右侧行动/聊天区域。
- 生成并持久保存 MapSpec、时代化 SVG/图片候选、路线、棋子和移动历史；重启后可恢复。
- KP 可修改地点/路线并保存不可变 MapSpec revision；雾区作为独立覆盖层自由圈画、
  删除和逐区揭开，并在新 revision 中按画布比例继承。玩家只接收当前版本的隐藏遮罩。
- KP 可为多枚棋子保存已批准的分队路线，玩家可提交本人路线候选；实际移动逐段核销，
  规划本身不会提前移动棋子。
- 导入 PDF、DOC 或 DOCX 格式的 KP 本，保留正文、表格、图片、页码/段落来源与剧透边界。
- 显式选择当前活动模组，用场景导演记录节奏、地点、线索/锚点状态和审计；玩家意图先做当前剧透范围内的只读预检，再决定引用原文、暂停或提出世界补全。
- 对确认的世界缺口调用本地模型生成带理由、风险和替代方案的审批草稿；模组运行或世界事实变化后，旧草稿会拒绝审批。
- 世界补全获批后仍需确认玩家确实接触；系统才会用一笔幂等事务追加严格 World Fact、
  记录或复用 NPC，并把 NPC 棋子放到已审核地图地点。
- 连续世界反应可保存为带进入条件、角色意图、候选效果、完成条件和剧情锚点保护的动态
  支线；批准时只建立计划，实际接触后才激活，逐节拍重验当前事实和模组图可达性，所有
  正式变化仍回到 World Fact/NPC/地图确认接口。
- 记录事件、主要/支线记忆、NPC 关系和世界时间；稳定调查员可凭带来源的旧接触记录，
  在新团中安全选择并再次遇见合理 NPC。确定性门控会复核生命周期、年代、地点/职业
  标签和每团出场预算；独立 NPC 工作台可编辑这些档案，并以团级地点图谱和整数分钟
  路线计算可解释最短旅行时间。KP 还可按触发事件执行私密 NPC 暗骰：所有候选地点只
  计算一次可达性，幂等回执防止重试重骰，且结果在实际接触前不会移动棋子或写入事实。
  无法证明的资料明确交给 KP 复核而不由 AI 编造。
- 用 SQLite 原文页块 + MiniRAG 隔离索引保存规则书知识；JSON 规则对象仍需来源校验、
  KP golden case 审核和确定性引擎验证。
- 将 AI 回合限制为结构化草稿；只有人类 KP 批准后，事件、记忆、NPC 更新、检定和
  地图移动才会原子写入正式状态。
- 每个玩家行动先保存目标、手段、对象、可行性、结算方式与效果上限；不可行目标不掷骰，
  大成功也不能跨越地图邻接、赋予目标不存在的反应或改写模组 Canon。
- 正式检定提供全桌、掷骰者与 KP、仅 KP 三种服务端可见模式，且 KP 始终可见。获准
  查看者会看到总值、逐颗百分骰、奖惩骰候选值和判定结果；原始骰面、规则版本与结果
  指纹进入追加式审计记录。
- 对抗检定持久保存双方原始检定和服务器裁决，并进入同一后果指纹；场景推进支持
  安全暂停、人类 KP 接管和明确交还。统一控制闸门覆盖回合、检定后果、世界补全与
  团后摘要；暂停会拒绝新调用、取消同进程在途调用，并在返回落库前重验持久控制快照。
- 玩家手册/线索支持草稿、揭示、撤回、固定和逐席已读；KP 从同团事实、NPC、地图、
  地点和模组实体候选中建立经服务端复核的关联。
- 模拟团既支持轻量秘密投影 contract，也支持 `product-service-replay.v4`：在 SQLite
  savepoint 沙盒中实际创建并批准调查员，调用 CoC7 角色状态、地图、分队路线、提案、
  线索和遭遇服务。临时世界会回滚，只保存可重放指纹及分层的 PL、KP、模型、规则引擎
  和系统日志。Debug 台显示进程内延迟、锁等待、检索质量与秘密泄露告警。
- 通过一次性实时票据、同源 WebSocket 和 SQLite outbox 同步多人状态并过滤 KP 信息。
- 在独立模型页面检测服务返回的真实模型 ID，切换远程 OpenAI-compatible 服务，或在
  Apple Silicon 上启动本机 MLX 模型目录。
- 创建包含 SQLite、地图/KP 本资产和规则索引的可校验本地备份。

完整交付状态不在 README 中重复维护。运行中的唯一事实源是
[`src/ai_kp/planning/capabilities.py`](src/ai_kp/planning/capabilities.py)，也可通过
`GET /capabilities` 或前端“功能规划”页查看 `available`、`partial` 和 `planned` 项。

## 技术栈

- 后端：Python 3.11+、FastAPI、Pydantic、SQLite
- 前端：React 18、TypeScript、Vite、Vitest、Playwright
- AI：OpenAI-compatible Chat/Image API；可选 MLX 本地运行时
- 知识：可追溯 SQLite 原文、可重建 MiniRAG 向量索引、受审核 JSON 规则对象

## 快速开始

### 1. 启动后端

```bash
git clone https://github.com/eThAN02010506/board_game.git
cd board_game/ai-kp-local

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env

uvicorn ai_kp.api.main:app --host 127.0.0.1 --port 8002 --reload
```

后端地址：

- `http://127.0.0.1:8002/`：仅限本机管理员的 Debug 调试台
- `http://127.0.0.1:8002/health`：健康检查
- `http://127.0.0.1:8002/docs`：OpenAPI 交互文档

规则书索引、本机 MLX 模型分别使用可选依赖组：

```bash
python -m pip install -e ".[rulebook,dev]"
python -m pip install -e ".[local-model]"
```

仓库提供由 Python 3.13/macOS Apple Silicon 生成并带下载哈希的 `pylock.toml`。仅在
同类环境需要严格复现时使用：

```bash
uv venv --python 3.13
uv pip sync pylock.toml
```

更换 Python 版本或操作系统时应重新解析依赖，不能把其中的 macOS/MLX wheel 当成
跨平台锁定结果。

### 2. 启动前端

另开终端：

```bash
cd board_game/ai-kp-local/apps/web
corepack enable
pnpm install
VITE_BACKEND_TARGET=http://127.0.0.1:8002 pnpm run dev
```

打开 `http://127.0.0.1:5173/`。前端统一使用 `pnpm`，请勿提交
`package-lock.json` 或 Yarn lockfile。

Vite 默认代理到 `http://localhost:8000`；只有后端使用其他端口时才需要设置
`VITE_BACKEND_TARGET`。HTTP 与 WebSocket 都经过同源 `/api` 代理。

### 3. 连接大模型

进入前端“模型设置”，填写服务地址并先执行“检测服务与模型”。也可以编辑 `.env`：

```env
AI_KP_LLM_BASE_URL=http://127.0.0.1:8001/v1
AI_KP_LLM_API_KEY=local
AI_KP_LLM_MODEL=<GET /v1/models 实际返回的 data[].id>
```

地址可省略末尾 `/v1`，后端会规范化。无鉴权服务的 API Key 可以留空；不要根据
`.gguf` 文件名猜测模型 ID。只有模型发现和一次完整 `/v1/chat/completions` KP 回合
都成功，才算连接完成。

地图图片模型独立配置，必须实现 `/v1/models` 与 `/v1/images/generations`，并返回
经过后端校验的 Base64 PNG/JPEG。未配置图片模型时，确定性 SVG 地图仍可完整游玩。
详见 [`docs/MODEL_CONFIGURATION.md`](docs/MODEL_CONFIGURATION.md) 与
[`docs/MAP_GENERATION.md`](docs/MAP_GENERATION.md)。

## 第一次真实游玩

建议用一个 KP 浏览器会话和一个玩家浏览器会话完成以下流程：

1. 管理员创建 Campaign，KP 开启活动会话。
2. KP 创建命名席位，并只把该席的一次性邀请码交给对应玩家。
3. 玩家认领席位，创建或导入调查员，保存不可变草稿并提交给当前团。
4. KP 审查字段差异；退回并填写修改意见，或批准后绑定到该玩家席位。
5. KP 导入规则书和 KP 本，等待解析完成，审核章节可见性、来源候选和剧情锚点。
6. KP 显式启动一个活动模组，设置当前场景与已解锁剧透。
7. KP 生成地图、放置绑定角色的棋子并发布；玩家只能读取已发布的安全投影。
8. 玩家提交行动；KP 手工创建或让 AI 生成草稿，再批准或拒绝。
9. AI 草稿要求检定时，由确定性 CoC7 引擎解析数字骰/实体骰；投骰以全桌、玩家与
   KP、仅 KP 三种服务端可见模式持久保存，KP 始终可见，获准查看者可展开逐骰明细
   并重放审计。
10. KP 在记忆工作台生成当前会话的团后摘要候选，核对来源、可见性和角色归属后逐条
    批准或拒绝；只有批准项进入正式时间线。
11. 关闭并重启服务，确认会话、活动模组、地图、棋子、事件、记忆和 NPC 都可恢复。

会话访问令牌和管理员口令只保存在浏览器 `sessionStorage`。稳定玩家令牌存入
`localStorage`，仅用于管理本人调查员和恢复已认领席位，不能替代团会话 Bearer
权限。任何令牌都不会写入 URL。

## 数据与安全边界

- SQLite 事件流和结构化实体是权威记录；AI 叙述、摘要、向量索引和浏览器状态都不是。
- 模组是带来源的权威大纲，而非完整世界清单。AI 可以提出合理世界补全，但不能覆盖
  Canon、剧情锚点或已确认事实。
- AI 输出永远先成为草稿；审批前不能改变正式事件、记忆、NPC、检定或地图位置。
- 通用随机层只记录数字/实体骰的骰面和完整性指纹；CoC7 插件负责解释百分骰候选、
  成功等级和效果，旧 `raw_dice` 投影继续兼容。
- 规则书可以提取术语、资源、状态、行动经济、成长和 Skill 指南候选，但所有候选必须
  保留页码证据；`skill_guidance` 只能作为 `reference_only`，不会自动安装或写状态。
- 团后摘要按冻结事件窗口和 SHA-256 指纹幂等生成；模型调用在数据库事务外执行，
  候选必须引用窗口内事件，KP 审核批准后才原子写入正式记忆。
- 世界补全的“批准候选”和“桌面实际接触”是两个边界；只有后者生成带来源的事实落地
  收据，且不能借此创建未审核地图地点。
- 动态支线是可重放的计划进度，不是事实来源；节拍完成、失败或跳过都不会直接写入
  World Fact、NPC 或地图。
- 玩家身份、受控 PC、地图位置和可见性全部由服务端令牌与数据库关系推导，不信任
  客户端提交的 `actor`、`pc_id` 或 KP 视角参数。
- 规则书、模组和团记忆使用不同存储命名空间，防止跨规则集、跨团和剧透污染。
- PDF、DOC/DOCX 与模型响应有尺寸、超时和结构限制；旧 DOC 通过无 shell 的
  LibreOffice 子进程转换，原文件与 SHA-256 始终保留。
- 地图图片只接收玩家安全投影，不能看到秘密地点、KP 备注、隐藏路线或棋子。

默认 `AI_KP_DEPLOYMENT_MODE=local` 只适合本机开发。局域网或反向代理必须至少设置：

```env
AI_KP_DEPLOYMENT_MODE=lan
AI_KP_ADMIN_TOKEN=<足够长的随机口令>
AI_KP_CORS_ORIGINS=https://你的前端域名
AI_KP_TRUSTED_HOSTS=你的域名或局域网地址
```

应用自身不提供 TLS；LAN 环境仍应通过 HTTPS 代理访问。当前没有完整的公网账号、
多因素认证、持久封禁和可信代理体系，因此不得直接暴露到互联网。

## 项目结构

```text
.
├── src/ai_kp/
│   ├── bootstrap/          # 配置、依赖装配、生命周期
│   ├── api/                # HTTP/WebSocket、鉴权、DTO、分域路由
│   ├── application/        # 用例与事务编排
│   ├── platform/           # 规则无关的记忆、模组、场景与随机证据
│   ├── evaluation/         # 可重放模拟团与确定性轨迹断言
│   ├── director/           # AI KP 上下文、proposal-only Skill 与草稿编排
│   ├── rule_authoring/     # 规则候选、来源校验、审核与确定性执行
│   ├── rulesets/           # 显式规则注册表与 CoC7 插件
│   ├── infrastructure/     # SQLite、MiniRAG、模型、图片、文档、实时适配器
│   └── planning/           # 可机读能力目录
├── apps/web/src/
│   ├── app/                # 路由、布局与应用组合
│   ├── api/                # HTTP 客户端与 TypeScript DTO
│   ├── auth/               # 浏览器凭据边界
│   ├── session/            # 会话与地图选择恢复
│   ├── realtime/           # WebSocket 生命周期
│   ├── features/           # 调查员、模组、地图、行动、规则等页面
│   └── ui/                 # 共享 UI 原语
├── tests/                  # 后端回归与安全测试
├── scripts/                # real-case、记忆评估、规则书验收、备份恢复
└── docs/                   # 深入设计、边界和操作文档
```

稳定 ASGI 入口是 `ai_kp.api.main:app`，装配根位于
`src/ai_kp/bootstrap/composition.py`。依赖方向和事务规则见
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。
控制权、资料揭示、地图编辑、评测与监控边界见
[`docs/CONTROL_AND_EVALUATION.md`](docs/CONTROL_AND_EVALUATION.md)。

## 开发与验证

后端：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m compileall -q src tests scripts
```

前端：

```bash
cd apps/web
pnpm test
pnpm run build
pnpm exec playwright install chromium   # 首次执行 E2E 前
pnpm e2e
```

连接运行在 `8002` 的后端和运行在 `8001` 的真实模型服务后，可执行完整 HTTP
real-case；脚本不会打印凭据：

```bash
.venv/bin/python scripts/realcase_smoke.py
```

规则书摄取可使用：

```bash
.venv/bin/python scripts/rulebook_realcase.py /path/to/rulebook.pdf
```

提交前还应运行 `git diff --check`。自动测试通过不代表真实模型、OCR、LibreOffice
或图片服务可用，这些外部依赖必须在目标机器上单独做 real-case。

最近一次完整验证（2026-07-31）：

- 后端：403 项测试与 50 个子测试通过；图片 provider 的 localhost 真实 HTTP 测试
  已在允许回环绑定的隔离环境中单独通过。
- 前端：24 个 Vitest 文件、105 项测试通过；TypeScript 与 Vite 生产构建通过。
- 浏览器 real-case：OldOnes 测试团完成公开 D100 投掷；总值、成功等级、逐骰明细、
  服务器时间和 SHA-256 结果指纹均可见并与持久化记录一致。
- 局域网 GPT-OSS-20B MXFP4：模型发现与结构化 chat completion 通过；gpt-oss 使用
  低 reasoning effort，并拒绝正文为空的截断响应。
- 《常暗之厢》关键 A 结局已通过真实产品服务黄金回放；测试包含 7 号车厢自由行动、
  严格车厢邻接和“不可能目标不因大成功改写世界”的裁定，且不会把临时角色、地图、
  线索或遭遇写入正式团。

## 当前限制

- 可执行规则集目前只有 CoC7；上传 Cyberpunk RED 等规则书不会自动生成可信规则插件。
- `/rulesets` 只列出显式安装的确定性系统，`/ai-skills` 只列出无权直接落库的提案型
  Skill；规则页会把它们与已上传知识库分开展示。
- CoC7 核心游玩状态机已覆盖战斗轮、战技/围攻、伤害与治疗、理智、追逐、成长标记及
  永久变更确认；状态、骰值、规则来源和版本均可重放。详见
  [`docs/COC7_GAMEPLAY_STATE_MACHINE.md`](docs/COC7_GAMEPLAY_STATE_MACHINE.md)。
- 枪械射程、连射、掩体和全自动射击等武器细项目前仍由 KP 通过检定修正输入，
  不允许 AI 绕过确定性状态接口直接写 HP。
- 对抗检定的顺序重试已幂等，但并发平局重掷仍需在取得写锁后重新检查 child，避免
  两个同时请求中的后一个得到冲突响应。
- 模组知识图谱、运行态和受约束动态支线生命周期已可用；自动结构校订、跨多条支线的
  全局合理性评分，以及模型自动选择支线节拍仍待整团重放验证。
- 世界事实账本、独立 `/facts` 工作台与 AI `proposed_facts` 原子审批已经接通；默认
  筛选和人工确认负担仍需真人团验证。
- 所有游戏导演文本模型入口已接入统一控制闸门；规则书/模组离线抽取、图片背景生成和
  模型健康探测属于准备/诊断入口，不随某一活动模组的导演控制权停止。
- 调查员已具备跨 Campaign 的安全时间线投影、单活动主线约束、显式平行分支和
  玩家确认的永久里程碑版本；地图发布差异预览仍属于后续能力。
- 分队路线、自由多边形雾层、revision 继承和线索关联已形成 API/UI 闭环；复杂多层地图、
  按小队分别揭示和拖拽式房间布局仍待实现。
- 产品服务回放已有《常暗之厢》关键 A 结局 golden case，覆盖调查员审批、SAN、
  地图/路线、行动裁定、提案、线索和追逐；其失败/战斗/减速结局、NPC 跨团重现、记忆召回、
  多模型/量化对比和外部依赖故障库仍待扩充。
- 授权矩阵已经覆盖本阶段新增资源的主要角色与跨团访问，但尚未从 OpenAPI 自动生成
  全路由、全方法、全角色组合；故障注入目前主要验证事务中途失败回滚。
- 中文图片 OCR 取决于本机 Tesseract `chi_sim`；否则应使用受支持的视觉模型。

请以“功能规划”页或 `GET /capabilities?include_available=false` 查看最新缺口，不要根据
这份概览推断某个计划接口已经存在。

## 文档索引

- [产品需求、用户流程与阶段验收](docs/PRODUCT_REQUIREMENTS.md)
- [架构与事务边界](docs/ARCHITECTURE.md)
- [架构骨架与迁移约束](docs/ARCHITECTURE_SKELETON.md)
- [调查员、Excel 与审批模型](docs/CHARACTER_SHEET_MODEL.md)
- [检定与结果驱动草稿](docs/CHECK_RESOLUTION.md)
- [模型配置与本机 MLX](docs/MODEL_CONFIGURATION.md)
- [地图生成、缓存与发布](docs/MAP_GENERATION.md)
- [PDF/Word 模组导入](docs/MODULE_DOCUMENT_IMPORT.md)
- [场景导演与只读意图分析](docs/SCENE_DIRECTOR.md)
- [规则书双存储与三层校验](docs/RULEBOOK_KNOWLEDGE.md)
- [规则系统边界](docs/RULESET_BOUNDARY.md)
- [规则插件 v1 契约](docs/RULESET_PLUGIN_SPEC.md)
- [本地规则来源](docs/RULES_REFERENCE.md)
- [长期记忆验收](docs/MEMORY_TESTING.md)
- [世界补全约束](docs/WORLD_EXPANSION.md)
- [动态支线生命周期](docs/DYNAMIC_BRANCHES.md)
- [《常暗之厢》真实服务回放](docs/REALCASE_CHANG_AN_ZHI_XIANG.md)
- [世界事实账本](docs/WORLD_FACT_LEDGER.md)
- [备份与恢复](docs/BACKUP_AND_RECOVERY.md)
- [Debug 调试台](docs/DEBUG_CONSOLE.md)
- [能力目录维护规则](docs/ROADMAP.md)

## 设计原则

1. 事实进入数据库，叙事由模型生成。
2. 事件流是权威记录，摘要和向量索引可以重建。
3. 玩家视角、KP 视角与秘密信息分开存储并在服务端过滤。
4. NPC 再出现必须通过时间、地点、关系、合理性和叙事预算判断。
5. 人类 KP 可以随时接管、覆写、冻结或补写状态。
6. 数值规则由可重放的确定性代码执行，AI 只建议检定和叙事。
7. 独立功能使用独立页面；`/play` 只组合当下游玩必须同时看到的信息。
