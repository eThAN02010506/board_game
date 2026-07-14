# AI KP Local

本项目是一个本地优先的 AI 跑团平台骨架。目标是让玩家可以导入 KP 本、创建角色、记录团内事件、维护 NPC 与长期记忆，并通过任意 OpenAI-compatible 大模型接口让 AI 扮演 KP。

## 当前骨架包含

- FastAPI 后端入口
- SQLite 本地数据库与事件流
- Campaign、PC、NPC、Memory、World Time 的基础数据模型
- KP 本/模组纯文本导入、切块、秘密标签和剧透边界
- NPC 跨本再出现的候选判断
- 玩家角色主要事件与支线事件记忆
- OpenAI-compatible LLM 适配层
- React/Vite 前端工作台骨架
- 可运行的核心单元测试

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
npm install
npm run dev
```

## 本地模型接口

只要模型服务提供 OpenAI-compatible `/v1/chat/completions` 接口即可。比如：

```env
AI_KP_LLM_BASE_URL=http://localhost:11434/v1
AI_KP_LLM_API_KEY=local
AI_KP_LLM_MODEL=qwen3:8b
```

## 设计原则

1. 事实进入数据库，叙事由模型生成。
2. 事件流是权威记录，摘要只是缓存。
3. 玩家视角、KP 视角、秘密信息分开存储。
4. NPC 再出现必须通过时间、地点、关系、合理性和预算判断。
5. 人类 KP 可以随时接管、覆盖、冻结或补写状态。

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
