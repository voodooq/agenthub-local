"""
增量会话摘要引擎 (SessionSummaryManager - L3 记忆压缩层)
核心特性：
1. 按滑动窗口水位线 (watermark_turn_id) 仅对滑出 L2 窗口的增量 Turn 进行合并摘要，避免全量历史重复编码
2. 4 个确定性结构化槽位：core_goal / confirmed_params / produced_artifacts / open_issues + 精简 summary_markdown
3. 双轨压缩策略：优先调度轻量模型 (task_role="light")，若未配置账号/超时/输出解析失败则平滑降级为确定性启发式摘要，永不阻塞主流程
4. 全量经过 MemoryPolicyGuard 隐私脱敏，并同步镜像写入 conversations.rolling_summary 保持向后兼容
"""
import asyncio
from datetime import datetime, timezone
import json
import re
from typing import Any

from ..core.logging_config import logger
from ..repository.conversation_repository import ConversationRepository
from ..repository.db import db_manager
from ..repository.message_repository import MessageRepository
from ..service.attachment_service import attachment_service
from .policy_guard import MemoryPolicyGuard


class SessionSummaryManager:
    """L3 增量会话摘要管理器"""

    DEFAULT_WINDOW_TURNS = 10
    MAX_SUMMARY_CHARS = 1200

    _ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"\[已真实物理落盘文件\s*`([^`]+)`"),
        re.compile(r"成功物理落盘文件:\s*`([^`]+)`"),
        re.compile(r"保存工作区专属技能:\s*([^\s\n]+)"),
    )
    _ERROR_LINE_RE = re.compile(r"(?:Error|ERROR|Exception|Traceback|失败|DENY)[^\n]{0,120}")

    @staticmethod
    def _safe_json_loads(raw: str | None, fallback: Any) -> Any:
        if not raw:
            return fallback
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, type(fallback)) else fallback
        except Exception:
            return fallback

    @classmethod
    def _build_markdown(
        cls,
        core_goal: str,
        confirmed_params: dict[str, Any],
        produced_artifacts: list[str],
        open_issues: list[str],
        watermark_turn_id: int,
    ) -> str:
        """将 4 个结构化槽位渲染为高密度紧凑 Markdown 摘要（<= 500 Token）"""
        lines: list[str] = [f"[已压缩至第 {watermark_turn_id} 轮历史摘要]"]
        if core_goal:
            lines.append(f"- **核心目标**: {core_goal.strip()}")
        if confirmed_params:
            params_preview = "; ".join(f"{k}={v}" for k, v in list(confirmed_params.items())[:10])
            lines.append(f"- **已确认参数**: {params_preview}")
        if produced_artifacts:
            artifacts_preview = ", ".join(f"`{p}`" for p in produced_artifacts[-8:])
            lines.append(f"- **已产出资产**: {artifacts_preview}")
        if open_issues:
            issues_preview = " | ".join(open_issues[-4:])
            lines.append(f"- **待关注事项**: {issues_preview}")
        return "\n".join(lines)[: cls.MAX_SUMMARY_CHARS]

    @classmethod
    async def get_summary(cls, conversation_id: str) -> dict[str, Any] | None:
        """
        读取指定会话的 L3 增量摘要；若 session_summaries 尚无记录则兼容回退 conversations.rolling_summary。
        """
        clean_conv = (conversation_id or "").strip()
        if not clean_conv:
            return None

        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM session_summaries WHERE conversation_id = ? LIMIT 1",
                (clean_conv,),
            ) as cur:
                row = await cur.fetchone()

        if row:
            d = dict(row)
            return {
                "conversation_id": d["conversation_id"],
                "workspace_id": d.get("workspace_id") or "default",
                "watermark_turn_id": int(d.get("watermark_turn_id") or 0),
                "core_goal": d.get("core_goal") or "",
                "confirmed_params": cls._safe_json_loads(d.get("confirmed_params_json"), {}),
                "produced_artifacts": cls._safe_json_loads(d.get("produced_artifacts_json"), []),
                "open_issues": cls._safe_json_loads(d.get("open_issues_json"), []),
                "summary_markdown": d.get("summary_markdown") or "",
                "updated_at": str(d.get("updated_at") or ""),
            }

        conv = await ConversationRepository.get_by_id(clean_conv)
        if conv and (conv.rolling_summary or "").strip():
            return {
                "conversation_id": clean_conv,
                "workspace_id": getattr(conv, "workspace_id", "default") or "default",
                "watermark_turn_id": 0,
                "core_goal": "",
                "confirmed_params": {},
                "produced_artifacts": [],
                "open_issues": [],
                "summary_markdown": conv.rolling_summary.strip(),
                "updated_at": str(conv.updated_at or ""),
            }
        return None

    @classmethod
    def _heuristic_merge(
        cls,
        *,
        existing_goal: str,
        existing_params: dict[str, Any],
        existing_artifacts: list[str],
        existing_issues: list[str],
        delta_rows: list[dict[str, Any]],
        watermark_turn_id: int,
    ) -> dict[str, Any]:
        """
        无轻量模型或模型不可用时的确定性启发式规则摘要兜底，保证水位线始终可靠推进。
        """
        core_goal = existing_goal.strip()
        confirmed_params = dict(existing_params)
        artifacts = list(existing_artifacts)
        issues = list(existing_issues)

        for row in delta_rows:
            role = row.get("role", "")
            raw_text = (
                row.get("compressed_content") or row.get("content") or ""
                if role == "tool"
                else (row.get("content") or "")
            )
            if role == "user":
                raw_text = attachment_service.strip_attachments_for_display(raw_text)
            clean_text, _ = MemoryPolicyGuard.sanitize_text(raw_text)

            if role == "user" and not core_goal and clean_text.strip():
                first_line = clean_text.strip().splitlines()[0]
                core_goal = first_line[:120]

            # 从已确认对齐文本或模板中提取显式 KV
            for m in re.finditer(r"\{\{([A-Za-z0-9_\u4e00-\u9fff-]+):([^}]+)\}\}", clean_text):
                k = m.group(1).strip().lower().replace("-", "_")
                v = m.group(2).strip()[:80]
                if k and v:
                    confirmed_params[k] = v

            # 提取落盘路径
            for pat in cls._ARTIFACT_PATTERNS:
                for m in pat.finditer(clean_text):
                    p = m.group(1).strip()
                    if p and p not in artifacts:
                        artifacts.append(p)

            # 提取异常线索；若出现明确成功标志则衰减旧未决问题
            if "成功" in clean_text or "passed" in clean_text.lower() or "exit_code=0" in clean_text:
                if len(issues) > 2:
                    issues = issues[-2:]
            else:
                err_m = cls._ERROR_LINE_RE.search(clean_text)
                if err_m:
                    err_snippet = err_m.group(0).strip()[:120]
                    if err_snippet not in issues:
                        issues.append(err_snippet)

        artifacts = artifacts[-12:]
        issues = issues[-5:]
        summary_md = cls._build_markdown(
            core_goal=core_goal,
            confirmed_params=confirmed_params,
            produced_artifacts=artifacts,
            open_issues=issues,
            watermark_turn_id=watermark_turn_id,
        )
        return {
            "core_goal": core_goal,
            "confirmed_params": confirmed_params,
            "produced_artifacts": artifacts,
            "open_issues": issues,
            "summary_markdown": summary_md,
        }

    @classmethod
    async def _llm_merge(
        cls,
        *,
        conversation_id: str,
        existing_summary: dict[str, Any] | None,
        delta_rows: list[dict[str, Any]],
        watermark_turn_id: int,
    ) -> dict[str, Any] | None:
        """
        调用轻量模型执行增量 JSON 合并摘要；任何失败、无可用账号或格式异常均安全返回 None 由上层回退启发式。
        """
        from ..service.engine_pool import engine_pool

        prev_state = {
            "core_goal": (existing_summary or {}).get("core_goal", ""),
            "confirmed_params": (existing_summary or {}).get("confirmed_params", {}),
            "produced_artifacts": (existing_summary or {}).get("produced_artifacts", []),
            "open_issues": (existing_summary or {}).get("open_issues", []),
        }

        delta_lines: list[str] = []
        for row in delta_rows:
            t_id = row.get("turn_id") or 1
            role = row.get("role", "user")
            text = (
                row.get("compressed_content") or row.get("content") or ""
                if role == "tool"
                else (row.get("content") or "")
            )
            if role == "user":
                text = attachment_service.strip_attachments_for_display(text)
            safe_txt, _ = MemoryPolicyGuard.sanitize_text(text)
            delta_lines.append(f"[Turn {t_id} | {role}] {safe_txt[:500]}")

        prompt = (
            "请把已有会话摘要与新增溢出轮次合并更新为严格 JSON 对象（不要输出任何 Markdown 围栏以外的多余解释），"
            "仅包含以下 4 个字段且总信息控制在 400 字以内：\n"
            '{"core_goal": "一句话核心目标", "confirmed_params": {"参数键": "参数值"}, '
            '"produced_artifacts": ["产物路径"], "open_issues": ["未决问题"]}\n\n'
            f"已有摘要:\n{json.dumps(prev_state, ensure_ascii=False)}\n\n"
            f"新增待合并对话:\n" + "\n".join(delta_lines)
        )

        async def _consume_light_stream() -> str:
            collected = ""
            async for evt in engine_pool.execute_stream_with_failover(
                conversation_id=conversation_id,
                user_prompt=prompt,
                model_name="gemini-2.5-flash",
                thinking_level="low",
                system_override="你是高密度会话记忆压缩器，只返回严格合法的 JSON。",
                temperature=0.2,
                pre_built_history=[],
                task_role="light",
            ):
                etype = evt.get("type")
                if etype == "chunk":
                    collected += evt.get("content", "")
                elif etype == "done":
                    collected = evt.get("full_content", collected)
                    break
                elif etype == "error":
                    return ""
            return collected

        try:
            raw_reply = await asyncio.wait_for(_consume_light_stream(), timeout=15.0)
        except Exception as e:
            logger.debug(f"[SessionSummary] 轻量模型压缩跳过，回退确定性规则摘要: {e}")
            return None

        if not raw_reply or not raw_reply.strip():
            return None

        json_match = re.search(r"\{[\s\S]*\}", raw_reply)
        if not json_match:
            return None

        try:
            parsed = json.loads(json_match.group(0))
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None

        core_goal = str(parsed.get("core_goal") or prev_state["core_goal"])[:160]
        confirmed_params = (
            parsed.get("confirmed_params")
            if isinstance(parsed.get("confirmed_params"), dict)
            else prev_state["confirmed_params"]
        )
        produced_artifacts = (
            [str(x) for x in parsed.get("produced_artifacts", []) if str(x).strip()]
            if isinstance(parsed.get("produced_artifacts"), list)
            else prev_state["produced_artifacts"]
        )[:12]
        open_issues = (
            [str(x) for x in parsed.get("open_issues", []) if str(x).strip()]
            if isinstance(parsed.get("open_issues"), list)
            else prev_state["open_issues"]
        )[:5]

        summary_md = cls._build_markdown(
            core_goal=core_goal,
            confirmed_params=confirmed_params,
            produced_artifacts=produced_artifacts,
            open_issues=open_issues,
            watermark_turn_id=watermark_turn_id,
        )
        return {
            "core_goal": core_goal,
            "confirmed_params": confirmed_params,
            "produced_artifacts": produced_artifacts,
            "open_issues": open_issues,
            "summary_markdown": summary_md,
        }

    @classmethod
    async def maybe_update_summary(
        cls,
        conversation_id: str,
        *,
        workspace_id: str | None = None,
        current_turn_id: int | None = None,
        window_turns: int = DEFAULT_WINDOW_TURNS,
        min_delta_turns: int = 2,
        use_llm: bool = True,
        incognito: bool | None = None,
    ) -> dict[str, Any] | None:
        """
        检查会话是否超出 L2 滑动窗口水位线；若超出则执行增量压缩并推进 watermark_turn_id。
        无痕模式或 oneshot 模式下直接跳过。
        """
        clean_conv = (conversation_id or "").strip()
        if not clean_conv:
            return None

        conv = await ConversationRepository.get_by_id(clean_conv)
        if not conv:
            return None

        is_incognito = bool(conv.incognito) if incognito is None else bool(incognito)
        if is_incognito or conv.scene_mode == "oneshot":
            return None

        effective_turn = (
            int(current_turn_id)
            if current_turn_id is not None
            else await MessageRepository.get_max_turn_id(clean_conv)
        )
        safe_window = max(1, int(window_turns))
        overflow_end_turn = effective_turn - safe_window
        if overflow_end_turn <= 0:
            return None

        existing = await cls.get_summary(clean_conv)
        old_watermark = int(existing["watermark_turn_id"]) if existing else 0
        if overflow_end_turn - old_watermark < max(1, int(min_delta_turns)):
            return None

        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT id, turn_id, role, content, compressed_content, created_at
                FROM messages
                WHERE conversation_id = ?
                  AND COALESCE(turn_id, 1) > ?
                  AND COALESCE(turn_id, 1) <= ?
                ORDER BY COALESCE(turn_id, 1) ASC, id ASC
                """,
                (clean_conv, old_watermark, overflow_end_turn),
            ) as cur:
                delta_rows = [dict(r) for r in await cur.fetchall()]

        if not delta_rows:
            return None

        merged: dict[str, Any] | None = None
        if use_llm:
            merged = await cls._llm_merge(
                conversation_id=clean_conv,
                existing_summary=existing,
                delta_rows=delta_rows,
                watermark_turn_id=overflow_end_turn,
            )

        if merged is None:
            merged = cls._heuristic_merge(
                existing_goal=(existing or {}).get("core_goal", ""),
                existing_params=(existing or {}).get("confirmed_params", {}),
                existing_artifacts=(existing or {}).get("produced_artifacts", []),
                existing_issues=(existing or {}).get("open_issues", []),
                delta_rows=delta_rows,
                watermark_turn_id=overflow_end_turn,
            )

        # 全量入库前强制隐私脱敏
        sanitized_merged, _ = MemoryPolicyGuard.sanitize_payload(merged)
        core_goal = str(sanitized_merged.get("core_goal") or "")[:160]
        confirmed_params = sanitized_merged.get("confirmed_params") or {}
        produced_artifacts = sanitized_merged.get("produced_artifacts") or []
        open_issues = sanitized_merged.get("open_issues") or []
        summary_markdown = str(sanitized_merged.get("summary_markdown") or "")[: cls.MAX_SUMMARY_CHARS]

        resolved_ws = (workspace_id or getattr(conv, "workspace_id", None) or "default").strip() or "default"
        now_ts = datetime.now(timezone.utc).isoformat()

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO session_summaries (
                    conversation_id, workspace_id, watermark_turn_id,
                    core_goal, confirmed_params_json, produced_artifacts_json,
                    open_issues_json, summary_markdown, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    watermark_turn_id = excluded.watermark_turn_id,
                    core_goal = excluded.core_goal,
                    confirmed_params_json = excluded.confirmed_params_json,
                    produced_artifacts_json = excluded.produced_artifacts_json,
                    open_issues_json = excluded.open_issues_json,
                    summary_markdown = excluded.summary_markdown,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_conv,
                    resolved_ws,
                    overflow_end_turn,
                    core_goal,
                    json.dumps(confirmed_params, ensure_ascii=False),
                    json.dumps(produced_artifacts, ensure_ascii=False),
                    json.dumps(open_issues, ensure_ascii=False),
                    summary_markdown,
                    now_ts,
                ),
            )
            await conn.execute(
                "UPDATE conversations SET rolling_summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (summary_markdown, clean_conv),
            )
            await conn.commit()

        logger.info(
            f"[SessionSummary] 会话 {clean_conv} 增量摘要水位线已推进: {old_watermark} -> {overflow_end_turn}"
        )
        return {
            "conversation_id": clean_conv,
            "workspace_id": resolved_ws,
            "watermark_turn_id": overflow_end_turn,
            "core_goal": core_goal,
            "confirmed_params": confirmed_params,
            "produced_artifacts": produced_artifacts,
            "open_issues": open_issues,
            "summary_markdown": summary_markdown,
            "updated_at": now_ts,
        }

    @classmethod
    async def collapse_turn_range(
        cls,
        conversation_id: str,
        *,
        old_anchor_turn_id: int,
        new_anchor_turn_id: int,
        existing_summary_markdown: str = "",
        workspace_id: str = "default",
        incognito: bool = False,
        use_llm: bool = True,
    ) -> dict[str, Any] | None:
        """按精确轮次区间 (old_anchor_turn_id, new_anchor_turn_id] 执行分块坍缩合并。

        NOTE: 供 LocalContextCompressor 在触发 Chunk Collapse 时调用；
        当 incognito=True 时仅在内存返回合并摘要，严禁写入 SQLite 持久化表。
        """
        clean_conv = (conversation_id or "").strip()
        if not clean_conv or new_anchor_turn_id <= old_anchor_turn_id:
            return None

        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT id, turn_id, role, content, compressed_content, created_at
                FROM messages
                WHERE conversation_id = ?
                  AND COALESCE(turn_id, 1) > ?
                  AND COALESCE(turn_id, 1) <= ?
                ORDER BY COALESCE(turn_id, 1) ASC, id ASC
                """,
                (clean_conv, int(old_anchor_turn_id), int(new_anchor_turn_id)),
            ) as cur:
                delta_rows = [dict(r) for r in await cur.fetchall()]

        if not delta_rows:
            return None

        existing = None if incognito else await cls.get_summary(clean_conv)
        if not existing and existing_summary_markdown.strip():
            existing = {
                "core_goal": "",
                "confirmed_params": {},
                "produced_artifacts": [],
                "open_issues": [],
                "summary_markdown": existing_summary_markdown.strip(),
            }

        merged: dict[str, Any] | None = None
        used_llm = False
        if use_llm:
            merged = await cls._llm_merge(
                conversation_id=clean_conv,
                existing_summary=existing,
                delta_rows=delta_rows,
                watermark_turn_id=int(new_anchor_turn_id),
            )
            if merged is not None:
                used_llm = True

        if merged is None:
            merged = cls._heuristic_merge(
                existing_goal=(existing or {}).get("core_goal", ""),
                existing_params=(existing or {}).get("confirmed_params", {}),
                existing_artifacts=(existing or {}).get("produced_artifacts", []),
                existing_issues=(existing or {}).get("open_issues", []),
                delta_rows=delta_rows,
                watermark_turn_id=int(new_anchor_turn_id),
            )

        sanitized_merged, _ = MemoryPolicyGuard.sanitize_payload(merged)
        core_goal = str(sanitized_merged.get("core_goal") or "")[:160]
        confirmed_params = sanitized_merged.get("confirmed_params") or {}
        produced_artifacts = sanitized_merged.get("produced_artifacts") or []
        open_issues = sanitized_merged.get("open_issues") or []
        summary_markdown = str(sanitized_merged.get("summary_markdown") or "")[: cls.MAX_SUMMARY_CHARS]

        now_ts = datetime.now(timezone.utc).isoformat()
        if not incognito:
            resolved_ws = (workspace_id or "default").strip() or "default"
            async with db_manager.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO session_summaries (
                        conversation_id, workspace_id, watermark_turn_id,
                        core_goal, confirmed_params_json, produced_artifacts_json,
                        open_issues_json, summary_markdown, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                        workspace_id = excluded.workspace_id,
                        watermark_turn_id = excluded.watermark_turn_id,
                        core_goal = excluded.core_goal,
                        confirmed_params_json = excluded.confirmed_params_json,
                        produced_artifacts_json = excluded.produced_artifacts_json,
                        open_issues_json = excluded.open_issues_json,
                        summary_markdown = excluded.summary_markdown,
                        updated_at = excluded.updated_at
                    """,
                    (
                        clean_conv,
                        resolved_ws,
                        int(new_anchor_turn_id),
                        core_goal,
                        json.dumps(confirmed_params, ensure_ascii=False),
                        json.dumps(produced_artifacts, ensure_ascii=False),
                        json.dumps(open_issues, ensure_ascii=False),
                        summary_markdown,
                        now_ts,
                    ),
                )
                await conn.execute(
                    "UPDATE conversations SET rolling_summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (summary_markdown, clean_conv),
                )
                await conn.commit()

        return {
            "watermark_turn_id": int(new_anchor_turn_id),
            "core_goal": core_goal,
            "confirmed_params": confirmed_params,
            "produced_artifacts": produced_artifacts,
            "open_issues": open_issues,
            "summary_markdown": summary_markdown,
            "delta_rows": delta_rows,
            "used_llm": used_llm,
            "updated_at": now_ts,
        }


session_summary_manager = SessionSummaryManager()

