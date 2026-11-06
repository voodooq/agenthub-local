"""
审计日志服务（Audit Logger）
记录所有工具调用、账号切换、确认操作，不含令牌与敏感内容。
日志写入 SQLite audit_log 表，前端可查阅。
"""
import json
from datetime import datetime, timezone
from ..core.logging_config import logger


class AuditService:
    """审计日志服务"""

    @staticmethod
    async def log(
        event_type: str,
        actor: str,
        detail: dict,
        account_name: str | None = None,
        approved: bool | None = None,
    ) -> None:
        """
        写入一条审计日志。
        Args:
            event_type: 事件类型，如 tool_call / account_switch / login / permission_denied
            actor: 操作发起方（如 user / system / agent:<alias>）
            detail: 事件详情（不含令牌/密钥）
            account_name: 关联的账号别名（可选）
            approved: 若为工具调用确认，记录是否批准（None=不适用）
        """
        # 过滤 detail 中的敏感 key
        safe_detail = {
            k: v for k, v in detail.items()
            if k.lower() not in {"token", "secret", "password", "key", "credential", "auth"}
        }
        try:
            from ..repository.db import db_manager
            async with db_manager.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_log (event_type, actor, account_name, detail, approved, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_type,
                        actor,
                        account_name,
                        json.dumps(safe_detail, ensure_ascii=False),
                        1 if approved is True else (0 if approved is False else None),
                        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    )
                )
                await conn.commit()
        except Exception as e:
            # NOTE: 审计失败不影响主流程，仅打印警告
            logger.warning(f"审计日志写入失败: {e}")


audit_service = AuditService()

