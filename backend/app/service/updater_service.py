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
from ..core.runtime_paths import get_writable_app_root, is_directory_writable

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

# 匹配 AgentHub-Local 历史可执行文件名中的版本号片段（如 -v1.4.1 / -v1.5.0-beta.1），保留后续系统与架构后缀
_VERSIONED_WIN_EXE_RE = re.compile(
    r"-v?\d+\.\d+\.\d+(?:-(?!windows\b)[0-9A-Za-z.]+)*",
    re.IGNORECASE,
)


def normalize_windows_target_exe(target_exe: Path) -> Path:
    """将带有历史版本号的 Windows EXE 路径归一化为无版本号文件名。"""
    if not target_exe.name.lower().startswith("agenthub-local"):
        return target_exe
    clean_name = _VERSIONED_WIN_EXE_RE.sub("", target_exe.name, count=1)
    if not clean_name or clean_name == target_exe.name:
        return target_exe
    return target_exe.with_name(clean_name)


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


def is_running_in_docker(
    dockerenv_path: Path | None = None,
    cgroup_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """判断当前进程是否运行在 Docker / 容器环境中。"""
    environ = env if env is not None else os.environ
    flag = str(environ.get("AGENTHUB_DOCKER") or "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True

    marker = dockerenv_path if dockerenv_path is not None else Path("/.dockerenv")
    if marker.exists():
        return True

    cg_file = cgroup_path if cgroup_path is not None else Path("/proc/1/cgroup")
    if cg_file.is_file():
        try:
            content = cg_file.read_text(encoding="utf-8", errors="ignore").lower()
            if any(token in content for token in ("docker", "containerd", "kubepods", "lxc")):
                return True
        except OSError:
            pass

    return False


def _is_mount_readonly(target_path: Path, mounts_path: Path | None = None) -> bool:
    """检查 Linux /proc/mounts 中匹配目标路径的最长挂载点是否带有只读 (ro) 标志。"""
    proc_mounts = mounts_path if mounts_path is not None else Path("/proc/mounts")
    if not proc_mounts.is_file():
        return False

    try:
        resolved_target = str(target_path.resolve())
    except OSError:
        resolved_target = str(target_path)

    best_mount = ""
    best_options: list[str] = []
    try:
        for line in proc_mounts.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            mount_point = parts[1].replace("\\040", " ")
            options = parts[3].split(",")
            is_match = (
                resolved_target == mount_point
                or mount_point == "/"
                or resolved_target.startswith(mount_point.rstrip("/") + "/")
            )
            if is_match and len(mount_point) >= len(best_mount):
                best_mount = mount_point
                best_options = options
    except OSError:
        return False

    return "ro" in best_options


def _is_file_writable(file_path: Path) -> bool:
    """验证已存在文件是否可在当前权限下原地覆写（兼容 Docker 单文件 bind mount）。"""
    try:
        with open(file_path, "r+b"):
            return True
    except OSError:
        return False


def detect_docker_update_capability(
    app_root: Path | None = None,
    mounts_path: Path | None = None,
) -> tuple[bool, str]:
    """检测 Docker 容器内是否具备原地热替换源码、前端静态资源与 version.json 的写权限。

    Returns:
        (can_auto_update, blocked_reason)
    """
    root = (app_root or get_writable_app_root()).resolve()
    readonly_targets: list[str] = []

    if _is_mount_readonly(root, mounts_path) or not is_directory_writable(root):
        readonly_targets.append(str(root))

    static_dir = root / "static"
    if static_dir.exists():
        if _is_mount_readonly(static_dir, mounts_path) or not is_directory_writable(static_dir):
            readonly_targets.append(str(static_dir))

    version_file = root / "version.json"
    if version_file.exists():
        if _is_mount_readonly(version_file, mounts_path) or not _is_file_writable(version_file):
            readonly_targets.append(str(version_file))

    if readonly_targets:
        joined = "、".join(readonly_targets)
        reason = (
            f"检测到 Docker 容器内路径为只读挂载或无写权限（{joined}），无法在容器内直接热更新。"
            "请在 docker-compose.yml 中移除对应挂载的 :ro 后重启容器，"
            "或在宿主机拉取最新代码并执行 docker compose up -d --build 完成升级。"
        )
        return False, reason

    return True, ""


def detect_install_mode() -> str:
    """识别当前客户端的部署/运行形态。

    Returns:
        "windows_exe" | "macos_app" | "docker" | "portable"
    """
    override = os.environ.get("AGENTHUB_INSTALL_MODE", "").strip().lower()
    if override in ("windows_exe", "macos_app", "docker", "portable"):
        return override

    is_frozen = bool(getattr(sys, "frozen", False))
    if is_frozen and sys.platform == "win32":
        return "windows_exe"
    if is_frozen and sys.platform == "darwin":
        return "macos_app"
    if is_running_in_docker():
        return "docker"
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
    prefer_hotpatch: bool = True,
) -> dict[str, Any] | None:
    """根据当前运行形态与宿主机架构，从 GitHub Release assets 中选择最佳安装包。

    NOTE: 在 windows_exe / macos_app 模式下，优先选择 ~4MB 的跨平台轻量更新包 (*-portable.zip)
    原地热更新至 app_override/，仅当不存在轻量更新包或显式禁用时回退完整安装包 (.exe / .dmg)。
    """
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
            if prefer_hotpatch and "portable" in lower and lower.endswith(".zip"):
                score = 150
            elif prefer_hotpatch and "portable" in lower and lower.endswith(".tar.gz"):
                score = 145
            elif lower.endswith(".exe"):
                score = 100
                if "windows" in lower or "win" in lower:
                    score += 30
                if arch_hint in lower:
                    score += 10
        elif install_mode == "macos_app":
            if prefer_hotpatch and "portable" in lower and lower.endswith(".zip"):
                score = 150
            elif prefer_hotpatch and "portable" in lower and lower.endswith(".tar.gz"):
                score = 145
            elif lower.endswith(".dmg"):
                score = 100
                if "macos" in lower or "mac" in lower or "darwin" in lower:
                    score += 30
                if arch_hint in lower:
                    score += 10
        else:
            # docker / portable 形态：优先跨平台压缩包，确保可安全热替换源码与前端静态资源
            if plat == "win32" and install_mode != "docker":
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


def _restore_file_permissions(target_path: Path, archived_mode: int) -> None:
    """在 POSIX 系统上保留归档中的可执行权限，并对 .sh 脚本兜底赋予执行位。"""
    if sys.platform == "win32":
        return
    mode = archived_mode & 0o777
    target_mode: int = 0
    if target_path.suffix.lower() == ".sh":
        target_mode = (mode | 0o755) if mode else 0o755
    elif mode & 0o111:
        target_mode = mode | 0o600
    else:
        return
    try:
        os.chmod(target_path, target_mode)
    except OSError:
        pass


def _extract_archive_safely(archive_path: Path, staging_dir: Path) -> Path:
    """安全解压 .zip / .tar.gz 更新包至暂存目录，防范 Zip Slip 路径穿越攻击并保留执行权限。"""
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
                _restore_file_permissions(target_path, member.external_attr >> 16)
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
                _restore_file_permissions(target_path, member.mode)
    else:
        raise ValueError(f"不支持的便携更新包格式: {archive_path.name}")

    # 兼容顶层包含单一发布文件夹（如 AgentHub-Local-v1.5.0-portable/）的压缩包布局
    entries = list(staging_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return staging_dir


def _write_file_inplace(src_file: Path, dst_file: Path) -> None:
    """原地覆写目标文件内容（保留原有 inode，兼容 Docker 单文件 bind mount）。"""
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    if dst_file.exists():
        # 直接截断写入内容，避免 unlink/replace 在 Docker 单文件挂载点触发 EBUSY
        with open(src_file, "rb") as src_fp, open(dst_file, "wb") as dst_fp:
            shutil.copyfileobj(src_fp, dst_fp)
        try:
            shutil.copymode(src_file, dst_file)
        except OSError:
            pass
    else:
        shutil.copy2(src_file, dst_file)


def _sync_directory_tree(
    src_dir: Path,
    dst_dir: Path,
    skip_top_dirs: frozenset[str] = PROTECTED_TOP_LEVEL_NAMES,
    skip_if_exists_files: frozenset[str] = PROTECTED_IF_EXISTS_FILES,
) -> int:
    """将源目录树安全同步至目标目录，跳过受保护目录并在根目录层保护用户配置文件。"""
    if not src_dir.is_dir():
        return 0

    dst_resolved = dst_dir.resolve()
    dst_resolved.mkdir(parents=True, exist_ok=True)
    updated_files = 0

    for root, dirnames, filenames in os.walk(src_dir):
        root_path = Path(root)
        rel_dir = root_path.relative_to(src_dir)

        if rel_dir == Path("."):
            dirnames[:] = [d for d in dirnames if d not in skip_top_dirs]
        else:
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]

        for filename in filenames:
            if rel_dir == Path("."):
                if filename in skip_top_dirs:
                    continue
                if filename in skip_if_exists_files and (dst_resolved / filename).exists():
                    continue

            src_file = root_path / filename
            dst_file = (dst_resolved / rel_dir / filename).resolve()
            if not dst_file.is_relative_to(dst_resolved):
                raise ValueError(f"拒绝写入目标根目录之外的路径: {dst_file}")
            _write_file_inplace(src_file, dst_file)
            updated_files += 1

    return updated_files


def _apply_portable_archive(archive_path: Path, app_root: Path) -> int:
    """将便携包中的新版本文件安全同步到应用根目录，严格跳过用户数据与运行环境目录。"""
    staging_dir = app_root / "data" / "updates" / f"staging_{int(time.time())}"
    try:
        src_root = _extract_archive_safely(archive_path, staging_dir)
        return _sync_directory_tree(src_root, app_root)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def _apply_docker_archive(archive_path: Path, app_root: Path) -> int:
    """将便携包按 Docker 容器挂载结构映射到 /app（backend->/app, frontend/out->/app/static, version.json->/app/version.json）。"""
    can_update, blocked_reason = detect_docker_update_capability(app_root)
    if not can_update:
        raise ValueError(blocked_reason)

    staging_dir = app_root / "data" / "updates" / f"staging_{int(time.time())}"
    updated_files = 0
    try:
        src_root = _extract_archive_safely(archive_path, staging_dir)

        # 1. 后端核心代码：压缩包内为 backend/，容器中 ./backend 挂载为 /app
        backend_src = src_root / "backend"
        if backend_src.is_dir():
            updated_files += _sync_directory_tree(backend_src, app_root)
        else:
            # 兜底兼容直接平铺后端源码的压缩包结构
            updated_files += _sync_directory_tree(
                src_root,
                app_root,
                skip_top_dirs=PROTECTED_TOP_LEVEL_NAMES | frozenset({"frontend"}),
            )

        # 2. 前端静态资源：压缩包内为 frontend/out/，容器中 ./frontend/out 挂载为 /app/static
        frontend_out_src = src_root / "frontend" / "out"
        if frontend_out_src.is_dir():
            static_dst = app_root / "static"
            updated_files += _sync_directory_tree(
                frontend_out_src,
                static_dst,
                skip_top_dirs=frozenset({"__pycache__"}),
                skip_if_exists_files=frozenset(),
            )

        # 3. 根版本清单：原地覆写 /app/version.json（兼容单文件 bind mount）
        version_src = src_root / "version.json"
        if version_src.is_file():
            _write_file_inplace(version_src, app_root / "version.json")
            updated_files += 1

        # 4. 内置技能库（仅当容器中存在可写 skills 目录或包内附带 skills 时同步）
        skills_src = src_root / "skills"
        if skills_src.is_dir():
            updated_files += _sync_directory_tree(
                skills_src,
                app_root / "skills",
                skip_top_dirs=frozenset({"__pycache__", ".git"}),
                skip_if_exists_files=frozenset(),
            )
    except PermissionError as exc:
        raise ValueError(f"Docker 容器内文件写入权限不足，请检查宿主机目录与挂载权限: {exc}") from exc
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return updated_files


def _apply_frozen_hotpatch(archive_path: Path, app_root: Path) -> int:
    """将 4MB 轻量更新包安全写入 app_override/ 目录，供 Windows EXE 与 macOS .app 热加载，绝不触碰用户数据。"""
    staging_dir = app_root / "data" / "updates" / f"hotpatch_staging_{int(time.time())}"
    override_dir = app_root / "app_override"
    updated_files = 0
    try:
        src_root = _extract_archive_safely(archive_path, staging_dir)
        if not (src_root / "backend" / "main.py").is_file():
            raise ValueError("热更新包缺少核心后端入口 backend/main.py")
        if not (src_root / "frontend" / "out" / "index.html").is_file():
            raise ValueError("热更新包缺少静态前端入口 frontend/out/index.html")
        if not (src_root / "version.json").is_file():
            raise ValueError("热更新包缺少版本清单 version.json")

        override_dir.mkdir(parents=True, exist_ok=True)
        for sub_name in ("backend", "frontend", "skills", "assets"):
            src_sub = src_root / sub_name
            if not src_sub.is_dir():
                continue
            dst_sub = override_dir / sub_name
            if dst_sub.exists():
                shutil.rmtree(dst_sub, ignore_errors=True)
            updated_files += _sync_directory_tree(
                src_sub,
                dst_sub,
                skip_top_dirs=frozenset({"__pycache__", "data", "tests"}),
                skip_if_exists_files=frozenset(),
            )

        _write_file_inplace(src_root / "version.json", override_dir / "version.json")
        updated_files += 1

        # 增量同步新增内置技能到用户可写 skills/ 目录（已有技能跳过，不覆盖用户自定义）
        skills_src = src_root / "skills"
        if skills_src.is_dir():
            user_skills = app_root / "skills"
            user_skills.mkdir(parents=True, exist_ok=True)
            for item in skills_src.iterdir():
                if not item.is_dir():
                    continue
                target_skill = user_skills / item.name
                if not target_skill.exists():
                    shutil.copytree(item, target_skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return updated_files
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def build_windows_hotpatch_restart_script(
    *,
    target_exe: Path,
    wait_pids: list[int],
) -> str:
    """生成 Windows 热更新后等待旧 EXE 释放再重新拉起的静默 PowerShell 脚本。"""
    exe_str = str(target_exe).replace("'", "''")
    work_dir = str(target_exe.parent).replace("'", "''")
    pids_literal = ", ".join(str(int(p)) for p in wait_pids if int(p) > 0) or "0"
    return f"""$ErrorActionPreference = 'SilentlyContinue'
$TargetExe = '{exe_str}'
$WorkDir = '{work_dir}'
$WaitPids = @({pids_literal})
foreach ($pidVal in $WaitPids) {{
    if ($pidVal -le 0) {{ continue }}
    for ($i = 0; $i -lt 50; $i++) {{
        $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
        if (-not $proc) {{ break }}
        Start-Sleep -Milliseconds 250
    }}
}}
Get-ChildItem Env: | Where-Object {{
    $_.Name -like '_PYI_*' -or $_.Name -in @('_MEIPASS2', 'DESKTOP_PARENT_PID', 'TAURI_PARENT_PID', 'PYTHONHOME', 'PYTHONPATH')
}} | ForEach-Object {{
    Remove-Item "Env:\\$($_.Name)" -ErrorAction SilentlyContinue
}}
$env:PYINSTALLER_RESET_ENVIRONMENT = '1'
if (Test-Path -LiteralPath $TargetExe) {{
    Start-Process -FilePath $TargetExe -WorkingDirectory $WorkDir -WindowStyle Normal
}}
"""


def build_macos_hotpatch_restart_script(
    *,
    target_exec: Path,
    wait_pid: int,
) -> str:
    """生成 macOS 热更新后等待旧 .app 退出再重新拉起的 Shell 脚本。"""
    app_bundle: Path | None = None
    for parent in target_exec.parents:
        if parent.name.endswith(".app"):
            app_bundle = parent
            break
    launch_cmd = f'open -n "{app_bundle.as_posix()}"' if app_bundle else f'"{target_exec.as_posix()}" &'
    return f"""#!/bin/sh
WAIT_PID={int(wait_pid)}
i=0
while [ "$i" -lt 50 ]; do
  if ! kill -0 "$WAIT_PID" 2>/dev/null; then
    break
  fi
  sleep 0.25
  i=$((i + 1))
done
unset _MEIPASS2 DESKTOP_PARENT_PID TAURI_PARENT_PID PYTHONHOME PYTHONPATH
export PYINSTALLER_RESET_ENVIRONMENT=1
{launch_cmd}
"""


def build_windows_exe_update_script(
    source_exe: Path,
    target_exe: Path,
    wait_pids: list[int],
    log_file: Path | None = None,
) -> str:
    """生成 Windows 单文件 EXE 脱离进程更新并自动重启的 PowerShell 脚本内容。

    由于 Windows 与 PyInstaller --onefile Bootloader 会锁定正在运行的可执行文件句柄，
    必须等待所有关联进程彻底退出后，先移走旧文件再落位新文件并重新拉起；
    若旧客户端文件名携带历史版本号，则在升级时自动归一化为无版本号文件名。
    """
    unique_pids = [str(pid) for pid in dict.fromkeys(wait_pids) if isinstance(pid, int) and pid > 0]
    pids_literal = ", ".join(unique_pids) if unique_pids else "0"
    resolved_target = target_exe.resolve()
    resolved_launch = normalize_windows_target_exe(resolved_target)
    src_str = str(source_exe.resolve()).replace("'", "''")
    dst_str = str(resolved_target).replace("'", "''")
    launch_str = str(resolved_launch).replace("'", "''")
    bak_str = f"{dst_str}.old"
    work_dir = str(resolved_launch.parent).replace("'", "''")
    log_path = log_file if log_file is not None else (source_exe.resolve().parent / "apply_update.log")
    log_str = str(log_path).replace("'", "''")

    if resolved_launch == resolved_target:
        replace_and_launch_block = """$replaced = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        if (-not (Test-Path -LiteralPath $SourceExe)) {
            Write-UpdateLog "ERROR: SourceExe does not exist."
            break
        }
        if (Test-Path -LiteralPath $TargetExe) {
            if (Test-Path -LiteralPath $BackupExe) {
                Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue
            }
            Move-Item -LiteralPath $TargetExe -Destination $BackupExe -Force -ErrorAction Stop
        }
        Move-Item -LiteralPath $SourceExe -Destination $TargetExe -Force -ErrorAction Stop
        if (Test-Path -LiteralPath $BackupExe) {
            Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue
        }
        $replaced = $true
        Write-UpdateLog "Replaced TargetExe successfully on attempt $attempt."
        break
    } catch {
        Write-UpdateLog "Attempt $attempt failed: $($_.Exception.Message)"
        if ((-not (Test-Path -LiteralPath $TargetExe)) -and (Test-Path -LiteralPath $BackupExe)) {
            try { Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction SilentlyContinue } catch {}
        }
        Start-Sleep -Milliseconds 500
    }
}

if ($replaced -and (Test-Path -LiteralPath $TargetExe)) {
    Write-UpdateLog "Launching updated executable: $TargetExe"
    # 清理 PyInstaller 6.x (_PYI_*) 与旧进程父级环境变量，强制新 EXE 独立解压运行
    Get-ChildItem Env: | Where-Object {
        $_.Name -like '_PYI_*' -or $_.Name -in @('_MEIPASS2', 'DESKTOP_PARENT_PID', 'TAURI_PARENT_PID', 'PYTHONHOME', 'PYTHONPATH')
    } | ForEach-Object {
        Remove-Item "Env:\\$($_.Name)" -ErrorAction SilentlyContinue
    }
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath $TargetExe -WorkingDirectory $WorkDir -WindowStyle Normal
} else {
    Write-UpdateLog "Update failed: TargetExe was not replaced."
}"""
    else:
        replace_and_launch_block = f"""$LaunchExe = '{launch_str}'
for ($i = 0; $i -lt 30; $i++) {{
    $launchHolders = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq $LaunchExe }})
    if ($launchHolders.Count -eq 0) {{ break }}
    Write-UpdateLog "Waiting for $($launchHolders.Count) process(es) holding LaunchExe..."
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
        if (Test-Path -LiteralPath $LaunchExe) {{
            Remove-Item -LiteralPath $LaunchExe -Force -ErrorAction Stop
        }}
        Move-Item -LiteralPath $SourceExe -Destination $LaunchExe -Force -ErrorAction Stop
        if (Test-Path -LiteralPath $BackupExe) {{
            Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue
        }}
        $replaced = $true
        Write-UpdateLog "Replaced and normalized TargetExe -> $LaunchExe on attempt $attempt."
        break
    }} catch {{
        Write-UpdateLog "Attempt $attempt failed: $($_.Exception.Message)"
        if ((-not (Test-Path -LiteralPath $TargetExe)) -and (Test-Path -LiteralPath $BackupExe)) {{
            try {{ Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction SilentlyContinue }} catch {{}}
        }}
        Start-Sleep -Milliseconds 500
    }}
}}

if ($replaced -and (Test-Path -LiteralPath $LaunchExe)) {{
    Write-UpdateLog "Launching normalized updated executable: $LaunchExe"
    # 清理 PyInstaller 6.x (_PYI_*) 与旧进程父级环境变量，强制新 EXE 独立解压运行
    Get-ChildItem Env: | Where-Object {{
        $_.Name -like '_PYI_*' -or $_.Name -in @('_MEIPASS2', 'DESKTOP_PARENT_PID', 'TAURI_PARENT_PID', 'PYTHONHOME', 'PYTHONPATH')
    }} | ForEach-Object {{
        Remove-Item "Env:\\$($_.Name)" -ErrorAction SilentlyContinue
    }}
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath $LaunchExe -WorkingDirectory $WorkDir -WindowStyle Normal
}} else {{
    Write-UpdateLog "Update failed: LaunchExe was not replaced."
}}"""

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

{replace_and_launch_block}

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

def _is_posix_zombie(target_pid: int) -> bool:
    stat_path = f'/proc/{{target_pid}}/stat'
    if not os.path.isfile(stat_path):
        return False
    try:
        with open(stat_path, 'r', encoding='utf-8', errors='ignore') as fp:
            content = fp.read()
        rparen = content.rfind(')')
        if rparen != -1 and len(content) > rparen + 2:
            return content[rparen + 2] == 'Z'
    except OSError:
        pass
    return False

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
            if _is_posix_zombie(pid):
                break
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
        main_name = Path(main_file).name.lower()
        if main_name == "main.py":
            return ["-m", "uvicorn", "backend.main:app", "--host", settings.host, "--port", str(settings.port)]
        if "uvicorn" in main_name:
            raw_args = list(sys.argv[1:])
            has_backend_main = (app_root / "backend" / "main.py").is_file()
            normalized_args = [
                "backend.main:app" if (arg == "main:app" and has_backend_main) else arg
                for arg in raw_args
                if arg != "--reload"
            ]
            if not normalized_args:
                normalized_args = ["backend.main:app", "--host", settings.host, "--port", str(settings.port)]
            return ["-m", "uvicorn", *normalized_args]
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
            "can_auto_update": True,
            "update_blocked_reason": "",
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

    @staticmethod
    def _is_cached_package_valid(
        cached_file: Path,
        expected_size: int,
        target_version: str,
        require_known_size: bool = False,
    ) -> bool:
        """校验本地缓存的更新包大小及所属版本号，防止无版本号包跨版本复用旧缓存。"""
        if not cached_file.is_file():
            return False
        if require_known_size and expected_size <= 0:
            return False
        if expected_size > 0 and cached_file.stat().st_size != expected_size:
            return False

        clean_target = str(target_version or "").strip().lstrip("vV")
        if not clean_target:
            return False

        ver_marker = cached_file.parent / f"{cached_file.name}.version"
        if ver_marker.is_file():
            try:
                cached_ver = ver_marker.read_text(encoding="utf-8").strip().lstrip("vV")
                return cached_ver == clean_target
            except OSError:
                return False

        # 无版本标记文件时，仅当文件名自身明确携带目标版本号才允许复用
        return clean_target in cached_file.name

    @staticmethod
    def _write_cache_version_marker(pkg_path: Path, target_version: str) -> None:
        """记录无版本号安装包对应下载的目标版本号，供后续缓存比对。"""
        clean_target = str(target_version or "").strip().lstrip("vV")
        if not clean_target:
            return
        ver_marker = pkg_path.parent / f"{pkg_path.name}.version"
        ver_marker.write_text(clean_target, encoding="utf-8")

    def get_status(self) -> dict[str, Any]:
        """返回当前更新状态快照（实时刷新版本号、运行形态与热更新可用性）。"""
        snapshot = dict(self._state)
        install_mode = detect_install_mode()
        snapshot["current_version"] = get_current_version()
        snapshot["install_mode"] = install_mode
        if install_mode == "docker":
            can_update, blocked_reason = detect_docker_update_capability(get_writable_app_root())
        else:
            can_update, blocked_reason = True, ""
        snapshot["can_auto_update"] = can_update
        snapshot["update_blocked_reason"] = blocked_reason
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

                # 检查本地更新缓存目录是否已存在同版本且同字节大小的完整更新包
                safe_asset_name = Path(asset_name).name
                cached_file = self.get_updates_dir() / safe_asset_name
                if self._is_cached_package_valid(
                    cached_file=cached_file,
                    expected_size=asset_size,
                    target_version=latest_ver,
                    require_known_size=False,
                ):
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
        target_version = str(self._state.get("latest_version") or "").strip()

        if not asset_name or not _is_allowed_download_url(download_url):
            self._state["status"] = "error"
            self._state["error_message"] = "更新包下载地址无效或不安全，请前往 GitHub Releases 手动下载"
            return self.get_status()

        final_path = self.get_updates_dir() / asset_name
        if not force and self._is_cached_package_valid(
            cached_file=final_path,
            expected_size=expected_size,
            target_version=target_version,
            require_known_size=True,
        ):
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
            self._download_worker(
                download_url=download_url,
                asset_name=asset_name,
                expected_size=expected_size,
                target_version=target_version,
            )
        )
        return self.get_status()

    async def _download_worker(
        self,
        download_url: str,
        asset_name: str,
        expected_size: int,
        target_version: str = "",
    ) -> None:
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
            self._write_cache_version_marker(final_path, target_version)
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
        is_hotpatch_archive = downloaded_path.name.lower().endswith((".zip", ".tar.gz"))

        # Windows / macOS 冻结态下载轻量便携更新包（~4MB）时，直接写入 app_override/ 并重启，无需重新下载 100MB+ 整包
        if install_mode in ("windows_exe", "macos_app") and is_hotpatch_archive:
            try:
                updated_files = await asyncio.to_thread(_apply_frozen_hotpatch, downloaded_path, app_root)
            except Exception as exc:
                self._state["status"] = "downloaded"
                self._state["error_message"] = str(exc)
                raise ValueError(str(exc)) from exc

            target_exec = Path(sys.executable).resolve()
            if install_mode == "windows_exe":
                wait_pids = [os.getpid()]
                parent_pid_raw = os.environ.get("DESKTOP_PARENT_PID", "").strip()
                if parent_pid_raw.isdigit():
                    wait_pids.append(int(parent_pid_raw))
                script_path = self.get_updates_dir() / "restart_hotpatch.ps1"
                script_path.write_text(
                    build_windows_hotpatch_restart_script(target_exe=target_exec, wait_pids=wait_pids),
                    encoding="utf-8-sig",
                )
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
                        cwd=target_exec.parent,
                    )
                    asyncio.create_task(self._delayed_exit(0.6))
            else:
                script_path = self.get_updates_dir() / "restart_hotpatch.sh"
                script_path.write_text(
                    build_macos_hotpatch_restart_script(target_exec=target_exec, wait_pid=os.getpid()),
                    encoding="utf-8",
                )
                os.chmod(script_path, 0o755)
                if trigger_exit:
                    self._spawn_detached_command(["/bin/sh", str(script_path)], cwd=app_root)
                    asyncio.create_task(self._delayed_exit(0.6))

            return {
                "status": "restarting",
                "install_mode": install_mode,
                "update_kind": "hotpatch",
                "target_version": self._state.get("latest_version"),
                "updated_files": updated_files,
                "script_path": str(script_path),
                "message": f"已完成轻量热更包 {updated_files} 个核心文件升级，客户端正在重启加载新版本...",
            }

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

        if install_mode == "docker":
            try:
                updated_files = await asyncio.to_thread(_apply_docker_archive, downloaded_path, app_root)
            except Exception as exc:
                self._state["status"] = "downloaded"
                self._state["error_message"] = str(exc)
                raise ValueError(str(exc)) from exc
            if trigger_exit:
                # Docker 下 uvicorn 为容器 PID 1：不另起子进程以免端口冲突或随容器销毁，直接退出由 restart: unless-stopped 自动拉起
                asyncio.create_task(self._delayed_exit(0.6))
            return {
                "status": "restarting",
                "install_mode": install_mode,
                "target_version": self._state.get("latest_version"),
                "updated_files": updated_files,
                "message": f"已完成 Docker 容器内 {updated_files} 个核心文件升级，正在安全退出进程以触发 Docker 自动重启加载新版本...",
            }

        # portable 模式：先在当前目录安全解压替换代码与前端静态资源，再重启主程序进程
        try:
            updated_files = await asyncio.to_thread(_apply_portable_archive, downloaded_path, app_root)
        except Exception as exc:
            self._state["status"] = "downloaded"
            self._state["error_message"] = str(exc)
            raise ValueError(str(exc)) from exc
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
