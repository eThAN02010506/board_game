# AI KP Local

本项目是一个本地优先的 AI 跑团平台 MVP。目标是让玩家导入 KP 本、创建角色、记录团内事件、维护 NPC 与长期记忆，并通过任意 OpenAI-compatible 大模型接口让 AI 扮演 KP。应用与数据库可完全本地运行，大模型是可替换的外部提供者。

## 当前可用能力

- FastAPI 后端入口
- SQLite 本地数据库与事件流
- Campaign、PC、NPC、Memory、World Time 的基础数据模型
- KP 本/模组纯文本导入、切块、秘密标签和剧透边界
- AI 地图生成；SVG、地点、路线、棋子位置、移动记录和棋子版本均持久化到 SQLite
- 线下跑团式棋子移动，以及基于棋子版本的并发移动冲突保护
- AI KP 草稿审批流：草稿、批准、拒绝、覆写，批准后才写入事件/记忆
- 严格结构化 AI 输出：叙述、检定、事件、记忆、NPC 更新和地图移动均校验
- 可解释的 AI 上下文组装：记录纳入/排除来源、剧透边界和 token 估算
- NPC 跨本再出现的候选判断
- 玩家角色主要事件与支线事件记忆
- OpenAI-compatible LLM 适配层
- React/Vite 前端工作台；启动时自动恢复当前浏览器会话及该 `campaign + role` 上次打开的地图
- 团会话、共享加入码、KP/玩家服务端权限校验
- 短期单次实时票据、同源 WebSocket、SQLite outbox、角色可见性过滤和断线重连
- 玩家行动队列与 `submitted -> reviewed -> resolved/rejected` 生命周期
- 地图 `draft -> published` 发布边界
- API 合同、应用服务、权限、数据库迁移、实时同步与核心域的自动测试

## 快速开始

```bash
cd ai-kp-local
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
uvicorn ai_kp.api.main:app --reload
```

前端：

```bash
cd apps/web
corepack enable
pnpm install
pnpm run dev
```

前端统一使用 `pnpm`，依赖版本由 `apps/web/pnpm-lock.yaml` 锁定。请不要在仓库中生成 `package-lock.json` 或 Yarn lockfile。

Vite 默认把 HTTP 与 WebSocket 的 `/api` 请求代理到 `http://localhost:8000`。需要连接另一台测试后端时，在启动前指定目标；浏览器仍只连接当前前端源：

```bash
VITE_BACKEND_TARGET=http://127.0.0.1:8000 pnpm run dev
```

默认配置只适合在运行后端的同一台机器上开发。浏览器打开前端后，可以按下面的真实流程验收：

1. 点击“连接后端”，创建测试团；前端会随即开启该团的 KP 会话。已有有效会话时，重载页面会自动恢复身份和该角色上次打开的地图。
2. KP 创建角色卡，在“团会话与权限”中复制本次显示的加入码。
3. 用另一个浏览器会话打开前端，输入加入码和玩家显示名；角色卡可以加入时填写，也可以由 KP 在成员列表中绑定。
4. KP 生成地图并放置绑定角色的棋子。新地图默认是草稿；发布后，玩家页面会自动显示地图，后续棋子移动也会自动同步，无需手动刷新。
5. 玩家只能移动自己角色的棋子，并可在“玩家行动”中提交行动。行动会自动出现在 KP 队列；KP 选中后创建手工草稿或调用本地 AI。
6. 行动被草稿认领后进入 `reviewed`；KP 批准草稿后变为 `resolved`，拒绝则变为 `rejected`，双方页面会自动同步状态。草稿未批准前不会写入正式事件与长期记忆。
7. KP 可以轮换加入码、撤销玩家凭证或关闭会话。撤销或关闭会让相关 Bearer 令牌与现有实时连接立即失效，页面进入未连接状态。

会话令牌和管理员口令只保存在浏览器 `sessionStorage`，关闭对应标签页/浏览器会话后不会作为长期登录状态保留；它们不会写进 URL。

## 项目结构

```text
ai-kp-local/
├── src/ai_kp/
│   ├── api/                    # ASGI 装配、依赖/鉴权/错误与分域 routers
│   ├── application/            # Campaign/World/Session/Map/Turn 用例服务
│   ├── storage/                # SQLite 基类、行解码、分域 repositories 与版本迁移
│   ├── core/                   # 配置、schema、ID 与兼容 Repository facade
│   ├── planning/               # 可机读的唯一能力目录
│   ├── kp/  maps/  memory/     # KP 回合、地图与记忆域
│   └── modules/ llm/ security/ realtime/ rules/ human_kp/
├── apps/web/src/
│   ├── api/                    # 请求客户端与 TypeScript DTO
│   ├── session/                # sessionStorage 凭据与地图选择
│   ├── hooks/                  # 前端 Provider 式实时连接与同步边界
│   ├── features/               # 会话、地图、行动、草稿和功能规划面板
│   └── shared/                 # 跨 feature 的纯展示组件
├── tests/                           # API/服务/存储/权限/实时回归测试
└── docs/                            # 架构、能力规划与记忆验证文档
```

`ai_kp.api.main:app` 继续是稳定启动入口，实际应用装配在 `api/app.py`。详细的分层边界和事务规则见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 开发自检

在项目根目录执行后端全量测试与静态检查：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

前端构建同时执行 TypeScript 检查与 Vite 生产打包：

```bash
cd apps/web
pnpm run build
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

只要模型服务提供 OpenAI-compatible `/v1/chat/completions` 接口即可。不要根据本地模型文件名猜测接口里的 model ID；real-case 测试应先请求模型列表：

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

## 设计原则

1. 事实进入数据库，叙事由模型生成。
2. 事件流是权威记录，摘要只是缓存。
3. 玩家视角、KP 视角、秘密信息分开存储。
4. NPC 再出现必须通过时间、地点、关系、合理性和预算判断。
5. 人类 KP 可以随时接管、覆盖、冻结或补写状态。

## 地图生成

地图会作为可重复调用的本地持久数据保存到 SQLite，而不是只留在模型上下文或进程内缓存中。保存内容包括地图元数据、SVG、地点、路线、棋子当前位置、移动历史和棋子 `version`。页面重新启动或再次进入团时，会先恢复会话，再通过 API 读取该 `campaign + role` 上次选择的地图；浏览器只缓存选择的地图 ID，权威地图内容仍来自 SQLite。

结构化地点和路线用于路线规划、分队行动与隐藏地点过滤，SVG 用于前端直接展示。移动请求携带当前棋子 `version`；并发页面使用旧版本重复移动时，服务端会拒绝冲突，客户端重新同步后才能继续。

新生成的地图状态为 `draft`，只有 KP 能列出或直接读取。KP 显式发布后状态变为 `published`，玩家才能看到其中玩家/全桌可见的地点、路线与棋子；KP 也可以把地图重新收回为草稿。

```http
POST /campaigns/{campaign_id}/maps/generate
POST /maps/{map_id}/publish
POST /maps/{map_id}/unpublish
```

```json
{
  "title": "旧码头区域图",
  "prompt": "旧码头、废弃仓库、报社、警局",
  "locations": ["旧码头", "废弃仓库", "报社", "警局"],
  "routes": [["旧码头", "废弃仓库"], ["旧码头", "报社"], ["报社", "警局"]]
}
```

玩家移动不是电子游戏式格子寻路，而是线下跑团式 token 移动：

```http
POST /maps/{map_id}/tokens
POST /map-tokens/{token_id}/move
GET /map-tokens/{token_id}/moves
```

玩家只能移动与自己会话身份所绑定 PC 相同的 token，且只能沿玩家可见路线移动到玩家可见地点。客户端提交的 `actor`、`moved_by`、PC 或任意 KP 视角参数都不能提升权限，最终身份由服务端令牌决定。

## 团会话与权限

每个团同一时间最多有一个活动会话。创建会话会产生一个 KP 成员、一个 KP Bearer 访问令牌和一个共享玩家加入码；玩家用加入码加入后会获得自己的 Bearer 访问令牌。访问令牌绑定 `session + campaign + role + member + pc`，跨团引用会被拒绝，玩家也不能读取 KP 草稿、KP notes、AI 上下文快照或未发布地图。

SQLite 只保存加入码和访问令牌的用途隔离 SHA-256 哈希，不保存它们的明文。明文只在创建、加入、主动轮换或撤销后自动轮换的响应中返回；恢复页面时只能使用当前浏览器 `sessionStorage` 中已有的访问令牌。加入码不能当 Bearer 令牌使用，访问令牌也不能当加入码使用。

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
```

撤销玩家会立即让该成员令牌失效，并自动轮换共享加入码，使旧加入码失效。这里必须注意：当前没有用户账号或稳定身份系统，因此这不等于“封禁某个人”；拿到新加入码的人仍能以新成员身份加入。若需要强封禁、审计到人或公开部署，应增加账号体系、逐席邀请和管理员审核。

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

必须在反向代理层启用 HTTPS；当前应用自身不提供 TLS。Bearer 令牌、共享加入码和管理员口令在纯 HTTP 的局域网中都可能被旁路监听。当前版本也没有登录账号、单人邀请、令牌有效期、登录/加入限流、暴力尝试锁定或持久封禁能力，因此不应直接暴露到公网。生产化前还需要可信代理配置、请求大小/速率限制、安全日志以及对未来导入或 AI 生成 SVG 的清洗与 CSP。

## 模组文本格式

当前支持纯文本导入。每个空行分隔的段落会成为一个 `module_chunk`。
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
