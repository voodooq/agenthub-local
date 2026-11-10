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

# 将源码根目录与 backend 路径加入 sys.path（兼容源码开发及 PyInstaller 冻结包）
_MEIPASS = getattr(sys, "_MEIPASS", None)
if _MEIPASS:
    BASE_DIR = Path(_MEIPASS).resolve()
else:
    BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
_BACKEND_DIR = BASE_DIR / "backend"
if _BACKEND_DIR.is_dir() and str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from backend.main import app, find_free_port
from backend.app.core.config import settings
from backend.app.core.logging_config import logger


def start_backend_server(port: int):
    """在后台独立线程中托管 FastAPI 服务"""
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


def _set_windows_app_user_model_id() -> None:
    """设置 Windows 独立 AppUserModelID，确保任务栏显示自定义图标而非默认宿主图标。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.agenthub.desktop")
    except Exception:
        pass


def _apply_windows_window_icon(icon_path: Path) -> None:
    """将自定义透明图标绑定到当前进程的 pywebview Windows 窗体（大小图标）。"""
    if sys.platform != "win32" or not icon_path.is_file():
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010

        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t

        h_icon_big = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
        h_icon_small = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if not h_icon_big and not h_icon_small:
            return

        current_pid = os.getpid()
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _enum_cb(hwnd: int, _lparam: int) -> bool:
            proc_id = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value == current_pid and user32.IsWindowVisible(hwnd):
                if h_icon_big:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_icon_big)
                if h_icon_small:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_icon_small)
            return True

        user32.EnumWindows(EnumWindowsProc(_enum_cb), 0)
    except Exception:
        pass


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

    _set_windows_app_user_model_id()

    # 1. 检测命令行参数
    is_debug = "--debug" in sys.argv or os.environ.get("AGENT_HUB_DEBUG") == "1" or os.environ.get("GEMINI_HUB_DEBUG") == "1"
    is_force_reload = is_debug or "--reload" in sys.argv or "--force" in sys.argv or os.environ.get("AGENT_HUB_FORCE") == "1" or os.environ.get("GEMINI_HUB_FORCE") == "1"

    # 如果在 Windows 带有 --debug 或 --force，自动在启动前清空 WebView2 本地强缓存目录，确保代码 100% 强制最新
    if is_force_reload and sys.platform == "win32":
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
        title=f"AgentHub Local v{settings.app_version} - 本地全场景多源算力与智能体工作台",
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

    def on_shown():
        # 窗体就绪后绑定原生透明 Logo 图标到 Windows 标题栏与任务栏
        icon_file = BASE_DIR / "assets" / "app_icon.ico"
        _apply_windows_window_icon(icon_file)

    if window:
        window.events.closed += on_closed
        window.events.shown += on_shown

    # 启动 GUI 事件循环（private_mode=True 彻底禁用 WebView2 磁盘与 HTTP 强缓存，保证修改代码立即生效）
    webview.start(debug=is_debug, private_mode=True)


if __name__ == "__main__":
    main()

