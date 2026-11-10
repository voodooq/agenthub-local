"""
Google Gemini 统一客户端 (Gemini Client)
双核引擎架构：
1. 官方通道：对接 Google Generative Language API (generativelanguage.googleapis.com)
2. 网页免额度通道：对接 Gemini Web Bridge Sidecar 独立容器 (gemini-web-bridge:8000)
"""
import json
import re
import time
from typing import AsyncGenerator, Any, Optional, Tuple
import httpx

from ..core.config import settings
from ..core.logging_config import logger


class GeminiAPIException(Exception):
    """Gemini API 业务异常基类"""
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Gemini API Error [{status_code}]: {message}")
        self.status_code = status_code
        self.message = message


class GeminiClient:
    """
    统一客户端：支持官方 API、Google Cloud Code Assist OAuth 通道与网页端 Sidecar 三通道
    """
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    CLOUDCODE_BASE_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal"
    CLOUDCODE_UA = "antigravity/2.19.1 windows/amd64"
    _DETECTED_UA: str | None = None
    _PROJECT_CACHE: dict[str, str] = {}
    _FALLBACK_CACHE: dict[str, Any] = {"token": "", "expires_at": 0.0, "legacy_tokens": set()}

    @classmethod
    def get_cloudcode_ua(cls) -> str:
        """动态解析本地或官方最新版 Antigravity User-Agent，避免触发弃用拦截"""
        if cls._DETECTED_UA:
            return cls._DETECTED_UA
        try:
            import os
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            exe_path = os.path.join(local_appdata, "Programs", "antigravity", "Antigravity.exe")
            if os.path.exists(exe_path):
                import subprocess
                from ..core.runtime_paths import get_silent_subprocess_kwargs

                cmd = f"(Get-Item '{exe_path}').VersionInfo.FileVersion"
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", cmd],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    **get_silent_subprocess_kwargs(),
                )
                v = res.stdout.strip()
                if v and len(v.split(".")) >= 2:
                    cls._DETECTED_UA = f"antigravity/{v} windows/amd64"
                    return cls._DETECTED_UA
        except Exception:
            pass
        cls._DETECTED_UA = "antigravity/2.19.1 windows/amd64"
        return cls._DETECTED_UA

    @classmethod
    def is_version_deprecation_message(cls, text: str) -> bool:
        """检测输出是否为 Google 服务端返回的 Antigravity 版本受限/套餐不满足警告文本"""
        if not text:
            return False
        indicators = [
            "This version of Antigravity is no longer supported",
            "download and install the latest version at https://antigravity.google/download",
            "unlock our latest models, including Gemini 4 Argon",
        ]
        return any(ind in text for ind in indicators)

    # Cloud Code Assist (OAuth) 模型映射字典
    CLOUDCODE_MODEL_MAP = {
        "gemini-2.5-flash": "gemini-2.5-flash",
        "gemini-2.5-pro": "gemini-2.5-pro",
        "gemini-2.0-flash": "gemini-2.5-flash",
        "gemini-2.0-flash-lite": "gemini-2.5-flash-lite",
        "gemini-4-argon": "gemini-2.5-pro",
        "4 Argon": "gemini-2.5-pro",
        "3.1 Pro": "gemini-2.5-pro",
        "gemini-3.8-flash": "gemini-3-flash",
        "3.8 Flash": "gemini-3-flash",
        "3.5 Flash-Lite": "gemini-2.5-flash-lite",
        "gemini-3.5-flash-lite": "gemini-2.5-flash-lite",
        "claude-sonnet-5-5": "claude-sonnet-5-5-high",
        "claude-opus-5-5": "claude-opus-5-5-high",
        "gpt-oss-120b-medium": "gpt-oss-120b-medium",
    }

    # 模型映射字典：前端展示名映射至官方真实 REST 模型标识
    MODEL_MAP = {
        "4 Argon": "gemini-2.5-pro",
        "3.1 Pro": "gemini-2.5-pro",
        "3.8 Flash": "gemini-2.5-flash",
        "3.5 Flash-Lite": "gemini-2.0-flash-lite",
        "gemini-4-argon": "gemini-2.5-pro",
        "gemini-4-argon-high": "gemini-2.5-pro",
        "gemini-4-argon-medium": "gemini-2.5-pro",
        "gemini-4-argon-low": "gemini-2.5-pro",
        "gemini-3.1-pro-preview": "gemini-2.5-pro",
        "gemini-3.8-flash": "gemini-2.5-flash",
        "gemini-3.8-flash-high": "gemini-2.5-flash",
        "gemini-3.8-flash-medium": "gemini-2.5-flash",
        "gemini-3.8-flash-low": "gemini-2.5-flash",
        "gemini-3.5-flash-lite": "gemini-2.0-flash-lite",
        "gemini-flash-latest": "gemini-2.5-flash",
        "gemini-pro-latest": "gemini-2.5-pro",
        "gemini-2.5-pro": "gemini-2.5-pro",
        "gemini-2.5-flash": "gemini-2.5-flash",
        "gemini-2.0-flash": "gemini-2.0-flash",
        "gemini-2.0-flash-thinking-exp-01-21": "gemini-2.0-flash-thinking-exp-01-21",
        "gemini-2.0-flash-lite": "gemini-2.0-flash-lite",
        # 网页端专属标识映射
        "Gemini 2.0 Pro (网页免额度)": "gemini-2.0-pro-web",
        "Gemini 2.0 Flash (网页免额度)": "gemini-2.0-flash-web",
        "Gemini 深度思考 (网页免额度)": "gemini-thinking-web",
    }

    THINKING_BUDGET_MAP = {
        "low": 2048,
        "medium": 8192,
        "high": 24576,
    }

    @classmethod
    def resolve_model_id(cls, model_name: str) -> str:
        """解析模型名称"""
        return cls.MODEL_MAP.get(model_name, model_name)

    @classmethod
    def resolve_cloudcode_model_id(cls, model_name: str) -> str:
        """解析 Cloud Code Assist OAuth 通道模型名称"""
        return cls.CLOUDCODE_MODEL_MAP.get(model_name, cls.MODEL_MAP.get(model_name, model_name))

    @classmethod
    async def _resolve_cloudcode_project(cls, token: str, client: httpx.AsyncClient) -> str:
        """动态解析并缓存当前 OAuth 账号所属的 cloudaicompanionProject"""
        cache_key = token[:32]
        if cache_key in cls._PROJECT_CACHE:
            return cls._PROJECT_CACHE[cache_key]
        try:
            resp = await client.post(
                f"{cls.CLOUDCODE_BASE_URL}:loadCodeAssist",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": cls.get_cloudcode_ua(),
                },
                json={
                    "metadata": {
                        "ideType": "ANTIGRAVITY",
                        "platform": "PLATFORM_UNSPECIFIED",
                        "pluginType": "GEMINI",
                    }
                },
            )
            if resp.status_code == 200:
                proj = resp.json().get("cloudaicompanionProject") or "aicode-consumers"
                cls._PROJECT_CACHE[cache_key] = proj
                return proj
        except Exception as e:
            logger.debug(f"解析 cloudaicompanionProject 使用默认值: {e}")
        return "aicode-consumers"

    @classmethod
    async def _get_fallback_oauth_token(cls, client: httpx.AsyncClient, failed_token: str = "") -> str:
        """当旧版 OAuth 令牌缺少 aicode 权限时，自动尝试从本机 Antigravity Keyring 提取/刷新有效令牌并缓存"""
        if failed_token:
            cls._FALLBACK_CACHE["legacy_tokens"].add(failed_token[:32])
        now = time.time()
        if cls._FALLBACK_CACHE.get("token") and cls._FALLBACK_CACHE.get("expires_at", 0) > now:
            return cls._FALLBACK_CACHE["token"]
        try:
            from ..core.oauth_google import google_oauth_manager
            host_cred = google_oauth_manager.get_host_keyring_credentials()
            if not host_cred:
                return ""
            if host_cred.get("refresh_token") and google_oauth_manager.client_id:
                resp = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": google_oauth_manager.client_id,
                        "client_secret": google_oauth_manager.client_secret,
                        "refresh_token": host_cred["refresh_token"],
                        "grant_type": "refresh_token",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tok = data.get("access_token", "")
                    if tok:
                        cls._FALLBACK_CACHE["token"] = tok
                        cls._FALLBACK_CACHE["expires_at"] = now + int(data.get("expires_in", 3600)) - 60
                        return tok
            tok = host_cred.get("access_token", "")
            if tok:
                cls._FALLBACK_CACHE["token"] = tok
                cls._FALLBACK_CACHE["expires_at"] = now + 600
            return tok
        except Exception:
            return ""

    @classmethod
    def _clean_credential(cls, raw: str | None) -> str:
        """清洗凭证字符串"""
        if not raw:
            return ""
        return raw.strip().replace("\r", "").replace("\n", "")

    @classmethod
    def parse_web_cookie(cls, raw_cred: str) -> Tuple[str, Optional[str]]:
        """
        高鲁棒性提取 Cookie 凭据：
        支持 JSON 格式 {"psid": "...", "psidts": "..."}、Raw Cookie 字符串或纯 PSID
        """
        cleaned = cls._clean_credential(raw_cred)
        if not cleaned:
            return "", None

        # 尝试 JSON 解析
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                psid = parsed.get("psid", "").strip()
                psidts = parsed.get("psidts", "")
                psidts = psidts.strip() if psidts else None
                if psid:
                    return psid, psidts
        except Exception:
            pass

        # 尝试正则从 Cookie 串提取
        psid_match = re.search(r'__Secure-1PSID=([^;]+)', cleaned)
        psidts_match = re.search(r'__Secure-1PSIDTS=([^;]+)', cleaned)
        if psid_match:
            psid = psid_match.group(1).strip()
            psidts = psidts_match.group(1).strip() if psidts_match else None
            return psid, psidts

        # 纯 PSID 兜底
        return cleaned, None

    @classmethod
    async def stream_generate_content(
        cls,
        api_key: str,
        model_name: str,
        contents: list[dict[str, Any]],
        system_instruction: str | None = None,
        thinking_level: str = "high",
        temperature: float = 0.7,
        auth_type: str = "api_key",
        on_downgrade: Any = None,
        on_usage: Any = None,
        on_first_byte: Any = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式生成调度入口：自适应判断走 Sidecar 网页通道还是官方 REST 通道
        """
        cleaned_cred = cls._clean_credential(api_key)

        if not cleaned_cred:
            raise GeminiAPIException(
                status_code=401,
                message="账号凭证为空，请在后台添加有效的 API Key 或 Cookie 凭据"
            )

        # 1. 路由判断：是否走网页端 Sidecar 通道
        is_web = (
            auth_type in ("web_cookie", "web_sidecar")
            or "psid" in cleaned_cred.lower()
            or "__Secure-" in cleaned_cred
        )

        if is_web:
            psid, psidts = cls.parse_web_cookie(cleaned_cred)
            if not psid:
                raise GeminiAPIException(
                    status_code=401,
                    message="未能在网页凭证中提取到有效的 __Secure-1PSID，请重新配置 Cookie"
                )

            # 转换上下文为标准消息列表
            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            
            for item in contents:
                role = "assistant" if item.get("role") == "model" else "user"
                parts = item.get("parts", [])
                text_content = "".join([p.get("text", "") for p in parts])
                if text_content:
                    messages.append({"role": role, "content": text_content})

            sidecar_payload = {
                "model": cls.resolve_model_id(model_name),
                "messages": messages,
                "stream": True,
                "psid": psid,
                "psidts": psidts,
            }

            sidecar_url = f"{settings.websidecar_url}/v1/chat/completions"
            logger.info(f"正在通过 Sidecar 网页网关调度算力 -> {sidecar_url}")

            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                try:
                    async with client.stream("POST", sidecar_url, json=sidecar_payload) as response:
                        if response.status_code != 200:
                            err_body = await response.aread()
                            raise GeminiAPIException(
                                status_code=response.status_code,
                                message=f"Sidecar 网页网关响应异常: {err_body.decode('utf-8', errors='ignore')}"
                            )

                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            if line.startswith("data: "):
                                raw_chunk = line[len("data: "):].strip()
                                if raw_chunk == "[DONE]":
                                    break
                                try:
                                    chunk_json = json.loads(raw_chunk)
                                    if "error" in chunk_json:
                                        raise GeminiAPIException(
                                            status_code=chunk_json.get("error", {}).get("code", 500),
                                            message=chunk_json.get("error", {}).get("message", "网页端生成失败")
                                        )
                                    choices = chunk_json.get("choices", [])
                                    if chunk_json.get("usage") and on_usage:
                                        try:
                                            on_usage(chunk_json["usage"])
                                        except Exception:
                                            pass
                                    if choices:
                                        delta = choices[0].get("delta", {}).get("content", "")
                                        if delta:
                                            if on_first_byte:
                                                try:
                                                    on_first_byte()
                                                except Exception:
                                                    pass
                                            yield delta
                                except json.JSONDecodeError:
                                    continue
                    return
                except httpx.ConnectError:
                    raise GeminiAPIException(
                        status_code=503,
                        message="无法连接至 Sidecar 网页反代容器 (gemini-web-bridge)，请检查容器运行状态"
                    )

        # 2. 官方 OAuth (Cloud Code Assist) 与 AI Studio API Key 通道
        from ..core.proxy_helper import get_system_proxy_url
        proxy = get_system_proxy_url()
        client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(90.0, connect=10.0)}
        if proxy:
            client_kwargs["proxy"] = proxy

        is_oauth = cleaned_cred.startswith("ya29.") or auth_type in ("oauth_google_one", "oauth_personal")

        if is_oauth and not cleaned_cred.startswith("AIzaSy"):
            cc_model_id = cls.resolve_cloudcode_model_id(model_name)
            generation_config_cc: dict[str, Any] = {"temperature": temperature}
            cc_request: dict[str, Any] = {
                "contents": contents,
                "generationConfig": generation_config_cc,
            }
            if system_instruction:
                cc_request["systemInstruction"] = {
                    "role": "user",
                    "parts": [{"text": system_instruction}],
                }

            async with httpx.AsyncClient(**client_kwargs) as client:
                active_token = cleaned_cred
                if active_token[:32] in cls._FALLBACK_CACHE["legacy_tokens"]:
                    active_token = await cls._get_fallback_oauth_token(client) or active_token
                project_id = await cls._resolve_cloudcode_project(active_token, client)
                # 若模型遇到服务端 503 容量不足、429 配额限制或版本/套餐不支持，自动降级至备选高可用模型
                candidate_models = [cc_model_id]
                if cc_model_id in ("gemini-4-argon", "4 Argon", "gemini-2.5-pro"):
                    candidate_models.extend(["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"])
                elif cc_model_id == "gemini-3.8-flash":
                    candidate_models.extend(["gemini-2.5-flash", "gemini-2.5-flash-lite"])
                elif cc_model_id not in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
                    candidate_models.extend(["gemini-2.5-flash", "gemini-2.5-flash-lite"])

                for idx, target_cc_model in enumerate(candidate_models):
                    cc_url = f"{cls.CLOUDCODE_BASE_URL}:streamGenerateContent?alt=sse"
                    cc_headers = {
                        "Authorization": f"Bearer {active_token}",
                        "Content-Type": "application/json",
                        "User-Agent": cls.get_cloudcode_ua(),
                    }
                    cc_payload = {
                        "model": target_cc_model,
                        "project": project_id,
                        "request": cc_request,
                    }
                    deprecation_detected = False
                    buffered_chunks: list[str] = []
                    total_buffered_len = 0
                    buffer_flushed = False

                    try:
                        async with client.stream("POST", cc_url, headers=cc_headers, json=cc_payload) as response:
                            if response.status_code in (401, 403) and idx == 0:
                                # 尝试自动使用本机 Keyring 中的新版 Antigravity 令牌无缝接力
                                fallback_tok = await cls._get_fallback_oauth_token(client, failed_token=active_token)
                                if fallback_tok and fallback_tok != active_token:
                                    active_token = fallback_tok
                                    project_id = await cls._resolve_cloudcode_project(active_token, client)
                                    cc_headers["Authorization"] = f"Bearer {active_token}"
                                    async with client.stream("POST", cc_url, headers=cc_headers, json=cc_payload) as retry_resp:
                                        if retry_resp.status_code == 200:
                                            first_byte_notified = False
                                            async for line in retry_resp.aiter_lines():
                                                if line and line.startswith("data: "):
                                                    if not first_byte_notified and on_first_byte:
                                                        first_byte_notified = True
                                                        try:
                                                            on_first_byte()
                                                        except Exception:
                                                            pass
                                                    raw_data = line[6:].strip()
                                                    if raw_data == "[DONE]":
                                                        break
                                                    try:
                                                        data_json = json.loads(raw_data)
                                                        inner = data_json.get("response", data_json)
                                                        usage_meta = inner.get("usageMetadata") or data_json.get("usageMetadata")
                                                        if usage_meta and on_usage:
                                                            try:
                                                                on_usage(usage_meta)
                                                            except Exception:
                                                                pass
                                                        for cand in inner.get("candidates", []):
                                                            for part in cand.get("content", {}).get("parts", []):
                                                                if part.get("text") and not part.get("thought"):
                                                                    yield part["text"]
                                                    except json.JSONDecodeError:
                                                        continue
                                            return

                            # 404 / 429 / 503 场景下，若还有候选可用模型，自动无缝降级切换
                            if response.status_code in (404, 429, 503) and idx < len(candidate_models) - 1:
                                next_model = candidate_models[idx + 1]
                                logger.info(f"CloudCode 模型 [{target_cc_model}] 响应 [{response.status_code}]，自动降级至 [{next_model}]")
                                if on_downgrade:
                                    try:
                                        on_downgrade(model_name, next_model, f"{response.status_code}_quota_or_capacity")
                                    except Exception as cb_err:
                                        logger.warning(f"触发模型降级回调异常: {cb_err}")
                                continue

                            if response.status_code != 200:
                                error_text = await response.aread()
                                err_msg = error_text.decode("utf-8", errors="ignore")
                                if cls.is_version_deprecation_message(err_msg) and idx < len(candidate_models) - 1:
                                    next_model = candidate_models[idx + 1]
                                    logger.warning(f"模型 [{target_cc_model}] 提示版本/套餐受限，自动降级至 [{next_model}]")
                                    if on_downgrade:
                                        try:
                                            on_downgrade(model_name, next_model, "plan_quota_unsupported")
                                        except Exception as cb_err:
                                            logger.warning(f"触发模型降级回调异常: {cb_err}")
                                    continue
                                raise GeminiAPIException(status_code=response.status_code, message=err_msg)

                            first_byte_notified = False
                            async for line in response.aiter_lines():
                                if not line:
                                    continue
                                if line.startswith("data: "):
                                    if not first_byte_notified and on_first_byte:
                                        first_byte_notified = True
                                        try:
                                            on_first_byte()
                                        except Exception:
                                            pass
                                    raw_data = line[len("data: "):].strip()
                                    if raw_data == "[DONE]":
                                        break
                                    try:
                                        data_json = json.loads(raw_data)
                                        inner = data_json.get("response", data_json)
                                        usage_meta = inner.get("usageMetadata") or data_json.get("usageMetadata")
                                        if usage_meta and on_usage:
                                            try:
                                                on_usage(usage_meta)
                                            except Exception:
                                                pass
                                        candidates = inner.get("candidates", [])
                                        if candidates:
                                            parts = candidates[0].get("content", {}).get("parts", [])
                                            for part in parts:
                                                text_chunk = part.get("text")
                                                if text_chunk and not part.get("thought"):
                                                    if not buffer_flushed:
                                                        buffered_chunks.append(text_chunk)
                                                        total_buffered_len += len(text_chunk)
                                                        combined_head = "".join(buffered_chunks)
                                                        if cls.is_version_deprecation_message(combined_head):
                                                            deprecation_detected = True
                                                            break
                                                        if total_buffered_len >= 120:
                                                            buffer_flushed = True
                                                            for bc in buffered_chunks:
                                                                yield bc
                                                            buffered_chunks.clear()
                                                    else:
                                                        yield text_chunk
                                            if deprecation_detected:
                                                break
                                    except json.JSONDecodeError:
                                        continue
                    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout, EOFError) as eof_err:
                        logger.warning(f"CloudCode 网关流式推流中途断开 (EOF/RemoteProtocolError): {eof_err}")
                        # 首包 120 字符缓冲期内若尚未向外吐出 Token，直接无感重试下一候选模型
                        if not buffer_flushed and idx < len(candidate_models) - 1:
                            continue
                        raise GeminiAPIException(
                            status_code=502,
                            message=f"CloudCode 网关推流中断 (EOF/ConnectionReset): {eof_err}"
                        )

                    if deprecation_detected:
                        if idx < len(candidate_models) - 1:
                            next_model = candidate_models[idx + 1]
                            logger.warning(
                                f"模型 [{target_cc_model}] 返回版本/套餐受限提示，自动切换至候选模型 [{next_model}]"
                            )
                            if on_downgrade:
                                try:
                                    on_downgrade(model_name, next_model, "plan_quota_unsupported")
                                except Exception as cb_err:
                                    logger.warning(f"触发模型降级回调异常: {cb_err}")
                            continue
                        else:
                            yield "【提示】：该 Google 账号当前套餐方案暂未解锁该模型的访问权限（需 Google AI Pro/Ultra 方案），已自动为您保护并建议切换至 Gemini 2.5 Flash。"
                            return

                    if not buffer_flushed and buffered_chunks:
                        for bc in buffered_chunks:
                            yield bc
                    return

        # 3. 官方 AI Studio REST API Key 通道 (AIzaSy...)
        api_model_id = cls.resolve_model_id(model_name)
        headers: dict[str, str] = {}
        url = f"{cls.BASE_URL}/{api_model_id}:streamGenerateContent?alt=sse&key={cleaned_cred}"

        generation_config: dict[str, Any] = {"temperature": temperature}

        if thinking_level and thinking_level != "off" and ("argon" in api_model_id.lower() or "pro" in api_model_id.lower()):
            budget = cls.THINKING_BUDGET_MAP.get(thinking_level, 24576)
            generation_config["thinkingConfig"] = {"thinkingBudget": budget}

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config
        }
        
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    err_msg = error_text.decode("utf-8", errors="ignore")
                    raise GeminiAPIException(status_code=response.status_code, message=err_msg)

                first_byte_notified = False
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        if not first_byte_notified and on_first_byte:
                            first_byte_notified = True
                            try:
                                on_first_byte()
                            except Exception:
                                pass
                        raw_data = line[len("data: "):].strip()
                        if raw_data == "[DONE]":
                            break
                        try:
                            data_json = json.loads(raw_data)
                            usage_meta = data_json.get("usageMetadata")
                            if usage_meta and on_usage:
                                try:
                                    on_usage(usage_meta)
                                except Exception:
                                    pass
                            candidates = data_json.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                for part in parts:
                                    text = part.get("text")
                                    if text:
                                        yield text
                        except json.JSONDecodeError:
                            continue

    @classmethod
    async def test_key(
        cls,
        api_key: str,
        model_name: str = "gemini-2.0-flash-lite",
        auth_type: str = "api_key",
        strict_model: bool = False,
    ) -> dict[str, Any]:
        """测试凭证可用性与延迟（自适应网页 Cookie、Google OAuth CloudCode 或官方 API Key）"""
        cleaned_cred = cls._clean_credential(api_key)

        if not cleaned_cred:
            return {
                "valid": False,
                "latency_ms": 0,
                "status_code": 400,
                "error": "凭证为空，请提供有效 API Key 或完成授权"
            }

        # 网页 Cookie 模式探测
        is_web = (
            auth_type in ("web_cookie", "web_sidecar")
            or "psid" in cleaned_cred.lower()
            or "__Secure-" in cleaned_cred
        )

        if is_web:
            psid, psidts = cls.parse_web_cookie(cleaned_cred)
            probe_url = f"{settings.websidecar_url}/probe"
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(probe_url, json={"psid": psid, "psidts": psidts})
                    if resp.status_code == 200:
                        data = resp.json()
                        return {
                            "valid": data.get("success", False),
                            "latency_ms": data.get("latency_ms", 0),
                            "status_code": 200 if data.get("success") else 401,
                            "error": data.get("error") if not data.get("success") else None,
                            "message": data.get("message")
                        }
                    else:
                        return {
                            "valid": False,
                            "latency_ms": 0,
                            "status_code": resp.status_code,
                            "error": resp.text[:200]
                        }
            except httpx.ConnectError:
                return {
                    "valid": False,
                    "latency_ms": 0,
                    "status_code": 503,
                    "error": "无法连接至 Sidecar 网页反代容器 (gemini-web-bridge)，请检查容器是否已启动"
                }

        from ..core.proxy_helper import get_system_proxy_url
        proxy = get_system_proxy_url()
        client_kwargs: dict[str, Any] = {"timeout": 15.0}
        if proxy:
            client_kwargs["proxy"] = proxy

        is_oauth = cleaned_cred.startswith("ya29.") or auth_type in ("oauth_google_one", "oauth_personal")

        if is_oauth and not cleaned_cred.startswith("AIzaSy"):
            cc_model_id = cls.resolve_cloudcode_model_id(model_name)
            candidate_models = [cc_model_id]
            if cc_model_id == "gemini-2.5-pro":
                candidate_models.append("gemini-3-flash")
            elif not strict_model:
                candidate_models.extend(["gemini-2.5-flash", "gemini-2.5-flash-lite"])

            start = time.time()
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    active_token = cleaned_cred
                    if active_token[:32] in cls._FALLBACK_CACHE["legacy_tokens"]:
                        active_token = await cls._get_fallback_oauth_token(client) or active_token
                    project_id = await cls._resolve_cloudcode_project(active_token, client)
                    for idx, target_m in enumerate(candidate_models):
                        cc_url = f"{cls.CLOUDCODE_BASE_URL}:generateContent"
                        cc_headers = {
                            "Authorization": f"Bearer {active_token}",
                            "Content-Type": "application/json",
                            "User-Agent": cls.get_cloudcode_ua(),
                        }
                        cc_payload = {
                            "model": target_m,
                            "project": project_id,
                            "request": {
                                "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                                "generationConfig": {"maxOutputTokens": 5},
                            },
                        }
                        resp = await client.post(cc_url, headers=cc_headers, json=cc_payload)
                        if resp.status_code in (401, 403) and idx == 0:
                            fallback_tok = await cls._get_fallback_oauth_token(client, failed_token=active_token)
                            if fallback_tok and fallback_tok != active_token:
                                active_token = fallback_tok
                                project_id = await cls._resolve_cloudcode_project(active_token, client)
                                cc_headers["Authorization"] = f"Bearer {active_token}"
                                cc_payload["project"] = project_id
                                resp = await client.post(cc_url, headers=cc_headers, json=cc_payload)

                        latency_ms = round((time.time() - start) * 1000, 1)
                        if resp.status_code == 200:
                            resp_text = resp.text
                            # 深度校验：若模型虽然返回 200 但内容是版本受限警告，标为不可用
                            if cls.is_version_deprecation_message(resp_text):
                                return {
                                    "valid": False,
                                    "latency_ms": latency_ms,
                                    "status_code": 403,
                                    "error": "该 Google 账号当前套餐暂未解锁此高级模型权限（需开通 Google AI Pro/Ultra 方案）",
                                }
                            return {"valid": True, "latency_ms": latency_ms, "status_code": 200, "model": model_name}
                        if resp.status_code == 503 and idx < len(candidate_models) - 1:
                            continue
                        if resp.status_code == 429:
                            err_msg = "该模型当前配额受限或处于频控冷却 (429)"
                            try:
                                raw_err = resp.json().get("error", {}).get("message", "")
                                if raw_err:
                                    err_msg = raw_err[:120]
                            except Exception:
                                pass
                            return {
                                "valid": False if strict_model else True,
                                "latency_ms": latency_ms,
                                "status_code": 429,
                                "error": err_msg,
                            }
                        if resp.status_code == 404:
                            return {
                                "valid": False,
                                "latency_ms": latency_ms,
                                "status_code": 404,
                                "error": f"该模型标识 [{target_m}] 未在当前 Cloud Code 节点开放",
                            }
                        err_summary = resp.text[:160]
                        try:
                            err_summary = resp.json().get("error", {}).get("message", err_summary)
                        except Exception:
                            pass
                        return {
                            "valid": False,
                            "latency_ms": latency_ms,
                            "status_code": resp.status_code,
                            "error": f"OAuth 通道校验失败 ({resp.status_code}): {err_summary[:140]}",
                        }
            except Exception as e:
                return {
                    "valid": False,
                    "latency_ms": round((time.time() - start) * 1000, 1),
                    "status_code": 500,
                    "error": f"连接 Google Cloud Code 通道异常: {str(e)[:120]}",
                }

        # 官方 AI Studio API Key 探测
        test_models = [model_name] if strict_model else [model_name, "gemini-2.0-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"]

        for m in test_models:
            api_model_id = cls.resolve_model_id(m) if not strict_model else m
            headers: dict[str, str] = {}
            url = f"{cls.BASE_URL}/{api_model_id}:generateContent?key={cleaned_cred}"

            payload = {
                "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                "generationConfig": {"maxOutputTokens": 2}
            }
            start = time.time()
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    latency_ms = round((time.time() - start) * 1000, 1)
                    if resp.status_code == 200:
                        return {"valid": True, "latency_ms": latency_ms, "status_code": 200, "model": api_model_id}
                    elif resp.status_code == 429:
                        if strict_model:
                            return {
                                "valid": False,
                                "latency_ms": latency_ms,
                                "status_code": 429,
                                "error": "该模型当前配额受限或处于频控冷却 (429)"
                            }
                        return {"valid": True, "latency_ms": latency_ms, "status_code": 429, "error": "密钥有效，当前处于官方频控等待状态 (429)"}
                    elif resp.status_code == 503 and not strict_model:
                        continue
                    else:
                        err_summary = resp.text[:160]
                        try:
                            err_json = resp.json()
                            err_summary = err_json.get("error", {}).get("message", err_summary)
                        except Exception:
                            pass
                        return {
                            "valid": False,
                            "latency_ms": latency_ms,
                            "status_code": resp.status_code,
                            "error": f"HTTP {resp.status_code}: {err_summary[:140]}"
                        }
            except Exception as e:
                if strict_model:
                    return {
                        "valid": False,
                        "latency_ms": round((time.time() - start) * 1000, 1),
                        "status_code": 500,
                        "error": f"网络或连接超时: {str(e)[:120]}"
                    }
                continue

        return {"valid": False, "latency_ms": 0, "status_code": 500, "error": "探测超时或官方模型暂不可用"}
