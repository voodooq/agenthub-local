"""
账号管理接口
"""
from typing import Any
from fastapi import APIRouter, HTTPException, Path
from ...schema.account_schema import (
    AccountResponse, AccountStatusPill,
    AntigravityDetectResponse,
)
from ...repository.account_repository import AccountRepository
from ...service.quota_monitor import quota_monitor
from ...service.antigravity_service import antigravity_service
from ...service.audit_service import audit_service
from ...core.logging_config import logger

router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.get("", response_model=list[AccountResponse])
async def list_accounts():
    """获取所有账号列表"""
    accounts = await AccountRepository.get_all()
    return [
        AccountResponse(
            id=acc.id or 0,
            name=acc.name,
            auth_type=acc.auth_type,
            priority=acc.priority,
            status=acc.status,
            cooldown_until=acc.cooldown_until,
            rpm_limit=acc.rpm_limit,
            rpd_limit=acc.rpd_limit,
            today_used=acc.today_used,
            total_tokens=acc.total_tokens,
            updated_at=acc.updated_at,
            email=acc.email,
            avatar_url=acc.avatar_url,
            cli_home_dir=acc.cli_home_dir,
        )
        for acc in accounts
    ]


@router.get("/dashboard/stats")
async def get_dashboard_stats():
    """获取所有账号详细额度水位指标（仪表盘看板使用）"""
    accounts = await AccountRepository.get_all()
    items = []
    total_today_used = 0
    total_tokens_all = 0
    active_count = 0
    cooldown_count = 0

    for acc in accounts:
        tracker = quota_monitor.get_tracker(acc.name, acc.rpm_limit)
        current_rpm = tracker.current_rpm
        rpm_percent = min(100, round((current_rpm / max(1, acc.rpm_limit)) * 100))
        rpd_percent = min(100, round((acc.today_used / max(1, acc.rpd_limit)) * 100, 1))

        if acc.status == "active":
            active_count += 1
        elif acc.status == "cooldown":
            cooldown_count += 1

        total_today_used += acc.today_used
        total_tokens_all += acc.total_tokens

        items.append({
            "id": acc.id,
            "name": acc.name,
            "auth_type": acc.auth_type,
            "email": acc.email,
            "avatar_url": acc.avatar_url,
            "cli_home_dir": acc.cli_home_dir,
            "priority": acc.priority,
            "status": acc.status,
            "cooldown_until": acc.cooldown_until,
            "current_rpm": current_rpm,
            "rpm_limit": acc.rpm_limit,
            "rpm_percent": rpm_percent,
            "today_used": acc.today_used,
            "rpd_limit": acc.rpd_limit,
            "rpd_percent": rpd_percent,
            "total_tokens": acc.total_tokens,
            "last_latency_ms": tracker.last_latency_ms,
            "is_warning": tracker.is_warning,
            "is_exhausted": tracker.is_exhausted
        })

    return {
        "accounts": items,
        "summary": {
            "total_accounts": len(accounts),
            "active_accounts": active_count,
            "cooldown_accounts": cooldown_count,
            "total_today_used": total_today_used,
            "total_tokens_all": total_tokens_all
        }
    }


@router.post("/{account_id}/test")
async def test_account(account_id: int = Path(...)):
    """测试指定账号连通性（自适应 CLI 原生引擎与 API Key 验证）"""
    from ...service.cli_account_service import is_account_logged_in
    target = await AccountRepository.get_by_id(account_id)
    if not target:
        raise HTTPException(status_code=404, detail="账号不存在")

    if target.auth_type == "api_key":
        import httpx
        from ...core.proxy_helper import get_system_proxy_url
        proxy = get_system_proxy_url()
        client_kwargs: dict[str, Any] = {"timeout": 10.0}
        if proxy:
            client_kwargs["proxy"] = proxy
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={target.credentials}"
                )
                if resp.status_code == 200:
                    if target.status != "active":
                        await AccountRepository.recover_active(account_id)
                    return {
                        "account_id": account_id,
                        "name": target.name,
                        "logged_in": True,
                        "status": "active",
                        "message": "官方 API Key 连通性测试 100% 成功，已恢复就绪！"
                    }
                else:
                    return {
                        "account_id": account_id,
                        "name": target.name,
                        "logged_in": False,
                        "status": target.status,
                        "message": f"API Key 验证未通过 ({resp.status_code}): {resp.text[:120]}"
                    }
        except Exception as e:
            return {
                "account_id": account_id,
                "name": target.name,
                "logged_in": False,
                "status": target.status,
                "message": f"连接 Google AI Studio 异常: {str(e)}"
            }

    if target.auth_type in ("oauth_google_one", "oauth_personal"):
        from ...service.gemini_client import GeminiClient
        from ...service.oauth_service import oauth_service
        from ...repository.db import db_manager
        try:
            valid_token = await oauth_service.ensure_valid_token(target)
            probe = await GeminiClient.test_key(
                api_key=valid_token,
                model_name="gemini-2.5-flash",
                auth_type="oauth_google_one",
                strict_model=False,
            )
            if probe.get("valid"):
                if target.status != "active":
                    await AccountRepository.recover_active(account_id)
                # 同步解除 llm_providers 中对应 OAuth 通道的冷却状态
                if target.email:
                    safe_slug = target.email.split("@")[0].replace(".", "_")
                    async with db_manager.get_connection() as conn:
                        await conn.execute(
                            "UPDATE llm_providers SET cooldown_until = NULL, is_enabled = 1 WHERE id = ?",
                            (f"google_oauth_{safe_slug}",),
                        )
                        await conn.commit()
                return {
                    "account_id": account_id,
                    "name": target.name,
                    "logged_in": True,
                    "status": "active",
                    "message": f"Google OAuth 算力通道连通性测试 100% 成功（延迟 {probe.get('latency_ms', 0)}ms），已恢复就绪！"
                }
            else:
                return {
                    "account_id": account_id,
                    "name": target.name,
                    "logged_in": False,
                    "status": target.status,
                    "message": probe.get("error") or "OAuth 令牌验证未通过，请重新发起 Google OAuth 授权"
                }
        except Exception as e:
            return {
                "account_id": account_id,
                "name": target.name,
                "logged_in": False,
                "status": target.status,
                "message": f"Google OAuth 连通性探测异常: {str(e)}"
            }

    # 默认针对 gemini_cli
    logged_in = is_account_logged_in(target.name)
    current_status = target.status
    if logged_in and current_status != "active":
        await AccountRepository.recover_active(account_id)
        current_status = "active"

    return {
        "account_id": account_id,
        "name": target.name,
        "logged_in": logged_in,
        "status": current_status,
        "message": "原生 CLI 引擎凭据就绪，已处于活跃就绪状态！" if logged_in else "未检测到有效认证，请点击重新授权"
    }


@router.post("/{account_id}/recover")
async def recover_account(account_id: int = Path(...)):
    """手动解除账号冷却，立即恢复为 active 状态"""
    target = await AccountRepository.get_by_id(account_id)
    if not target:
        raise HTTPException(status_code=404, detail="账号不存在")

    await AccountRepository.recover_active(account_id)
    await audit_service.log(
        event_type="account_recover",
        actor="user",
        detail={"action": "manual_recover", "account_id": account_id},
        account_name=target.name,
        approved=True
    )
    return {
        "account_id": account_id,
        "name": target.name,
        "status": "active",
        "message": f"账号 [{target.name}] 已手动解除冷却，恢复就绪"
    }


@router.delete("/purge/all")
async def purge_all_accounts():
    """彻底清空算力池所有账号：清理数据库、删除全部本地目录、清空配额与缓存"""
    from ...service.cli_account_service import delete_account_dir, ACCOUNTS_ROOT
    from ...repository.db import db_manager
    import shutil
    
    accounts = await AccountRepository.get_all()
    for acc in accounts:
        delete_account_dir(acc.name)
        quota_monitor.remove_tracker(acc.name)
        if acc.id:
            await AccountRepository.delete(acc.id)

    async with db_manager.get_connection() as conn:
        await conn.execute("DELETE FROM llm_providers WHERE provider_type = 'google_oauth'")
        await conn.commit()
            
    # 清理所有孤儿目录
    if ACCOUNTS_ROOT.exists():
        for item in ACCOUNTS_ROOT.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
                
    quota_monitor.clear_all()
    return {"message": "算力池所有账号、本地目录与缓存已全部彻底清除！"}


@router.delete("/{account_id}")
async def delete_account(account_id: int):
    """彻底删除单个账号：删除数据库记录、删除专属目录及配置文件、清理内存追踪缓存"""
    from ...service.cli_account_service import delete_account_dir
    from ...repository.db import db_manager
    account = await AccountRepository.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    # 1. 彻底删除本地账号物理目录
    delete_account_dir(account.name)

    # 2. 清理内存水位监控缓存
    quota_monitor.remove_tracker(account.name)

    # 3. 数据库删除（含同步清理 llm_providers 中的 Google OAuth 通道）
    await AccountRepository.delete(account_id)
    if account.email:
        safe_slug = account.email.split("@")[0].replace(".", "_")
        async with db_manager.get_connection() as conn:
            await conn.execute("DELETE FROM llm_providers WHERE id = ?", (f"google_oauth_{safe_slug}",))
            await conn.commit()
    await audit_service.log(
        event_type="account_delete",
        actor="user",
        detail={"account_id": account_id, "name": account.name},
        account_name=account.name,
        approved=True
    )
    return {"message": f"账号 [{account.name}] 及其本地文件与缓存已彻底清除"}


@router.get("/status/pill", response_model=AccountStatusPill)
@router.get("/status-pill", response_model=AccountStatusPill)
async def get_status_pill():
    """获取顶栏状态胶囊轻量数据（带高鲁棒性容错）"""
    try:
        candidate = await AccountRepository.get_active_candidate()
        if not candidate:
            return AccountStatusPill(
                active_account_name="无可用账号 (All in Cooldown)",
                auth_type="none",
                status="exhausted",
                today_used=0,
                current_rpm=0,
                rpm_limit=0,
                is_warning=True
            )

        tracker = quota_monitor.get_tracker(candidate.name, candidate.rpm_limit)
        return AccountStatusPill(
            active_account_name=candidate.name,
            auth_type=candidate.auth_type,
            status=candidate.status,
            today_used=candidate.today_used,
            current_rpm=tracker.current_rpm,
            rpm_limit=candidate.rpm_limit,
            is_warning=tracker.is_warning,
            email=candidate.email,
            avatar_url=candidate.avatar_url
        )
    except Exception as e:
        logger.warning(f"获取状态胶囊异常 (已自动容错兜底): {e}")
        return AccountStatusPill(
            active_account_name="正在连接中...",
            auth_type="oauth_google_one",
            status="active",
            today_used=0,
            current_rpm=0,
            rpm_limit=15,
            is_warning=False
        )


# =========================================================================
# Gemini CLI 账号管理路由（T3）
# =========================================================================
from ...service.cli_account_service import cli_account_service, is_account_logged_in
from pydantic import BaseModel as _Base, Field as _Field


class GeminiCliAccountAddRequest(_Base):
    """添加新 Gemini CLI 账号请求"""
    alias: str = _Field(..., description="账号别名，唯一，仅含字母数字下划线连字符")
    email: str | None = _Field(default=None, description="Google 邮箱（可选，登录后自动更新）")
    priority: int = _Field(default=1, description="调度优先级")


class GeminiCliSubmitCodeRequest(_Base):
    """提交 Google 授权码请求"""
    alias: str = _Field(..., description="账号别名")
    code: str = _Field(..., description="Google 页面复制的授权代码 (形如 4/0A...)")


class CliTerminalOpenRequest(_Base):
    """呼出桌面终端请求"""
    alias: str = _Field(..., description="账号别名")


class ApiKeyAddRequest(_Base):
    """添加 Google AI Studio 官方免费 API Key 请求"""
    name: str = _Field(..., description="账号别名（唯一，例如 my-gmail-key）")
    api_key: str = _Field(..., description="官方免费 API Key (以 AIzaSy 开头)")
    priority: int = _Field(default=1, description="调度优先级")


@router.post("/api-key/add")
async def add_api_key_account(req: ApiKeyAddRequest):
    """添加官方免费个人 API Key 账号入池"""
    key = req.api_key.strip()
    name = req.name.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API Key 不能为空")
    if not key.startswith("AIzaSy"):
        raise HTTPException(status_code=400, detail="Google AI Studio 官方 API Key 通常以 'AIzaSy' 开头，请核对您复制的密钥")

    existing = await AccountRepository.get_by_name(name)
    if existing:
        raise HTTPException(status_code=400, detail=f"账号别名 [{name}] 已存在，请换一个名称")

    acc_id = await AccountRepository.create({
        "name": name,
        "auth_type": "api_key",
        "credentials": key,
        "priority": req.priority,
        "rpm_limit": 15,
        "rpd_limit": 1500,
        "email": f"{name}@aistudio.google",
        "extra_meta": '{"source": "ai_studio_api_key"}'
    })

    await audit_service.log(
        event_type="account_add",
        actor="user",
        detail={"name": name, "auth_type": "api_key", "priority": req.priority},
        account_name=name,
        approved=True
    )

    return {
        "account_id": acc_id,
        "name": name,
        "message": f"官方免费 API Key 账号 [{name}] 已成功加入算力池！"
    }


@router.post("/gemini-cli/add")
async def add_gemini_cli_account(req: GeminiCliAccountAddRequest):
    """添加新 Google 账号：创建目录并拉起官方 Gemini CLI 登录授权"""
    try:
        result = await cli_account_service.add_account(
            alias=req.alias,
            email=req.email,
            priority=req.priority
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        err_msg = str(e) or type(e).__name__
        logger.error(f"添加账号失败: {err_msg}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"添加账号失败: {err_msg}")


@router.get("/gemini-cli/login-status/{alias}")
async def check_cli_login_status(alias: str):
    """轮询查询指定账号的 Gemini CLI 登录状态与授权指引"""
    from ...service.cli_account_service import get_account_auth_info
    logged_in = is_account_logged_in(alias)
    info = get_account_auth_info(alias)
    return {
        "alias": alias,
        "logged_in": logged_in,
        **info
    }


@router.post("/gemini-cli/submit-code")
async def submit_cli_auth_code(req: GeminiCliSubmitCodeRequest):
    """向等待中的 CLI 登录进程标准输入发送 Google 授权代码"""
    success, msg = await cli_account_service.submit_auth_code(req.alias, req.code)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@router.post("/gemini-cli/import-system")
async def import_system_gemini_cli():
    """一键纳管宿主机已安装且已登录的官方 Antigravity / Gemini CLI 原生引擎"""
    try:
        result = await cli_account_service.import_system_cli_account()
        return result
    except Exception as e:
        logger.error(f"纳管本机 Antigravity 原生引擎失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gemini-cli/open-terminal")
async def open_cli_terminal(req: CliTerminalOpenRequest):
    """呼出系统交互式终端完成官方客户端授权"""
    from ...service.cli_account_service import open_interactive_terminal
    try:
        success = open_interactive_terminal(req.alias)
        return {"success": success, "message": "已在系统桌面呼出 PowerShell 授权窗口，请在窗口中按回车即可完成认证"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gemini-cli/relogin/{account_id}")
async def relogin_gemini_cli_account(account_id: int = Path(...)):
    """为已存在账号重新拉起 Gemini CLI 登录授权（令牌失效时使用）"""
    from ...service.cli_account_service import launch_cli_login
    account = await AccountRepository.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    result = await launch_cli_login(account.name)
    return result


@router.delete("/{account_id}/gemini-cli")
async def remove_gemini_cli_account(account_id: int = Path(...)):
    """删除账号并清理其 Gemini CLI 本地目录"""
    try:
        await cli_account_service.remove_account(account_id)
        return {"message": "账号及本地目录已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
