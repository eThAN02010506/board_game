# 动态支线生命周期

动态支线用于处理模组没有逐字描述、但在当前世界中合理发生的连续反应。它不是第二套
World Fact，也不能让模型绕过 KP 审批。

## 状态与边界

```text
AI 结构化候选
  -> KP 批准（approved）
  -> 玩家实际接触（active，条件不满足则 paused）
  -> 逐节拍记录 succeeded / failed / skipped
  -> completed / paused / abandoned
```

- `environment` 补全可以没有支线计划。
- `reactive_branch` 必须包含目标、进入条件、因果节拍和完成条件。
- `anchor_bridge` 还必须引用当前模组图中已有的剧情锚点。
- 审批只创建 `dynamic_branch_runs`；玩家实际接触前不会启动支线。
- 节拍效果永远是候选。正式事实、NPC、地图与实体状态继续使用原有人工确认接口。

## 一致性

- 计划在模型输出、审批和激活阶段分别校验。
- 实体、World Fact 与锚点引用只能来自生成时的确定性快照。
- 激活和每个节拍都会重新读取当前场景、时间、实体状态、Fact 头和模组图可达性。
- 前置条件或锚点保护失败时，支线安全暂停。
- 最后一个节拍只有在完成条件仍成立时才能进入 `completed`。
- 所有状态变化使用 `expected_version` 和 `command_id`；重试返回同一事件，不重复推进。

## 持久化与审计

- `dynamic_branch_runs` 保存当前投影和原始结构化计划。
- `dynamic_branch_events` 是顺序追加的状态变更记录。
- 支线进度事件明确保存 `world_writes_performed: false`。
- 实际接触仍由 `world_expansion_materializations` 保存幂等事实落地回执，并关联支线 ID。

## 权限

支线计划、条件、事件和控制接口全部只对当前 Campaign 的 KP 开放。玩家只能看到经现有
叙事、线索、地图和 World Fact 安全投影公开的结果。

## 主要接口

- `GET /campaigns/{campaign_id}/dynamic-branches`
- `GET /dynamic-branches/{branch_id}`
- `POST /dynamic-branches/{branch_id}/beats/resolve`
- `POST /dynamic-branches/{branch_id}/resume`
- `POST /dynamic-branches/{branch_id}/abandon`

审批与实际接触仍使用原有 Proposal 接口。前端场景导演页提供独立动态支线工作台，避免
把支线控制与模组原文编辑、严格事实落地混在一起。
