"""
AgentHub Local v1.1.7 本地离线发布打包脚本
生成独立 Windows 便携包与跨平台便携运行包至 dist/ 目录
"""
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"
VERSION = "v1.1.7"
PKG_NAME = f"AgentHub-Local-{VERSION}"
STAGE_DIR = DIST_DIR / PKG_NAME


def package():
    print(f"=== 开始打包 AgentHub Local {VERSION} ===")
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR, ignore_errors=True)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 复制后端目录 (严格排除 __pycache__、个人 data/uploads、tests 等)
    print("  -> 拷贝后端核心模块 backend/ ...")
    shutil.copytree(
        BASE_DIR / "backend",
        STAGE_DIR / "backend",
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.db*", "data", "tests", "logs", "*.log", "diagnose.py"
        ),
    )

    # 2. 复制已导出的静态前端 frontend/out
    frontend_out = BASE_DIR / "frontend" / "out"
    if not frontend_out.exists():
        raise FileNotFoundError(f"未找到前端静态产物: {frontend_out}，请先执行 npm run build 导出！")
    print("  -> 拷贝前端静态资源 frontend/out/ ...")
    shutil.copytree(frontend_out, STAGE_DIR / "frontend" / "out")

    # 3. 复制全局底座技能 skills/
    if (BASE_DIR / "skills").exists():
        print("  -> 拷贝底座技能库 skills/ ...")
        shutil.copytree(BASE_DIR / "skills", STAGE_DIR / "skills")

    # 4. 拷贝启动脚本与配置文件
    file_list = [
        "desktop_app.py",
        "requirements.txt",
        "mcp_servers.json",
        "README.md",
        "LICENSE",
        ".env.example",
        "启动AgentHub桌面版.bat",
        "启动AgentHub_调试模式(带控制台日志).bat",
    ]
    for fname in file_list:
        src = BASE_DIR / fname
        if src.exists():
            shutil.copy2(src, STAGE_DIR / fname)

    # 5. 生成 Windows 便携 Zip 包
    zip_path = DIST_DIR / f"{PKG_NAME}-windows-x64.zip"
    print(f"  -> 正在压缩生成 Windows 客户端包: {zip_path.name} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(STAGE_DIR):
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(DIST_DIR)
                zf.write(full_path, arcname=str(rel_path))

    # 6. 生成跨平台便携 tar.gz 包
    tar_path = DIST_DIR / f"{PKG_NAME}-portable.tar.gz"
    print(f"  -> 正在压缩生成跨平台便携包: {tar_path.name} ...")
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(STAGE_DIR, arcname=PKG_NAME)

    # 清理暂存目录
    shutil.rmtree(STAGE_DIR, ignore_errors=True)

    print("\n=======================================================")
    print(f"🎉 AgentHub Local {VERSION} 多客户端离线包构建成功！")
    print(f"1. Windows 客户端包: {zip_path} ({round(zip_path.stat().st_size / (1024 * 1024), 2)} MB)")
    print(f"2. 跨平台便携包:     {tar_path} ({round(tar_path.stat().st_size / (1024 * 1024), 2)} MB)")
    print("=======================================================\n")


if __name__ == "__main__":
    package()

