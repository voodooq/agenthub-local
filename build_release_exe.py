"""
AgentHub Local 独立桌面可执行程序构建脚本 (Windows 单文件 EXE & macOS .app / DMG)
核心目标：
1. 从根目录唯一配置源 version.json 读取当前版本并自动同步全仓生态清单
2. 彻底瘦身：强力排除无关重型科学计算与测试库（pandas / numpy / scipy / matplotlib / pytest 等），减负 ~19 MB
3. 分平台产出独立成品（不与便携源码包混装）：
   - Windows (--mode onefile，默认)：单文件独立运行 dist/AgentHub-Local-v<ver>-windows-x64.exe
   - Windows (--mode onedir)：免解压极速绿包目录 dist/AgentHub/
   - macOS (--mode macos-dmg)：生成原生 AgentHub.app 并打包为 dist/AgentHub-Local-v<ver>-macos-<arch>.dmg
4. 严格隔离与断言 0 个人数据/测试代码/冗余依赖泄漏
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from sync_version import load_version_config, sync_all_manifests

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_OUT = FRONTEND_DIR / "out"
DIST_DIR = BASE_DIR / "dist"
BUILD_DIR = BASE_DIR / "build"
APP_NAME = "AgentHub"

# 严禁进入独立桌面发行包的重型/测试无关依赖（切断 openpyxl -> pandas -> numpy 约 19 MB 体积膨胀链）
EXCLUDED_MODULES: tuple[str, ...] = (
    "pandas",
    "numpy",
    "scipy",
    "matplotlib",
    "pytest",
    "_pytest",
    "tkinter",
    "IPython",
    "jupyter",
    "notebook",
    "setuptools.tests",
)


def _detect_arch_tag() -> str:
    """规范化宿主 CPU 架构后缀名（用于发布包文件名）。"""
    mach = platform.machine().lower()
    if mach in ("arm64", "aarch64"):
        return "arm64"
    if mach in ("amd64", "x86_64", "x64"):
        return "x64"
    return mach or "x64"


def prepare_clean_backend_stage() -> Path:
    """准备纯净的 backend 运行时源码切片，物理阻断 data/、tests/、缓存文件进入发行包。"""
    clean_backend_dir = BUILD_DIR / "_clean_backend"
    if clean_backend_dir.exists():
        shutil.rmtree(clean_backend_dir, ignore_errors=True)
    clean_backend_dir.mkdir(parents=True, exist_ok=True)

    src_backend = BASE_DIR / "backend"
    ignore_fn = shutil.ignore_patterns(
        "__pycache__",
        "*.pyc",
        "*.pyo",
        "*.db",
        "*.db-wal",
        "*.db-shm",
        "*.sqlite*",
        "*.log",
        ".access_token",
        ".env",
    )

    shutil.copytree(src_backend / "app", clean_backend_dir / "app", ignore=ignore_fn)
    for fname in ("__init__.py", "main.py", "requirements.txt"):
        src_file = src_backend / fname
        if src_file.is_file():
            shutil.copy2(src_file, clean_backend_dir / fname)

    return clean_backend_dir


def prepare_clean_skills_stage() -> Path | None:
    """准备纯净的全局内置底座技能种子目录（打包进 sys._MEIPASS，首次运行自动释放到可写目录）。"""
    src_skills = BASE_DIR / "skills"
    if not src_skills.is_dir():
        return None
    clean_skills_dir = BUILD_DIR / "_clean_skills"
    if clean_skills_dir.exists():
        shutil.rmtree(clean_skills_dir, ignore_errors=True)
    shutil.copytree(
        src_skills,
        clean_skills_dir,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git*", ".DS_Store"),
    )
    return clean_skills_dir


def ensure_frontend_built() -> None:
    """确保前端静态导出文件存在且可用。"""
    if not (FRONTEND_OUT / "index.html").is_file():
        print(">>> 正在编译前端静态导出产物 (Next.js Static Export)...")
        npm_bin = "npm.cmd" if sys.platform == "win32" else "npm"
        subprocess.run([npm_bin, "run", "build"], cwd=str(FRONTEND_DIR), check=True)
    else:
        print(f">>> 前端静态导出产物已就绪: {FRONTEND_OUT}")


def _assert_toc_and_stage_clean() -> None:
    """精确校验 PyInstaller PYZ/PKG/COLLECT 清单及暂存切片，确保 0 隐私泄漏并剔除无关重型包。"""
    import ast

    forbidden_top_pkgs = {"pandas", "numpy", "scipy", "matplotlib", "pytest", "_pytest"}
    pyz_toc = BUILD_DIR / APP_NAME / "PYZ-00.toc"
    if pyz_toc.is_file():
        try:
            _, entries = ast.literal_eval(pyz_toc.read_text(encoding="utf-8", errors="ignore"))
            leaked_mods = sorted({
                name.split(".")[0]
                for name, _, _ in entries
                if isinstance(name, str) and name.split(".")[0] in forbidden_top_pkgs
            })
            if leaked_mods:
                raise RuntimeError(f"打包质量门控拦截：PYZ 中发现本应排除的重型模块 {leaked_mods}")
        except SyntaxError:
            pass

    for toc_name in ("PKG-00.toc", "COLLECT-00.toc"):
        toc_file = BUILD_DIR / APP_NAME / toc_name
        if not toc_file.is_file():
            continue
        toc_text = toc_file.read_text(encoding="utf-8", errors="ignore").lower().replace("\\", "/")
        for bad_tok in ("backend/tests", "backend/data/", ".access_token", "agenthub_local.db"):
            if bad_tok in toc_text:
                raise RuntimeError(f"安全红线阻断：{toc_name} 中检测到禁止内容 {bad_tok}")

    clean_backend_dir = BUILD_DIR / "_clean_backend"
    for bad in (clean_backend_dir / "data", clean_backend_dir / "tests"):
        if bad.exists():
            raise RuntimeError(f"安全红线阻断：发现非法目录残留 {bad}")


def build_executable(mode: str, release_version: str) -> Path:
    """调用 PyInstaller 按目标模式构建独立可运行桌面程序，返回核心产物路径。"""
    ensure_frontend_built()
    clean_backend_dir = prepare_clean_backend_stage()
    clean_skills_dir = prepare_clean_skills_stage()

    # 清理旧同名产物，防止新旧版本残留混淆
    if mode == "onefile":
        for stale in (DIST_DIR / f"{APP_NAME}.exe", DIST_DIR / f"AgentHub-Local-{release_version}-windows-{_detect_arch_tag()}.exe"):
            if stale.exists():
                stale.unlink()
    elif mode == "macos-dmg":
        app_bundle = DIST_DIR / f"{APP_NAME}.app"
        if app_bundle.exists():
            shutil.rmtree(app_bundle, ignore_errors=True)
        for stale_dir in (DIST_DIR / APP_NAME,):
            if stale_dir.exists():
                shutil.rmtree(stale_dir, ignore_errors=True)

    print(f"\n>>> 正在使用 PyInstaller 编译桌面应用 ({mode} / {release_version})...")

    sep = ";" if sys.platform == "win32" else ":"
    pyinstaller_cmd: list[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--windowed",
        "--onefile" if mode == "onefile" else "--onedir",
        "--paths",
        str(BASE_DIR / "backend"),
        "--add-data",
        f"{FRONTEND_OUT}{sep}static",
        "--add-data",
        f"{clean_backend_dir}{sep}backend",
        "--add-data",
        f"{BASE_DIR / 'version.json'}{sep}.",
    ]

    if clean_skills_dir is not None:
        pyinstaller_cmd.extend(["--add-data", f"{clean_skills_dir}{sep}skills"])
    mcp_cfg = BASE_DIR / "mcp_servers.json"
    if mcp_cfg.is_file():
        pyinstaller_cmd.extend(["--add-data", f"{mcp_cfg}{sep}."])

    if sys.platform == "darwin":
        pyinstaller_cmd.extend(["--osx-bundle-identifier", "com.agenthub.desktop"])

    # 强制排除重型非必须科学计算与测试模块
    for mod in EXCLUDED_MODULES:
        pyinstaller_cmd.extend(["--exclude-module", mod])

    # 按平台精准收集原生 WebView 与运行依赖
    collect_pkgs = [
        "webview",
        "uvicorn",
        "fastapi",
        "starlette",
        "pydantic",
        "pydantic_settings",
        "psutil",
        "aiosqlite",
        "httpx",
        "requests",
    ]
    if sys.platform == "win32":
        collect_pkgs.extend(["clr_loader", "pythonnet"])

    for pkg in collect_pkgs:
        pyinstaller_cmd.extend(["--collect-all", pkg])

    # 仅收集纯 Python 解析所需的 openpyxl & et_xmlfile（绝不用 --collect-all openpyxl 以防带出额外冗余）
    pyinstaller_cmd.extend(["--collect-submodules", "openpyxl", "--collect-submodules", "et_xmlfile"])

    if sys.platform == "darwin":
        pyinstaller_cmd.extend(["--hidden-import", "webview.platforms.cocoa"])

    hidden_imports = [
        "openpyxl",
        "backend.main",
        "backend.app.api.router",
        "backend.app.api.v1.artifacts",
        "backend.app.api.v1.experiences",
        "backend.app.api.v1.memories",
        "backend.app.api.v1.skills",
        "backend.app.api.v1.loop",
        "backend.app.agent.runtime",
        "backend.app.agent.context_builder",
        "backend.app.agent.policy_interceptor",
        "backend.app.agent.tool_protocol",
        "backend.app.agent.shell_executor",
        "backend.app.agent.ui_operator",
        "backend.app.core.runtime_paths",
        "backend.app.repository.db",
        "backend.app.repository.artifact_repository",
        "backend.app.repository.conversation_repository",
        "backend.app.repository.message_repository",
        "backend.app.repository.workspace_repository",
        "backend.app.repository.loop_run_repository",
        "backend.app.repository.scheduled_repository",
        "backend.app.repository.search_repository",
        "backend.app.service.engine_pool",
        "backend.app.service.audit_service",
        "backend.app.service.attachment_service",
        "backend.app.memory",
        "backend.app.memory.long_term",
        "backend.app.memory.policy_guard",
        "backend.app.memory.ledger",
        "backend.app.memory.fact_manager",
        "backend.app.memory.session_summary",
        "backend.app.memory.workspace_fts",
        "backend.app.skills",
        "backend.app.skills.skill_service",
        "backend.app.skills.experience_distiller",
        "backend.app.extensions.skill_loader",
        "backend.app.extensions.mcp_manager",
        "backend.app.schema.chat_schema",
        "backend.app.schema.loop_schema",
    ]
    for hi in hidden_imports:
        pyinstaller_cmd.extend(["--hidden-import", hi])

    pyinstaller_cmd.append(str(BASE_DIR / "desktop_app.py"))

    print(">>> 启动 PyInstaller 编译执行...")
    subprocess.run(pyinstaller_cmd, cwd=str(BASE_DIR), check=True)
    _assert_toc_and_stage_clean()

    arch_tag = _detect_arch_tag()
    if mode == "onefile":
        raw_exe = DIST_DIR / (f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME)
        if not raw_exe.is_file():
            raise FileNotFoundError(f"编译完成，但未找到预期单文件程序: {raw_exe}")
        ext = ".exe" if sys.platform == "win32" else ""
        plat_tag = "windows" if sys.platform == "win32" else sys.platform
        final_exe = DIST_DIR / f"AgentHub-Local-{release_version}-{plat_tag}-{arch_tag}{ext}"
        if final_exe.exists():
            final_exe.unlink()
        raw_exe.replace(final_exe)
        size_mb = final_exe.stat().st_size / (1024 * 1024)
        print("\n=================================================================")
        print(f"🎉 AgentHub Local {release_version} 独立单文件客户端构建完成！")
        print(f"🚀 单文件独立可执行程序: {final_exe} ({size_mb:.2f} MB)")
        print("   ✓ 已内置静态前端/后端微内核/默认技能/MCP 配置，支持单文件双击运行")
        print("   ✓ 已裁剪 pandas / numpy / 测试代码等冗余体积，并保证 0 隐私数据泄漏")
        print("=================================================================\n")
        return final_exe

    if mode == "macos-dmg":
        app_bundle = DIST_DIR / f"{APP_NAME}.app"
        if not app_bundle.is_dir():
            raise FileNotFoundError(f"编译完成，但未找到预期的 macOS .app 应用包: {app_bundle}")
        dmg_stage = BUILD_DIR / "_dmg_stage"
        if dmg_stage.exists():
            shutil.rmtree(dmg_stage, ignore_errors=True)
        dmg_stage.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app_bundle, dmg_stage / f"{APP_NAME}.app", symlinks=True)
        os.symlink("/Applications", dmg_stage / "Applications")

        dmg_path = DIST_DIR / f"AgentHub-Local-{release_version}-macos-{arch_tag}.dmg"
        if dmg_path.exists():
            dmg_path.unlink()
        print(f">>> 正在封装 macOS 安装镜像: {dmg_path.name} ...")
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                f"AgentHub Local {release_version}",
                "-srcfolder",
                str(dmg_stage),
                "-ov",
                "-format",
                "UDZO",
                str(dmg_path),
            ],
            check=True,
        )
        shutil.rmtree(dmg_stage, ignore_errors=True)
        size_mb = dmg_path.stat().st_size / (1024 * 1024)
        print("\n=================================================================")
        print(f"🎉 AgentHub Local {release_version} macOS 安装镜像构建完成！")
        print(f"🍏 macOS DMG 镜像: {dmg_path} ({size_mb:.2f} MB)")
        print("=================================================================\n")
        return dmg_path

    # onedir 模式保留兼容（例如本地开发调试免解压目录）
    out_dir = DIST_DIR / APP_NAME
    exe_file = out_dir / (f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME)
    if not exe_file.is_file():
        raise FileNotFoundError(f"编译完成，但未找到预期的主程序: {exe_file}")
    print(f"✓ 目录版可执行程序已生成: {exe_file}")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 AgentHub Local 独立桌面发行文件")
    parser.add_argument(
        "--mode",
        choices=("auto", "onefile", "onedir", "macos-dmg"),
        default="auto",
        help="构建模式: auto(Win默认onefile, Mac默认macos-dmg) | onefile | onedir | macos-dmg",
    )
    args = parser.parse_args()

    changed_manifests = sync_all_manifests(BASE_DIR)
    if changed_manifests:
        print(f">>> 已从 version.json 自动同步以下生态清单: {', '.join(changed_manifests)}")

    version_cfg = load_version_config(BASE_DIR)
    release_version = f"v{version_cfg['version']}"

    resolved_mode = args.mode
    if resolved_mode == "auto":
        resolved_mode = "macos-dmg" if sys.platform == "darwin" else "onefile"

    build_executable(resolved_mode, release_version)


if __name__ == "__main__":
    main()
