"""
内置支持 HTTP Range 断点续传、智能网络退避重试、线路自愈切换与确认断开的下载引擎 (ResumableDownloader)
负责：
1. 使用 .part 临时文件与 .meta.json 元数据持久化已下载进度，支持 206 Partial Content 断点续传；
2. 精准区分“致命不可恢复错误 (401/403/404/磁盘满)”与“瞬时网络抖动 (EOF/Timeout/ConnectionReset/429/5xx)”；
3. 基于“有效字节进展重置预算”：只要单次重试有新字节写入，就重置连续失败计数；
4. 连续零进展达阈值时自动在 [系统代理/默认 -> 强制直连 -> 备用镜像源] 之间切换线路；
5. 所有线路均确认无进展或命中致命错误时安全断开连接，并保留 .part 断点文件供恢复后接续。
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

# 不可恢复的致命 HTTP 状态码（立即快速失败断开，不做无效重试）
FATAL_HTTP_STATUSES: frozenset[int] = frozenset({400, 401, 403, 404, 410, 451})
# 可恢复的瞬时 HTTP 状态码（支持退避重试）
RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.I)


@dataclass(frozen=True)
class DownloadRoute:
    """单个下载候选路由策略。"""

    url: str
    trust_env: bool
    label: str


@dataclass
class ResumableDownloadConfig:
    """断点续传下载器运行参数配置。"""

    connect_timeout: float = 15.0
    read_timeout: float = 30.0
    max_zero_progress_retries: int = 5
    switch_route_after_zero_progress: int = 2
    backoff_schedule: tuple[float, ...] = (2.0, 4.0, 8.0, 15.0, 25.0)
    progress_retry_delay: float = 1.0
    chunk_size: int = 256 * 1024
    progress_log_interval: float = 3.0


@dataclass
class ResumableDownloadResult:
    """断点续传下载执行结果。"""

    success: bool
    cancelled: bool = False
    save_path: str = ""
    part_path: str = ""
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    sha256: str | None = None
    attempts: int = 0
    routes_tried: list[str] = field(default_factory=list)
    failure_kind: str | None = None  # fatal_http_error | disk_error | network_unreachable | checksum_mismatch | cancelled
    error: str | None = None


def build_download_routes(url: str, fallback_urls: list[str] | None = None) -> list[DownloadRoute]:
    """根据主 URL 与备用镜像列表构造自动自愈线路序列（默认路由 -> 强制直连 -> 备用镜像）。"""
    routes: list[DownloadRoute] = []
    seen_urls: list[str] = []
    for raw in [url, *(fallback_urls or [])]:
        cleaned = (raw or "").strip()
        if cleaned and cleaned not in seen_urls:
            seen_urls.append(cleaned)

    for idx, target_url in enumerate(seen_urls):
        host = urlparse(target_url).netloc or "source"
        prefix = "主源" if idx == 0 else f"备用源#{idx}"
        routes.append(DownloadRoute(url=target_url, trust_env=True, label=f"{prefix}({host})·默认/代理"))
        routes.append(DownloadRoute(url=target_url, trust_env=False, label=f"{prefix}({host})·直连(绕过代理)"))

    return routes


def _format_mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def _parse_content_range_total(header_val: str | None) -> int | None:
    if not header_val:
        return None
    m = _CONTENT_RANGE_RE.match(header_val.strip())
    if not m:
        return None
    total_str = m.group(3)
    if total_str == "*":
        return None
    try:
        return int(total_str)
    except ValueError:
        return None


def _compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            buf = fp.read(1024 * 1024)
            if not buf:
                break
            digest.update(buf)
    return digest.hexdigest()


def _load_meta(meta_path: Path) -> dict[str, Any]:
    if not meta_path.is_file():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_meta(meta_path: Path, payload: dict[str, Any]) -> None:
    try:
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


async def _cancellable_sleep(seconds: float, cancel_event: asyncio.Event | None) -> bool:
    """可被取消信号立即打断的异步等待；若被取消返回 True。"""
    if seconds <= 0:
        return bool(cancel_event and cancel_event.is_set())
    if cancel_event is None:
        await asyncio.sleep(seconds)
        return False
    if cancel_event.is_set():
        return True
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def download_with_resume(
    *,
    url: str,
    save_path: str | Path,
    fallback_urls: list[str] | None = None,
    expected_sha256: str | None = None,
    config: ResumableDownloadConfig | None = None,
    cancel_event: asyncio.Event | None = None,
    on_log: Callable[[str], None] | None = None,
    client_factory: Callable[[DownloadRoute, httpx.Timeout], httpx.AsyncClient] | None = None,
) -> ResumableDownloadResult:
    """
    执行带断点续传、智能退避重试、多线路自愈及确认断开保护的异步下载。

    Args:
        url: 主下载链接。
        save_path: 最终保存的目标文件路径。
        fallback_urls: 可选的备用镜像下载链接列表。
        expected_sha256: 可选的目标文件 SHA256 校验值（小写十六进制）。
        config: 下载器参数配置（超时、重试上限、退避曲线等）。
        cancel_event: 可选的异步取消事件。
        on_log: 实时进度与重试日志回调。
        client_factory: 可选的自定义 httpx.AsyncClient 工厂（主要用于单测注入 MockTransport）。
    """
    cfg = config or ResumableDownloadConfig()
    target_path = Path(save_path).expanduser().resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = target_path.with_name(f"{target_path.name}.part")
    meta_path = target_path.with_name(f"{target_path.name}.meta.json")

    def _emit(msg: str) -> None:
        if on_log:
            try:
                on_log(msg)
            except Exception:
                pass

    # 若目标文件已存在且提供了匹配 SHA256，直接秒过
    if target_path.is_file() and expected_sha256:
        existing_hash = await asyncio.to_thread(_compute_file_sha256, target_path)
        if existing_hash.lower() == expected_sha256.strip().lower():
            size_now = target_path.stat().st_size
            _emit(f"[download] 目标文件已存在且 SHA256 校验一致 ({_format_mb(size_now)})，跳过重复下载。")
            return ResumableDownloadResult(
                success=True,
                save_path=str(target_path),
                part_path=str(part_path),
                downloaded_bytes=size_now,
                total_bytes=size_now,
                sha256=existing_hash,
                attempts=0,
            )

    routes = build_download_routes(url, fallback_urls)
    if not routes:
        return ResumableDownloadResult(
            success=False,
            save_path=str(target_path),
            part_path=str(part_path),
            failure_kind="fatal_http_error",
            error="Error: 未提供有效的下载 URL。",
        )

    meta = _load_meta(meta_path)
    total_bytes: int | None = meta.get("total_bytes") if isinstance(meta.get("total_bytes"), int) else None
    etag: str | None = meta.get("etag") if isinstance(meta.get("etag"), str) else None

    downloaded_bytes = part_path.stat().st_size if part_path.is_file() else 0
    if downloaded_bytes > 0:
        total_hint = f" / {_format_mb(total_bytes)}" if total_bytes else ""
        _emit(f"[resume] 检测到本地断点文件 {part_path.name}，将从 {_format_mb(downloaded_bytes)}{total_hint} 处续传。")

    route_idx = 0
    route_zero_failures = 0
    total_zero_failures = 0
    attempts = 0
    routes_tried: list[str] = []
    last_error = ""
    timeout = httpx.Timeout(
        connect=cfg.connect_timeout,
        read=cfg.read_timeout,
        write=30.0,
        pool=cfg.connect_timeout,
    )

    def _make_client(route: DownloadRoute) -> httpx.AsyncClient:
        if client_factory is not None:
            return client_factory(route, timeout)
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            trust_env=route.trust_env,
            headers={"User-Agent": "AgentHub-ResumableDownloader/1.0"},
        )

    while True:
        if cancel_event is not None and cancel_event.is_set():
            _emit(f"[cancelled] 下载已取消，保留断点文件 {_format_mb(downloaded_bytes)} ({part_path.name})。")
            return ResumableDownloadResult(
                success=False,
                cancelled=True,
                save_path=str(target_path),
                part_path=str(part_path),
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
                attempts=attempts,
                routes_tried=routes_tried,
                failure_kind="cancelled",
                error="下载任务已被用户取消。",
            )

        route = routes[route_idx]
        if route.label not in routes_tried:
            routes_tried.append(route.label)

        attempts += 1
        downloaded_bytes = part_path.stat().st_size if part_path.is_file() else 0
        req_headers: dict[str, str] = {}
        if downloaded_bytes > 0:
            req_headers["Range"] = f"bytes={downloaded_bytes}-"
            if etag and not etag.startswith("W/"):
                req_headers["If-Range"] = etag

        _emit(
            f"[connect] 第 {attempts} 次请求 | 线路: {route.label} | "
            f"起始断点: {_format_mb(downloaded_bytes)}"
        )

        bytes_this_attempt = 0
        fatal_error: tuple[str, str] | None = None

        try:
            async with _make_client(route) as client:
                async with client.stream("GET", route.url, headers=req_headers) as resp:
                    status = resp.status_code

                    if status in FATAL_HTTP_STATUSES:
                        fatal_error = (
                            "fatal_http_error",
                            f"服务端返回不可恢复 HTTP {status} ({route.label})，已停止重试。",
                        )
                    elif status in RETRYABLE_HTTP_STATUSES:
                        last_error = f"HTTP {status} 临时异常 ({route.label})"
                    elif status == 416:
                        # Range 超出范围：可能已经完整下载，也可能是远端文件长度变小
                        cr_total = _parse_content_range_total(resp.headers.get("Content-Range"))
                        if cr_total is not None and downloaded_bytes == cr_total and cr_total > 0:
                            total_bytes = cr_total
                            _emit(f"[download] 远端返回 416 且本地大小与总长一致 ({_format_mb(cr_total)})，进入校验落盘。")
                            break
                        # 否则本地 .part 与远端不一致，重置重新下载
                        _emit("[resume] 远端返回 416，本地断点与远端文件不一致，重置从 0 下载。")
                        try:
                            part_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                        downloaded_bytes = 0
                        total_bytes = None
                        etag = None
                        last_error = "HTTP 416 断点偏移重置"
                    elif status not in (200, 206):
                        last_error = f"非预期 HTTP 状态码 {status} ({route.label})"
                    else:
                        # 200 OK 或 206 Partial Content
                        resp_etag = resp.headers.get("ETag")
                        if resp_etag:
                            etag = resp_etag

                        if status == 206:
                            mode = "ab"
                            cr_total = _parse_content_range_total(resp.headers.get("Content-Range"))
                            if cr_total is not None:
                                total_bytes = cr_total
                            elif resp.headers.get("Content-Length"):
                                try:
                                    total_bytes = downloaded_bytes + int(resp.headers["Content-Length"])
                                except ValueError:
                                    pass
                        else:
                            # 200 OK：服务端不支持 Range 或 If-Range 失效，需要从 0 重写
                            if downloaded_bytes > 0:
                                _emit("[resume] 服务端返回 200 OK（不支持断点续传或资源已更新），重置从头写入。")
                            mode = "wb"
                            downloaded_bytes = 0
                            if resp.headers.get("Content-Length"):
                                try:
                                    total_bytes = int(resp.headers["Content-Length"])
                                except ValueError:
                                    total_bytes = None

                        _save_meta(
                            meta_path,
                            {
                                "url": route.url,
                                "etag": etag,
                                "total_bytes": total_bytes,
                                "downloaded_bytes": downloaded_bytes,
                                "updated_at": time.time(),
                            },
                        )

                        last_log_ts = time.monotonic()
                        window_bytes = 0
                        window_start = last_log_ts

                        with open(part_path, mode) as fp:
                            # 真实流式响应使用 aiter_raw() 避免 ByteChunker 囤积未满 chunk_size 的数据导致断流时丢字节；
                            # 若响应体已被预先加载（如 MockTransport content=bytes），则回退 aiter_bytes()
                            chunk_iter = (
                                resp.aiter_bytes()
                                if getattr(resp, "is_stream_consumed", False)
                                else resp.aiter_raw()
                            )
                            async for chunk in chunk_iter:
                                if cancel_event is not None and cancel_event.is_set():
                                    raise asyncio.CancelledError()
                                if not chunk:
                                    continue
                                fp.write(chunk)
                                chunk_len = len(chunk)
                                downloaded_bytes += chunk_len
                                bytes_this_attempt += chunk_len
                                window_bytes += chunk_len

                                now = time.monotonic()
                                elapsed_win = now - window_start
                                if now - last_log_ts >= cfg.progress_log_interval:
                                    fp.flush()
                                    speed_mbs = (window_bytes / (1024 * 1024)) / max(elapsed_win, 0.1)
                                    pct_str = (
                                        f" ({downloaded_bytes * 100 / total_bytes:.1f}%)"
                                        if total_bytes and total_bytes > 0
                                        else ""
                                    )
                                    total_str = f" / {_format_mb(total_bytes)}" if total_bytes else ""
                                    _emit(
                                        f"[progress] {_format_mb(downloaded_bytes)}{total_str}{pct_str} | "
                                        f"速度: {speed_mbs:.2f} MB/s | 线路: {route.label}"
                                    )
                                    _save_meta(
                                        meta_path,
                                        {
                                            "url": route.url,
                                            "etag": etag,
                                            "total_bytes": total_bytes,
                                            "downloaded_bytes": downloaded_bytes,
                                            "updated_at": time.time(),
                                        },
                                    )
                                    last_log_ts = now
                                    window_start = now
                                    window_bytes = 0

                            fp.flush()

                        # 流正常结束：校验是否达到声明的总大小（防服务端提前静默 EOF）
                        if total_bytes is not None and downloaded_bytes < total_bytes:
                            last_error = (
                                f"响应流提前结束 ({_format_mb(downloaded_bytes)} / {_format_mb(total_bytes)})"
                            )
                            _emit(f"[warn] {last_error}，将从新断点继续拉取。")
                        else:
                            # 完整下载完成
                            break

        except asyncio.CancelledError:
            _save_meta(
                meta_path,
                {
                    "url": route.url,
                    "etag": etag,
                    "total_bytes": total_bytes,
                    "downloaded_bytes": downloaded_bytes,
                    "updated_at": time.time(),
                },
            )
            _emit(f"[cancelled] 下载已中断，保留断点文件 {_format_mb(downloaded_bytes)} ({part_path.name})。")
            return ResumableDownloadResult(
                success=False,
                cancelled=True,
                save_path=str(target_path),
                part_path=str(part_path),
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
                attempts=attempts,
                routes_tried=routes_tried,
                failure_kind="cancelled",
                error="下载任务已被用户取消。",
            )
        except OSError as os_err:
            if isinstance(os_err, PermissionError) or getattr(os_err, "errno", None) == errno.ENOSPC:
                fatal_error = (
                    "disk_error",
                    f"本地磁盘写入失败（权限不足或空间已满）: {os_err}",
                )
            else:
                last_error = f"网络或 I/O 异常: {os_err}"
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.RemoteProtocolError,
            httpx.StreamError,
            httpx.HTTPError,
        ) as net_err:
            last_error = f"{type(net_err).__name__}: {net_err}"

        # 若命中不可恢复错误，立即确认断开并保留断点
        if fatal_error is not None:
            kind, reason = fatal_error
            _save_meta(
                meta_path,
                {
                    "url": route.url,
                    "etag": etag,
                    "total_bytes": total_bytes,
                    "downloaded_bytes": downloaded_bytes,
                    "updated_at": time.time(),
                },
            )
            _emit(f"[disconnect] {reason}")
            return ResumableDownloadResult(
                success=False,
                save_path=str(target_path),
                part_path=str(part_path),
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
                attempts=attempts,
                routes_tried=routes_tried,
                failure_kind=kind,
                error=reason,
            )

        # 进展感知预算判定：有有效字节增长则重置零进展计数，否则计入零进展失败
        if bytes_this_attempt > 0:
            total_zero_failures = 0
            route_zero_failures = 0
            _save_meta(
                meta_path,
                {
                    "url": route.url,
                    "etag": etag,
                    "total_bytes": total_bytes,
                    "downloaded_bytes": downloaded_bytes,
                    "updated_at": time.time(),
                },
            )
            _emit(
                f"[retry] 本轮已成功续传 +{_format_mb(bytes_this_attempt)} "
                f"(累计 {_format_mb(downloaded_bytes)})，但流尚未完成 ({last_error})；"
                f"重置故障计数，{cfg.progress_retry_delay:.1f}s 后继续断点续传..."
            )
            if await _cancellable_sleep(cfg.progress_retry_delay, cancel_event):
                continue
        else:
            total_zero_failures += 1
            route_zero_failures += 1

            # 达到总零进展上限，确认确实无法下载并安全断开
            if total_zero_failures >= cfg.max_zero_progress_retries:
                _save_meta(
                    meta_path,
                    {
                        "url": route.url,
                        "etag": etag,
                        "total_bytes": total_bytes,
                        "downloaded_bytes": downloaded_bytes,
                        "updated_at": time.time(),
                    },
                )
                disconnect_msg = (
                    f"已连续 {total_zero_failures} 次重试零字节推进（尝试线路: {', '.join(routes_tried)}），"
                    f"确认当前网络或下载源不可达，已安全断开连接并保留断点文件 "
                    f"{part_path.name} ({_format_mb(downloaded_bytes)})。最后异常: {last_error}"
                )
                _emit(f"[disconnect] {disconnect_msg}")
                return ResumableDownloadResult(
                    success=False,
                    save_path=str(target_path),
                    part_path=str(part_path),
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    attempts=attempts,
                    routes_tried=routes_tried,
                    failure_kind="network_unreachable",
                    error=disconnect_msg,
                )

            # 连续在同一线路零进展达到切换阈值，尝试切换直连/代理/备用源
            if route_zero_failures >= cfg.switch_route_after_zero_progress and len(routes) > 1:
                next_idx = (route_idx + 1) % len(routes)
                if next_idx != route_idx:
                    route_idx = next_idx
                    route_zero_failures = 0
                    _emit(f"[route] 当前线路连续零进展，自动切换线路至: {routes[route_idx].label}")

            backoff_idx = min(total_zero_failures - 1, len(cfg.backoff_schedule) - 1)
            delay = cfg.backoff_schedule[backoff_idx] if cfg.backoff_schedule else 2.0
            _emit(
                f"[retry] 零进展异常 ({total_zero_failures}/{cfg.max_zero_progress_retries}): {last_error} | "
                f"保留断点 {_format_mb(downloaded_bytes)}，{delay:.1f}s 后退避重试..."
            )
            if await _cancellable_sleep(delay, cancel_event):
                continue

    # 流结束：校验 SHA256 与原子落盘
    actual_sha256 = await asyncio.to_thread(_compute_file_sha256, part_path)
    if expected_sha256 and actual_sha256.lower() != expected_sha256.strip().lower():
        err = (
            f"SHA256 校验失败: 期望 {expected_sha256.strip().lower()}, "
            f"实际 {actual_sha256.lower()}（已保留文件在 {part_path}）"
        )
        _emit(f"[disconnect] {err}")
        return ResumableDownloadResult(
            success=False,
            save_path=str(target_path),
            part_path=str(part_path),
            downloaded_bytes=downloaded_bytes,
            total_bytes=total_bytes,
            sha256=actual_sha256,
            attempts=attempts,
            routes_tried=routes_tried,
            failure_kind="checksum_mismatch",
            error=err,
        )

    os.replace(part_path, target_path)
    try:
        meta_path.unlink(missing_ok=True)
    except OSError:
        pass

    final_size = target_path.stat().st_size
    _emit(f"[completed] 下载并落盘成功: {target_path} ({_format_mb(final_size)}, sha256={actual_sha256[:12]}...)")
    return ResumableDownloadResult(
        success=True,
        save_path=str(target_path),
        part_path=str(part_path),
        downloaded_bytes=final_size,
        total_bytes=final_size,
        sha256=actual_sha256,
        attempts=attempts,
        routes_tried=routes_tried,
    )

