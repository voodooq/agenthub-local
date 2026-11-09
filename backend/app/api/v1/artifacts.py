"""
工作区资产库 (Artifacts) 接口路由
支持资产同步、安全沙盒路径解析、系统默认应用直接打开、安全分级降级与画布内容预览
"""
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, unquote, urlparse
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from ...core.config import settings
from ...core.logging_config import logger
from ...repository.artifact_repository import ArtifactRepository
from ...repository.db import db_manager
from ...repository.workspace_repository import WorkspaceRepository
from ...service.attachment_service import attachment_service

router = APIRouter(prefix="/artifacts", tags=["Workspace Artifacts"])

# 高危可执行程序与脚本扩展名清单：点击直接打开时自动安全降级为“打开所在文件夹并选中”
HIGH_RISK_EXECUTABLE_EXTS: set[str] = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr",
    ".ps1", ".vbs", ".vbe", ".jse", ".wsf", ".wsh",
    ".reg", ".dll", ".sys", ".cpl", ".lnk", ".hta",
}

PREVIEW_IMAGE_EXTS: set[str] = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".ico", ".svg"}


class ArtifactRegisterRequest(BaseModel):
    workspace_id: str
    file_path: str
    file_name: str
    file_type: str = Field("document", description="document, code, media, report")
    file_size: int = 0
    conversation_id: str | None = None
    source: str = "ai_generated"
    summary: str | None = None


class ArtifactOpenByPathRequest(BaseModel):
    workspace_id: str = Field(..., description="当前工作区 ID")
    file_path: str = Field(..., description="工作区相对路径、绝对路径或 file:// URI")
    mode: Literal["launch", "reveal"] = Field("launch", description="launch=系统默认应用直接打开; reveal=在文件夹中选中定位")


class ArtifactContentByPathRequest(BaseModel):
    workspace_id: str = Field(..., description="当前工作区 ID")
    file_path: str = Field(..., description="工作区相对路径、绝对路径或 file:// URI")


class ArtifactOpenRequest(BaseModel):
    mode: Literal["launch", "reveal"] = Field("reveal", description="launch=系统默认应用直接打开; reveal=在文件夹中选中定位")


def _launch_with_default_app(target: Path) -> None:
    """使用宿主操作系统默认关联应用直接打开目标文件或目录。"""
    target_str = str(target)
    if any(ch in target_str for ch in ("\x00", "\r", "\n")):
        raise ValueError("目标路径包含非法控制字符")

    if os.name == "nt":
        os.startfile(target_str)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target_str])
    else:
        subprocess.Popen(["xdg-open", target_str])


def _reveal_in_file_manager(target: Path) -> None:
    """在宿主操作系统文件管理器中打开并高亮定位目标文件（或直接打开目录）。"""
    target_str = str(target)
    if any(ch in target_str for ch in ("\x00", "\r", "\n", '"')):
        raise ValueError("目标路径包含非法控制字符或引号")

    if os.name == "nt":
        if target.is_dir():
            subprocess.Popen(f'explorer "{target_str}"', shell=False)
        else:
            # NOTE: Windows CreateProcess 对 shell=False 传入完整字符串时不会经过 cmd.exe，
            # 既保留了 explorer /select,"..." 的标准引号语法以兼容空格/中文路径，又杜绝了 Shell 注入。
            subprocess.Popen(f'explorer /select,"{target_str}"', shell=False)
    elif sys.platform == "darwin":
        if target.is_dir():
            subprocess.Popen(["open", target_str])
        else:
            subprocess.Popen(["open", "-R", target_str])
    else:
        parent_dir = str(target if target.is_dir() else target.parent)
        subprocess.Popen(["xdg-open", parent_dir])


def _open_local_target(target: Path, mode: Literal["launch", "reveal"] = "launch") -> dict[str, Any]:
    """根据安全等级执行打开或定位操作，并自动对高危可执行文件降级为目录定位。"""
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"目标文件不存在: {target}")

    ext = target.suffix.lower()
    is_dir = target.is_dir()
    effective_mode: Literal["launch", "reveal"] = mode
    downgraded = False
    downgraded_reason: str | None = None

    if not is_dir and mode == "launch" and ext in HIGH_RISK_EXECUTABLE_EXTS:
        effective_mode = "reveal"
        downgraded = True
        downgraded_reason = (
            f"出于安全防护限制，高危可执行/脚本文件 ({ext}) 禁止直接运行，"
            "已自动转为打开所在文件夹并选中该文件。"
        )

    try:
        if effective_mode == "launch":
            _launch_with_default_app(target)
            message = f"已在系统默认应用中打开 {target.name}"
        else:
            _reveal_in_file_manager(target)
            message = (
                downgraded_reason
                if downgraded and downgraded_reason
                else f"已在系统文件管理器中定位 {target.name}"
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"打开本地目标失败 ({effective_mode}): {target} -> {exc}")
        raise HTTPException(status_code=500, detail=f"唤起本地程序失败: {exc}")

    return {
        "success": True,
        "launched": effective_mode == "launch",
        "mode": effective_mode,
        "requested_mode": mode,
        "downgraded": downgraded,
        "downgraded_reason": downgraded_reason,
        "path": str(target),
        "file_name": target.name,
        "message": message,
    }


def _normalize_raw_file_reference(raw_path: str) -> str:
    """清洗文件路径字符串（解包 file:/// URI、去除引号及末尾行号锚点）。"""
    cleaned = (raw_path or "").strip()
    while len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("`", '"', "'"):
        cleaned = cleaned[1:-1].strip()

    if not cleaned:
        return ""

    if any(ch in cleaned for ch in ("\x00", "\r", "\n")):
        raise HTTPException(status_code=400, detail="文件路径包含非法控制字符")

    if cleaned.lower().startswith("file://"):
        parsed = urlparse(cleaned)
        uri_path = unquote(parsed.path or "")
        # Windows file:///E:/... 会解析出前导 '/'，需去掉以还原盘符路径
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", uri_path):
            uri_path = uri_path[1:]
        elif parsed.netloc and parsed.netloc.lower() != "localhost":
            uri_path = f"//{parsed.netloc}{uri_path}"
        cleaned = uri_path
    else:
        cleaned = unquote(cleaned)

    # 剥离 Markdown 链接常附带的 #L123-L145 行号锚点
    cleaned = re.sub(r"#L?\d+(?:-L?\d+)?$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def _strip_trailing_line_number(path_str: str) -> str:
    """尝试剥离形如 path/to/file.py:42 或 :42:10 的末尾行列号定位后缀。"""
    # 保护 Windows 盘符 C:\，仅剥除末尾冒号后的纯数字片段
    return re.sub(r"(?<=[^\\/:]):\d+(?::\d+)?$", "", path_str).strip()


async def _get_workspace_root_path(workspace_id: str) -> tuple[str, Path]:
    """查询目标工作区并解析其真实物理根目录。"""
    eff_ws_id = (workspace_id or "default").strip() or "default"
    ws = await WorkspaceRepository.get_by_id(eff_ws_id)
    if not ws and eff_ws_id != "default":
        raise HTTPException(status_code=404, detail=f"未找到指定的工作区: {eff_ws_id}")

    if ws and ws.get("root_path"):
        return str(ws.get("id") or eff_ws_id), Path(ws["root_path"]).resolve()

    fallback_root = (Path(settings.db_path).parent / "workspaces" / eff_ws_id).resolve()
    return eff_ws_id, fallback_root


def _is_within_workspace(candidate: Path, ws_root: Path) -> bool:
    """判断解析后的物理路径是否严格落于当前工作区根目录之内。"""
    try:
        return candidate.resolve().is_relative_to(ws_root.resolve())
    except (ValueError, OSError):
        return False


async def _resolve_workspace_target_file(
    workspace_id: str,
    raw_path: str,
    *,
    allow_directory: bool = True,
) -> tuple[Path, Path, str, str]:
    """
    严格在指定工作区物理边界内安全解析目标文件/目录。

    Returns:
        (resolved_target, ws_root, effective_workspace_id, rel_path_posix)
    """
    eff_ws_id, ws_root = await _get_workspace_root_path(workspace_id)
    normalized = _normalize_raw_file_reference(raw_path)
    if not normalized:
        raise HTTPException(status_code=400, detail="文件路径不能为空")

    variants = [normalized]
    no_line = _strip_trailing_line_number(normalized)
    if no_line and no_line != normalized:
        variants.append(no_line)

    matched_path: Path | None = None
    saw_out_of_bounds = False

    for item_str in variants:
        if os.name == "nt":
            path_obj = Path(item_str.replace("/", "\\"))
        else:
            path_obj = Path(item_str.replace("\\", "/"))

        # 1. 绝对路径：直接 resolve 并强制检查工作区边界
        if path_obj.is_absolute():
            resolved = path_obj.resolve()
            if not _is_within_workspace(resolved, ws_root):
                saw_out_of_bounds = True
                continue
            if resolved.exists() and (resolved.is_file() or (allow_directory and resolved.is_dir())):
                matched_path = resolved
                break
            continue

        # 2. 相对路径：优先剥除冗余前导 "./" 并与 ws_root 拼接做越界硬校验
        rel_clean = item_str.replace("\\", "/").lstrip("/")
        while rel_clean.startswith("./"):
            rel_clean = rel_clean[2:]
        if not rel_clean:
            continue

        direct_candidate = (ws_root / rel_clean).resolve()
        if not _is_within_workspace(direct_candidate, ws_root):
            # 一旦相对路径出现 ../../ 等跳出工作区的意图，立即记为越界并拒绝回退搜索
            raise HTTPException(status_code=403, detail=f"拒绝越界访问工作区之外的路径: {raw_path}")

        if direct_candidate.exists() and (direct_candidate.is_file() or (allow_directory and direct_candidate.is_dir())):
            matched_path = direct_candidate
            break

        # 3. 常见相对写法容错（仍在 ws_root 严格边界内）：
        #    - AI 误把工作区目录名作为第一段前缀，如 my-ws/output/a.xlsx
        #    - AI 仅提及文件名或输出相对片段，按安全常见产出目录回退探测
        sub_candidates: list[Path] = []
        rel_parts = [p for p in rel_clean.split("/") if p and p != "."]
        if len(rel_parts) >= 2 and rel_parts[0].lower() == ws_root.name.lower():
            sub_candidates.append((ws_root / "/".join(rel_parts[1:])).resolve())

        base_name = Path(rel_clean).name
        if base_name and base_name not in (".", ".."):
            for folder in ("output", "reports", ".agenthub/attachments", "attachments"):
                sub_candidates.append((ws_root / folder / base_name).resolve())

        for cand in sub_candidates:
            if not _is_within_workspace(cand, ws_root):
                continue
            if cand.exists() and (cand.is_file() or (allow_directory and cand.is_dir())):
                matched_path = cand
                break
        if matched_path:
            break

        # 4. 数据库已登记资产后缀精准匹配（仅限位于 ws_root 内且磁盘真实存在的文件）
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT file_path FROM workspace_artifacts WHERE workspace_id = ?;",
                (eff_ws_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                rel_suffix = "/" + rel_clean.lower()
                for row in rows:
                    fp = Path(str(row["file_path"] or "")).resolve()
                    if not _is_within_workspace(fp, ws_root):
                        continue
                    fp_posix = fp.as_posix().lower()
                    if (fp_posix.endswith(rel_suffix) or fp.name.lower() == base_name.lower()) and fp.exists() and fp.is_file():
                        matched_path = fp
                        break
        if matched_path:
            break

    if saw_out_of_bounds and matched_path is None:
        raise HTTPException(status_code=403, detail=f"拒绝访问工作区目录之外的文件: {raw_path}")

    if matched_path is None:
        raise HTTPException(status_code=404, detail=f"当前工作区中未找到目标文件: {raw_path}")

    if not allow_directory and matched_path.is_dir():
        raise HTTPException(status_code=400, detail=f"目标路径是文件夹而非可预览文件: {raw_path}")

    rel_posix = matched_path.relative_to(ws_root).as_posix()
    return matched_path, ws_root, eff_ws_id, rel_posix


async def _ensure_artifact_registered(
    *,
    workspace_id: str,
    ws_root: Path,
    target: Path,
) -> None:
    """当通过路径命中工作区内刚生成的文件时，若资产库尚未记录则轻量补齐登记。"""
    if not target.is_file():
        return
    try:
        rel = target.relative_to(ws_root)
        if ArtifactRepository._is_ignored_rel_path(rel):
            return
        norm_path = str(target.resolve())
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT id FROM workspace_artifacts WHERE workspace_id = ? AND file_path = ?;",
                (workspace_id, norm_path),
            ) as cursor:
                if await cursor.fetchone():
                    return
        f_type = ArtifactRepository._detect_file_type(target)
        f_size = target.stat().st_size
        await ArtifactRepository.register_artifact(
            workspace_id=workspace_id,
            file_path=norm_path,
            file_name=target.name,
            file_type=f_type,
            file_size=f_size,
            source="ai_generated",
            summary=f"生成文件 ({rel.as_posix()})",
        )
    except Exception as exc:
        logger.debug(f"按需补齐资产记录跳过: {exc}")


def _build_artifact_preview_payload(
    *,
    target: Path,
    artifact_id: str | None = None,
    workspace_id: str | None = None,
    rel_path: str | None = None,
    file_type: str | None = None,
) -> dict[str, Any]:
    """统一构建资产或按路径文件的画布预览载荷。"""
    file_name = target.name
    eff_type = file_type or ArtifactRepository._detect_file_type(target)
    ext, category, canonical_mime = attachment_service.classify_extension(file_name)
    raw_url: str | None = None
    if workspace_id:
        path_param = rel_path or str(target)
        raw_url = (
            f"/api/v1/artifacts/raw-by-path?workspace_id={quote(workspace_id, safe='')}"
            f"&file_path={quote(path_param, safe='')}"
        )

    if ext in PREVIEW_IMAGE_EXTS or category == "image":
        canvas_type = "image"
        content = (
            f"[图片资产] {file_name} ({attachment_service.format_size_str(target.stat().st_size)})\n"
            f"物理路径: {target}"
        )
    elif ext in (".html", ".htm"):
        canvas_type = "html"
        content = attachment_service.decode_text_bytes(target.read_bytes())
    elif ext in (".mmd", ".mermaid"):
        canvas_type = "mermaid"
        content = attachment_service.decode_text_bytes(target.read_bytes())
    elif category == "office" or ext in (".xlsx", ".docx", ".pptx", ".pdf"):
        canvas_type = "code"
        ctx_info = attachment_service.extract_attachment_context(target, canonical_mime, file_name)
        content = ctx_info.get("preview_text") or "(未提取到可读文本内容)"
    elif category == "text":
        canvas_type = "code"
        content = attachment_service.decode_text_bytes(target.read_bytes())
    else:
        canvas_type = "code"
        content = (
            f"[二进制或特殊格式文件] {file_name}\n"
            f"大小: {attachment_service.format_size_str(target.stat().st_size)}\n"
            f"物理路径: {target}\n\n"
            "该格式暂不支持在画布直接渲染正文，请点击「在默认程序中打开」使用系统关联软件查看。"
        )

    return {
        "artifact_id": artifact_id,
        "file_name": file_name,
        "file_type": eff_type,
        "file_path": str(target),
        "rel_path": rel_path,
        "canvas_type": canvas_type,
        "mime_type": canonical_mime or "application/octet-stream",
        "raw_url": raw_url,
        "content": content,
        "size_bytes": target.stat().st_size,
    }


@router.get("")
async def list_artifacts(
    workspace_id: str = Query(..., description="工作区 ID"),
    file_type: str | None = Query(None, description="资产分类: document / code / media / report"),
):
    """获取指定工作区的资产列表（自动物理增量巡检）"""
    items = await ArtifactRepository.list_artifacts(workspace_id, file_type, auto_sync=True)
    return {"artifacts": items, "total": len(items)}


@router.post("/sync")
async def sync_artifacts(workspace_id: str = Query(..., description="工作区 ID")):
    """立即对指定工作区的物理目录进行全量深度巡检并同步至资产库"""
    synced_count = await ArtifactRepository.sync_physical_workspace_artifacts(workspace_id)
    items = await ArtifactRepository.list_artifacts(workspace_id, auto_sync=False)
    return {
        "success": True,
        "synced": synced_count,
        "total": len(items),
        "artifacts": items,
    }


@router.post("/open-by-path")
async def open_artifact_by_path(req: ArtifactOpenByPathRequest):
    """
    在当前工作区沙盒内按相对/绝对路径解析并打开生成文件（或定位所在目录）。
    对高危可执行/脚本扩展名强制降级为打开所在文件夹并选中文件。
    """
    target, ws_root, eff_ws_id, rel_posix = await _resolve_workspace_target_file(
        req.workspace_id,
        req.file_path,
        allow_directory=True,
    )
    if target.is_file():
        await _ensure_artifact_registered(workspace_id=eff_ws_id, ws_root=ws_root, target=target)

    result = _open_local_target(target, mode=req.mode)
    result["workspace_id"] = eff_ws_id
    result["rel_path"] = rel_posix
    return result


@router.post("/content-by-path")
async def get_artifact_content_by_path(req: ArtifactContentByPathRequest):
    """按工作区文件路径读取资产预览内容（用于聊天芯片一键在右侧画布预览）"""
    target, ws_root, eff_ws_id, rel_posix = await _resolve_workspace_target_file(
        req.workspace_id,
        req.file_path,
        allow_directory=False,
    )
    await _ensure_artifact_registered(workspace_id=eff_ws_id, ws_root=ws_root, target=target)

    try:
        return _build_artifact_preview_payload(
            target=target,
            workspace_id=eff_ws_id,
            rel_path=rel_posix,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"按路径读取文件内容异常: {target} -> {exc}")
        raise HTTPException(status_code=500, detail=f"读取文件失败: {exc}")


@router.get("/raw-by-path")
async def get_raw_artifact_by_path(
    workspace_id: str = Query(..., description="工作区 ID"),
    file_path: str = Query(..., description="工作区相对路径或绝对路径"),
):
    """在严格工作区沙盒边界内安全返回原始二进制文件流（供画布大图预览等）。"""
    target, _, _, _ = await _resolve_workspace_target_file(
        workspace_id,
        file_path,
        allow_directory=False,
    )
    _, _, canonical_mime = attachment_service.classify_extension(target.name)
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type=canonical_mime or "application/octet-stream",
    )


@router.post("")
async def register_artifact(req: ArtifactRegisterRequest):
    """登记一件新资产"""
    item = await ArtifactRepository.register_artifact(
        workspace_id=req.workspace_id,
        file_path=req.file_path,
        file_name=req.file_name,
        file_type=req.file_type,
        file_size=req.file_size,
        conversation_id=req.conversation_id,
        source=req.source,
        summary=req.summary,
    )
    return item


@router.post("/{artifact_id}/open")
async def open_artifact_in_explorer(
    artifact_id: str,
    mode: Literal["reveal", "launch"] | None = Query(
        None,
        description="打开模式: reveal=在文件夹中定位（默认向后兼容）, launch=用系统默认应用直接打开",
    ),
    body: ArtifactOpenRequest | None = Body(None),
):
    """
    在本地资源管理器中定位该物理文件，或直接用系统默认关联程序打开（兼容旧版默认 reveal 行为）。
    """
    async with db_manager.get_connection() as conn:
        async with conn.execute(
            "SELECT file_path FROM workspace_artifacts WHERE id = ?;", (artifact_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="未找到指定的资产记录")
            file_path = row["file_path"]

    target = Path(file_path).resolve()
    eff_mode: Literal["launch", "reveal"] = mode or (body.mode if body else "reveal")
    return _open_local_target(target, mode=eff_mode)


@router.get("/{artifact_id}/content")
async def get_artifact_content(artifact_id: str):
    """读取指定资产物理文件的预览内容（用于 Canvas 预览，支持图片/HTML/文档结构化摘要）"""
    async with db_manager.get_connection() as conn:
        async with conn.execute(
            "SELECT workspace_id, file_path, file_name, file_type FROM workspace_artifacts WHERE id = ?;",
            (artifact_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="未找到指定的资产记录")
            ws_id = row["workspace_id"]
            file_path = row["file_path"]
            file_type = row["file_type"]

    target = Path(file_path).resolve()
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="目标文件在磁盘上不存在")

    try:
        return _build_artifact_preview_payload(
            target=target,
            artifact_id=artifact_id,
            workspace_id=ws_id,
            rel_path=str(target),
            file_type=file_type,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"读取资产文件内容异常: {exc}")
        raise HTTPException(status_code=500, detail=f"读取文件失败: {exc}")
