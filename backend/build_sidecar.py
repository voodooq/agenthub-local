"""
PyInstaller Sidecar 跨平台自动打包命名脚本 (Sidecar Target Triple Builder)
按 Tauri 2.0 规格将 Python 后端编译并命名为附带 Host Target Triple 的二进制文件
"""
import sys
import os
import shutil
import subprocess
from pathlib import Path


def get_tauri_target_triple() -> str:
    """获取当前平台的 Rust Target Triple（例如 x86_64-pc-windows-msvc）"""
    try:
        out = subprocess.check_output(["rustc", "-Vv"], text=True)
        for line in out.splitlines():
            if line.startswith("host:"):
                return line.split()[1].strip()
    except Exception:
        pass

    # 若本机暂未安装 rustc，基于系统架构自动匹配标准 Target Triple
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc"
    elif sys.platform == "darwin":
        import platform
        return "aarch64-apple-darwin" if platform.machine() == "arm64" else "x86_64-apple-darwin"
    else:
        return "x86_64-unknown-linux-gnu"


def build_sidecar():
    base_dir = Path(__file__).resolve().parent
    triple = get_tauri_target_triple()
    ext = ".exe" if sys.platform == "win32" else ""

    print(f">>> 目标 Target Triple: {triple}")
    print(">>> 正在启动 PyInstaller 编译 Sidecar 内核...")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "agenthub-backend",
        str(base_dir / "main.py"),
    ]
    subprocess.run(cmd, cwd=str(base_dir), check=True)

    src = base_dir / "dist" / f"agenthub-backend{ext}"
    dst_dir = base_dir.parent / "src-tauri" / "binaries"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"agenthub-backend-{triple}{ext}"

    if src.exists():
        shutil.move(str(src), str(dst))
        print(f"🎉 Tauri Sidecar 二进制构建完成: {dst}")
    else:
        print("未找到构建产物，请检查 backend/dist/")


if __name__ == "__main__":
    build_sidecar()

