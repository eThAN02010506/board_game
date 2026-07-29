"""Reusable spawn-process boundary for untrusted document decoding."""

from __future__ import annotations

import multiprocessing
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

_CHILD_ERROR_LIMIT = 2_000
_PROCESS_KILL_GRACE_SECONDS = 1.0
_RESULT_ERROR_NAME = "error.txt"

ResultT = TypeVar("ResultT")
TerminationHook = Callable[[Path, bool], None]


class DocumentProcessSandboxError(ValueError):
    """A bounded, user-safe failure raised by the document process boundary."""


@dataclass(frozen=True)
class DocumentProcessPolicy:
    """Wall-clock and POSIX resource budgets for one document attempt."""

    timeout_seconds: float = 90
    memory_limit_mib: int = 1024
    cpu_seconds: int = 60

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("Document process timeout must be positive")
        if self.memory_limit_mib <= 0:
            raise ValueError("Document process memory limit must be positive")
        if self.cpu_seconds <= 0:
            raise ValueError("Document process CPU limit must be positive")


def run_document_process_isolated(
    source_path: Path,
    *,
    operation_label: str,
    policy: DocumentProcessPolicy,
    target: Callable[..., None],
    child_args: tuple[object, ...],
    read_result: Callable[[Path], ResultT],
    termination_hook: TerminationHook | None = None,
) -> ResultT:
    """Run a decoder in a killable spawn child and read results from disk."""

    context = multiprocessing.get_context("spawn")
    result_root = Path(
        tempfile.mkdtemp(
            prefix=".document-parse-",
            dir=source_path.parent,
        )
    )
    process = context.Process(
        target=target,
        args=(
            str(result_root),
            str(source_path),
            *child_args,
            policy,
        ),
        name="ai-kp-document-parser",
        daemon=True,
    )
    started = False
    try:
        process.start()
        started = True
        process.join(timeout=policy.timeout_seconds)
        if process.is_alive():
            _terminate_process(
                process,
                result_root=result_root,
                termination_hook=termination_hook,
            )
            raise DocumentProcessSandboxError(
                f"{operation_label}超过 {policy.timeout_seconds:g} 秒限制"
            )
        error_path = result_root / _RESULT_ERROR_NAME
        if error_path.is_file():
            raise DocumentProcessSandboxError(_read_child_error(error_path))
        if process.exitcode != 0:
            raise DocumentProcessSandboxError(
                _unexpected_exit_message(operation_label, process.exitcode)
            )
        return read_result(result_root)
    finally:
        if started:
            if process.is_alive():
                _terminate_process(
                    process,
                    result_root=result_root,
                    termination_hook=termination_hook,
                )
            if not process.is_alive():
                process.close()
        shutil.rmtree(result_root, ignore_errors=True)


def read_bounded_source(source_path: Path, *, max_bytes: int, label: str) -> bytes:
    """Read an immutable source snapshot with a hard byte bound."""

    try:
        size = source_path.stat().st_size
    except OSError as exc:
        raise ValueError(f"无法读取{label}源文件") from exc
    if size <= 0 or size > max_bytes:
        raise ValueError(f"{label}为空或超过 {max_bytes // (1024 * 1024)} MiB 限制")
    try:
        with source_path.open("rb") as source:
            data = source.read(max_bytes + 1)
    except OSError as exc:
        raise ValueError(f"无法读取{label}源文件") from exc
    if len(data) != size or len(data) > max_bytes:
        raise ValueError(f"{label}源文件在解析期间发生变化或超过安全限制")
    return data


def apply_posix_resource_limits(policy: DocumentProcessPolicy) -> None:
    """Apply best-effort address-space and CPU limits inside the child."""

    if os.name != "posix":
        return
    try:
        import resource
    except ImportError:
        return

    address_space = policy.memory_limit_mib * 1024 * 1024
    if hasattr(resource, "RLIMIT_AS"):
        _set_resource_limit(resource, resource.RLIMIT_AS, address_space, address_space)
    if hasattr(resource, "RLIMIT_CPU"):
        _set_resource_limit(
            resource,
            resource.RLIMIT_CPU,
            policy.cpu_seconds,
            policy.cpu_seconds + 1,
        )


def write_process_error(result_root: Path, message: str) -> None:
    """Persist a bounded error without sending exception objects through IPC."""

    try:
        (result_root / _RESULT_ERROR_NAME).write_text(
            message[:_CHILD_ERROR_LIMIT],
            encoding="utf-8",
        )
    except OSError:
        return


def _set_resource_limit(
    resource_module: Any,
    resource_kind: int,
    requested_soft: int,
    requested_hard: int,
) -> None:
    try:
        current_soft, current_hard = resource_module.getrlimit(resource_kind)
        infinity = resource_module.RLIM_INFINITY
        soft = _lower_limit(current_soft, requested_soft, infinity)
        hard = _lower_limit(current_hard, requested_hard, infinity)
        soft = min(soft, hard)
        resource_module.setrlimit(resource_kind, (soft, hard))
    except (OSError, ValueError):
        return


def _lower_limit(current: int, requested: int, infinity: int) -> int:
    if current == infinity:
        return requested
    return min(current, requested)


def _read_child_error(error_path: Path) -> str:
    try:
        with error_path.open("r", encoding="utf-8") as source:
            message = source.read(_CHILD_ERROR_LIMIT + 1)
    except (OSError, UnicodeError) as exc:
        raise DocumentProcessSandboxError(
            "文档解析子进程返回了无效错误"
        ) from exc
    return message[:_CHILD_ERROR_LIMIT] or "文档解析失败"


def _terminate_process(
    process: multiprocessing.process.BaseProcess,
    *,
    result_root: Path,
    termination_hook: TerminationHook | None,
) -> None:
    if not process.is_alive():
        process.join(timeout=0)
        return
    if termination_hook is not None:
        termination_hook(result_root, False)
    process.terminate()
    process.join(timeout=_PROCESS_KILL_GRACE_SECONDS)
    if termination_hook is not None:
        termination_hook(result_root, True)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=_PROCESS_KILL_GRACE_SECONDS)


def _unexpected_exit_message(operation_label: str, exit_code: int | None) -> str:
    if exit_code is None:
        return f"{operation_label}子进程意外退出"
    if exit_code < 0:
        return f"{operation_label}超过资源限制或被操作系统终止"
    return f"{operation_label}子进程异常退出（代码 {exit_code}）"
