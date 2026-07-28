# AI KP Local

本项目是一个本地优先的 AI 跑团平台 MVP。目标是让玩家导入 KP 本、创建角色、记录团内事件、维护 NPC 与长期记忆，并通过任意 OpenAI-compatible 大模型接口让 AI 扮演 KP。应用与数据库可完全本地运行，大模型是可替换的外部提供者。

KP 本被视为带来源的权威大纲而非完整世界清单。AI 可以为玩家走出原路线后的合理世界
缺口提出地点、NPC 和支线候选，但不得覆盖模组真相或已经确认的世界事实；详细边界见
[`docs/WORLD_EXPANSION.md`](docs/WORLD_EXPANSION.md)。

## 当前可用能力

- FastAPI 后端入口
- SQLite 本地数据库与事件流
- Campaign、PC、NPC、Memory、World Time 的基础数据模型
- KP 本/模组内部文本切块、秘密标签和剧透边界；面向 KP 的文件导入将只接收 PDF/Word
- 时代化地图生成；MapSpec、修订、校验报告、SVG、地点、路线、棋子与图片候选均可本地持久化
- 可选 OpenAI-compatible 图片模型；只接收玩家安全投影，图片失效时自动回退到完整可玩的确定性 SVG
- 线下跑团式棋子移动，以及基于棋子版本的并发移动冲突保护
- AI KP 草稿审批流：草稿、批准、拒绝、覆写，批准后才写入事件/记忆
- 严格结构化 AI 输出：叙述、检定、事件、记忆、NPC 更新和地图移动均校验
- 可解释的 AI 上下文组装：记录纳入/排除来源、剧透边界和 token 估算
- NPC 跨本再出现的候选判断
- 玩家角色主要事件与支线事件记忆
- OpenAI-compatible LLM 适配层
- 独立模型设置页；可运行时切换 OpenAI-compatible 地址、发现真实模型 ID，或提交本机 MLX 模型目录并启动/停止本地服务
- React/Vite 前端工作台；启动时自动恢复当前浏览器会话及该 `campaign + role` 上次打开的地图
- 独立调查员页面；本地长期玩家档案、安全 Excel 导入预览、规则重算与不可变草稿版本
- 参考 `COC空白卡.xlsx` 的 67 项完整技能表；支持本地职业模板推荐加点、手动微调、保存、专攻、搜索、预算和成功率即时计算，技能名悬停 3 秒显示简介
- 玩家提交调查员、KP 退回修改/批准、批准版本绑定玩家席位与团内 HP/SAN/MP/幸运状态
- 规则书双存储：SQLite 页码原文 + MiniRAG 本地索引；JSON 规则对象通过 Schema、引用、冲突三层校验后才可由确定性 DSL 执行
- 分页式产品导航；游玩页同时呈现本人角色卡、队友公开摘要、中央地图和右侧滚动行动区
- 团会话、逐席单次邀请、稳定玩家身份与 KP/玩家服务端权限校验（共享加入码仅保留兼容）
- 可重放的 CoC7 普通/困难/极难检定；支持数字骰、实体骰、奖惩骰、暗骰、孤注一掷和有理由的 KP 覆盖
- 显式规则系统注册表；当前只安装 CoC7，未知系统和单纯上传的规则书不会被误当作可执行规则
- 短期单次实时票据、同源 WebSocket、SQLite outbox、角色可见性过滤和断线重连
- 玩家行动队列与 `submitted -> reviewed -> resolved/rejected` 生命周期
- 地图 `draft -> published` 发布边界
- API 合同、应用服务、权限、数据库迁移、实时同步与核心域的自动测试
- 后端根路径 Debug 调试台；集中查看模型、SQLite、路由、请求、日志，并提供 API、Excel 与 WebSocket 探针
- 可校验的完整本地备份；在线快照 SQLite、地图资产与规则索引，停服后安全恢复
- SQLite FTS5 trigram 记忆候选索引；短词或非连续中文查询自动回退到原有词法召回
- `local`/`lan` 部署模式；LAN 模式启用 Host allowlist、敏感操作限流与管理员口令强制校验

## 快速开始

```bash
cd ai-kp-local
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
uvicorn ai_kp.api.main:app --reload
```

仓库同时提供由 Python 3.13、macOS Apple Silicon 环境生成的
`pylock.toml`，其中包含核心、开发、规则书和本地 MLX 依赖以及完整下载哈希。
需要在同类机器上严格复现当前解析结果时可使用：

```bash
uv venv --python 3.13
uv pip sync pylock.toml
```

更换 Python 版本或操作系统时应重新生成锁文件，不能把其中的 macOS/MLX
wheel 当成跨平台解析结果：

```bash
python -m pip lock ".[dev,rulebook,local-model]" -o pylock.toml
```

需要导入和索引规则书时安装独立的本地 MiniRAG 依赖组：

```bash
python -m pip install -e ".[rulebook,dev]"
```

前端：

```bash
cd apps/web
corepack enable
pnpm install
pnpm run dev
```

需要直接从本机 MLX 模型目录启动服务时，额外安装：

```bash
python -m pip install -e ".[local-model]"
```

前端统一使用 `pnpm`，依赖版本由 `apps/web/pnpm-lock.yaml` 锁定。请不要在仓库中生成 `package-lock.json` 或 Yarn lockfile。

Vite 默认把 HTTP 与 WebSocket 的 `/api` 请求代理到 `http://localhost:8000`。需要连接另一台测试后端时，在启动前指定目标；浏览器仍只连接当前前端源：

```bash
VITE_BACKEND_TARGET=http://127.0.0.1:8000 pnpm run dev
```

如果像当前开发环境一样把后端运行在 `8002`，需要在启动 Vite 时明确指定代理目标：

```bash
uvicorn ai_kp.api.main:app --host 127.0.0.1 --port 8002
cd apps/web
VITE_BACKEND_TARGET=http://127.0.0.1:8002 pnpm run dev
```

访问后端根地址（例如 `http://127.0.0.1:8002/`）会打开本机 Debug 调试台，而不是返回 JSON 404。调试台只允许本机管理员访问，数据库预览会隐藏令牌、哈希、密码和 API Key；详细说明见 [`docs/DEBUG_CONSOLE.md`](docs/DEBUG_CONSOLE.md)。

默认配置只适合在运行后端的同一台机器上开发。浏览器打开前端后，可以按下面的真实流程验收：

1. 点击“连接后端”，创建测试团；前端会随即开启该团的 KP 会话。已有有效会话时，重载页面会自动恢复身份和该角色上次打开的地图。
2. KP 为每个玩家创建单独席位（可预留角色），只分享该席的一次性邀请码。玩家首次认领时建立本机长期身份，以后可从“我的历史席位”恢复活动团。
3. 玩家保存不可变草稿版本并提交给当前团 KP。KP 可退回并留下修改意见，或批准当前版本后绑定到玩家席位；新版本审核期间不会覆盖已批准版本。
4. KP 生成地图并放置绑定角色的棋子。新地图默认是草稿；发布后，玩家页面会自动显示地图，后续棋子移动也会自动同步，无需手动刷新。
5. 玩家只能移动自己角色的棋子，并可在“玩家行动”中提交行动。行动会自动出现在 KP 队列；KP 选中后创建手工草稿或调用本地 AI。
6. 行动被草稿认领后进入 `reviewed`；KP 批准草稿后变为 `resolved`，拒绝则变为 `rejected`，双方页面会自动同步状态。草稿未批准前不会写入正式事件与长期记忆。
7. KP 可以轮换加入码、撤销玩家凭证或关闭会话。撤销或关闭会让相关 Bearer 令牌与现有实时连接立即失效，页面进入未连接状态。
8. KP 或已批准的 AI 草稿发布待检定；玩家在游玩页使用数字骰或录入实体骰。结果保留原始个位/十位骰、难度、规则版本和书内页码，可在重启后重放校验。

会话令牌和管理员口令只保存在浏览器 `sessionStorage`。玩家长期身份令牌单独保存在 `localStorage`，仅用于管理调查员和恢复自己的席位，不能替代团会话 Bearer 权限；任何令牌都不会写进 URL。

## 项目结构

```text
ai-kp-local/
├── src/ai_kp/
│   ├── bootstrap/              # 配置、依赖装配与进程生命周期
│   ├── api/                    # HTTP/WebSocket 传输、鉴权、错误与分域 routers
│   ├── application/            # 用例编排；不依赖 FastAPI 或具体数据库
│   ├── platform/               # 通用记忆、模组、场景与规则无关领域结构
│   ├── director/               # AI KP 上下文、提示词、提案编排与结构化输出
│   ├── rule_authoring/         # 规则对象提取、三层校验与确定性执行
│   ├── infrastructure/         # SQLite、MiniRAG、LLM、图片、权限和实时适配器
│   ├── planning/               # 可机读的唯一能力目录
│   ├── rulesets/               # 应用层规则端口、显式注册表与当前 CoC7 适配器
│   └── core/及旧包             # ID 与兼容导出；新代码不再依赖旧实现路径
├── apps/web/src/
│   ├── app/                    # 应用入口、布局与后续路由组合
│   ├── api/                    # 请求客户端与 TypeScript DTO
│   ├── auth/                   # 身份与权限边界
│   ├── session/                # sessionStorage 凭据与地图选择
│   ├── realtime/               # WebSocket 生命周期与同步边界
│   ├── features/               # 独立调查员页，以及会话、地图、行动、草稿和规划功能
│   ├── ruleset-ui/             # 经过批准的规则系统专用渲染器
│   └── shared/                 # 跨 feature 的纯展示组件
├── tests/                           # API/服务/存储/权限/实时回归测试
└── docs/                            # 架构、能力规划与记忆验证文档
```

`ai_kp.api.main:app` 继续是稳定启动入口，实际应用装配在 `bootstrap/composition.py`。详细的分层边界和事务规则见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。调查员字段、Excel 导入、不可变版本、按团审批和跨团状态边界见 [`docs/CHARACTER_SHEET_MODEL.md`](docs/CHARACTER_SHEET_MODEL.md)。
目标目录骨架、当前实现与迁移位置的对应关系，以及占位文件启用条件见 [`docs/ARCHITECTURE_SKELETON.md`](docs/ARCHITECTURE_SKELETON.md)。
规则书 PDF 摄取、MiniRAG 隔离索引、三层校验、权限和真实验收见 [`docs/RULEBOOK_KNOWLEDGE.md`](docs/RULEBOOK_KNOWLEDGE.md)。
当前仅支持 CoC、上传规则书与可执行插件的区别，以及未来第二规则系统的接入条件见 [`docs/RULESET_BOUNDARY.md`](docs/RULESET_BOUNDARY.md)。
备份包格式、在线快照、完整性校验和停服恢复步骤见 [`docs/BACKUP_AND_RECOVERY.md`](docs/BACKUP_AND_RECOVERY.md)。
事件权威的世界事实分类、追加式纠错、角色可见性与 AI 上下文边界见 [`docs/WORLD_FACT_LEDGER.md`](docs/WORLD_FACT_LEDGER.md)。
地图 MapSpec、时代约束、图片安全投影、版本/缓存和真实验收边界见 [`docs/MAP_GENERATION.md`](docs/MAP_GENERATION.md)。

## 开发自检

在项目根目录执行后端全量测试与静态检查：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

前端组件测试、TypeScript 检查与 Vite 生产打包：

```bash
cd apps/web
pnpm test
pnpm run build
pnpm exec playwright install chromium  # 首次运行
pnpm e2e
```

与结构迁移直接相关的快速回归集是：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_api_contract.py \
  tests/test_application_services.py \
  tests/test_db_migrations.py \
  tests/test_capabilities.py
```

所有更改完成后还应执行 `git diff --check`。自动测试通过不代表真实模型已可用；本地模型仍需按下方的 model discovery 和双角色浏览器流程做 real-case 验收。

当后端测试实例运行在 `8002`、模型服务运行在 `8001` 时，可执行不会打印凭据的完整 HTTP real-case：

```bash
.venv/bin/python scripts/realcase_smoke.py
```

该脚本会创建一次性团数据，验证 KP/玩家权限、地图草稿与发布、棋子位置推导、NPC/记忆/模组上下文、真实 AI 草稿、审批生命周期和地图再次读取。创建的数据保留在当前测试数据库，便于重启后复查。

## 功能规划与未实现占位

顶部“功能规划”、“NPC”和“规则检定”入口会读取 `GET /capabilities`，仅把未完整交付的能力显示为“部分可用”或“规划中”，不会伪造可点击的功能接口。只查询未完成项可使用：

```http
GET /capabilities?include_available=false
```

`src/ai_kp/planning/capabilities.py` 是状态、阶段、依赖和验收标准的唯一事实源。它也统一跟踪逐席邀请、多格式 KP 本导入、规则系统插件、本地语义记忆检索、地图揭示编辑和团数据备份/恢复等未完整能力；请不要从 README 的描述推断实时状态。能力目录的更新规则见 [`docs/ROADMAP.md`](docs/ROADMAP.md)。

## 本地模型接口

顶部“模型设置”页面可以直接完成下列配置；也可继续通过环境变量启动。只要模型服务提供 OpenAI-compatible `/v1/chat/completions` 接口即可。远程模式不要根据本地模型文件名猜测接口里的 model ID；应先检测模型列表：

```bash
curl http://<模型服务地址>:8001/v1/models
```

从响应的 `data[].id` 取得服务实际暴露的 model ID，再原样写入 `AI_KP_LLM_MODEL`：

```env
AI_KP_LLM_BASE_URL=http://<模型服务地址>:8001/v1
AI_KP_LLM_API_KEY=local
AI_KP_LLM_MODEL=<从 /v1/models 响应取得的 id>
```

若模型服务要求鉴权，请在 `/v1/models` 请求和 `AI_KP_LLM_API_KEY` 中使用它要求的密钥。模型列表、`/v1/chat/completions` 和一次完整 KP 回合均成功后，才算该模型的 real-case 通过；仅知道主机、端口或 `.gguf` 文件名不算验证成功。

本地目录模式目前面向 Apple Silicon 上的 MLX 格式目录。后端会校验 `config.json`、`tokenizer_config.json` 和 Safetensors 权重，并使用参数数组启动 `mlx_lm.server`；不执行用户提供的命令。保存的路径属于运行 FastAPI 的机器，本地模型子进程只绑定回环地址。完整行为和权限边界见 [`docs/MODEL_CONFIGURATION.md`](docs/MODEL_CONFIGURATION.md)。

## 设计原则

1. 事实进入数据库，叙事由模型生成。
2. 事件流是权威记录，摘要只是缓存。
3. 玩家视角、KP 视角、秘密信息分开存储。
4. NPC 再出现必须通过时间、地点、关系、合理性和预算判断。
5. 人类 KP 可以随时接管、覆盖、冻结或补写状态。
6. CoC7 数值规则由可重放的确定性代码执行，AI 只能建议检定和叙事，不能充当规则计算器。
7. 每项功能使用独立页面；游玩页只组合玩家当下需要同时查看的角色、队友摘要、地图和行动信息。

游戏内规则以用户提供的 `Version2002c` 中文第七版守秘人规则书为本地权威来源。规则 PDF 不进入仓库；版本、文件哈希、章节页码索引、可选规则边界和实现验收要求见 [`docs/RULES_REFERENCE.md`](docs/RULES_REFERENCE.md)。角色卡审批等平台流程会明确标为产品策略，不冒充书中规则。

## 地图生成

地图会作为可重复调用的本地持久数据保存，而不是只留在模型上下文或进程缓存中。SQLite 保存地图身份、不可变 MapSpec 修订、校验报告、地点/路线投影、已选图片、棋子当前位置、移动历史和棋子 `version`；PNG/JPEG 图片按内容哈希写入 `AI_KP_MAP_ASSET_ROOT`。页面重新启动或再次进入团时，会先恢复会话，再通过 API 读取该 `campaign + role` 上次选择的地图；浏览器只缓存选择的地图 ID，权威内容仍来自后端。

MapSpec 是结构事实源，包含地图种类、统一坐标系、时代/地域、地点、连接、场景元素、必含元素和视觉约束。创建时会校验 Schema、画布边界、路线端点、连通性、时代信息和必含元素覆盖率；存在错误时不能保存或发布。结构化地点和路线继续用于路线规划、分队行动与隐藏地点过滤。

渲染采用同一坐标系内的分层画布：可选时代背景图片、确定性结构/标签 SVG、棋子。图片只负责材质、光照和氛围；全部地点、路线和必含元素仍由 MapSpec/SVG 保证，因此图片模型不可用、漏画或文件损坏时，地图仍能完整游玩。

新生成的地图状态为 `draft`，只有 KP 能列出或直接读取。发送给图片模型的内容先投影为玩家安全版本，完全移除 KP 地点、秘密路线、KP 备注、棋子与线索；生成候选不会自动替换当前背景或发布地图。KP 预览并选用候选后，再显式发布。玩家只能取得已发布地图当前选中的公共图片及玩家/全桌可见结构。

```http
POST /campaigns/{campaign_id}/maps/generate
GET  /maps/{map_id}/image-prompt
POST /maps/{map_id}/image-assets/generate
POST /maps/{map_id}/assets/{asset_id}/select
GET  /map-assets/{asset_id}/content
POST /maps/{map_id}/publish
POST /maps/{map_id}/unpublish
```

```json
{
  "title": "黑水镇警局",
  "prompt": "1928 年新英格兰警局的夜间调查地图",
  "map_kind": "floorplan",
  "locations": ["街道入口", "接待大厅", "走廊", "问询室", "值班办公室", "楼梯"],
  "routes": [["街道入口", "接待大厅"], ["接待大厅", "走廊"], ["走廊", "问询室"]],
  "features": ["深色木制接待台", "机械打字机", "有线电话"],
  "required_elements": ["街道入口", "接待大厅", "走廊", "问询室", "深色木制接待台"],
  "era_year": 1928,
  "locale": "美国马萨诸塞州",
  "time_of_day": "夜晚",
  "weather": "冷雨",
  "public_architecture": ["新英格兰市政建筑", "红砖", "深色木材"],
  "forbidden_elements": ["霓虹招牌"]
}
```

`forbidden_elements` 是 KP 私有的额外视觉审查清单，不会进入公共图片提示词。图片提示只
使用由年份确定性产生的通用时代禁忌，避免负面提示本身泄露“不要画某个秘密地点”。
`public_architecture` 则是明确的公共字段：玩家和图片模型都能看到，不能填写秘密建筑。

图片模型是独立可选依赖，不复用聊天模型配置。可以在前端“模型设置 →
地图图片模型”中检测服务返回的真实模型 ID、保存地址和超时；配置保存在 SQLite，
API Key 只返回“是否已配置”，不会回传明文。重启后会自动恢复。环境变量仍可作为
首次启动时的默认值：

```env
AI_KP_IMAGE_BASE_URL=http://127.0.0.1:8188/v1
AI_KP_IMAGE_API_KEY=local
AI_KP_IMAGE_MODEL=<服务实际暴露的图片模型 ID>
AI_KP_MAP_ASSET_ROOT=data/map-assets
```

图片服务必须同时提供 `/v1/models` 和 `/v1/images/generations`，生成接口需要返回
`b64_json`。模型列表检测成功只表示协议和模型 ID 可读，真正的图片能力仍会在生成
第一张候选背景时校验。

当前适配器不跟随模型返回的任意远程图片 URL，只接受经过 Base64 解码、PNG/JPEG
签名、尺寸、像素和 32 MiB 上限校验的位图。完整设计、权限和 real-case 清单见
[`docs/MAP_GENERATION.md`](docs/MAP_GENERATION.md)。

玩家移动不是电子游戏式格子寻路，而是线下跑团式 token 移动：

```http
POST /maps/{map_id}/tokens
POST /map-tokens/{token_id}/move
GET /map-tokens/{token_id}/moves
```

玩家只能移动与自己会话身份所绑定 PC 相同的 token，且只能沿玩家可见路线移动到玩家可见地点。客户端提交的 `actor`、`moved_by`、PC 或任意 KP 视角参数都不能提升权限，最终身份由服务端令牌决定。

## 团会话与权限

每个团同一时间最多有一个活动会话。KP 为玩家创建命名席位，每个席位有独立的一次性邀请和可选的预留角色。认领后邀请立即失效，玩家获得绑定 `session + campaign + role + member + pc + player_profile + seat` 的 Bearer 会话令牌。

SQLite 只保存邀请码、稳定玩家令牌和会话访问令牌的用途隔离 SHA-256 哈希，不保存明文。席位邀请明文只在创建或重新签发的响应中出现一次。

主要接口：

```http
POST /campaigns/{campaign_id}/sessions
POST /sessions/join
GET  /auth/me
GET  /sessions/{session_id}/members
POST /sessions/{session_id}/members/{member_id}/assign-pc
POST /sessions/{session_id}/members/{member_id}/revoke
POST /sessions/{session_id}/rotate-join-code
POST /sessions/{session_id}/close
POST /sessions/{session_id}/seats
GET  /sessions/{session_id}/seats
POST /sessions/{session_id}/seats/{seat_id}/reissue
POST /sessions/{session_id}/seats/{seat_id}/revoke
PATCH /sessions/{session_id}/seats/{seat_id}/pc
POST /session-seats/claim
GET  /player-profile/session-seats
POST /session-seats/{seat_id}/recover
```

撤销一个席位只会让该席的邀请和成员会话令牌失效，不影响其他玩家，也不删除该玩家的长期档案、调查员或历史。强封禁、密码/多因素登录和公网账号恢复仍不在当前范围。

## 实时同步

HTTP Bearer 令牌不会放进 WebSocket URL。前端先通过 `POST /realtime/tickets` 换取默认 30 秒有效、只能消费一次且禁止缓存的短期票据，再从当前页面同源的 `/api/ws` 建立连接；服务端同时校验 `Origin` 白名单。

业务变更与 `realtime_events` outbox 记录在同一 SQLite 事务中。事件按 `session`、`kp` 或指定 `member` 受众由服务端过滤，发送给浏览器的游标是不可推断数据库序号的 opaque key。前端按会话成员保存游标，断线后指数退避重连并补放可见事件；游标失效或重新连上时会执行一次安全的全量同步。撤销成员或关闭团会话会在现有连接的下一次校验中立即终止实时资格。

页面顶部显示“连接中 / 实时同步 / 重连中 / 未连接”状态。连续事件只触发静默、100 ms 合并的数据刷新，不会覆盖用户当前操作日志或反复显示全局加载状态。

## 玩家行动队列

玩家通过 `POST /campaigns/{campaign_id}/actions` 提交行动。服务端从会话身份和已发布地图上的受控 PC token 推导角色与地点，不信任客户端伪造的身份/地点。前端会发送 `client_action_id`，相同会话成员重复发送同一个 ID 时只创建一条行动。

行动状态由服务端推进：

```text
submitted --KP/AI 草稿认领--> reviewed --批准草稿--> resolved
                                      \--拒绝草稿--> rejected
```

玩家只能查看自己的行动；KP 可以查看团内行动队列。行动关联的 KP 草稿、秘密说明和提示词快照不会通过玩家行动响应泄露。

## AI 草稿审批

开发模式可以手动创建草稿，绕过真实 LLM：

```http
POST /campaigns/{campaign_id}/proposals
GET /campaigns/{campaign_id}/proposals
POST /kp/proposals/{proposal_id}/approve
POST /kp/proposals/{proposal_id}/reject
```

`/kp/turn` 会调用配置的大模型生成结构化草稿，但不会直接写入世界状态。模型输出必须通过严格字段、类型、数量和可见性校验；格式错误时只允许一次修复请求。

人类 KP 批准后，同一事务内会应用：

- 公开叙述和事件
- 待检定请求
- 筛选后的长期记忆
- NPC 关系和最近出现时间
- 符合当前地图路线的棋子移动

任何 PC、NPC 或棋子跨团引用都会拒绝整次审批并回滚，不会留下部分事件。

每个由 AI 生成的草稿还会保存一份上下文快照，可用来检查模型当时看到了什么：

```http
GET /kp/proposals/{proposal_id}/context
```

快照包含最终消息、纳入来源、排除来源与原因、可见性范围和 token 估算。未激活的 `spoiler_tag` 不会进入当前 AI 回合。

## LAN 与反向代理部署安全

默认的 `AI_KP_LOCAL_ADMIN_ENABLED=true` 是本机初始化便利功能：后端根据实际 socket 的回环地址判断本机管理员，不信任 `X-Forwarded-For`。一旦通过 LAN、容器端口映射或反向代理提供服务，代理到后端的请求很可能都表现为本机请求，因此必须关闭它，并配置管理员口令：

```env
AI_KP_LOCAL_ADMIN_ENABLED=false
AI_KP_ADMIN_TOKEN=请替换为足够长的随机值
AI_KP_CORS_ORIGINS=https://你的前端域名
```

管理员操作使用 `X-AI-KP-Admin-Token` 请求头。前端提供管理员口令输入框，并同样只存于 `sessionStorage`。

必须在反向代理层启用 HTTPS；当前应用自身不提供 TLS。Bearer 令牌、席位邀请码和管理员口令在纯 HTTP 的局域网中都可能被旁路监听。当前版本没有令牌有效期、加入限流、暴力尝试锁定、密码登录或持久封禁能力，因此不应直接暴露到公网。生产化前还需要可信代理配置、请求大小/速率限制、安全日志以及对未来导入或 AI 生成 SVG 的清洗与 CSP。

## 模组文本格式

当前底层 API 支持纯文本导入，用于验证切块和剧透边界；它不是最终面向 KP 的文件
上传界面。正式 KP 本入口只接受 PDF/Word 文档，并需要同时提取正文与文档内的照片、
地图和扫描线索。详细边界见 [`docs/MODULE_DOCUMENT_IMPORT.md`](docs/MODULE_DOCUMENT_IMPORT.md)。

内部文本格式中，每个空行分隔的段落会成为一个 `module_chunk`。
段落首行可以写元数据：

```text
@visibility=player @scene=旧码头
玩家可见：旧码头起雾，仓库门半开。

@visibility=kp @spoiler=chapter-1 @scene=旧码头
KP信息：仓库内有隐藏祭坛。

@visibility=secret @spoiler=ending
真相：失踪者仍然活着。
```

`visibility` 可选值：

- `player`: 玩家视角可见
- `table`: 全桌可见
- `kp`: KP/AI KP 可见
- `secret`: 默认不会进入普通 KP 上下文，需要显式揭示
