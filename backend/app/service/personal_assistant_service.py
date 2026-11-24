"""
私人助理核心领域服务（PersonalAssistantService）。
职责：
1. 个人备忘与待办持久化管理（personal_notes + 同步长期记忆召回）；
2. 历史 IM 对话记录检索与会话统计（dialogue_wiki_records）；
3. 中文自然语言定时提醒解析、scheduled_tasks 落库与取消；
4. IM 主动推送、失败重试队列（im_outbox_retries 指数退避）及 Weixin Sidecar 心跳巡检。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx

from ..core.logging_config import logger
from ..repository.db import db_manager


class PersonalAssistantService:
    """私人助理业务编排服务，统一供 IM 快捷指令、Agent 工具与后台调度器调用。"""

    def __init__(self) -> None:
        self._last_heartbeat_check_ts: float = 0.0

    @staticmethod
    async def ensure_tables(conn: Any) -> None:
        """幂等确保备忘表与 IM 重试队列表存在。"""
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS personal_notes (
                id TEXT PRIMARY KEY,
                link_id TEXT NOT NULL DEFAULT '',
                sender_id TEXT NOT NULL DEFAULT 'default',
                category TEXT NOT NULL DEFAULT 'memo',
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                due_at TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_personal_notes_owner ON personal_notes(sender_id, status, created_at DESC)"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS im_outbox_retries (
                id TEXT PRIMARY KEY,
                channel_type TEXT NOT NULL,
                link_id TEXT NOT NULL,
                target_user_id TEXT NOT NULL,
                text_content TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 5,
                next_retry_at REAL NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_im_outbox_status_next ON im_outbox_retries(status, next_retry_at)"
        )
        await conn.commit()

    # ────────────────────────── 1. 备忘与待办管理 ──────────────────────────

    async def add_note(
        self,
        *,
        sender_id: str,
        content: str,
        category: str = "memo",
        link_id: str = "",
        due_at: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """写入一条个人备忘或待办，并尝试同步沉淀至长期记忆库。"""
        clean_content = (content or "").strip()
        if not clean_content:
            raise ValueError("备忘/待办内容不能为空")

        norm_cat = "todo" if str(category or "").strip().lower() == "todo" else "memo"
        owner_id = (sender_id or "default").strip() or "default"
        note_id = f"note_{uuid.uuid4().hex[:12]}"
        now_ts = time.time()
        tags_str = json.dumps(tags or [], ensure_ascii=False)

        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            await conn.execute(
                """
                INSERT INTO personal_notes (
                    id, link_id, sender_id, category, content, status, due_at, tags_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (note_id, link_id or "", owner_id, norm_cat, clean_content, due_at, tags_str, now_ts, now_ts),
            )
            await conn.commit()

        # 顺手同步至长期记忆表，以便跨端、跨会话语义召回
        try:
            from ..memory.long_term import LongTermMemoryManager

            date_label = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M")
            await LongTermMemoryManager.add_memory(
                category="personal_memo",
                content=f"[{date_label}][{norm_cat}] {clean_content}",
                confidence=0.95,
                source="im_personal_assistant",
                is_pinned=(norm_cat == "memo"),
            )
        except Exception as mem_err:
            logger.debug(f"[PersonalAssistant] 同步写入 long_term_memories 跳过: {mem_err}")

        return {
            "id": note_id,
            "sender_id": owner_id,
            "category": norm_cat,
            "content": clean_content,
            "status": "active",
            "due_at": due_at,
            "created_at": now_ts,
            "created_at_str": datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M"),
        }

    async def list_notes(
        self,
        *,
        sender_id: str,
        category: str | None = None,
        keyword: str = "",
        status: str = "active",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """查询当前用户的个人备忘与待办。"""
        owner_id = (sender_id or "default").strip() or "default"
        safe_limit = max(1, min(50, int(limit or 20)))
        sql = "SELECT * FROM personal_notes WHERE sender_id = ?"
        params: list[Any] = [owner_id]

        if status and status != "all":
            sql += " AND status = ?"
            params.append(status)
        if category in ("memo", "todo"):
            sql += " AND category = ?"
            params.append(category)
        if keyword.strip():
            sql += " AND content LIKE ?"
            params.append(f"%{keyword.strip()}%")

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(safe_limit)

        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            cur = await conn.execute(sql, tuple(params))
            rows = await cur.fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            ts = float(d.get("created_at") or time.time())
            d["created_at_str"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            items.append(d)
        return items

    async def update_note_status(
        self,
        *,
        sender_id: str,
        identifier: str,
        status: str = "done",
    ) -> dict[str, Any]:
        """按 ID 或内容关键字将备忘/待办标记为已完成(done)或删除(deleted)。"""
        owner_id = (sender_id or "default").strip() or "default"
        ident = (identifier or "").strip()
        if not ident:
            return {"updated": 0, "message": "请提供要操作的备忘 ID 或关键词。"}

        target_status = "deleted" if status == "deleted" else "done"
        now_ts = time.time()

        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            cur = await conn.execute(
                """
                SELECT id, content FROM personal_notes
                WHERE sender_id = ? AND status = 'active' AND (id = ? OR content LIKE ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (owner_id, ident, f"%{ident}%"),
            )
            row = await cur.fetchone()
            if not row:
                return {"updated": 0, "message": f"未找到匹配「{ident}」的活跃备忘或待办。"}

            note_id = str(row["id"])
            note_content = str(row["content"])
            await conn.execute(
                "UPDATE personal_notes SET status = ?, updated_at = ? WHERE id = ?",
                (target_status, now_ts, note_id),
            )
            await conn.commit()

        return {
            "updated": 1,
            "id": note_id,
            "content": note_content,
            "status": target_status,
            "message": f"已将「{note_content[:40]}」标记为{'已删除' if target_status == 'deleted' else '已完成'}。",
        }

    # ────────────────────────── 2. 历史对话检索与统计 ──────────────────────────

    async def search_im_chat_history(
        self,
        *,
        link_id: str,
        sender_id: str,
        keyword: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """从 dialogue_wiki_records 按关键词与日期检索真实历史对话（含精确时间）。"""
        safe_limit = max(1, min(30, int(limit or 12)))
        clauses: list[str] = []
        params: list[Any] = []

        if link_id:
            clauses.append("link_id = ?")
            params.append(link_id)
        if sender_id:
            clauses.append("sender_id = ?")
            params.append(sender_id)
        if keyword.strip():
            kw = f"%{keyword.strip()}%"
            clauses.append("(user_message LIKE ? OR assistant_reply LIKE ?)")
            params.extend([kw, kw])
        if date_from.strip():
            try:
                ts_from = datetime.strptime(date_from.strip()[:10], "%Y-%m-%d").timestamp()
                clauses.append("created_at >= ?")
                params.append(ts_from)
            except ValueError:
                pass
        if date_to.strip():
            try:
                ts_to = (datetime.strptime(date_to.strip()[:10], "%Y-%m-%d") + timedelta(days=1)).timestamp()
                clauses.append("created_at < ?")
                params.append(ts_to)
            except ValueError:
                pass

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT record_id, user_message, assistant_reply, created_at FROM dialogue_wiki_records {where_sql} ORDER BY created_at DESC LIMIT ?"
        params.append(safe_limit)

        async with db_manager.get_connection() as conn:
            try:
                cur = await conn.execute(sql, tuple(params))
                rows = await cur.fetchall()
            except Exception as e:
                logger.debug(f"[PersonalAssistant] 检索 dialogue_wiki_records 跳过: {e}")
                return []

        results: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            ts = float(d.get("created_at") or 0)
            results.append(
                {
                    "record_id": d.get("record_id", ""),
                    "time_str": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else "",
                    "user_message": str(d.get("user_message") or ""),
                    "assistant_reply": str(d.get("assistant_reply") or ""),
                }
            )
        return results

    async def get_session_stats(self, *, link_id: str, sender_id: str) -> dict[str, Any]:
        """统计该用户在此渠道的首条对话时间、最后对话时间与累计轮数。"""
        empty_stats = {
            "total_rounds": 0,
            "first_msg_time_str": "",
            "last_msg_time_str": "",
            "last_msg_ts": 0.0,
        }
        async with db_manager.get_connection() as conn:
            try:
                cur = await conn.execute(
                    """
                    SELECT COUNT(*) AS cnt, MIN(created_at) AS min_ts, MAX(created_at) AS max_ts
                    FROM dialogue_wiki_records
                    WHERE link_id = ? AND sender_id = ?
                    """,
                    (link_id, sender_id),
                )
                row = await cur.fetchone()
                if not row or not row["cnt"]:
                    return empty_stats
                min_ts = float(row["min_ts"] or 0)
                max_ts = float(row["max_ts"] or 0)
                return {
                    "total_rounds": int(row["cnt"] or 0),
                    "first_msg_time_str": datetime.fromtimestamp(min_ts).strftime("%Y-%m-%d %H:%M") if min_ts else "",
                    "last_msg_time_str": datetime.fromtimestamp(max_ts).strftime("%Y-%m-%d %H:%M") if max_ts else "",
                    "last_msg_ts": max_ts,
                }
            except Exception:
                return empty_stats

    # ────────────────────────── 3. 自然语言定时提醒 ──────────────────────────

    @staticmethod
    def parse_reminder_schedule(
        trigger_expr: str,
        now: datetime | None = None,
    ) -> tuple[str, bool, str]:
        """将中文自然时间或 Cron 表达式解析为 (cron_expr, one_shot, 人类可读描述)。"""
        from ..scheduler.task_scheduler import CronExpression

        raw = (trigger_expr or "").strip()
        if not raw:
            raise ValueError("提醒时间不能为空。")

        base_now = (now or datetime.now()).replace(second=0, microsecond=0)

        # 1. 标准 Cron 或 @daily/@hourly 等宏
        if raw.startswith("@") or len(raw.split()) == 5:
            CronExpression.parse(raw)
            return raw, False, f"周期任务 ({raw})"

        # 2. 相对时间：例如 "2分钟后" / "1小时后" / "3天后"
        m_rel = re.search(r"(\d+)\s*(分钟|分|小时|个小时|天)后?", raw)
        if m_rel and ("后" in raw or re.fullmatch(r"\d+\s*(分钟|分|小时|个小时|天)", raw)):
            val = max(1, int(m_rel.group(1)))
            unit = m_rel.group(2)
            if "分" in unit:
                target_dt = base_now + timedelta(minutes=val)
            elif "小时" in unit:
                target_dt = base_now + timedelta(hours=val)
            else:
                target_dt = base_now + timedelta(days=val)
            cron_expr = f"{target_dt.minute} {target_dt.hour} {target_dt.day} {target_dt.month} *"
            CronExpression.parse(cron_expr)
            return cron_expr, True, target_dt.strftime("%Y-%m-%d %H:%M")

        # 3. 周期中文：例如 "每天早上8:30" / "每日9点" / "每周一10点"
        m_daily = re.match(
            r"^(?:每天|每日)(早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})(?:[:：点时](\d{1,2})?)?分?$",
            raw,
        )
        if m_daily:
            period = m_daily.group(1) or ""
            hour = int(m_daily.group(2))
            minute = int(m_daily.group(3) or 0)
            if period in ("下午", "晚上") and hour < 12:
                hour += 12
            elif period == "中午" and hour < 11:
                hour += 12
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"时间点超出范围：{raw}")
            cron_expr = f"{minute} {hour} * * *"
            CronExpression.parse(cron_expr)
            return cron_expr, False, f"每天 {hour:02d}:{minute:02d}"

        # 4. 指定相对日期时间：例如 "明早8点" / "明天上午9:30" / "今晚10点" / "后天14:00"
        m_day = re.match(
            r"^(今天|今日|今晚|明天|明日|明早|后天)\s*(早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})(?:[:：点时](\d{1,2})?)?分?$",
            raw,
        )
        if m_day:
            day_word = m_day.group(1)
            period = m_day.group(2) or ""
            hour = int(m_day.group(3))
            minute = int(m_day.group(4) or 0)
            if day_word == "今晚" or period in ("下午", "晚上"):
                if hour < 12:
                    hour += 12
            elif period == "中午" and hour < 11:
                hour += 12
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"时间点超出范围：{raw}")

            day_offset = 0
            if day_word in ("明天", "明日", "明早"):
                day_offset = 1
            elif day_word == "后天":
                day_offset = 2
            target_dt = (base_now + timedelta(days=day_offset)).replace(hour=hour, minute=minute)
            if day_offset == 0 and target_dt <= base_now:
                target_dt = target_dt + timedelta(days=1)
            cron_expr = f"{target_dt.minute} {target_dt.hour} {target_dt.day} {target_dt.month} *"
            CronExpression.parse(cron_expr)
            return cron_expr, True, target_dt.strftime("%Y-%m-%d %H:%M")

        # 5. ISO 日期时间：YYYY-MM-DD HH:MM 或 HH:MM
        m_iso = re.match(r"^(\d{4}-\d{1,2}-\d{1,2})\s+(\d{1,2})[:：](\d{1,2})$", raw)
        if m_iso:
            target_dt = datetime.strptime(
                f"{m_iso.group(1)} {int(m_iso.group(2)):02d}:{int(m_iso.group(3)):02d}",
                "%Y-%m-%d %H:%M",
            )
            cron_expr = f"{target_dt.minute} {target_dt.hour} {target_dt.day} {target_dt.month} *"
            CronExpression.parse(cron_expr)
            return cron_expr, True, target_dt.strftime("%Y-%m-%d %H:%M")

        m_hm = re.match(r"^(\d{1,2})[:：](\d{1,2})$", raw)
        if m_hm:
            hour = int(m_hm.group(1))
            minute = int(m_hm.group(2))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"时间点超出范围：{raw}")
            target_dt = base_now.replace(hour=hour, minute=minute)
            if target_dt <= base_now:
                target_dt += timedelta(days=1)
            cron_expr = f"{target_dt.minute} {target_dt.hour} {target_dt.day} {target_dt.month} *"
            CronExpression.parse(cron_expr)
            return cron_expr, True, target_dt.strftime("%Y-%m-%d %H:%M")

        raise ValueError(
            f"暂不支持时间格式「{raw}」，可使用如：10分钟后、明早8点、今晚22:30、每天早上8:30 或标准 Cron。"
        )

    async def create_im_reminder(
        self,
        *,
        workspace_id: str,
        link_id: str,
        channel_type: str,
        target_user_id: str,
        content: str,
        trigger_expr: str,
        run_as_agent: bool = False,
    ) -> dict[str, Any]:
        """创建并持久化一条支持微信/IM 主动推送的定时提醒任务。"""
        from ..scheduler.task_scheduler import compute_next_run_iso

        clean_content = (content or "").strip()
        if not clean_content:
            raise ValueError("提醒内容不能为空。")

        cron_expr, one_shot, human_desc = self.parse_reminder_schedule(trigger_expr)
        task_id = f"rem_{uuid.uuid4().hex[:10]}"
        next_run_at = compute_next_run_iso(cron_expr)
        prompt_template = clean_content if run_as_agent else f"__IM_REMINDER__:{clean_content}"
        task_name = f"IM提醒: {clean_content[:24]}"

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO scheduled_tasks (
                    id, workspace_id, name, cron_expr, prompt_template, status, next_run_at,
                    notify_channel, notify_link_id, notify_target_user_id, one_shot
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    workspace_id or "default",
                    task_name,
                    cron_expr,
                    prompt_template,
                    next_run_at,
                    channel_type or "",
                    link_id or "",
                    target_user_id or "",
                    1 if one_shot else 0,
                ),
            )
            await conn.commit()

        return {
            "id": task_id,
            "name": task_name,
            "content": clean_content,
            "cron_expr": cron_expr,
            "one_shot": one_shot,
            "schedule_desc": human_desc,
            "next_run_at": next_run_at,
        }

    async def list_im_reminders(self, *, link_id: str, target_user_id: str) -> list[dict[str, Any]]:
        """列出当前用户活跃的 IM 定时提醒任务。"""
        clauses = ["status = 'active'"]
        params: list[Any] = []
        if link_id:
            clauses.append("notify_link_id = ?")
            params.append(link_id)
        if target_user_id:
            clauses.append("notify_target_user_id = ?")
            params.append(target_user_id)

        sql = f"SELECT id, name, cron_expr, prompt_template, next_run_at, one_shot FROM scheduled_tasks WHERE {' AND '.join(clauses)} ORDER BY next_run_at ASC LIMIT 20"
        async with db_manager.get_connection() as conn:
            cur = await conn.execute(sql, tuple(params))
            rows = await cur.fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            pt = str(d.get("prompt_template") or "")
            d["display_content"] = pt.split(":", 1)[1].strip() if pt.startswith("__IM_REMINDER__:") else pt
            out.append(d)
        return out

    async def cancel_im_reminder(
        self,
        *,
        link_id: str,
        target_user_id: str,
        identifier: str,
    ) -> dict[str, Any]:
        """按 ID 或内容关键词取消活跃的 IM 定时提醒。"""
        ident = (identifier or "").strip()
        if not ident:
            return {"cancelled": 0, "message": "请指定要取消的提醒 ID 或内容关键词。"}

        clauses = ["status = 'active'", "(id = ? OR name LIKE ? OR prompt_template LIKE ?)"]
        params: list[Any] = [ident, f"%{ident}%", f"%{ident}%"]
        if link_id:
            clauses.append("notify_link_id = ?")
            params.append(link_id)
        if target_user_id:
            clauses.append("notify_target_user_id = ?")
            params.append(target_user_id)

        async with db_manager.get_connection() as conn:
            cur = await conn.execute(
                f"SELECT id, name FROM scheduled_tasks WHERE {' AND '.join(clauses)} LIMIT 1",
                tuple(params),
            )
            row = await cur.fetchone()
            if not row:
                return {"cancelled": 0, "message": f"未找到匹配「{ident}」的活跃提醒。"}
            tid = str(row["id"])
            tname = str(row["name"])
            await conn.execute(
                "UPDATE scheduled_tasks SET status = 'paused', next_run_at = NULL WHERE id = ?",
                (tid,),
            )
            await conn.commit()
        return {"cancelled": 1, "id": tid, "name": tname, "message": f"已取消提醒「{tname}」。"}

    # ────────────────────────── 4. IM 主动推送、重试与心跳检测 ──────────────────────────

    async def _send_fallback_alert(self, title: str, message: str) -> None:
        """Sidecar 长期掉线或推送失败时触发可选备用 Webhook 告警（未配置时仅记日志）。"""
        webhook_url = (os.getenv("IM_FALLBACK_ALERT_WEBHOOK") or "").strip()
        logger.warning(f"[IMFallbackAlert] {title}: {message}")
        if not webhook_url:
            return
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                await client.post(
                    webhook_url,
                    json={"title": title, "message": message, "timestamp": datetime.now().isoformat()},
                )
        except Exception as err:
            logger.debug(f"[IMFallbackAlert] 备用 Webhook 通知异常: {err}")

    async def push_im_proactive_message(
        self,
        *,
        channel_type: str,
        link_id: str,
        target_user_id: str,
        text: str,
        enqueue_on_fail: bool = True,
    ) -> dict[str, Any]:
        """向指定 IM 用户主动推送消息；发送失败时写入重试队列。"""
        from ..channels import adapters as _adapters  # noqa: F401
        from ..channels.base import InboundMessage, OutboundMessage
        from ..channels.formatter import format_for_im_channel
        from ..channels.registry import channel_registry

        adapter = channel_registry.get(channel_type)
        if not adapter:
            err_msg = f"未找到渠道适配器: {channel_type}"
            if enqueue_on_fail:
                await self._enqueue_retry(channel_type, link_id, target_user_id, text, err_msg)
            return {"pushed": False, "error": err_msg}

        link_cfg: dict[str, Any] = {}
        async with db_manager.get_connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM dialogue_channel_links WHERE link_id = ? LIMIT 1",
                (link_id,),
            )
            row = await cur.fetchone()
            if row:
                link_cfg = dict(row)
                try:
                    link_cfg["channel_config"] = json.loads(link_cfg.get("channel_config_json") or "{}")
                except Exception:
                    link_cfg["channel_config"] = {}

        endpoint_token = str(link_cfg.get("endpoint_token") or link_id or "")
        fmt_text, chunks = format_for_im_channel(
            text, max_chunk_chars=getattr(adapter, "max_chunk_chars", 1400)
        )
        synthetic_in = InboundMessage(
            channel_type=channel_type,
            endpoint_token=endpoint_token,
            external_msg_id=f"proactive_{uuid.uuid4().hex[:10]}",
            sender_id=target_user_id,
            sender_name=target_user_id,
            chat_id=target_user_id,
            text="",
            reply_context={"to_wxid": target_user_id, "chat_id": target_user_id},
        )
        outbound = OutboundMessage(
            raw_markdown=text,
            formatted_text=fmt_text,
            chunks=chunks or [fmt_text],
            metadata={"proactive": True},
        )
        res = await adapter.send_reply(inbound=synthetic_in, outbound=outbound, link_config=link_cfg)
        if res.get("pushed"):
            return res

        err_msg = str(res.get("error") or f"推送失败 (status={res.get('status_code')})")
        if enqueue_on_fail:
            await self._enqueue_retry(channel_type, link_id, target_user_id, text, err_msg)
            await self._send_fallback_alert("IM 主动推送失败已入重试队列", f"target={target_user_id}, err={err_msg}")
        return {"pushed": False, "error": err_msg, "queued_for_retry": enqueue_on_fail}

    async def _enqueue_retry(
        self,
        channel_type: str,
        link_id: str,
        target_user_id: str,
        text_content: str,
        last_error: str,
    ) -> None:
        now_ts = time.time()
        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            await conn.execute(
                """
                INSERT INTO im_outbox_retries (
                    id, channel_type, link_id, target_user_id, text_content,
                    retry_count, max_retries, next_retry_at, last_error, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, 5, ?, ?, 'pending', ?, ?)
                """,
                (
                    f"out_{uuid.uuid4().hex[:12]}",
                    channel_type,
                    link_id,
                    target_user_id,
                    text_content,
                    now_ts + 30.0,
                    last_error[:300],
                    now_ts,
                    now_ts,
                ),
            )
            await conn.commit()

    async def process_im_outbox_retries(self) -> int:
        """扫描并重试到期的失败主动推送消息（指数退避，最多 5 次）。"""
        now_ts = time.time()
        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            cur = await conn.execute(
                """
                SELECT * FROM im_outbox_retries
                WHERE status = 'pending' AND next_retry_at <= ?
                ORDER BY next_retry_at ASC LIMIT 10
                """,
                (now_ts,),
            )
            pending_rows = [dict(r) for r in await cur.fetchall()]

        if not pending_rows:
            return 0

        backoff_steps = [60.0, 180.0, 600.0, 1800.0, 3600.0]
        sent_count = 0
        for item in pending_rows:
            res = await self.push_im_proactive_message(
                channel_type=str(item["channel_type"]),
                link_id=str(item["link_id"]),
                target_user_id=str(item["target_user_id"]),
                text=str(item["text_content"]),
                enqueue_on_fail=False,
            )
            upd_ts = time.time()
            async with db_manager.get_connection() as conn:
                if res.get("pushed"):
                    await conn.execute(
                        "UPDATE im_outbox_retries SET status = 'sent', updated_at = ? WHERE id = ?",
                        (upd_ts, item["id"]),
                    )
                    sent_count += 1
                else:
                    next_cnt = int(item.get("retry_count") or 0) + 1
                    max_r = int(item.get("max_retries") or 5)
                    if next_cnt >= max_r:
                        await conn.execute(
                            "UPDATE im_outbox_retries SET status = 'failed', retry_count = ?, last_error = ?, updated_at = ? WHERE id = ?",
                            (next_cnt, str(res.get("error") or "")[:300], upd_ts, item["id"]),
                        )
                    else:
                        wait_sec = backoff_steps[min(next_cnt - 1, len(backoff_steps) - 1)]
                        await conn.execute(
                            "UPDATE im_outbox_retries SET retry_count = ?, next_retry_at = ?, last_error = ?, updated_at = ? WHERE id = ?",
                            (next_cnt, upd_ts + wait_sec, str(res.get("error") or "")[:300], upd_ts, item["id"]),
                        )
                await conn.commit()
        return sent_count

    async def check_weixin_sidecar_heartbeat(self, min_interval_seconds: float = 300.0) -> dict[str, Any]:
        """定期检查活跃的微信 Sidecar 在线状态；掉线时自动尝试拉起并按需触发备用告警。"""
        now_ts = time.time()
        if now_ts - self._last_heartbeat_check_ts < min_interval_seconds:
            return {"skipped": True}
        self._last_heartbeat_check_ts = now_ts

        async with db_manager.get_connection() as conn:
            try:
                cur = await conn.execute(
                    "SELECT link_id, channel_config_json FROM dialogue_channel_links WHERE channel_type = 'weixin_clawbot' AND status = 'active'"
                )
                links = [dict(r) for r in await cur.fetchall()]
            except Exception:
                links = []

        if not links:
            return {"checked": 0, "online": True}

        from ..channels.adapters.weixin_clawbot import DEFAULT_CLAWBOT_BRIDGE_URL
        from ..channels.qr_binding import ensure_clawbot_sidecar_running

        checked_urls: set[str] = set()
        offline_urls: list[str] = []
        for lk in links:
            try:
                cfg = json.loads(lk.get("channel_config_json") or "{}")
            except Exception:
                cfg = {}
            bridge_url = str(cfg.get("bridge_url") or DEFAULT_CLAWBOT_BRIDGE_URL).rstrip("/")
            if bridge_url in checked_urls:
                continue
            checked_urls.add(bridge_url)
            ok, msg = await ensure_clawbot_sidecar_running(bridge_url=bridge_url)
            if not ok:
                offline_urls.append(f"{bridge_url}: {msg}")

        if offline_urls:
            await self._send_fallback_alert("微信 Sidecar 心跳离线告警", "; ".join(offline_urls))
            return {"checked": len(checked_urls), "online": False, "errors": offline_urls}

        return {"checked": len(checked_urls), "online": True}


personal_assistant_service = PersonalAssistantService()
