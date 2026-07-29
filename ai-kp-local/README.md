# AI KP Local

本地优先的 AI 跑团平台。它将团会话、调查员、地图、模组、NPC、长期记忆和规则知识
保存在本机，通过可替换的 OpenAI-compatible 模型让 AI 担任 KP，也允许人类 KP
随时审核、接管或覆写。

当前产品以 **CoC 第七版**为唯一可执行规则系统。架构已经隔离规则集，但上传其他
规则书只会建立独立知识库，不会自动获得一个可信的可执行规则插件。

> 项目仍处于本地开发阶段，适合单机或受信任局域网测试，不应直接暴露到公网。

## 现在能做什么

- 创建团、开启会话，并通过逐席单次邀请码建立稳定玩家身份。
- 玩家手工创建或从 Excel 安全导入调查员；KP 可退回修改或批准精确的不可变版本。
- 在游玩页同时查看本人角色卡、队友公开摘要、中央地图与右侧行动/聊天区域。
- 生成并持久保存 MapSpec、时代化 SVG/图片候选、路线、棋子和移动历史；重启后可恢复。
- 导入 PDF、DOC 或 DOCX 格式的 KP 本，保留正文、表格、图片、页码/段落来源与剧透边界。
- 显式选择当前活动模组，并把场景、已解锁剧透和有限运行态送入 AI KP 上下文。
- 记录事件、主要/支线记忆、NPC 关系和世界时间，并筛选跨本 NPC 再出现候选。
- 用 SQLite 原文页块 + MiniRAG 隔离索引保存规则书知识；JSON 规则对象仍需来源校验、
  KP golden case 审核和确定性引擎验证。
- 将 AI 回合限制为结构化草稿；只有人类 KP 批准后，事件、记忆、NPC 更新、检定和
  地图移动才会原子写入正式状态。
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
9. AI 草稿要求检定时，由确定性 CoC7 引擎解析数字骰/实体骰；结果可重放审计。
10. 关闭并重启服务，确认会话、活动模组、地图、棋子、事件、记忆和 NPC 都可恢复。

会话访问令牌和管理员口令只保存在浏览器 `sessionStorage`。稳定玩家令牌存入
`localStorage`，仅用于管理本人调查员和恢复已认领席位，不能替代团会话 Bearer
权限。任何令牌都不会写入 URL。

## 数据与安全边界

- SQLite 事件流和结构化实体是权威记录；AI 叙述、摘要、向量索引和浏览器状态都不是。
- 模组是带来源的权威大纲，而非完整世界清单。AI 可以提出合理世界补全，但不能覆盖
  Canon、剧情锚点或已确认事实。
- AI 输出永远先成为草稿；审批前不能改变正式事件、记忆、NPC、检定或地图位置。
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
│   ├── platform/           # 规则无关的记忆、模组、场景领域
│   ├── director/           # AI KP 上下文、结构化输出与草稿编排
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

## 当前限制

- 可执行规则集目前只有 CoC7；上传 Cyberpunk RED 等规则书不会自动生成可信规则插件。
- 检定状态机仍缺持久化对抗检定的完整 API/UI。
- 模组知识图谱和运行态已可用，但自动结构校订、合理性评分和完整动态支线编排仍在开发。
- 世界事实账本已有后端边界，尚缺完整工作台与 AI `proposed_facts` 审批 UI。
- 跨本角色永久时间线、分队路线规划、完整地图揭示编辑仍属于规划能力。
- 中文图片 OCR 取决于本机 Tesseract `chi_sim`；否则应使用受支持的视觉模型。

请以“功能规划”页或 `GET /capabilities?include_available=false` 查看最新缺口，不要根据
这份概览推断某个计划接口已经存在。

## 文档索引

- [架构与事务边界](docs/ARCHITECTURE.md)
- [架构骨架与迁移约束](docs/ARCHITECTURE_SKELETON.md)
- [调查员、Excel 与审批模型](docs/CHARACTER_SHEET_MODEL.md)
- [检定与结果驱动草稿](docs/CHECK_RESOLUTION.md)
- [模型配置与本机 MLX](docs/MODEL_CONFIGURATION.md)
- [地图生成、缓存与发布](docs/MAP_GENERATION.md)
- [PDF/Word 模组导入](docs/MODULE_DOCUMENT_IMPORT.md)
- [规则书双存储与三层校验](docs/RULEBOOK_KNOWLEDGE.md)
- [规则系统边界](docs/RULESET_BOUNDARY.md)
- [本地规则来源](docs/RULES_REFERENCE.md)
- [长期记忆验收](docs/MEMORY_TESTING.md)
- [世界补全约束](docs/WORLD_EXPANSION.md)
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
