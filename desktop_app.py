"""
AgentHub Local 桌面原生客户端启动入口 (Desktop Native App Entry)
基于 Windows 原生 Microsoft Edge WebView2 运行时 + FastAPI + 静态前端
实现免环境依赖、无黑框原生窗体、动态端口分配与父子生命周期强绑定
"""
import sys
import os
import time
import socket
import threading
import uvicorn
import webview
from pathlib import Path

# 将项目路径与 backend 路径加入 sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "backend"))

from backend.main import app, find_free_port
from backend.app.core.logging_config import logger


def start_backend_server(port: int):
    """在后台独立线程中托管 FastAPI 服务"""
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


def wait_for_server_ready(port: int, max_retries: int = 50) -> bool:
    """轮询等待本地服务就绪"""
    for _ in range(max_retries):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.1)
    return False


def main():
    import shutil

    # 1. 检测命令行参数
    is_debug = "--debug" in sys.argv or os.environ.get("AGENT_HUB_DEBUG") == "1" or os.environ.get("GEMINI_HUB_DEBUG") == "1"
    is_force_reload = is_debug or "--reload" in sys.argv or "--force" in sys.argv or os.environ.get("AGENT_HUB_FORCE") == "1" or os.environ.get("GEMINI_HUB_FORCE") == "1"

    # 如果带有 --debug 或 --force，自动在启动前彻底清空 WebView2 本地强缓存目录，确保代码 100% 强制最新
    if is_force_reload:
        cache_root = Path(os.environ.get("APPDATA", "")) / "pywebview" / "EBWebView"
        if cache_root.exists():
            for folder_name in ("Cache", "Code Cache", "DawnCache", "GPUCache", "GrShaderCache", "ShaderCache"):
                folder_path = cache_root / "Default" / folder_name
                if folder_path.exists():
                    shutil.rmtree(folder_path, ignore_errors=True)
            print("[调试模式] 已自动清除 WebView2 历史缓存，执行强制刷新加载最新代码。", flush=True)

    # 2. 动态分配或检测可用端口
    target_port = 8686
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", target_port))
    except OSError:
        target_port = find_free_port()

    # 3. 标记当前主进程 PID 供守护线程监听
    os.environ["DESKTOP_PARENT_PID"] = str(os.getpid())

    # 4. 启动后台服务线程
    server_thread = threading.Thread(target=start_backend_server, args=(target_port,), daemon=True)
    server_thread.start()

    # 5. 等待本地站点完全就绪
    if not wait_for_server_ready(target_port):
        print(f"本地服务在端口 {target_port} 启动超时，请重试。")
        sys.exit(1)

    # 追加随机时间戳 query 参数，彻底粉碎任何 HTTP 级别强缓存
    app_url = f"http://127.0.0.1:{target_port}/?_t={int(time.time())}"

    # 6. 调用 Windows 原生 WebView2 渲染独立原生窗口 (支持启动即自动最大化全屏、F12 开发者工具与调试模式)
    window = webview.create_window(
        title="AgentHub Local v1.1.2 - 本地全场景多源算力与智能体工作台",
        url=app_url,
        width=1440,
        height=900,
        min_size=(1024, 680),
        background_color="#101513",
        text_select=True,
        maximized=True,
    )

    def on_closed():
        # 窗口彻底销毁时退出整个进程树
        os._exit(0)

    if window:
        window.events.closed += on_closed

    # 启动 GUI 事件循环（private_mode=True 彻底禁用 WebView2 磁盘与 HTTP 强缓存，保证修改代码立即生效）
    webview.start(debug=is_debug, private_mode=True)


if __name__ == "__main__":
    main()

