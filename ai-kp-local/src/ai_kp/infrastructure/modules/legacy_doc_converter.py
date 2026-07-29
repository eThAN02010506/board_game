"""Bounded LibreOffice adapter for legacy binary Word documents."""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import threading
from pathlib import Path
from typing import BinaryIO

from ai_kp.platform.modules.documents import MAX_MODULE_BYTES, OLE_CFB_SIGNATURE

CONVERTER_PID_FILENAME = "legacy-doc-converter.pid"
MAX_CONVERTER_DIAGNOSTIC_BYTES = 64 * 1024
_PROCESS_GRACE_SECONDS = 1.0
_READ_CHUNK_BYTES = 16 * 1024


class LegacyDocConversionError(ValueError):
    """A bounded, user-safe legacy Word conversion failure."""


class _BoundedOutput:
    def __init__(self, limit: int):
        self.limit = limit
        self.data = bytearray()
        self.exceeded = False

    def drain(self, stream: BinaryIO | None) -> None:
        if stream is None:
            return
        try:
            while chunk := stream.read(_READ_CHUNK_BYTES):
                remaining = self.limit - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.exceeded = True
        except (OSError, ValueError):
            return

    def text(self) -> str:
        return bytes(self.data).decode("utf-8", errors="replace").strip()


def convert_legacy_doc_to_docx(
    source_data: bytes,
    work_root: Path,
    *,
    converter_command: str,
    timeout_seconds: float,
) -> bytes:
    """Convert one validated OLE CFB document inside the parser subprocess."""

    if not source_data.startswith(OLE_CFB_SIGNATURE):
        raise LegacyDocConversionError("旧式 .doc 文件签名不正确")
    executable = _resolve_converter(converter_command)
    conversion_root = work_root / "legacy-doc-conversion"
    output_root = conversion_root / "output"
    profile_root = conversion_root / "profile"
    conversion_root.mkdir(mode=0o700)
    output_root.mkdir(mode=0o700)
    profile_root.mkdir(mode=0o700)
    source_path = conversion_root / "input.doc"
    source_path.write_bytes(source_data)
    source_path.chmod(0o400)
    output_path = output_root / "input.docx"
    pid_path = work_root / CONVERTER_PID_FILENAME

    arguments = [
        executable,
        f"-env:UserInstallation={profile_root.resolve().as_uri()}",
        "--headless",
        "--nologo",
        "--nodefault",
        "--norestore",
        "--convert-to",
        "docx:Office Open XML Text",
        "--outdir",
        str(output_root),
        str(source_path),
    ]
    environment = os.environ.copy()
    environment["HOME"] = str(conversion_root)
    environment["TMPDIR"] = str(conversion_root)
    for key in (
        "LD_PRELOAD",
        "PYTHONHOME",
        "PYTHONPATH",
        "URE_BOOTSTRAP",
        "UNO_PATH",
    ):
        environment.pop(key, None)
    for key in tuple(environment):
        if key.startswith("DYLD_"):
            environment.pop(key, None)

    process_options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": conversion_root,
        "env": environment,
        "close_fds": True,
    }
    if os.name == "posix":
        process_options["start_new_session"] = True
    elif os.name == "nt":
        process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        process = subprocess.Popen(arguments, **process_options)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise LegacyDocConversionError(
            "未找到可执行的旧式 .doc 转换器；请安装 LibreOffice，或配置 "
            "AI_KP_LEGACY_DOC_CONVERTER_COMMAND"
        ) from exc

    stdout = _BoundedOutput(MAX_CONVERTER_DIAGNOSTIC_BYTES)
    stderr = _BoundedOutput(MAX_CONVERTER_DIAGNOSTIC_BYTES)
    stdout_thread = threading.Thread(
        target=stdout.drain,
        args=(process.stdout,),
        name="legacy-doc-converter-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=stderr.drain,
        args=(process.stderr,),
        name="legacy-doc-converter-stderr",
        daemon=True,
    )
    started_threads: list[threading.Thread] = []
    try:
        pid_path.write_text(str(process.pid), encoding="ascii")
        stdout_thread.start()
        started_threads.append(stdout_thread)
        stderr_thread.start()
        started_threads.append(stderr_thread)
    except BaseException:
        _terminate_converter(process)
        for thread in started_threads:
            thread.join(timeout=_PROCESS_GRACE_SECONDS)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        pid_path.unlink(missing_ok=True)
        raise
    timed_out = False
    try:
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_converter(process)
            return_code = process.returncode
    finally:
        if process.poll() is None:
            _terminate_converter(process)
        else:
            _terminate_leftover_group(process.pid)
        for thread in started_threads:
            thread.join(timeout=_PROCESS_GRACE_SECONDS)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        for thread in started_threads:
            if thread.is_alive():
                thread.join(timeout=_PROCESS_GRACE_SECONDS)
        pid_path.unlink(missing_ok=True)

    if timed_out:
        raise LegacyDocConversionError(
            f"旧式 .doc 转换超过 {timeout_seconds:g} 秒限制"
        )
    if stdout.exceeded or stderr.exceeded:
        raise LegacyDocConversionError("旧式 .doc 转换器输出超过 64 KiB 安全限制")
    if return_code != 0:
        diagnostic = _safe_diagnostic(stderr.text() or stdout.text())
        suffix = f"：{diagnostic}" if diagnostic else ""
        raise LegacyDocConversionError(
            f"旧式 .doc 转换失败（退出代码 {return_code}）{suffix}"
        )
    return _read_converted_docx(output_path)


def terminate_recorded_converter_group(work_root: Path, *, force: bool) -> None:
    """Best-effort cleanup if the outer parser kills a converting child."""

    pid_path = work_root / CONVERTER_PID_FILENAME
    try:
        raw_pid = pid_path.read_text(encoding="ascii")[:32]
        if not raw_pid.isascii() or not raw_pid.isdigit():
            return
        process_group = int(raw_pid)
    except (OSError, ValueError):
        return
    if process_group <= 1:
        return
    if os.name == "posix":
        try:
            if process_group == os.getpgrp():
                return
            os.killpg(process_group, signal.SIGKILL if force else signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return


def _resolve_converter(command: str) -> str:
    normalized = command.strip()
    if (
        not normalized
        or len(normalized) > 500
        or "\x00" in normalized
        or "\r" in normalized
        or "\n" in normalized
    ):
        raise LegacyDocConversionError("旧式 .doc 转换器命令配置无效")
    if os.sep in normalized or (os.altsep and os.altsep in normalized):
        path = Path(normalized).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise LegacyDocConversionError(
                "未找到可执行的旧式 .doc 转换器；请安装 LibreOffice，或配置 "
                "AI_KP_LEGACY_DOC_CONVERTER_COMMAND"
            )
        return str(path)
    resolved = shutil.which(normalized)
    if resolved is None:
        raise LegacyDocConversionError(
            "未找到可执行的旧式 .doc 转换器；请安装 LibreOffice，或配置 "
            "AI_KP_LEGACY_DOC_CONVERTER_COMMAND"
        )
    return resolved


def _terminate_converter(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        _terminate_leftover_group(process.pid)
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()
    else:
        process.terminate()
    try:
        process.wait(timeout=_PROCESS_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
        else:
            process.kill()
        try:
            process.wait(timeout=_PROCESS_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise LegacyDocConversionError(
                "无法终止超时的旧式 .doc 转换器进程组"
            ) from exc
    _terminate_leftover_group(process.pid)


def _terminate_leftover_group(process_group: int) -> None:
    if os.name != "posix":
        return
    try:
        if process_group != os.getpgrp():
            os.killpg(process_group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return


def _read_converted_docx(output_path: Path) -> bytes:
    try:
        metadata = output_path.lstat()
    except OSError as exc:
        raise LegacyDocConversionError(
            "旧式 .doc 转换器未生成 DOCX；请确认 LibreOffice Writer 已安装"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise LegacyDocConversionError("旧式 .doc 转换器生成了不安全的输出文件")
    if metadata.st_size <= 0 or metadata.st_size > MAX_MODULE_BYTES:
        raise LegacyDocConversionError("转换后的 DOCX 为空或超过 64 MiB 限制")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output_path, flags)
        try:
            actual = os.fstat(descriptor)
            if not stat.S_ISREG(actual.st_mode) or actual.st_size != metadata.st_size:
                raise LegacyDocConversionError("转换后的 DOCX 在读取期间发生变化")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                data = source.read(MAX_MODULE_BYTES + 1)
        finally:
            os.close(descriptor)
    except LegacyDocConversionError:
        raise
    except OSError as exc:
        raise LegacyDocConversionError("无法读取转换后的 DOCX") from exc
    if len(data) != metadata.st_size or len(data) > MAX_MODULE_BYTES:
        raise LegacyDocConversionError("转换后的 DOCX 在读取期间发生变化")
    if not data.startswith(b"PK\x03\x04"):
        raise LegacyDocConversionError("转换产物不是有效的 DOCX ZIP 文件")
    return data


def _safe_diagnostic(value: str) -> str:
    return " ".join(value.split())[:500]
