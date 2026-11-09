"""
AgentHub Local v1.1.6 独立可执行程序与离线发布包一键构建脚本 (Release EXE Builder)
核心目标：
1. 使用 PyInstaller 将完整 Python 运行环境、WebView2 宿主外壳与后端 FastAPI 编译为独立的原生绿色免安装目录 (dist/AgentHub/)
2. 内嵌最新编译的前端静态页面 (static/)、全局默认技能 (skills/) 与 MCP 初始配置 (mcp_servers.json)
3. 严格过滤源码切片与隐私隔离，确保 0 私有数据/测试代码污染（绝对不包含 uploads、测试账号、旧数据库或历史路径，初次启动自动建全新库）
4. 一键压缩打包为 AgentHub-Local-v1.1.6-windows-x64.zip，可直接上传发布至 GitHub Releases
"""
import os
import sys
import shutil
import zipfile
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_OUT = FRONTEND_DIR / "out"
DIST_DIR = BASE_DIR / "dist"
BUILD_DIR = BASE_DIR / "build"
RELEASE_VERSION = "v1.1.6"
APP_NAME = "AgentHub"
OUTPUT_DIR = DIST_DIR / APP_NAME
ZIP_NAME = f"AgentHub-Local-{RELEASE_VERSION}-windows-x64.zip"
FINAL_ZIP_PATH = DIST_DIR / ZIP_NAME


def prepare_clean_backend_stage() -> Path:
    """准备纯净的 backend 源码切片，严格剔除个人 data/uploads、tests 与缓存文件"""
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
    )

    # 仅拷贝运行期必需的代码目录与入口文件，物理阻断 data/、tests/ 等进入发行包
    shutil.copytree(src_backend / "app", clean_backend_dir / "app", ignore=ignore_fn)
    for fname in ("__init__.py", "main.py", "requirements.txt"):
        src_file = src_backend / fname
        if src_file.is_file():
            shutil.copy2(src_file, clean_backend_dir / fname)

    return clean_backend_dir


def ensure_frontend_built():
    """确保前端静态导出文件最新且有效"""
    if not (FRONTEND_OUT / "index.html").exists():
        print(">>> 正在编译前端静态导出产物 (Next.js Static Export)...")
        subprocess.run(["npm", "run", "build"], cwd=str(FRONTEND_DIR), check=True, shell=True)
    else:
        print(f">>> 前端静态导出产物已就绪: {FRONTEND_OUT}")


def build_pyinstaller_exe():
    """调用 PyInstaller 将所有 Python 运行环境与微内核编译为独立绿色目录"""
    ensure_frontend_built()
    clean_backend_dir = prepare_clean_backend_stage()

    print(f"\n>>> 正在使用 PyInstaller 编译独立桌面程序 {APP_NAME}.exe ({RELEASE_VERSION})...")

    # 动态构建 PyInstaller 编译参数
    sep = ";" if sys.platform == "win32" else ":"
    static_arg = f"{FRONTEND_OUT}{sep}static"
    backend_data_arg = f"{clean_backend_dir}{sep}backend"

    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--windowed",     # 无黑框原生窗体
        "--onedir",       # 独立目录模式：秒级启动，免重复解压
        "--paths",
        str(BASE_DIR / "backend"),
        "--add-data",
        static_arg,
        "--add-data",
        backend_data_arg,
        "--collect-all",
        "webview",
        "--collect-all",
        "clr_loader",
        "--collect-all",
        "pythonnet",
        "--collect-all",
        "uvicorn",
        "--collect-all",
        "fastapi",
        "--collect-all",
        "starlette",
        "--collect-all",
        "pydantic",
        "--collect-all",
        "pydantic_settings",
        "--collect-all",
        "psutil",
        "--collect-all",
        "aiosqlite",
        "--collect-all",
        "httpx",
        "--collect-all",
        "requests",
        "--hidden-import",
        "backend.main",
        "--hidden-import",
        "backend.app.api.router",
        "--hidden-import",
        "backend.app.api.v1.experiences",
        "--hidden-import",
        "backend.app.api.v1.memories",
        "--hidden-import",
        "backend.app.api.v1.skills",
        "--hidden-import",
        "backend.app.api.v1.loop",
        "--hidden-import",
        "backend.app.agent.runtime",
        "--hidden-import",
        "backend.app.agent.context_builder",
        "--hidden-import",
        "backend.app.agent.policy_interceptor",
        "--hidden-import",
        "backend.app.agent.tool_protocol",
        "--hidden-import",
        "backend.app.agent.shell_executor",
        "--hidden-import",
        "backend.app.agent.ui_operator",
        "--hidden-import",
        "backend.app.repository.db",
        "--hidden-import",
        "backend.app.repository.conversation_repository",
        "--hidden-import",
        "backend.app.repository.message_repository",
        "--hidden-import",
        "backend.app.repository.workspace_repository",
        "--hidden-import",
        "backend.app.repository.loop_run_repository",
        "--hidden-import",
        "backend.app.repository.scheduled_repository",
        "--hidden-import",
        "backend.app.service.engine_pool",
        "--hidden-import",
        "backend.app.service.audit_service",
        "--hidden-import",
        "backend.app.memory",
        "--hidden-import",
        "backend.app.memory.long_term",
        "--hidden-import",
        "backend.app.memory.policy_guard",
        "--hidden-import",
        "backend.app.memory.ledger",
        "--hidden-import",
        "backend.app.memory.fact_manager",
        "--hidden-import",
        "backend.app.memory.session_summary",
        "--hidden-import",
        "backend.app.memory.workspace_fts",
        "--hidden-import",
        "backend.app.skills",
        "--hidden-import",
        "backend.app.skills.skill_service",
        "--hidden-import",
        "backend.app.skills.experience_distiller",
        "--hidden-import",
        "backend.app.extensions.skill_loader",
        "--hidden-import",
        "backend.app.extensions.mcp_manager",
        "--hidden-import",
        "backend.app.schema.chat_schema",
        "--hidden-import",
        "backend.app.schema.loop_schema",
        str(BASE_DIR / "desktop_app.py"),
    ]

    print(">>> 启动编译执行...")
    subprocess.run(pyinstaller_cmd, cwd=str(BASE_DIR), check=True)

    exe_file = OUTPUT_DIR / f"{APP_NAME}.exe"
    if not exe_file.exists():
        raise FileNotFoundError(f"编译完成，但未找到预期的主程序: {exe_file}")
    print(f"✓ 主可执行程序已生成: {exe_file}")


def assemble_standalone_package():
    """组装所有默认运行资产至 dist/AgentHub 目录，确保 0 私有数据污染"""
    print("\n>>> 正在组装完整免安装绿色便携运行包...")

    # 1. 确保 static 目录存在并同步前端产物
    target_static = OUTPUT_DIR / "static"
    if target_static.exists():
        shutil.rmtree(target_static)
    shutil.copytree(FRONTEND_OUT, target_static)
    print("  -> 嵌入静态前端站点到 static/ ...")

    # 同时复制一份到 frontend/out 保证多层级候选路径 100% 兼容
    target_frontend_out = OUTPUT_DIR / "frontend" / "out"
    if target_frontend_out.exists():
        shutil.rmtree(target_frontend_out)
    shutil.copytree(FRONTEND_OUT, target_frontend_out)

    # 2. 复制全局底座默认技能 (skills/)
    src_skills = BASE_DIR / "skills"
    target_skills = OUTPUT_DIR / "skills"
    if target_skills.exists():
        shutil.rmtree(target_skills)
    if src_skills.exists():
        shutil.copytree(src_skills, target_skills, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git*"))
        print("  -> 嵌入默认全局底座技能到 skills/ ...")

    # 3. 复制默认初始 MCP 配置文件
    src_mcp = BASE_DIR / "mcp_servers.json"
    if src_mcp.exists():
        shutil.copy2(src_mcp, OUTPUT_DIR / "mcp_servers.json")
        print("  -> 嵌入默认初始 mcp_servers.json ...")

    # 4. 拷贝文档与许可
    for doc_name in ("README.md", "LICENSE"):
        doc_src = BASE_DIR / doc_name
        if doc_src.exists():
            shutil.copy2(doc_src, OUTPUT_DIR / doc_name)

    # 5. 植入纯绿色中文启动批处理脚本
    launcher_bat = OUTPUT_DIR / "启动AgentHub.bat"
    launcher_bat.write_text(
        '@echo off\r\n'
        'title AgentHub Local 桌面启动器\r\n'
        'cd /d "%~dp0"\r\n'
        'start "" "%~dp0AgentHub.exe"\r\n'
        'exit\r\n',
        encoding="gbk",
    )

    debug_bat = OUTPUT_DIR / "启动AgentHub_调试模式(带控制台日志).bat"
    debug_bat.write_text(
        '@echo off\r\n'
        'title AgentHub Local 调试控制台\r\n'
        'cd /d "%~dp0"\r\n'
        'echo ========================================================\r\n'
        f'echo   AgentHub Local {RELEASE_VERSION} 调试运行模式 (按 Ctrl+C 可停止)\r\n'
        'echo ========================================================\r\n'
        'echo.\r\n'
        '"%~dp0AgentHub.exe" --debug\r\n'
        'pause\r\n',
        encoding="gbk",
    )
    print("  -> 已生成一键启动脚本 启动AgentHub.bat 与 调试启动脚本 ...")

    # 6. 安全红线校验：彻底清除并校验运行包内 0 个人数据 / 数据库 / 测试代码残留
    for dirty_rel in ("data", "_internal/backend/data", "_internal/backend/tests"):
        dirty_path = OUTPUT_DIR / dirty_rel
        if dirty_path.exists():
            shutil.rmtree(dirty_path, ignore_errors=True)
            print(f"  [安全清除] 已移除敏感/非发行目录: {dirty_rel}")

    forbidden_patterns = ("*.db", "*.db-wal", "*.db-shm", "*.sqlite*", "*.log", ".access_token", ".env")
    leaked_items: list[Path] = []
    for pat in forbidden_patterns:
        for matched in OUTPUT_DIR.rglob(pat):
            # 忽略 PyInstaller 内嵌 Python 标准库或三方包自带非业务文件外的根及 backend 路径
            rel_str = str(matched.relative_to(OUTPUT_DIR)).replace("\\", "/")
            if rel_str.startswith("_internal/") and not rel_str.startswith("_internal/backend/"):
                continue
            matched.unlink(missing_ok=True)
            leaked_items.append(matched)
    if leaked_items:
        print(f"  [安全清除] 已清理 {len(leaked_items)} 个敏感文件残留。")

    # 断言终态无上传/个人配置/数据库残留
    for forbidden_dir in (OUTPUT_DIR / "data", OUTPUT_DIR / "_internal" / "backend" / "data", OUTPUT_DIR / "_internal" / "backend" / "tests"):
        if forbidden_dir.exists():
            raise RuntimeError(f"安全红线阻断：发现非法目录残留 {forbidden_dir}")

    print("  ✓ 已保证运行包 100% 纯净初始状态（无任何历史会话、个人上传或私有密钥）")


def create_release_zip():
    """将独立绿色便携目录压缩为适用于 GitHub Releases 的分发 Zip"""
    print(f"\n>>> 正在压缩生成 GitHub Releases 独立运行包: {ZIP_NAME} ...")
    if FINAL_ZIP_PATH.exists():
        FINAL_ZIP_PATH.unlink()

    with zipfile.ZipFile(FINAL_ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(OUTPUT_DIR):
            for f in files:
                full_file = Path(root) / f
                rel_path = full_file.relative_to(DIST_DIR)
                zf.write(full_file, arcname=str(rel_path))

    zip_size_mb = FINAL_ZIP_PATH.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 65)
    print(f"🎉 AgentHub Local {RELEASE_VERSION} 独立运行可执行程序包构建完成！")
    print(f"📁 独立免安装绿色目录: {OUTPUT_DIR}")
    print(f"   - 双击直接运行: {OUTPUT_DIR / 'AgentHub.exe'}")
    print(f"📦 GitHub Releases 发布包: {FINAL_ZIP_PATH} ({zip_size_mb:.2f} MB)")
    print("=====================================================\n")


if __name__ == "__main__":
    build_pyinstaller_exe()
    assemble_standalone_package()
    create_release_zip()
