# 世界事实账本

## 定位

世界事实账本不是另一套可变状态表。`events` 仍是团世界的权威记录，事实能力只定义两种保留事件，并从事件链投影当前状态：

- `world_fact.asserted`
- `world_fact.retconned`

`memories` 继续负责相关内容召回，不承担事实真伪；它不能覆盖事实 head。

模组知识中的 `module_canon` 和 `module_anchor` 是带原文来源及剧透范围的故事约束，
不属于这个运行事实分类。AI 世界补全只能先成为 proposal 候选；KP 批准或玩家实际
确认后，才通过本账本追加运行事实。完整权威顺序见
[`WORLD_EXPANSION.md`](WORLD_EXPANSION.md)。

## 事实类别

| 类别 | 含义 | 可见范围 |
| --- | --- | --- |
| `canonical_fact` | 已公开确认的世界事实 | 全桌 |
| `kp_secret` | 已确认但尚未公开的世界事实 | KP |
| `character_belief` | 指定角色当前相信的内容，不保证真实 | 该角色与 KP |
| `rumor` | 桌面已知的传闻，不保证真实 | 全桌 |
| `ai_hypothesis` | AI 的待验证推测，绝不是正式事实 | KP |
| `retconned` | 对上一事实 revision 的撤回说明 | 继承原事实范围 |

固定范围由领域模型和专用服务共同校验。角色认知在通用事件表中按 KP 可见存储，因为旧事件读取器不理解 `pc_id`；只有事实投影器可以把它返回给对应角色。

## 追加与并发

每条事实链拥有稳定 `fact_key`，每个 revision 使用事件 ID。纠错请求必须提交当前 `expected_head_event_id`：

1. 服务在短写事务中读取当前 head。
2. 过期 head 直接失败，不生成分叉。
3. 新 `retconned` revision 指向上一事件。
4. 原事件保留不变；当前投影默认隐藏已撤回链。
5. KP 可用 `include_history=true` 查看完整审计历史。

通用 `/events` 写入和 AI 的普通 `proposed_events` 都不能伪造 `world_fact.*` 保留事件；它们必须经过事实服务。

AI 与人工回合可以提交独立的 `proposed_facts`。每项候选都必须通过与事实 API 相同的
类型、长度和角色范围校验。存在未解决 `proposed_checks` 时，候选事实与其他世界效果
一样必须为空，避免提前把成功后果写成真相。

批准普通或检定后果 proposal 时，服务先取得短写事务，再执行现有效果、重新检查事实
冲突、追加事实事件、记录 `proposed_facts_applied` 审计和 realtime outbox。任何候选
无效、证据跨团、角色不属于当前团或权威事实冲突，整次批准都会回滚，proposal 保持
`draft`。世界补全 proposal 仍是例外：批准只认可候选，必须经过玩家实际接触接口才可
落地事实。

## API

```http
POST /campaigns/{campaign_id}/facts
GET  /campaigns/{campaign_id}/facts
GET  /campaigns/{campaign_id}/facts/{fact_key}
POST /campaigns/{campaign_id}/facts/{fact_key}/retcon
```

独立前端 `/facts` 使用这些接口。KP 可以筛选当前 head、查看历史 revision、人工追加
和撤回；玩家只能读取公开 head 与本人角色认知，不能读取 `source_reference`、
`evidence_event_ids`、`asserted_by` 或 KP 历史。

断言示例：

```json
{
  "fact_type": "character_belief",
  "subject": "林若川",
  "predicate": "相信",
  "object_text": "报社线人持有仓库钥匙",
  "pc_id": "pc_...",
  "evidence_event_ids": ["evt_..."],
  "source_reference": {"kind": "kp_observation"}
}
```

纠错示例：

```json
{
  "expected_head_event_id": "evt_...",
  "reason": "此前把 NPC 的说法误记成了事实。"
}
```

## AI 上下文

`ContextBuilder` 只加入当前有效 head，并将类别、角色范围和 revision 写入上下文来源审计。系统提示明确规定：

- `canonical_fact`、`kp_secret` 才是已确认事实。
- `character_belief`、`rumor`、`ai_hypothesis` 不得被自行升级为事实。
- 已 `retconned` 的旧内容不进入正常 AI 上下文。

## 后续扩展

- 从已审核模组实体导入事实候选。
- 在工作台中提供并排冲突比较，以及“撤回旧事实并建立替代事实”的一次性原子操作。
- 真人团验证筛选、来源展示和人工确认负担后，再决定默认视图与自动化强度。

## 实现依据

- [SQLite Transactions](https://www.sqlite.org/lang_transaction.html) 与
  [SQLite Savepoints](https://www.sqlite.org/lang_savepoint.html)：proposal 效果、
  事实追加和 outbox 使用同一外层事务；内部 savepoint 释放不代表已独立提交。
- [OWASP API1:2023 Broken Object Level Authorization](https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/)：
  每个 campaign/fact 标识都需要服务端对象级授权，不能依靠不可预测 ID。
- [OWASP API3:2023 Broken Object Property Level Authorization](https://owasp.org/API-Security/editions/2023/en/0xa3-broken-object-property-level-authorization/)：
  玩家响应使用显式安全投影，不能把完整事实对象交给前端后再隐藏敏感字段。
