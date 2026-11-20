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
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger
from ..kb.storage import KBStorageManager
from ..providers.light_llm_router import light_llm_router
from .embedding_provider import embedding_router, HAS_NUMPY, np
from .rule_extractor import UniversalRuleExtractor, StructuredRuleExtractor
from .rule_profiles import RuleProfileManager


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


HYPE_SYSTEM_PROMPT = (
    "你是知识库问答提炼助手。请根据给定的文档段落或表格数据，"
    "提炼 3 到 4 个用户平时会随口问的大白话短问题。"
    "要求：\n"
    "1. 每条问题控制在 6~18 个字，口语自然，不要出现文件全称；\n"
    "2. 无论是规程、制度还是财务数据表，都必须提炼出可提问的问题，不要输出空数组；\n"
    "3. 每行直接输出一条问题，不要编号、不要引号、不要多余解释。"
)


class HyPEQuestionWorker:
    """HyPE 口语问答后台提炼生成工作器。"""

    _running_tasks: set[str] = set()
    # 记录 (kb_id, version, parent_id) -> 连续失败/空返回次数
    _failed_attempts: dict[tuple[str, str, str], int] = {}
    _max_attempts: int = 2
    # 与 llama-server 的 -np 4 吞吐对齐，按 event loop 绑定 4 路并发信号量
    _concurrency_limit: int = 4
    _concurrency_sems: dict[int, asyncio.Semaphore] = {}

    # 严禁生成的套话黑名单关键词
    FORBIDDEN_HYPE_PATTERNS = (
        ".doc", ".docx", ".pdf", ".txt", ".xlsx",
        "的具体要求和规定是什么", "的具体要求是什么", "的具体规定是什么",
        "如何执行", "怎么理解", "相关规定是什么", "请问如何",
    )

    @classmethod
    def _get_concurrency_sem(cls) -> asyncio.Semaphore:
        """获取当前事件循环对应的 4 路并发信号量。"""
        loop_id = id(asyncio.get_running_loop())
        sem = cls._concurrency_sems.get(loop_id)
        if sem is None:
            sem = asyncio.Semaphore(cls._concurrency_limit)
            cls._concurrency_sems[loop_id] = sem
        return sem

    @classmethod
    def reset_failed_cache(cls, kb_id: str | None = None) -> None:
        """清空指定知识库（或全部）的熔断记录，重置提炼计数。"""
        if not kb_id:
            cls._failed_attempts.clear()
        else:
            to_del = [k for k in cls._failed_attempts if k[0] == kb_id]
            for k in to_del:
                cls._failed_attempts.pop(k, None)
        logger.info(f"[HyPE] 熔断记录已重置 (kb_id={kb_id or 'all'})")

    @classmethod
    def _parse_questions(cls, raw: str) -> list[str]:
        """解析轻量模型输出，同时兼容 JSON 数组与逐行纯文本格式。"""
        text = (raw or "").strip()
        if not text:
            return []

        # 剥离已闭合与未闭合的 <think> 标签
        text = re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        # 剥离 ```json ... ``` 代码块包裹
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if json_match:
            text = json_match.group(1).strip()

        if not text:
            return []

        candidates: list[str]
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    candidates = [str(x or "").strip() for x in parsed]
                else:
                    candidates = text.splitlines()
            except Exception:
                candidates = text.splitlines()
        else:
            candidates = text.splitlines()

        questions: list[str] = []
        for line in candidates:
            q = line.strip().strip("\"'[]").rstrip("\"',]")
            # 仅剥离列表编号前缀（"1. "、"2、"、"- "），绝不误剥 "1.45米" 等小数数字
            q = re.sub(r"^(?:\d{1,2}[、)）]\s*|\d{1,2}\.\s+|[-•*]\s*)", "", q).strip()
            # 过滤因数字截断导致的残缺碎片（如以“米/元/岁/公斤”等孤立单位开头，或失去小数点前缀的“35米/30米/20米”）
            if re.match(r"^(?:米|元|岁|公分|厘米|千克|公斤)", q):
                continue
            if re.match(r"^(?:10|20|30|35|40|45)米", q):
                continue
            if 6 <= len(q) <= 45 and q not in ("[]", "无", "暂无"):
                if not any(bad in q for bad in cls.FORBIDDEN_HYPE_PATTERNS):
                    if q not in questions:
                        questions.append(q)
                        if len(questions) >= 4:
                            break
        return questions[:4]

    @classmethod
    async def _generate_colloquial_questions(
        cls,
        content: str,
        accounts: list[dict[str, Any]] | None = None,
    ) -> tuple[list[str], str]:
        """调用轻量模型提炼大白话短问题，返回 (解析后的问题列表, 模型原始文本)。"""
        # 预先剥离元数据头审查正文
        clean_content = re.sub(r"^【[^】]+】[^\n]*\n?", "", content or "").strip()
        if len(clean_content) < 10:
            return [], ""

        raw = await light_llm_router.complete(
            prompt=f"参考内容如下：\n{clean_content[:1800]}",
            accounts=accounts,
            system_prompt=HYPE_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=512,
        )
        if not raw or not raw.strip():
            return [], ""

        return cls._parse_questions(raw), raw

    @classmethod
    async def extract_questions_from_llm(
        cls,
        content: str,
        title: str = "",
        accounts: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        """调用后台轻量大语言模型提炼 3~4 个真实用户大白话短问题（纯连接池驱动）。

        Args:
            content: 父切片正文。
            title: 文档标题（预留兼容参数）。
            accounts: 可选的算力池账号列表（若为 None 则自动从数据库读取）。

        Returns:
            list[str]: 提炼出的高质量短口语列表；若无可用 Light 节点或未提取成功则返回空列表。
        """
        questions, _ = await cls._generate_colloquial_questions(
            content=content,
            accounts=accounts,
        )
        return questions

    @classmethod
    async def _mark_chunks_skip_hype(cls, db_path: Path, parent_ids: list[str]) -> None:
        """批量标记连续熔断的父切片为 skip_hype=1，避免无效空转。"""
        if not parent_ids:
            return

        def _update() -> None:
            conn = sqlite3.connect(str(db_path))
            try:
                with conn:
                    for i in range(0, len(parent_ids), 400):
                        batch = parent_ids[i : i + 400]
                        q_marks = ",".join(["?"] * len(batch))
                        conn.execute(
                            f"UPDATE hybrid_chunks SET skip_hype = 1 WHERE id IN ({q_marks})",
                            batch,
                        )
            except Exception as err:
                logger.warning(f"[HyPE] 写入 skip_hype 熔断标记失败: {err}")
            finally:
                conn.close()

        await asyncio.to_thread(_update)
        logger.info(f"[HyPE] 已在库中熔断标记 {len(parent_ids)} 个连续失败切片 (skip_hype=1)")

    @classmethod
    async def process_kb_hype(
        cls,
        kb_id: str,
        version: str = "v1.0.0",
        batch_limit: int = 12,
    ) -> int:
        """为指定知识库版本中未处理的 parent 切片提炼口语问答并向量化入库。

        Args:
            kb_id: 知识库 ID。
            version: 版本号。
            batch_limit: 每次处理上限（默认 12，与 4 路并发对齐）。

        Returns:
            int: 成功生成的口语问题切片数。
        """
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        if not db_path.exists():
            return 0

        # 查询未处理 HyPE 的 parent 切片
        def _fetch_parents() -> list[dict[str, Any]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                sql = """
                SELECT id, remote_uri, title, content, authority_tier, mtime, etag,
                       doc_year, effective_date, effective_ts, doc_priority_type,
                       topic_cluster_id, is_latest_in_cluster, section_path
                FROM hybrid_chunks
                WHERE (chunk_role = 'parent' OR (chunk_role = 'child' AND (parent_chunk_id IS NULL OR parent_chunk_id = '')))
                  AND (skip_hype IS NULL OR skip_hype = 0)
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

        # 拉取连接池并检查是否有可用轻量模型；无节点时严禁触发失败计数与熔断
        pool_accounts = await light_llm_router.fetch_pool_accounts()
        if light_llm_router.resolve_target(pool_accounts) is None:
            logger.debug("[HyPE] 当前算力池无可用轻量模型节点，本轮静默挂起等待")
            return 0

        # 过滤出未熔断切片，并将内存中已熔断切片落库标记
        already_fused_ids = [
            p["id"] for p in parents_to_process
            if cls._failed_attempts.get((kb_id, version, p["id"]), 0) >= cls._max_attempts
        ]
        active_parents = [
            p for p in parents_to_process
            if cls._failed_attempts.get((kb_id, version, p["id"]), 0) < cls._max_attempts
        ]
        if not active_parents:
            if already_fused_ids:
                await cls._mark_chunks_skip_hype(db_path, already_fused_ids)
            return 0

        logger.info(f"[HyPE] 发现 {len(active_parents)} 个父切片待 4 路并发生成口语问答 (kb_id={kb_id}, ver={version})")

        # 解析知识库所属领域及规则模板
        domain_type = "general"
        rule_profile_json = ""
        try:
            from ..repository.db import db_manager
            async with db_manager.get_connection() as conn:
                async with conn.execute(
                    "SELECT domain_type, rule_profile_json FROM knowledge_bases WHERE kb_id = ?",
                    (kb_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        domain_type = row["domain_type"] if "domain_type" in row.keys() and row["domain_type"] else "general"
                        rule_profile_json = row["rule_profile_json"] if "rule_profile_json" in row.keys() and row["rule_profile_json"] else ""
        except Exception:
            pass

        # 若为 custom 自定义行业且尚未生成模板，首次冷启动生成模板
        if domain_type == "custom" and not rule_profile_json and active_parents:
            sample_texts = [p.get("content", "") for p in active_parents[:3]]
            custom_profile = await RuleProfileManager.generate_custom_profile_from_llm(sample_texts)
            rule_profile_json = json.dumps(custom_profile, ensure_ascii=False)
            try:
                from ..repository.db import db_manager
                async with db_manager.get_connection() as conn:
                    await conn.execute(
                        "UPDATE knowledge_bases SET rule_profile_json = ? WHERE kb_id = ?",
                        (rule_profile_json, kb_id),
                    )
                    await conn.commit()
            except Exception:
                pass

        profile = UniversalRuleExtractor.resolve_profile(domain_type, rule_profile_json)
        provider = await embedding_router.get_provider()

        async def _handle_one(
            parent: dict[str, Any],
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
            # 前台优先让路退避调度：进入并发槽前休眠，避免空占 4 路槽位
            while ForegroundChatTracker.is_active():
                logger.debug("[HyPE] 检测到前台活跃对话，后台索引增强自动让路休眠 1.5s...")
                await asyncio.sleep(1.5)

            pid = parent["id"]
            content = parent.get("content", "")
            fuse_key = (kb_id, version, pid)

            async with cls._get_concurrency_sem():
                # 1. 结构化刚性业务规则同步抽取 (Universal Rule Engine)
                local_rules: list[dict[str, Any]] = []
                try:
                    extracted = UniversalRuleExtractor.extract_rules_from_parent_chunk(
                        chunk=parent,
                        kb_id=kb_id,
                        profile=profile,
                    )
                    if extracted:
                        local_rules.extend(extracted)
                except Exception as r_err:
                    logger.debug(f"[HyPE] 抽取结构化规则卡片异常: {r_err}")

                # 2. 提炼 3~4 个口语化提问
                questions, raw = await cls._generate_colloquial_questions(
                    content=content,
                    accounts=pool_accounts,
                )
                if not questions:
                    count = cls._failed_attempts.get(fuse_key, 0) + 1
                    cls._failed_attempts[fuse_key] = count
                    logger.warning(
                        f"[HyPE] parent_id={pid} 解析出 0 条问题 "
                        f"(第 {count}/{cls._max_attempts} 次)，原始返回: {repr((raw or '')[:200])}"
                    )
                    fused_id = pid if count >= cls._max_attempts else None
                    return [], local_rules, fused_id

                # 提取成功：清除历史失败计数并构建带范围隔离前缀的口语切片
                cls._failed_attempts.pop(fuse_key, None)
                local_hypes: list[dict[str, Any]] = []
                scope_label = str(parent.get("topic_cluster_id") or parent.get("title") or "").strip()[:30]
                year_val = int(parent.get("doc_year") or 0)
                prefix_parts = [p for p in (scope_label, str(year_val) if year_val > 0 else "") if p]
                scope_prefix = f"[{' | '.join(prefix_parts)}] " if prefix_parts else ""
                for idx, q in enumerate(questions, start=1):
                    scoped_q = f"{scope_prefix}{q}" if scope_prefix and not q.startswith("[") else q
                    local_hypes.append({
                        "id": f"hype_{pid}_{idx}",
                        "remote_uri": parent.get("remote_uri", ""),
                        "title": f"【口语问答】{q}",
                        "content": scoped_q,
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
                return local_hypes, local_rules, None

        results = await asyncio.gather(*[_handle_one(p) for p in active_parents])

        hype_chunks_to_insert: list[dict[str, Any]] = []
        rules_to_insert: list[dict[str, Any]] = []
        fused_parent_ids = list(already_fused_ids)

        for local_hypes, local_rules, fused_id in results:
            hype_chunks_to_insert.extend(local_hypes)
            rules_to_insert.extend(local_rules)
            if fused_id:
                fused_parent_ids.append(fused_id)

        if fused_parent_ids:
            await cls._mark_chunks_skip_hype(db_path, fused_parent_ids)

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
                # 持续循环处理直到全部 parent 完成或熔断
                while True:
                    count = await cls.process_kb_hype(kb_id, version, batch_limit=12)
                    if count == 0:
                        break
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"[HyPE] 异步问答生成任务异常: {e}")
            finally:
                cls._running_tasks.discard(task_key)

        try:
            from ..service.enrichment_queue import BackgroundEnrichmentQueue
            BackgroundEnrichmentQueue.enqueue(f"hype:{task_key}", _runner)
        except RuntimeError:
            pass

    @classmethod
    async def get_enhancement_status(cls, kb_id: str, version: str = "v1.0.0") -> dict[str, Any]:
        """获取知识库的后台口语化问答、RAPTOR 分层摘要与刚性规则抽取进度。"""
        from ..service.enrichment_queue import BackgroundEnrichmentQueue

        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        if not db_path.exists():
            return {
                "kb_id": kb_id,
                "version": version,
                "total_parents": 0,
                "hype_completed": 0,
                "rules_extracted": 0,
                "raptor_l1_count": 0,
                "raptor_l2_ready": False,
                "queue_status": BackgroundEnrichmentQueue.get_queue_status(kb_id),
                "status": "idle",
            }

        def _query() -> tuple[int, int, int, list[str], int, bool]:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            try:
                cur.execute("SELECT count(*) FROM hybrid_chunks WHERE chunk_role = 'parent'")
                total_parents = cur.fetchone()[0]

                cur.execute(
                    "SELECT count(DISTINCT parent_chunk_id) FROM hybrid_chunks WHERE chunk_role = 'hype_question' AND parent_chunk_id IS NOT NULL"
                )
                hype_completed = cur.fetchone()[0]

                cur.execute("SELECT count(*) FROM hybrid_chunks WHERE chunk_role = 'summary_l1'")
                raptor_l1_count = cur.fetchone()[0]

                cur.execute("SELECT count(*) FROM hybrid_chunks WHERE chunk_role = 'summary_l2'")
                raptor_l2_ready = cur.fetchone()[0] > 0

                rules_count = 0
                try:
                    cur.execute("SELECT count(*) FROM kb_structured_rules WHERE kb_id = ?", (kb_id,))
                    rules_count = cur.fetchone()[0]
                except Exception:
                    pass

                pending_ids: list[str] = []
                try:
                    cur.execute(
                        """
                        SELECT id FROM hybrid_chunks
                        WHERE chunk_role = 'parent'
                          AND (skip_hype IS NULL OR skip_hype = 0)
                          AND id NOT IN (
                              SELECT DISTINCT parent_chunk_id
                              FROM hybrid_chunks
                              WHERE chunk_role = 'hype_question' AND parent_chunk_id IS NOT NULL
                          )
                        """
                    )
                    pending_ids = [r[0] for r in cur.fetchall()]
                except Exception:
                    pending_ids = []

                return total_parents, hype_completed, rules_count, pending_ids, raptor_l1_count, raptor_l2_ready
            except Exception:
                return 0, 0, 0, [], 0, False
            finally:
                conn.close()

        total, completed, rules, pending_ids, raptor_l1_count, raptor_l2_ready = await asyncio.to_thread(_query)
        active_pending = [
            pid for pid in pending_ids
            if cls._failed_attempts.get((kb_id, version, pid), 0) < cls._max_attempts
        ]
        task_key = f"{kb_id}:{version}"
        if task_key in cls._running_tasks:
            status = "running"
        elif total > 0 and len(active_pending) == 0:
            status = "completed"
        elif len(active_pending) > 0:
            # 存在未完成且未熔断的父切片，自动接续调度后台增强
            cls.schedule_background_hype(kb_id, version)
            status = "running"
        else:
            status = "idle"

        return {
            "kb_id": kb_id,
            "version": version,
            "total_parents": total,
            "hype_completed": completed,
            "rules_extracted": rules,
            "raptor_l1_count": raptor_l1_count,
            "raptor_l2_ready": raptor_l2_ready,
            "queue_status": BackgroundEnrichmentQueue.get_queue_status(kb_id),
            "status": status,
        }

