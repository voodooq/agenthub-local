"""
多协议大模型统一接入与适配层 (ProviderAdapter)
支持四大通道类型：
1. google_pool: 原有 Google OAuth / Antigravity CLI 多账号算力池 (<50ms 429 自动接力)
2. gemini_api: 用户自定义 Google AI Studio API Key
3. openai_compat: 兼容 OpenAI / DeepSeek / 硅基流动 / OpenRouter / 本地 Ollama (http://127.0.0.1:11434/v1)
4. anthropic: 兼容 Claude 官方与中转 Messages API

同时支持「主力推理 (primary)」与「后台轻量记忆提炼 (light)」双角色绑定策略。
"""
import base64
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncGenerator
import httpx

from .logging_config import logger
from ..repository.db import db_manager
from ..service.gemini_client import GeminiClient


class ProviderAdapter:
    """多协议大模型供应商管理器与统一流式调用适配器"""

    @staticmethod
    def _encode_key(raw_key: str) -> str:
        if not raw_key:
            return ""
        return base64.b64encode(raw_key.strip().encode("utf-8")).decode("utf-8")

    @staticmethod
    def _decode_key(enc_key: str) -> str:
        if not enc_key:
            return ""
        try:
            return base64.b64decode(enc_key.encode("utf-8")).decode("utf-8")
        except Exception:
            return enc_key

    @staticmethod
    def _mask_key(raw_key: str) -> str:
        if not raw_key:
            return ""
        if len(raw_key) <= 8:
            return "****"
        return f"{raw_key[:4]}****{raw_key[-4:]}"

    @classmethod
    async def list_providers(cls) -> list[dict[str, Any]]:
        """获取所有已配置的大模型供应商列表（API Key 与 Refresh Token 自动脱敏，含实时冷却状态）"""
        now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM llm_providers ORDER BY priority ASC, created_at ASC"
            ) as cur:
                rows = await cur.fetchall()
                results: list[dict[str, Any]] = []
                for r in rows:
                    d = dict(r)
                    raw_k = cls._decode_key(d.get("api_key_encrypted") or "")
                    raw_ref = cls._decode_key(d.get("refresh_token_encrypted") or "")
                    d["api_key_masked"] = cls._mask_key(raw_k)
                    d["has_api_key"] = bool(raw_k)
                    d["has_refresh_token"] = bool(raw_ref)
                    d.pop("api_key_encrypted", None)
                    d.pop("refresh_token_encrypted", None)
                    cd_until = d.get("cooldown_until")
                    d["is_cooling_down"] = bool(cd_until and str(cd_until) > now_utc_str)
                    try:
                        d["models"] = json.loads(d.get("models_json") or "[]")
                    except Exception:
                        d["models"] = []
                    results.append(d)
                return results

    @classmethod
    async def get_enabled_providers_for_role(cls, role: str = "primary") -> list[dict[str, Any]]:
        """
        按角色绑定 ('primary' 主力推理 | 'light' 后台轻量提炼) 获取已启用且未处于 429 冷却期的供应商列表（按 priority 升序）
        """
        now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM llm_providers
                WHERE is_enabled = 1
                  AND (role_binding = ? OR role_binding = 'both')
                  AND (cooldown_until IS NULL OR cooldown_until <= ?)
                ORDER BY priority ASC
                """,
                (role, now_utc_str),
            ) as cur:
                rows = await cur.fetchall()
                items: list[dict[str, Any]] = []
                for r in rows:
                    d = dict(r)
                    d["api_key"] = cls._decode_key(d.get("api_key_encrypted") or "")
                    try:
                        d["models"] = json.loads(d.get("models_json") or "[]")
                    except Exception:
                        d["models"] = []
                    items.append(d)
                return items

    @classmethod
    async def mark_provider_cooldown(cls, provider_id: str, cooldown_seconds: int = 60) -> str:
        """当账号/供应商遇到 HTTP 429 时，立即置入冷却状态（默认锁定 60 秒）"""
        until_str = (datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        async with db_manager.get_connection() as conn:
            await conn.execute(
                "UPDATE llm_providers SET cooldown_until = ? WHERE id = ?",
                (until_str, provider_id),
            )
            await conn.commit()
        logger.warning(f"算力通道 [{provider_id}] 触发 429 限流，已置入冷却至 {until_str}")
        return until_str

    @classmethod
    async def save_provider(
        cls,
        name: str,
        provider_type: str,
        base_url: str = "",
        api_key: str = "",
        models: list[str] | None = None,
        role_binding: str = "both",
        tool_call_mode: str = "auto",
        priority: int = 10,
        is_enabled: int = 1,
        provider_id: str | None = None,
    ) -> dict[str, Any]:
        """新增或更新一个大模型供应商通道"""
        pid = provider_id or f"prov_{uuid.uuid4().hex[:8]}"
        valid_type = (
            provider_type
            if provider_type in ("google_pool", "google_oauth", "gemini_api", "openai_compat", "anthropic", "ollama")
            else "openai_compat"
        )
        effective_base_url = base_url.strip().rstrip("/")
        if valid_type == "ollama" and not effective_base_url:
            effective_base_url = "http://127.0.0.1:11434/v1"
        models_str = json.dumps(models or [], ensure_ascii=False)

        async with db_manager.get_connection() as conn:
            # 若是更新且未传新 api_key，保留原加密 key 与 refresh_token
            enc_key = cls._encode_key(api_key)
            enc_refresh = ""
            if provider_id:
                async with conn.execute(
                    "SELECT api_key_encrypted, refresh_token_encrypted FROM llm_providers WHERE id = ?",
                    (provider_id,),
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        if not api_key:
                            enc_key = row["api_key_encrypted"] or ""
                        enc_refresh = row["refresh_token_encrypted"] or ""

            await conn.execute(
                """
                INSERT OR REPLACE INTO llm_providers
                (id, name, provider_type, base_url, api_key_encrypted, refresh_token_encrypted, models_json, role_binding, tool_call_mode, priority, is_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    name.strip(),
                    valid_type,
                    effective_base_url,
                    enc_key,
                    enc_refresh,
                    models_str,
                    role_binding,
                    tool_call_mode,
                    int(priority),
                    1 if is_enabled else 0,
                ),
            )
            await conn.commit()

        return {
            "id": pid,
            "name": name.strip(),
            "provider_type": valid_type,
            "base_url": effective_base_url,
            "models": models or [],
            "role_binding": role_binding,
            "tool_call_mode": tool_call_mode,
            "priority": int(priority),
            "is_enabled": 1 if is_enabled else 0,
        }

    @classmethod
    async def delete_provider(cls, provider_id: str) -> bool:
        """删除自定义供应商（内置 google_pool 禁止删除，仅可禁用）"""
        if provider_id == "provider_google_pool":
            return False
        async with db_manager.get_connection() as conn:
            cur = await conn.execute("DELETE FROM llm_providers WHERE id = ?", (provider_id,))
            await conn.commit()
            return (cur.rowcount or 0) > 0

    @classmethod
    async def stream_openai_compatible(
        cls,
        base_url: str,
        api_key: str,
        model_name: str,
        messages: list[dict[str, Any]],
        system_instruction: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """
        调用标准 OpenAI 兼容接口 (/v1/chat/completions) 流式生成：
        无缝支持 DeepSeek、OpenAI、硅基流动、OpenRouter 以及本地 Ollama (http://127.0.0.1:11434/v1)
        """
        effective_base = (base_url or "https://api.openai.com/v1").rstrip("/")
        if not effective_base.endswith("/chat/completions"):
            endpoint = f"{effective_base}/chat/completions"
        else:
            endpoint = effective_base

        oai_messages: list[dict[str, str]] = []
        if system_instruction:
            oai_messages.append({"role": "system", "content": system_instruction})

        for m in messages:
            r = m.get("role", "user")
            role_mapped = "assistant" if r in ("model", "assistant") else "user"
            prefix = "[工具输出]: " if r == "tool" else ""
            oai_messages.append({"role": role_mapped, "content": f"{prefix}{m.get('content', '')}"})

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model_name,
            "messages": oai_messages,
            "temperature": temperature,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    err_body = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                    raise RuntimeError(f"OpenAI 兼容接口报错 ({resp.status_code}): {err_body}")

                async for line in resp.aiter_lines():
                    line_str = line.strip()
                    if not line_str or not line_str.startswith("data:"):
                        continue
                    data_part = line_str[5:].strip()
                    if data_part == "[DONE]":
                        break
                    try:
                        chunk_json = json.loads(data_part)
                        choices = chunk_json.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta") or {}
                            content_piece = delta.get("content") or ""
                            if content_piece:
                                yield content_piece
                    except Exception:
                        continue

    @classmethod
    async def stream_anthropic(
        cls,
        base_url: str,
        api_key: str,
        model_name: str,
        messages: list[dict[str, Any]],
        system_instruction: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """调用 Anthropic Claude (/v1/messages) 流式接口"""
        effective_base = (base_url or "https://api.anthropic.com/v1").rstrip("/")
        endpoint = (
            f"{effective_base}/messages"
            if not effective_base.endswith("/messages")
            else effective_base
        )

        claude_msgs: list[dict[str, str]] = []
        for m in messages:
            r = m.get("role", "user")
            role_mapped = "assistant" if r in ("model", "assistant") else "user"
            prefix = "[工具输出]: " if r == "tool" else ""
            claude_msgs.append({"role": role_mapped, "content": f"{prefix}{m.get('content', '')}"})

        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        payload: dict[str, Any] = {
            "model": model_name,
            "max_tokens": 4096,
            "messages": claude_msgs,
            "temperature": temperature,
            "stream": True,
        }
        if system_instruction:
            payload["system"] = system_instruction

        async with httpx.AsyncClient(timeout=90.0) as client:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    err_body = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                    raise RuntimeError(f"Anthropic 接口报错 ({resp.status_code}): {err_body}")

                async for line in resp.aiter_lines():
                    line_str = line.strip()
                    if not line_str.startswith("data:"):
                        continue
                    data_part = line_str[5:].strip()
                    try:
                        ev = json.loads(data_part)
                        if ev.get("type") == "content_block_delta":
                            text_piece = (ev.get("delta") or {}).get("text") or ""
                            if text_piece:
                                yield text_piece
                    except Exception:
                        continue

    @classmethod
    async def generate_light_background_text(cls, prompt: str, system_prompt: str = "") -> str | None:
        """
        使用绑定为 'light'（后台轻量记忆提炼与摘要模型）的算力通道执行低成本文本提炼。
        若未单独配置或调用失败，安全返回 None（回退零消耗启发式提取）。
        """
        providers = await cls.get_enabled_providers_for_role("light")
        for prov in providers:
            ptype = prov.get("provider_type")
            models = prov.get("models") or []
            target_model = models[0] if models else "gpt-4o-mini"

            try:
                if ptype == "openai_compat" and (prov.get("api_key") or "11434" in (prov.get("base_url") or "")):
                    out = []
                    async for ch in cls.stream_openai_compatible(
                        base_url=prov.get("base_url") or "",
                        api_key=prov.get("api_key") or "",
                        model_name=target_model,
                        messages=[{"role": "user", "content": prompt}],
                        system_instruction=system_prompt,
                        temperature=0.2,
                    ):
                        out.append(ch)
                    return "".join(out).strip()

                elif ptype == "gemini_api" and prov.get("api_key"):
                    out = []
                    async for ch in GeminiClient.stream_generate_content(
                        api_key=prov["api_key"],
                        model_name=target_model if "gemini" in target_model else "gemini-2.5-flash",
                        contents=[{"role": "user", "parts": [{"text": prompt}]}],
                        system_instruction=system_prompt,
                        thinking_level="low",
                        temperature=0.2,
                        auth_type="api_key",
                    ):
                        out.append(ch)
                    return "".join(out).strip()
            except Exception as e:
                logger.debug(f"后台轻量模型 [{prov.get('name')}] 调用跳过: {e}")
                continue
        return None


provider_adapter = ProviderAdapter()

