# AC-LONG 真实 UI 验收手册

本文只定义执行与取证步骤，不改变 [PRODUCT_REQUIREMENTS.md](PRODUCT_REQUIREMENTS.md) 的要求，
也不把脚本通过等同于产品已经通过 `AC-LONG`。

## 验收边界

`apps/web/e2e/full-ai-long-campaign.realcase.e2e.ts` 从浏览器完成：

1. KP 建团、建立四个单席邀请；
2. 四名玩家分别认领、建卡、提交，KP 审批并绑定；
3. Session 0 切换为 Full AI KP，并由全桌确认；
4. KP 通过 UI 上传真实模组、等待 MinerU 完成、启动运行；
5. AI 自动作者化、独立审核、发布并绑定可执行契约；
6. 五类玩家行为轮换，包含同窗多人行动、具体澄清、技能确认、数字骰、接受失败和一次推动策略；
7. 第 4 次 Session 由 KP UI 建立规则插件战斗、玩家 UI 预览并确认规则动作，随后由规则状态机
   结束遭遇；KP 再从物品权威账本登记公开战利品，由玩家 UI 拾取；
8. 在同一 Campaign 中预备已审核后备角色，并于第 5–8 次 Session 依次完成规则伤害导致死亡、
   玩家确认换角、暂离/回归、中途单席邀请入团，以及一名原玩家离队转为 Observer；最终仍保持
   四名可行动玩家；
9. 第 3 次 Session 执行 AI-assisted GM 接管、追加权威事实并交还 AI；结合引导式建团和第 4–5
   次 Session 的规则权威工具操作，分别形成新手 GM、资深 GM 与 AI-assisted GM 的可重建轨迹；
10. 第 9 次 Session 只能消费先前成功检定形成的成长标记，由 KP UI 执行规则成长，再由角色所有者
   在时间线确认永久变化；没有真实标记时明确失败；
11. 每个 Session 从 KP UI 执行 Session End；第一次 Continue 前由受管测试进程真正停止后端、
   使用同一 SQLite 启动新 PID，并在恢复页出现后执行 Continue；
12. 只能在玩家 UI 看到权威结局后成功。

脚本不调用业务 API，不写数据库，不注入契约或骰值。测试输入不进入产品分支，禁止按模组名、
人物名、地点名或示例行动修改生产代码。

## 密钥安全

复用已有数据库时，推荐先在本机 `/models` 页面手动保存模型配置，然后以
`AI_KP_REALCASE_USE_SAVED_MODEL=1` 执行。专用空白验收数据库中没有已保存配置，必须显式提供
KP 模型地址与模型 ID。无鉴权本地服务无需 API Key；真实用例会强制关闭 trace 与 video。

无鉴权的本地 OpenAI-compatible 服务可以不设置 `AI_KP_REALCASE_API_KEY`。只有在隔离 CI 已提供受保护 secret 时，才使用
`AI_KP_REALCASE_BASE_URL`、`AI_KP_REALCASE_API_KEY` 和 `AI_KP_REALCASE_MODEL`。不得把值写入
仓库、测试代码、截图、日志或提交信息。

## 执行

长跑必须使用测试自带的受管后端，不能复用常驻后端。Playwright 仍负责前端，测试本身负责启动、
停止和重启后端，并验证进程实例、PID、SQLite 存储身份和 Session End 快照没有漂移。
`AI_KP_PLAYWRIGHT_DB_PATH` 必须是专用 SQLite 的绝对路径，且不能同时设置
`AI_KP_PLAYWRIGHT_REUSE_SERVERS=1`。

在不运行真实验收时，不要执行下面命令。具备模型额度并准备正式取证后执行：

```bash
AI_KP_PLAYWRIGHT_MANAGED_BACKEND=1 \
AI_KP_PLAYWRIGHT_DB_PATH=/absolute/path/to/ac-long.sqlite3 \
AI_KP_REALCASE_BASE_URL=http://192.168.1.97:8001/v1 \
AI_KP_REALCASE_MODEL='<exact-model-id-from-/v1/models>' \
AI_KP_REALCASE_PLAYER_BASE_URL=http://192.168.1.97:8001/v1 \
AI_KP_REALCASE_PLAYER_MODEL='<same-exact-model-id>' \
AI_KP_REALCASE_MODULE_PATH=/absolute/path/to/module.docx \
AI_KP_REALCASE_SESSIONS=10 \
AI_KP_REALCASE_EVIDENCE_PATH=/absolute/path/to/evidence.json \
AI_KP_REALCASE_SOURCE_COMMIT=$(git rev-parse HEAD) \
pnpm exec playwright test e2e/full-ai-long-campaign.realcase.e2e.ts
```

玩家驱动模型是正式证据的必填项：后续回合由受限、只读玩家观察驱动的 Agent 选择行动，不能静默
退回固定脚本后仍声称完成真实 Full AI 长跑。首回合同窗动作仍由已发布契约选项建立可重复的多人
权威锚点。KP 与玩家 Agent 可以使用同一个本地小模型。

`AC-LONG` 验收的是连续 Campaign，不要求把一个短模组强行拖满十次 Session。若当前模组提前
到达权威结局，脚本会正常执行 Session End/Continue，并从
`AI_KP_REALCASE_MODULE_PATHS_JSON` 提供的下一份不同模组继续。例如：

```bash
AI_KP_REALCASE_MODULE_PATHS_JSON='["/absolute/first.docx","/absolute/second.pdf"]'
```

只提供单个 `AI_KP_REALCASE_MODULE_PATH` 仍向后兼容；若该模组在第十次 Session 前结束且没有
下一份来源，验收会明确失败，而不是重复同一本或伪造未发生的团次。

证据 JSON 只记录白名单权威 ID、模式、玩家数、Session、行为策略、玩家可见终态和不含密钥的
进程重启旁证，不记录密钥、私密提示、模型原始请求或 KP 秘密。快照指纹由只读重建器从
SQLite 重新计算，浏览器脚本不能自报。首次权威结局会在导入下一模组前冻结一个单模组
`single_session_anchor_evidence`；缺少该锚点的长跑必定拒绝。测试失败时仍会写出已完成部分，
便于修复后整轮重测。

证据重建与严格校验使用：

```bash
.venv/bin/python scripts/verify_real_long_ui_journey.py \
  /absolute/path/to/evidence.json \
  /absolute/path/to/ac-long.sqlite3 \
  --rps-fault-evidence /absolute/path/to/rps-fault.json \
  --output /absolute/path/to/verified-long-ui.json
```

严格重建通过后，校验器还会输出 `persona_evidence_candidates`。它只统计由玩家可见状态驱动、
`driver_source=model` 且到达终态的行动；首回合固定多人锚点、模型失败后的确定性 fallback 和
缺少交互计数的记录都不能证明 persona。候选记录绑定原始证据 SHA-256 与 source commit，供最终
schema v2 门禁合并。GM persona 不会从 Full AI 玩家脚本推断，必须另由真实 GM UI 轨迹证明。
校验器只有在 Session 0 全桌确认、资深规则工具链、以及 `ai_assist → human_kp → ai_assist`
控制事件分别存在时，才生成三类 GM persona 候选。

同一校验器还输出 `journey_coverage_evidence_candidates`，逐项重建探索、NPC 对话、调查、成功/
失败检定、战斗、物品/战利品、规则外行动、重大选择、成长、死亡、换角、中途入团和玩家离队。
语义行动必须来自真实玩家 Agent；战斗、物品和生命周期还必须同时存在 UI 旁证与 SQLite 权威 ID。
任一类别缺失都不能靠 session 数量或脚本自报补齐。

`RPS-07` 使用独立的玩家浏览器故障轨迹，不能由长团 runner 自报。该用例把模型端点固定为不可达
的本机回环地址，验证玩家看到“尚未执行”、没有确认按钮、可以改写行动，且行动仅停在待审状态、
公开权威回合数量完全不变。它不调用 8001，也不读取模组：

```bash
cd apps/web
AI_KP_RPS_FAULT_EVIDENCE_PATH=/absolute/path/to/rps-fault.json \
AI_KP_REALCASE_SOURCE_COMMIT=$(git rev-parse HEAD) \
pnpm exec playwright test e2e/no-kp-play.e2e.ts
```

正式长团、故障轨迹、非 UI foundation 和三轮完整重测必须来自同一最终 source commit。重测报告
包含 `source_commit`、`open_defects`，以及至少三条 `rounds`；每轮必须记录 `id`、
`input_version`、`evidence_ref`、`evidence_sha256`、`full_retest_passed` 和 `remaining_risks`。
`evidence_ref` 必须是绝对路径，且引用不可变 JSON 记录。最终装配命令为：

```bash
.venv/bin/python scripts/assemble_long_campaign_release.py \
  /absolute/path/to/foundation.json \
  /absolute/path/to/verified-long-ui.json \
  /absolute/path/to/repair-report.json \
  --output /absolute/path/to/ac-long-release.json
```

装配器会实际读取 persona、RPS、旅程覆盖和每轮重测引用的文件并重算 SHA-256；文件缺失、摘要
漂移、跨 commit 混用、重复声明、少于四名玩家、少于十个 ended episode、P0/P1 未清零或任一
AC-LONG 门禁未闭合时均不会生成发布文件。基础 V4 历史证据来自旧 commit，只能用于说明既有
基础，不能直接与最终长团拼接；正式发布必须在最终 commit 重新生成 foundation。

## 发布判定

长团脚本与独立故障脚本为 `AC-LONG` 第 1、2、4、6、7、8、9 项提供真实 UI 驱动，但只有实际观察到的分支才算
证据；它不替代 20 Session/50 小时 durability、50 个 edge cases、关闭模型重放或三轮完整重测。每轮结果应登记到
[PRD_IMPLEMENTATION_AUDIT.md](PRD_IMPLEMENTATION_AUDIT.md)，并保留 commit、模组来源哈希、
模型配置世代和剩余风险。
