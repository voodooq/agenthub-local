"""
HyPE (Hypothetical Prompt Enriched) 口语问答生成引擎
核心功能：
1. 后台异步扫描未提炼口语问答的父切片 (parent chunk)；
2. 调度后台轻量大模型 (task_role="light"，严格排除 embedding_only) 提炼 3~4 个真实用户可能提问的大白话问题；
3. 将提炼的口语问题作为独立的 'hype_question' 切片存入知识库 index.db；
4. 绑定 parent_chunk_id 并计算 1024 维向量；
5. 提供异步任务队列与批量调度接口。
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger
from ..kb.storage import KBStorageManager
from .embedding_provider import embedding_router, HAS_NUMPY, np
from .rule_extractor import StructuredRuleExtractor


class ForegroundChatTracker:
    """前台活跃对话追踪器（用于后台增强任务让路限流）。"""

    _active_count: int = 0

    @classmethod
    def enter(cls) -> None:
        cls._active_count += 1

    @classmethod
    def exit(cls) -> None:
        cls._active_count = max(0, cls._active_count - 1)

    @classmethod
    def is_active(cls) -> bool:
        return cls._active_count > 0


HYPE_SYSTEM_PROMPT = "你是一个专业规程问答提炼专家，只输出纯文本问题列表，严禁寒暄与废话。"

HYPE_EXTRACT_PROMPT = """请仔细阅读以下规程或技术文档段落，设想 3~4 个真实用户在日常工作中可能提问的大白话/口语问题（例如：“出差伙食补贴一天多少”、“加班可以申请调休还是必须发加班费”、“怎么办理注销”）。

要求：
1. 每行仅输出一个问题，不要带任何序号（如 1. 2. - *）、标号或解释说明；
2. 提问要像普通员工日常口语大白话，直奔核心规则、金额或流程；
3. 输出 3 到 4 个问题，每行一个。

文档段落：
{content}
"""


class HyPEQuestionWorker:
    """HyPE 口语问答后台提炼生成工作器。"""

    _running_tasks: set[str] = set()

    @classmethod
    async def extract_questions_from_llm(cls, content: str, title: str = "") -> list[str]:
        """调用后台轻量大语言模型提炼 3~4 个真实用户口语化问题。

        Args:
            content: 父切片正文。
            title: 文档标题。

        Returns:
            list[str]: 提炼出的口语问题列表。
        """
        prompt = HYPE_EXTRACT_PROMPT.format(content=content[:1800])
        raw_output = ""

        try:
            from ..service.engine_pool import engine_pool

            async def _consume_stream() -> str:
                collected = ""
                async for evt in engine_pool.execute_stream_with_failover(
                    conversation_id="hype_worker_bg",
                    user_prompt=prompt,
                    model_name="",
                    thinking_level="low",
                    system_override=HYPE_SYSTEM_PROMPT,
                    temperature=0.3,
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

            raw_output = await asyncio.wait_for(_consume_stream(), timeout=20.0)
        except Exception as e:
            logger.debug(f"[HyPE] 轻量大模型生成口语问题跳过或超时: {e}")

        # 解析与清洗结果
        questions: list[str] = []
        if raw_output and raw_output.strip():
            for line in raw_output.splitlines():
                cleaned = re.sub(r"^[\d\.\-\*\s一二三四、]+", "", line).strip()
                cleaned = cleaned.rstrip("？? ") + "？" if cleaned else ""
                if len(cleaned) >= 4 and cleaned not in questions:
                    questions.append(cleaned)
                    if len(questions) >= 4:
                        break

        # 启发式规则兜底：如果 LLM 未能返回足够问题，利用标题与段落首句生成
        if len(questions) < 2:
            clean_title = re.sub(r"[\.pdf|\.docx|\.md|\.txt]", "", title).strip()
            if clean_title:
                questions.append(f"{clean_title}的具体要求和规定是什么？")
                questions.append(f"如何执行{clean_title}？")
            else:
                first_sentence = content.split("。")[0].strip()
                if first_sentence:
                    questions.append(f"{first_sentence[:30]}怎么理解？")

        return questions[:4]

    @classmethod
    async def process_kb_hype(
        cls,
        kb_id: str,
        version: str = "v1.0.0",
        batch_limit: int = 15,
    ) -> int:
        """为指定知识库版本中未处理的 parent 切片提炼口语问答并向量化入库。

        Args:
            kb_id: 知识库 ID。
            version: 版本号。
            batch_limit: 每次处理上限。

        Returns:
            int: 成功生成的口语问题切片数。
        """
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        if not db_path.exists():
            return 0

        # 查询未处理 HyPE 的 parent 切片
        parents_to_process: list[dict[str, Any]] = []

        def _fetch_parents() -> list[dict[str, Any]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                sql = """
                SELECT id, remote_uri, title, content, authority_tier, mtime, etag,
                       doc_year, effective_date, effective_ts, doc_priority_type,
                       topic_cluster_id, is_latest_in_cluster, section_path
                FROM hybrid_chunks
                WHERE chunk_role = 'parent'
                  AND id NOT IN (
                      SELECT DISTINCT parent_chunk_id
                      FROM hybrid_chunks
                      WHERE chunk_role = 'hype_question' AND parent_chunk_id IS NOT NULL
                  )
                LIMIT ?
                """
                rows = conn.execute(sql, (batch_limit,)).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

        parents_to_process = await asyncio.to_thread(_fetch_parents)
        if not parents_to_process:
            return 0

        logger.info(f"[HyPE] 发现 {len(parents_to_process)} 个父切片待生成口语化问答 (kb_id={kb_id}, ver={version})")

        provider = await embedding_router.get_provider()
        hype_chunks_to_insert: list[dict[str, Any]] = []
        rules_to_insert: list[dict[str, Any]] = []

        for parent in parents_to_process:
            # 前台优先让路退避调度：感知到前台活跃对话时休眠 1.5s
            while ForegroundChatTracker.is_active():
                logger.debug("[HyPE] 检测到前台活跃对话，后台索引增强自动让路休眠 1.5s...")
                await asyncio.sleep(1.5)

            pid = parent["id"]
            title = parent.get("title", "")
            content = parent.get("content", "")

            # 1. 结构化刚性业务规则同步抽取 (T-159-23)
            try:
                extracted = StructuredRuleExtractor.extract_rules_from_parent_chunk(
                    chunk=parent,
                    kb_id=kb_id,
                )
                if extracted:
                    rules_to_insert.extend(extracted)
            except Exception as r_err:
                logger.debug(f"[HyPE] 抽取结构化规则卡片异常: {r_err}")

            # 2. 提炼 3~4 个口语化提问
            questions = await cls.extract_questions_from_llm(content=content, title=title)
            for idx, q in enumerate(questions, start=1):
                chunk_id = f"hype_{pid}_{idx}"
                hype_chunks_to_insert.append({
                    "id": chunk_id,
                    "remote_uri": parent.get("remote_uri", ""),
                    "title": f"【口语问答】{q}",
                    "content": q,
                    "authority_tier": parent.get("authority_tier", 2),
                    "mtime": parent.get("mtime", time.time()),
                    "etag": parent.get("etag", ""),
                    "doc_year": parent.get("doc_year", 0),
                    "effective_date": parent.get("effective_date", ""),
                    "effective_ts": parent.get("effective_ts", 0),
                    "doc_priority_type": parent.get("doc_priority_type", "normal"),
                    "topic_cluster_id": parent.get("topic_cluster_id", ""),
                    "is_latest_in_cluster": parent.get("is_latest_in_cluster", 1),
                    "chunk_role": "hype_question",
                    "parent_chunk_id": pid,
                    "section_path": parent.get("section_path", ""),
                })

        # 3. 批量持久化结构化规则
        if rules_to_insert:
            saved_r_count = StructuredRuleExtractor.batch_save_rules(db_path=db_path, rules=rules_to_insert)
            logger.info(f"[HyPE] 成功抽取并写入 {saved_r_count} 条刚性业务规则卡片 (kb_id={kb_id})")

        if not hype_chunks_to_insert:
            return 0

        # 批量计算 1024 维向量
        texts = [c["content"] for c in hype_chunks_to_insert]
        embeddings = None
        if provider.is_available and provider.dimension > 0:
            try:
                embeddings = await provider.encode_batch(texts)
                if embeddings is None:
                    embeddings = await provider.embed_texts(texts)
            except Exception as e:
                logger.warning(f"[HyPE] 批量向量化失败，降级为文本索引: {e}")

        # 写入数据库
        now = time.time()

        def _write_hype_records():
            conn = sqlite3.connect(str(db_path))
            try:
                with conn:
                    for idx, c in enumerate(hype_chunks_to_insert):
                        blob_data = None
                        if embeddings is not None and idx < len(embeddings):
                            vec = embeddings[idx]
                            if HAS_NUMPY and np is not None and hasattr(vec, "astype"):
                                blob_data = vec.astype(np.float32).tobytes()
                            else:
                                import array
                                blob_data = array.array("f", (float(x) for x in vec)).tobytes()

                        conn.execute(
                            """
                            INSERT OR REPLACE INTO hybrid_chunks (
                                id, remote_uri, title, content, authority_tier, mtime, etag, embedding_blob,
                                doc_year, effective_date, effective_ts, doc_priority_type, topic_cluster_id,
                                is_latest_in_cluster, chunk_role, parent_chunk_id, section_path, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                c["id"],
                                c["remote_uri"],
                                c["title"],
                                c["content"],
                                c["authority_tier"],
                                c["mtime"],
                                c["etag"],
                                blob_data,
                                c["doc_year"],
                                c["effective_date"],
                                c["effective_ts"],
                                c["doc_priority_type"],
                                c["topic_cluster_id"],
                                c["is_latest_in_cluster"],
                                c["chunk_role"],
                                c["parent_chunk_id"],
                                c["section_path"],
                                now,
                            ),
                        )
            finally:
                conn.close()

        await asyncio.to_thread(_write_hype_records)
        logger.info(f"[HyPE] 成功写入 {len(hype_chunks_to_insert)} 条口语问答切片并向量化")
        return len(hype_chunks_to_insert)

    @classmethod
    def schedule_background_hype(cls, kb_id: str, version: str = "v1.0.0") -> None:
        """调度后台异步 HyPE 任务，不阻塞上传与检索。"""
        task_key = f"{kb_id}:{version}"
        if task_key in cls._running_tasks:
            return

        async def _runner():
            cls._running_tasks.add(task_key)
            try:
                # 持续处理直到全部 parent 完成或达到单次轮转上限
                for _ in range(5):
                    count = await cls.process_kb_hype(kb_id, version, batch_limit=10)
                    if count == 0:
                        break
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"[HyPE] 异步问答生成任务异常: {e}")
            finally:
                cls._running_tasks.discard(task_key)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_runner())
        except RuntimeError:
            pass

    @classmethod
    async def get_enhancement_status(cls, kb_id: str, version: str = "v1.0.0") -> dict[str, Any]:
        """获取知识库的后台口语化问答与刚性规则抽取进度。"""
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        if not db_path.exists():
            return {
                "kb_id": kb_id,
                "version": version,
                "total_parents": 0,
                "hype_completed": 0,
                "rules_extracted": 0,
                "status": "idle",
            }

        def _query():
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            try:
                cur.execute("SELECT count(*) FROM hybrid_chunks WHERE chunk_role = 'parent'")
                total_parents = cur.fetchone()[0]

                cur.execute(
                    "SELECT count(DISTINCT parent_chunk_id) FROM hybrid_chunks WHERE chunk_role = 'hype_question' AND parent_chunk_id IS NOT NULL"
                )
                hype_completed = cur.fetchone()[0]

                rules_count = 0
                try:
                    cur.execute("SELECT count(*) FROM kb_structured_rules WHERE kb_id = ?", (kb_id,))
                    rules_count = cur.fetchone()[0]
                except Exception:
                    pass

                return total_parents, hype_completed, rules_count
            except Exception:
                return 0, 0, 0
            finally:
                conn.close()

        total, completed, rules = await asyncio.to_thread(_query)
        task_key = f"{kb_id}:{version}"
        if task_key in cls._running_tasks:
            status = "running"
        elif total > 0 and completed >= total:
            status = "completed"
        else:
            status = "idle"

        return {
            "kb_id": kb_id,
            "version": version,
            "total_parents": total,
            "hype_completed": completed,
            "rules_extracted": rules,
            "status": status,
        }

