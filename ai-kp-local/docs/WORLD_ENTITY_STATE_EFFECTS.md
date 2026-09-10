# 规则效果与模板实体状态

状态只有一个权威来源：Campaign 世界实体账本。AI KP 与真人 KP 选择同一份已发布合同中的动作后，单人和并行结算都由 `ScenarioEffectService` 消费持久化批次，再调用 `WorldEntityStateService`。

## 合同命令

```json
{
  "kind": "set_world_entity_state",
  "entity_id": "duty-officer",
  "path": "cooperation",
  "value": "willing",
  "payload": {"visibility": "kp"}
}
```

- `entity_id` 必须是合同的实体 ID，且 EntitySpec 已显式绑定当前模组的 `module_entity_id`。
- `path` 是原型声明的状态维度，不是事实路径。不按名称匹配，不创造未物化实体，不猜测态度与合作程度之间的关系。
- `value` 支持文本、整数、布尔值；`null` 表示清除。可见性必须明确，且不能比实体本身更公开。
- 合同编译检查命令形状和身份绑定；首次执行再检查当前来源图、精确物化身份、合法维度与可见性。
- 并行写同实体同维度而值或可见性不一致时拒绝整批；相同赋值可合并。

## 提交与重试

规则快照仅记录 `world_entity_state_requested` 效果请求，不存第二份模板状态。实际写入带 `rules_kernel` 来源，保留 KP 审批身份、事件、版本及不可变收据。角色规则效果、世界实体效果和规则批次处于同一外层事务；任意效果失败会一起回滚。

幂等键来自持久化批次 ID 和命令序号。即使已经是目标值，也追加一次收据和版本；否则旧请求可能在后来人工修改之后首次执行。重试使用收据中的原始版本校验命令哈希，不用当前版本重新执行。已提交收据的重放不依赖当前活动模组或后续合同覆盖层。

数据库 v91 只扩展状态收据的来源枚举，迁移保留历史数据和索引；不新增运行依赖。采用 SQLite 嵌套保存点，内层释放仍可被外层回滚，参见 [SQLite 保存点](https://www.sqlite.org/lang_savepoint.html)；表约束升级采用重建并复制数据，参见 [SQLite 表结构变更](https://www.sqlite.org/lang_altertable.html)。

## 模型编制目录

模型编制可用更短的 `actions.world_state_effects` 记录，不必选择命令种类和存储路径。例如：

```json
{
  "entity_id": "duty-officer",
  "dimension": "cooperation",
  "value": "willing",
  "visibility": "kp",
  "applies_on": "success"
}
```

`entity_id` 是本次声明的本地实体 ID；对应实体仍须绑定目录提供的模组 ID。时机沿用当前编制 IR 的 always/success/failure/pushed_failure 四个分支。服务器在动作分析前显式转换成共享命令，清空已转换的记录以免重复追加；原输入保持不变。已有原始命令继续兼容。重复赋值仅在类型和值都相同时合并，结果超出原有分支预算则拒绝，不截断效果。值使用严格类型，参见 [Pydantic 严格类型](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/)。

编制证据现在附带 `entity_candidates`，按已确认实体的来源引用关联到原文块。候选提供精确 `module_entity_id`、名称、类型、可用维度与状态可见性下限；不附带当前状态值或历史。维度来自活动模组选定的固定版本 Setting Profile，已有物化实体则以实际账本维度为准。未选中的配置不会自动被采用，构建目录不会物化实体。

编制 IR 保留显式来源 ID；组装后的来源引用、维度、可见性都再次检查，审核替换和任务恢复也使用同一校验。同名但不同来源的实体不能通过名称归并。已声明的模板维度不得被 `set_fact` 写到 `entities.<id>.<dimension>` 等另一个状态位置。

目录进入生成指纹，生成期间图实体、维度或可见性下限变化会使旧任务失效。空目录保持旧指纹计算方式。每块最多 64 个候选，候选文本计入分区字符预算；过大目录明确拒绝，不悄悄截掉候选后让模型猜测。

实现采用现有 Pydantic 形状校验加显式的目录成员检查，而不是把完整动态枚举塞进每个 JSON Schema；这种分离保留稳定 IR 和可解释的错误。对照参考 [Pydantic 校验机制](https://pydantic.dev/docs/validation/latest/concepts/validators/)。

## 尚未完成的闭环

规则条件读取模板状态仍未接通。当前不应把这条写入链路解释为已实现完整的双向世界规则系统。动态行动 IR 也未开放此命令；它通过审核后的合同效果执行。目录和短记录可用不等于模型能可靠选择：本地 20B 合成探针仍出现状态写成普通事实、混用字段与漏填动作数据，校验均不允许这些错误进入执行；仍需要更细的编制步骤与语义评估。完整真实模组 UI 跑团验收仍待完成。
