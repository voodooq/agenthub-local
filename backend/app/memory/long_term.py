"""
零外部依赖纯本地长期记忆管理器 (LongTermMemoryManager)
核心特性：
1. 毫秒级 FTS5 BM25 检索 + 置信度加权召回 Top 5（无需数 GB 的外部向量模型）
2. 提示词级语义冲突合并（ADD / UPDATE / NOOP）
3. 30天未命中记忆指数自然衰减与自动淘汰
4. 完整的可视化 CRUD 管理接口（供前端 Memory Hub 面板调用）
"""
import asyncio
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from ..core.logging_config import logger
from ..model.models import LongTermMemoryModel
from ..repository.db import db_manager


class LongTermMemoryManager:
    """长期记忆画像引擎"""

    @staticmethod
    async def list_all() -> list[dict[str, Any]]:
        """获取所有长期记忆列表（置顶优先，其次按置信度与最后访问时间降序）"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM long_term_memories ORDER BY COALESCE(is_pinned, 0) DESC, confidence DESC, hit_count DESC, last_accessed_at DESC"
            ) as cur:
                rows = list(await cur.fetchall())
                return [dict(r) for r in rows]

    @staticmethod
    async def add_memory(
        category: str,
        content: str,
        confidence: float = 0.85,
        mem_id: str | None = None,
        source: str = "auto_extract",
        is_pinned: int | bool = 0,
        import_batch_id: str | None = None,
    ) -> dict[str, Any]:
        """新增一条长期记忆"""
        new_id = mem_id or f"mem_{uuid.uuid4().hex[:10]}"
        valid_cat = category if category in ("env_info", "preference", "rule") else "preference"
        clean_content = content.strip()
        pinned_int = 1 if is_pinned else 0
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO long_term_memories
                (id, category, content, confidence, source, is_pinned, import_batch_id, hit_count, last_accessed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (new_id, valid_cat, clean_content, round(float(confidence), 2), source, pinned_int, import_batch_id),
            )
            await conn.commit()
        return {
            "id": new_id,
            "category": valid_cat,
            "content": clean_content,
            "confidence": confidence,
            "source": source,
            "is_pinned": pinned_int,
            "import_batch_id": import_batch_id,
            "hit_count": 0,
        }

    @staticmethod
    async def update_memory(
        mem_id: str,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
        is_pinned: bool | int | None = None,
    ) -> bool:
        """修改现有长期记忆内容、置信度或置顶状态"""
        updates = ["last_accessed_at = CURRENT_TIMESTAMP"]
        params: list[Any] = []
        if content is not None:
            updates.append("content = ?")
            params.append(content.strip())
        if category is not None:
            updates.append("category = ?")
            params.append(category)
        if confidence is not None:
            updates.append("confidence = ?")
            params.append(round(float(confidence), 2))
        if is_pinned is not None:
            updates.append("is_pinned = ?")
            params.append(1 if is_pinned else 0)

        params.append(mem_id)
        async with db_manager.get_connection() as conn:
            cur = await conn.execute(
                f"UPDATE long_term_memories SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await conn.commit()
            return (cur.rowcount or 0) > 0

    @staticmethod
    async def delete_memory(mem_id: str) -> bool:
        """删除单条长期记忆"""
        async with db_manager.get_connection() as conn:
            cur = await conn.execute("DELETE FROM long_term_memories WHERE id = ?", (mem_id,))
            await conn.commit()
            return (cur.rowcount or 0) > 0

    @staticmethod
    async def clear_all() -> int:
        """一键清空全部长期记忆"""
        async with db_manager.get_connection() as conn:
            cur = await conn.execute("DELETE FROM long_term_memories")
            await conn.commit()
            return cur.rowcount or 0

    @classmethod
    async def retrieve_relevant(
        cls,
        user_query: str,
        limit: int = 5,
        min_confidence: float = 0.5,
    ) -> list[dict[str, Any]]:
        """
        基于 SQLite FTS5 trigram BM25 + 置信度加权检索最相关的 Top K 长期记忆。
        同时自动将命中记忆的 hit_count + 1 并刷新 last_accessed_at。
        """
        async with db_manager.get_connection() as conn:
            matched_ids: list[str] = []

            # 1. 提取长度 >= 3 的关键词片段用于 trigram FTS5 匹配
            tokens = [t for t in re.split(r"[\s,.;:!?，。；：！？、/\\]+", (user_query or "").strip()) if len(t) >= 3]
            if tokens:
                fts_expr = " OR ".join([f'"{t.replace(chr(34), "")}"' for t in tokens[:6]])
                try:
                    async with conn.execute(
                        """
                        SELECT memory_id, bm25(long_term_memories_fts) as score
                        FROM long_term_memories_fts
                        WHERE long_term_memories_fts MATCH ?
                        ORDER BY score ASC
                        LIMIT ?
                        """,
                        (fts_expr, limit * 2),
                    ) as cur:
                        for r in await cur.fetchall():
                            matched_ids.append(r["memory_id"])
                except Exception as e:
                    logger.debug(f"FTS5 长期记忆查询回退常驻高权重模式: {e}")

            results: list[dict[str, Any]] = []
            seen_ids: set[str] = set()

            # 2. 先按 FTS5 命中的 ID 查询满足置信度的记录
            if matched_ids:
                placeholders = ",".join(["?"] * len(matched_ids))
                sql_matched = f"""
                SELECT * FROM long_term_memories
                WHERE id IN ({placeholders}) AND confidence >= ?
                ORDER BY confidence DESC, hit_count DESC
                """
                async with conn.execute(sql_matched, (*matched_ids, min_confidence)) as cur:
                    for row in await cur.fetchall():
                        d = dict(row)
                        results.append(d)
                        seen_ids.add(d["id"])
                        if len(results) >= limit:
                            break

            # 3. 若未满 limit，补充全局置顶或高置信度的核心规则与环境记忆
            if len(results) < limit:
                async with conn.execute(
                    """
                    SELECT * FROM long_term_memories
                    WHERE confidence >= ? OR COALESCE(is_pinned, 0) = 1
                    ORDER BY COALESCE(is_pinned, 0) DESC, (confidence * (1.0 + 0.05 * hit_count)) DESC, last_accessed_at DESC
                    LIMIT ?
                    """,
                    (min_confidence, limit * 2),
                ) as cur:
                    for row in await cur.fetchall():
                        d = dict(row)
                        if d["id"] not in seen_ids:
                            results.append(d)
                            seen_ids.add(d["id"])
                            if len(results) >= limit:
                                break

            # 4. 异步更新被召回记忆的命中次数与访问时间
            if seen_ids:
                id_Placeholders = ",".join(["?"] * len(seen_ids))
                await conn.execute(
                    f"""
                    UPDATE long_term_memories
                    SET hit_count = hit_count + 1, last_accessed_at = CURRENT_TIMESTAMP
                    WHERE id IN ({id_Placeholders})
                    """,
                    tuple(seen_ids),
                )
                await conn.commit()
                for r in results:
                    if r["id"] in seen_ids:
                        r["hit_count"] = int(r.get("hit_count", 0)) + 1

            return results

    @classmethod
    async def apply_time_decay(cls, inactive_days: int = 30, decay_lambda: float = 0.03, min_threshold: float = 0.3) -> dict[str, int]:
        """
        对超过 30 天未命中的长期记忆执行自然指数衰减：C_new = C_old * exp(-lambda * delta_days)
        低于阈值 0.3 的记忆自动清理淘汰。
        """
        decayed_count = 0
        deleted_count = 0
        now = datetime.now(timezone.utc)

        async with db_manager.get_connection() as conn:
            # 豁免 is_pinned = 1 的核心置顶规则
            async with conn.execute(
                "SELECT id, confidence, last_accessed_at FROM long_term_memories WHERE COALESCE(is_pinned, 0) = 0"
            ) as cur:
                rows = list(await cur.fetchall())

            for r in rows:
                mem_id = r["id"]
                conf = float(r["confidence"] or 0.8)
                last_str = r["last_accessed_at"]
                if not last_str:
                    continue

                try:
                    last_dt = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    delta_days = (now - last_dt).total_seconds() / 86400.0
                except Exception:
                    continue

                if delta_days > inactive_days:
                    over_days = delta_days - inactive_days
                    new_conf = round(conf * math.exp(-decay_lambda * over_days), 3)
                    if new_conf < min_threshold:
                        await conn.execute("DELETE FROM long_term_memories WHERE id = ?", (mem_id,))
                        deleted_count += 1
                    else:
                        await conn.execute(
                            "UPDATE long_term_memories SET confidence = ? WHERE id = ?",
                            (new_conf, mem_id),
                        )
                        decayed_count += 1

            if decayed_count or deleted_count:
                await conn.commit()

        return {"decayed": decayed_count, "deleted": deleted_count}

    @classmethod
    async def apply_merge_operations(cls, operations: list[dict[str, Any]]) -> int:
        """
        执行由 LLM 输出的记忆合并指令集（ADD / UPDATE / DELETE / NOOP）
        """
        applied = 0
        for op in operations:
            if not isinstance(op, dict):
                continue
            action = str(op.get("action", "")).upper()
            if action == "ADD":
                content = str(op.get("content", "")).strip()
                cat = str(op.get("category", "preference")).strip()
                conf = float(op.get("confidence", 0.85))
                if content:
                    await cls.add_memory(category=cat, content=content, confidence=conf)
                    applied += 1
            elif action == "UPDATE":
                target_id = str(op.get("target_id", "")).strip()
                new_content = str(op.get("new_content") or op.get("content") or "").strip()
                conf = float(op.get("confidence", 0.9))
                if target_id and new_content:
                    ok = await cls.update_memory(mem_id=target_id, content=new_content, confidence=conf)
                    if ok:
                        applied += 1
            elif action == "DELETE":
                target_id = str(op.get("target_id", "")).strip()
                if target_id:
                    ok = await cls.delete_memory(target_id)
                    if ok:
                        applied += 1
        return applied


    _debounce_tasks: dict[str, asyncio.Task] = {}

    @classmethod
    def cancel_extraction(cls, conv_id: str) -> bool:
        """取消指定会话尚未执行或挂起中的 180s 长期记忆防抖定时任务，防止与新一轮高优先级推理争抢算力"""
        if not conv_id:
            return False
        task = cls._debounce_tasks.pop(conv_id, None)
        if task and not task.done():
            task.cancel()
            return True
        return False

    @classmethod
    def schedule_extraction(cls, conv_id: str, delay_seconds: int = 180) -> None:
        """
        挂载 180 秒闲置防抖提炼定时任务：
        若用户在 180 秒内持续对话，自动重置计时器；闲置 3 分钟后静默启动后台轻量记忆提纯。
        """
        if not conv_id:
            return

        if conv_id in cls._debounce_tasks and not cls._debounce_tasks[conv_id].done():
            cls._debounce_tasks[conv_id].cancel()

        async def _debounce_worker():
            current = asyncio.current_task()
            try:
                await asyncio.sleep(delay_seconds)
                from ..repository.message_repository import MessageRepository
                from ..repository.conversation_repository import ConversationRepository
                current_max_turn = await MessageRepository.get_max_turn_id(conv_id)
                last_turn = await ConversationRepository.get_last_extracted_turn_id(conv_id)
                if current_max_turn <= last_turn:
                    # 自上次提炼以来没有任何新对话轮次，零 Token 消耗直接退出
                    return

                logger.info(f"会话 {conv_id} 已闲置 {delay_seconds}s，开始后台异步提炼长期记忆画像...")
                await cls.extract_from_conversation(conv_id)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"会话 {conv_id} 异步记忆提炼异常: {e}")
            finally:
                # 仅当注册表仍指向当前任务时再移除，避免新挂载的任务被误清除
                if cls._debounce_tasks.get(conv_id) is current:
                    cls._debounce_tasks.pop(conv_id, None)

        task = asyncio.create_task(_debounce_worker())
        cls._debounce_tasks[conv_id] = task

    @classmethod
    async def extract_from_conversation(cls, conv_id: str) -> int:
        """
        基于幂等水位线 (last_extracted_turn_id) 与 task_role="light" 双轨调度，
        增量提炼当前会话的新事实与偏好，并执行 FTS5 BM25 冲突合并。
        """
        from ..repository.conversation_repository import ConversationRepository
        from ..service.engine_pool import engine_pool

        last_turn = await ConversationRepository.get_last_extracted_turn_id(conv_id)
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT turn_id, role, content FROM messages
                WHERE conversation_id = ? AND turn_id > ? AND role IN ('user', 'model', 'assistant')
                ORDER BY turn_id ASC, id ASC
                """,
                (conv_id, last_turn),
            ) as cur:
                rows = list(await cur.fetchall())

        if not rows:
            return 0

        max_turn = max(int(r["turn_id"] or 0) for r in rows)
        dialog_lines: list[str] = []
        for r in rows:
            role_label = "用户" if r["role"] == "user" else "AI助手"
            c = str(r["content"] or "").strip()
            # 过滤超长工具伪文本与冗余
            if len(c) > 600:
                c = c[:300] + "...[省略]..." + c[-100:]
            if c:
                dialog_lines.append(f"{role_label}: {c}")

        dialog_text = "\n".join(dialog_lines)
        if len(dialog_text) < 40:
            # 有效信息量太少，直接推进水位线无需消耗 LLM
            await ConversationRepository.update_last_extracted_turn_id(conv_id, max_turn)
            return 0

        # FTS5 检索最相关的已有记忆作为上下文
        existing_mems = await cls.retrieve_relevant(dialog_text, limit=8, min_confidence=0.4)
        existing_fmt = json.dumps([
            {"id": m["id"], "category": m["category"], "content": m["content"], "confidence": m["confidence"]}
            for m in existing_mems
        ], ensure_ascii=False)

        prompt = f"""你是一个个人认知画像提炼引擎。请从以下新增对话中，提炼出【长期有效】的用户偏好、环境路径、硬性指令或技术栈习惯，并结合已存记忆做合并决策。

### 新增增量对话片段：
{dialog_text}

### 现有最相近的存量长期记忆：
{existing_fmt}

### 输出要求：
1. 仅提炼长期不变的事实（如用户的开发环境习惯、喜欢的技术栈、操作系统版本、硬性格式要求），忽略临时性代码或琐碎讨论。
2. 对比存量记忆：
   - 若为全新偏好或环境事实，生成 action="ADD"（分类 category: env_info / preference / rule）；
   - 若与存量记忆有冲突或更新，生成 action="UPDATE" 并带上 target_id 与 updated_content；
   - 若已有相同或更详细记忆，无需重复添加。
3. 必须输出且仅输出标准 JSON 数组，严禁包含任何 Markdown 标记或多余解释：
[
  {{"action": "ADD", "category": "preference", "content": "用户倾向使用 TypeScript 编写前端组件", "confidence": 0.9}},
  {{"action": "UPDATE", "target_id": "mem_xxx", "content": "用户当前工作区已迁移至 E:\\work\\project", "confidence": 0.95}}
]
若无可提炼的长期事实，请直接输出 []。"""

        async def _consume_extract() -> tuple[str, bool]:
            collected = ""
            async for evt in engine_pool.execute_stream_with_failover(
                conversation_id=conv_id,
                user_prompt=prompt,
                model_name="",
                thinking_level="low",
                system_override="你是一个专门提取用户画像的极简信息抽取引擎，必须仅输出 JSON 数组格式。",
                temperature=0.2,
                pre_built_history=[],
                task_role="light",
            ):
                etype = evt.get("type")
                if etype == "chunk":
                    collected += evt.get("content", "")
                elif etype == "done":
                    collected = evt.get("full_content", collected)
                    return collected, True
                elif etype == "error":
                    logger.info(f"会话 {conv_id} 长期记忆提炼跳过: {evt.get('content', '')}")
                    return "", False
            return collected, bool(collected.strip())

        try:
            full_response, stream_ok = await asyncio.wait_for(_consume_extract(), timeout=25.0)
            if not stream_ok:
                # 通道不可用或中途异常时推进水位线，防止通道冷却期间每分钟无限空转重试
                await ConversationRepository.update_last_extracted_turn_id(conv_id, max_turn)
                return 0

            # 提取 JSON 数组
            json_match = re.search(r"\[\s*\{.*\}\s*\]", full_response, re.DOTALL)
            if json_match:
                ops = json.loads(json_match.group(0))
                applied = await cls.apply_merge_operations(ops)
                logger.info(f"会话 {conv_id} 长期记忆提炼完成：共应用 {applied} 条记忆决策")
            await ConversationRepository.update_last_extracted_turn_id(conv_id, max_turn)
            return len(rows)
        except Exception as e:
            logger.warning(f"长期记忆提炼执行失败: {e}")
            try:
                await ConversationRepository.update_last_extracted_turn_id(conv_id, max_turn)
            except Exception:
                pass
            return 0


long_term_memory = LongTermMemoryManager()

