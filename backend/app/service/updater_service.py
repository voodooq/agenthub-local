"""
应用自动检查更新、流式下载与重启加载服务
基于 GitHub Releases 管理远端版本，支持 Windows 单文件 EXE、macOS DMG 与便携源码包多种分发形态。
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..core.config import load_version_metadata, settings
from ..core.logging_config import logger
from ..core.proxy_helper import get_system_proxy_url
from ..core.runtime_paths import get_writable_app_root

DEFAULT_GITHUB_REPO = "voodooq/agenthub-local"
CHECK_CACHE_TTL_SECONDS = 900.0
DOWNLOAD_CHUNK_SIZE = 65536

# 便携包覆写更新时绝对禁止覆盖的顶层用户目录与敏感文件
PROTECTED_TOP_LEVEL_NAMES = frozenset({
    "data",
    ".env",
    ".venv",
    "venv",
    "node_modules",
    ".git",
    "__pycache__",
})

# 若宿主机已存在则保留用户个人自定义配置的文件（仅在首次不存在时写入）
PROTECTED_IF_EXISTS_FILES = frozenset({
    "mcp_servers.json",
    "profile.md",
})

_SEMVER_RE = re.compile(
    r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$",
    re.IGNORECASE,
)


def get_github_repo() -> str:
    """获取用于检查更新的 GitHub 仓库（owner/repo）。"""
    return os.environ.get("AGENTHUB_GITHUB_REPO", "").strip() or DEFAULT_GITHUB_REPO


def get_release_api_url() -> str:
    """获取 GitHub Releases 最新版本 API 地址，支持环境变量覆盖。"""
    override = os.environ.get("AGENTHUB_UPDATE_API_URL", "").strip()
    if override:
        return override
    return f"https://api.github.com/repos/{get_github_repo()}/releases/latest"


def get_current_version() -> str:
    """获取当前运行中客户端的版本号。"""
    file_ver, _ = load_version_metadata()
    if file_ver and file_ver != "0.0.0":
        return file_ver
    return str(settings.app_version or "0.0.0").strip().lstrip("vV")


def _prerelease_sort_key(prerelease: str) -> tuple[tuple[int, int | str], ...]:
    """将 SemVer 预发布标记拆分为可比较的自然序元组。"""
    if not prerelease:
        return ()
    parts: list[tuple[int, int | str]] = []
    for segment in prerelease.split("."):
        if segment.isdigit():
            parts.append((0, int(segment)))
        else:
            parts.append((1, segment.lower()))
    return tuple(parts)


def parse_semver(version_str: str) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    """解析语义化版本号为可比较元组。

    说明：第 4 位权重中正式版为 1、预发布版为 0，确保 1.4.0 > 1.4.0-beta.1。
    """
    raw = str(version_str or "").strip()
    match = _SEMVER_RE.match(raw)
    if not match:
        nums = [int(x) for x in re.findall(r"\d+", raw)[:3]]
        while len(nums) < 3:
            nums.append(0)
        return (nums[0], nums[1], nums[2], 1, ())

    major = int(match.group(1) or 0)
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    prerelease = (match.group(4) or "").strip()
    is_stable = 0 if prerelease else 1
    return (major, minor, patch, is_stable, _prerelease_sort_key(prerelease))


def is_newer_version(remote_ver: str, current_ver: str) -> bool:
    """判断远端版本号是否严格大于本地版本号。"""
    return parse_semver(remote_ver) > parse_semver(current_ver)


def detect_install_mode() -> str:
    """识别当前客户端的部署/运行形态。

    Returns:
        "windows_exe" | "macos_app" | "portable"
    """
    override = os.environ.get("AGENTHUB_INSTALL_MODE", "").strip().lower()
    if override in ("windows_exe", "macos_app", "portable"):
        return override

    is_frozen = bool(getattr(sys, "frozen", False))
    if is_frozen and sys.platform == "win32":
        return "windows_exe"
    if is_frozen and sys.platform == "darwin":
        return "macos_app"
    return "portable"


def _is_allowed_download_url(url: str) -> bool:
    """校验更新包下载链接协议安全性，仅允许 HTTPS 或本机回环测试地址。"""
    clean = str(url or "").strip()
    if not clean:
        return False
    parsed = urlparse(clean)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme == "https" and bool(parsed.netloc):
        return True
    if scheme == "http" and host in ("127.0.0.1", "localhost", "::1", "testserver"):
        return True
    return False


def _is_checksum_or_signature_file(name_lower: str) -> bool:
    """排除校验和或签名附属文件，避免误选作安装包。"""
    return name_lower.endswith((".sha256", ".sha512", ".md5", ".asc", ".sig"))


def select_release_asset(
    assets: list[dict[str, Any]],
    install_mode: str,
    platform_str: str | None = None,
) -> dict[str, Any] | None:
    """根据当前运行形态与宿主机架构，从 GitHub Release assets 中选择最佳安装包。"""
    if not assets:
        return None

    plat = platform_str or sys.platform
    is_arm = os.uname().machine.lower() in ("arm64", "aarch64") if hasattr(os, "uname") else False
    if plat == "win32":
        arch_hint = "arm64" if "arm" in os.environ.get("PROCESSOR_ARCHITECTURE", "").lower() else "x64"
    else:
        arch_hint = "arm64" if is_arm else "x64"

    scored: list[tuple[int, dict[str, Any]]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        download_url = str(asset.get("browser_download_url") or asset.get("url") or "").strip()
        if not name or not download_url:
            continue
        lower = name.lower()
        if _is_checksum_or_signature_file(lower):
            continue

        score = -1
        if install_mode == "windows_exe":
            if lower.endswith(".exe"):
                score = 100
                if "windows" in lower or "win" in lower:
                    score += 30
                if arch_hint in lower:
                    score += 10
        elif install_mode == "macos_app":
            if lower.endswith(".dmg"):
                score = 100
                if "macos" in lower or "mac" in lower or "darwin" in lower:
                    score += 30
                if arch_hint in lower:
                    score += 10
        else:
            # portable 便携包形态：优先跨平台压缩包，确保可安全热替换源码与前端静态资源
            if plat == "win32":
                if "portable" in lower and lower.endswith(".zip"):
                    score = 120
                elif lower.endswith(".zip"):
                    score = 90
                elif lower.endswith(".tar.gz"):
                    score = 60
                elif lower.endswith(".exe"):
                    score = 40
            else:
                if "portable" in lower and lower.endswith(".tar.gz"):
                    score = 120
                elif "portable" in lower and lower.endswith(".zip"):
                    score = 100
                elif lower.endswith(".tar.gz"):
                    score = 80
                elif lower.endswith(".zip"):
                    score = 70

        if score > 0:
            scored.append((score, asset))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _extract_archive_safely(archive_path: Path, staging_dir: Path) -> Path:
    """安全解压 .zip / .tar.gz 更新包至暂存目录，防范 Zip Slip 路径穿越攻击。"""
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_resolved = staging_dir.resolve()

    lower_name = archive_path.name.lower()
    if lower_name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.infolist():
                member_name = member.filename.replace("\\", "/")
                if not member_name or member_name.endswith("/"):
                    continue
                target_path = (staging_resolved / member_name).resolve()
                if not target_path.is_relative_to(staging_resolved):
                    raise ValueError(f"检测到非法越权路径，已拦截解压: {member.filename}")
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, "r") as src_fp, open(target_path, "wb") as dst_fp:
                    shutil.copyfileobj(src_fp, dst_fp)
    elif lower_name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as tf:
            for member in tf.getmembers():
                if member.issym() or member.islnk() or member.isdev():
                    raise ValueError(f"检测到不支持的特殊链接或设备文件，已拒绝解压: {member.name}")
                if member.isdir():
                    continue
                target_path = (staging_resolved / member.name).resolve()
                if not target_path.is_relative_to(staging_resolved):
                    raise ValueError(f"检测到非法越权路径，已拦截解压: {member.name}")
                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with extracted, open(target_path, "wb") as dst_fp:
                    shutil.copyfileobj(extracted, dst_fp)
    else:
        raise ValueError(f"不支持的便携更新包格式: {archive_path.name}")

    # 兼容顶层包含单一发布文件夹（如 AgentHub-Local-v1.5.0-portable/）的压缩包布局
    entries = list(staging_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return staging_dir


def _apply_portable_archive(archive_path: Path, app_root: Path) -> int:
    """将便携包中的新版本文件安全同步到应用根目录，严格跳过用户数据与运行环境目录。"""
    staging_dir = app_root / "data" / "updates" / f"staging_{int(time.time())}"
    updated_files = 0
    try:
        src_root = _extract_archive_safely(archive_path, staging_dir)
        app_resolved = app_root.resolve()

        for root, dirnames, filenames in os.walk(src_root):
            root_path = Path(root)
            rel_dir = root_path.relative_to(src_root)

            # 在根目录层级过滤掉受保护目录，避免遍历或写入用户数据库/虚拟环境
            if rel_dir == Path("."):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in PROTECTED_TOP_LEVEL_NAMES
                ]

            for filename in filenames:
                if rel_dir == Path("."):
                    if filename in PROTECTED_TOP_LEVEL_NAMES:
                        continue
                    if filename in PROTECTED_IF_EXISTS_FILES and (app_resolved / filename).exists():
                        continue

                src_file = root_path / filename
                dst_file = (app_resolved / rel_dir / filename).resolve()
                if not dst_file.is_relative_to(app_resolved):
                    raise ValueError(f"拒绝写入应用根目录之外的路径: {dst_file}")
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                updated_files += 1
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return updated_files


def build_windows_exe_update_script(
    source_exe: Path,
    target_exe: Path,
    wait_pids: list[int],
    log_file: Path | None = None,
) -> str:
    """生成 Windows 单文件 EXE 脱离进程更新并自动重启的 PowerShell 脚本内容。

    由于 Windows 与 PyInstaller --onefile Bootloader 会锁定正在运行的可执行文件句柄，
    必须等待所有关联进程彻底退出后，先移走旧文件再落位新文件并重新拉起。
    """
    unique_pids = [str(pid) for pid in dict.fromkeys(wait_pids) if isinstance(pid, int) and pid > 0]
    pids_literal = ", ".join(unique_pids) if unique_pids else "0"
    src_str = str(source_exe.resolve()).replace("'", "''")
    dst_str = str(target_exe.resolve()).replace("'", "''")
    bak_str = f"{dst_str}.old"
    work_dir = str(target_exe.resolve().parent).replace("'", "''")
    log_path = log_file if log_file is not None else (source_exe.resolve().parent / "apply_update.log")
    log_str = str(log_path).replace("'", "''")

    return f"""$ErrorActionPreference = 'Continue'
$SourceExe = '{src_str}'
$TargetExe = '{dst_str}'
$BackupExe = '{bak_str}'
$WorkDir = '{work_dir}'
$LogFile = '{log_str}'
$WaitPids = @({pids_literal})

function Write-UpdateLog($msg) {{
    try {{
        $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -LiteralPath $LogFile -Value "[$ts] $msg" -Encoding UTF8
    }} catch {{}}
}}

Write-UpdateLog "Update script started. Source=$SourceExe Target=$TargetExe"

foreach ($pidVal in $WaitPids) {{
    if ($pidVal -le 0) {{ continue }}
    for ($i = 0; $i -lt 60; $i++) {{
        $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
        if (-not $proc) {{ break }}
        Start-Sleep -Milliseconds 300
    }}
}}

# 等待 PyInstaller --onefile 外层 Bootloader 及所有占用该路径的进程彻底退出
for ($i = 0; $i -lt 60; $i++) {{
    $holders = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq $TargetExe }})
    if ($holders.Count -eq 0) {{ break }}
    Write-UpdateLog "Waiting for $($holders.Count) process(es) holding TargetExe..."
    Start-Sleep -Milliseconds 400
}}

$replaced = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {{
    try {{
        if (-not (Test-Path -LiteralPath $SourceExe)) {{
            Write-UpdateLog "ERROR: SourceExe does not exist."
            break
        }}
        if (Test-Path -LiteralPath $TargetExe) {{
            if (Test-Path -LiteralPath $BackupExe) {{
                Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue
            }}
            Move-Item -LiteralPath $TargetExe -Destination $BackupExe -Force -ErrorAction Stop
        }}
        Move-Item -LiteralPath $SourceExe -Destination $TargetExe -Force -ErrorAction Stop
        if (Test-Path -LiteralPath $BackupExe) {{
            Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue
        }}
        $replaced = $true
        Write-UpdateLog "Replaced TargetExe successfully on attempt $attempt."
        break
    }} catch {{
        Write-UpdateLog "Attempt $attempt failed: $($_.Exception.Message)"
        if ((-not (Test-Path -LiteralPath $TargetExe)) -and (Test-Path -LiteralPath $BackupExe)) {{
            try {{ Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction SilentlyContinue }} catch {{}}
        }}
        Start-Sleep -Milliseconds 500
    }}
}}

if ($replaced -and (Test-Path -LiteralPath $TargetExe)) {{
    Write-UpdateLog "Launching updated executable: $TargetExe"
    # 清理 PyInstaller 6.x (_PYI_*) 与旧进程父级环境变量，强制新 EXE 独立解压运行
    Get-ChildItem Env: | Where-Object {{
        $_.Name -like '_PYI_*' -or $_.Name -in @('_MEIPASS2', 'DESKTOP_PARENT_PID', 'TAURI_PARENT_PID', 'PYTHONHOME', 'PYTHONPATH')
    }} | ForEach-Object {{
        Remove-Item "Env:\\$($_.Name)" -ErrorAction SilentlyContinue
    }}
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath $TargetExe -WorkingDirectory $WorkDir -WindowStyle Normal
}} else {{
    Write-UpdateLog "Update failed: TargetExe was not replaced."
}}

Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""


def build_portable_restart_script(
    python_exe: str,
    launch_args: list[str],
    work_dir: Path,
    wait_pid: int,
) -> str:
    """生成跨平台便携源码模式进程重启辅助 Python 代码片段。"""
    cmd_list = [python_exe, *launch_args]
    return f"""import os, subprocess, sys, time
pid = {int(wait_pid)}
cmd = {cmd_list!r}
cwd = {str(work_dir.resolve())!r}

# 轮询等待旧进程完全退出并释放端口
for _ in range(40):
    try:
        if sys.platform == 'win32':
            import ctypes
            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if not handle:
                break
            ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(pid, 0)
    except OSError:
        break
    time.sleep(0.25)

time.sleep(0.3)
env = os.environ.copy()
# 清除旧窗口的父进程心跳标识，防止新进程误触发 watch_parent_process 自毁
env.pop('DESKTOP_PARENT_PID', None)
env.pop('TAURI_PARENT_PID', None)

kwargs = {{'cwd': cwd, 'env': env}}
if sys.platform == 'win32':
    kwargs['creationflags'] = (
        getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
        | getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    )
    kwargs['close_fds'] = True
else:
    kwargs['start_new_session'] = True

subprocess.Popen(cmd, **kwargs)
try:
    os.remove(__file__)
except OSError:
    pass
"""


def _resolve_portable_restart_args(app_root: Path) -> list[str]:
    """推断便携模式下重启应当使用的入口脚本及参数。"""
    main_mod = sys.modules.get("__main__")
    main_file = getattr(main_mod, "__file__", "") or ""
    if "desktop_app.py" in main_file or "DESKTOP_PARENT_PID" in os.environ:
        desktop_script = app_root / "desktop_app.py"
        if desktop_script.is_file():
            return [str(desktop_script)]

    if main_file and "pytest" not in main_file and "unittest" not in main_file:
        if Path(main_file).name == "main.py":
            return ["-m", "uvicorn", "backend.main:app", "--host", settings.host, "--port", str(settings.port)]
        if Path(main_file).is_file():
            return [str(Path(main_file).resolve()), *sys.argv[1:]]

    desktop_script = app_root / "desktop_app.py"
    if desktop_script.is_file() and sys.platform == "win32":
        return [str(desktop_script)]
    return ["-m", "uvicorn", "backend.main:app", "--host", settings.host, "--port", str(settings.port)]


class AutoUpdaterService:
    """GitHub Releases 自动版本检查、流式下载与重启加载管理器。"""

    def __init__(self) -> None:
        self._download_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._state: dict[str, Any] = self._initial_state()

    @staticmethod
    def _initial_state() -> dict[str, Any]:
        return {
            "status": "idle",
            "has_update": False,
            "current_version": get_current_version(),
            "latest_version": get_current_version(),
            "release_name": "",
            "release_notes": "",
            "published_at": "",
            "html_url": f"https://github.com/{get_github_repo()}/releases",
            "install_mode": detect_install_mode(),
            "asset_name": "",
            "download_url": "",
            "total_bytes": 0,
            "downloaded_bytes": 0,
            "progress_percent": 0.0,
            "speed_bps": 0.0,
            "downloaded_path": "",
            "error_message": "",
            "checked_at": 0.0,
        }

    def reset_for_test(self) -> None:
        """重置内存状态，仅供自动化单元测试清理上下文使用。"""
        self._download_task = None
        self._state = self._initial_state()

    def get_updates_dir(self) -> Path:
        """获取更新包临时存放目录 `<writable_root>/data/updates`。"""
        updates_dir = get_writable_app_root() / "data" / "updates"
        updates_dir.mkdir(parents=True, exist_ok=True)
        return updates_dir

    def get_status(self) -> dict[str, Any]:
        """返回当前更新状态快照（实时刷新版本号与运行形态）。"""
        snapshot = dict(self._state)
        snapshot["current_version"] = get_current_version()
        snapshot["install_mode"] = detect_install_mode()
        return snapshot

    @staticmethod
    def _build_github_headers() -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"AgentHub-Local/{get_current_version()}",
        }
        token = (os.environ.get("AGENTHUB_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def check_for_updates(self, force: bool = False) -> dict[str, Any]:
        """向 GitHub Releases 检查最新版本及升级日志。"""
        current_status = self._state.get("status")
        if current_status in ("downloading", "applying"):
            return self.get_status()

        now = time.time()
        checked_at = float(self._state.get("checked_at") or 0.0)
        if (
            not force
            and checked_at > 0
            and (now - checked_at) < CHECK_CACHE_TTL_SECONDS
            and current_status in ("available", "up_to_date", "downloaded")
        ):
            return self.get_status()

        async with self._lock:
            # 双重检查锁，避免并发检查重复打穿 GitHub API
            if not force and self._state.get("checked_at", 0.0) > 0:
                if (time.time() - float(self._state["checked_at"])) < CHECK_CACHE_TTL_SECONDS:
                    if self._state.get("status") in ("available", "up_to_date", "downloaded"):
                        return self.get_status()

            current_ver = get_current_version()
            install_mode = detect_install_mode()
            self._state["status"] = "checking"
            self._state["current_version"] = current_ver
            self._state["install_mode"] = install_mode
            self._state["error_message"] = ""

            api_url = get_release_api_url()
            proxy_url = get_system_proxy_url()

            try:
                async with httpx.AsyncClient(
                    proxy=proxy_url,
                    follow_redirects=True,
                    timeout=httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0),
                ) as client:
                    resp = await client.get(api_url, headers=self._build_github_headers())

                if resp.status_code == 404:
                    raise RuntimeError("远端仓库尚无已发布的正式 Release 版本")
                if resp.status_code >= 400:
                    raise RuntimeError(f"GitHub 版本接口返回状态码 {resp.status_code}")

                payload = resp.json()
                if not isinstance(payload, dict):
                    raise ValueError("GitHub 版本接口响应结构无效")

                tag_name = str(payload.get("tag_name") or payload.get("name") or "").strip()
                latest_ver = tag_name.lstrip("vV") or current_ver
                release_name = str(payload.get("name") or tag_name or f"v{latest_ver}").strip()
                release_notes = str(payload.get("body") or "").strip()
                published_at = str(payload.get("published_at") or "").strip()
                html_url = str(payload.get("html_url") or f"https://github.com/{get_github_repo()}/releases").strip()
                assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []

                has_update = is_newer_version(latest_ver, current_ver)
                matched_asset = select_release_asset(assets, install_mode) if has_update else None

                asset_name = str(matched_asset.get("name") or "").strip() if matched_asset else ""
                download_url = (
                    str(matched_asset.get("browser_download_url") or matched_asset.get("url") or "").strip()
                    if matched_asset
                    else ""
                )
                asset_size = int(matched_asset.get("size") or 0) if matched_asset else 0

                self._state.update({
                    "has_update": has_update,
                    "current_version": current_ver,
                    "latest_version": latest_ver,
                    "release_name": release_name,
                    "release_notes": release_notes,
                    "published_at": published_at,
                    "html_url": html_url,
                    "install_mode": install_mode,
                    "asset_name": asset_name,
                    "download_url": download_url,
                    "total_bytes": asset_size,
                    "downloaded_bytes": 0,
                    "progress_percent": 0.0,
                    "speed_bps": 0.0,
                    "downloaded_path": "",
                    "error_message": "",
                    "checked_at": time.time(),
                })

                if not has_update:
                    self._state["status"] = "up_to_date"
                    return self.get_status()

                if not matched_asset or not _is_allowed_download_url(download_url):
                    self._state["status"] = "error"
                    self._state["error_message"] = (
                        f"检测到新版本 v{latest_ver}，但未找到适配当前平台 ({install_mode}) 的安全安装包。"
                    )
                    return self.get_status()

                # 检查本地更新缓存目录是否已存在同字节大小的完整更新包
                safe_asset_name = Path(asset_name).name
                cached_file = self.get_updates_dir() / safe_asset_name
                if cached_file.is_file() and (asset_size <= 0 or cached_file.stat().st_size == asset_size):
                    actual_size = cached_file.stat().st_size
                    self._state["status"] = "downloaded"
                    self._state["downloaded_bytes"] = actual_size
                    self._state["total_bytes"] = asset_size or actual_size
                    self._state["progress_percent"] = 100.0
                    self._state["downloaded_path"] = str(cached_file)
                else:
                    self._state["status"] = "available"

                return self.get_status()

            except Exception as exc:
                logger.warning(f"[AutoUpdater] 检查 GitHub 更新失败: {exc}")
                self._state["status"] = "error"
                self._state["checked_at"] = time.time()
                self._state["error_message"] = f"检查更新失败: {exc}"
                return self.get_status()

    async def start_download(self, force: bool = False) -> dict[str, Any]:
        """触发异步流式下载新版本客户端，前端可通过 get_status 轮询百分比进度。"""
        if self._download_task and not self._download_task.done():
            return self.get_status()

        if not self._state.get("download_url") or not self._state.get("has_update"):
            await self.check_for_updates(force=True)

        if not self._state.get("has_update"):
            return self.get_status()

        download_url = str(self._state.get("download_url") or "").strip()
        asset_name = Path(str(self._state.get("asset_name") or "")).name
        expected_size = int(self._state.get("total_bytes") or 0)

        if not asset_name or not _is_allowed_download_url(download_url):
            self._state["status"] = "error"
            self._state["error_message"] = "更新包下载地址无效或不安全，请前往 GitHub Releases 手动下载"
            return self.get_status()

        final_path = self.get_updates_dir() / asset_name
        if not force and final_path.is_file() and expected_size > 0 and final_path.stat().st_size == expected_size:
            self._state.update({
                "status": "downloaded",
                "downloaded_bytes": expected_size,
                "total_bytes": expected_size,
                "progress_percent": 100.0,
                "speed_bps": 0.0,
                "downloaded_path": str(final_path),
                "error_message": "",
            })
            return self.get_status()

        self._state.update({
            "status": "downloading",
            "downloaded_bytes": 0,
            "progress_percent": 0.0,
            "speed_bps": 0.0,
            "downloaded_path": "",
            "error_message": "",
        })
        self._download_task = asyncio.create_task(
            self._download_worker(download_url=download_url, asset_name=asset_name, expected_size=expected_size)
        )
        return self.get_status()

    async def _download_worker(self, download_url: str, asset_name: str, expected_size: int) -> None:
        """后台流式下载任务：边读取字节块边落盘并实时刷新进度与速率。"""
        updates_dir = self.get_updates_dir()
        final_path = updates_dir / asset_name
        part_path = updates_dir / f"{asset_name}.part"
        proxy_url = get_system_proxy_url()
        start_ts = time.monotonic()

        try:
            part_path.unlink(missing_ok=True)
            async with httpx.AsyncClient(
                proxy=proxy_url,
                follow_redirects=True,
                timeout=httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=15.0),
            ) as client:
                async with client.stream("GET", download_url, headers={"User-Agent": f"AgentHub-Local/{get_current_version()}"}) as resp:
                    if resp.status_code >= 400:
                        raise RuntimeError(f"下载客户端失败，HTTP 状态码: {resp.status_code}")

                    content_len_raw = resp.headers.get("content-length")
                    header_size = int(content_len_raw) if (content_len_raw and content_len_raw.isdigit()) else 0
                    total_bytes = header_size or expected_size
                    if total_bytes > 0:
                        self._state["total_bytes"] = total_bytes

                    downloaded_bytes = 0
                    with open(part_path, "wb") as fp:
                        async for chunk in resp.aiter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
                            if not chunk:
                                continue
                            fp.write(chunk)
                            downloaded_bytes += len(chunk)
                            elapsed = max(0.05, time.monotonic() - start_ts)
                            speed_bps = round(downloaded_bytes / elapsed, 1)

                            if total_bytes > 0:
                                pct = round(min(99.9, downloaded_bytes * 100.0 / total_bytes), 1)
                            else:
                                pct = 0.0

                            self._state["downloaded_bytes"] = downloaded_bytes
                            self._state["progress_percent"] = pct
                            self._state["speed_bps"] = speed_bps

            if total_bytes > 0 and downloaded_bytes != total_bytes:
                raise RuntimeError(f"下载文件大小不完整（期望 {total_bytes} 字节，实际 {downloaded_bytes} 字节）")

            part_path.replace(final_path)
            self._state.update({
                "status": "downloaded",
                "total_bytes": total_bytes or downloaded_bytes,
                "downloaded_bytes": downloaded_bytes,
                "progress_percent": 100.0,
                "speed_bps": 0.0,
                "downloaded_path": str(final_path),
                "error_message": "",
            })
            logger.info(f"[AutoUpdater] 新版本客户端下载完成: {final_path} ({downloaded_bytes} bytes)")

        except Exception as exc:
            part_path.unlink(missing_ok=True)
            logger.warning(f"[AutoUpdater] 下载更新包失败: {exc}")
            self._state.update({
                "status": "error",
                "speed_bps": 0.0,
                "error_message": f"下载失败: {exc}",
            })

    @staticmethod
    async def _delayed_exit(delay_seconds: float = 0.6) -> None:
        """延迟短暂时间确保 HTTP 响应已刷回前端后，再终止当前旧进程树以完成覆盖重启。"""
        await asyncio.sleep(delay_seconds)
        logger.info("[AutoUpdater] 旧版本进程正在退出，移交更新重启脚本加载新版本...")
        os._exit(0)

    @staticmethod
    def _spawn_detached_command(cmd: list[str], cwd: Path) -> None:
        """跨平台启动完全脱离当前进程生命期的子进程，并剥离父进程与 PyInstaller 临时环境变量。"""
        clean_env = os.environ.copy()
        for env_key in list(clean_env.keys()):
            if env_key.startswith("_PYI_") or env_key in (
                "DESKTOP_PARENT_PID",
                "TAURI_PARENT_PID",
                "_MEIPASS2",
                "PYTHONHOME",
                "PYTHONPATH",
            ):
                clean_env.pop(env_key, None)
        clean_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": clean_env,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            # 注意：Windows 下 DETACHED_PROCESS 与 CREATE_NO_WINDOW 互斥，同时传入会导致子进程静默启动失败
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
            kwargs["close_fds"] = True
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)

    async def apply_update_and_restart(self, trigger_exit: bool = True) -> dict[str, Any]:
        """校验更新包已就绪后执行版本替换并触发客户端重启重新加载。"""
        downloaded_path_str = str(self._state.get("downloaded_path") or "").strip()
        if self._state.get("status") != "downloaded" or not downloaded_path_str:
            raise ValueError("新版本尚未下载完成，无法执行重启升级")

        downloaded_path = Path(downloaded_path_str)
        if not downloaded_path.is_file():
            raise ValueError("已下载的更新安装包不存在，请重新下载")

        install_mode = detect_install_mode()
        self._state["status"] = "applying"
        self._state["install_mode"] = install_mode
        app_root = get_writable_app_root()

        if install_mode == "windows_exe":
            target_exe = Path(sys.executable).resolve()
            wait_pids = [os.getpid()]
            parent_pid_raw = os.environ.get("DESKTOP_PARENT_PID", "").strip()
            if parent_pid_raw.isdigit():
                wait_pids.append(int(parent_pid_raw))

            script_path = self.get_updates_dir() / "apply_update.ps1"
            script_content = build_windows_exe_update_script(
                source_exe=downloaded_path,
                target_exe=target_exe,
                wait_pids=wait_pids,
            )
            script_path.write_text(script_content, encoding="utf-8-sig")

            if trigger_exit:
                self._spawn_detached_command(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-WindowStyle",
                        "Hidden",
                        "-File",
                        str(script_path),
                    ],
                    cwd=target_exe.parent,
                )
                asyncio.create_task(self._delayed_exit(0.6))

            return {
                "status": "restarting",
                "install_mode": install_mode,
                "target_version": self._state.get("latest_version"),
                "script_path": str(script_path),
                "message": "正在应用 Windows 新版客户端并自动重启，请稍候...",
            }

        if install_mode == "macos_app":
            if trigger_exit and sys.platform == "darwin":
                subprocess.Popen(["open", str(downloaded_path)])
            return {
                "status": "manual_action_required",
                "install_mode": install_mode,
                "target_version": self._state.get("latest_version"),
                "downloaded_path": str(downloaded_path),
                "message": "已下载并为您打开 macOS 安装镜像 (.dmg)，请拖入 Applications 完成替换后重新启动。",
            }

        # portable 模式：先在当前目录安全解压替换代码与前端静态资源，再重启主程序进程
        updated_files = await asyncio.to_thread(_apply_portable_archive, downloaded_path, app_root)
        restart_args = _resolve_portable_restart_args(app_root)
        restart_script_path = self.get_updates_dir() / "restart_portable.py"
        restart_script = build_portable_restart_script(
            python_exe=sys.executable,
            launch_args=restart_args,
            work_dir=app_root,
            wait_pid=os.getpid(),
        )
        restart_script_path.write_text(restart_script, encoding="utf-8")

        if trigger_exit:
            self._spawn_detached_command([sys.executable, str(restart_script_path)], cwd=app_root)
            asyncio.create_task(self._delayed_exit(0.6))

        return {
            "status": "restarting",
            "install_mode": install_mode,
            "target_version": self._state.get("latest_version"),
            "updated_files": updated_files,
            "script_path": str(restart_script_path),
            "message": f"已完成 {updated_files} 个核心文件升级，客户端正在重启并加载新版本...",
        }


auto_updater = AutoUpdaterService()
