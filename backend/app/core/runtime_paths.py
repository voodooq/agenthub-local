"""
跨平台运行时路径解析器与单文件冻结资源中心
解决 PyInstaller (--onefile / --onedir)、macOS .app Bundle、Docker 容器及源码开发模式下的：
1. 只读内嵌资源根目录 (Bundle Root / sys._MEIPASS) 定位
2. 用户可写持久化根目录 (data/、skills/、mcp_servers.json) 智能抉择与无权限兜底
3. 内嵌默认 Skill 与配置首次启动增量释放（绝不覆盖用户历史自定义）
4. 冻结态下外部 Python 解释器解析（防止误用 GUI 主程序自启弹窗）
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

# NOTE: 底层基础路径模块禁止导入 logging_config/config，防止循环导入
logger = logging.getLogger("agenthub")


_SOURCE_REPO_ROOT = Path(__file__).resolve().parents[3]


def is_directory_writable(directory: Path) -> bool:
    """通过实际创建并立即删除探针文件，可靠校验目录在当前用户权限下是否真实可写。"""
    try:
        if not directory.exists() or not directory.is_dir():
            return False
        probe_path = directory / f".agenthub_write_probe_{uuid.uuid4().hex}.tmp"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def get_bundle_root() -> Path:
    """获取当前运行的只读应用内嵌资源根目录（static/、version.json、内置种子 skills/ 等）。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass).resolve()

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        # macOS PyInstaller .app Bundle 结构兼容 Contents/Resources
        if sys.platform == "darwin" and exe_dir.name == "MacOS":
            resources_dir = exe_dir.parent / "Resources"
            if resources_dir.is_dir():
                return resources_dir.resolve()
        return exe_dir

    return _SOURCE_REPO_ROOT


def get_fallback_user_data_dir(
    platform: str | None = None,
    home_dir: Path | None = None,
    local_app_data: str | None = None,
    xdg_data_home: str | None = None,
) -> Path:
    """按操作系统惯例获取标准用户可写应用数据目录。"""
    plat = platform or sys.platform
    home = home_dir or Path.home()

    if plat == "win32":
        base = Path(local_app_data) if local_app_data else Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
        return base / "AgentHubLocal"

    if plat == "darwin":
        return home / "Library" / "Application Support" / "AgentHubLocal"

    xdg_base = xdg_data_home or os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_base) if xdg_base else (home / ".local" / "share")
    return base / "AgentHubLocal"


def get_writable_app_root(
    platform: str | None = None,
    frozen: bool | None = None,
    executable_path: Path | None = None,
    env_override: str | None = None,
    docker_app_dir: Path | None = None,
) -> Path:
    """计算用于持久化 data/、skills/ 与 mcp_servers.json 的可写根目录。

    规则：
    1. 优先响应 AGENTHUB_DATA_ROOT 环境变量；
    2. Docker 容器环境下使用 /app；
    3. 源码开发或测试环境下返回仓库根目录；
    4. macOS 冻结 .app 统一落入 ~/Library/Application Support/AgentHubLocal（规避 AppTranslocation 只读沙盒与升级覆盖丢失）；
    5. Windows / Linux 单文件冻结态优先采用同级便携模式（executable 同级目录）；若目录只读无写权限（如 Program Files），安全回退至标准用户数据目录。
    """
    override = env_override if env_override is not None else os.environ.get("AGENTHUB_DATA_ROOT", "").strip()
    if override:
        override_path = Path(override).expanduser().resolve()
        override_path.mkdir(parents=True, exist_ok=True)
        return override_path

    plat = platform or sys.platform
    app_marker = docker_app_dir if docker_app_dir is not None else Path("/app")
    if (docker_app_dir is not None or plat != "win32") and ((app_marker / "data").exists() or app_marker.exists()):
        return app_marker

    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not is_frozen:
        return _SOURCE_REPO_ROOT

    if plat == "darwin":
        mac_root = get_fallback_user_data_dir(platform="darwin")
        mac_root.mkdir(parents=True, exist_ok=True)
        return mac_root

    exe_dir = (executable_path or Path(sys.executable)).resolve().parent
    if is_directory_writable(exe_dir):
        return exe_dir

    fallback_dir = get_fallback_user_data_dir(platform=plat)
    fallback_dir.mkdir(parents=True, exist_ok=True)
    return fallback_dir


def seed_builtin_skills(bundle_root: Path, target_skills_dir: Path) -> list[str]:
    """首次运行单文件/安装版时，将只读 Bundle 内置的默认 Skill 增量复制到可写目录（不覆盖已有目录）。"""
    copied: list[str] = []
    src_skills_dir = bundle_root / "skills"
    if not src_skills_dir.is_dir():
        return copied

    try:
        if src_skills_dir.resolve() == target_skills_dir.resolve():
            return copied
    except OSError:
        pass

    try:
        target_skills_dir.mkdir(parents=True, exist_ok=True)
        for item in src_skills_dir.iterdir():
            if not item.is_dir():
                continue
            target_item = target_skills_dir / item.name
            # 若用户目录尚无该技能或目录为空且 Bundle 有文件，则补充释放
            needs_seed = not target_item.exists() or not any(target_item.iterdir())
            if needs_seed:
                shutil.copytree(
                    item,
                    target_item,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git*"),
                )
                copied.append(item.name)
    except Exception as exc:
        logger.debug(f"补充复制 Bundle 内置技能跳过: {exc}")

    return copied


def seed_mcp_config(bundle_root: Path, target_cfg_path: Path) -> bool:
    """首次运行冻结包时，若目标无 mcp_servers.json 且只读 Bundle 内置有模板，则安全拷出。"""
    src_cfg = bundle_root / "mcp_servers.json"
    if not src_cfg.is_file() or target_cfg_path.exists():
        return False

    try:
        if src_cfg.resolve() == target_cfg_path.resolve():
            return False
        target_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_cfg, target_cfg_path)
        return True
    except Exception as exc:
        logger.debug(f"复制默认 mcp_servers.json 跳过: {exc}")
        return False


def resolve_python_interpreter() -> str | None:
    """解析用于运行子脚本的 Python 解释器路径。

    在单文件/桌面冻结态下绝不返回 GUI 主程序自身（如 AgentHub.exe），避免误触发二次拉起桌面窗体。
    """
    if not getattr(sys, "frozen", False):
        return sys.executable

    self_exe = Path(sys.executable).resolve()
    candidates = ("python3", "python") if sys.platform != "win32" else ("python", "python3")
    for cmd_name in candidates:
        found = shutil.which(cmd_name)
        if not found:
            continue
        found_path = Path(found).resolve()
        # 屏蔽 WindowsApps 空壳应用商店存根与程序自身
        if "windowsapps" in str(found_path).lower():
            continue
        if found_path == self_exe:
            continue
        return str(found_path)

    return None


def get_silent_subprocess_kwargs(
    allow_gui: bool = False,
    platform: str | None = None,
) -> dict[str, Any]:
    """生成跨平台静默子进程参数字典，阻止 Windows GUI 宿主派生后台子进程时弹出终端黑框。

    Args:
        allow_gui: 若为 True，仅禁止创建控制台窗口而不附带 SW_HIDE，保证 WinForms 等原生弹窗仍正常显示。
        platform: 可选平台标识，默认取 sys.platform。

    Returns:
        dict[str, Any]: 可直接解包传给 subprocess.run / subprocess.Popen / create_subprocess_exec 的参数字典。
    """
    plat = platform or sys.platform
    if plat != "win32":
        return {}

    # NOTE: PyInstaller --windowed 单体 EXE 主进程无控制台宿主，Windows 11 默认终端(Windows Terminal)
    # 会在任意子进程启动控制台程序(powershell/cmd/python/cli)时自动弹窗，CREATE_NO_WINDOW 可在内核层阻止分配控制台。
    kwargs: dict[str, Any] = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    }
    if not allow_gui:
        startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
        if startupinfo_cls is not None:
            startupinfo = startupinfo_cls()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
            kwargs["startupinfo"] = startupinfo
    return kwargs
