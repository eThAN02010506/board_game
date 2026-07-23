# 后端 Debug 调试台

FastAPI 根路径 `/` 是本机诊断首页。它不替代玩家/KP 前端，也不改变正式 API；用途是开发、联调和 real-case test。

## 功能

- 系统快照：版本、Python、平台、进程、运行时间、磁盘与 SQLite 配置。
- 模型诊断：脱敏配置、本地 MLX 进程状态、`/models` 发现结果和连接延迟。
- 数据库浏览：所有业务表的字段和记录数、脱敏行预览、`quick_check` 与外键检查。
- API 浏览器：搜索全部 HTTP 与 WebSocket 路由，并将 HTTP 路由载入请求实验台。
- 请求实验台：自定义方法、路径、Header 和 JSON Body；非 GET 请求会二次确认。
- Excel 探针：直接调用本机安全解析器，展示身份、属性、技能、装备、警告与规范化角色卡。
- WebSocket 探针：使用 `/realtime/tickets` 生成的一次性 ticket 检查认证、心跳和事件流。
- 运行观测：内存中保留最近 250 个请求的状态和耗时，并显示本地模型日志末尾内容。

## 安全边界

- 所有 `/debug/*` HTTP 接口都执行 `require_local_admin`。
- 默认只允许回环地址；关闭本机管理员模式时需要 `X-AI-KP-Admin-Token`。
- 诊断 JSON 不返回 API Key。数据库浏览器会隐藏名称包含 token、hash、key、secret 或 password 的字段。
- 请求记录不保存查询参数、Header、请求体或响应体。
- Debug WebSocket 仍必须使用正式的一次性 realtime ticket。
- Debug 页面发送的请求只指向当前后端源，不提供任意远程代理。

## 8002 联调

```bash
PYTHONPATH=src .venv/bin/python -m uvicorn ai_kp.api.app:app \
  --host 127.0.0.1 --port 8002

cd apps/web
VITE_BACKEND_TARGET=http://127.0.0.1:8002 pnpm run dev
```

打开：

- Debug 调试台：`http://127.0.0.1:8002/`
- OpenAPI：`http://127.0.0.1:8002/docs`
- 玩家/KP 前端：`http://127.0.0.1:5173/`

模型服务地址（例如 `http://192.168.1.97:8001`）与 FastAPI 地址是两层不同配置：浏览器始终通过 5173 的 `/api` 代理连接 FastAPI，FastAPI 再连接被保存的模型服务。
