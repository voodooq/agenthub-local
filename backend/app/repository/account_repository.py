"""
账号仓储层 (Account Repository)
封装所有针对 accounts 表的底层 CRUD 与原子状态操作，严禁在 API 层直接执行 SQL
"""
from datetime import datetime, timezone
from typing import Any
from ..model.models import AccountModel
from .db import db_manager


class AccountRepository:
    """账号数据访问对象"""
    
    @staticmethod
    async def get_all() -> list[AccountModel]:
        """获取所有账号（按优先级升序排序）"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM accounts ORDER BY priority ASC, id ASC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [AccountModel.from_row(row) for row in rows]

    @staticmethod
    async def get_by_id(account_id: int) -> AccountModel | None:
        """通过 ID 查找账号"""
        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)) as cursor:
                row = await cursor.fetchone()
                return AccountModel.from_row(row) if row else None

    @staticmethod
    async def get_active_candidate() -> AccountModel | None:
        """
        获取当前最优可用账号（状态活跃，或冷却时间已过）
        """
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        async with db_manager.get_connection() as conn:
            # 先做轻量只读检查，仅在有到期需要解冻的账号时才申请写锁
            async with conn.execute(
                "SELECT 1 FROM accounts WHERE status = 'cooldown' AND cooldown_until <= ? LIMIT 1",
                (now_str,)
            ) as check_cursor:
                need_unlock = await check_cursor.fetchone()

            if need_unlock:
                await conn.execute(
                    "UPDATE accounts SET status = 'active', cooldown_until = NULL "
                    "WHERE status = 'cooldown' AND cooldown_until <= ?",
                    (now_str,)
                )
                await conn.commit()

            async with conn.execute(
                "SELECT * FROM accounts WHERE status = 'active' "
                "ORDER BY priority ASC, today_used ASC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return AccountModel.from_row(row)
                return None

    @staticmethod
    async def get_by_name(name: str) -> AccountModel | None:
        """通过名称查找账号"""
        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT * FROM accounts WHERE name = ?", (name,)) as cursor:
                row = await cursor.fetchone()
                return AccountModel.from_row(row) if row else None

    @staticmethod
    async def get_by_email(email: str) -> AccountModel | None:
        """通过 Google 邮箱查找已绑定的账号"""
        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)) as cursor:
                row = await cursor.fetchone()
                return AccountModel.from_row(row) if row else None

    @staticmethod
    async def create(data: dict[str, Any]) -> int:
        """创建新账号并返回新生成的 ID"""
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO accounts (
                    name, auth_type, credentials, priority, rpm_limit, rpd_limit,
                    email, avatar_url, extra_meta, cli_home_dir, refresh_token, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["name"],
                    data.get("auth_type", "gemini_cli"),
                    data["credentials"],
                    data.get("priority", 1),
                    data.get("rpm_limit", 15),
                    data.get("rpd_limit", 1500),
                    data.get("email"),
                    data.get("avatar_url"),
                    data.get("extra_meta"),
                    data.get("cli_home_dir"),
                    data.get("refresh_token"),
                    data.get("expires_at")
                )
            )
            await conn.commit()
            return cursor.lastrowid or 0

    @staticmethod
    async def mark_cooldown(account_name: str, cooldown_until: str) -> None:
        """将指定账号标记为冷却中"""
        async with db_manager.get_connection() as conn:
            await conn.execute(
                "UPDATE accounts SET status = 'cooldown', cooldown_until = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (cooldown_until, account_name)
            )
            await conn.commit()

    @staticmethod
    async def recover_active(account_id: int) -> bool:
        """手动或探测成功后解除冷却状态，恢复为 active"""
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute(
                "UPDATE accounts SET status = 'active', cooldown_until = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (account_id,)
            )
            await conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def record_usage(account_name: str, tokens_spent: int = 0) -> None:
        """原子累加该账号今日调用频次与 Token 消耗"""
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE accounts 
                SET today_used = today_used + 1,
                    total_tokens = total_tokens + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE name = ?
                """,
                (tokens_spent, account_name)
            )
            await conn.commit()

    @staticmethod
    async def delete(account_id: int) -> bool:
        """删除指定账号"""
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            await conn.commit()
            return cursor.rowcount > 0
    @staticmethod
    async def update_oauth_token(
        account_id: int,
        access_token: str,
        expires_at: int,
        refresh_token: str | None = None
    ) -> None:
        """更新指定账号的 OAuth Access Token 与过期时间"""
        async with db_manager.get_connection() as conn:
            if refresh_token:
                await conn.execute(
                    "UPDATE accounts SET credentials = ?, refresh_token = ?, expires_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (access_token, refresh_token, expires_at, account_id)
                )
            else:
                await conn.execute(
                    "UPDATE accounts SET credentials = ?, expires_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (access_token, expires_at, account_id)
                )
            await conn.commit()
