"""
Gemini / Antigravity CLI 账号目录管理服务
负责为每个账号创建/删除独立 GEMINI_CLI_HOME 目录，
以及引导用户在该目录完成官方 CLI 登录授权
基于 Windows 全环境兼容的子进程管道适配器
"""
import os
import re
import json
import subprocess
import threading
import asyncio
from pathlib import Path
from typing import Tuple

from ..core.logging_config import logger
from ..core.proxy_helper import get_system_proxy_env, get_system_proxy_url
from ..repository.account_repository import AccountRepository
from .agent_runner import _find_cli_binary

# 账号目录根路径（优先使用 .agenthub/accounts，兼容回退旧路径）
_new_root = Path.home() / ".agenthub" / "accounts"
_legacy_root = Path.home() / ".gemini" / "geminihub-accounts"
ACCOUNTS_ROOT = _legacy_root if not _new_root.exists() and _legacy_root.exists() else _new_root

# 内存中暂存各账号拉起登录时捕获的 OAuth 授权链接或终端命令
_AUTH_URL_STORE: dict[str, str] = {}
_AUTH_MSG_STORE: dict[str, str] = {}


class CliLoginProcess:
    """Windows 全环境透明兼容的 CLI 登录子进程管理器（彻底免疫 NotImplementedError）"""
    def __init__(self, cmd: list[str], env: dict[str, str], cwd: str, alias: str):
        self.alias = alias
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        self.pid = self._proc.pid
        self.returncode: int | None = None

        def _pump_stream(stream, name: str):
            url_pattern = re.compile(r'https://[^\s]+(?:accounts\.google\.com|oauth)[^\s]*')
            for line_bytes in iter(stream.readline, b""):
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if line:
                    logger.info(f"[CLI Login - {alias} - {name}]: {line}")
                    m = url_pattern.search(line)
                    if m:
                        url = m.group(0).rstrip(").,;")
                        _AUTH_URL_STORE[alias] = url
                        logger.info(f"捕获到账号 [{alias}] 官方登录授权 URL: {url}")
            try:
                stream.close()
            except Exception:
                pass

        threading.Thread(target=_pump_stream, args=(self._proc.stdout, "stdout"), daemon=True).start()
        threading.Thread(target=_pump_stream, args=(self._proc.stderr, "stderr"), daemon=True).start()

    def write_stdin(self, text: str) -> None:
        """向等待中的 CLI 进程写入授权码并冲刷管道（同时支持 Windows CRLF 与 LF 冲刷）"""
        if self._proc.stdin and not self._proc.stdin.closed:
            clean = text.strip()
            # 写入 CRLF 确保 Windows 控制台感知回车
            self._proc.stdin.write(f"{clean}\r\n".encode("utf-8"))
            self._proc.stdin.flush()
            logger.info(f"已向账号 [{self.alias}] 管道写入授权码并冲刷缓冲区 (长度: {len(clean)})")

    def poll(self) -> int | None:
        self.returncode = self._proc.poll()
        return self.returncode

    def kill(self) -> None:
        try:
            self._proc.kill()
        except Exception:
            pass


_CLI_PROC_STORE: dict[str, CliLoginProcess] = {}


def get_account_cli_home(alias: str) -> Path:
    """根据账号别名获取其专属 CLI 目录路径"""
    return ACCOUNTS_ROOT / alias


def open_interactive_terminal(alias: str) -> bool:
    """在 Windows 宿主机上弹出一个专属的独立交互式 PowerShell 窗口完成官方登录（避免多层命令行引号脱壳）"""
    import sys
    account_dir = create_account_dir(alias)
    cli_bin, _ = _find_cli_binary()
    if not cli_bin:
        raise FileNotFoundError("未在系统中检测到可用的 Antigravity (agy) 或 Gemini CLI 可执行文件")

    if sys.platform == "win32":
        proxy_url = get_system_proxy_url() or ""
        script_path = account_dir / "login_session.ps1"

        script_lines = [
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            f'$host.UI.RawUI.WindowTitle = "GeminiHub CLI 官方授权 - {alias}"',
        ]
        if proxy_url:
            script_lines.extend([
                f'$env:HTTP_PROXY = "{proxy_url}"',
                f'$env:HTTPS_PROXY = "{proxy_url}"',
                f'$env:ALL_PROXY = "{proxy_url}"',
                '$env:NO_PROXY = "127.0.0.1,localhost"',
            ])
        script_lines.extend([
            f'$env:GEMINI_CLI_HOME = "{account_dir}"',
            '$env:GEMINI_CLI_TRUST_WORKSPACE = "true"',
            'Write-Host "============================================================" -ForegroundColor Cyan',
            f'Write-Host " [AgentHub] 正在为账号 [{alias}] 启动 Google 官方客户端授权" -ForegroundColor Yellow',
            'Write-Host "============================================================" -ForegroundColor Cyan',
            f'& "{cli_bin}" -p "hi"',
            'Write-Host "`n[AgentHub] 授权已完成或退出，请关闭本窗口返回网页查看状态。" -ForegroundColor Green',
            'Write-Host "本窗口将在 10 秒后自动关闭..." -ForegroundColor Gray',
            'Start-Sleep -Seconds 10',
        ])

        # 使用 UTF-8 with BOM 保存脚本文件，防止 Windows PowerShell 解析中文字符乱码
        script_path.write_text("\r\n".join(script_lines), encoding="utf-8-sig")

        # 使用 Win32 标准的 CREATE_NEW_CONSOLE (0x00000010) 标志直接派生新控制台窗口
        CREATE_NEW_CONSOLE = 0x00000010
        cmd = [
            "powershell",
            "-NoExit",
            "-ExecutionPolicy", "Bypass",
            "-File", str(script_path)
        ]
        subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE)
        logger.info(f"已为账号 [{alias}] 生成专用登录脚本并呼出全新独立 PowerShell 控制台")
        return True
    return False


def get_windows_account_cmd(alias: str) -> str:
    """生成适用于 Windows PowerShell 的账号专属交互式登录命令"""
    cli_bin, _ = _find_cli_binary()
    acc_dir = ACCOUNTS_ROOT / alias
    return f'$env:GEMINI_CLI_HOME="{acc_dir}"; & "{cli_bin}" -p "hi"'


def get_account_auth_info(alias: str) -> dict:
    """获取指定账号的授权指引信息"""
    return {
        "auth_url": _AUTH_URL_STORE.get(alias),
        "auth_command": get_windows_account_cmd(alias),
        "auth_message": _AUTH_MSG_STORE.get(alias)
    }


def create_account_dir(alias: str) -> Path:
    """
    为账号创建独立 CLI 目录，预置 OAuth 设置并设置安全权限（仅当前用户可读写）。
    """
    account_dir = get_account_cli_home(alias)
    account_dir.mkdir(parents=True, exist_ok=True)

    gemini_conf_dir = account_dir / ".gemini"
    gemini_conf_dir.mkdir(parents=True, exist_ok=True)
    settings_file = gemini_conf_dir / "settings.json"
    if not settings_file.exists():
        try:
            settings_file.write_text(
                json.dumps({
                    "security": {
                        "auth": {
                            "selectedType": "oauth-personal"
                        }
                    }
                }, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"预置 settings.json 失败: {e}")

    try:
        username = os.environ.get("USERNAME", "")
        if username:
            subprocess.run(
                ["icacls", str(account_dir), "/inheritance:r",
                 "/grant:r", f"{username}:(OI)(CI)F"],
                capture_output=True, check=False
            )
    except Exception as e:
        logger.warning(f"设置账号目录权限失败（非致命）: {e}")

    return account_dir


def delete_account_dir(alias: str) -> None:
    """删除账号专属 CLI 目录（账号删除时调用）"""
    import shutil
    account_dir = get_account_cli_home(alias)
    if account_dir.exists():
        shutil.rmtree(account_dir, ignore_errors=True)
        logger.info(f"已删除账号目录: {account_dir}")
    _AUTH_URL_STORE.pop(alias, None)
    _AUTH_MSG_STORE.pop(alias, None)
    proc = _CLI_PROC_STORE.pop(alias, None)
    if proc:
        proc.kill()


def is_account_logged_in(alias: str) -> bool:
    """
    检测账号是否已完成 CLI 登录（目录内存在有效的凭据文件或为宿主机已纳管原生引擎）
    """
    if alias.lower() in ("antigravity-local", "default", "antigravity_local"):
        return True

    account_dir = get_account_cli_home(alias)
    gemini_dir = account_dir / ".gemini"

    # 检查进程退出状态
    proc = _CLI_PROC_STORE.get(alias)
    if proc and proc.poll() == 0:
        return True

    creds_exist = (
        (gemini_dir / "oauth_creds.json").exists()
        or (gemini_dir / "google_accounts.json").exists()
        or (gemini_dir / "credentials.json").exists()
        or (gemini_dir / "tokens.json").exists()
        or (account_dir / "installation_id").exists()
    )
    if creds_exist:
        return True

    settings_path = gemini_dir / "settings.json"
    if settings_path.exists():
        try:
            content = json.loads(settings_path.read_text(encoding="utf-8"))
            if content.get("security", {}).get("auth", {}).get("token"):
                return True
        except Exception:
            pass

    return False


def get_login_status_all(aliases: list[str]) -> dict[str, bool]:
    """批量查询多个账号的登录状态"""
    return {alias: is_account_logged_in(alias) for alias in aliases}


def get_account_auth_url(alias: str) -> str | None:
    """获取指定账号捕获到的 OAuth 登录地址"""
    return _AUTH_URL_STORE.get(alias)


async def launch_cli_login(alias: str) -> dict:
    """
    为指定账号拉起官方 Gemini / Antigravity CLI 登录流程
    基于全兼容的管道适配器，彻底免疫 Windows NotImplementedError
    """
    account_dir = create_account_dir(alias)

    env = os.environ.copy()
    env["GEMINI_CLI_HOME"] = str(account_dir)
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    env.update(get_system_proxy_env())

    cli_bin, cli_type = _find_cli_binary()
    if not cli_bin:
        raise FileNotFoundError("未在系统中检测到可用的 Antigravity (agy) 或 Gemini CLI 可执行文件")

    # 创建通用全兼容子进程
    proc = CliLoginProcess(
        cmd=[cli_bin, "-p", "hi"],
        env=env,
        cwd=str(account_dir),
        alias=alias
    )

    _CLI_PROC_STORE[alias] = proc

    logger.info(f"已为账号 [{alias}] 拉起 {cli_type} CLI 登录兼容进程 PID={proc.pid}")
    return {
        "alias": alias,
        "pid": proc.pid,
        "cli_home_dir": str(account_dir),
        "cli_binary": cli_bin,
        "message": f"官方 {cli_type} CLI 登录授权已启动，请在弹出的系统浏览器中完成登录。"
    }


class CliAccountService:
    """Gemini CLI 账号管理服务门面"""

    async def add_account(self, alias: str, email: str | None = None,
                          priority: int = 1) -> dict:
        """
        添加新账号：创建目录、写入数据库、拉起登录（支持失败事务回滚）。
        """
        if not re.match(r'^[\w\-]{1,32}$', alias):
            raise ValueError(f"账号别名只能包含字母、数字、下划线或连字符，最长 32 位: {alias}")

        existing = await AccountRepository.get_by_name(alias)
        if existing:
            if not is_account_logged_in(alias):
                logger.info(f"账号 [{alias}] 存在但未完成认证，允许重新拉起登录")
                login_info = await launch_cli_login(alias)
                return {
                    "account_id": existing.id,
                    "alias": alias,
                    "cli_home_dir": str(get_account_cli_home(alias)),
                    "login_info": login_info
                }
            raise ValueError(f"账号别名 '{alias}' 已存在且已就绪")

        cli_home_dir = create_account_dir(alias)

        account_id = await AccountRepository.create({
            "name": alias,
            "auth_type": "gemini_cli",
            "credentials": str(cli_home_dir),
            "priority": priority,
            "rpm_limit": 60,
            "rpd_limit": 1000,
            "email": email,
            "cli_home_dir": str(cli_home_dir),
            "extra_meta": '{"source": "gemini_cli"}'
        })

        try:
            login_info = await launch_cli_login(alias)
            return {
                "account_id": account_id,
                "alias": alias,
                "cli_home_dir": str(cli_home_dir),
                "login_info": login_info
            }
        except Exception as e:
            logger.error(f"拉起 CLI 进程异常，执行事务回滚删除账号 [{alias}]: {e}")
            await AccountRepository.delete(account_id)
            delete_account_dir(alias)
            raise e

    async def submit_auth_code(self, alias: str, code: str) -> Tuple[bool, str]:
        """向正在等待交互式输入的 CLI 进程 stdin 写入用户复制的 Google 授权代码"""
        proc = _CLI_PROC_STORE.get(alias)
        if not proc or proc.poll() is not None:
            return False, "当前未检测到正在运行的授权进程，请重新点击拉起登录"

        try:
            proc.write_stdin(code.strip())
            logger.info(f"已成功向账号 [{alias}] CLI 子进程写入授权码")
            return True, "授权码已成功提交给 CLI 进程，正在完成认证..."
        except Exception as e:
            logger.error(f"向 CLI 子进程写入授权码失败: {e}")
            return False, f"写入授权码异常: {str(e)}"

    async def remove_account(self, account_id: int) -> None:
        """删除账号：从数据库删除并清理 CLI 目录"""
        account = await AccountRepository.get_by_id(account_id)
        if account:
            delete_account_dir(account.name)
            await AccountRepository.delete(account_id)

    def get_login_status(self, alias: str) -> bool:
        """查询单个账号是否已完成登录"""
        return is_account_logged_in(alias)


    async def import_system_cli_account(self, priority: int = 1) -> dict:
        """
        一键纳管宿主机已安装且已登录的官方 Antigravity / Gemini CLI 原生引擎
        """
        cli_bin, cli_type = _find_cli_binary()
        if not cli_bin:
            raise FileNotFoundError("未在系统中检测到可用的 Antigravity (agy) 或 Gemini CLI 可执行文件")

        alias = "Antigravity-Local"
        existing = await AccountRepository.get_by_name(alias)
        if existing:
            return {
                "account_id": existing.id,
                "alias": alias,
                "message": f"宿主机官方 {cli_type} 原生引擎已存在于算力池中（就绪状态）！"
            }

        account_id = await AccountRepository.create({
            "name": alias,
            "auth_type": "gemini_cli",
            "credentials": "system_default",
            "priority": priority,
            "rpm_limit": 60,
            "rpd_limit": 1500,
            "email": "local-antigravity@gemini",
            "cli_home_dir": "default",
            "extra_meta": json.dumps({"source": "system_default", "cli_binary": cli_bin})
        })

        logger.info(f"成功一键纳管宿主机官方 {cli_type} 原生引擎 (ID: {account_id})")
        return {
            "account_id": account_id,
            "alias": alias,
            "message": f"成功一键纳管宿主机官方 {cli_type} 原生引擎，已就绪可随时调用！"
        }


cli_account_service = CliAccountService()
