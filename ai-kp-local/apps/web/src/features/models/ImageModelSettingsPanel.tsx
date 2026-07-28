import { Image, Save, Search, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { requestJson } from "../../api/client";

type ImageModelSettings = {
  base_url: string | null;
  model: string;
  timeout_seconds: number;
  api_key_configured: boolean;
  persisted: boolean;
  updated_at?: string;
};

type ImageModelDiscovery = {
  models: string[];
  normalized_base_url: string;
};

export function ImageModelSettingsPanel() {
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8188/v1");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyConfigured, setApiKeyConfigured] = useState(false);
  const [model, setModel] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("300");
  const [models, setModels] = useState<string[]>([]);
  const [message, setMessage] = useState("正在读取图片模型设置……");
  const [busy, setBusy] = useState(false);

  const payload = useMemo(() => ({
    base_url: baseUrl.trim(),
    api_key: apiKey.trim() || null,
    model: model.trim(),
    timeout_seconds: Number(timeoutSeconds || 300)
  }), [apiKey, baseUrl, model, timeoutSeconds]);

  function applySettings(settings: ImageModelSettings) {
    setBaseUrl(settings.base_url || "http://127.0.0.1:8188/v1");
    setModel(settings.model || "");
    setTimeoutSeconds(String(settings.timeout_seconds || 300));
    setApiKeyConfigured(settings.api_key_configured);
    setApiKey("");
  }

  async function loadSettings() {
    try {
      const settings = await requestJson<ImageModelSettings>("/image-model-settings");
      applySettings(settings);
      setMessage(
        settings.persisted
          ? "已读取本地保存的图片模型设置。"
          : "尚未单独配置图片模型；地图会继续使用确定性 SVG。"
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  useEffect(() => {
    void loadSettings();
  }, []);

  async function discover() {
    setBusy(true);
    setModels([]);
    try {
      const result = await requestJson<ImageModelDiscovery>(
        "/image-model-settings/discover",
        {
          method: "POST",
          body: JSON.stringify(payload)
        }
      );
      setModels(result.models);
      setBaseUrl(result.normalized_base_url);
      if (result.models.length && !result.models.includes(model)) {
        setModel(result.models[0]);
      }
      setMessage(
        result.models.length
          ? `连接成功，服务返回 ${result.models.length} 个模型 ID；请选择具备图片生成能力的模型。`
          : "连接成功，但 /models 没有返回可用模型 ID。"
      );
      return result;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function saveSettings() {
    setBusy(true);
    try {
      let savePayload = payload;
      if (!savePayload.model) {
        const discovery = await requestJson<ImageModelDiscovery>(
          "/image-model-settings/discover",
          {
            method: "POST",
            body: JSON.stringify(savePayload)
          }
        );
        if (!discovery.models.length) {
          throw new Error("服务没有返回图片模型 ID，请手动填写后再保存。");
        }
        const detectedModel = discovery.models[0];
        setModels(discovery.models);
        setModel(detectedModel);
        setBaseUrl(discovery.normalized_base_url);
        savePayload = {
          ...savePayload,
          base_url: discovery.normalized_base_url,
          model: detectedModel
        };
      }
      const settings = await requestJson<ImageModelSettings>(
        "/image-model-settings",
        {
          method: "PUT",
          body: JSON.stringify(savePayload)
        }
      );
      applySettings(settings);
      setMessage("图片模型设置已保存；重新打开地图即可生成背景候选。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page-card image-model-settings-card">
      <div className="page-intro">
        <div>
          <p className="eyebrow">独立视觉提供者</p>
          <h2>地图图片模型</h2>
          <p>
            图片模型只生成不含文字与秘密的公共背景；地点、路线、标签和棋子继续由结构图层控制。
          </p>
        </div>
        <Image size={27} />
      </div>

      <div className="model-settings-form image-model-settings-form">
        <label>
          OpenAI-compatible 图片地址
          <input
            placeholder="http://127.0.0.1:8188/v1"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
          />
          <small>需要提供 /v1/models 与 /v1/images/generations。</small>
        </label>
        <label>
          API Key
          <input
            autoComplete="off"
            placeholder={apiKeyConfigured ? "已保存；留空表示保持不变" : "无鉴权服务可以留空"}
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
          />
        </label>
        <label>
          图片模型 ID
          <input
            list="discovered-image-models"
            placeholder="请选择实际返回且支持图片生成的模型"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          />
          <datalist id="discovered-image-models">
            {models.map((item) => <option key={item} value={item} />)}
          </datalist>
        </label>
        <label>
          生成超时（秒）
          <input
            max="1800"
            min="10"
            step="10"
            type="number"
            value={timeoutSeconds}
            onChange={(event) => setTimeoutSeconds(event.target.value)}
          />
        </label>
      </div>

      {models.length > 0 && (
        <div className="discovered-model-list">
          <strong>服务返回的模型</strong>
          {models.map((item) => (
            <button
              className={model === item ? "active" : ""}
              key={item}
              onClick={() => setModel(item)}
              type="button"
            >
              {item}
            </button>
          ))}
        </div>
      )}

      <div className="inline-actions model-settings-actions">
        <button
          className="secondary-button"
          disabled={busy || !baseUrl.trim()}
          onClick={() => void discover()}
          type="button"
        >
          <Search size={16} />
          检测图片服务模型
        </button>
        <button
          className="primary-button"
          disabled={busy || !baseUrl.trim()}
          onClick={() => void saveSettings()}
          type="button"
        >
          <Save size={16} />
          {model.trim() ? "保存图片模型" : "检测并保存图片模型"}
        </button>
      </div>

      <div className="image-model-safety-note">
        <ShieldCheck size={16} />
        <span>API Key 仅保存在后端数据库，不会回传；模型生成失败时仍保留原 SVG 地图。</span>
      </div>
      <p className="inline-message">{message}</p>
    </section>
  );
}
