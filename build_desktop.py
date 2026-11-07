"""
GeminiHub WorkBuddy 桌面可执行程序一键构建脚本 (Desktop EXE Builder)
"""
import sys
import os
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_OUT = FRONTEND_DIR / "out"


def build_frontend_if_needed():
    """确保前端静态导出目录已就绪"""
    if not (FRONTEND_OUT / "index.html").exists():
        print(">>> 正在编译前端静态导出产物 (Next.js Static Export)...")
        env = os.environ.copy()
        env["NEXT_EXPORT"] = "true"
        subprocess.run(["npm", "run", "build"], cwd=str(FRONTEND_DIR), env=env, check=True, shell=True)
    else:
        print(f">>> 检测到前端静态产物已就绪: {FRONTEND_OUT}")


def build_exe():
    """调用 PyInstaller 打包生成独立桌面程序"""
    build_frontend_if_needed()

    print(">>> 正在启动 PyInstaller 编译桌面原生程序 GeminiHub.exe ...")
    sep = ";" if sys.platform == "win32" else ":"
    static_arg = f"{FRONTEND_OUT}{sep}static"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        "GeminiHub",
        "--windowed",  # 无黑色命令行控制台窗口
        "--onedir",    # 目录模式启动速度最快，秒级打开，免重复解压
        "--add-data",
        static_arg,
        "--collect-all",
        "webview",
        "--collect-all",
        "clr_loader",
        "--collect-all",
        "pythonnet",
        "--collect-all",
        "uvicorn",
        "--collect-all",
        "psutil",
        "--collect-all",
        "aiosqlite",
        "--collect-all",
        "httpx",
        "--hidden-import",
        "backend.main",
        str(BASE_DIR / "desktop_app.py"),
    ]

    print(">>> 执行编译命令:", " ".join(cmd))
    subprocess.run(cmd, cwd=str(BASE_DIR), check=True)

    exe_path = BASE_DIR / "dist" / "GeminiHub" / "GeminiHub.exe"
    if exe_path.exists():
        print("\n=======================================================")
        print("[SUCCESS] 桌面原生程序构建成功！")
        print(f"运行路径: {exe_path}")
        print("双击 GeminiHub.exe 即可直接打开原生窗口应用！")
        print("=======================================================\n")
    else:
        print(">>> 构建完成，但未在预期路径找到 GeminiHub.exe，请检查 dist/ 目录。")


if __name__ == "__main__":
    build_exe()
