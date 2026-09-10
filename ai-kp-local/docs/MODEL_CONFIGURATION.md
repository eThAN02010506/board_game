# 模型配置与本地目录运行

当前本地模型候选、分层职责、微调顺序与上线评测见
[`MODEL_SELECTION_2026-09.md`](MODEL_SELECTION_2026-09.md)。具体型号是日期化决策，
不得写入 Campaign 权威数据或替代 capability profile。

## 两种来源

`/models` 是独立模型设置页，配置保存在 SQLite 的单行 `model_configuration` 中。保存后应用实例会替换当前 `llm_base_url`、`llm_api_key` 与 `llm_model`，因此后续新发起的 AI KP 回合、模组知识抽取、契约作者化和规则抽取立即使用新配置。已经在执行中的请求不会被中途切换，但其旧世代结果在落库前会被版本闸门拒绝并安全重试。

常驻后台 worker 不缓存启动时的提供者。每次领取任务都会从 SQLite 捕获一次不可变模型快照；可恢复契约的所有分区与补写周期还绑定同一个模型世代。切换模型后，旧世代的派生分区会被丢弃并从不可变来源证据重新作者化，不能把两个模型的中间结果拼成一个发布契约。

### OpenAI-compatible 服务

- 输入 HTTP(S) 地址；缺少 `/v1` 时由后端补齐。
- `POST /model-settings/discover` 请求服务的 `/v1/models`，前端展示服务实际返回的 ID。
- API Key 只写入本地 SQLite；读取配置时只返回 `api_key_configured`，不回传密钥。
- 地址可以是本机、局域网或其他由本地管理员信任的服务。
- 当服务返回的模型 ID 明确属于 `gpt-oss` 时，聊天请求会向 llama.cpp 的 Harmony
  模板传入 `reasoning_effort=low`。这不会把思考内容交给玩家，而是为 AI KP 所需的
  结构化最终正文保留输出预算；采样按官方建议规范为
  `temperature=1.0, top_p=1.0`。`chat_template_kwargs` 不是 OpenAI 标准字段，因此
  严格兼容服务若以 400/422 拒绝它，客户端会自动移除该可选提示并安全重试一次；
  其他模型不会收到这个字段。

### 本地 MLX 模型目录

- 路径是 FastAPI 后端所在机器的目录，不是浏览器上传文件。
- 后端要求目录含 `config.json`、`tokenizer_config.json` 及至少一个 `.safetensors` 权重。
- 安装 `.[local-model]` 后，`POST /model-runtime/start` 使用当前 Python 解释器运行 `mlx_lm.server`。
- 模型路径作为参数传递，不经过 Shell；工作目录设为模型目录的父目录，以相对目录名交给 MLX-LM。
- 托管服务默认向聊天模板传入 `enable_thinking=false`，避免推理模型只返回隐藏思考而没有 AI KP 所需的结构化正文。
- 托管端口由管理员选择，实际 AI KP 基地址固定为 `http://127.0.0.1:<port>/v1`。
- 后端关闭时会终止由它启动的本地模型子进程；模型不会在服务重启后未经确认自动加载。
- 运行日志默认写在数据库同目录的 `model-runtime.log`。

## 权限与接口

模型地址、密钥和本机进程属于实例级管理功能，所有接口都要求本地管理员权限：

- `GET/PUT /model-settings`
- `POST /model-settings/discover`
- `GET /model-runtime`
- `POST /model-runtime/start`
- `POST /model-runtime/stop`

默认开发配置允许回环地址访问；关闭 `local_admin_enabled` 后必须提供 `X-AI-KP-Admin-Token`。

## 验收顺序

1. 远程模式输入地址并检测，选择 `/v1/models` 实际返回的 ID。
2. 保存后重读页面，确认地址与模型恢复，API Key 只显示“已配置”。
3. 本地模式检查目录，确认解析出的绝对路径、权重数量和大小。
4. 保存并启动，等待进程状态为运行中；再次检测，直到 `/v1/models` 返回模型。
5. 发起一次完整 AI KP 草稿，确认 `source_model` 是当前模型且草稿仍需 KP 审批。
6. 在后台契约仍运行时切换一次模型，确认旧调用不能落库、任务从第一个来源分区重新开始且最终 `source_model` 为新模型。
7. 停止本地模型，确认进程退出；再次发起请求时应明确失败而不是静默切回远程服务。

当前 `http://192.168.1.97:8001/v1` 已按 `/models` 实际返回的 GPT-OSS 20B GGUF ID
完成生产 real-case：正式调查员提交/批准/绑定、NPC 与长期记忆、活动模组、地图保存
与移动、玩家行动、AI KP 结构化草稿及 KP 批准全部通过，且草稿 `source_model` 与服务
发现的完整 ID 精确相同。
