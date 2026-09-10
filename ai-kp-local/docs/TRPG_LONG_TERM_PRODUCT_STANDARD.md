# 长期 TRPG 产品来源映射

> 状态：非规范性来源索引；原“长期 TRPG 产品标准”已并入主 PRD
> 迁移日期：2026-08-21

## 1. 权威说明

本文件不再定义独立产品标准。唯一规范产品源是
[`PRODUCT_REQUIREMENTS.md`](PRODUCT_REQUIREMENTS.md)；实现状态以
[`src/ai_kp/planning/capabilities.py`](../src/ai_kp/planning/capabilities.py) 为准；
逐项证据与缺口见 [`PRD_IMPLEMENTATION_AUDIT.md`](PRD_IMPLEMENTATION_AUDIT.md)。

用户提供的《设计并打磨一个真正可长期使用的跑团 App》prompt 已逐字归档为
[`references/LONG_TERM_TRPG_APP_PROMPT_2026-08-21.txt`](references/LONG_TERM_TRPG_APP_PROMPT_2026-08-21.txt)，
SHA-256 为 `4aa285a82756b88d200661fb0979923ca997393c65aa5988a5519311d24604f9`。
归档原文用于追溯产品意图，不能与主 PRD 形成第二套要求。

## 2. 来源到主 PRD 的映射

| 原 prompt 主题 | 主 PRD 规范位置 |
|---|---|
| 北极星、完整 Campaign、50 小时后仍可用、Depth > Feature Count | 第 2 节、`AC-LONG` |
| Player、GM、Observer 与真实多人 | 第 4 节、FR-02、FR-17 |
| Session 0、风格期待、Lines/Veils/X-Card | FR-16 |
| 角色创建、关系、成长、负伤、死亡、换角 | FR-03、FR-21 |
| 自然语言、低摩擦 UI、Rule of Cool、任意行动 | FR-06、FR-09、FR-12、RPS-02..05 |
| 程序 / AI / GM 决策边界与 Full AI 自动审核 | 第 5 节、FR-05、FR-13A、FR-14、FR-15 |
| 骰子、可见性、暗骰与重投 | FR-08 |
| 战斗、回合、掉线、挂机、规则外行动 | FR-19 |
| 长期世界、分层记忆与 NPC 知识边界 | FR-10、FR-11 |
| Theater of the Mind、战术地图、迷雾 | FR-07、FR-12、FR-14 |
| 物品、经济、战利品、诅咒和隐藏属性 | FR-20 |
| 玩家加入/退出、Observer 转换 | FR-17、FR-21 |
| Session End、双摘要、Continue、Previously on... | FR-18 |
| 社交体验、私聊、通知与可选语音 | FR-17、FR-13B |
| 玩家情绪、失败也有趣、AI 幻觉防护 | FR-05、FR-09、RPS-01..12 |
| 五类玩家、三类 GM、乱来/极端测试 | `AC-LONG` |
| 10 Session 脚本、20 Session/50h durability、三轮迭代 | `AC-LONG` |
| P0–P4 Bug 分类 | 第 11.1 节 |
| 完整用户旅程、Player/GM flow、状态/AI/UX 架构 | 第 2、5、6、8 节及 `ARCHITECTURE.md` |

## 3. 维护规则

- 新产品要求只编辑主 PRD，并给出稳定 FR、RPS 或 AC 标识。
- 本文件只在来源映射变化时更新，不复制主 PRD 正文或实现状态。
- 如果本文件、README、能力目录或专题设计与主 PRD 冲突，以主 PRD 为产品要求；
  状态冲突则以 capability catalogue 为准，并修复漂移。
