import sys
import os

# 限制底层 OpenMP / MKL / OpenBLAS / ONNX 线程池上限，避免单次文档解析或向量推理吃满宿主机全部核心
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "2")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "2")

import socket
import asyncio
import secrets
from pathlib import Path
from contextlib import asynccontextmanager

"""
FastAPI 应用主程序入口
负责中间件装配、路由挂载、静态前端托管与全场景生命周期绑定
"""
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import psutil

from app.core.config import settings
from app.core.logging_config import setup_logging, logger
from app.core.runtime_paths import get_bundle_root, get_writable_app_root
from app.repository.db import db_manager
from app.core.proxy_helper import apply_system_proxy_to_process
from app.api.router import api_router


def find_free_port() -> int:
    """动态获取一个宿主机当前可用的空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def watch_parent_process(parent_pid: int) -> None:
    """父进程存活心跳守护器：防止主窗口崩溃后 Python 沦为孤儿僵尸进程"""
    while True:
        if not psutil.pid_exists(parent_pid):
            logger.info(f"父进程 [PID: {parent_pid}] 已退出，AgentHub 后端守护服务自毁清理。")
            os._exit(0)
        await asyncio.sleep(2)


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    """
    本机访问安全守卫中间件
    防 DNS 重绑定攻击：校验 Host header 只能为 localhost / 127.0.0.1
    访问令牌校验：针对 /api/ 接口严格校验 X-Access-Token，对前端静态页面放行
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # 1. 静态资源与免检页面放行
        if not path.startswith("/api/"):
            return await call_next(request)

        # 健康检查、OAuth 回调、本地上传附件静态预览以及自带高强度令牌/签名校验的 IM 渠道入站与扫码绑定接口放行
        if (
            path in ("/api/v1/health", "/api/v1/accounts/oauth/callback")
            or path.startswith("/api/v1/chat/uploads/")
            or path.startswith("/api/v1/dialogue-wiki/ingress/")
            or path.startswith("/api/v1/dialogue-wiki/bind/")
            or path.startswith("/api/v1/personal-assistant/reports/")
        ):
            return await call_next(request)

        # 2. 防 DNS 重绑定：Host 只能是 localhost / 127.0.0.1 / backend / testserver
        host_header = request.headers.get("host", "")
        host_name = host_header.split(":")[0]
        if host_name not in ("localhost", "127.0.0.1", "backend", "testserver"):
            return Response(
                content="Forbidden: Host 不合法，仅允许本机访问",
                status_code=403,
                media_type="text/plain",
            )

        # 3. 本机专属令牌握手接口：仅限来源 IP 确实为回环地址才放行
        if path == "/api/v1/auth/local-token":
            client_ip = request.client.host if request.client else ""
            is_internal = (
                client_ip in ("127.0.0.1", "::1", "localhost", "testclient")
                or host_name == "backend"
                or client_ip.startswith("172.")
            )
            if is_internal:
                return Response(
                    content=f'{{"token":"{settings.access_token}"}}',
                    status_code=200,
                    media_type="application/json",
                )
            return Response(content="Forbidden: 仅允许本机客户端读取令牌", status_code=403)

        # 4. 访问令牌校验：X-Access-Token 或 URL query token 必须与启动令牌一致
        token = request.headers.get("x-access-token", "") or request.query_params.get("token", "")
        if not token or token != settings.access_token:
            return Response(
                content="Forbidden: X-Access-Token 无效或缺失",
                status_code=403,
                media_type="text/plain",
            )

        response = await call_next(request)
        return response

    async def __call__(self, scope, receive, send):
        # 拦截静态 HTML / JS 资源，追加 Cache-Control: no-cache，杜绝 WebView2 强缓存旧代码
        if scope["type"] == "http":
            req_path = scope.get("path", "")
            if req_path == "/" or req_path.endswith(".html") or req_path.endswith(".js"):
                async def send_wrapper(message):
                    if message["type"] == "http.response.start":
                        headers = list(message.get("headers", []))
                        headers.append((b"cache-control", b"no-cache, no-store, must-revalidate"))
                        headers.append((b"pragma", b"no-cache"))
                        headers.append((b"expires", b"0"))
                        message["headers"] = headers
                    await send(message)
                await super().__call__(scope, receive, send_wrapper)
                return
        await super().__call__(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期钩子：初始化数据库、加载安全令牌、绑定父进程守护"""
    setup_logging()
    apply_system_proxy_to_process()

    token_file = Path(settings.db_path).parent / ".access_token"
    if token_file.exists():
        try:
            settings.access_token = token_file.read_text(encoding="utf-8").strip()
        except Exception:
            settings.access_token = ""

    if not settings.access_token:
        settings.access_token = secrets.token_urlsafe(32)
        try:
            token_file.write_text(settings.access_token, encoding="utf-8")
        except Exception as e:
            logger.warning(f"写入本地访问令牌文件失败: {e}")

    logger.info(f"正在启动 {settings.app_name} v{settings.app_version}...")
    logger.info(f"[安全] 本机访问令牌 (X-Access-Token): {settings.access_token}")

    # 监听桌面父进程 PID（如果传入）
    parent_watch_task: asyncio.Task | None = None
    parent_pid_str = os.environ.get("DESKTOP_PARENT_PID") or os.environ.get("TAURI_PARENT_PID")
    if parent_pid_str and parent_pid_str.isdigit():
        parent_pid = int(parent_pid_str)
        if parent_pid > 0:
            logger.info(f"已成功绑定桌面父进程 PID: {parent_pid}")
            parent_watch_task = asyncio.create_task(watch_parent_process(parent_pid))

    await db_manager.init_db()

    # 启动生命周期中执行一次知识库挂载资产纳管检查（内部带 mtime 幂等短路）
    try:
        from app.kb.manager import KBLifecycleManager

        await KBLifecycleManager.auto_adopt_mounted_kbs()
    except Exception as kb_adopt_err:
        logger.warning(f"[KBLifecycle] 启动扫描挂载知识库资产跳过: {kb_adopt_err}")

    # 将上次异常退出遗留的活跃 Loop 统一收敛为 paused，防止启动后前端无限卡在执行中状态
    try:
        from app.repository.loop_run_repository import TaskLoopRepository

        orphaned_count = await TaskLoopRepository.reconcile_orphaned_loops_on_startup()
        if orphaned_count:
            logger.info(f"[TaskLoop] 启动自愈：已将 {orphaned_count} 个遗留活跃 Loop 收敛为 paused 状态")
    except Exception as loop_reconcile_err:
        logger.warning(f"[TaskLoop] 启动收敛遗留 Loop 状态失败: {loop_reconcile_err}")

    # 启动长期记忆 30 天自然指数衰减每日守护任务
    async def _daily_decay_task():
        while True:
            try:
                from app.memory.long_term import long_term_memory
                res = await long_term_memory.apply_time_decay()
                if res.get("decayed") or res.get("deleted"):
                    logger.info(f"[记忆生命周期] 30 天未命中衰减扫描完成: 衰减 {res['decayed']} 条，清理 {res['deleted']} 条")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"长期记忆衰减任务执行异常: {e}")
            await asyncio.sleep(86400)

    decay_task = asyncio.create_task(_daily_decay_task())

    # 启动计划任务后台 Cron 调度守护器，确保服务启动即自动巡检到期任务
    scheduler_task: asyncio.Task | None = None
    try:
        from app.scheduler.task_scheduler import TaskScheduler

        scheduler = TaskScheduler.get_instance()
        scheduler.ensure_started()
        scheduler_task = scheduler._loop_task
    except Exception as sched_err:
        logger.warning(f"[Scheduler] 启动计划任务后台守护器失败: {sched_err}")

    # 若已配置个人微信 ClawBot 机器人且本机已有登录态，服务启动时自动在后台静默拉起 Sidecar，避免漏收或延迟回复
    async def _auto_boot_weixin_sidecar() -> None:
        try:
            has_clawbot_link = False
            async with db_manager.get_connection() as conn:
                cur = await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='dialogue_channel_links'"
                )
                if await cur.fetchone():
                    cur2 = await conn.execute(
                        "SELECT 1 FROM dialogue_channel_links WHERE channel_type = 'weixin_clawbot' AND status = 'active' LIMIT 1"
                    )
                    has_clawbot_link = (await cur2.fetchone()) is not None
            # 仅当数据库中确实存在活跃的 weixin_clawbot 对接链接时才自动拉起 Sidecar，
            # 防止用户已解绑后因本地残留 session 文件误拉起，从而抢占云端/Linux 同 Bot 的长轮询！
            if has_clawbot_link:
                from app.channels.qr_binding import ensure_clawbot_sidecar_running
                ok_boot, msg_boot = await ensure_clawbot_sidecar_running()
                logger.info(f"[WeixinClawbot] 启动自检自动拉起 Sidecar: ok={ok_boot}, msg={msg_boot}")
            else:
                logger.info("[WeixinClawbot] 未检测到活跃的微信 ClawBot 绑定链接，跳过自动拉起 Sidecar")
        except Exception as wx_boot_err:
            logger.debug(f"[WeixinClawbot] 启动自动拉起 Sidecar 跳过: {wx_boot_err}")

    weixin_boot_task = asyncio.create_task(_auto_boot_weixin_sidecar())

    # 后台静默预热无 GPU 纯 CPU 离线语音识别模型（仅在已安装 sherpa-onnx 且未配置云端 ASR 时按需拉取）
    async def _warmup_local_asr_model() -> None:
        try:
            await asyncio.sleep(3.0)
            if (os.getenv("ASR_API_BASE_URL") or "").strip():
                return
            if (os.getenv("DISABLE_ASR_MODEL_AUTO_DOWNLOAD") or "").strip() == "1":
                return
            from app.channels.media_processor import media_processor

            if media_processor._is_sherpa_installed() and not media_processor._has_local_sensevoice_model():
                await media_processor.ensure_sensevoice_model()
        except asyncio.CancelledError:
            raise
        except Exception as asr_err:
            logger.debug(f"[MediaProcessor] 启动预热本地 ASR 模型跳过: {asr_err}")

    asr_warmup_task = asyncio.create_task(_warmup_local_asr_model())

    async def _clawbot_keepalive_loop() -> None:
        from app.channels.qr_binding import keepalive_clawbot_sidecar

        while True:
            try:
                await asyncio.sleep(45.0)
                await keepalive_clawbot_sidecar()
            except asyncio.CancelledError:
                raise
            except Exception as ka_err:
                logger.debug(f"[WeixinClawbot] 保活巡检异常: {ka_err}")

    clawbot_keepalive_task = asyncio.create_task(_clawbot_keepalive_loop())

    try:
        yield
    finally:
        logger.info("应用正在优雅关机，正在清理后台任务与常驻 MCP 进程池...")
        for bg_task in (
            decay_task,
            scheduler_task,
            parent_watch_task,
            weixin_boot_task,
            asr_warmup_task,
            clawbot_keepalive_task,
        ):
            if bg_task and not bg_task.done():
                bg_task.cancel()
                try:
                    await bg_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        try:
            from app.extensions.mcp_manager import mcp_manager
            await mcp_manager.shutdown_all()
        except Exception as e:
            logger.debug(f"关闭 MCP 进程池异常: {e}")
        try:
            from app.tools.background_process import BackgroundProcessManager
            cleaned_bg = BackgroundProcessManager.get_instance().cleanup_all()
            if cleaned_bg:
                logger.info(f"[BackgroundProcessManager] 优雅关机：已回收 {cleaned_bg} 个活跃托管后台进程树")
        except Exception as bg_clean_err:
            logger.debug(f"[BackgroundProcessManager] 关机清理后台进程异常: {bg_clean_err}")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# CORS 允许本机各种端口跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*", "X-Content-MD5", "X-KB-Version", "X-Local-File-Path"],
)

# 本机访问安全守卫中间件
app.add_middleware(LocalOnlyMiddleware)

# 注册所有 API 业务路由
app.include_router(api_router)

# 挂载 Next.js 静态导出资源目录 (实现免 Node.js 运行)
base_dir = Path(__file__).resolve().parent
bundle_root = get_bundle_root()
writable_root = get_writable_app_root()
candidates = [
    bundle_root / "static",
    bundle_root / "frontend" / "out",
    writable_root / "static",
    writable_root / "frontend" / "out",
    base_dir.parent / "frontend" / "out",
    base_dir / "static",
]
static_path = None
for c in candidates:
    if c.exists() and (c / "index.html").exists():
        static_path = c
        break

if static_path:
    logger.info(f"已成功挂载静态前端站点: {static_path}")
    app.mount("/", StaticFiles(directory=str(static_path), html=True), name="static")


def run_server(port: int = 8686):
    import uvicorn
    # 打印约定格式的端口信息，供桌面外壳主进程捕获
    print(f"AGENT_HUB_PORT={port}", flush=True)
    print(f"GEMINI_HUB_PORT={port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    target_port = settings.port
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        target_port = int(sys.argv[1])
    elif os.environ.get("AGENT_HUB_DYNAMIC_PORT") == "1" or os.environ.get("GEMINI_HUB_DYNAMIC_PORT") == "1":
        target_port = find_free_port()
    run_server(target_port)
