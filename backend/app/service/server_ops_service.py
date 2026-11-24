"""
远程服务器 SSH 运维与三层安全门禁服务（ServerOpsService）。
职责：
1. 托管服务器资产注册与维护（Fernet 加密凭证存储，严禁明文与硬编码密钥，严格脱敏返回）；
2. SSH 远程只读体检（CPU/内存/磁盘/负载/Docker/Top 进程）与运维命令执行；
3. 工具级 + 路径级 + 命令级 + 间接提示词注入防护四维三层风险评估（L0 / L1 / L2）；
4. L2 高危操作挂起、4 位确认码发放、微信二次确认放行执行或取消。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import secrets
import shlex
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from ..core.config import settings
from ..core.logging_config import logger
from ..repository.db import db_manager

# 明确危险或写盘破坏性指令模式（匹配即强制升级 L2 二次确认）
_HIGH_RISK_CMD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|\s|;|&&|\|\|)(?:rm|rmdir|unlink|shred)\b", re.IGNORECASE),
    re.compile(r"\b(?:mkfs|fdisk|parted|dd\s+if=|reboot|shutdown|poweroff|halt|init\s+0)\b", re.IGNORECASE),
    re.compile(r"\bsystemctl\s+(?:restart|stop|disable|mask|reload)\b", re.IGNORECASE),
    re.compile(r"\bdocker(?:\s+compose)?\s+(?:rm|stop|restart|kill|down|prune|exec)\b", re.IGNORECASE),
    re.compile(r"\bkill(?:all)?\s+(?:-9|-SIGKILL)\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+(?:-R\s+)?777\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\b.*(?:--force|-f)\b", re.IGNORECASE),
    re.compile(r"\bpython[0-9.]*\s+-c\b.*(?:unlink|rmtree|remove|system|popen|exec)", re.IGNORECASE),
    re.compile(r"\b(?:DROP\s+(?:TABLE|DATABASE)|DELETE\s+FROM|TRUNCATE\s+TABLE)\b", re.IGNORECASE),
    re.compile(r">\s*(?:/etc/|/usr/|/var/|/boot/|/root/\.ssh)", re.IGNORECASE),
)

# 生产机默认仅免密放行的安全只读命令前缀白名单
_READONLY_CMD_PREFIXES: tuple[str, ...] = (
    "ls",
    "ll",
    "cat",
    "head",
    "tail",
    "grep",
    "egrep",
    "rg",
    "df",
    "free",
    "uptime",
    "ps",
    "top -bn1",
    "ss",
    "netstat",
    "lsof",
    "docker ps",
    "docker logs",
    "docker stats --no-stream",
    "uname",
    "lscpu",
    "du",
    "find",
    "wc",
    "pwd",
    "whoami",
    "id",
    "date",
    "hostname",
    "git status",
    "git log",
    "git diff",
    "git branch",
)

# 敏感系统路径前缀（工作区外写入或直接命中心敏感路径即升 L2）
_SENSITIVE_PATH_PARTS: tuple[str, ...] = (
    "/etc/",
    "/.ssh/",
    "/boot/",
    "/usr/bin/",
    "/usr/sbin/",
    "nginx.conf",
    ".env",
    "id_rsa",
    "id_ed25519",
    "authorized_keys",
)


class ServerOpsService:
    """多服务器 SSH 运维与安全门禁中枢。"""

    def __init__(self) -> None:
        self._fernet_cache: Fernet | None = None

    def _get_fernet(self) -> Fernet:
        """基于环境变量或本地持久化密钥文件派生 Fernet 对称加密器。"""
        if self._fernet_cache is not None:
            return self._fernet_cache

        env_secret = (os.getenv("AGENTHUB_SECRET_KEY") or os.getenv("SECRET_KEY") or "").strip()
        if env_secret:
            key_bytes = base64.urlsafe_b64encode(hashlib.sha256(env_secret.encode("utf-8")).digest())
            self._fernet_cache = Fernet(key_bytes)
            return self._fernet_cache

        vault_key_path = Path(settings.db_path).parent / ".server_vault.key"
        vault_key_path.parent.mkdir(parents=True, exist_ok=True)
        if vault_key_path.exists():
            raw = vault_key_path.read_text(encoding="utf-8").strip()
            if raw:
                self._fernet_cache = Fernet(raw.encode("utf-8"))
                return self._fernet_cache

        generated = Fernet.generate_key()
        try:
            vault_key_path.write_text(generated.decode("utf-8"), encoding="utf-8")
            if os.name != "nt":
                os.chmod(vault_key_path, 0o600)
        except Exception as e:
            logger.debug(f"[ServerOps] 写入本地密钥文件失败: {e}")
        self._fernet_cache = Fernet(generated)
        return self._fernet_cache

    def encrypt_secret(self, plain: str) -> str:
        """加密敏感凭证。"""
        clean = (plain or "").strip()
        if not clean:
            return ""
        return self._get_fernet().encrypt(clean.encode("utf-8")).decode("utf-8")

    def decrypt_secret(self, token: str) -> str:
        """解密敏感凭证。"""
        clean = (token or "").strip()
        if not clean:
            return ""
        return self._get_fernet().decrypt(clean.encode("utf-8")).decode("utf-8")

    @staticmethod
    async def ensure_tables(conn: Any) -> None:
        """幂等创建服务器资产与挂起审批表。"""
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS server_assets (
                id TEXT PRIMARY KEY,
                alias TEXT NOT NULL UNIQUE,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 22,
                username TEXT NOT NULL DEFAULT 'root',
                auth_type TEXT NOT NULL DEFAULT 'key_path',
                credential_enc TEXT NOT NULL DEFAULT '',
                key_path TEXT NOT NULL DEFAULT '',
                env_tier TEXT NOT NULL DEFAULT 'prod',
                default_workdir TEXT NOT NULL DEFAULT '',
                allow_write INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS im_pending_approvals (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                link_id TEXT NOT NULL DEFAULT '',
                sender_id TEXT NOT NULL,
                channel_type TEXT NOT NULL DEFAULT 'weixin_clawbot',
                risk_level TEXT NOT NULL DEFAULT 'L2',
                tool_name TEXT NOT NULL,
                action_summary TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_im_approvals_sender ON im_pending_approvals(sender_id, status, expires_at DESC)"
        )
        await conn.commit()

    # ────────────────────────── 1. 服务器资产 CRUD（严格脱敏） ──────────────────────────

    async def upsert_server(
        self,
        *,
        alias: str,
        host: str,
        port: int = 22,
        username: str = "root",
        auth_type: str = "key_path",
        credential: str = "",
        key_path: str = "",
        env_tier: str = "prod",
        default_workdir: str = "",
        allow_write: bool = False,
        notes: str = "",
    ) -> dict[str, Any]:
        """新增或更新托管服务器资产（密码/私钥加密落库）。"""
        clean_alias = (alias or "").strip()
        clean_host = (host or "").strip()
        if not clean_alias or not clean_host:
            raise ValueError("服务器别名 (alias) 和主机地址 (host) 不能为空")

        norm_auth = auth_type if auth_type in ("key_path", "private_key_enc", "password_enc") else "key_path"
        norm_tier = env_tier if env_tier in ("prod", "staging", "dev", "host") else "prod"
        now_ts = time.time()
        enc_cred = self.encrypt_secret(credential) if credential.strip() else ""

        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            cur = await conn.execute(
                "SELECT id, credential_enc FROM server_assets WHERE alias = ? LIMIT 1",
                (clean_alias,),
            )
            existing = await cur.fetchone()
            if existing:
                srv_id = str(existing["id"])
                final_cred = enc_cred or str(existing["credential_enc"] or "")
                await conn.execute(
                    """
                    UPDATE server_assets
                    SET host = ?, port = ?, username = ?, auth_type = ?, credential_enc = ?,
                        key_path = ?, env_tier = ?, default_workdir = ?, allow_write = ?,
                        notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        clean_host,
                        int(port or 22),
                        (username or "root").strip(),
                        norm_auth,
                        final_cred,
                        key_path.strip(),
                        norm_tier,
                        default_workdir.strip(),
                        1 if allow_write else 0,
                        notes.strip(),
                        now_ts,
                        srv_id,
                    ),
                )
            else:
                srv_id = f"srv_{uuid.uuid4().hex[:10]}"
                await conn.execute(
                    """
                    INSERT INTO server_assets (
                        id, alias, host, port, username, auth_type, credential_enc, key_path,
                        env_tier, default_workdir, allow_write, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        srv_id,
                        clean_alias,
                        clean_host,
                        int(port or 22),
                        (username or "root").strip(),
                        norm_auth,
                        enc_cred,
                        key_path.strip(),
                        norm_tier,
                        default_workdir.strip(),
                        1 if allow_write else 0,
                        notes.strip(),
                        now_ts,
                        now_ts,
                    ),
                )
            await conn.commit()

        return await self.get_server(clean_alias, include_secret=False) or {"id": srv_id, "alias": clean_alias}

    async def list_servers(self) -> list[dict[str, Any]]:
        """列出所有托管服务器（剥离敏感凭证密文，仅保留 has_credential 布尔状态）。"""
        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            cur = await conn.execute("SELECT * FROM server_assets ORDER BY updated_at DESC")
            rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            cred_enc = str(d.pop("credential_enc", "") or "")
            d["has_credential"] = bool(cred_enc or d.get("key_path"))
            d["allow_write"] = bool(d.get("allow_write"))
            out.append(d)
        return out

    async def get_server(self, alias_or_id: str, include_secret: bool = False) -> dict[str, Any] | None:
        """按别名或 ID 查询服务器资产。"""
        ident = (alias_or_id or "").strip()
        if not ident:
            return None
        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            cur = await conn.execute(
                "SELECT * FROM server_assets WHERE alias = ? OR id = ? LIMIT 1",
                (ident, ident),
            )
            row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        cred_enc = str(d.pop("credential_enc", "") or "")
        d["has_credential"] = bool(cred_enc or d.get("key_path"))
        d["allow_write"] = bool(d.get("allow_write"))
        if include_secret and cred_enc:
            d["credential_plain"] = self.decrypt_secret(cred_enc)
        return d

    async def delete_server(self, alias_or_id: str) -> bool:
        """删除托管服务器资产。"""
        ident = (alias_or_id or "").strip()
        if not ident:
            return False
        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            cur = await conn.execute(
                "DELETE FROM server_assets WHERE alias = ? OR id = ?",
                (ident, ident),
            )
            await conn.commit()
            return cur.rowcount > 0

    # ────────────────────────── 2. SSH 远程命令与一键体检 ──────────────────────────

    @staticmethod
    def _ssh_exec_sync(srv: dict[str, Any], command: str, timeout: int = 25) -> dict[str, Any]:
        """同步执行 SSH 命令（运行在线程池，隔离异步主循环）。"""
        try:
            import paramiko  # type: ignore
        except ImportError:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "系统尚未安装 paramiko，请在环境中安装 paramiko>=3.4.0 后使用远程 SSH 运维。",
            }

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict[str, Any] = {
            "hostname": str(srv["host"]),
            "port": int(srv.get("port") or 22),
            "username": str(srv.get("username") or "root"),
            "timeout": min(12, max(3, int(timeout))),
            "banner_timeout": 12,
        }
        auth_type = str(srv.get("auth_type") or "key_path")
        secret_plain = str(srv.get("credential_plain") or "")
        key_path = str(srv.get("key_path") or "").strip()

        if auth_type == "password_enc" and secret_plain:
            connect_kwargs["password"] = secret_plain
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False
        elif auth_type == "private_key_enc" and secret_plain:
            pkey = None
            for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
                try:
                    pkey = key_cls.from_private_key(io.StringIO(secret_plain))
                    break
                except Exception:
                    continue
            if pkey is None:
                return {"success": False, "exit_code": -1, "stdout": "", "stderr": "无法解析加密存储的 SSH 私钥格式。"}
            connect_kwargs["pkey"] = pkey
        elif key_path:
            connect_kwargs["key_filename"] = key_path

        try:
            client.connect(**connect_kwargs)
            _, stdout_ch, stderr_ch = client.exec_command(command, timeout=timeout)
            exit_code = stdout_ch.channel.recv_exit_status()
            out_txt = stdout_ch.read(16384).decode("utf-8", errors="replace").strip()
            err_txt = stderr_ch.read(8192).decode("utf-8", errors="replace").strip()
            return {
                "success": exit_code == 0,
                "exit_code": exit_code,
                "stdout": out_txt[:6000],
                "stderr": err_txt[:2000],
            }
        except Exception as err:
            return {"success": False, "exit_code": -1, "stdout": "", "stderr": f"SSH 连接或执行异常: {err}"}
        finally:
            try:
                client.close()
            except Exception:
                pass

    async def run_remote_command(
        self,
        *,
        alias: str,
        command: str,
        workdir: str = "",
        timeout: int = 25,
    ) -> dict[str, Any]:
        """在指定别名托管服务器上执行命令。"""
        srv = await self.get_server(alias, include_secret=True)
        if not srv:
            return {"success": False, "error": f"未找到名为「{alias}」的托管服务器，请先前往管理页添加。"}

        eff_dir = (workdir or srv.get("default_workdir") or "").strip()
        final_cmd = f"cd {shlex.quote(eff_dir)} && {command}" if eff_dir else command
        res = await asyncio.to_thread(self._ssh_exec_sync, srv, final_cmd, timeout)
        return {"alias": srv["alias"], "host": srv["host"], "env_tier": srv["env_tier"], **res}

    @staticmethod
    def _inspect_local_node_health() -> dict[str, Any]:
        """无托管 SSH 资产时，通过 psutil 检测当前运行实例自身资源状态。"""
        import platform

        alerts: list[str] = []
        try:
            import psutil

            cpu_pct = psutil.cpu_percent(interval=0.2)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            mem_pct = round(mem.percent, 1)
            disk_pct = round(disk.percent, 1)
            if mem_pct >= 90.0:
                alerts.append(f"内存占用过高 ({mem_pct}%)")
            if disk_pct >= 85.0:
                alerts.append(f"磁盘占用过高 ({disk_pct}%)")
            summary = (
                f"📍 当前运行节点 ({platform.node() or 'local'})：\n"
                f"- CPU：{psutil.cpu_count()} 核，实时使用率 {cpu_pct}%\n"
                f"- 内存：{mem.used // (1024**2)}MB / {mem.total // (1024**2)}MB ({mem_pct}%)\n"
                f"- 磁盘：{disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB ({disk_pct}%)"
            )
            return {
                "alias": "local_node",
                "success": True,
                "has_alert": bool(alerts),
                "alerts": alerts,
                "mem_percent": mem_pct,
                "disk_percent": disk_pct,
                "summary": summary,
            }
        except Exception as err:
            return {
                "alias": "local_node",
                "success": False,
                "has_alert": False,
                "alerts": [],
                "summary": f"本机体检异常: {err}",
            }

    async def inspect_server_health(
        self,
        alias: str = "",
        mem_warn: float = 90.0,
        disk_warn: float = 85.0,
    ) -> dict[str, Any]:
        """一键体检指定服务器、全部已托管 SSH 服务器或回退本机运行节点。"""
        servers = await self.list_servers()
        if alias.strip():
            servers = [s for s in servers if s["alias"] == alias.strip() or s["id"] == alias.strip()]
            if not servers:
                return {"success": False, "has_alert": False, "summary": f"未找到托管服务器「{alias}」。"}

        if not servers:
            local_res = self._inspect_local_node_health()
            local_res["reports"] = [local_res]
            return local_res

        probe_script = (
            "echo 'HOST='$(hostname); "
            "echo 'UPTIME='$(uptime -p 2>/dev/null || uptime); "
            "echo 'LOAD='$(cat /proc/loadavg 2>/dev/null | awk '{print $1,$2,$3}'); "
            "echo 'MEM='$(free -m | awk '/Mem:/ {printf \"%s/%sMB (%.1f%%)\", $3,$2,$3*100/$2}'); "
            "echo 'DISK='$(df -h / | awk 'NR==2 {print $3\"/\"$2\" (\"$5\")\"}'); "
            "echo 'DOCKER='$(docker ps --format '{{.Names}}:{{.Status}}' 2>/dev/null | head -n 8 | tr '\\n' ';')"
        )

        reports: list[dict[str, Any]] = []
        any_alert = False
        summary_blocks: list[str] = []

        for srv in servers:
            r = await self.run_remote_command(alias=srv["alias"], command=probe_script, timeout=15)
            srv_alerts: list[str] = []
            if not r.get("success"):
                any_alert = True
                err_txt = r.get("stderr") or r.get("error") or "无法连通"
                srv_alerts.append(f"SSH 不可达: {err_txt}")
                reports.append({"alias": srv["alias"], "success": False, "has_alert": True, "alerts": srv_alerts})
                summary_blocks.append(f"🔴 **{srv['alias']}** ({srv['host']})：连接失败 — {err_txt[:120]}")
                continue

            kv: dict[str, str] = {}
            for line in str(r.get("stdout") or "").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    kv[k.strip()] = v.strip()

            mem_str = kv.get("MEM", "")
            disk_str = kv.get("DISK", "")
            m_mem = re.search(r"\((\d+(?:\.\d+)?)%\)", mem_str)
            m_disk = re.search(r"\((\d+(?:\.\d+)?)%\)", disk_str)
            mem_pct = float(m_mem.group(1)) if m_mem else 0.0
            disk_pct = float(m_disk.group(1)) if m_disk else 0.0
            if mem_pct >= mem_warn:
                srv_alerts.append(f"内存告警 {mem_pct}%")
            if disk_pct >= disk_warn:
                srv_alerts.append(f"磁盘告警 {disk_pct}%")
            if srv_alerts:
                any_alert = True

            icon = "🟠" if srv_alerts else "🟢"
            docker_part = f"\n- 容器：{kv.get('DOCKER')}" if kv.get("DOCKER") else ""
            alert_part = f"\n- ⚠️ 告警：{'；'.join(srv_alerts)}" if srv_alerts else ""
            blk = (
                f"{icon} **{srv['alias']}** ({srv['host']} · {srv['env_tier']})\n"
                f"- 负载：{kv.get('LOAD') or kv.get('UPTIME', '-')}\n"
                f"- 内存：{mem_str or '-'} | 磁盘：{disk_str or '-'}"
                f"{docker_part}{alert_part}"
            )
            reports.append(
                {
                    "alias": srv["alias"],
                    "success": True,
                    "has_alert": bool(srv_alerts),
                    "alerts": srv_alerts,
                    "mem_percent": mem_pct,
                    "disk_percent": disk_pct,
                    "raw": kv,
                }
            )
            summary_blocks.append(blk)

        return {
            "success": True,
            "has_alert": any_alert,
            "reports": reports,
            "summary": "\n\n".join(summary_blocks),
        }

    # ────────────────────────── 3. 三层 L2 门禁与防提示词注入 ──────────────────────────

    @staticmethod
    def evaluate_action_risk(
        tool_name: str,
        args: dict[str, Any],
        *,
        workspace_root: str = "",
        server_env_tier: str = "",
        server_allow_write: bool = False,
        tainted_by_untrusted: bool = False,
    ) -> tuple[str, str]:
        """评估工具调用风险等级（工具级 + 路径级 + 命令级 + 防提示词注入）。"""
        write_tools = {"write_local_file", "replace_in_file", "execute_local_command", "run_remote_ssh_command"}
        if tool_name not in write_tools:
            return "L0", "只读操作"

        # 1. 间接提示词注入防护：若同一轮读过未知网页或日志，严禁静默写改或执行命令
        if tainted_by_untrusted:
            return "L2", "本轮上下文读取过外部网页/未知内容，为防范间接提示词注入，写与执行类操作需主人二次确认"

        # 2. 路径级拦截（写/改文件）
        if tool_name in ("write_local_file", "replace_in_file"):
            raw_path = str(args.get("file_path") or args.get("path") or "").strip()
            norm_low = raw_path.replace("\\", "/").lower()
            if any(part in norm_low for part in _SENSITIVE_PATH_PARTS):
                return "L2", f"命中敏感系统配置或密钥路径 ({raw_path})"
            if workspace_root:
                try:
                    ws_resolved = Path(workspace_root).resolve()
                    target_resolved = Path(raw_path).resolve() if Path(raw_path).is_absolute() else (ws_resolved / raw_path).resolve()
                    target_resolved.relative_to(ws_resolved)
                except Exception:
                    return "L2", f"写入目标路径位于当前授权工作区之外 ({raw_path})"
            return "L1", "工作区内代码修改"

        # 3. 命令级拦截（本地/SSH Shell 命令）
        cmd_text = str(args.get("command") or "").strip()
        for pat in _HIGH_RISK_CMD_PATTERNS:
            if pat.search(cmd_text):
                return "L2", f"命中高危运维/删除/重启命令模式 ({cmd_text[:80]})"

        # 4. 生产机 / 只读服务器 SSH 严格白名单
        if tool_name == "run_remote_ssh_command":
            if server_env_tier == "prod" or not server_allow_write:
                # 拆分管道和 && 检查首命令是否在安全只读白名单内（禁止反引号/$() 命令替换逃逸）
                if "$(" in cmd_text or "`" in cmd_text:
                    return "L2", "生产/只读服务器禁止未确认的命令替换语法"
                segments = [s.strip() for s in re.split(r"[;|&\n]+", cmd_text) if s.strip()]
                all_ro = bool(segments) and all(
                    any(seg == pfx or seg.startswith(pfx + " ") for pfx in _READONLY_CMD_PREFIXES)
                    for seg in segments
                )
                if not all_ro:
                    return "L2", f"目标服务器 ({server_env_tier or '只读'}) 执行非只读排查命令需确认"
                return "L0", "托管服务器只读排查命令"

        return "L1", "常规受控操作"

    # ────────────────────────── 4. L2 二次确认挂起与执行 ──────────────────────────

    async def create_pending_approval(
        self,
        *,
        link_id: str,
        sender_id: str,
        channel_type: str,
        tool_name: str,
        action_summary: str,
        payload: dict[str, Any],
        ttl_seconds: int = 180,
    ) -> dict[str, Any]:
        """挂起一条 L2 高危操作并签发 4 位数字确认码。"""
        app_id = f"apr_{uuid.uuid4().hex[:10]}"
        code = f"{secrets.randbelow(9000) + 1000}"
        now_ts = time.time()
        exp_ts = now_ts + max(60, int(ttl_seconds))

        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            await conn.execute(
                """
                INSERT INTO im_pending_approvals (
                    id, code, link_id, sender_id, channel_type, risk_level,
                    tool_name, action_summary, payload_json, status, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'L2', ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    app_id,
                    code,
                    link_id or "",
                    sender_id,
                    channel_type or "weixin_clawbot",
                    tool_name,
                    action_summary,
                    json.dumps(payload, ensure_ascii=False),
                    exp_ts,
                    now_ts,
                    now_ts,
                ),
            )
            await conn.commit()

        return {"id": app_id, "code": code, "expires_at": exp_ts, "action_summary": action_summary}

    async def list_pending_approvals(self, limit: int = 20) -> list[dict[str, Any]]:
        """查询近期 L2 审批记录（供控制台审计查看）。"""
        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            cur = await conn.execute(
                "SELECT id, code, link_id, sender_id, risk_level, tool_name, action_summary, status, expires_at, created_at FROM im_pending_approvals ORDER BY created_at DESC LIMIT ?",
                (max(1, min(100, int(limit))),),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def resolve_approval_reply(
        self,
        *,
        sender_id: str,
        link_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        """拦截主人对 L2 高危操作的确认或取消回复；未匹配审批意图时返回 None。"""
        clean = (text or "").strip()
        if not clean:
            return None

        is_reject = clean in ("取消", "拒绝", "取消执行", "算了", "不执行")
        m_confirm = re.match(r"^(?:确认执行|同意执行|确认)(?:\s+(\d{4}))?$", clean)
        if not is_reject and not m_confirm:
            return None

        req_code = m_confirm.group(1) if m_confirm else None
        now_ts = time.time()

        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            if req_code:
                cur = await conn.execute(
                    """
                    SELECT * FROM im_pending_approvals
                    WHERE sender_id = ? AND status = 'pending' AND code = ? AND expires_at >= ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (sender_id, req_code, now_ts),
                )
            else:
                cur = await conn.execute(
                    """
                    SELECT * FROM im_pending_approvals
                    WHERE sender_id = ? AND status = 'pending' AND expires_at >= ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (sender_id, now_ts),
                )
            row = await cur.fetchone()
            if not row:
                return None

            apr = dict(row)
            if is_reject:
                await conn.execute(
                    "UPDATE im_pending_approvals SET status = 'rejected', updated_at = ? WHERE id = ?",
                    (now_ts, apr["id"]),
                )
                await conn.commit()
                return {
                    "handled": True,
                    "approved": False,
                    "reply_text": f"已取消高危操作（确认码 {apr['code']}）：{apr['action_summary']}",
                }

            await conn.execute(
                "UPDATE im_pending_approvals SET status = 'executed', updated_at = ? WHERE id = ?",
                (now_ts, apr["id"]),
            )
            await conn.commit()

        try:
            payload = json.loads(apr.get("payload_json") or "{}")
        except Exception:
            payload = {}

        t_name = str(apr["tool_name"])
        exec_summary = await self._execute_approved_payload(t_name, payload)
        return {
            "handled": True,
            "approved": True,
            "reply_text": f"✅ 已二次确认并执行（{apr['code']}）：\n{exec_summary}",
        }

    async def _execute_approved_payload(self, tool_name: str, payload: dict[str, Any]) -> str:
        """执行已获主人二次确认的高危载荷。"""
        if tool_name == "run_remote_ssh_command":
            res = await self.run_remote_command(
                alias=str(payload.get("alias") or ""),
                command=str(payload.get("command") or ""),
                workdir=str(payload.get("workdir") or ""),
                timeout=int(payload.get("timeout") or 25),
            )
            if res.get("success"):
                return (res.get("stdout") or "命令执行成功（无输出）")[:1200]
            return f"执行失败 (code={res.get('exit_code')}): {res.get('stderr') or res.get('error')}"

        if tool_name == "execute_local_command":
            import subprocess

            cmd = str(payload.get("command") or "")
            cwd = str(payload.get("cwd") or "").strip() or None
            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            return f"退出码 {proc.returncode}\n{out.strip()[:1200]}"

        if tool_name == "write_local_file":
            f_path = Path(str(payload.get("file_path") or ""))
            content = str(payload.get("content") or "")
            f_path.parent.mkdir(parents=True, exist_ok=True)
            f_path.write_text(content, encoding="utf-8")
            return f"已写入文件 {f_path}（{len(content)} 字符）"

        if tool_name == "replace_in_file":
            f_path = Path(str(payload.get("file_path") or ""))
            target = str(payload.get("target") or "")
            replacement = str(payload.get("replacement") or "")
            raw = f_path.read_text(encoding="utf-8")
            if target not in raw:
                return f"替换失败：文件中未找到目标片段 ({f_path})"
            f_path.write_text(raw.replace(target, replacement, 1), encoding="utf-8")
            return f"已替换更新文件 {f_path}"

        return f"已放行未知工具载荷: {tool_name}"


server_ops_service = ServerOpsService()

