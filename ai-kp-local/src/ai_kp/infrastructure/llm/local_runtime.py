"""Local MLX model-process adapter with explicit lifecycle and log capture."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from threading import RLock
from typing import IO, Any

from ai_kp.infrastructure.llm.model_configuration import validate_local_model_path


class LocalModelRuntime:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: IO[bytes] | None = None
        self._model_path: str | None = None
        self._port: int | None = None
        self._lock = RLock()

    @property
    def available(self) -> bool:
        return importlib.util.find_spec("mlx_lm") is not None

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            return_code = process.poll() if process else None
            if process is None:
                state = "stopped"
            elif return_code is None:
                state = "running"
            else:
                state = "exited"
            return {
                "state": state,
                "available": self.available,
                "pid": process.pid if process and return_code is None else None,
                "return_code": return_code,
                "model_path": self._model_path,
                "port": self._port,
                "log_path": str(self.log_path),
            }

    def start(self, model_path: str, port: int) -> dict[str, Any]:
        model_info = validate_local_model_path(model_path)
        if not self.available:
            raise RuntimeError(
                "当前后端未安装 mlx-lm；请安装项目的 local-model 可选依赖后重启后端"
            )
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                if self._model_path == model_info["resolved_path"] and self._port == port:
                    return self.status()
                raise RuntimeError("已有本地模型正在运行，请先停止后再切换")
            self._close_log()
            path = Path(str(model_info["resolved_path"]))
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log_path.open("ab")
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "mlx_lm",
                    "server",
                    "--model",
                    path.name,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--chat-template-args",
                    '{"enable_thinking":false}',
                ],
                cwd=path.parent,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self._model_path = str(path)
            self._port = port
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self._process = None
            self._model_path = None
            self._port = None
            self._close_log()
        return self.status()

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
