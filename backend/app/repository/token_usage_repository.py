"""
Token 与 KV Cache / 耗时指标仓储层 (Token Usage Repository)
职责：
1. 全角色（primary / scrubber / summarizer / memory）单步推理用量明细落盘；
2. 高效聚合单轮或整会话的 `TurnTokenMetrics` 映射（消除前端消息列表恢复时的 N+1 查询）；
3. 为 `/stats` 会话趋势看板提供汇总 KPI、角色分账、通道分布及逐轮诊断数据。
"""
from datetime import datetime, timezone
import sqlite3
from typing import Any
from .db import db_manager


def _build_turn_summary_from_rows(
    turn_id: int,
    rows: list[dict[str, Any]],
    session_collapsed_saved_tokens: int,
) -> dict[str, Any]:
    """将同一轮次的多步明细流水按统一口径聚合成微水表对象。

    口径约定：
    - `context_tokens` / `context_limit`: 取主链路最后一步（汇总回答步）的 `prompt_tokens + completion_tokens`；
    - `cached_tokens` / `cache_hit_pct`: 取主链路各步合计 `cached_tokens / prompt_tokens`（单步时即当步命中率）；
    - `turn_saved_tokens`: 取本轮旁路清洗等增量节省（不重复叠加全会话坍缩值）；
    - `saved_tokens`: `turn_saved_tokens + session_collapsed_saved_tokens`。
    """
    primary_rows = [r for r in rows if (r.get("agent_role") or "primary") == "primary"]
    metric_rows = primary_rows or rows

    if not metric_rows:
        return {
            "turn_id": turn_id,
            "context_tokens": 0,
            "context_limit": 0,
            "context_usage_pct": 0.0,
            "cached_tokens": 0,
            "cache_hit_pct": 0.0,
            "eval_tokens": 0,
            "completion_tokens": 0,
            "saved_tokens": session_collapsed_saved_tokens,
            "turn_saved_tokens": 0,
            "session_collapsed_saved_tokens": session_collapsed_saved_tokens,
            "ttft_ms": 0.0,
            "prefill_ms": 0.0,
            "tps": 0.0,
            "provider_type": "",
            "provider_name": "",
            "model_name": "",
            "react_steps": 0,
            "is_estimated": True,
        }

    # 按 step_index、id 升序排列确保最后一步一定是汇总交付步
    ordered_metric = sorted(metric_rows, key=lambda r: (int(r.get("step_index") or 0), int(r.get("id") or 0)))
    last_step = ordered_metric[-1]

    last_prompt = int(last_step.get("prompt_tokens") or 0)
    last_completion = int(last_step.get("completion_tokens") or 0)
    context_tokens = last_prompt + last_completion
    context_limit = int(last_step.get("context_limit") or 0)
    if context_limit <= 0:
        for row in reversed(ordered_metric):
            if int(row.get("context_limit") or 0) > 0:
                context_limit = int(row["context_limit"])
                break

    total_prompt = sum(int(r.get("prompt_tokens") or 0) for r in ordered_metric)
    total_cached = sum(int(r.get("cached_tokens") or 0) for r in ordered_metric)
    total_eval = sum(int(r.get("eval_tokens") or 0) for r in ordered_metric)
    total_completion = sum(int(r.get("completion_tokens") or 0) for r in ordered_metric)

    # 若末步包含全部上下文的 cached_tokens，直接对多步求平均命中率，且展示末步上下文与缓存快照（多步时加总更具总消耗对比意义，这里按方案口径：单步直接展示该步值；多步使用 sum(cached)/sum(prompt) 计算 cache_hit_pct，cached_tokens 取 sum(cached)）
    cache_hit_pct = round((total_cached / total_prompt) * 100.0, 1) if total_prompt > 0 else 0.0
    context_usage_pct = round((context_tokens / context_limit) * 100.0, 1) if context_limit > 0 else 0.0

    # 本轮节省仅累加本轮增量（如旁路脱水），防止把 session_collapsed_saved_tokens 重复叠加
    turn_saved = sum(int(r.get("saved_tokens") or 0) for r in rows)

    # TTFT / prefill_ms / TPS 取末步有效值（若末步被中断为0，回溯寻找非零实测值）
    ttft_ms = float(last_step.get("ttft_ms") or 0.0)
    if ttft_ms <= 0:
        for r in reversed(ordered_metric):
            if float(r.get("ttft_ms") or 0.0) > 0:
                ttft_ms = float(r["ttft_ms"])
                break

    prefill_ms = sum(float(r.get("prefill_ms") or 0.0) for r in ordered_metric)
    tps = float(last_step.get("tps") or 0.0)
    if tps <= 0:
        for r in reversed(ordered_metric):
            if float(r.get("tps") or 0.0) > 0:
                tps = float(r["tps"])
                break

    # 仅当本轮主推理全为估算值时标记 is_estimated=True
    all_estimated = all(bool(r.get("is_estimated", 0)) for r in ordered_metric)

    return {
        "turn_id": int(turn_id),
        "context_tokens": context_tokens,
        "context_limit": context_limit,
        "context_usage_pct": min(100.0, max(0.0, context_usage_pct)) if context_limit > 0 else 0.0,
        "cached_tokens": int(last_step.get("cached_tokens") or 0),
        "cache_hit_pct": min(100.0, max(0.0, cache_hit_pct)),
        "eval_tokens": total_eval,
        "completion_tokens": total_completion,
        "saved_tokens": turn_saved + max(0, int(session_collapsed_saved_tokens or 0)),
        "turn_saved_tokens": turn_saved,
        "session_collapsed_saved_tokens": max(0, int(session_collapsed_saved_tokens or 0)),
        "ttft_ms": round(ttft_ms, 1),
        "prefill_ms": round(prefill_ms, 1),
        "tps": round(tps, 1),
        "provider_type": str(last_step.get("provider_type") or ""),
        "provider_name": str(last_step.get("provider_name") or ""),
        "model_name": str(last_step.get("model_name") or ""),
        "react_steps": len(ordered_metric),
        "is_estimated": all_estimated,
    }


class TokenUsageRepository:
    """Token 指标与缓存监控数据访问对象"""

    @staticmethod
    async def record_usage(
        conversation_id: str,
        turn_id: int,
        step_index: int = 0,
        agent_role: str = "primary",
        provider_id: str = "",
        provider_name: str = "",
        provider_type: str = "",
        model_name: str = "",
        prompt_tokens: int = 0,
        cached_tokens: int = 0,
        eval_tokens: int = 0,
        completion_tokens: int = 0,
        saved_tokens: int = 0,
        context_limit: int = 0,
        ttft_ms: float = 0.0,
        prefill_ms: float = 0.0,
        tps: float = 0.0,
        is_estimated: bool = False,
    ) -> int:
        """持久化单次推理/清洗/摘要的细粒度消耗日志"""
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO token_usage_logs (
                    conversation_id, turn_id, step_index, agent_role,
                    provider_id, provider_name, provider_type, model_name,
                    prompt_tokens, cached_tokens, eval_tokens, completion_tokens,
                    saved_tokens, context_limit, ttft_ms, prefill_ms, tps, is_estimated
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    int(turn_id),
                    int(step_index),
                    agent_role,
                    provider_id or "",
                    provider_name or "",
                    provider_type or "",
                    model_name or "",
                    max(0, int(prompt_tokens)),
                    max(0, int(cached_tokens)),
                    max(0, int(eval_tokens)),
                    max(0, int(completion_tokens)),
                    max(0, int(saved_tokens)),
                    max(0, int(context_limit)),
                    float(max(0.0, ttft_ms)),
                    float(max(0.0, prefill_ms)),
                    float(max(0.0, tps)),
                    1 if is_estimated else 0,
                ),
            )
            await conn.commit()
            return cursor.lastrowid or 0

    @staticmethod
    async def get_turn_summary(conversation_id: str, turn_id: int) -> dict[str, Any] | None:
        """获取指定轮次聚合后的水表字典，无流水时返回 None"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT COALESCE(collapsed_tokens_saved, 0) AS collapsed_tokens_saved
                FROM conversations WHERE id = ?
                """,
                (conversation_id,),
            ) as c_cur:
                c_row = await c_cur.fetchone()
                collapsed_saved = int(c_row["collapsed_tokens_saved"]) if c_row else 0

            async with conn.execute(
                """
                SELECT * FROM token_usage_logs
                WHERE conversation_id = ? AND turn_id = ?
                ORDER BY step_index ASC, id ASC
                """,
                (conversation_id, int(turn_id)),
            ) as cursor:
                rows = [dict(r) for r in await cursor.fetchall()]

        if not rows:
            return None
        return _build_turn_summary_from_rows(int(turn_id), rows, collapsed_saved)

    @staticmethod
    async def get_turn_summaries_map(conversation_id: str) -> dict[int, dict[str, Any]]:
        """一次性批量返回指定会话全部轮次的水表映射 {turn_id: TurnTokenMetrics}，避免 N+1 查询"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT COALESCE(collapsed_tokens_saved, 0) AS collapsed_tokens_saved
                FROM conversations WHERE id = ?
                """,
                (conversation_id,),
            ) as c_cur:
                c_row = await c_cur.fetchone()
                collapsed_saved = int(c_row["collapsed_tokens_saved"]) if c_row else 0

            async with conn.execute(
                """
                SELECT * FROM token_usage_logs
                WHERE conversation_id = ?
                ORDER BY turn_id ASC, step_index ASC, id ASC
                """,
                (conversation_id,),
            ) as cursor:
                rows = [dict(r) for r in await cursor.fetchall()]

        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            tid = int(row.get("turn_id") or 1)
            grouped.setdefault(tid, []).append(row)

        return {
            tid: _build_turn_summary_from_rows(tid, t_rows, collapsed_saved)
            for tid, t_rows in grouped.items()
        }

    @staticmethod
    async def get_conversation_detailed_stats(conversation_id: str) -> dict[str, Any]:
        """构建会话全维度统计与趋势报告（供 /stats 弹窗展示）"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT id, title, collapse_anchor_turn_id, COALESCE(collapsed_tokens_saved, 0) AS collapsed_tokens_saved
                FROM conversations WHERE id = ?
                """,
                (conversation_id,),
            ) as c_cur:
                conv_row = await c_cur.fetchone()

            async with conn.execute(
                """
                SELECT * FROM token_usage_logs
                WHERE conversation_id = ?
                ORDER BY turn_id ASC, step_index ASC, id ASC
                """,
                (conversation_id,),
            ) as cursor:
                all_rows = [dict(r) for r in await cursor.fetchall()]

        collapsed_saved = int(conv_row["collapsed_tokens_saved"]) if conv_row else 0
        collapse_anchor = int(conv_row["collapse_anchor_turn_id"] or 0) if conv_row else 0

        primary_rows = [r for r in all_rows if (r.get("agent_role") or "primary") == "primary"]
        total_prompt = sum(int(r.get("prompt_tokens") or 0) for r in primary_rows)
        total_cached = sum(int(r.get("cached_tokens") or 0) for r in primary_rows)
        total_eval = sum(int(r.get("eval_tokens") or 0) for r in primary_rows)
        total_completion = sum(int(r.get("completion_tokens") or 0) for r in primary_rows)
        total_scrub_saved = sum(int(r.get("saved_tokens") or 0) for r in all_rows)
        total_saved = total_scrub_saved + collapsed_saved

        avg_cache_hit_pct = round((total_cached / total_prompt) * 100.0, 1) if total_prompt > 0 else 0.0

        valid_ttfts = [float(r["ttft_ms"]) for r in primary_rows if float(r.get("ttft_ms") or 0.0) > 0]
        avg_ttft_ms = round(sum(valid_ttfts) / len(valid_ttfts), 1) if valid_ttfts else 0.0

        valid_tps = [float(r["tps"]) for r in primary_rows if float(r.get("tps") or 0.0) > 0]
        avg_tps = round(sum(valid_tps) / len(valid_tps), 1) if valid_tps else 0.0

        # 按角色分账统计
        role_map: dict[str, dict[str, Any]] = {}
        for row in all_rows:
            role = str(row.get("agent_role") or "primary")
            bucket = role_map.setdefault(
                role,
                {
                    "agent_role": role,
                    "call_count": 0,
                    "prompt_tokens": 0,
                    "cached_tokens": 0,
                    "eval_tokens": 0,
                    "completion_tokens": 0,
                    "saved_tokens": 0,
                },
            )
            bucket["call_count"] += 1
            bucket["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
            bucket["cached_tokens"] += int(row.get("cached_tokens") or 0)
            bucket["eval_tokens"] += int(row.get("eval_tokens") or 0)
            bucket["completion_tokens"] += int(row.get("completion_tokens") or 0)
            bucket["saved_tokens"] += int(row.get("saved_tokens") or 0)

        # 按算力节点与模型分布统计
        provider_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in all_rows:
            ptype = str(row.get("provider_type") or "unknown")
            pname = str(row.get("provider_name") or ptype)
            mname = str(row.get("model_name") or "unknown")
            key = (ptype, pname, mname)
            bucket = provider_map.setdefault(
                key,
                {
                    "provider_type": ptype,
                    "provider_name": pname,
                    "model_name": mname,
                    "call_count": 0,
                    "prompt_tokens": 0,
                    "cached_tokens": 0,
                    "eval_tokens": 0,
                    "completion_tokens": 0,
                },
            )
            bucket["call_count"] += 1
            bucket["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
            bucket["cached_tokens"] += int(row.get("cached_tokens") or 0)
            bucket["eval_tokens"] += int(row.get("eval_tokens") or 0)
            bucket["completion_tokens"] += int(row.get("completion_tokens") or 0)

        # 逐轮趋势聚合
        grouped_turns: dict[int, list[dict[str, Any]]] = {}
        for row in all_rows:
            tid = int(row.get("turn_id") or 1)
            grouped_turns.setdefault(tid, []).append(row)

        turns = [
            _build_turn_summary_from_rows(tid, t_rows, collapsed_saved)
            for tid, t_rows in sorted(grouped_turns.items(), key=lambda kv: kv[0])
        ]

        # 生成可读性调优诊断建议
        hints: list[str] = []
        local_multi_turns = [
            t for t in turns
            if t["turn_id"] >= 2
            and t["provider_type"] in {"llama_server", "ollama"}
            and t["cached_tokens"] == 0
        ]
        if local_multi_turns:
            hints.append(
                f"检测到 {len(local_multi_turns)} 个本地多轮回合 KV Cache 命中为 0，建议检查 llama-server 启动参数是否包含 `--cache-reuse 256` 及并发槽位 `-c 16384 -np 2`。"
            )
        if collapse_anchor > 0:
            hints.append(
                f"当前会话已在 Turn {collapse_anchor} 完成分块坍缩，累计从热上下文移除 {collapsed_saved:,} tokens 并固化至不动前缀。"
            )
        if total_scrub_saved > 0:
            hints.append(
                f"本地旁路工具清洗 (Context Scrubber) 已在本会话累计脱水省下 {total_scrub_saved:,} tokens。"
            )

        return {
            "conversation_id": conversation_id,
            "summary": {
                "total_turns": len(turns),
                "total_calls": len(all_rows),
                "total_prompt_tokens": total_prompt,
                "total_cached_tokens": total_cached,
                "total_eval_tokens": total_eval,
                "total_completion_tokens": total_completion,
                "avg_cache_hit_pct": avg_cache_hit_pct,
                "total_saved_tokens": total_saved,
                "scrub_saved_tokens": total_scrub_saved,
                "collapsed_saved_tokens": collapsed_saved,
                "collapse_anchor_turn_id": collapse_anchor,
                "avg_ttft_ms": avg_ttft_ms,
                "avg_tps": avg_tps,
                "hints": hints,
            },
            "by_role": list(role_map.values()),
            "by_provider": list(provider_map.values()),
            "turns": turns,
        }

    @staticmethod
    async def get_pool_usage_summary(
        known_account_names: set[str] | None = None,
    ) -> dict[str, Any]:
        """跨全量会话汇总多源算力池真实 LLM 调用次数、Token 消耗及缓存复用（兼容 accounts 去重）

        Args:
            known_account_names: 当前 `accounts` 表中已存在的传统账号名称集合，用于防止与 `accounts` 统计重复记账。

        Returns:
            dict[str, Any]: 包含 `extra_*`（仅存在多协议通道的增量统计）、`logs_*`（全库日志总览）以及分通道映射。
        """
        empty_summary: dict[str, Any] = {
            "extra_today_calls": 0,
            "extra_today_tokens": 0,
            "extra_today_cached_tokens": 0,
            "extra_total_calls": 0,
            "extra_total_tokens": 0,
            "extra_total_cached_tokens": 0,
            "logs_today_calls": 0,
            "logs_today_tokens": 0,
            "logs_today_cached_tokens": 0,
            "logs_total_calls": 0,
            "logs_total_tokens": 0,
            "logs_total_cached_tokens": 0,
            "by_provider_id": {},
            "by_provider_name": {},
        }

        # NOTE: SQLite CURRENT_TIMESTAMP 存储为 UTC 字符串；需将本地零点换算成 UTC 再做自然日下界比对
        local_midnight = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        today_start_utc = local_midnight.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        known_names = {str(n) for n in (known_account_names or set()) if str(n)}

        try:
            async with db_manager.get_connection() as conn:
                async with conn.execute(
                    """
                    SELECT
                        COALESCE(provider_id, '') AS provider_id,
                        COALESCE(provider_name, '') AS provider_name,
                        COALESCE(provider_type, '') AS provider_type,
                        COUNT(*) AS total_calls,
                        SUM(CASE WHEN replace(created_at, 'T', ' ') >= ? THEN 1 ELSE 0 END) AS today_calls,
                        SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS total_tokens,
                        SUM(
                            CASE
                                WHEN replace(created_at, 'T', ' ') >= ?
                                THEN COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)
                                ELSE 0
                            END
                        ) AS today_tokens,
                        SUM(COALESCE(cached_tokens, 0)) AS total_cached_tokens,
                        SUM(
                            CASE
                                WHEN replace(created_at, 'T', ' ') >= ?
                                THEN COALESCE(cached_tokens, 0)
                                ELSE 0
                            END
                        ) AS today_cached_tokens,
                        SUM(COALESCE(eval_tokens, 0)) AS total_eval_tokens,
                        SUM(COALESCE(completion_tokens, 0)) AS total_completion_tokens
                    FROM token_usage_logs
                    WHERE COALESCE(provider_type, '') != 'local_rule'
                    GROUP BY COALESCE(provider_id, ''), COALESCE(provider_name, ''), COALESCE(provider_type, '')
                    """,
                    (today_start_utc, today_start_utc, today_start_utc),
                ) as cursor:
                    rows = [dict(r) for r in await cursor.fetchall()]
        except (sqlite3.Error, OSError):
            return empty_summary

        by_provider_id: dict[str, dict[str, int]] = {}
        by_provider_name: dict[str, dict[str, int]] = {}

        def _merge_bucket(target: dict[str, dict[str, int]], key: str, vals: dict[str, int]) -> None:
            bucket = target.setdefault(
                key,
                {
                    "today_calls": 0,
                    "today_tokens": 0,
                    "today_cached_tokens": 0,
                    "total_calls": 0,
                    "total_tokens": 0,
                    "total_cached_tokens": 0,
                    "total_eval_tokens": 0,
                    "total_completion_tokens": 0,
                },
            )
            for metric_key, metric_val in vals.items():
                bucket[metric_key] += metric_val

        for row in rows:
            pid = str(row.get("provider_id") or "")
            pname = str(row.get("provider_name") or "")
            metrics = {
                "today_calls": int(row.get("today_calls") or 0),
                "today_tokens": int(row.get("today_tokens") or 0),
                "today_cached_tokens": int(row.get("today_cached_tokens") or 0),
                "total_calls": int(row.get("total_calls") or 0),
                "total_tokens": int(row.get("total_tokens") or 0),
                "total_cached_tokens": int(row.get("total_cached_tokens") or 0),
                "total_eval_tokens": int(row.get("total_eval_tokens") or 0),
                "total_completion_tokens": int(row.get("total_completion_tokens") or 0),
            }

            empty_summary["logs_today_calls"] += metrics["today_calls"]
            empty_summary["logs_today_tokens"] += metrics["today_tokens"]
            empty_summary["logs_today_cached_tokens"] += metrics["today_cached_tokens"]
            empty_summary["logs_total_calls"] += metrics["total_calls"]
            empty_summary["logs_total_tokens"] += metrics["total_tokens"]
            empty_summary["logs_total_cached_tokens"] += metrics["total_cached_tokens"]

            # 通道 1 / 通道 2 会同步调用 AccountRepository.record_usage 更新 accounts 表，需避免重复计账
            is_account_channel = (
                pid.startswith("account_")
                or pid.startswith("cli_")
                or (bool(pname) and pname in known_names)
            )
            if not is_account_channel:
                empty_summary["extra_today_calls"] += metrics["today_calls"]
                empty_summary["extra_today_tokens"] += metrics["today_tokens"]
                empty_summary["extra_today_cached_tokens"] += metrics["today_cached_tokens"]
                empty_summary["extra_total_calls"] += metrics["total_calls"]
                empty_summary["extra_total_tokens"] += metrics["total_tokens"]
                empty_summary["extra_total_cached_tokens"] += metrics["total_cached_tokens"]

            if pid:
                _merge_bucket(by_provider_id, pid, metrics)
            elif pname:
                _merge_bucket(by_provider_name, pname, metrics)

        empty_summary["by_provider_id"] = by_provider_id
        empty_summary["by_provider_name"] = by_provider_name
        return empty_summary

    @staticmethod
    def map_providers_usage(
        custom_providers: list[dict[str, Any]],
        pool_usage: dict[str, Any],
    ) -> dict[str, dict[str, int]]:
        """将全局聚合结果映射为以通道 ID 为键的用量明细字典（按 ID 优先，缺失 ID 的旧日志按名称兜底）"""
        by_pid = pool_usage.get("by_provider_id") or {}
        by_pname = pool_usage.get("by_provider_name") or {}
        mapped: dict[str, dict[str, int]] = {}
        for prov in custom_providers:
            pid = str(prov.get("id") or "")
            pname = str(prov.get("name") or "")
            if not pid:
                continue
            if pid in by_pid:
                mapped[pid] = dict(by_pid[pid])
            elif pname and pname in by_pname:
                mapped[pid] = dict(by_pname[pname])
        return mapped



