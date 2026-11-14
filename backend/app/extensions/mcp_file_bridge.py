"""
工作区沙盒与远程 MCP 之间的通用安全跨网文件协同桥接模块
职责：
1. mcp_upload_workspace_file：将工作区沙盒内文件流式 multipart/form-data 上传至已配置的远程 MCP upload_endpoint
2. mcp_download_artifact_to_workspace：从同源/白名单地址安全流式下载产物到工作区允许子目录（禁止覆盖只读附件目录）
"""
import json
import mimetypes
import os
from pathlib import Path
from typing import Any
import uuid
import httpx

from ..core.logging_config import logger
from ..service.attachment_service import attachment_service
from .mcp_manager import (
    build_httpx_client_kwargs,
    extract_origin,
    is_same_origin,
    mcp_manager,
)

DEFAULT_MAX_TRANSFER_BYTES = 50 * 1024 * 1024  # 50 MB

# 当产物后缀属于文档、表格或二进制媒体时，拒绝服务端误返 HTML 错误页伪装落盘
_NON_HTML_ARTIFACT_SUFFIXES = frozenset({
    ".xlsx", ".xls", ".xlsm", ".csv", ".pdf", ".docx", ".pptx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".zip", ".tar", ".gz", ".json", ".mp3", ".mp4",
})


def _is_within_root(candidate: Path, root: Path) -> bool:
    """校验解析后的路径是否严格落于目标根目录边界之内。"""
    try:
        return candidate.resolve().is_relative_to(root.resolve())
    except (ValueError, OSError):
        return False


def _resolve_upload_source(
    raw_path: str,
    *,
    guard_root: Path,
    current_cwd: Path,
) -> tuple[Path | None, str | None]:
    """在工作区物理边界内解析待上传文件；越权路径直接返回 Denied 错误文本。"""
    clean_raw = (raw_path or "").strip()
    if not clean_raw:
        return None, "Error: 缺少必要参数 `local_file_path`。"

    resolved_guard = guard_root.resolve()
    resolved_cwd = current_cwd.resolve()
    raw_p = Path(clean_raw)

    if raw_p.is_absolute():
        candidate = raw_p.resolve()
        if not _is_within_root(candidate, resolved_guard):
            return None, (
                f"Denied: 安全拦截！禁止跨工作区边界读取并上传外部文件 (`{clean_raw}`)。"
                f"请仅指定当前工作区 (`{resolved_guard}`) 内的文件。"
            )
    else:
        # 优先按 current_cwd 解析（且不能逃逸出 guard_root），若不在 guard_root 内直接拒绝
        cwd_candidate = (resolved_cwd / raw_p).resolve()
        guard_candidate = (resolved_guard / raw_p).resolve()
        if not _is_within_root(cwd_candidate, resolved_guard) and not _is_within_root(guard_candidate, resolved_guard):
            return None, f"Denied: 安全拦截！检测到路径越权穿越 (`{clean_raw}`)，拒绝访问工作区外路径。"

        candidate = cwd_candidate if _is_within_root(cwd_candidate, resolved_guard) and cwd_candidate.exists() else guard_candidate
        if not candidate.exists():
            # 兼容用户/模型书写 `./attachments/<file>` 或仅文件名的情况，映射到物理工作区只读附件目录
            pure_name = raw_p.name
            for fallback in (
                (resolved_guard / "attachments" / pure_name).resolve(),
                (resolved_guard / ".agenthub" / "attachments" / pure_name).resolve(),
            ):
                if _is_within_root(fallback, resolved_guard) and fallback.exists():
                    candidate = fallback
                    break

    if not _is_within_root(candidate, resolved_guard):
        return None, f"Denied: 安全拦截！路径 `{clean_raw}` 解析后超出工作区边界。"
    if not candidate.exists():
        return None, f"Error: 工作区文件不存在: `{clean_raw}`。"
    if not candidate.is_file():
        return None, f"Error: `{clean_raw}` 不是常规文件（禁止直接上传目录或特殊节点）。"

    return candidate, None


def _resolve_download_target(
    save_rel_path: str,
    *,
    guard_root: Path,
    current_cwd: Path,
) -> tuple[Path | None, str, str | None]:
    """解析并严格限制产物下载落盘路径：禁止绝对路径、.. 越界及覆写只读附件目录。"""
    clean_raw = (save_rel_path or "").strip()
    if not clean_raw:
        return None, "", "Error: 缺少必要参数 `save_rel_path`。"

    normalized = clean_raw.replace("\\", "/")
    if normalized.startswith("/") or Path(clean_raw).is_absolute():
        return None, "", f"Denied: 安全拦截！`save_rel_path` 必须为工作区内的相对路径（禁止传入绝对路径 `{clean_raw}`）。"

    # 显式拦截路径段中的 .. 以防歧义，并用 resolve + is_relative_to 做双保险
    if ".." in Path(normalized).parts:
        return None, "", f"Denied: 安全拦截！`save_rel_path` 严禁包含 `..` 上级目录跳转 (`{clean_raw}`)。"

    resolved_guard = guard_root.resolve()
    target_path = (resolved_guard / Path(normalized)).resolve()
    if not _is_within_root(target_path, resolved_guard):
        return None, "", f"Denied: 安全拦截！目标落盘路径超出工作区范围 (`{clean_raw}`)。"

    if (
        attachment_service.is_path_in_attachments_dir(target_path, workspace_root=resolved_guard)
        or attachment_service.is_path_in_attachments_dir(target_path, workspace_root=current_cwd.resolve())
    ):
        return None, "", (
            f"Denied: 安全拦截！工作区输入附件目录 (`attachments/` 与 `.agenthub/attachments/`) 为只读区，"
            f"严禁通过下载覆盖 (`{clean_raw}`)，请保存至 `./output/` 等输出目录。"
        )

    if target_path.exists() and target_path.is_dir():
        return None, "", f"Error: 目标落盘路径 `{clean_raw}` 是一个已存在的目录，无法覆写。"

    rel_posix = target_path.relative_to(resolved_guard).as_posix()
    return target_path, rel_posix, None


def _compact_upload_result(raw_payload: Any, file_name: str, file_size: int) -> str:
    """将服务端上传返回压缩为 < 500 字符的紧凑高信噪比 JSON。"""
    if isinstance(raw_payload, dict):
        summary: dict[str, Any] = {}
        for key in ("fileId", "file_id", "id", "fileName", "filename", "name", "url", "path", "message", "status"):
            if key in raw_payload and raw_payload[key] is not None:
                val = raw_payload[key]
                summary[key] = str(val)[:180] if isinstance(val, str) else val
        if not summary:
            summary = {"result": str(raw_payload)[:260]}
        summary.setdefault("fileName", file_name)
        summary.setdefault("sizeBytes", file_size)
        serialized = json.dumps(summary, ensure_ascii=False)
        return serialized[:480]
    return json.dumps({"fileName": file_name, "sizeBytes": file_size, "result": str(raw_payload)[:280]}, ensure_ascii=False)[:480]


async def upload_workspace_file_to_mcp(
    *,
    server_name: str,
    local_file_path: str,
    guard_root: Path,
    current_cwd: Path,
    max_bytes: int = DEFAULT_MAX_TRANSFER_BYTES,
) -> tuple[str, dict[str, Any]]:
    """安全流式上传工作区文件至已启用远程 MCP 服务配置的 upload_endpoint。"""
    srv = (server_name or "").strip()
    cfg, cfg_err = mcp_manager.get_enabled_remote_file_config(srv, strict_env=False)
    if cfg_err or cfg is None:
        return cfg_err or "Error: 无效的 MCP 服务配置。", {"status": "error"}

    upload_ep = str(cfg.get("upload_endpoint") or "").strip()
    if not upload_ep.startswith(("http://", "https://")):
        return (
            f"Error: 远程 MCP 服务 '{srv}' 未显式配置 `upload_endpoint`。"
            "普通公共 MCP 无需上传文件；若该服务支持文件上传接口，请先在 MCP 配置中填写 `upload_endpoint`。",
            {"status": "error"},
        )

    src_path, path_err = _resolve_upload_source(local_file_path, guard_root=guard_root, current_cwd=current_cwd)
    if path_err or src_path is None:
        err_txt = path_err or "Error: 文件路径校验失败。"
        return err_txt, {"status": "denied" if err_txt.startswith("Denied:") else "error"}

    missing_vars = list(cfg.get("missing_env_vars") or [])
    if missing_vars:
        return (
            f"Error: 远程 MCP 服务 '{srv}' 引用了未定义的环境变量: {', '.join(missing_vars)}，无法执行鉴权请求。",
            {"status": "error"},
        )

    file_size = int(src_path.stat().st_size)
    if file_size > max_bytes:
        return (
            f"Error: 待上传文件 `{src_path.name}` 大小 ({file_size} 字节) 超过单次上传上限 ({max_bytes} 字节)。",
            {"status": "error"},
        )

    field_name = str(cfg.get("upload_file_field") or "file").strip() or "file"
    timeout_sec = float(cfg.get("timeout_seconds") or 60.0)
    headers = dict(cfg.get("expanded_headers") or {})
    # 由 httpx 自动生成带 boundary 的 multipart Content-Type
    headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}

    mime_type, _ = mimetypes.guess_type(src_path.name)
    effective_mime = mime_type or "application/octet-stream"

    client_kwargs = build_httpx_client_kwargs(
        upload_ep,
        httpx.Timeout(timeout_sec, connect=min(10.0, timeout_sec)),
        follow_redirects=False,
    )

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            with src_path.open("rb") as fh:
                resp = await client.post(
                    upload_ep,
                    headers=headers,
                    files={field_name: (src_path.name, fh, effective_mime)},
                )
        if resp.status_code in (301, 302, 303, 307, 308):
            return (
                f"Denied: 上传请求触发跨源/外部重定向 (HTTP {resp.status_code})，已阻断以防鉴权凭据泄露。",
                {"status": "denied"},
            )
        if resp.status_code < 200 or resp.status_code >= 300:
            snippet = (resp.text or "")[:240].strip()
            return (
                f"Error: 文件上传至 MCP '{srv}' 失败 (HTTP {resp.status_code}): {snippet or resp.reason_phrase}",
                {"status": "error"},
            )

        try:
            body_obj: Any = resp.json()
        except Exception:
            body_obj = resp.text

        compact_out = _compact_upload_result(body_obj, src_path.name, file_size)
        return compact_out, {
            "status": "completed",
            "file_name": src_path.name,
            "file_size": file_size,
            "server_name": srv,
        }
    except httpx.TimeoutException:
        return f"Error: 上传 `{src_path.name}` 至 MCP '{srv}' 超时 (>{timeout_sec:.0f}s)。", {"status": "error"}
    except Exception as e:
        logger.warning(f"MCP 文件上传异常 ({srv}/{src_path.name}): {e}")
        return f"Error: 上传文件至 MCP '{srv}' 异常: {e}", {"status": "error"}


async def download_mcp_artifact_to_workspace(
    *,
    server_name: str,
    artifact_url: str,
    save_rel_path: str,
    guard_root: Path,
    current_cwd: Path,
    max_bytes: int = DEFAULT_MAX_TRANSFER_BYTES,
) -> tuple[str, dict[str, Any]]:
    """从同源或允许白名单安全流式下载远程 MCP 产物至工作区产出目录。"""
    srv = (server_name or "").strip()
    dl_url = (artifact_url or "").strip()
    if not dl_url.startswith(("http://", "https://")):
        return f"Error: 非法 `artifact_url`: '{dl_url}'（必须为 http:// 或 https://）。", {"status": "error"}

    cfg, cfg_err = mcp_manager.get_enabled_remote_file_config(srv, strict_env=False)
    if cfg_err or cfg is None:
        return cfg_err or "Error: 无效的 MCP 服务配置。", {"status": "error"}

    same_origin = is_same_origin(dl_url, str(cfg.get("url") or ""))
    dl_origin = extract_origin(dl_url)
    allowed_origins = list(cfg.get("download_origins") or [])
    if not same_origin and dl_origin not in allowed_origins:
        return (
            f"Denied: 安全拦截！`artifact_url` ({dl_origin or dl_url}) 与服务 '{srv}' 配置源不同源，"
            "且不在允许的 `download_origins` 白名单中，已禁止任意外网下载。",
            {"status": "denied"},
        )

    target_path, rel_posix, path_err = _resolve_download_target(
        save_rel_path,
        guard_root=guard_root,
        current_cwd=current_cwd,
    )
    if path_err or target_path is None:
        err_txt = path_err or "Error: 目标落盘路径非法。"
        return err_txt, {"status": "denied" if err_txt.startswith("Denied:") else "error"}

    if same_origin and cfg.get("missing_env_vars"):
        missing_vars = list(cfg.get("missing_env_vars") or [])
        return (
            f"Error: 远程 MCP 服务 '{srv}' 引用了未定义的环境变量: {', '.join(missing_vars)}，无法执行同源下载。",
            {"status": "error"},
        )

    timeout_sec = float(cfg.get("timeout_seconds") or 60.0)
    # NOTE: 仅在同源下载时附带 MCP 服务鉴权 Headers；跨域对象存储预签名白名单不透传敏感头
    dl_headers = dict(cfg.get("expanded_headers") or {}) if same_origin else {}
    dl_headers = {k: v for k, v in dl_headers.items() if k.lower() != "content-type"}

    client_kwargs = build_httpx_client_kwargs(
        dl_url,
        httpx.Timeout(timeout_sec, connect=min(10.0, timeout_sec)),
        follow_redirects=False,
    )

    tmp_path: Path | None = None
    written_bytes = 0
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream("GET", dl_url, headers=dl_headers) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    return (
                        f"Denied: 下载触发重定向 (HTTP {resp.status_code})，已禁止跳转防止 SSRF/跨源泄密。",
                        {"status": "denied"},
                    )
                if resp.status_code < 200 or resp.status_code >= 300:
                    err_raw = await resp.aread()
                    snippet = err_raw[:400].decode("utf-8", errors="replace").strip()
                    return (
                        f"Error: 下载远程产物失败 (HTTP {resp.status_code}): {snippet or resp.reason_phrase}",
                        {"status": "error"},
                    )

                content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                suffix_lower = target_path.suffix.lower()
                if suffix_lower in _NON_HTML_ARTIFACT_SUFFIXES and content_type in ("text/html", "application/xhtml+xml"):
                    return (
                        f"Error: 远程响应 Content-Type 为 `{content_type}` 而非预期的 `{suffix_lower}` 文件内容，"
                        "拒绝将网页/错误页写入目标文件。",
                        {"status": "error"},
                    )

                target_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex[:8]}.tmp")
                with tmp_path.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        if not chunk:
                            continue
                        written_bytes += len(chunk)
                        if written_bytes > max_bytes:
                            raise ValueError(f"下载体积超过单文件安全上限 ({max_bytes} 字节)")
                        fh.write(chunk)

        os.replace(tmp_path, target_path)
        tmp_path = None

        rel_hint = f"./{rel_posix}" if not rel_posix.startswith("./") else rel_posix
        summary_out = json.dumps(
            {
                "status": "saved",
                "file_path": rel_hint,
                "file_name": target_path.name,
                "size_bytes": written_bytes,
            },
            ensure_ascii=False,
        )
        return summary_out[:480], {
            "status": "completed",
            "abs_path": str(target_path),
            "rel_path": rel_posix,
            "file_name": target_path.name,
            "file_size": written_bytes,
            "server_name": srv,
        }
    except ValueError as limit_err:
        return f"Error: {limit_err}，已中止写入并清理临时文件。", {"status": "error"}
    except httpx.TimeoutException:
        return f"Error: 下载远端产物超时 (>{timeout_sec:.0f}s)。", {"status": "error"}
    except Exception as e:
        logger.warning(f"MCP 下载产物落盘异常 ({srv} -> {save_rel_path}): {e}")
        return f"Error: 下载并保存远端产物异常: {e}", {"status": "error"}
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
