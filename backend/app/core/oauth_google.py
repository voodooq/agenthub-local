"""
Google 官方桌面端 OAuth 2.0 (PKCE) 双模授权与静默续期管理器
支持：
1. 借壳官方桌面端公开 Client ID + 标准 S256 PKCE 校验流（免自建 GCP 项目门槛）
2. 方式 A (本机自动回环捕获): 监听 127.0.0.1:1455/auth/callback 自动截获 code 并入库
3. 方式 B (远程/指纹浏览器手动粘贴兜底): 支持直接粘贴完整回调 URL 或 code 换取长期 refresh_token
4. 静默刷新 Access Token: 基于 refresh_token 自动续期并缓存，配合 EnginePool 实现 <50ms 429 故障转移
"""
import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from typing import Any, Dict, Optional

import httpx

from pathlib import Path
from .config import settings
from .logging_config import logger
from ..repository.db import db_manager
from ..repository.account_repository import AccountRepository


class GoogleOAuthManager:
    """Google 官方桌面端 PKCE OAuth 授权与凭据续期核心引擎"""

    CALLBACK_PORT = 1455
    REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"

    SCOPES = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ]

    def __init__(self) -> None:
        self._pending_sessions: Dict[str, Dict[str, Any]] = {}
        self._server: Optional[asyncio.AbstractServer] = None
        # 内存 Access Token 缓存: provider_id -> {"access_token": str, "expires_at": float}
        self._token_cache: Dict[str, Dict[str, Any]] = {}
        self._cfg_file = Path("data/.oauth_client.json")

    def _load_local_oauth_cfg(self) -> Dict[str, str]:
        if self._cfg_file.exists():
            try:
                return json.loads(self._cfg_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    @property
    def client_id(self) -> str:
        env_val = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or getattr(settings, "google_oauth_client_id", "")
        if env_val:
            return env_val.strip()
        return self._load_local_oauth_cfg().get("client_id", "YOUR_GOOGLE_OAUTH_CLIENT_ID")

    @property
    def client_secret(self) -> str:
        env_val = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or getattr(settings, "google_oauth_client_secret", "")
        if env_val:
            return env_val.strip()
        return self._load_local_oauth_cfg().get("client_secret", "")

    @staticmethod
    def _generate_pkce() -> tuple[str, str]:
        code_verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return code_verifier, code_challenge

    @staticmethod
    def _encode_secret(raw_val: str) -> str:
        if not raw_val:
            return ""
        return base64.b64encode(raw_val.strip().encode("utf-8")).decode("utf-8")

    @staticmethod
    def _decode_secret(enc_val: str) -> str:
        if not enc_val:
            return ""
        try:
            return base64.b64decode(enc_val.encode("utf-8")).decode("utf-8")
        except Exception:
            return enc_val

    async def create_auth_session(self, remark: str = "", role_binding: str = "both", priority: int = 5) -> Dict[str, str]:
        """初始化 PKCE OAuth 授权会话并确保本地 1455 端口回环监听已启动"""
        state = secrets.token_urlsafe(16)
        code_verifier, code_challenge = self._generate_pkce()

        self._pending_sessions[state] = {
            "code_verifier": code_verifier,
            "remark": remark.strip(),
            "role_binding": role_binding,
            "priority": priority,
            "event": asyncio.Event(),
            "result": None,
        }

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(self.SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
        await self._ensure_local_callback_server()

        return {
            "state": state,
            "auth_url": auth_url,
            "redirect_uri": self.REDIRECT_URI,
        }

    async def complete_by_manual_url(
        self,
        callback_url: str,
        fallback_state: Optional[str] = None,
        remark: str = "",
        role_binding: str = "both",
        priority: int = 5,
    ) -> Dict[str, Any]:
        """
        方式 B：接收用户在指纹浏览器或远程环境登录完后复制粘贴的完整回调 URL（或直接粘贴的 code），
        解析 code 并完成 Token 换取与 SQLite 落盘。
        """
        raw_input = (callback_url or "").strip()
        if not raw_input:
            raise ValueError("回调地址不能为空")

        if raw_input.startswith("http://") or raw_input.startswith("https://") or "?" in raw_input:
            parsed = urllib.parse.urlparse(raw_input)
            query = urllib.parse.parse_qs(parsed.query)
            code = (query.get("code") or [""])[0]
            state = (query.get("state") or [fallback_state])[0]
        else:
            code = raw_input
            state = fallback_state

        if not code:
            raise ValueError("未在链接中识别到有效的 code 参数，请检查粘贴的内容。")

        token_info = await self._exchange_code_for_token(code=code, state=state)
        saved = await self._persist_google_provider(
            token_info=token_info,
            remark=remark,
            state=state,
            role_binding=role_binding,
            priority=priority,
        )
        return saved

    async def _exchange_code_for_token(self, code: str, state: Optional[str]) -> Dict[str, Any]:
        """向 Google OAuth2 Token 端点换取 access_token 与长期 refresh_token"""
        session = self._pending_sessions.get(state or "")
        payload: Dict[str, Any] = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.REDIRECT_URI,
        }
        if session and "code_verifier" in session:
            payload["code_verifier"] = session["code_verifier"]

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post("https://oauth2.googleapis.com/token", data=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Google Token 换取失败 ({resp.status_code}): {resp.text}")
            tokens = resp.json()

            user_resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            user_info = user_resp.json() if user_resp.status_code == 200 else {}

        return {
            "email": user_info.get("email", "unknown@gmail.com"),
            "picture": user_info.get("picture", ""),
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token", ""),
            "expires_in": int(tokens.get("expires_in", 3600)),
        }

    async def _persist_google_provider(
        self,
        token_info: Dict[str, Any],
        remark: str = "",
        state: Optional[str] = None,
        role_binding: str = "both",
        priority: int = 5,
    ) -> Dict[str, Any]:
        """将换取成功的 Google OAuth 账号写入 llm_providers 表，并同步至 accounts 表"""
        session = self._pending_sessions.get(state or "") or {}
        effective_remark = remark.strip() or session.get("remark") or ""
        effective_role = session.get("role_binding") or role_binding or "both"
        effective_priority = int(session.get("priority") or priority or 5)

        email = token_info.get("email") or "google_user@gmail.com"
        display_name = f"{effective_remark} ({email})" if effective_remark else f"Google OAuth ({email})"
        safe_email_slug = email.split("@")[0].replace(".", "_")
        provider_id = f"google_oauth_{safe_email_slug}"

        access_token = token_info.get("access_token") or ""
        refresh_token = token_info.get("refresh_token") or ""
        expires_in = int(token_info.get("expires_in", 3600))

        enc_access = self._encode_secret(access_token)
        enc_refresh = self._encode_secret(refresh_token)
        default_models = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

        async with db_manager.get_connection() as conn:
            # 若此前已存在同 ID 账号且本次未返回新 refresh_token，保留原 refresh_token
            if not enc_refresh:
                async with conn.execute(
                    "SELECT refresh_token_encrypted FROM llm_providers WHERE id = ?",
                    (provider_id,),
                ) as cur:
                    old_row = await cur.fetchone()
                    if old_row and old_row["refresh_token_encrypted"]:
                        enc_refresh = old_row["refresh_token_encrypted"]

            await conn.execute(
                """
                INSERT OR REPLACE INTO llm_providers
                (id, name, provider_type, base_url, api_key_encrypted, refresh_token_encrypted,
                 models_json, role_binding, tool_call_mode, priority, cooldown_until, is_enabled)
                VALUES (?, ?, 'google_oauth', '', ?, ?, ?, ?, 'auto', ?, NULL, 1)
                """,
                (
                    provider_id,
                    display_name,
                    enc_access,
                    enc_refresh,
                    json.dumps(default_models, ensure_ascii=False),
                    effective_role,
                    effective_priority,
                ),
            )
            await conn.commit()

        # 更新内存缓存
        self._token_cache[provider_id] = {
            "access_token": access_token,
            "expires_at": time.time() + expires_in - 60,
        }

        # 同步写入 accounts 表以兼容顶部状态胶囊与账号池视图
        try:
            await AccountRepository.upsert_oauth_account(
                name=f"google_{safe_email_slug}",
                email=email,
                avatar_url=token_info.get("picture", ""),
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=int(time.time()) + expires_in,
                auth_type="oauth_google_one",
            )
        except Exception as e:
            logger.debug(f"同步写入 accounts 表跳过: {e}")

        if session and isinstance(session.get("event"), asyncio.Event):
            session["result"] = {"id": provider_id, "email": email, "name": display_name}
            session["event"].set()

        return {
            "id": provider_id,
            "name": display_name,
            "email": email,
            "picture": token_info.get("picture", ""),
            "provider_type": "google_oauth",
            "models": default_models,
            "role_binding": effective_role,
            "priority": effective_priority,
        }

    async def ensure_valid_access_token(self, provider_row: Dict[str, Any]) -> str:
        """
        静默刷新 Access Token：
        若内存缓存未过期则直接返回（<1ms）；若已过期则使用 refresh_token_encrypted 向 Google 刷新（约 100ms）。
        """
        pid = provider_row["id"]
        now = time.time()
        cached = self._token_cache.get(pid)
        if cached and cached.get("access_token") and cached.get("expires_at", 0) > now:
            return cached["access_token"]

        refresh_token = self._decode_secret(provider_row.get("refresh_token_encrypted") or "")
        if not refresh_token:
            # 回退使用已保存的 access_token
            fallback_token = self._decode_secret(provider_row.get("api_key_encrypted") or "")
            if fallback_token:
                return fallback_token
            raise RuntimeError(f"Google OAuth 账号 [{provider_row.get('name')}] 缺少 refresh_token，请重新授权")

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post("https://oauth2.googleapis.com/token", data=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"刷新 Google Access Token 失败 ({resp.status_code}): {resp.text[:200]}")
            data = resp.json()
            new_access = data["access_token"]
            expires_in = int(data.get("expires_in", 3600))

        self._token_cache[pid] = {
            "access_token": new_access,
            "expires_at": now + expires_in - 60,
        }

        # 异步持久化最新 access_token
        enc_access = self._encode_secret(new_access)
        async with db_manager.get_connection() as conn:
            await conn.execute(
                "UPDATE llm_providers SET api_key_encrypted = ? WHERE id = ?",
                (enc_access, pid),
            )
            await conn.commit()

        return new_access

    async def _ensure_local_callback_server(self) -> None:
        """启动本机 127.0.0.1:1455 临时回调捕获服务（方式 A：本机自动捕获，基于标准库 asyncio 零外部依赖）"""
        if self._server is not None:
            return

        async def _handle_http_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                raw_req = await asyncio.wait_for(reader.read(4096), timeout=5.0)
                req_line = raw_req.decode("utf-8", errors="replace").splitlines()[0] if raw_req else ""
                parts = req_line.split(" ")
                req_path = parts[1] if len(parts) >= 2 else ""

                parsed = urllib.parse.urlparse(req_path)
                query = urllib.parse.parse_qs(parsed.query)
                code = (query.get("code") or [""])[0]
                state = (query.get("state") or [""])[0]

                if not code:
                    body = "<h2>授权失败：未收到 code 参数</h2>".encode("utf-8")
                    status_line = "HTTP/1.1 400 Bad Request\r\n"
                else:
                    try:
                        token_info = await self._exchange_code_for_token(code, state)
                        saved = await self._persist_google_provider(token_info=token_info, state=state)
                        html = (
                            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>授权成功</title></head>"
                            "<body style='font-family:sans-serif;text-align:center;padding-top:80px;background:#0f172a;color:#f8fafc;'>"
                            f"<h2 style='color:#34d399;'>✅ 账号 {saved['email']} 授权成功！</h2>"
                            "<p style='color:#94a3b8;'>已自动加入 GeminiHub Local 多账号算力池，您可以关闭此页面返回工作台。</p>"
                            "<script>setTimeout(()=>window.close(), 1200);</script></body></html>"
                        )
                        body = html.encode("utf-8")
                        status_line = "HTTP/1.1 200 OK\r\n"
                    except Exception as ex:
                        logger.error(f"1455 端口自动回调换票失败: {ex}")
                        body = f"<h2>换取凭据失败: {str(ex)}</h2>".encode("utf-8")
                        status_line = "HTTP/1.1 500 Internal Server Error\r\n"

                headers = (
                    f"{status_line}"
                    "Content-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("utf-8")
                writer.write(headers + body)
                await writer.drain()
            except Exception as e:
                logger.debug(f"处理本地 OAuth 回调连接结束: {e}")
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        try:
            self._server = await asyncio.start_server(_handle_http_client, "127.0.0.1", self.CALLBACK_PORT)
            logger.info(f"Google OAuth 本地回环捕获服务已就绪: {self.REDIRECT_URI}")
        except OSError as e:
            logger.warning(f"端口 {self.CALLBACK_PORT} 已被占用，可使用手动粘贴回调 URL 兜底: {e}")


google_oauth_manager = GoogleOAuthManager()
