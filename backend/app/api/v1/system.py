"""
系统级硬件协同与治理接口 (System Telemetry & Governance Router)
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import webbrowser
from fastapi import APIRouter, Body, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from ...probe.collector import hardware_probe
from ...service.hardware_skills import hardware_skills, proposal_manager
from ...core.config import settings

router = APIRouter(prefix="/system", tags=["System & Hardware"])


@router.get("/snapshot")
async def get_system_snapshot() -> dict[str, Any]:
    """获取当前宿主机硬件与 Top 进程瞬时快照"""
    return hardware_probe.collect_snapshot()


@router.get("/stream")
async def stream_hardware_telemetry(interval: float = Query(2.0, ge=1.0, le=10.0)) -> StreamingResponse:
    """
    Server-Sent Events (SSE) 硬件指标流式推送
    前端展开折叠 HUD 时建立连接，收起时自动断连以节约宿主机资源
    """
    async def event_generator():
        while True:
            snapshot = hardware_probe.collect_snapshot()
            yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            await asyncio.sleep(interval)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/proposals")
async def list_proposals(status: str | None = None) -> list[dict[str, Any]]:
    """获取优化提案列表（支持按 pending / approved / rejected 筛选）"""
    return proposal_manager.list_proposals(status=status)


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str) -> dict[str, Any]:
    """【人类确认锁】用户物理点击确认执行优化方案"""
    return await proposal_manager.execute_proposal(proposal_id)


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: str) -> dict[str, Any]:
    """用户驳回优化方案"""
    return proposal_manager.reject_proposal(proposal_id)


@router.post("/port/inspect")
async def inspect_port(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """排查端口占用，若被占用自动生成释放待审批单"""
    port = payload.get("port")
    if not port or not isinstance(port, int) or port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="端口号不合法，必须为 1~65535 整数")
    return hardware_skills.inspect_port(port)


@router.post("/governance/propose")
async def create_governance_proposal(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """手动或由 AI 触发创建进程治理提案"""
    scenario = payload.get("scenario", "system")
    targets = payload.get("targets", [])
    reason = payload.get("reason", "用户提请治理高资源进程")
    return hardware_skills.propose_process_governance(scenario, targets, reason)


@router.get("/cache/scan")
async def scan_caches() -> list[dict[str, Any]]:
    """扫描常用开发工具与系统缓存目录"""
    return hardware_skills.scan_cache_directories()


@router.get("/logs")
async def get_recent_logs(lines: int = Query(100, ge=10, le=1000)) -> dict[str, Any]:
    """读取本地最新写入的系统运行与错误日志切片"""
    logs_dir = Path(settings.db_path).parent / "logs"
    log_file = logs_dir / "agenthub.log"
    if not log_file.exists():
        legacy_log = logs_dir / "geminihub.log"
        if legacy_log.exists():
            log_file = legacy_log
        elif Path("agenthub.log").exists():
            log_file = Path("agenthub.log")
        elif Path("geminihub.log").exists():
            log_file = Path("geminihub.log")

    if not log_file.exists():
        return {"file": str(log_file), "lines": []}

    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
            return {"file": str(log_file.resolve()), "lines": all_lines[-lines:]}
    except Exception as e:
        return {"error": str(e), "lines": []}


@router.post("/logs/open-folder")
async def open_logs_folder() -> dict[str, str]:
    """调用系统文件资源管理器一键打开日志所在目录"""
    log_dir = Path(settings.db_path).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", str(log_dir.resolve())])
    return {"status": "ok", "path": str(log_dir.resolve())}


class OpenExternalUrlRequest(BaseModel):
    """请求通过系统默认浏览器安全打开外部网址"""

    url: str = Field(..., min_length=8, max_length=2048, description="仅支持 http:// 或 https:// 协议链接")


@router.post("/open-url")
async def open_external_url(req: OpenExternalUrlRequest) -> dict[str, Any]:
    """校验安全协议并在宿主机默认浏览器打开外部 URL（供桌面端 WebView2 外链跳转使用）"""
    clean_url = (req.url or "").strip()
    if any(ch in clean_url for ch in ("\r", "\n", "\t", " ")):
        raise HTTPException(status_code=400, detail="URL 格式包含非法空白字符")

    parsed = urlparse(clean_url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="安全校验拦截：仅允许打开 http:// 或 https:// 网址")

    opened = await asyncio.to_thread(webbrowser.open, clean_url, 2)
    return {"status": "ok", "opened": bool(opened), "url": clean_url}
