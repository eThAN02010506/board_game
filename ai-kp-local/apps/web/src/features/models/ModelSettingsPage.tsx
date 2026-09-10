import {
  CheckCircle2,
  Cpu,
  FolderOpen,
  Play,
  RefreshCw,
  Save,
  Search,
  Server,
  Square
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { requestJson } from "../../api/client";
import { ImageModelSettingsPanel } from "./ImageModelSettingsPanel";

type ProviderType = "openai_compatible" | "local_mlx";
type SemanticProfile = "small" | "large";

type RuntimeStatus = {
  state: "stopped" | "running" | "exited";
  available: boolean;
  pid: number | null;
  return_code: number | null;
  model_path: string | null;
  port: number | null;
  log_path: string;
};

type ModelSettings = {
  provider_type: ProviderType;
  base_url: string | null;
  model: string;
  local_model_path: string | null;
  local_port: number;
  semantic_profile: SemanticProfile;
  api_key_configured: boolean;
  persisted: boolean;
  version: number;
  updated_at?: string;
  runtime: RuntimeStatus;
};

type DiscoveryResult = {
  models: string[];
  normalized_base_url?: string;
  model_path?: {
    resolved_path: string;
    model_name: string;
    weight_files: number;
    size_bytes: number;
  };
  runtime: RuntimeStatus;
};

function formatBytes(value: number) {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GiB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${value} B`;
}

export function ModelSettingsPage() {
  const [providerType, setProviderType] = useState<ProviderType>("openai_compatible");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8001/v1");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyConfigured, setApiKeyConfigured] = useState(false);
  const [model, setModel] = useState("");
  const [localModelPath, setLocalModelPath] = useState("");
  const [localPort, setLocalPort] = useState("8011");
  const [semanticProfile, setSemanticProfile] = useState<SemanticProfile>("small");
  const [configurationVersion, setConfigurationVersion] = useState(0);
  const [models, setModels] = useState<string[]>([]);
  const [pathInfo, setPathInfo] = useState<DiscoveryResult["model_path"]>();
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [message, setMessage] = useState("正在读取后端模型设置……");
  const [busy, setBusy] = useState(false);

  const payload = useMemo(() => ({
    provider_type: providerType,
    base_url: providerType === "openai_compatible" ? baseUrl.trim() : null,
    api_key: apiKey.trim() || null,
    model: model.trim(),
    local_model_path: providerType === "local_mlx" ? localModelPath.trim() : null,
    local_port: Number(localPort || 8011),
    semantic_profile: semanticProfile
  }), [apiKey, baseUrl, localModelPath, localPort, model, providerType, semanticProfile]);

  function applySettings(settings: ModelSettings) {
    setProviderType(settings.provider_type);
    setBaseUrl(settings.base_url || "http://127.0.0.1:8001/v1");
    setModel(settings.model || "");
    setLocalModelPath(settings.local_model_path || "");
    setLocalPort(String(settings.local_port || 8011));
    setSemanticProfile(settings.semantic_profile || "small");
    setConfigurationVersion(settings.version);
    setApiKeyConfigured(settings.api_key_configured);
    setApiKey("");
    setRuntime(settings.runtime);
  }

  async function loadSettings() {
    try {
      const settings = await requestJson<ModelSettings>("/model-settings");
      applySettings(settings);
      setMessage(settings.persisted ? "已读取本地保存的模型设置。" : "当前使用环境变量中的默认模型设置。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  useEffect(() => {
    void loadSettings();
  }, []);

  useEffect(() => {
    if (providerType !== "local_mlx" || runtime?.state !== "running") return;
    const timer = window.setInterval(() => {
      void requestJson<RuntimeStatus>("/model-runtime")
        .then(setRuntime)
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [providerType, runtime?.state]);

  async function saveSettings(silent = false) {
    if (!silent) setBusy(true);
    try {
      let savePayload = payload;
      if (providerType === "openai_compatible" && !savePayload.model) {
        setMessage("正在连接服务并自动识别模型 ID……");
        const discovery = await requestJson<DiscoveryResult>("/model-settings/discover", {
          method: "POST",
          body: JSON.stringify(savePayload)
        });
        if (!discovery.models.length) {
          throw new Error("服务连接成功，但 /models 没有返回可用模型；请手动填写模型 ID。");
        }
        const detectedModel = discovery.models[0];
        setModels(discovery.models);
        setModel(detectedModel);
        setRuntime(discovery.runtime);
        savePayload = { ...savePayload, model: detectedModel };
      }
      const settings = await requestJson<ModelSettings>("/model-settings", {
        method: "PUT",
        body: JSON.stringify(savePayload)
      });
      applySettings(settings);
      setMessage("模型设置已保存并立即用于后续 AI 请求。");
      return settings;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      if (!silent) setBusy(false);
    }
  }

  async function discover() {
    setBusy(true);
    setModels([]);
    setPathInfo(undefined);
    try {
      const result = await requestJson<DiscoveryResult>("/model-settings/discover", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      setModels(result.models);
      setPathInfo(result.model_path);
      setRuntime(result.runtime);
      if (providerType === "local_mlx") {
        setMessage(result.models.length
          ? `本地服务已就绪，发现 ${result.models.length} 个模型。`
          : "模型目录有效；如尚未启动，请先保存并启动本地模型。"
        );
        if (!model.trim() && result.model_path) setModel(result.model_path.model_name);
      } else {
        setMessage(`连接成功，服务实际返回 ${result.models.length} 个模型 ID。`);
        if (result.models.length && !result.models.includes(model)) setModel(result.models[0]);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function startLocalModel() {
    setBusy(true);
    try {
      const saved = await saveSettings(true);
      if (!saved) return;
      const status = await requestJson<RuntimeStatus>("/model-runtime/start", { method: "POST" });
      setRuntime(status);
      setMessage(`本地模型进程已启动（PID ${status.pid}）；模型载入完成后再点击“检测模型”。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function stopLocalModel() {
    setBusy(true);
    try {
      const status = await requestJson<RuntimeStatus>("/model-runtime/stop", { method: "POST" });
      setRuntime(status);
      setMessage("本地模型进程已停止。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  const runtimeLabel = runtime?.state === "running"
    ? "本地进程运行中"
    : runtime?.state === "exited"
      ? `进程异常退出${runtime.return_code == null ? "" : `（${runtime.return_code}）`}`
      : "本地进程未启动";

  return <div className="model-settings-page">
    <section className="page-card model-settings-main">
      <div className="page-intro">
        <div><p className="eyebrow">运行时可切换</p><h2>模型来源与连接</h2><p>设置只保存在这台后端的 SQLite 中，保存后立即用于新发起的 AI KP、模组作者化和规则抽取 Agent。</p></div>
        <Cpu size={28} />
      </div>

      <div className="model-provider-grid">
        <button className={`model-provider-card ${providerType === "openai_compatible" ? "active" : ""}`} onClick={() => { setProviderType("openai_compatible"); setModel(""); setModels([]); setPathInfo(undefined); }} type="button"><Server size={21} /><strong>服务地址</strong><span>局域网、本机或其他 OpenAI-compatible API</span></button>
        <button className={`model-provider-card ${providerType === "local_mlx" ? "active" : ""}`} onClick={() => { setProviderType("local_mlx"); setModel(""); setModels([]); }} type="button"><FolderOpen size={21} /><strong>本地模型目录</strong><span>由本机后端启动 MLX 模型服务</span></button>
      </div>

      {providerType === "openai_compatible" ? <div className="model-settings-form">
        <label>OpenAI-compatible 地址<input placeholder="http://192.168.1.97:8001/v1" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /><small>可以省略末尾的 /v1，后端会自动规范化。</small></label>
        <label>API Key<input autoComplete="off" placeholder={apiKeyConfigured ? "已保存；留空表示保持不变" : "无鉴权服务可以留空"} type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label>
        <label>模型 ID<input list="discovered-models" placeholder="先检测服务，再选择实际返回的 ID" value={model} onChange={(event) => setModel(event.target.value)} /><datalist id="discovered-models">{models.map((item) => <option key={item} value={item} />)}</datalist></label>
      </div> : <div className="model-settings-form local-model-form">
        <label className="model-path-field">后端本机模型目录<input placeholder="/absolute/path/to/mlx-model" value={localModelPath} onChange={(event) => setLocalModelPath(event.target.value)} /><small>这是运行 FastAPI 后端的电脑路径，不是浏览器上传文件。</small></label>
        <label>本地端口<input max="65535" min="1024" type="number" value={localPort} onChange={(event) => setLocalPort(event.target.value)} /></label>
        <label>模型 ID（可选）<input placeholder="留空则使用目录名" value={model} onChange={(event) => setModel(event.target.value)} /></label>
      </div>}

      <div className="model-settings-form">
        <label>语义能力档位<select value={semanticProfile} onChange={(event) => setSemanticProfile(event.target.value as SemanticProfile)}><option value="small">小模型：只选择已有原子行动</option><option value="large">大模型：允许有界计划与扩展候选</option></select><small>档位只改变模型可提出的候选范围，不改变规则、事实和结局权限。</small></label>
      </div>

      {pathInfo && <div className="model-path-result"><CheckCircle2 size={18} /><div><strong>{pathInfo.model_name}</strong><span>{pathInfo.weight_files} 个权重文件 · {formatBytes(pathInfo.size_bytes)}</span><code>{pathInfo.resolved_path}</code></div></div>}
      {models.length > 0 && <div className="discovered-model-list"><strong>服务返回的模型</strong>{models.map((item) => <button className={model === item ? "active" : ""} key={item} onClick={() => setModel(item)} type="button">{item}</button>)}</div>}

      <div className="inline-actions model-settings-actions">
        <button className="secondary-button" disabled={busy || (providerType === "local_mlx" ? !localModelPath.trim() : !baseUrl.trim())} onClick={() => void discover()} type="button"><Search size={16} />{providerType === "local_mlx" ? "检查路径 / 检测模型" : "检测服务与模型"}</button>
        <button className="primary-button" disabled={busy || (providerType === "openai_compatible" ? !baseUrl.trim() : !localModelPath.trim())} onClick={() => void saveSettings()} type="button"><Save size={16} />{providerType === "openai_compatible" && !model.trim() ? "检测、保存并使用" : "保存并使用"}</button>
        {providerType === "local_mlx" && runtime?.state !== "running" && <button className="primary-button" disabled={busy || !localModelPath.trim()} onClick={() => void startLocalModel()} type="button"><Play size={16} />保存并启动本地模型</button>}
        {providerType === "local_mlx" && runtime?.state === "running" && <button className="danger-button" disabled={busy} onClick={() => void stopLocalModel()} type="button"><Square size={15} />停止本地模型</button>}
      </div>
      <p className="inline-message">{message}</p>
      <p className="permission-hint">
        当前执行世代：{configurationVersion > 0 ? `v${configurationVersion}` : "环境默认"}。
        保存新配置后，旧世代的在途 Agent 输出会被拒绝落库；后台契约会从不可变来源重新作者化。
      </p>
    </section>

    <aside className="page-card model-runtime-card">
      <div className="page-intro"><div><p className="eyebrow">本地运行状态</p><h2>{runtimeLabel}</h2></div><button aria-label="刷新模型状态" className="icon-button" disabled={busy} onClick={() => void loadSettings()} type="button"><RefreshCw size={16} /></button></div>
      <dl className="model-runtime-details">
        <div><dt>MLX 运行组件</dt><dd>{runtime?.available ? "已安装" : "未安装"}</dd></div>
        <div><dt>PID</dt><dd>{runtime?.pid ?? "—"}</dd></div>
        <div><dt>端口</dt><dd>{runtime?.port ?? localPort}</dd></div>
        <div><dt>日志</dt><dd><code>{runtime?.log_path || "—"}</code></dd></div>
      </dl>
      {!runtime?.available && <div className="runtime-install-note"><strong>首次使用本地目录前</strong><code>python -m pip install -e ".[local-model]"</code><span>安装完成后重启 FastAPI 后端。</span></div>}
      <p className="permission-hint">本地模型服务只监听后端机器的 127.0.0.1。页面只能提交模型目录，不会执行用户输入的命令。</p>
    </aside>
    <ImageModelSettingsPanel />
  </div>;
}
