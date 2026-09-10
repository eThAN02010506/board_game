"""Bounded adapter for an isolated local MinerU API process."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import time
import zipfile
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import httpx
from pypdf import PdfReader

from ai_kp.platform.modules.documents import (
    MAX_ASSETS,
    MAX_DOCUMENT_CHUNKS,
    MAX_EXTRACTED_CHARACTERS,
    MAX_PDF_PAGE_CONTENT_BYTES,
    MAX_PDF_PAGES,
    MAX_PDF_TOTAL_CONTENT_BYTES,
    MAX_TOTAL_ASSET_BYTES,
    MAX_TOTAL_IMAGE_PIXELS,
    DocumentChunk,
    ExtractedModuleDocument,
    _pdf_page_content_bytes,
)
from ai_kp.platform.modules.mineru_documents import extract_mineru_document
from ai_kp.platform.modules.section_ancestry import (
    inferred_scene_key,
    resolve_heading_level,
)

MINERU_PID_FILENAME = "mineru-parser.pid"
_PROCESS_GRACE_SECONDS = 3.0
_PDF_SEGMENT_PAGES = 48
_RESULT_ARCHIVE_MAX_BYTES = 512 * 1024 * 1024
_RESULT_ARCHIVE_MAX_FILES = 20_000
_PDF_TEXT_SAMPLE_PAGES = 12


class MineruParseError(ValueError):
    """A bounded user-safe MinerU failure."""


def parse_with_mineru(
    source_data: bytes,
    source_filename: str,
    work_root: Path,
    *,
    title: str,
    source_hash: str,
    command: str,
    backend: str,
    model_source: str,
    timeout_seconds: float,
) -> ExtractedModuleDocument:
    suffix = Path(source_filename).suffix.casefold()
    if suffix not in {".pdf", ".docx"}:
        raise MineruParseError("MinerU 导入只接受 PDF 或 DOCX 中间文件")
    executable = _resolve_command(command)
    input_root = work_root / "mineru-input"
    input_root.mkdir(mode=0o700)
    input_path = input_root / f"input{suffix}"
    input_path.write_bytes(source_data)
    input_path.chmod(0o400)
    environment = os.environ.copy()
    environment["MINERU_MODEL_SOURCE"] = model_source
    if suffix == ".pdf":
        page_ranges, method = _pdf_parse_plan(source_data)
    else:
        page_ranges, method = ((None, None),), "auto"
    deadline = time.monotonic() + timeout_seconds
    extracted_segments: list[ExtractedModuleDocument] = []
    api_process, api_url = _start_mineru_api(
        executable,
        work_root=work_root,
        environment=environment,
        deadline=deadline,
    )
    try:
        for segment_index, (start_page, end_page) in enumerate(page_ranges, start=1):
            output_root = work_root / f"mineru-output-{segment_index:04d}"
            output_root.mkdir(mode=0o700)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MineruParseError(
                    f"MinerU 解析超过 {timeout_seconds:g} 秒限制"
                )
            _parse_segment_via_api(
                api_url,
                input_path=input_path,
                output_root=output_root,
                source_filename=source_filename,
                backend=backend,
                method=method,
                start_page=start_page,
                end_page=end_page,
                timeout_seconds=remaining,
            )
            extracted_segments.append(
                extract_mineru_document(
                    output_root,
                    title=title,
                    source_hash=source_hash,
                    source_type="pdf" if suffix == ".pdf" else "docx",
                    page_offset=start_page or 0,
                )
            )
    finally:
        _terminate(api_process)
        (work_root / MINERU_PID_FILENAME).unlink(missing_ok=True)
    return _merge_segments(extracted_segments, title=title)


def _parse_segment_via_api(
    api_url: str,
    *,
    input_path: Path,
    output_root: Path,
    source_filename: str,
    backend: str,
    method: str,
    start_page: int | None,
    end_page: int | None,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    form_data = {
        "lang_list": "ch",
        "backend": backend,
        "effort": "medium",
        "parse_method": method,
        "formula_enable": "false",
        "table_enable": "true",
        "image_analysis": "true",
        "return_md": "false",
        "return_middle_json": "true",
        "return_model_output": "false",
        "return_content_list": "true",
        "return_images": "true",
        "response_format_zip": "true",
        "return_original_file": "false",
        "client_side_output_generation": "false",
        "start_page_id": str(start_page or 0),
        "end_page_id": str(99999 if end_page is None else end_page),
    }
    timeout = httpx.Timeout(connect=10, read=120, write=120, pool=10)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        try:
            with input_path.open("rb") as source:
                response = client.post(
                    f"{api_url}/tasks",
                    data=form_data,
                    files={"files": (source_filename, source, "application/octet-stream")},
                )
        except httpx.HTTPError as exc:
            raise MineruParseError(f"提交 MinerU 任务失败：{exc}") from exc
        if response.status_code != 202:
            raise MineruParseError(
                f"MinerU 拒绝解析任务（HTTP {response.status_code}）"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MineruParseError("MinerU 返回了无效的任务响应") from exc
        task_id = payload.get("task_id")
        status_url = payload.get("status_url")
        result_url = payload.get("result_url")
        expected_prefix = f"{api_url}/tasks/"
        if (
            not isinstance(task_id, str)
            or not isinstance(status_url, str)
            or not isinstance(result_url, str)
            or not status_url.startswith(expected_prefix)
            or not result_url.startswith(expected_prefix)
        ):
            raise MineruParseError("MinerU 返回了无效的任务地址")
        while time.monotonic() < deadline:
            try:
                status_response = client.get(status_url)
                if status_response.status_code != 200:
                    time.sleep(0.5)
                    continue
                status_payload = status_response.json()
            except (httpx.HTTPError, ValueError):
                time.sleep(0.5)
                continue
            status = status_payload.get("status")
            if status == "completed":
                break
            if status == "failed":
                error = str(status_payload.get("error") or "未知错误")[:500]
                raise MineruParseError(f"MinerU 解析失败：{error}")
            time.sleep(0.5)
        else:
            raise MineruParseError(f"MinerU 解析超过 {timeout_seconds:g} 秒限制")

        archive_data: bytes | None = None
        while time.monotonic() < deadline:
            try:
                result_response = client.get(result_url)
            except httpx.HTTPError:
                time.sleep(0.5)
                continue
            if result_response.status_code == 200:
                content_length = _bounded_content_length(result_response)
                if content_length > _RESULT_ARCHIVE_MAX_BYTES:
                    raise MineruParseError("MinerU 结果压缩包超过 512 MiB 限制")
                archive_data = result_response.content
                break
            if result_response.status_code not in {202, 404, 409, 425, 503}:
                raise MineruParseError(
                    f"下载 MinerU 结果失败（HTTP {result_response.status_code}）"
                )
            time.sleep(0.5)
        if archive_data is None:
            raise MineruParseError("MinerU 结果在时限内未就绪")
        if len(archive_data) > _RESULT_ARCHIVE_MAX_BYTES:
            raise MineruParseError("MinerU 结果压缩包超过 512 MiB 限制")
        _extract_result_archive(archive_data, output_root)


def _extract_result_archive(data: bytes, output_root: Path) -> None:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > _RESULT_ARCHIVE_MAX_FILES:
                raise MineruParseError("MinerU 结果文件数量超过安全限制")
            total_bytes = 0
            for info in infos:
                if info.is_dir():
                    continue
                relative = Path(info.filename)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in info.filename
                ):
                    raise MineruParseError("MinerU 结果包含不安全路径")
                total_bytes += info.file_size
                if total_bytes > _RESULT_ARCHIVE_MAX_BYTES:
                    raise MineruParseError("MinerU 结果展开后超过 512 MiB 限制")
                destination = output_root.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(info) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
    except (OSError, zipfile.BadZipFile) as exc:
        raise MineruParseError("MinerU 返回了无效的结果压缩包") from exc


def _bounded_content_length(response: httpx.Response) -> int:
    raw = response.headers.get("content-length")
    if raw is None:
        return 0
    try:
        value = int(raw)
    except ValueError as exc:
        raise MineruParseError("MinerU 返回了无效的结果长度") from exc
    if value < 0:
        raise MineruParseError("MinerU 返回了无效的结果长度")
    return value


def _start_mineru_api(
    executable: str,
    *,
    work_root: Path,
    environment: dict[str, str],
    deadline: float,
) -> tuple[subprocess.Popen[bytes], str]:
    api_executable = Path(executable).with_name("mineru-api")
    if not api_executable.is_file() or not os.access(api_executable, os.X_OK):
        raise MineruParseError("未找到与 MinerU CLI 配套的 mineru-api")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = int(reservation.getsockname()[1])
    options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": work_root,
        "env": environment,
        "close_fds": True,
    }
    if os.name == "posix":
        options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            [str(api_executable), "--host", "127.0.0.1", "--port", str(port)],
            **options,
        )
    except (OSError, ValueError) as exc:
        raise MineruParseError(f"无法启动 mineru-api：{exc}") from exc
    pid_path = work_root / MINERU_PID_FILENAME
    _write_recorded_pids(pid_path, (process.pid,))
    startup_deadline = min(deadline, time.monotonic() + 300)
    try:
        while time.monotonic() < startup_deadline:
            if process.poll() is not None:
                raise MineruParseError("mineru-api 在启动期间退出")
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                connection.request("GET", "/health")
                response = connection.getresponse()
                response.read()
                connection.close()
                if response.status == 200:
                    return process, f"http://127.0.0.1:{port}"
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.25)
    except BaseException:
        _terminate(process)
        pid_path.unlink(missing_ok=True)
        raise
    _terminate(process)
    pid_path.unlink(missing_ok=True)
    raise MineruParseError("mineru-api 启动超时")


def _write_recorded_pids(path: Path, pids: tuple[int, ...]) -> None:
    path.write_text(json.dumps(pids, separators=(",", ":")), encoding="ascii")


def _pdf_page_ranges(source_data: bytes) -> tuple[tuple[int, int], ...]:
    return _pdf_parse_plan(source_data)[0]


def _pdf_parse_plan(
    source_data: bytes,
) -> tuple[tuple[tuple[int, int], ...], str]:
    try:
        reader = PdfReader(BytesIO(source_data), strict=False)
        if reader.is_encrypted:
            raise MineruParseError("不接受加密的 KP 本 PDF")
        page_count = len(reader.pages)
    except MineruParseError:
        raise
    except Exception as exc:
        raise MineruParseError("无法读取 KP 本 PDF 页数") from exc
    if page_count <= 0:
        raise MineruParseError("KP 本 PDF 没有页面")
    if page_count > MAX_PDF_PAGES:
        raise MineruParseError(f"KP 本超过 {MAX_PDF_PAGES} 页限制")
    extracted_characters = 0
    text_pages = 0
    text_sample_indices = frozenset(_pdf_text_sample_indices(page_count))
    content_bytes = 0
    for page_number, page in enumerate(reader.pages, start=1):
        page_content_bytes = _pdf_page_content_bytes(page, page_number)
        if page_content_bytes > MAX_PDF_PAGE_CONTENT_BYTES:
            raise MineruParseError(
                f"第 {page_number} 页解压后的 PDF 内容流超过 16 MiB 限制"
            )
        content_bytes += page_content_bytes
        if content_bytes > MAX_PDF_TOTAL_CONTENT_BYTES:
            raise MineruParseError("KP 本解压后的 PDF 内容流总计超过 128 MiB 限制")
        if page_number - 1 in text_sample_indices:
            try:
                text_length = len((page.extract_text() or "").strip())
            except Exception:  # noqa: BLE001 - unreadable samples select auto/OCR
                text_length = 0
            extracted_characters += text_length
            text_pages += int(text_length >= 80)
    ranges = tuple(
        (start, min(start + _PDF_SEGMENT_PAGES, page_count) - 1)
        for start in range(0, page_count, _PDF_SEGMENT_PAGES)
    )
    sample_count = len(text_sample_indices)
    text_coverage = text_pages / sample_count
    method = (
        "txt"
        if text_coverage >= 0.6 and extracted_characters / sample_count >= 100
        else "auto"
    )
    return ranges, method


def _pdf_text_sample_indices(page_count: int) -> tuple[int, ...]:
    sample_count = min(page_count, _PDF_TEXT_SAMPLE_PAGES)
    if sample_count == page_count:
        return tuple(range(page_count))
    last_index = page_count - 1
    return tuple(
        round(sample_index * last_index / (sample_count - 1))
        for sample_index in range(sample_count)
    )


def _merge_segments(
    segments: list[ExtractedModuleDocument],
    *,
    title: str,
) -> ExtractedModuleDocument:
    if not segments:
        raise MineruParseError("MinerU 没有返回解析分段")
    chunks = []
    assets = []
    inherited_heading = title
    extracted_characters = 0
    asset_bytes = 0
    image_pixels = 0
    for segment in segments:
        for chunk in segment.chunks:
            extracted_characters += len(chunk.text)
            if extracted_characters > MAX_EXTRACTED_CHARACTERS:
                raise MineruParseError("MinerU 分段合并文本超过安全字符上限")
            if len(chunks) >= MAX_DOCUMENT_CHUNKS:
                raise MineruParseError("MinerU 分段合并文本块超过安全上限")
            if chunk.title == title and inherited_heading != title:
                chunk = replace(chunk, title=inherited_heading)
            else:
                inherited_heading = chunk.title
            chunks.append(replace(chunk, order_index=len(chunks)))
        for asset in segment.assets:
            if len(assets) >= MAX_ASSETS:
                raise MineruParseError("MinerU 分段合并图片超过安全上限")
            asset_bytes += len(asset.data)
            if asset_bytes > MAX_TOTAL_ASSET_BYTES:
                raise MineruParseError("MinerU 分段合并图片总大小超过安全上限")
            if asset.width and asset.height:
                image_pixels += asset.width * asset.height
                if image_pixels > MAX_TOTAL_IMAGE_PIXELS:
                    raise MineruParseError("MinerU 分段合并图片总像素超过安全上限")
            if asset.nearby_heading == title and inherited_heading != title:
                asset = replace(asset, nearby_heading=inherited_heading)
            assets.append(asset)
    chunks = _normalize_merged_chunk_structure(chunks, title=title)
    first = segments[0]
    return ExtractedModuleDocument(
        source_type=first.source_type,
        source_hash=first.source_hash,
        title=first.title,
        unit_count=max(item.unit_count for item in segments),
        chunks=tuple(chunks),
        assets=tuple(assets),
    )


def _normalize_merged_chunk_structure(
    chunks: list[DocumentChunk],
    *,
    title: str,
) -> list[DocumentChunk]:
    """Rebuild ancestry once after segmentation instead of trusting local state."""

    normalized = []
    current_heading = title
    section_headings: dict[int, str] = {}
    structural_parent_level: int | None = None
    for chunk in chunks:
        is_heading = chunk.content_kind == "heading" or chunk.semantic_kind == "heading"
        if is_heading:
            current_heading = chunk.text[:240] or current_heading
        elif chunk.title and chunk.title != title:
            # Older MinerU payloads can carry a useful enclosing title without
            # having classified the corresponding block as a heading.
            current_heading = chunk.title
        heading_level, structural_parent_level = resolve_heading_level(
            title=chunk.text if is_heading else "",
            is_heading=is_heading,
            explicit_level=chunk.heading_level if is_heading else None,
            structural_parent_level=structural_parent_level,
            prefer_structural_parent_for_generic_explicit=True,
        )
        if heading_level is not None:
            section_headings = {
                level: heading
                for level, heading in section_headings.items()
                if level < heading_level
            }
            section_headings[heading_level] = current_heading
        section_path = tuple(section_headings[level] for level in sorted(section_headings))
        normalized.append(
            replace(
                chunk,
                title=current_heading,
                heading_level=heading_level if is_heading else None,
                section_path=section_path,
                scene_key=inferred_scene_key(section_path),
            )
        )
    return normalized


def terminate_recorded_mineru_group(work_root: Path, *, force: bool) -> None:
    pid_path = work_root / MINERU_PID_FILENAME
    try:
        raw = pid_path.read_text(encoding="ascii")[:256]
        decoded = json.loads(raw)
        process_groups = (
            (int(decoded),) if isinstance(decoded, int) else tuple(map(int, decoded))
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return
    for process_group in process_groups:
        if process_group <= 1 or os.name != "posix":
            continue
        try:
            if process_group != os.getpgrp():
                os.killpg(process_group, signal.SIGKILL if force else signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue


def _resolve_command(command: str) -> str:
    normalized = command.strip()
    if not normalized or len(normalized) > 500 or any(
        marker in normalized for marker in ("\x00", "\r", "\n")
    ):
        raise MineruParseError("MinerU 命令配置无效")
    if os.sep in normalized or (os.altsep and os.altsep in normalized):
        candidate = Path(normalized).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    else:
        resolved = shutil.which(normalized)
        if resolved is not None:
            return resolved
    raise MineruParseError(
        "未找到 MinerU；请安装后配置 AI_KP_MINERU_COMMAND"
    )


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
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
        process.wait(timeout=_PROCESS_GRACE_SECONDS)


__all__ = [
    "MineruParseError",
    "parse_with_mineru",
    "terminate_recorded_mineru_group",
]
