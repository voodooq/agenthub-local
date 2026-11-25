from __future__ import annotations

import json
import re
import time
from typing import Any
import httpx

from ..core.logging_config import logger


class LightLLMRouter:
    """后台轻量任务专用路由（口语化 HyPE / 记忆提炼 / 摘要），完全由算力连接池配置驱动。"""

    def __init__(self, cache_ttl_sec: int = 60) -> None:
        self._cache_ttl = cache_ttl_sec
        self._cached_target: dict[str, Any] | None = None
        self._cache_ts: float = 0.0

    def invalidate_cache(self) -> None:
        """连接池增删改或启停状态变更时调用，立即触发热生效。"""
        self._cached_target = None
        self._cache_ts = 0.0
        logger.info("[LightLLMRouter] 连接池配置已更新，轻量模型路由缓存已清空")

    async def fetch_pool_accounts(self) -> list[dict[str, Any]]:
        """从底层数据库中动态拉取当前所有已配置的大模型与算力节点。"""
        from ..core.provider_adapter import provider_adapter
        from ..repository.db import db_manager

        nodes: list[dict[str, Any]] = []
        try:
            async with db_manager.get_connection() as conn:
                async with conn.execute(
                    "SELECT * FROM llm_providers ORDER BY priority ASC, created_at ASC"
                ) as cur:
                    rows = await cur.fetchall()
                    for r in rows:
                        d = dict(r)
                        d["api_key"] = provider_adapter._decode_key(d.get("api_key_encrypted") or "")
                        try:
                            d["models"] = json.loads(d.get("models_json") or "[]")
                        except Exception:
                            d["models"] = []
                        if d.get("models") and not d.get("model_name"):
                            d["model_name"] = d["models"][0]
                        d["is_enabled"] = bool(d.get("is_enabled", 1))
                        nodes.append(d)
        except Exception as e:
            logger.debug(f"[LightLLMRouter] 读取 llm_providers 失败: {e}")

        try:
            from ..repository.account_repository import AccountRepository
            accs = await AccountRepository.get_all()
            for a in accs:
                nodes.append({
                    "id": a.id,
                    "name": a.name,
                    "provider_type": "gemini_cli",
                    "role_binding": getattr(a, "role_binding", "primary"),
                    "is_enabled": getattr(a, "is_active", True) and a.status == "active",
                    "priority": 99,
                })
        except Exception as e:
            logger.debug(f"[LightLLMRouter] 读取 accounts 失败: {e}")

        return nodes

    def resolve_target(self, accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
        """从算力连接池中按角色优先级选取轻量模型节点。"""
        now = time.time()
        if self._cached_target and (now - self._cache_ts) < self._cache_ttl:
            return self._cached_target

        # 过滤禁用、纯向量与主力专用节点
        valid_accounts = [
            acc for acc in accounts
            if acc.get("is_enabled", True)
            and acc.get("role_binding") not in ("embedding_only", "primary")
            and not any(
                k in (acc.get("model_name") or "").lower()
                for k in ("bge", "embed", "m3e", "nomic")
            )
        ]

        # Tier 1: 显式绑定为 light 的节点
        light_nodes = [a for a in valid_accounts if a.get("role_binding") == "light"]
        if light_nodes:
            light_nodes.sort(
                key=lambda x: (x.get("provider_type") == "gemini_cli", x.get("priority", 99))
            )
            return self._set_cache(self._wrap_account(light_nodes[0]))

        # Tier 2: 兜底使用 both 节点（HTTP API 优先于 CLI 子进程）
        both_nodes = [a for a in valid_accounts if a.get("role_binding") in ("both", None, "")]
        if both_nodes:
            both_nodes.sort(
                key=lambda x: (x.get("provider_type") == "gemini_cli", x.get("priority", 99))
            )
            return self._set_cache(self._wrap_account(both_nodes[0]))

        return None

    def _wrap_account(self, acc: dict[str, Any]) -> dict[str, Any]:
        if acc.get("provider_type") == "gemini_cli":
            return {
                "mode": "engine_pool",
                "account": acc,
                "source": acc.get("name", "cli_both"),
            }
        return {
            "mode": "openai_http",
            "base_url": (acc.get("base_url") or "").rstrip("/"),
            "api_key": acc.get("api_key") or "sk-local",
            "model": acc.get("model_name") or (acc.get("models") and acc.get("models")[0]) or "default",
            "source": acc.get("name", "pool_light"),
        }

    def _set_cache(self, target: dict[str, Any]) -> dict[str, Any]:
        self._cached_target = target
        self._cache_ts = time.time()
        logger.info(
            f"[LightLLMRouter] 锁定后台轻量节点: {target['source']} "
            f"(mode={target['mode']}, model={target.get('model', 'cli')})"
        )
        return target

    @classmethod
    def is_cloud_target(cls, target: dict[str, Any] | None) -> bool:
        """判定当前轻量节点是否为云端高速 HTTP 通道（非本地回环地址）。"""
        if not target or target.get("mode") != "openai_http":
            return False
        base_url = str(target.get("base_url") or "").lower()
        return not any(host in base_url for host in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))

    async def complete(
        self,
        prompt: str,
        accounts: list[dict[str, Any]] | None = None,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 512,
        enable_thinking: bool = False,
    ) -> str | None:
        """执行轻量推理；仅直连 OpenAI 兼容 HTTP 节点，不抢占 Primary 或拉起 CLI。"""
        if accounts is None:
            accounts = await self.fetch_pool_accounts()

        target = self.resolve_target(accounts)
        if not target:
            logger.info("[LightLLMRouter] 连接池暂未配置可用的 Light/Both 节点，跳过本次后台推理")
            return None

        if target["mode"] != "openai_http":
            logger.warning(
                f"[LightLLMRouter] 仅命中 CLI 节点 ({target['source']})，为防止并发抖动跳过后台批处理"
            )
            return None

        if not target["base_url"]:
            logger.warning(f"[LightLLMRouter] 节点 {target['source']} 未配置 base_url")
            return None

        model_name = str(target.get("model") or "")
        user_content = prompt
        # 仅在模型名包含 qwen3 且关闭思考时追加 /no_think 软指令，避免影响其他模型
        if not enable_thinking and "qwen3" in model_name.lower() and "/no_think" not in user_content:
            user_content = f"{user_content.rstrip()}\n/no_think"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max(64, int(max_tokens)),
            "stream": False,
        }
        if not enable_thinking:
            # 关闭 Qwen3 等模型默认思考模式，避免无意义的 <think> 消耗 token 或被截断
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Authorization": f"Bearer {target['api_key']}"}

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(
                    f"{target['base_url']}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"] or ""
                # 兜底清理思考标签（兼容已闭合与未闭合的 <think>）
                content = re.sub(r"<think>.*?(</think>|$)", "", content, flags=re.DOTALL).strip()
                return content
        except Exception as e:
            logger.warning(f"[LightLLMRouter] 调用节点 {target['source']} 失败，清空缓存: {e}")
            self.invalidate_cache()
            return None


light_llm_router = LightLLMRouter()

