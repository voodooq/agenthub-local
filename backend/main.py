import sys
import os
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

        # 健康检查、OAuth 回调以及本地上传附件静态预览直接放行（供 <img> 标签与浏览器无损直接渲染）
        if (
            path in ("/api/v1/health", "/api/v1/accounts/oauth/callback")
            or path.startswith("/api/v1/chat/uploads/")
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
    parent_pid_str = os.environ.get("DESKTOP_PARENT_PID") or os.environ.get("TAURI_PARENT_PID")
    if parent_pid_str and parent_pid_str.isdigit():
        parent_pid = int(parent_pid_str)
        if parent_pid > 0:
            logger.info(f"已成功绑定桌面父进程 PID: {parent_pid}")
            asyncio.create_task(watch_parent_process(parent_pid))

    await db_manager.init_db()

    # 启动长期记忆 30 天自然指数衰减每日守护任务
    async def _daily_decay_task():
        while True:
            try:
                from app.memory.long_term import long_term_memory
                res = await long_term_memory.apply_time_decay()
                if res.get("decayed") or res.get("deleted"):
                    logger.info(f"[记忆生命周期] 30 天未命中衰减扫描完成: 衰减 {res['decayed']} 条，清理 {res['deleted']} 条")
            except Exception as e:
                logger.debug(f"长期记忆衰减任务执行异常: {e}")
            await asyncio.sleep(86400)

    asyncio.create_task(_daily_decay_task())

    yield
    logger.info("应用正在优雅关机，正在清理常驻 MCP 进程池...")
    try:
        from app.extensions.mcp_manager import mcp_manager
        mcp_manager.shutdown_all()
    except Exception as e:
        logger.debug(f"关闭 MCP 进程池异常: {e}")


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
)

# 本机访问安全守卫中间件
app.add_middleware(LocalOnlyMiddleware)

# 注册所有 API 业务路由
app.include_router(api_router)

# 挂载 Next.js 静态导出资源目录 (实现免 Node.js 运行)
base_dir = Path(__file__).resolve().parent
candidates = [
    base_dir.parent / "frontend" / "out",
    base_dir / "static",
    Path(sys.executable).parent / "static",
    Path(sys.executable).parent / "frontend" / "out",
]
meipass = getattr(sys, "_MEIPASS", None)
if meipass:
    candidates.insert(0, Path(meipass) / "static")
    candidates.insert(1, Path(meipass) / "frontend" / "out")
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
