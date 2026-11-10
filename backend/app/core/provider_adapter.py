"""
多协议大模型统一接入与适配层 (ProviderAdapter)
支持通道类型：
1. google_pool: 原有 Google OAuth / Antigravity CLI 多账号算力池 (<50ms 429 自动接力)
2. gemini_api: 用户自定义 Google AI Studio API Key
3. openai_compat: 兼容 OpenAI / DeepSeek / 硅基流动 / OpenRouter / 本地 Ollama (http://127.0.0.1:11434/v1)
4. llama_server: 本地 llama.cpp 原生服务 (http://127.0.0.1:8080/v1)，支持 /health、/props 单槽位探针、/tokenize 与 timings
5. anthropic: 兼容 Claude 官方与中转 Messages API

同时支持「主力推理 (primary)」与「后台轻量记忆提炼 (light)」双角色绑定策略。
"""
import base64
import json
import time
from typing import Any, AsyncGenerator, Callable
import uuid
from datetime import datetime, timezone, timedelta
import httpx

from .logging_config import logger
from ..repository.db import db_manager
from ..service.gemini_client import GeminiClient

# 进程级缓存：不支持 stream_options.include_usage 的第三方兼容节点 URL 集合
_STREAM_OPTIONS_UNSUPPORTED_HOSTS: set[str] = set()
# 进程级缓存：本地服务 /props 槽位与 n_ctx 探测结果 {host: (ts, info_dict)}，TTL 60 秒
_LOCAL_SLOTS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LOCAL_SLOTS_TTL_SECS = 60.0


def _extract_host_root(base_url: str, default_host: str = "http://127.0.0.1:8080") -> str:
    """从 OpenAI 兼容 Base URL 推导宿主根地址（剥离 /v1 与 /chat/completions）"""
    clean = (base_url or "").strip().rstrip("/")
    if not clean:
        return default_host
    if clean.endswith("/chat/completions"):
        clean = clean[:-len("/chat/completions")].rstrip("/")
    if clean.endswith("/v1"):
        clean = clean[:-3].rstrip("/")
    return clean or default_host


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
        max_context_tokens: int = 0,
    ) -> dict[str, Any]:
        """新增或更新一个大模型供应商通道（支持 llama_server 与 max_context_tokens 上限配置）"""
        pid = provider_id or f"prov_{uuid.uuid4().hex[:8]}"
        valid_type = (
            provider_type
            if provider_type in (
                "google_pool",
                "google_oauth",
                "gemini_api",
                "openai_compat",
                "anthropic",
                "ollama",
                "llama_server",
            )
            else "openai_compat"
        )
        effective_base_url = base_url.strip().rstrip("/")
        if valid_type == "ollama" and not effective_base_url:
            effective_base_url = "http://127.0.0.1:11434/v1"
        elif valid_type == "llama_server" and not effective_base_url:
            effective_base_url = "http://127.0.0.1:8080/v1"

        resolved_max_ctx = max(0, int(max_context_tokens or 0))
        if resolved_max_ctx == 0 and valid_type in ("llama_server", "ollama"):
            resolved_max_ctx = 8192

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
                (id, name, provider_type, base_url, api_key_encrypted, refresh_token_encrypted, models_json, role_binding, tool_call_mode, priority, is_enabled, max_context_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    resolved_max_ctx,
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
            "max_context_tokens": resolved_max_ctx,
        }

    @classmethod
    async def probe_local_slots(cls, base_url: str, force: bool = False) -> dict[str, Any]:
        """探测本地 llama-server 的 /props 元信息（单槽位 n_ctx 与并发 total_slots），带 60s 缓存。

        NOTE: 在 llama.cpp 机制中 `-c` 为全部槽位共享总上下文池，
        `/props` 返回的 `default_generation_settings.n_ctx` 即服务端已除以 `total_slots` 后的单槽位真实可用上限。
        """
        host_root = _extract_host_root(base_url)
        now = time.time()
        if not force and host_root in _LOCAL_SLOTS_CACHE:
            cached_ts, cached_info = _LOCAL_SLOTS_CACHE[host_root]
            if now - cached_ts < _LOCAL_SLOTS_TTL_SECS:
                return cached_info

        info: dict[str, Any] = {"detected_n_ctx": 0, "total_slots": 0}
        try:
            async with httpx.AsyncClient(timeout=1.2) as client:
                resp = await client.get(f"{host_root}/props")
                if resp.status_code == 200:
                    data = resp.json()
                    gen_settings = data.get("default_generation_settings") or {}
                    n_ctx = int(gen_settings.get("n_ctx") or 0)
                    slots = int(data.get("total_slots") or 0)
                    info = {"detected_n_ctx": n_ctx, "total_slots": slots}
        except Exception:
            pass

        _LOCAL_SLOTS_CACHE[host_root] = (now, info)
        return info

    @classmethod
    async def tokenize_count(
        cls,
        base_url: str,
        content: str,
        timeout: float = 0.8,
    ) -> int | None:
        """调用 llama-server 原生 POST /tokenize 精准测算完整文本 Token 数；失败或超时即返回 None"""
        if not content:
            return 0
        host_root = _extract_host_root(base_url)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{host_root}/tokenize",
                    json={"content": content, "add_special": True},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tokens = data.get("tokens")
                    if isinstance(tokens, list):
                        return len(tokens)
        except Exception:
            pass
        return None

    @classmethod
    async def delete_provider(cls, provider_id: str) -> bool:
        """删除指定大模型通道供应商（彻底放开删除权限，无任何死锁）"""
        async with db_manager.get_connection() as conn:
            cur = await conn.execute("DELETE FROM llm_providers WHERE id = ?", (provider_id,))
            # 若属于 google_oauth，同步清理 accounts 表中的对应记录
            if provider_id.startswith("google_oauth_"):
                slug = provider_id.replace("google_oauth_", "")
                await conn.execute("DELETE FROM accounts WHERE name LIKE ? OR email LIKE ?", (f"%{slug}%", f"%{slug}%"))
            await conn.commit()
            return (cur.rowcount or 0) > 0

    @classmethod
    async def test_connection(
        cls,
        provider_type: str,
        base_url: str = "",
        api_key: str = "",
        model_name: str = "",
    ) -> dict[str, Any]:
        """
        探测指定供应商或自定义 Base URL 的实时连通性、网络延迟与可用模型列表。
        """
        import time
        start_t = time.time()
        ptype = provider_type.strip().lower()

        # 1. 本地 Ollama 探测 (http://127.0.0.1:11434)
        if ptype == "ollama" or "11434" in base_url:
            clean_host = (base_url or "http://127.0.0.1:11434").rstrip("/")
            if clean_host.endswith("/v1"):
                clean_host = clean_host[:-3]
            tags_url = f"{clean_host}/api/tags"
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    resp = await client.get(tags_url)
                    latency = int((time.time() - start_t) * 1000)
                    if resp.status_code == 200:
                        data = resp.json()
                        models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                        return {
                            "success": True,
                            "latency_ms": latency,
                            "models": models,
                            "message": f"成功连通本地 Ollama 服务！已发现 {len(models)} 个本地模型（延迟: {latency}ms）",
                        }
                    return {
                        "success": False,
                        "latency_ms": latency,
                        "models": [],
                        "message": f"Ollama 服务返回状态码 {resp.status_code}: {resp.text[:100]}",
                    }
            except Exception as e:
                return {
                    "success": False,
                    "latency_ms": int((time.time() - start_t) * 1000),
                    "models": [],
                    "message": f"连接本地 Ollama 服务失败 (请确认 Ollama 已启动): {str(e)}",
                }

        # 1.5 本地 llama-server (llama.cpp) 专属探测：/health + /v1/models + /props 单槽位嗅探
        if ptype == "llama_server":
            host_root = _extract_host_root(base_url, default_host="http://127.0.0.1:8080")
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    health_resp = await client.get(f"{host_root}/health")
                    latency = int((time.time() - start_t) * 1000)
                    if health_resp.status_code == 503:
                        return {
                            "success": False,
                            "latency_ms": latency,
                            "models": [],
                            "message": "llama-server 正在加载 GGUF 模型权重中 (503 Loading model)，请稍等数秒后再试。",
                        }
                    if health_resp.status_code != 200:
                        return {
                            "success": False,
                            "latency_ms": latency,
                            "models": [],
                            "message": f"llama-server /health 返回异常状态 {health_resp.status_code}: {health_resp.text[:120]}",
                        }

                    model_list: list[str] = []
                    try:
                        m_resp = await client.get(f"{host_root}/v1/models")
                        if m_resp.status_code == 200:
                            m_data = m_resp.json()
                            if isinstance(m_data, dict) and isinstance(m_data.get("data"), list):
                                model_list = [item.get("id") for item in m_data["data"] if item.get("id")]
                    except Exception:
                        pass

                    props_info = await cls.probe_local_slots(host_root, force=True)
                    detected_n_ctx = int(props_info.get("detected_n_ctx") or 0)
                    total_slots = int(props_info.get("total_slots") or 0)

                    msg_parts = [f"成功连通本地 llama-server！延迟 {latency}ms"]
                    if model_list:
                        msg_parts.append(f"已加载模型: {model_list[0]}")
                    if detected_n_ctx > 0:
                        slot_hint = (
                            f"，并发槽位={total_slots}（单槽位真实 n_ctx={detected_n_ctx:,}"
                            + ("；单槽位模式下旁路清洗将自动走零开销规则提取" if total_slots == 1 else "")
                            + "）"
                        )
                        msg_parts.append(slot_hint)

                    return {
                        "success": True,
                        "latency_ms": latency,
                        "models": model_list[:20],
                        "detected_n_ctx": detected_n_ctx,
                        "total_slots": total_slots,
                        "message": "".join(msg_parts),
                    }
            except Exception as e:
                return {
                    "success": False,
                    "latency_ms": int((time.time() - start_t) * 1000),
                    "models": [],
                    "message": f"连接本地 llama-server ({host_root}) 失败，请确认已启动 llama-server 服务: {str(e)}",
                }

        # 2. 通用 OpenAI 兼容 / DeepSeek / 硅基流动 / Gemini API 探测
        effective_base = (base_url or "https://api.openai.com/v1").rstrip("/")
        if effective_base.endswith("/chat/completions"):
            models_url = effective_base.replace("/chat/completions", "/models")
        else:
            models_url = f"{effective_base}/models"

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(models_url, headers=headers)
                latency = int((time.time() - start_t) * 1000)
                if resp.status_code == 200:
                    data = resp.json()
                    model_list = []
                    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                        model_list = [item.get("id") for item in data["data"] if item.get("id")]
                    elif isinstance(data, list):
                        model_list = [item.get("id") for item in data if isinstance(item, dict) and item.get("id")]

                    msg = f"连接成功！延迟 {latency}ms"
                    if model_list:
                        msg += f"，探测到 {len(model_list)} 个可用模型"
                    return {
                        "success": True,
                        "latency_ms": latency,
                        "models": model_list[:20],
                        "message": msg,
                    }
                elif resp.status_code in (401, 403):
                    return {
                        "success": False,
                        "latency_ms": latency,
                        "models": [],
                        "message": f"认证失败 (HTTP {resp.status_code}): API Key 无效或权限不足",
                    }
                else:
                    return {
                        "success": False,
                        "latency_ms": latency,
                        "models": [],
                        "message": f"端点返回状态码 {resp.status_code}: {resp.text[:100]}",
                    }
        except Exception as e:
            return {
                "success": False,
                "latency_ms": int((time.time() - start_t) * 1000),
                "models": [],
                "message": f"网络连通失败: {str(e)}",
            }

    @classmethod
    async def stream_openai_compatible(
        cls,
        base_url: str,
        api_key: str,
        model_name: str,
        messages: list[dict[str, Any]],
        system_instruction: str | None = None,
        temperature: float = 0.7,
        on_usage: Callable[[dict[str, Any] | None, dict[str, Any] | None], None] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        调用标准 OpenAI 兼容接口 (/v1/chat/completions) 流式生成：
        无缝支持 DeepSeek、OpenAI、硅基流动、OpenRouter、本地 Ollama 以及 llama-server (timings/cache_n)
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

        include_usage = endpoint not in _STREAM_OPTIONS_UNSUPPORTED_HOSTS
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": oai_messages,
            "temperature": temperature,
            "stream": True,
        }
        if include_usage:
            payload["stream_options"] = {"include_usage": True}

        async with httpx.AsyncClient(timeout=90.0) as client:
            for attempt in range(2):
                async with client.stream("POST", endpoint, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        err_body = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                        # 若第三方兼容中转拒收 stream_options，则自动记忆剔除该字段并当场重连重试
                        if (
                            attempt == 0
                            and "stream_options" in payload
                            and resp.status_code in (400, 422)
                            and any(k in err_body.lower() for k in ("stream_options", "extra inputs", "unrecognized", "unknown field"))
                        ):
                            _STREAM_OPTIONS_UNSUPPORTED_HOSTS.add(endpoint)
                            payload.pop("stream_options", None)
                            logger.info(f"[ProviderAdapter] 节点 {endpoint} 不支持 stream_options，已自动降级移除并透明重试")
                            continue
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
                            chunk_usage = chunk_json.get("usage")
                            chunk_timings = chunk_json.get("timings")
                            if (chunk_usage or chunk_timings) and on_usage:
                                try:
                                    on_usage(chunk_usage, chunk_timings)
                                except Exception:
                                    pass
                            choices = chunk_json.get("choices") or []
                            if choices:
                                delta = choices[0].get("delta") or {}
                                content_piece = delta.get("content") or ""
                                if content_piece:
                                    yield content_piece
                        except Exception:
                            continue
                    return

    @classmethod
    async def stream_anthropic(
        cls,
        base_url: str,
        api_key: str,
        model_name: str,
        messages: list[dict[str, Any]],
        system_instruction: str | None = None,
        temperature: float = 0.7,
        on_usage: Callable[[dict[str, Any] | None, dict[str, Any] | None], None] | None = None,
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
                        ev_type = ev.get("type")
                        if ev_type == "message_start" and on_usage:
                            msg_usage = (ev.get("message") or {}).get("usage")
                            if isinstance(msg_usage, dict):
                                try:
                                    on_usage(msg_usage, None)
                                except Exception:
                                    pass
                        elif ev_type == "message_delta" and on_usage:
                            delta_usage = ev.get("usage")
                            if isinstance(delta_usage, dict):
                                try:
                                    on_usage(delta_usage, None)
                                except Exception:
                                    pass
                        elif ev_type == "content_block_delta":
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
                if ptype in ("openai_compat", "ollama", "llama_server") and (
                    prov.get("api_key") or ptype in ("ollama", "llama_server") or "11434" in (prov.get("base_url") or "")
                ):
                    default_url = (
                        "http://127.0.0.1:8080/v1"
                        if ptype == "llama_server"
                        else ("http://127.0.0.1:11434/v1" if ptype == "ollama" else "")
                    )
                    out = []
                    async for ch in cls.stream_openai_compatible(
                        base_url=prov.get("base_url") or default_url,
                        api_key=prov.get("api_key") or ("local" if ptype in ("ollama", "llama_server") else ""),
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

