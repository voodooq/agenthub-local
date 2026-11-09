"""
AgentHub Local 轻量跨平台便携源码包构建脚本 (Portable Release Builder)
特性：
1. 读取根目录 version.json 并同步全仓生态清单
2. 产出纯净、轻量的跨平台源码运行包（严格不含 .exe、二进制依赖库、data/、tests/、缓存文件）
3. 同时输出 Windows 通用压缩包 (*-portable.zip) 与 macOS / Linux 压缩包 (*-portable.tar.gz)
"""
import os
import shutil
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

# 强制开启 UTF-8 IO：防止英文版 Windows CI Runner 默认 cp1252 编码打印中文或 Emoji 崩溃
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

from sync_version import load_version_config, sync_all_manifests

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"
BUILD_DIR = BASE_DIR / "build"
VERSION = f"v{load_version_config(BASE_DIR)['version']}"
PKG_NAME = f"AgentHub-Local-{VERSION}"
STAGE_DIR = BUILD_DIR / f"_portable_{PKG_NAME}"


def _clean_stage_dir() -> Path:
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR, ignore_errors=True)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    return STAGE_DIR


def _assert_zero_leakage(target_dir: Path) -> None:
    """安全红线校验：断言便携包内无个人数据库、访问令牌、上传或测试目录残留"""
    for forbidden_dir in (
        target_dir / "data",
        target_dir / "backend" / "data",
        target_dir / "backend" / "tests",
    ):
        if forbidden_dir.exists():
            raise RuntimeError(f"安全红线阻断：便携包内发现禁止目录残留 {forbidden_dir}")

    for pat in ("*.db", "*.db-wal", "*.db-shm", "*.sqlite*", "*.log", ".access_token", ".env"):
        matches = list(target_dir.rglob(pat))
        if matches:
            raise RuntimeError(f"安全红线阻断：便携包内发现敏感文件残留 {[str(m) for m in matches]}")


def package() -> tuple[Path, Path]:
    """构建轻量跨平台便携 zip 与 tar.gz 压缩包，返回 (zip_path, tar_path)。"""
    sync_all_manifests(BASE_DIR)
    cfg = load_version_config(BASE_DIR)
    version = f"v{cfg['version']}"
    pkg_name = f"AgentHub-Local-{version}"

    print(f"=== 开始打包 AgentHub Local {version} 轻量跨平台便携包 ===")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    stage_dir = _clean_stage_dir()

    # 1. 复制后端运行核心源码 (严格排除 __pycache__、个人 data、tests 等)
    print("  -> 拷贝后端核心源码 backend/app/ ...")
    ignore_fn = shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.pyo", "*.db*", "data", "tests", "logs", "*.log", "diagnose.py", ".access_token"
    )
    shutil.copytree(BASE_DIR / "backend" / "app", stage_dir / "backend" / "app", ignore=ignore_fn)
    for fname in ("__init__.py", "main.py", "requirements.txt"):
        src_f = BASE_DIR / "backend" / fname
        if src_f.is_file():
            shutil.copy2(src_f, stage_dir / "backend" / fname)

    # 2. 复制已导出的静态前端 frontend/out
    frontend_out = BASE_DIR / "frontend" / "out"
    if not (frontend_out / "index.html").is_file():
        raise FileNotFoundError(f"未找到前端静态产物: {frontend_out}，请先执行 npm --prefix frontend run build 导出！")
    print("  -> 拷贝前端静态资源 frontend/out/ ...")
    shutil.copytree(frontend_out, stage_dir / "frontend" / "out")

    # 3. 复制全局底座技能 skills/
    src_skills = BASE_DIR / "skills"
    if src_skills.is_dir():
        print("  -> 拷贝底座技能库 skills/ ...")
        shutil.copytree(
            src_skills,
            stage_dir / "skills",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git*"),
        )

    # 4. 拷贝启动脚本与配置文件
    file_list = [
        "version.json",
        "desktop_app.py",
        "requirements.txt",
        "mcp_servers.json",
        "README.md",
        "LICENSE",
        ".env.example",
        "启动AgentHub桌面版.bat",
        "启动AgentHub_调试模式(带控制台日志).bat",
        "启动AgentHub桌面版.sh",
    ]
    for fname in file_list:
        src = BASE_DIR / fname
        if src.is_file():
            dst = stage_dir / fname
            shutil.copy2(src, dst)
            if fname.endswith(".sh"):
                try:
                    os.chmod(dst, 0o755)
                except OSError:
                    pass

    _assert_zero_leakage(stage_dir)

    # 5. 生成轻量通用 Zip 包 (Windows/通用)
    zip_path = DIST_DIR / f"{pkg_name}-portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    print(f"  -> 正在压缩生成便携 Zip 包: {zip_path.name} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(stage_dir):
            for file in files:
                full_path = Path(root) / file
                rel_inside = full_path.relative_to(stage_dir)
                arcname = f"{pkg_name}/{rel_inside.as_posix()}"
                zf.write(full_path, arcname=arcname)

    # 6. 生成轻量跨平台 Tar.gz 包 (macOS / Linux，保留 .sh 可执行位 0755)
    tar_path = DIST_DIR / f"{pkg_name}-portable.tar.gz"
    if tar_path.exists():
        tar_path.unlink()
    print(f"  -> 正在压缩生成跨平台 Tar.gz 包: {tar_path.name} ...")

    def _tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
        if tarinfo.name.endswith(".sh"):
            tarinfo.mode = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
        return tarinfo

    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(stage_dir, arcname=pkg_name, filter=_tar_filter)

    shutil.rmtree(stage_dir, ignore_errors=True)

    zip_mb = zip_path.stat().st_size / (1024 * 1024)
    tar_mb = tar_path.stat().st_size / (1024 * 1024)
    print("\n=======================================================")
    print(f"🎉 AgentHub Local {version} 轻量跨平台便携包构建成功！")
    print(f"1. 轻量 Zip 包 (Win/通用):    {zip_path} ({zip_mb:.2f} MB)")
    print(f"2. 轻量 Tar.gz 包 (Mac/Linux): {tar_path} ({tar_mb:.2f} MB)")
    print("=======================================================\n")
    return zip_path, tar_path


if __name__ == "__main__":
    package()
