# 本地模型选型记录（2026-09）

这是一份日期化的工程决策，不是 Campaign 权威状态或永久产品承诺。模型版本更新后应以
同一套 AIKP 评测集重跑，而不是只按公开榜单替换。

## 结论

首发基线采用同一家族的三档能力配置，而不是要求玩家同时运行三个模型：

| AIKP 档位 | 首选基线 | 运行职责 | 首发训练策略 |
| --- | --- | --- | --- |
| narrow | Qwen3.5-0.8B | frame 分类、路由、白名单候选 ID、短摘要 | 主流程稳定后，用 4B/9B 轨迹蒸馏并做窄任务 LoRA |
| standard | Qwen3.5-4B | 默认主 KP、受约束行动理解、NPC/叙事、模组作者化 | 唯一首发微调重点；先评测原模型，再训练 LoRA |
| quality | Qwen3.5-9B | 更复杂计划、独立审查、高质量叙事和可选视觉理解 | 先作为未微调质量上界；只有评测证明收益才微调 |

“1B/4B/8B”是产品能力档位，不要求参数名精确等于整数。Qwen3.5 当前对应约
0.8B/4B/9B。三档共享模型家族、对话模板、工具调用解析和 Apache-2.0 许可证，能降低
单人开发者维护三套推理适配器、训练管线和提示模板的成本。Host 按硬件和质量偏好只加载
一个主档；玩家客户端不加载模型。即使模型宣称支持超长上下文，Steam 默认配置也应通过
评测确定较小上限，给 KV cache、并发玩家和确定性服务留出内存。

这不是把 0.8B 当作完整 KP。官方第三方结构化抽取评测页面显示该档与 4B/9B 有明显差距，
因此它只能接收短上下文、封闭枚举和可校验 JSON；失败两次即澄清或交给主模型。状态、权限、
骰子、规则效果、可见性和提交仍归确定性 Kernel。

## 检索模型

记忆/规则/模组检索与生成模型分离。默认候选为 Qwen3-Embedding-0.6B：官方模型卡标明
100+ 语言、32K 输入和可裁剪 embedding 维度，适合中英文及后续多规则书。只有离线评测证明
RRF 后仍需要重排时，才增加 Qwen3-Reranker-0.6B；运行时可卸载或按批调用，失败必须退回
Unicode 词法 + SQLite FTS5 + RRF。

BGE-M3 是保守备选：MIT 许可证、100+ 语言、8192 token，并原生覆盖 dense、sparse 和
multi-vector。它的官方建议同样是 hybrid retrieval 后接 reranker。两者必须用真实规则段落、
中文行动改写、秘密隔离和跨规则书负样本比较，不能只采用厂商榜单。

## 对照候选

- MiniCPM5-1B：Apache-2.0，面向端侧、中文/英文、长上下文和工具调用；适合作为 narrow 档
  A/B 候选。若它显著胜出，可以只替换 narrow 档，但要承担第二套模板与运行时测试成本。
- Ministral 3 3B/8B Instruct：Apache-2.0，官方提供 JSON 输出、原生函数调用、中文和量化
  格式；是 standard/quality 档的主要跨家族对照。
- Phi-4-mini-instruct：MIT、128K、支持工具调用，适合作为 4B 对照；其官方模型卡也明确提示
  函数调用可能臆造函数名，因此在 AIKP 中仍必须只接受白名单 ID，并不作为默认选择。
- Gemma：能力和尺寸有吸引力，但分发需携带 Gemma 使用限制并通知下游用户。对单人 Steam
  项目而言，合规工作高于 Apache/MIT 候选，除非实测质量有明显优势，否则不优先捆绑。

## 微调顺序

不同时微调三个模型。

1. 先冻结协议和评测：桌面对话 frame、operator/skill ID 选择、秘密泄漏、虚构状态、错误恢复、
   中文改写、多步骤计划、叙事与不可变结算一致性。
2. 先测 Qwen3.5-4B 原模型。只有可归因于模型、且提示/候选裁剪无法解决的稳定失败才进入数据集。
3. 对 4B 做 LoRA/SFT；训练目标是协议遵循和领域表达，不把 CoC7 规则结果背进权重。规则仍来自
   ruleset plugin 与来源绑定知识，因此以后增加规则书不必重新训练整个核心模型。
4. 用通过审核的 4B/9B 轨迹蒸馏 narrow 模型，只训练少数封闭 Agent 任务。
5. 9B 保持未微调质量基线；当盲测证明 LoRA 对长计划/叙事收益超过维护成本时再训练。

每个训练样本应包含输入投影版本、允许 ID、期望 JSON、拒绝理由和规则集无关标签。CoC7 数据可
提供语境，但不得让通用 frame/router 学会把所有危险行动硬编码成 CoC 技能。

## 上线门禁

模型进入 Steam 可选包前至少通过：

- JSON/schema 首次成功率、两次修复后成功率及澄清率；
- operator/skill 白名单准确率和越权 ID 为零；
- KP/secret 泄漏为零，权限过滤发生在模型调用之前；
- 单行动、多步骤 DAG、四玩家并发收集与模型重启恢复；
- 中文、英文和混合输入，以及同义改写/否定/结果断言；
- 固定硬件上的首 token、整轮延迟、峰值内存、KV cache 与连续两小时稳定性；
- 未微调基线、LoRA、不同量化的盲测，确认收益大于包体和维护成本。

## 官方资料

- Qwen3.5-0.8B：<https://huggingface.co/Qwen/Qwen3.5-0.8B>
- Qwen3.5-4B：<https://huggingface.co/Qwen/Qwen3.5-4B>
- Qwen3.5-9B：<https://huggingface.co/Qwen/Qwen3.5-9B>
- Qwen3 Embedding/Reranker：<https://huggingface.co/Qwen/Qwen3-Embedding-0.6B>、
  <https://huggingface.co/Qwen/Qwen3-Reranker-0.6B>
- MiniCPM5-1B：<https://huggingface.co/openbmb/MiniCPM5-1B>
- Ministral 3：<https://mistral.ai/news/mistral-3/>
- Phi-4-mini-instruct：<https://huggingface.co/microsoft/Phi-4-mini-instruct>
- BGE-M3：<https://huggingface.co/BAAI/bge-m3>
- Gemma 使用条款：<https://ai.google.dev/gemma/terms>
