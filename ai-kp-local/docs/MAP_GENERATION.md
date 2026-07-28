# 地图生成、时代约束与图片资产

## 目标与不变量

地图流水线需要同时满足四个目标：

1. **能玩**：图片模型离线、超时、漏画或文件损坏时，地点、路线、标签和棋子仍可使用。
2. **符合年代**：团时间、地区、季节、天气、建筑材料、可用技术和禁止出现的现代物件进入结构化时代档案。
3. **包含全部元素**：必含地点和场景元素由确定性校验及 SVG 覆盖，不通过“看起来像画到了”猜测。
4. **不泄露秘密**：公共背景的 MapSpec、提示词、图片文件和 API 响应都不包含 KP 专属信息。

因此地图不是“一张 AI 图片”，而是分层结果：

```text
KP 输入 + 团时间
        ↓
MapSpec v1（结构事实源）
        ↓
结构/几何/路线/覆盖/时代校验
        ↓
SQLite revision + 地点/路线兼容投影
        ↓
确定性 SVG ───────────────────────┐
        ↓                         │
玩家安全 MapSpec 投影             │
        ↓                         │
可选图片模型 → 候选 PNG/JPEG      │
        ↓                         │
KP 预览并选择                      │
        └──── 背景 + SVG + 棋子 ──┘
```

图片只负责纸张/地面材质、灯光、家具氛围和美术风格。空间结构、标签、路线、隐藏层与棋子必须继续由程序控制。

## MapSpec v1

`src/ai_kp/platform/scenes/map_spec.py` 定义当前版本 `map-spec.v1`。核心字段包括：

- `map_kind`：`regional`、`site` 或 `floorplan`；
- `canvas`：统一逻辑坐标系；
- `era`：年份、地域、季节、时间、天气、已确认公开的建筑、技术和由年份确定性产生的公共时代禁忌；
- `locations`：稳定元素 ID、位置、可见性、公开描述及 KP 备注；
- `connections`：两端地点、通行与可见性；
- `features`：家具、环境物件或其他必需场景元素；
- `visual_brief`：俯视视角、风格预设、配色、灯光、文字与人物策略；
- `coverage.required_element_names`：必须进入结构地图的元素；
- `provenance`：来源种类、团时间、提示版本和只供 KP 审核的自由文本视觉排除项。

规范 JSON 使用稳定键序列化并保存：

- `spec_hash`：完整 MapSpec 内容哈希；
- `layout_hash`：影响结构/渲染的布局哈希；
- `generation_input_hash`：玩家安全投影、图片提示、Provider、模型、尺寸和 seed 的组合哈希。

棋子移动不创建结构 revision；它只更新棋子 optimistic `version`。当前阶段还没有结构编辑 API，因此创建地图产生 revision 1，后续“编辑并生成新 revision”仍属于部分能力。

## 确定性校验

保存前必须通过以下检查：

- Schema 版本和地图类型有效；
- 画布达到最小尺寸；
- 地点 ID、名称与场景元素 ID 不为空且不重复；
- 地点坐标不越界；
- 路线两端均引用现有地点；
- 可见性只使用 `player`、`table`、`kp`；
- 未连通区域产生明确警告；
- 场景元素只引用现有地点；
- `required_elements` 全部存在于地点或场景元素；
- 年代未知时产生警告并采用保守的中性时代提示。

校验错误阻止保存和发布；警告留给 KP 审核。覆盖率由名称 manifest 计算，达到
`100%` 表示每个必含元素进入了 MapSpec/SVG，不表示图片模型的每个像素都经过语义识别。

## 年代与美术约束

年份优先使用请求中的 `era_year`，其次从团时间、标题和描述中推断。当前提供四段保守默认：

- 1880 年以前；
- 1880–1945；
- 1946–1989；
- 1990 年以后。

例如 1928 年档案会允许有线电话、钨丝灯、机械打字机和相应年代车辆，同时禁止
LED、液晶屏、CCTV、现代塑料家具、战后车辆和数字标牌。KP 可以通过
`public_architecture` 追加已确认可公开的地域建筑；该字段会展示给玩家并发送给图片模型。
自由填写的禁止元素只进入 KP 审核记录，不发送给图片模型，因为“不要画地下祭坛”这类
负面提示本身也会泄露秘密。

图片提示固定要求正交俯视、无文字、无人物、无棋子、无秘密房间，并为程序标签保留可读空间。时代约束能降低明显错误，但“合理、好看”不能由单元测试证明；候选不会自动选中，最终由 KP 预览判断。

## 秘密信息边界

公共图片生成调用 `project_map_spec(spec, ("player", "table"))`。投影会：

- 删除 KP 地点；
- 删除连接到隐藏地点的路线；
- 删除 KP 场景元素；
- 删除 `scene_brief`、`provenance` 和所有 `kp_notes`；
- 重新计算公共必含元素；
- 不包含棋子、行动、NPC、线索或模组原文。

玩家 API 也只返回该安全投影和由其重新渲染的 SVG。玩家只能下载：

1. 属于当前团；
2. 地图已经发布；
3. 是该地图当前选中的公共背景

的图片。猜中其他候选的 `asset_id` 仍返回 404。

## 图片 Provider 与文件安全

图片模型有独立端口 `MapImageProvider`，不复用聊天用 `LlmClient`。当前适配器要求
OpenAI-compatible：

```http
POST <AI_KP_IMAGE_BASE_URL>/images/generations
```

请求使用 `response_format=b64_json`。本地安全模式不下载 Provider 返回的任意远程 URL。
响应会经过：

- 严格 Base64 解码；
- PNG/JPEG 文件签名和尺寸读取；
- 32 MiB 文件上限；
- 16,777,216 像素上限；
- SHA-256 内容哈希；
- 受控根目录内的临时文件 + 原子重命名。

文件路径形如：

```text
data/map-assets/sha256/ab/<sha256>.png
```

SQLite 只保存相对路径与审计元数据。相同 `generation_input_hash` 且缓存文件仍存在时，
不会再次调用图片模型；数据库有记录但文件丢失时会重新生成并修复该缓存记录。未指定
seed 时服务会先生成并返回一个具体随机 seed，因此“新候选”不会意外命中旧缓存。

配置示例：

```env
AI_KP_IMAGE_BASE_URL=http://127.0.0.1:8188/v1
AI_KP_IMAGE_API_KEY=local
AI_KP_IMAGE_MODEL=<真实图片模型 ID>
AI_KP_MAP_ASSET_ROOT=data/map-assets
```

没有配置图片 Provider 时，前端禁用“生成背景候选”，但确定性时代 SVG、路线和棋子仍完整可用。

## 数据与 API

主要存储：

- `maps`：稳定身份、发布状态、当前 revision、已选公共图片；
- `map_revisions`：MapSpec、校验结果、`spec_hash`、`layout_hash`；
- `map_locations` / `map_routes`：移动系统使用的当前兼容投影；
- `map_assets`：生成哈希、内容哈希、相对路径、尺寸、Provider、模型、seed 和提示审计；
- `map_tokens` / `map_token_moves`：棋子位置、版本与移动历史。

主要接口：

```http
POST /campaigns/{campaign_id}/maps/generate
GET  /maps/{map_id}
GET  /maps/{map_id}/image-prompt
POST /maps/{map_id}/image-assets/generate
POST /maps/{map_id}/assets/{asset_id}/select
GET  /map-assets/{asset_id}/content
POST /maps/{map_id}/publish
POST /maps/{map_id}/unpublish
```

生成候选只发 KP 专属 `map.asset_ready` 实时事件；选中背景才发全团可见的
`map.changed`。候选生成失败不会替换当前背景，也不会改变发布状态。发布请求必须携带
KP 实际审核过的 `expected_revision_id` 与 `expected_selected_asset_id`；服务端在同一
写事务中核对版本、正式背景和校验报告，避免审核后内容被并发替换。

## 前端渲染与恢复

`MapStage` 在一个 SVG `viewBox` 中放置：

1. 已选或正在预览的背景 Blob；
2. 玩家/KP 对应的确定性 SVG；
3. 棋子。

缩放作用于整个坐标系，避免图片、标签和棋子各自缩放造成错位。图片通过带 Bearer
鉴权的 Blob 请求读取；切换候选或地图时会释放旧 Object URL。读取失败只隐藏背景，
并显示安全回退提示。

刷新页面或重启后端后，地图 revision、选中图片、结构和棋子从 SQLite/资产目录恢复。
浏览器只记住最后选择的地图 ID。

v13 迁移会为旧地图补建 revision。旧版本曾允许、但不符合当前 MapSpec 约束的数据会
连同失败校验报告一起保留供 KP 查看，不会因为单张旧地图而阻断整个数据库启动。

## 自动测试与 real case

后端：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_map_spec.py \
  tests/test_map_image_api.py \
  tests/test_map_storage.py \
  tests/test_map_workflow.py
```

前端：

```bash
cd apps/web
pnpm test
pnpm run build
```

当前自动 real case 使用“1928 年马萨诸塞州小镇警局”，验证：

- 九个地点/场景元素覆盖率 100%；
- 年代与程序生成的时代禁忌进入安全提示，KP 自由文本排除项不会进入；
- 生成两个不同 seed 候选；
- KP 预览、选中并发布；
- 玩家只能读取选中图片，不能读取未选候选；
- 玩家 MapSpec 不含 KP provenance/scene brief；
- 应用重启后 revision 与图片字节仍可恢复。

测试 Provider 使用固定合法 PNG，验证的是完整业务路径、缓存、权限和持久化。只有连接
真实图片模型并由 KP 检查年代、构图、元素氛围与美观后，才算视觉 real-case 通过；
当前代码不能诚实地自动宣称审美质量已经得到证明。

## 当前限制与下一阶段

当前能力仍标记为部分可用，主要缺口是：

- MapSpec 结构编辑、revision 2+ 和并发编辑保护；
- 房间墙体、门窗、多层建筑与可拖动布局编辑器；
- 按玩家/队伍保存的揭示状态、迷雾和隐藏 overlay；
- ComfyUI/ControlNet 布局控制图适配器；
- 图片任务队列、进度、失败重试和取消；
- 真实本地图像模型的固定视觉验收集；
- 备份/恢复时同时打包数据库与 `map-assets`。

下一步应先实现“地图结构编辑 → 新 revision → token/揭示引用完整性”，再接
ComfyUI/ControlNet。这样图片质量提升不会破坏当前已经稳定的权限和玩法事实层。
