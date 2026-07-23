from pathlib import Path

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from ai_kp.llm.local_runtime import LocalModelRuntime
from ai_kp.llm.model_configuration import (
    normalize_openai_base_url,
    validate_local_model_path,
)


def _model_directory(root: Path) -> Path:
    model_path = root / "test-mlx-model"
    model_path.mkdir()
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    (model_path / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (model_path / "model.safetensors").write_bytes(b"weights")
    return model_path


def _settings(db_path: Path) -> Settings:
    return Settings(
        db_path=db_path,
        local_admin_enabled=False,
        admin_token="model-admin",
        llm_base_url="http://env-model.local:8000/v1",
        llm_api_key="env-secret",
        llm_model="env-model",
    )


def test_model_configuration_persists_and_replaces_runtime_settings(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "models.sqlite3"
    model_path = _model_directory(tmp_path)
    headers = {"X-AI-KP-Admin-Token": "model-admin"}

    app = create_app(_settings(db_path))
    with TestClient(app) as client:
        saved = client.put(
            "/model-settings",
            headers=headers,
            json={
                "provider_type": "local_mlx",
                "local_model_path": str(model_path),
                "local_port": 8111,
                "model": "",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["model"] == "test-mlx-model"
        assert saved.json()["local_model_path"] == str(model_path.resolve())
        assert app.state.settings.llm_base_url == "http://127.0.0.1:8111/v1"
        assert app.state.settings.llm_model == "test-mlx-model"

    restarted = create_app(_settings(db_path))
    with TestClient(restarted) as client:
        loaded = client.get("/model-settings", headers=headers)
        assert loaded.status_code == 200
        assert loaded.json()["provider_type"] == "local_mlx"
        assert restarted.state.settings.llm_base_url == "http://127.0.0.1:8111/v1"


def test_remote_model_configuration_redacts_api_key(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path / "remote.sqlite3"))
    headers = {"X-AI-KP-Admin-Token": "model-admin"}

    with TestClient(app) as client:
        saved = client.put(
            "/model-settings",
            headers=headers,
            json={
                "provider_type": "openai_compatible",
                "base_url": "http://192.168.1.97:8001",
                "api_key": "new-secret",
                "model": "gpt-oss-20b",
            },
        )
        loaded = client.get("/model-settings", headers=headers)

    assert saved.status_code == 200
    assert app.state.settings.llm_base_url == "http://192.168.1.97:8001/v1"
    assert app.state.settings.llm_api_key == "new-secret"
    assert loaded.json()["api_key_configured"] is True
    assert "api_key" not in loaded.json()


def test_local_model_path_validation_and_url_normalization(tmp_path: Path) -> None:
    model_path = _model_directory(tmp_path)

    info = validate_local_model_path(str(model_path))

    assert info["model_name"] == "test-mlx-model"
    assert info["weight_files"] == 1
    assert normalize_openai_base_url("http://localhost:8001") == (
        "http://localhost:8001/v1"
    )


def test_local_runtime_uses_argument_list_without_shell(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model_path = _model_directory(tmp_path)
    captured: dict = {}

    class FakeProcess:
        pid = 4321
        return_code = None

        def poll(self):
            return self.return_code

        def terminate(self):
            self.return_code = 0

        def wait(self, timeout):
            return self.return_code

        def kill(self):
            self.return_code = -9

    def fake_popen(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("ai_kp.llm.local_runtime.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr("ai_kp.llm.local_runtime.subprocess.Popen", fake_popen)
    runtime = LocalModelRuntime(tmp_path / "runtime.log")

    started = runtime.start(str(model_path), 8112)
    stopped = runtime.stop()

    assert started["state"] == "running"
    assert captured["arguments"][-9:] == [
        "server",
        "--model",
        "test-mlx-model",
        "--host",
        "127.0.0.1",
        "--port",
        "8112",
        "--chat-template-args",
        '{"enable_thinking":false}',
    ]
    assert "shell" not in captured["kwargs"]
    assert captured["kwargs"]["cwd"] == model_path.parent
    assert stopped["state"] == "stopped"
