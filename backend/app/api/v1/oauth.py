"""
Google OAuth 2.0 合规企业接入路由接口
提供自建 OAuth 客户端授权跳转、认证回调处理、配置更新与手动刷新
"""
import html
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Path
from fastapi.responses import HTMLResponse
from ...schema.account_schema import OAuthUrlResponse, OAuthConfigRequest
from ...service.oauth_service import oauth_service
from ...repository.account_repository import AccountRepository
from ...core.logging_config import logger

router = APIRouter(prefix="/accounts/oauth", tags=["OAuth"])


@router.get("/google/url", response_model=OAuthUrlResponse)
async def get_google_auth_url() -> OAuthUrlResponse:
    """
    获取 Google Cloud 自建企业 OAuth 2.0 授权跳转 URL
    若尚未在后台配置企业 Client ID，则阻断并引导配置
    """
    if not oauth_service.is_configured():
        raise HTTPException(
            status_code=400,
            detail="尚未配置自建 Google Cloud OAuth Client ID / Secret，请先在面板「企业 OAuth」中完成配置"
        )

    try:
        auth_url, state = oauth_service.generate_auth_url()
        return OAuthUrlResponse(
            authorization_url=auth_url,
            state=state,
            client_id_configured=True
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/callback", response_class=HTMLResponse)
async def google_oauth_callback(
    code: str = Query(None, description="Google 授权码"),
    state: str = Query(None, description="防 CSRF state 凭据"),
    error: str = Query(None, description="Google 授权错误信息")
) -> HTMLResponse:
    """
    接收 Google OAuth 2.0 回调并完成账号绑定
    返回携带 postMessage 通信脚本的现代化深色弹窗页面
    """
    if error:
        safe_err = html.escape(str(error))
        logger.warning(f"Google 授权取消或拒绝: {safe_err}")
        return HTMLResponse(content=f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="utf-8">
            <title>Google 授权取消</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    background: #131314;
                    color: #e3e3e3;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                }}
                .card {{
                    background: #1e1f20;
                    padding: 32px;
                    border-radius: 16px;
                    border: 1px solid #3c4043;
                    text-align: center;
                    max-width: 420px;
                }}
                .err {{ color: #f28b82; font-size: 18px; font-weight: 600; }}
                .sub {{ color: #9aa0a6; font-size: 13px; margin-top: 10px; }}
            </style>
        </head>
        <body>
            <div class="card">
                <div class="err">授权未完成或已取消</div>
                <div class="sub">{safe_err}</div>
            </div>
            <script>
                if (window.opener) {{
                    window.opener.postMessage({{ type: 'GOOGLE_OAUTH_FAILED', error: '{safe_err}' }}, '*');
                }}
                setTimeout(() => window.close(), 2500);
            </script>
        </body>
        </html>
        """, status_code=200)

    if not code:
        raise HTTPException(status_code=400, detail="未收到有效的 authorization code")

    try:
        tokens = await oauth_service.exchange_code_for_tokens(code, state=state)
        user_info = await oauth_service.fetch_user_info(tokens["access_token"])
        account = await oauth_service.bind_account(tokens, user_info)
        
        email = account.email or "Google 账号"
        account_name = account.name
        safe_email = html.escape(str(email))
        safe_name = html.escape(str(account_name))

        return HTMLResponse(content=f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="utf-8">
            <title>Google 账号绑定成功</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    background: #131314;
                    color: #e3e3e3;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                }}
                .card {{
                    background: #1e1f20;
                    padding: 36px;
                    border-radius: 20px;
                    border: 1px solid #3c4043;
                    text-align: center;
                    max-width: 440px;
                    box-shadow: 0 12px 32px rgba(0,0,0,0.6);
                }}
                .icon {{
                    width: 56px;
                    height: 56px;
                    margin: 0 auto 16px auto;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    background: rgba(52, 168, 83, 0.15);
                    border-radius: 50%;
                }}
                .title {{ font-size: 20px; font-weight: 600; color: #c4eed0; }}
                .sub {{ color: #9aa0a6; font-size: 14px; margin-top: 8px; line-height: 1.5; }}
                .email-tag {{
                    display: inline-block;
                    margin-top: 14px;
                    background: #282a2c;
                    padding: 6px 14px;
                    border-radius: 20px;
                    font-size: 13px;
                    color: #a8c7fa;
                    border: 1px solid #3c4043;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <div class="icon">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#34a853" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                </div>
                <div class="title">Google 账号授权绑定成功</div>
                <div class="sub">已合规接入本地算力池，支持自动轮转与智能避险。</div>
                <div class="email-tag">&#10003; {safe_email}</div>
            </div>
            <script>
                if (window.opener) {{
                    window.opener.postMessage({{
                        type: 'GOOGLE_OAUTH_SUCCESS',
                        email: '{safe_email}',
                        account_name: '{safe_name}'
                    }}, '*');
                }}
                setTimeout(() => {{
                    window.close();
                }}, 1800);
            </script>
        </body>
        </html>
        """)
    except Exception as e:
        logger.error(f"处理 OAuth 回调失败: {e}")
        safe_err = html.escape(str(e))
        return HTMLResponse(content=f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="utf-8">
            <title>绑定异常</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    background: #131314;
                    color: #f28b82;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                }}
                .card {{
                    background: #1e1f20;
                    padding: 32px;
                    border-radius: 16px;
                    border: 1px solid #5c2b29;
                    text-align: center;
                    max-width: 440px;
                }}
                h3 {{ margin: 0 0 12px 0; color: #f28b82; font-size: 18px; }}
                p {{ color: #9aa0a6; font-size: 13px; line-height: 1.5; margin: 0; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h3>Google 绑定失败</h3>
                <p>{safe_err}</p>
            </div>
            <script>
                if (window.opener) {{
                    window.opener.postMessage({{ type: 'GOOGLE_OAUTH_FAILED', error: '{safe_err}' }}, '*');
                }}
            </script>
        </body>
        </html>
        """, status_code=500)


@router.post("/config")
async def update_oauth_config(cfg: OAuthConfigRequest) -> dict[str, str]:
    """配置自建 Google Cloud OAuth Client ID / Secret"""
    oauth_service.set_credentials(cfg.client_id, cfg.client_secret)
    return {"message": "Google Cloud 企业 OAuth 凭据已成功保存"}


@router.post("/{account_id}/refresh")
async def force_refresh_oauth_token(account_id: int = Path(...)) -> dict[str, Any]:
    """手动刷新指定企业 OAuth 账号的 Access Token"""
    account = await AccountRepository.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    if account.auth_type != "oauth_google_one":
        raise HTTPException(status_code=400, detail="该账号不是 OAuth 认证类型")

    try:
        new_token = await oauth_service.refresh_access_token(account)
        return {
            "message": "Token 刷新成功",
            "account_id": account.id,
            "expires_at": account.expires_at,
            "token_preview": f"{new_token[:8]}...{new_token[-6:]}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新失败: {str(e)}")

from pydantic import BaseModel, Field

class ImportRefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., description="Google OAuth Refresh Token")
    alias_name: str | None = None
    priority: int = 1
    client_id: str | None = None
    client_secret: str | None = None


@router.post("/import-token")
async def import_refresh_token(req: ImportRefreshTokenRequest) -> dict[str, Any]:
    """通过现有 Refresh Token 直接换取初始凭据并自动绑定账号"""
    try:
        account = await oauth_service.import_by_refresh_token(
            refresh_token=req.refresh_token,
            client_id=req.client_id,
            client_secret=req.client_secret,
            alias_name=req.alias_name,
            priority=req.priority
        )
        return {
            "message": f"成功绑定 Google 账号 [{account.email}]",
            "account_id": account.id,
            "name": account.name,
            "email": account.email
        }
    except Exception as e:
        logger.error(f"导入 Refresh Token 失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/config")
async def get_oauth_config() -> dict[str, Any]:
    """获取当前 OAuth 客户端配置状态"""
    return {
        "is_configured": oauth_service.is_configured(),
        "client_id": oauth_service.client_id[:16] + "..." if oauth_service.client_id else "",
        "redirect_uri": oauth_service.redirect_uri
    }
