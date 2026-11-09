"""
Antigravity IDE 本地凭据管理与无缝切号服务
负责宿主机 Antigravity 安装与账号配置文件探测、本地账号同步与一键切号
支持宿主机原生运行及 Docker 容器挂载探测
"""
import os
import json
import shutil
from typing import Any
from ..core.logging_config import logger
from ..repository.account_repository import AccountRepository
from ..schema.account_schema import AntigravityDetectResponse


class AntigravityService:
    """Antigravity IDE 本地联动服务"""

    def __init__(self) -> None:
        self.user_home = os.path.expanduser("~")
        self.appdata = os.environ.get("APPDATA", os.path.join(self.user_home, "AppData", "Roaming"))
        self.known_install_paths = [
            r"D:\Programs\Antigravity IDE\bin",
            r"D:\Programs\Antigravity\bin",
            r"C:\Program Files\Antigravity\bin",
            os.path.join(self.user_home, "AppData", "Local", "Programs", "Antigravity", "bin"),
            "/mnt/d/Programs/Antigravity IDE/bin",
            "/mnt/d/Programs/Antigravity/bin",
            os.path.expanduser("~/.gemini/bin"),
        ]

    def _resolve_accounts_json_path(self) -> str:
        """智能解析宿主机或容器内的 google_accounts.json 真实路径"""
        candidates = [
            "/root/.gemini/google_accounts.json",
            os.path.join(self.user_home, ".gemini", "google_accounts.json"),
            "/host_gemini/google_accounts.json"
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return os.path.join(self.user_home, ".gemini", "google_accounts.json")

    def detect_environment(self) -> AntigravityDetectResponse:
        """
        探测本地 Antigravity IDE 运行环境及账号凭据状态
        :returns: AntigravityDetectResponse 探测结构
        """
        installed_path = None
        for p in self.known_install_paths:
            if os.path.exists(p):
                installed_path = p
                break

        accounts_json_path = self._resolve_accounts_json_path()
        accounts_file_exists = os.path.exists(accounts_json_path)
        active_email = None
        old_emails: list[str] = []

        if accounts_file_exists:
            try:
                with open(accounts_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    active_email = data.get("active")
                    old_emails = data.get("old", [])
            except Exception as e:
                logger.error(f"读取 google_accounts.json 异常: {e}")

        total_accounts = (1 if active_email else 0) + len(old_emails)

        detected = bool(installed_path or accounts_file_exists)

        return AntigravityDetectResponse(
            detected=detected,
            installed_path=installed_path or "D:\\Programs\\Antigravity IDE\\bin (本机)",
            accounts_file_path=accounts_json_path if accounts_file_exists else None,
            active_email=active_email,
            old_emails=old_emails,
            total_accounts=total_accounts,
            database_path=None,
            database_exists=False
        )

    async def sync_local_accounts(self, import_active: bool = True, import_old: bool = True) -> dict[str, Any]:
        """
        扫描本地 google_accounts.json 并一键同步导入到 AgentHub 数据库中
        """
        accounts_json_path = self._resolve_accounts_json_path()
        if not os.path.exists(accounts_json_path):
            return {"success": False, "message": f"未找到本地 Antigravity 账号配置 ({accounts_json_path})"}

        with open(accounts_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        active_email = data.get("active")
        old_emails = data.get("old", [])

        targets: list[tuple[str, bool]] = []
        if import_active and active_email:
            targets.append((active_email, True))
        if import_old and old_emails:
            for em in old_emails:
                if em != active_email:
                    targets.append((em, False))

        synced_count = 0
        for email, is_active in targets:
            existing = await AccountRepository.get_by_email(email)
            if not existing:
                account_data = {
                    "name": f"AG-{email.split('@')[0]}",
                    "auth_type": "oauth_google_one",
                    "credentials": "PENDING_TOKEN_SYNC",
                    "refresh_token": None,
                    "expires_at": None,
                    "email": email,
                    "avatar_url": None,
                    "priority": 1 if is_active else 2,
                    "rpm_limit": 60,
                    "rpd_limit": 5000,
                    "extra_meta": json.dumps({"source": "antigravity_sync", "is_ide_active": is_active})
                }
                await AccountRepository.create(account_data)
                synced_count += 1
            else:
                logger.info(f"本地账号 [{email}] 已在算力池中存在，跳过新建")

        return {
            "success": True,
            "message": f"成功同步 {synced_count} 个 Antigravity 本地账号节点",
            "active_email": active_email,
            "synced_count": synced_count
        }

    async def switch_active_account(self, target_email: str, sync_priority: bool = True) -> dict[str, Any]:
        """
        一键切换 Antigravity 当前使用的活跃账号（热更新 google_accounts.json）
        并同步将 AgentHub 内部对应账号的调度优先级设为 1
        """
        accounts_json_path = self._resolve_accounts_json_path()
        if not os.path.exists(accounts_json_path):
            raise FileNotFoundError(f"配置文件不存在: {accounts_json_path}")

        # 1. 备份原配置
        backup_path = accounts_json_path + ".bak"
        try:
            shutil.copyfile(accounts_json_path, backup_path)
            logger.info(f"已创建配置文件安全备份: {backup_path}")
        except Exception as e:
            logger.warning(f"备份失败: {e}")

        # 2. 读取当前内容
        with open(accounts_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        prev_active = data.get("active")
        old_list: list[str] = data.get("old", [])

        if prev_active == target_email:
            logger.info(f"账号 [{target_email}] 已是当前活跃账号")
        else:
            if prev_active and prev_active not in old_list:
                old_list.append(prev_active)
            if target_email in old_list:
                old_list.remove(target_email)

            data["active"] = target_email
            data["old"] = old_list

            # 3. 写回配置
            with open(accounts_json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info(f"Antigravity IDE 活跃账号已切换: [{prev_active}] -> [{target_email}]")

        # 4. 同步提升 AgentHub 内部该账号的优先级为 1 (Top Priority)
        if sync_priority:
            account = await AccountRepository.get_by_email(target_email)
            if account and account.id:
                await AccountRepository.update(account.id, {"priority": 1})
                logger.info(f"已将 AgentHub 账号 [{account.name}] 设为主力优先级 1")

        return {
            "success": True,
            "target_email": target_email,
            "previous_active": prev_active,
            "message": f"成功将 Antigravity IDE 活跃账号切换为: {target_email}"
        }


antigravity_service = AntigravityService()
