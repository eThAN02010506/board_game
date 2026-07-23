DEBUG_DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AI KP · Debug Console</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #09110f;
      --panel: #101c18;
      --panel-2: #14241e;
      --line: #294238;
      --text: #edf5f0;
      --muted: #91a89e;
      --green: #70d19d;
      --gold: #e8bd67;
      --blue: #73b8e8;
      --red: #f08b80;
      --code: #07100d;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 80% -10%, rgba(52, 126, 91, .22), transparent 32rem),
        radial-gradient(circle at 0 20%, rgba(166, 113, 39, .12), transparent 28rem),
        var(--bg);
      color: var(--text);
      font: 13px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }
    button, input, select, textarea { font: inherit; }
    button, a.button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      min-height: 34px;
      border: 1px solid #3b5c4e;
      border-radius: 8px;
      background: #183429;
      color: var(--text);
      padding: 7px 11px;
      text-decoration: none;
      cursor: pointer;
    }
    button:hover, a.button:hover { border-color: var(--green); background: #204435; }
    button.secondary { background: transparent; }
    button.danger { border-color: #74433d; background: #412522; }
    button:disabled { cursor: not-allowed; opacity: .48; }
    input, select, textarea {
      width: 100%;
      border: 1px solid #314b41;
      border-radius: 8px;
      outline: 0;
      background: #0b1512;
      color: var(--text);
      padding: 9px 10px;
    }
    input:focus, select:focus, textarea:focus { border-color: var(--green); }
    textarea { resize: vertical; }
    code, pre { font-family: "SFMono-Regular", Consolas, monospace; }
    code { color: #b7dac8; }
    .shell { width: min(1580px, calc(100% - 32px)); margin: 0 auto; padding: 22px 0 60px; }
    .topbar {
      position: sticky;
      z-index: 30;
      top: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      margin: -22px 0 18px;
      border-bottom: 1px solid rgba(61, 94, 80, .8);
      backdrop-filter: blur(14px);
      background: rgba(9, 17, 15, .88);
      padding: 14px 0;
    }
    .brand { display: flex; align-items: center; gap: 11px; }
    .brand-mark {
      display: grid;
      width: 38px;
      height: 38px;
      place-items: center;
      border: 1px solid #4e715f;
      border-radius: 11px;
      background: linear-gradient(145deg, #204937, #142a22);
      color: var(--gold);
      font-weight: 900;
    }
    .brand h1 { margin: 0; font-size: 17px; letter-spacing: -.02em; }
    .brand small { display: block; color: var(--muted); font-size: 10px; }
    .actions { display: flex; flex-wrap: wrap; gap: 7px; }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      box-shadow: 0 0 0 5px rgba(112, 209, 157, .1);
      background: var(--green);
    }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(300px, .6fr);
      gap: 14px;
      margin-bottom: 14px;
    }
    .hero-copy, .hero-help {
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: linear-gradient(135deg, rgba(27, 60, 47, .9), rgba(14, 29, 24, .94));
      padding: 22px;
    }
    .hero-copy { position: relative; }
    .hero-copy::after {
      position: absolute;
      right: -70px;
      bottom: -120px;
      width: 260px;
      height: 260px;
      border: 1px solid rgba(232, 189, 103, .18);
      border-radius: 50%;
      content: "";
    }
    .eyebrow { margin: 0 0 4px; color: var(--gold); font-size: 10px; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; }
    .hero h2 { margin: 0; font-size: clamp(24px, 3vw, 38px); letter-spacing: -.04em; }
    .hero-copy > p:last-child { max-width: 800px; margin-bottom: 0; color: #bed0c7; }
    .hero-help { display: grid; align-content: center; gap: 8px; }
    .hero-help div { display: flex; justify-content: space-between; gap: 12px; color: var(--muted); }
    .hero-help strong { color: var(--text); }
    .metrics {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .metric, .panel {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(16, 28, 24, .92);
    }
    .metric { min-width: 0; padding: 13px; }
    .metric span { display: block; overflow: hidden; color: var(--muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
    .metric strong { display: block; overflow: hidden; margin-top: 4px; color: var(--green); font-size: 20px; text-overflow: ellipsis; white-space: nowrap; }
    .grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; }
    .span-4 { grid-column: span 4; }
    .span-5 { grid-column: span 5; }
    .span-6 { grid-column: span 6; }
    .span-7 { grid-column: span 7; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: 1 / -1; }
    .panel { min-width: 0; overflow: hidden; }
    .panel-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--line);
      padding: 14px 16px;
    }
    .panel-head h3 { margin: 0; font-size: 14px; }
    .panel-head p { margin: 2px 0 0; color: var(--muted); font-size: 10px; }
    .panel-body { padding: 15px 16px; }
    .toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    .toolbar input { min-width: 180px; flex: 1; }
    .kv { display: grid; grid-template-columns: minmax(120px, .65fr) minmax(0, 1.35fr); gap: 0; margin: 0; }
    .kv dt, .kv dd { min-width: 0; margin: 0; border-bottom: 1px solid #21372e; padding: 8px 0; }
    .kv dt { color: var(--muted); }
    .kv dd { overflow-wrap: anywhere; color: #d8e7e0; }
    .badge {
      display: inline-flex;
      align-items: center;
      border: 1px solid #3b5b4d;
      border-radius: 999px;
      background: #152a22;
      color: var(--green);
      padding: 3px 7px;
      font-size: 9px;
      font-weight: 800;
      letter-spacing: .04em;
    }
    .badge.get { color: var(--blue); }
    .badge.post { color: var(--green); }
    .badge.put, .badge.patch { color: var(--gold); }
    .badge.delete { color: var(--red); }
    .badge.ws { color: #ca9ff0; }
    .scroll { max-height: 450px; overflow: auto; }
    .routes, .tables { display: grid; gap: 7px; margin-top: 12px; }
    .route, .table-row {
      display: grid;
      align-items: center;
      gap: 8px;
      border: 1px solid #263e34;
      border-radius: 8px;
      background: #0d1915;
      padding: 8px;
    }
    .route { grid-template-columns: 56px minmax(0, 1fr) auto; }
    .route code { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .table-row { grid-template-columns: minmax(0, 1fr) auto auto; }
    .table-row small, .route small { color: var(--muted); }
    .form-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; }
    label { display: grid; align-content: start; gap: 5px; color: var(--muted); font-size: 10px; }
    label.wide { grid-column: span 3; }
    label.full { grid-column: 1 / -1; }
    .result {
      min-height: 130px;
      max-height: 520px;
      overflow: auto;
      margin: 12px 0 0;
      border: 1px solid #274136;
      border-radius: 9px;
      background: var(--code);
      color: #cfe7da;
      padding: 12px;
      font-size: 11px;
      white-space: pre-wrap;
      word-break: break-word;
    }
    table { width: 100%; border-collapse: collapse; font-size: 11px; }
    th, td { border-bottom: 1px solid #243a31; padding: 7px 8px; text-align: left; vertical-align: top; }
    th { position: sticky; top: 0; background: #14231d; color: var(--muted); }
    td code { white-space: nowrap; }
    .ok { color: var(--green); }
    .warn { color: var(--gold); }
    .bad { color: var(--red); }
    .muted { color: var(--muted); }
    .empty { color: var(--muted); text-align: center; padding: 22px; }
    .quick { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
    .quick button { min-height: 28px; padding: 4px 8px; font-size: 10px; }
    .switch { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
    .switch input { width: auto; }
    .footer { margin-top: 18px; color: var(--muted); font-size: 10px; text-align: center; }
    @media (max-width: 1160px) {
      .metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .span-4, .span-5, .span-6, .span-7, .span-8 { grid-column: span 6; }
    }
    @media (max-width: 760px) {
      .shell { width: min(100% - 20px, 1580px); }
      .topbar, .hero { grid-template-columns: 1fr; }
      .topbar { position: static; align-items: flex-start; flex-direction: column; margin-top: 0; }
      .hero { display: grid; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .span-4, .span-5, .span-6, .span-7, .span-8 { grid-column: 1 / -1; }
      .form-grid { grid-template-columns: 1fr; }
      label.wide, label.full { grid-column: auto; }
      .route { grid-template-columns: 50px minmax(0, 1fr); }
      .route button { grid-column: 1 / -1; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark">KP</span>
        <div><h1>AI KP Debug Console</h1><small>本机服务诊断与接口实验台 · 127.0.0.1:8002</small></div>
      </div>
      <nav class="actions">
        <span class="button"><i class="status-dot"></i><span id="live-status">正在连接</span></span>
        <a class="button" href="/docs" target="_blank" rel="noreferrer">OpenAPI</a>
        <a class="button" href="/redoc" target="_blank" rel="noreferrer">ReDoc</a>
        <a class="button" href="http://127.0.0.1:5173" target="_blank" rel="noreferrer">前端</a>
        <button class="secondary" id="refresh-all">立即刷新</button>
      </nav>
    </header>

    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">LOCAL ADMINISTRATOR ONLY</p>
        <h2>后端状态，一页看清</h2>
        <p>查看模型、数据库、API、实时请求与本地运行日志；也可以直接执行接口、解析 Excel 角色卡、检查 SQLite 完整性以及连接团会话 WebSocket。</p>
      </div>
      <div class="hero-help">
        <div><span>自动刷新</span><label class="switch"><input id="auto-refresh" type="checkbox" checked>每 8 秒</label></div>
        <div><span>快照时间</span><strong id="snapshot-time">—</strong></div>
        <div><span>管理范围</span><strong>仅本机 / 已脱敏</strong></div>
      </div>
    </section>

    <section class="metrics">
      <article class="metric"><span>服务状态</span><strong id="metric-service">—</strong></article>
      <article class="metric"><span>运行时间</span><strong id="metric-uptime">—</strong></article>
      <article class="metric"><span>累计请求</span><strong id="metric-requests">—</strong></article>
      <article class="metric"><span>SQLite 记录</span><strong id="metric-rows">—</strong></article>
      <article class="metric"><span>API / WS 路由</span><strong id="metric-routes">—</strong></article>
      <article class="metric"><span>模型状态</span><strong id="metric-model">—</strong></article>
    </section>

    <section class="grid">
      <article class="panel span-6">
        <div class="panel-head"><div><h3>系统与配置</h3><p>敏感值只显示是否已配置</p></div><button class="secondary" id="export-snapshot">导出快照</button></div>
        <div class="panel-body"><dl class="kv" id="system-kv"><dt>状态</dt><dd>正在读取…</dd></dl></div>
      </article>

      <article class="panel span-6">
        <div class="panel-head"><div><h3>模型服务探针</h3><p>检查 OpenAI-compatible / MLX 模型列表与延迟</p></div><button id="probe-model">运行探针</button></div>
        <div class="panel-body">
          <dl class="kv" id="model-kv"><dt>状态</dt><dd>正在读取…</dd></dl>
          <pre class="result" id="model-result">点击“运行探针”执行实时连接检查。</pre>
        </div>
      </article>

      <article class="panel span-5">
        <div class="panel-head"><div><h3>SQLite 数据浏览器</h3><p>表结构、记录数与脱敏行预览</p></div><button id="check-db">完整性检查</button></div>
        <div class="panel-body">
          <div class="toolbar"><input id="table-search" placeholder="筛选数据表"><span class="badge" id="table-total">0 TABLES</span></div>
          <div class="tables scroll" id="table-list"><p class="empty">正在读取…</p></div>
          <pre class="result" id="db-result">选择数据表查看最多 30 行，令牌、哈希与密钥字段会自动隐藏。</pre>
        </div>
      </article>

      <article class="panel span-7">
        <div class="panel-head"><div><h3>API 路由浏览器</h3><p>搜索全部 HTTP / WebSocket 入口并载入请求实验台</p></div><span class="badge" id="route-total">0 ROUTES</span></div>
        <div class="panel-body">
          <div class="toolbar">
            <input id="route-search" placeholder="搜索路径、标签或说明">
            <select id="route-method"><option value="">全部方法</option><option>GET</option><option>POST</option><option>PUT</option><option>PATCH</option><option>DELETE</option><option>WS</option></select>
          </div>
          <div class="routes scroll" id="route-list"><p class="empty">正在读取…</p></div>
        </div>
      </article>

      <article class="panel span-12" id="request-lab">
        <div class="panel-head"><div><h3>API 请求实验台</h3><p>所有请求只发往当前 8002 服务；非 GET 请求执行前会再次确认</p></div><button id="run-request">发送请求</button></div>
        <div class="panel-body">
          <div class="quick">
            <button data-quick="GET|/health">健康检查</button>
            <button data-quick="GET|/capabilities">能力清单</button>
            <button data-quick="GET|/campaigns">团列表</button>
            <button data-quick="GET|/model-settings">模型配置</button>
            <button data-quick="GET|/model-runtime">模型进程</button>
            <button data-quick="GET|/rulebooks/sources">规则书</button>
            <button data-quick="GET|/investigator-skills/catalog">技能目录</button>
          </div>
          <div class="form-grid">
            <label>方法<select id="request-method"><option>GET</option><option>POST</option><option>PUT</option><option>PATCH</option><option>DELETE</option></select></label>
            <label class="wide">路径<input id="request-path" value="/health" spellcheck="false"></label>
            <label class="full">请求头 JSON<textarea id="request-headers" rows="3" spellcheck="false" placeholder='{"Authorization":"Bearer …","X-AI-KP-Player-Token":"…"}'>{}</textarea></label>
            <label class="full">请求体 JSON<textarea id="request-body" rows="7" spellcheck="false" placeholder="GET 请求可留空"></textarea></label>
          </div>
          <pre class="result" id="request-result">准备就绪。</pre>
        </div>
      </article>

      <article class="panel span-6">
        <div class="panel-head"><div><h3>Excel 角色卡解析探针</h3><p>无需创建玩家档案，直接测试后端安全解析器</p></div><button id="parse-xlsx">解析文件</button></div>
        <div class="panel-body">
          <label>选择 .xlsx 文件<input id="xlsx-file" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"></label>
          <pre class="result" id="xlsx-result">文件只会发送到本机 8002 服务，不执行公式、宏或外部链接。</pre>
        </div>
      </article>

      <article class="panel span-6">
        <div class="panel-head"><div><h3>实时 WebSocket 探针</h3><p>使用 /realtime/tickets 返回的一次性 ticket</p></div><div class="toolbar"><button id="ws-connect">连接</button><button class="danger" id="ws-close">断开</button></div></div>
        <div class="panel-body">
          <div class="form-grid">
            <label class="wide">Ticket<input id="ws-ticket" type="password" autocomplete="off" placeholder="一次性 realtime ticket"></label>
            <label>游标<input id="ws-cursor" placeholder="可选 after_cursor"></label>
          </div>
          <div class="quick"><button id="ws-ping">发送 ping</button><span class="badge" id="ws-state">CLOSED</span></div>
          <pre class="result" id="ws-result">等待连接。</pre>
        </div>
      </article>

      <article class="panel span-8">
        <div class="panel-head"><div><h3>最近请求</h3><p>仅保存在内存中的最近 250 条，不记录查询参数和请求体</p></div><button class="secondary" id="refresh-requests">刷新</button></div>
        <div class="panel-body scroll">
          <table><thead><tr><th>时间</th><th>方法</th><th>路径</th><th>状态</th><th>耗时</th><th>客户端</th></tr></thead><tbody id="request-rows"><tr><td colspan="6" class="empty">暂无记录</td></tr></tbody></table>
        </div>
      </article>

      <article class="panel span-4">
        <div class="panel-head"><div><h3>本地模型日志</h3><p>model-runtime.log 最后 120 行</p></div><button class="secondary" id="refresh-logs">刷新</button></div>
        <div class="panel-body"><pre class="result" id="log-result">正在读取…</pre></div>
      </article>
    </section>
    <p class="footer">AI KP Local Debug Console · 所有诊断接口均受本机管理员检查保护 · 不会展示 API Key、访问令牌或哈希原文</p>
  </main>

  <script>
    const appState = { diagnostics: null, ws: null, timer: null };
    const $ = (id) => document.getElementById(id);
    const escapeHtml = (value) => String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
    const formatBytes = (value) => {
      if (!Number.isFinite(Number(value))) return "—";
      const units = ["B", "KB", "MB", "GB", "TB"];
      let size = Number(value), index = 0;
      while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
      return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
    };
    const formatDuration = (seconds) => {
      const value = Math.max(0, Number(seconds) || 0);
      const hours = Math.floor(value / 3600);
      const minutes = Math.floor((value % 3600) / 60);
      const secs = Math.floor(value % 60);
      return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${secs}s` : `${secs}s`;
    };
    const pretty = (value) => JSON.stringify(value, null, 2);
    const setResult = (id, value) => { $(id).textContent = typeof value === "string" ? value : pretty(value); };

    async function request(path, options = {}) {
      const started = performance.now();
      const response = await fetch(path, { cache: "no-store", ...options });
      const text = await response.text();
      let data;
      try { data = text ? JSON.parse(text) : null; } catch { data = text; }
      return {
        ok: response.ok,
        status: response.status,
        statusText: response.statusText,
        duration_ms: Number((performance.now() - started).toFixed(2)),
        headers: Object.fromEntries(response.headers.entries()),
        data,
      };
    }

    function renderKv(id, entries) {
      $(id).innerHTML = entries.map(([key, value]) =>
        `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
    }

    function renderDiagnostics(data) {
      appState.diagnostics = data;
      $("live-status").textContent = "服务正常";
      $("snapshot-time").textContent = new Date(data.service.time_utc).toLocaleTimeString();
      $("metric-service").textContent = data.service.status.toUpperCase();
      $("metric-uptime").textContent = formatDuration(data.telemetry.uptime_seconds);
      $("metric-requests").textContent = data.telemetry.total_requests;
      $("metric-rows").textContent = data.database.total_rows.toLocaleString();
      $("metric-routes").textContent = data.routes.length;
      $("metric-model").textContent = data.model.runtime.state.toUpperCase();
      renderKv("system-kv", [
        ["服务 / 版本", `${data.service.name} ${data.service.version}`],
        ["Python / 平台", `${data.service.python} · ${data.service.machine}`],
        ["进程 / CPU", `PID ${data.service.pid} · ${data.service.cpu_count ?? "?"} cores`],
        ["数据库", data.filesystem.database_path],
        ["数据库大小", formatBytes(data.filesystem.database_size_bytes)],
        ["SQLite / Schema", `${data.database.sqlite_version} · v${data.database.latest_supported_schema}`],
        ["Journal / FK", `${data.database.journal_mode} · ${data.database.foreign_keys ? "ON" : "OFF"}`],
        ["RAG 索引", data.filesystem.rulebook_index_root],
        ["CORS", data.settings.cors_origins.join(", ") || "未配置"],
        ["管理员令牌", data.settings.admin_token_configured ? "已配置（隐藏）" : "未配置，本机回环地址放行"],
        ["磁盘可用", data.filesystem.disk ? formatBytes(data.filesystem.disk.free_bytes) : "—"],
      ]);
      const config = data.model.configuration;
      renderKv("model-kv", [
        ["Provider", config.provider_type],
        ["Base URL", config.base_url || "本地 MLX runtime"],
        ["Model", config.model || "未配置"],
        ["API Key", config.api_key_configured ? "已配置（隐藏）" : "未配置"],
        ["Runtime", `${data.model.runtime.state} · ${data.model.runtime.available ? "mlx-lm available" : "mlx-lm unavailable"}`],
        ["Local Path", config.local_model_path || "—"],
        ["Port / PID", `${config.local_port || "—"} / ${data.model.runtime.pid || "—"}`],
      ]);
      renderTables();
      renderRoutes();
    }

    function renderTables() {
      const tables = appState.diagnostics?.database.tables || [];
      const query = $("table-search").value.trim().toLowerCase();
      const visible = tables.filter((table) => table.name.toLowerCase().includes(query));
      $("table-total").textContent = `${tables.length} TABLES`;
      $("table-list").innerHTML = visible.length ? visible.map((table) =>
        `<div class="table-row"><div><code>${escapeHtml(table.name)}</code><small> · ${table.columns.length} columns</small></div><span class="badge">${table.rows} ROWS</span><button data-table="${escapeHtml(table.name)}">预览</button></div>`
      ).join("") : '<p class="empty">没有匹配的数据表</p>';
    }

    function renderRoutes() {
      const routes = appState.diagnostics?.routes || [];
      const query = $("route-search").value.trim().toLowerCase();
      const method = $("route-method").value;
      const visible = routes.filter((route) => {
        const haystack = `${route.path} ${route.name || ""} ${(route.tags || []).join(" ")} ${route.summary || ""}`.toLowerCase();
        return (!query || haystack.includes(query)) && (!method || route.methods.includes(method));
      });
      $("route-total").textContent = `${routes.length} ROUTES`;
      $("route-list").innerHTML = visible.length ? visible.map((route) => {
        const routeMethod = route.methods[0] || "GET";
        return `<div class="route"><span class="badge ${routeMethod.toLowerCase()}">${escapeHtml(route.methods.join("/"))}</span><div><code>${escapeHtml(route.path)}</code><small>${escapeHtml(route.summary || route.name || "")}</small></div><button data-route-method="${escapeHtml(routeMethod)}" data-route-path="${escapeHtml(route.path)}" ${routeMethod === "WS" ? "disabled" : ""}>载入</button></div>`;
      }).join("") : '<p class="empty">没有匹配的路由</p>';
    }

    async function refreshDiagnostics() {
      try {
        const result = await request("/debug/diagnostics");
        if (!result.ok) throw new Error(pretty(result.data));
        renderDiagnostics(result.data);
      } catch (error) {
        $("live-status").textContent = "连接失败";
        $("metric-service").textContent = "ERROR";
        setResult("model-result", `诊断请求失败：${error.message}`);
      }
    }

    async function refreshRequests() {
      const result = await request("/debug/requests?limit=80");
      const rows = result.data?.requests || [];
      $("request-rows").innerHTML = rows.length ? rows.map((item) => {
        const statusClass = item.status >= 500 ? "bad" : item.status >= 400 ? "warn" : "ok";
        return `<tr><td>${escapeHtml(new Date(item.at).toLocaleTimeString())}</td><td><code>${escapeHtml(item.method)}</code></td><td><code>${escapeHtml(item.path)}</code></td><td class="${statusClass}">${item.status}</td><td>${item.duration_ms} ms</td><td>${escapeHtml(item.client || "—")}</td></tr>`;
      }).join("") : '<tr><td colspan="6" class="empty">暂无记录</td></tr>';
    }

    async function refreshLogs() {
      const result = await request("/debug/logs?lines=120");
      if (!result.ok) return setResult("log-result", result);
      const lines = result.data.lines || [];
      setResult("log-result", lines.length ? lines.join("\n") : "暂无本地模型运行日志。\n启动 MLX 模型后，stdout/stderr 会写入此处。" );
    }

    async function refreshAll() {
      await Promise.all([refreshDiagnostics(), refreshRequests(), refreshLogs()]);
    }

    $("table-search").addEventListener("input", renderTables);
    $("route-search").addEventListener("input", renderRoutes);
    $("route-method").addEventListener("change", renderRoutes);
    $("table-list").addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-table]");
      if (!button) return;
      setResult("db-result", `正在读取 ${button.dataset.table}…`);
      const result = await request(`/debug/database/tables/${encodeURIComponent(button.dataset.table)}?limit=30`);
      setResult("db-result", result);
    });
    $("route-list").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-route-path]");
      if (!button) return;
      $("request-method").value = button.dataset.routeMethod;
      $("request-path").value = button.dataset.routePath;
      $("request-lab").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    document.querySelectorAll("button[data-quick]").forEach((button) => button.addEventListener("click", () => {
      const [method, path] = button.dataset.quick.split("|");
      $("request-method").value = method;
      $("request-path").value = path;
    }));

    $("run-request").addEventListener("click", async () => {
      const method = $("request-method").value;
      const path = $("request-path").value.trim();
      if (!path.startsWith("/")) return setResult("request-result", "路径必须以 / 开头。");
      if (method !== "GET" && !confirm(`确认向本机服务发送 ${method} ${path}？`)) return;
      let headers, body;
      try { headers = JSON.parse($("request-headers").value || "{}"); }
      catch (error) { return setResult("request-result", `请求头不是合法 JSON：${error.message}`); }
      if (method !== "GET" && $("request-body").value.trim()) {
        try { body = JSON.stringify(JSON.parse($("request-body").value)); }
        catch (error) { return setResult("request-result", `请求体不是合法 JSON：${error.message}`); }
        headers["Content-Type"] ||= "application/json";
      }
      setResult("request-result", "请求中…");
      try { setResult("request-result", await request(path, { method, headers, body })); }
      catch (error) { setResult("request-result", `网络错误：${error.message}`); }
      refreshRequests();
    });

    $("probe-model").addEventListener("click", async () => {
      $("probe-model").disabled = true;
      setResult("model-result", "正在探测模型服务（超时 4 秒）…");
      try { setResult("model-result", await request("/debug/model/probe")); }
      finally { $("probe-model").disabled = false; }
    });
    $("check-db").addEventListener("click", async () => {
      $("check-db").disabled = true;
      setResult("db-result", "正在执行 PRAGMA quick_check 与 foreign_key_check…");
      try { setResult("db-result", await request("/debug/database/check")); }
      finally { $("check-db").disabled = false; }
    });
    $("parse-xlsx").addEventListener("click", async () => {
      const file = $("xlsx-file").files[0];
      if (!file) return setResult("xlsx-result", "请先选择 .xlsx 文件。");
      $("parse-xlsx").disabled = true;
      setResult("xlsx-result", `正在解析 ${file.name}（${formatBytes(file.size)}）…`);
      try {
        const result = await request("/debug/xlsx/preview", {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream", "X-File-Name": encodeURIComponent(file.name) },
          body: file,
        });
        if (result.data?.canonical_sheet) {
          const sheet = result.data.canonical_sheet;
          setResult("xlsx-result", {
            http: { status: result.status, duration_ms: result.duration_ms },
            source_filename: result.data.source_filename,
            template_id: result.data.template_id,
            parser_version: result.data.parser_version,
            ignored_formula_cells: result.data.ignored_formula_cells,
            warnings: result.data.warnings,
            summary: {
              investigator: sheet.identity?.name,
              characteristics: Object.keys(sheet.characteristics || {}).length,
              skills: (sheet.skills || []).length,
              weapons: (sheet.combat?.weapons || []).length,
              items: (sheet.assets?.items || []).length,
              derived: sheet.derived,
            },
            canonical_sheet: sheet,
          });
        } else setResult("xlsx-result", result);
      } catch (error) { setResult("xlsx-result", `解析失败：${error.message}`); }
      finally { $("parse-xlsx").disabled = false; }
    });

    function wsLog(value) {
      const output = $("ws-result");
      output.textContent += `${output.textContent ? "\n" : ""}${new Date().toLocaleTimeString()} ${typeof value === "string" ? value : pretty(value)}`;
      output.scrollTop = output.scrollHeight;
    }
    $("ws-connect").addEventListener("click", () => {
      const ticket = $("ws-ticket").value.trim();
      if (!ticket) return setResult("ws-result", "请先填写一次性 realtime ticket。");
      if (appState.ws && appState.ws.readyState < 2) appState.ws.close();
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(`${protocol}//${location.host}/debug/ws`);
      appState.ws = socket;
      $("ws-result").textContent = "";
      $("ws-state").textContent = "CONNECTING";
      socket.addEventListener("open", () => {
        $("ws-state").textContent = "AUTHENTICATING";
        socket.send(JSON.stringify({ type: "authenticate", ticket, after_cursor: $("ws-cursor").value.trim() || undefined }));
        $("ws-ticket").value = "";
        wsLog("WebSocket 已连接，认证帧已发送。ticket 已从输入框清除。");
      });
      socket.addEventListener("message", (event) => {
        let payload = event.data;
        try { payload = JSON.parse(event.data); } catch {}
        if (payload?.type === "realtime.ready") $("ws-state").textContent = "READY";
        if (payload?.type === "realtime.ping") socket.send(JSON.stringify({ type: "pong" }));
        wsLog(payload);
      });
      socket.addEventListener("error", () => { $("ws-state").textContent = "ERROR"; wsLog("WebSocket 错误"); });
      socket.addEventListener("close", (event) => { $("ws-state").textContent = "CLOSED"; wsLog(`连接关闭：${event.code} ${event.reason || ""}`); });
    });
    $("ws-close").addEventListener("click", () => appState.ws?.close(1000, "Debug console closed"));
    $("ws-ping").addEventListener("click", () => {
      if (!appState.ws || appState.ws.readyState !== WebSocket.OPEN) return wsLog("当前未连接。");
      appState.ws.send(JSON.stringify({ type: "ping" }));
      wsLog("→ ping");
    });

    $("export-snapshot").addEventListener("click", () => {
      if (!appState.diagnostics) return;
      const blob = new Blob([pretty(appState.diagnostics)], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `ai-kp-diagnostics-${new Date().toISOString().replaceAll(":", "-")}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    });
    $("refresh-all").addEventListener("click", refreshAll);
    $("refresh-requests").addEventListener("click", refreshRequests);
    $("refresh-logs").addEventListener("click", refreshLogs);
    $("auto-refresh").addEventListener("change", (event) => {
      clearInterval(appState.timer);
      appState.timer = event.target.checked ? setInterval(refreshAll, 8000) : null;
    });

    refreshAll();
    appState.timer = setInterval(refreshAll, 8000);
  </script>
</body>
</html>
"""


__all__ = ["DEBUG_DASHBOARD_HTML"]
