"""
Google OAuth 2.0 企业自建凭据授权服务
基于 RFC 8252 与 RFC 7636 标准的 PKCE 授权码模式
对接官方标准客户端与用户自建 OAuth 客户端
"""
import time
import uuid
import hashlib
import base64
import secrets
import urllib.parse
import json
from typing import Any
import httpx

from ..core.config import settings
from ..core.logging_config import logger
from ..model.models import AccountModel
from ..repository.account_repository import AccountRepository


class GoogleOAuthService:
    """Google OAuth 2.0 凭据授权全生命周期管理服务"""

    AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"

    # NOTE: 官方标准登录权限集合（对齐 Antigravity 官方客户端权限集）
    SCOPES = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/cclog",
        "https://www.googleapis.com/auth/experimentsandconfigs",
        "https://www.googleapis.com/auth/aicode",
    ]

    def __init__(self) -> None:
        import os
        from pathlib import Path
        from ..core.oauth_google import google_oauth_manager
        self._cfg_file = Path("data/.oauth_client.json")
        self.client_id: str = (getattr(settings, "google_oauth_client_id", "") or "").strip() or google_oauth_manager.client_id
        self.client_secret: str = (getattr(settings, "google_oauth_client_secret", "") or "").strip() or google_oauth_manager.client_secret

        if not self.client_id or self.client_id == "YOUR_GOOGLE_OAUTH_CLIENT_ID":
            self.client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
        if not self.client_secret:
            self.client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")

        self.redirect_uri: str = "http://localhost:8686/api/v1/accounts/oauth/callback"
        self._pkce_store: dict[str, str] = {}

    def set_credentials(self, client_id: str, client_secret: str) -> None:
        """动态配置用户自建的 Google Cloud OAuth 凭据"""
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        try:
            self._cfg_file.parent.mkdir(parents=True, exist_ok=True)
            self._cfg_file.write_text(
                json.dumps({"client_id": self.client_id, "client_secret": self.client_secret}),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"持久化 OAuth 凭据配置失败: {e}")
        logger.info(f"已动态配置并持久化 Google Cloud OAuth 凭据: {self.client_id[:12]}...")

    def is_configured(self) -> bool:
        """检测是否已配置合规的 Client ID 与 Secret"""
        return bool(self.client_id and self.client_secret)

    def generate_auth_url(self) -> tuple[str, str]:
        """
        生成携带 RFC 7636 PKCE 的 Google OAuth 2.0 官方授权链接
        :return: (authorization_url, state)
        """
        if not self.client_id:
            raise ValueError("尚未配置 Google Cloud OAuth Client ID")

        state = str(uuid.uuid4())
        
        # 生成 RFC 7636 PKCE 验证对
        code_verifier = secrets.token_urlsafe(64)
        hashed = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(hashed).decode("ascii").rstrip("=")
        
        self._pkce_store[state] = code_verifier

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256"
        }
        auth_url = f"{self.AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"
        return auth_url, state

    async def exchange_code_for_tokens(self, code: str, state: str | None = None) -> dict[str, Any]:
        """向 Google 令牌端点发送授权 code，换取 Access Token 与 Refresh Token"""
        code_verifier = self._pkce_store.pop(state, None) if state else None

        payload: dict[str, str] = {
            "client_id": self.client_id,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret
        if code_verifier:
            payload["code_verifier"] = code_verifier

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self.TOKEN_ENDPOINT, data=payload)
            if resp.status_code == 200:
                logger.info("成功从 Google 令牌端点换取 OAuth 令牌")
                return resp.json()

            logger.error(f"Google OAuth Token 交换失败 [{resp.status_code}]: {resp.text}")
            raise ValueError(f"Google 令牌端点响应异常: {resp.text}")

    async def fetch_user_info(self, access_token: str) -> dict[str, Any]:
        """使用 access_token 获取 Google 用户头像与邮箱"""
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(self.USERINFO_ENDPOINT, headers=headers)
            if resp.status_code != 200:
                raise ValueError(f"获取 Google 用户信息失败: {resp.text}")
            return resp.json()

    async def bind_account(self, tokens: dict[str, Any], user_info: dict[str, Any]) -> AccountModel:
        """持久化 Google OAuth 账号"""
        email = user_info.get("email", "personal-user@google.com")
        avatar_url = user_info.get("picture")
        name_prefix = user_info.get("name") or email.split("@")[0]
        
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in", 3600)
        expires_at = int(time.time()) + expires_in

        existing = await AccountRepository.get_by_email(email)
        if existing:
            await AccountRepository.update_oauth_token(
                account_id=existing.id or 0,
                access_token=access_token,
                expires_at=expires_at,
                refresh_token=refresh_token
            )
            logger.info(f"OAuth 账号 [{email}] 令牌更新成功 (ID: {existing.id})")
            updated = await AccountRepository.get_by_email(email)
            return updated or existing
        else:
            account_data = {
                "name": f"OAuth-{name_prefix}",
                "auth_type": "oauth_google_one",
                "credentials": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "email": email,
                "avatar_url": avatar_url,
                "priority": 1,
                "rpm_limit": 60,
                "rpd_limit": 5000,
                "extra_meta": '{"tier": "google_personal_oauth"}'
            }
            acc_id = await AccountRepository.create(account_data)
            logger.info(f"新建 OAuth 账号 [{email}] 成功 (ID: {acc_id})")
            created = await AccountRepository.get_by_id(acc_id)
            if not created:
                raise RuntimeError("创建账号失败")
            return created

    async def import_by_refresh_token(
        self,
        refresh_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        alias_name: str | None = None,
        priority: int = 1
    ) -> AccountModel:
        """
        通过 Google Refresh Token 直接换取初始凭据并自动绑定账号
        支持外部传入 client_id/secret，若未指定则使用系统配置
        """
        cid = (client_id or self.client_id or "").strip()
        csecret = (client_secret or self.client_secret or "").strip()

        payload: dict[str, str] = {
            "refresh_token": refresh_token.strip(),
            "grant_type": "refresh_token"
        }
        if cid:
            payload["client_id"] = cid
        if csecret:
            payload["client_secret"] = csecret

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self.TOKEN_ENDPOINT, data=payload)
            if resp.status_code != 200:
                logger.error(f"Refresh Token 验证失败 [{resp.status_code}]: {resp.text}")
                raise ValueError(f"Refresh Token 校验失败或 Google 拒绝: {resp.text}")

            token_data = resp.json()
            access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 3600)
            expires_at = int(time.time()) + expires_in

            # 尝试通过 access_token 获取用户信息
            email = "google-user@oauth.com"
            avatar_url = None
            try:
                user_info = await self.fetch_user_info(access_token)
                email = user_info.get("email", email)
                avatar_url = user_info.get("picture")
            except Exception as e:
                logger.warning(f"获取用户信息异常，使用缺省邮箱: {e}")

            name = alias_name or f"OAuth-{email.split('@')[0]}"

            existing = await AccountRepository.get_by_email(email)
            if existing:
                await AccountRepository.update_oauth_token(
                    account_id=existing.id or 0,
                    access_token=access_token,
                    expires_at=expires_at,
                    refresh_token=refresh_token.strip()
                )
                logger.info(f"直接导入更新账号 [{email}] 成功")
                updated = await AccountRepository.get_by_id(existing.id or 0)
                return updated or existing
            else:
                account_data = {
                    "name": name,
                    "auth_type": "oauth_google_one",
                    "credentials": access_token,
                    "refresh_token": refresh_token.strip(),
                    "expires_at": expires_at,
                    "email": email,
                    "avatar_url": avatar_url,
                    "priority": priority,
                    "rpm_limit": 60,
                    "rpd_limit": 5000,
                    "extra_meta": json.dumps({"source": "refresh_token_import", "client_id": cid})
                }
                acc_id = await AccountRepository.create(account_data)
                created = await AccountRepository.get_by_id(acc_id)
                if not created:
                    raise RuntimeError("账号入库失败")
                return created

    async def refresh_access_token(self, account: AccountModel) -> str:
        """静默续期 Access Token"""
        if not account.refresh_token:
            raise ValueError(f"账号 [{account.name}] 无可用 Refresh Token，需重新授权")

        payload: dict[str, str] = {
            "client_id": self.client_id,
            "refresh_token": account.refresh_token,
            "grant_type": "refresh_token"
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret

        from ..core.proxy_helper import get_system_proxy_url
        proxy = get_system_proxy_url()
        client_kwargs: dict[str, Any] = {"timeout": 15.0}
        if proxy:
            client_kwargs["proxy"] = proxy

        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(self.TOKEN_ENDPOINT, data=payload)
            if resp.status_code != 200:
                raise ValueError(f"Google 凭据静默刷新失败: {resp.text}")

            data = resp.json()
            new_access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            new_expires_at = int(time.time()) + expires_in
            new_refresh_token = data.get("refresh_token")

            await AccountRepository.update_oauth_token(
                account_id=account.id or 0,
                access_token=new_access_token,
                expires_at=new_expires_at,
                refresh_token=new_refresh_token
            )
            account.credentials = new_access_token
            account.expires_at = new_expires_at
            if new_refresh_token:
                account.refresh_token = new_refresh_token

            logger.info(f"已静默续期 OAuth 账号 [{account.name}] 的 Access Token，将在 {expires_in} 秒后过期")
            return new_access_token

    async def ensure_valid_token(self, account: AccountModel) -> str:
        """
        确保账号具有有效的 Access Token。
        若令牌不存在、已过期或即将过期（预留 60 秒刷新缓冲期），
        自动利用 Refresh Token 发送请求静默换发新 Access Token 并持久化回数据库。
        """
        now = int(time.time())
        token = (account.credentials or "").strip()
        expires_at = account.expires_at or 0

        # 如果当前 access_token 依然有效且剩余有效期大于 60 秒，则直接复用
        if token and expires_at > (now + 60):
            return token

        # 若无 refresh_token，降级直接返回现有凭证
        if not account.refresh_token:
            return token

        # 使用 refresh_token 静默刷新续期
        try:
            return await self.refresh_access_token(account)
        except Exception as e:
            logger.error(f"静默续期 OAuth 账号 [{account.name}] 失败: {e}")
            # 智能兜底：若旧 refresh_token 因 Client ID 迁移导致 unauthorized_client，自动从本机 Antigravity Keyring 恢复活性
            if "unauthorized_client" in str(e):
                try:
                    from ..core.oauth_google import google_oauth_manager
                    host_cred = google_oauth_manager.get_host_keyring_credentials()
                    if host_cred and host_cred.get("access_token"):
                        logger.info(f"已自动使用本机 Windows Keyring 凭据为账号 [{account.name}] 恢复活性")
                        new_acc = host_cred["access_token"]
                        new_ref = host_cred.get("refresh_token")
                        account.credentials = new_acc
                        account.expires_at = now + 3600
                        if new_ref:
                            account.refresh_token = new_ref
                        await AccountRepository.update_oauth_token(
                            account_id=account.id or 0,
                            access_token=new_acc,
                            expires_at=now + 3600,
                            refresh_token=new_ref or account.refresh_token,
                        )
                        return new_acc
                except Exception as rec_err:
                    logger.warning(f"从 Keyring 自动恢复账号活性跳过: {rec_err}")
            return token


oauth_service = GoogleOAuthService()
