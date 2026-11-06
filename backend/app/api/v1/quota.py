"""
额度监控面板接口（T6）
提供各账号本地统计（请求数/token/冷却状态）及 429 事件记录。
NOTE: Google 未提供机读的真实剩余额度接口，
      所有百分比均为本地估算值，前端必须标注"估算"。
"""
from fastapi import APIRouter
from ...repository.account_repository import AccountRepository
from ...service.quota_monitor import quota_monitor

router = APIRouter(prefix="/quota", tags=["Quota"])


@router.get("/stats")
async def get_quota_stats():
    """
    获取所有账号的额度水位统计。
    返回数据全部为本地估算（基于请求计数），
    字段 is_estimated=True 表示非 Google 官方实时值。
    """
    accounts = await AccountRepository.get_all()
    items = []

    for acc in accounts:
        tracker = quota_monitor.get_tracker(acc.name, acc.rpm_limit)
        current_rpm = tracker.current_rpm
        rpm_pct = min(100, round((current_rpm / max(1, acc.rpm_limit)) * 100))
        rpd_pct = min(100, round((acc.today_used / max(1, acc.rpd_limit)) * 100, 1))

        items.append({
            "id": acc.id,
            "name": acc.name,
            "auth_type": acc.auth_type,
            "cli_home_dir": acc.cli_home_dir,
            "email": acc.email,
            "avatar_url": acc.avatar_url,
            "status": acc.status,
            "cooldown_until": acc.cooldown_until,
            "priority": acc.priority,
            # --- 本地估算值 ---
            "is_estimated": True,              # 告知前端：非官方实时额度
            "current_rpm": current_rpm,
            "rpm_limit": acc.rpm_limit,
            "rpm_pct": rpm_pct,
            "today_used": acc.today_used,
            "rpd_limit": acc.rpd_limit,
            "rpd_pct": rpd_pct,
            "total_tokens": acc.total_tokens,
            "last_latency_ms": tracker.last_latency_ms,
            "is_warning": tracker.is_warning,
            "is_exhausted": tracker.is_exhausted,
        })

    total_used = sum(a.today_used for a in accounts)
    total_tokens = sum(a.total_tokens for a in accounts)
    active_cnt = sum(1 for a in accounts if a.status == "active")
    cooldown_cnt = sum(1 for a in accounts if a.status == "cooldown")

    return {
        "accounts": items,
        "summary": {
            "total_accounts": len(accounts),
            "active_accounts": active_cnt,
            "cooldown_accounts": cooldown_cnt,
            "total_today_used": total_used,
            "total_tokens_all": total_tokens,
            "is_estimated": True,
        }
    }


@router.post("/{account_id}/reset-daily")
async def reset_daily_usage(account_id: int):
    """手动重置指定账号的今日用量计数（每日自动重置未实现时使用）"""
    from ...repository.db import db_manager
    async with db_manager.get_connection() as conn:
        await conn.execute(
            "UPDATE accounts SET today_used = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (account_id,)
        )
        await conn.commit()
    return {"message": "今日用量已重置", "account_id": account_id}

