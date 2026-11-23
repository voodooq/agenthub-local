"""
NVIDIA Auto Ontology 异步客户端与本地连接配置管理

对接已部署的 Auto Ontology Next.js 公开网关（x-api-key 鉴权）：
1. GET  /api/terms              -> 检索业务术语、字段口径与关联表名
2. POST /api/chat/completions   -> SSE 流式发起自然语言查询并提取 answer / sql_code / rows
"""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from ..core.logging_config import logger
from ..repository.db import db_manager


def parse_auto_ontology_rows(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """统一拆箱 Auto Ontology 返回的 sql_response_from_db。

    兼容 ReAct 模式返回的 ['[{...}]']（JSON 字符串列表）、裸 JSON 字符串、
    标准 list[dict] 以及列式 columns+rows 结构。
    """
    raw = answer.get("sql_response_from_db")
    if raw is None:
        return []

    # 1. 拆箱 JSON 字符串列表（如 ['[{"省份名称": "北京市", "运动员数量": 420}]']）
    if isinstance(raw, list) and raw and all(isinstance(item, str) for item in raw):
        unpacked: list[Any] = []
        for item in raw:
            text = item.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    unpacked.extend(parsed)
                elif isinstance(parsed, dict):
                    unpacked.append(parsed)
            except json.JSONDecodeError:
                continue
        raw = unpacked
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []

    # 2. 兼容列式 {"columns": [...], "rows": [[...], ...]}
    if isinstance(raw, dict) and isinstance(raw.get("columns"), list) and isinstance(raw.get("rows"), list):
        cols = [str(c) for c in raw["columns"]]
        return [dict(zip(cols, r)) for r in raw["rows"] if isinstance(r, (list, tuple))]

    # 3. 兼容二维数组配合 answer["sql_columns"]
    if isinstance(raw, list):
        if raw and all(isinstance(r, (list, tuple)) for r in raw):
            cols = answer.get("sql_columns")
            if isinstance(cols, list) and cols:
                str_cols = [str(c) for c in cols]
                return [dict(zip(str_cols, r)) for r in raw]
        return [row for row in raw if isinstance(row, dict)]

    return []


def mask_api_key(raw_key: str) -> str:
    """对 API Token 进行首尾保留掩码脱敏，绝不向前端或日志明文泄露。"""
    key = (raw_key or "").strip()
    if not key:
        return ""
    if len(key) <= 12:
        return "*" * len(key)
    return f"{key[:14]}...{key[-4:]}"


def _normalize_base_url(url: str) -> str:
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw


async def _ensure_table(conn: Any) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auto_ontology_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            base_url TEXT NOT NULL DEFAULT '',
            api_key TEXT NOT NULL DEFAULT '',
            timeout_seconds INTEGER NOT NULL DEFAULT 300,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


async def get_auto_ontology_config(masked: bool = True) -> dict[str, Any]:
    """读取本地持久化的 Auto Ontology 连接配置。"""
    async with db_manager.get_connection() as conn:
        await _ensure_table(conn)
        cur = await conn.execute(
            "SELECT base_url, api_key, timeout_seconds, enabled FROM auto_ontology_settings WHERE id = 1;"
        )
        row = await cur.fetchone()
        if not row:
            return {
                "base_url": "",
                "api_key": "",
                "has_api_key": False,
                "timeout_seconds": 300,
                "enabled": False,
            }
        raw_key = str(row["api_key"] or "")
        return {
            "base_url": str(row["base_url"] or ""),
            "api_key": mask_api_key(raw_key) if masked else raw_key,
            "has_api_key": bool(raw_key.strip()),
            "timeout_seconds": int(row["timeout_seconds"] or 300),
            "enabled": bool(row["enabled"]) and bool(str(row["base_url"] or "").strip()) and bool(raw_key.strip()),
        }


async def save_auto_ontology_config(
    *,
    base_url: str,
    api_key: str | None = None,
    timeout_seconds: int = 300,
    enabled: bool = True,
) -> dict[str, Any]:
    """保存或更新 Auto Ontology 连接配置（支持传空/掩码时保留原密钥）。"""
    clean_url = _normalize_base_url(base_url)
    timeout_val = max(15, min(900, int(timeout_seconds or 300)))
    enabled_int = 1 if enabled else 0

    async with db_manager.get_connection() as conn:
        await _ensure_table(conn)
        cur = await conn.execute("SELECT api_key FROM auto_ontology_settings WHERE id = 1;")
        existing = await cur.fetchone()
        prev_key = str(existing["api_key"] or "") if existing else ""

        incoming_key = (api_key or "").strip()
        # 若传入的是脱敏串（含 ...）或空字符串，则沿用已有密钥
        if not incoming_key or ("..." in incoming_key and prev_key and incoming_key == mask_api_key(prev_key)):
            final_key = prev_key
        else:
            final_key = incoming_key

        await conn.execute(
            """
            INSERT INTO auto_ontology_settings (id, base_url, api_key, timeout_seconds, enabled, updated_at)
            VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                base_url = excluded.base_url,
                api_key = excluded.api_key,
                timeout_seconds = excluded.timeout_seconds,
                enabled = excluded.enabled,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (clean_url, final_key, timeout_val, enabled_int),
        )
        await conn.commit()

    return await get_auto_ontology_config(masked=True)


class AutoOntologyClient:
    """NVIDIA Auto Ontology REST API 异步客户端。"""

    _TERMS_TTL_SECONDS = 600.0
    _terms_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def __init__(self, base_url: str, api_key: str, timeout_seconds: int = 300) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = max(15, min(900, int(timeout_seconds or 300)))

    @classmethod
    async def from_saved_config(cls) -> AutoOntologyClient | None:
        """从持久化配置构建客户端；未启用或缺少参数时返回 None。"""
        cfg = await get_auto_ontology_config(masked=False)
        if not cfg["enabled"] or not cfg["base_url"] or not cfg["api_key"]:
            return None
        return cls(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            timeout_seconds=cfg["timeout_seconds"],
        )

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Accept": "application/json",
        }

    def _client_kwargs(self, timeout: float) -> dict[str, Any]:
        # 对内网/直连服务器不强制走本地代理，避免误拦截内网地址
        host = (urlparse(self.base_url).hostname or "").lower()
        trust_env = host not in ("127.0.0.1", "localhost", "::1")
        return {"timeout": timeout, "trust_env": trust_env}

    async def health_check(self) -> dict[str, Any]:
        """探测 GET /api/terms?limit=1，快速校验连通性与 API Token 权限。"""
        if not self.base_url or not self.api_key:
            raise ValueError("Auto Ontology 服务地址与 API Token 不能为空")
        url = f"{self.base_url}/api/terms"
        async with httpx.AsyncClient(**self._client_kwargs(10.0)) as client:
            resp = await client.get(url, headers=self._headers(), params={"limit": 1})
            if resp.status_code == 401:
                raise PermissionError("API Token 无效或已被撤销（HTTP 401）")
            resp.raise_for_status()
        return {"ok": True}

    async def test_connection(self) -> dict[str, Any]:
        """测试连通性与 API Token 有效性，返回样例术语与已识别库名。"""
        if not self.base_url or not self.api_key:
            raise ValueError("Auto Ontology 服务地址与 API Token 不能为空")

        url = f"{self.base_url}/api/terms"
        async with httpx.AsyncClient(**self._client_kwargs(15.0)) as client:
            resp = await client.get(url, headers=self._headers(), params={"limit": 20})
            if resp.status_code == 401:
                raise PermissionError("API Token 无效或已被撤销（HTTP 401）")
            resp.raise_for_status()
            data = resp.json()

        terms = data.get("terms") if isinstance(data, dict) else []
        if not isinstance(terms, list):
            terms = []
        schemas = sorted(
            {
                str(s)
                for t in terms
                if isinstance(t, dict)
                for s in (t.get("schema_names") or [])
                if s
            }
        )
        return {
            "ok": True,
            "terms_count": len(terms),
            "schemas": schemas,
            "sample_terms": [t.get("name") for t in terms[:6] if isinstance(t, dict) and t.get("name")],
        }

    async def _fetch_all_terms(self, limit: int = 100) -> list[dict[str, Any]]:
        """获取并按 base_url 缓存 10 分钟全部业务术语定义。"""
        now = time.monotonic()
        cached = self._terms_cache.get(self.base_url)
        if cached and (now - cached[0]) < self._TERMS_TTL_SECONDS:
            return cached[1]

        url = f"{self.base_url}/api/terms"
        async with httpx.AsyncClient(**self._client_kwargs(20.0)) as client:
            resp = await client.get(url, headers=self._headers(), params={"limit": max(100, limit)})
            resp.raise_for_status()
            data = resp.json()

        terms = data.get("terms") if isinstance(data, dict) else []
        if not isinstance(terms, list):
            return []
        self._terms_cache[self.base_url] = (now, terms)
        return terms

    async def search_terms_with_meta(
        self, keyword: str = "", limit: int = 20
    ) -> tuple[list[dict[str, Any]], bool]:
        """检索业务术语；若带关键词未命中，则自动回退返回全量实体摘要并返回 fallback_all=True。"""
        terms = await self._fetch_all_terms(limit=max(100, limit))
        kw = (keyword or "").strip().lower()
        if not kw:
            return terms[:limit], False

        matched: list[dict[str, Any]] = []
        for item in terms:
            if not isinstance(item, dict):
                continue
            haystack = " ".join(
                [
                    str(item.get("name") or ""),
                    str(item.get("description") or ""),
                    " ".join(str(x) for x in (item.get("synonyms") or [])),
                    " ".join(str(x) for x in (item.get("schema_names") or [])),
                ]
            ).lower()
            if kw in haystack:
                matched.append(item)
                if len(matched) >= limit:
                    break

        if not matched and terms:
            return terms[:limit], True
        return matched, False

    async def search_terms(self, keyword: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """检索业务术语定义、同义词与物理 Schema 映射（带 10 分钟 TTL 缓存与空搜全量兜底）。"""
        items, _ = await self.search_terms_with_meta(keyword=keyword, limit=limit)
        return items

    async def ask_question(
        self,
        question: str,
        conversation_id: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """流式调用 POST /api/chat/completions，提取完整回答、SQL 与结构化结果行（支持进度心跳回调）。"""
        url = f"{self.base_url}/api/chat/completions"
        payload: dict[str, Any] = {"question": question.strip()}
        if conversation_id:
            payload["conversation_id"] = conversation_id

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        final_answer: dict[str, Any] = {}
        streamed_sql: str = ""
        steps: list[str] = []
        received_result = False
        req_start = time.monotonic()
        last_notify_ts = req_start

        try:
            async with httpx.AsyncClient(**self._client_kwargs(float(self.timeout_seconds))) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code == 401:
                        raise PermissionError("API Token 无效或已被撤销（HTTP 401）")
                    if resp.status_code == 409:
                        raise RuntimeError("Auto Ontology 同一会话上一轮查询尚未结束（HTTP 409）")
                    resp.raise_for_status()

                    async for raw_line in resp.aiter_lines():
                        line = (raw_line or "").strip()
                        if not line:
                            continue
                        if not line.startswith("data:"):
                            # 处理服务端 : ping 保活行，确保上层 SSE 不断流
                            if on_progress and (time.monotonic() - last_notify_ts >= 15.0):
                                last_notify_ts = time.monotonic()
                                await on_progress("Auto Ontology 正在推理与执行查询...")
                            continue
                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            break
                        try:
                            ev = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        ev_type = ev.get("type")
                        if ev_type == "step":
                            node = ev.get("node") or ""
                            phase = ev.get("phase") or ""
                            label = ev.get("label") or node
                            if node:
                                steps.append(f"{node}:{phase}")
                            if on_progress and phase == "start" and label:
                                last_notify_ts = time.monotonic()
                                await on_progress(f"业务库查询中: {label}")
                        elif ev_type == "sql":
                            if isinstance(ev.get("sql"), str):
                                streamed_sql = ev["sql"]
                                if on_progress:
                                    last_notify_ts = time.monotonic()
                                    await on_progress("SQL 校验通过，正在执行数据库查询...")
                        elif ev_type == "result":
                            if isinstance(ev.get("answer"), dict):
                                final_answer = ev["answer"]
                                received_result = True
                                # 已收到最终结果主体后立即退出流循环关闭连接
                                if "response" in final_answer or "sql_response_from_db" in final_answer:
                                    break
                        elif ev_type == "error":
                            raise RuntimeError(str(ev.get("message") or "Auto Ontology 查询失败"))
        except httpx.StreamError as stream_err:
            raise RuntimeError(f"Auto Ontology SSE 流式连接中断: {stream_err}") from stream_err
        except httpx.TimeoutException as timeout_err:
            raise RuntimeError(f"Auto Ontology 查询超时（>{self.timeout_seconds}s）") from timeout_err

        if not received_result:
            raise RuntimeError("Auto Ontology 流式响应意外中断，未收到完整查询结果")

        response_text = str(final_answer.get("response") or "").strip()
        sql_code = str(final_answer.get("sql_code") or streamed_sql or "").strip()
        rows = parse_auto_ontology_rows(final_answer)
        thoughts = final_answer.get("thoughts")
        if not isinstance(thoughts, list):
            thoughts = steps

        elapsed_seconds = round(time.monotonic() - req_start, 2)
        logger.info(
            f"[AutoOntology] 查询完成: q='{question[:40]}', sql_len={len(sql_code)}, rows={len(rows)}, elapsed={elapsed_seconds}s"
        )
        return {
            "response": response_text,
            "sql_code": sql_code,
            "rows": rows,
            "thoughts": thoughts,
            "elapsed_seconds": elapsed_seconds,
        }
