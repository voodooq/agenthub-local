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
    统一客户端：支持官方 API 与网页端 Sidecar 双通道
    """
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

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
        auth_type: str = "api_key"
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
                                    if choices:
                                        delta = choices[0].get("delta", {}).get("content", "")
                                        if delta:
                                            yield delta
                                except json.JSONDecodeError:
                                    continue
                    return
                except httpx.ConnectError:
                    raise GeminiAPIException(
                        status_code=503,
                        message="无法连接至 Sidecar 网页反代容器 (gemini-web-bridge)，请检查容器运行状态"
                    )

        # 2. 官方 REST API 通道
        api_model_id = cls.resolve_model_id(model_name)
        headers: dict[str, str] = {}

        if cleaned_cred.startswith("AIzaSy"):
            url = f"{cls.BASE_URL}/{api_model_id}:streamGenerateContent?alt=sse&key={cleaned_cred}"
        elif cleaned_cred.startswith("ya29.") or auth_type == "oauth_google_one":
            url = f"{cls.BASE_URL}/{api_model_id}:streamGenerateContent?alt=sse"
            headers["Authorization"] = f"Bearer {cleaned_cred}"
        else:
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

        from ..core.proxy_helper import get_system_proxy_url
        proxy = get_system_proxy_url()
        client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(90.0, connect=10.0)}
        if proxy:
            client_kwargs["proxy"] = proxy

        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    err_msg = error_text.decode("utf-8", errors="ignore")
                    
                    if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in err_msg:
                        clean_msg = (
                            "Google 权限限制 (403): 当前 OAuth 令牌缺少 generative-language 范围。\n"
                            "💡 建议方案：直接访问 aistudio.google.com 为该账号免费生成专属官方 API Key（以 AIzaSy 开头）录入系统，即可秒级畅通调用！"
                        )
                        raise GeminiAPIException(status_code=403, message=clean_msg)
                    
                    raise GeminiAPIException(status_code=response.status_code, message=err_msg)

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        raw_data = line[len("data: "):].strip()
                        if raw_data == "[DONE]":
                            break
                        try:
                            data_json = json.loads(raw_data)
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
        """测试凭证可用性与延迟（自适应网页 Cookie 或官方 API Key）"""
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
        client_kwargs: dict[str, Any] = {"timeout": 10.0}
        if proxy:
            client_kwargs["proxy"] = proxy

        # 官方 API 探测
        test_models = [model_name] if strict_model else [model_name, "gemini-2.0-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"]

        for m in test_models:
            api_model_id = cls.resolve_model_id(m) if not strict_model else m
            headers: dict[str, str] = {}
            if cleaned_cred.startswith("AIzaSy"):
                url = f"{cls.BASE_URL}/{api_model_id}:generateContent?key={cleaned_cred}"
            elif cleaned_cred.startswith("ya29.") or auth_type == "oauth_google_one":
                url = f"{cls.BASE_URL}/{api_model_id}:generateContent"
                headers["Authorization"] = f"Bearer {cleaned_cred}"
            else:
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
                    elif resp.status_code == 403 and "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in resp.text:
                        return {
                            "valid": False,
                            "latency_ms": latency_ms,
                            "status_code": 403,
                            "error": "OAuth 令牌权限不足：缺少 generative-language 范围。"
                        }
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
