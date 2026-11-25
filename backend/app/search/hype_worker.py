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
    "提炼 2 个用户平时会随口问的最核心大白话短问题。"
    "要求：\n"
    "1. 每条问题控制在 6~18 个字，口语自然，不要出现文件全称；\n"
    "2. 必须提炼出可提问的问题，不要输出空数组；\n"
    "3. 每行直接输出一条问题，不要编号、不要引号、不要多余解释；\n"
    "4. 严禁截断高度、金额、比例等小数或数字前缀（如 `1.00米`、`0.60米`、`1.35米` 必须完整保留前面的 `1.` 或 `0.`）。"
)

HYPE_BATCH_SYSTEM_PROMPT = (
    "你是知识库问答提炼助手。请为下面带编号 [1]、[2]... 的每个文档片段，各提炼 2 个用户平时会随口问的最核心大白话短问题。\n"
    "严格要求：\n"
    "1. 每条问题控制在 6~18 个字，口语自然，不要出现文件全称，完整保留小数/金额前缀（如 1.20米、0.60米）；\n"
    "2. 严格只输出一个 JSON 对象，Key 为片段编号字符串，Value 为包含 2 条问题的字符串数组，例如：{\"1\": [\"问题A\", \"问题B\"], \"2\": [\"问题C\", \"问题D\"]}；\n"
    "3. 严禁输出任何解释、Markdown 说明或多余字符。"
)


class HyPEQuestionWorker:
    """HyPE 口语问答后台提炼生成工作器。"""

    _running_tasks: set[str] = set()
    # 记录 (kb_id, version, parent_id) -> 连续失败/空返回次数
    _failed_attempts: dict[tuple[str, str, str], int] = {}
    _max_attempts: int = 2
    # 本地轻量节点默认 4 路（对齐 llama-server -np 4），云端高速通道开 10 路
    _local_concurrency_limit: int = 4
    _cloud_concurrency_limit: int = 10
    _concurrency_sems: dict[tuple[int, int], asyncio.Semaphore] = {}
    # 批量打包与批量向量化参数
    LLM_PACK_SIZE: int = 4
    EMBEDDING_BATCH_SIZE: int = 32
    MIN_BODY_CHARS: int = 80

    # 严禁生成的套话黑名单关键词
    FORBIDDEN_HYPE_PATTERNS = (
        ".doc", ".docx", ".pdf", ".txt", ".xlsx",
        "的具体要求和规定是什么", "的具体要求是什么", "的具体规定是什么",
        "如何执行", "怎么理解", "相关规定是什么", "请问如何",
    )

    @classmethod
    def _is_gibberish_content(cls, content: str) -> bool:
        """复用通用文本质量门禁，判定切片正文是否为二进制流误解码生成的全生僻字符乱码。"""
        from .text_quality_gate import TextQualityGate
        return TextQualityGate.is_gibberish_chinese(content)

    @classmethod
    def _strip_meta_header(cls, content: str) -> str:
        """剥离标准元数据头，返回纯正文文本。"""
        return re.sub(r"^【[^】]+】[^\n]*\n?", "", content or "").strip()

    @classmethod
    def _should_skip_chunk_prefilter(cls, parent: dict[str, Any]) -> tuple[bool, str]:
        """前置过滤：跳过 Tier 3 归档文档、长度 < 80 字无实质碎片以及二进制乱码切片。"""
        tier = int(parent.get("authority_tier") or 2)
        if tier >= 3:
            return True, "tier3_archived"
        clean_body = cls._strip_meta_header(str(parent.get("content") or ""))
        if len(clean_body) < cls.MIN_BODY_CHARS:
            return True, f"short_body<{cls.MIN_BODY_CHARS}"
        if cls._is_gibberish_content(str(parent.get("content") or "")):
            return True, "gibberish"
        return False, ""

    @classmethod
    def _get_concurrency_sem(cls, is_cloud: bool = False) -> asyncio.Semaphore:
        """按运行节点类型动态获取并发信号量（本地 4 路 / 云端高速 10 路）。"""
        limit = cls._cloud_concurrency_limit if is_cloud else cls._local_concurrency_limit
        loop_id = id(asyncio.get_running_loop())
        key = (loop_id, limit)
        sem = cls._concurrency_sems.get(key)
        if sem is None:
            sem = asyncio.Semaphore(limit)
            cls._concurrency_sems[key] = sem
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

        return cls._filter_valid_questions(candidates, max_count=2)

    @classmethod
    def _filter_valid_questions(cls, candidates: list[str], max_count: int = 2) -> list[str]:
        """清洗并过滤口语问句列表，确保保留小数前缀且无黑名单套话。"""
        questions: list[str] = []
        for line in candidates:
            q = str(line or "").strip().strip("\"'[]").rstrip("\"',]")
            # 仅剥离列表编号前缀（"1. "、"2、"、"- "），绝不误剥 "1.45米" 等小数数字
            q = re.sub(r"^(?:\d{1,2}[、)）]\s*|\d{1,2}\.\s+|[-•*]\s*)", "", q).strip()
            if re.match(r"^(?:米|元|岁|公分|厘米|千克|公斤)", q):
                continue
            if re.match(r"^(?:00|10|20|30|35|40|45|50|60|70|80|90)米", q):
                continue
            if 6 <= len(q) <= 45 and q not in ("[]", "无", "暂无"):
                if not any(bad in q for bad in cls.FORBIDDEN_HYPE_PATTERNS):
                    if q not in questions:
                        questions.append(q)
                        if len(questions) >= max_count:
                            break
        return questions[:max_count]

    @classmethod
    def _parse_batch_questions(cls, raw: str, item_count: int) -> dict[int, list[str]] | None:
        """解析打包返回的 JSON 对象 {"1": [...], "2": [...]}，解析失败返回 None 触发单片降级。"""
        text = (raw or "").strip()
        if not text:
            return None
        text = re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if json_match:
            text = json_match.group(1).strip()
        # 容错提取首尾大括号内的 JSON
        lbrace = text.find("{")
        rbrace = text.rfind("}")
        if lbrace != -1 and rbrace > lbrace:
            text = text[lbrace : rbrace + 1]
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                return None
            out: dict[int, list[str]] = {}
            for idx in range(1, item_count + 1):
                val = parsed.get(str(idx)) or parsed.get(idx)
                if isinstance(val, list):
                    qs = cls._filter_valid_questions([str(x or "") for x in val], max_count=2)
                    if qs:
                        out[idx - 1] = qs
            return out if out else None
        except Exception:
            return None

    @classmethod
    async def _generate_colloquial_questions(
        cls,
        content: str,
        accounts: list[dict[str, Any]] | None = None,
    ) -> tuple[list[str], str]:
        """调用轻量模型提炼 2 个核心大白话短问题，返回 (解析后的问题列表, 模型原始文本)。"""
        clean_content = cls._strip_meta_header(content)
        if len(clean_content) < 10:
            return [], ""

        raw = await light_llm_router.complete(
            prompt=f"参考内容如下：\n{clean_content[:1400]}",
            accounts=accounts,
            system_prompt=HYPE_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=160,
            enable_thinking=False,
        )
        if not raw or not raw.strip():
            return [], ""

        return cls._parse_questions(raw), raw

    @classmethod
    async def _generate_batch_questions(
        cls,
        contents: list[str],
        accounts: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[int, list[str]] | None, str]:
        """将 3~4 个父切片打包为 1 次 LLM 请求，返回 {索引: 问题列表}。"""
        blocks: list[str] = []
        for idx, c in enumerate(contents, start=1):
            clean_c = cls._strip_meta_header(c)[:900]
            blocks.append(f"[{idx}]\n{clean_c}")
        prompt = "请为以下文档片段分别提炼 2 个核心口语问题（输出 JSON 对象）：\n\n" + "\n\n".join(blocks)

        raw = await light_llm_router.complete(
            prompt=prompt,
            accounts=accounts,
            system_prompt=HYPE_BATCH_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=384,
            enable_thinking=False,
        )
        if not raw or not raw.strip():
            return None, ""
        return cls._parse_batch_questions(raw, len(contents)), raw

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
    def _compute_content_hash(cls, row: dict[str, Any]) -> str:
        """获取切片内容哈希（优先读取 content_hash 字段，缺失时按 content 计算 MD5）。"""
        existing = str(row.get("content_hash") or "").strip()
        if existing:
            return existing
        return hashlib.md5(str(row.get("content") or "").encode("utf-8")).hexdigest()

    @classmethod
    def _reuse_from_previous_versions(
        cls,
        kb_id: str,
        current_ver: str,
        db_path: Path,
        candidates: list[dict[str, Any]],
    ) -> tuple[int, list[dict[str, Any]]]:
        """跨版本按父切片 content_hash 复用旧版 hype_question 与向量，重映射 parent_chunk_id 并同步 FTS5。"""
        if not candidates:
            return 0, []

        vers_dir = KBStorageManager.get_kb_dir(kb_id) / "versions"
        if not vers_dir.exists():
            return 0, candidates

        # 按修改时间倒序收集除当前版本外的历史版本 index.db
        prev_dbs: list[Path] = []
        for d in sorted(vers_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if d.is_dir() and d.name != current_ver:
                cand_db = d / "index.db"
                if cand_db.exists():
                    prev_dbs.append(cand_db)
        if not prev_dbs:
            return 0, candidates

        # 为当前待处理父切片建立 hash -> list[parent_dict] 索引
        hash_to_parents: dict[str, list[dict[str, Any]]] = {}
        for p in candidates:
            chash = cls._compute_content_hash(p)
            hash_to_parents.setdefault(chash, []).append(p)

        reused_records: list[dict[str, Any]] = []
        reused_parent_ids: set[str] = set()

        for pdb in prev_dbs:
            remaining_hashes = [h for h, plist in hash_to_parents.items() if any(p["id"] not in reused_parent_ids for p in plist)]
            if not remaining_hashes:
                break
            p_conn = sqlite3.connect(str(pdb))
            p_conn.row_factory = sqlite3.Row
            try:
                p_cols = [c[1] for c in p_conn.execute("PRAGMA table_info(hybrid_chunks)").fetchall()]
                p_hash_col = ", content_hash" if "content_hash" in p_cols else ", '' as content_hash"
                old_parents = p_conn.execute(
                    f"SELECT id, content{p_hash_col} FROM hybrid_chunks "
                    "WHERE chunk_role = 'parent' OR (chunk_role = 'child' AND (parent_chunk_id IS NULL OR parent_chunk_id = ''))"
                ).fetchall()
                old_pid_by_hash: dict[str, str] = {}
                for op in old_parents:
                    op_d = dict(op)
                    oh = cls._compute_content_hash(op_d)
                    if oh in hash_to_parents and oh not in old_pid_by_hash:
                        old_pid_by_hash[oh] = str(op_d["id"])

                if not old_pid_by_hash:
                    continue

                old_pids = list(old_pid_by_hash.values())
                old_pid_to_hash = {v: k for k, v in old_pid_by_hash.items()}
                for i in range(0, len(old_pids), 300):
                    batch_pids = old_pids[i : i + 300]
                    q_marks = ",".join("?" for _ in batch_pids)
                    h_rows = p_conn.execute(
                        f"SELECT title, content, embedding_blob, parent_chunk_id "
                        f"FROM hybrid_chunks WHERE chunk_role = 'hype_question' AND parent_chunk_id IN ({q_marks})",
                        batch_pids,
                    ).fetchall()
                    by_old_pid: dict[str, list[dict[str, Any]]] = {}
                    for hr in h_rows:
                        by_old_pid.setdefault(str(hr["parent_chunk_id"]), []).append(dict(hr))

                    for old_pid, q_list in by_old_pid.items():
                        chash = old_pid_to_hash.get(old_pid)
                        if not chash:
                            continue
                        for new_parent in hash_to_parents.get(chash, []):
                            new_pid = new_parent["id"]
                            if new_pid in reused_parent_ids:
                                continue
                            reused_parent_ids.add(new_pid)
                            for idx, q_item in enumerate(q_list, start=1):
                                reused_records.append({
                                    "id": f"hype_{new_pid}_{idx}",
                                    "remote_uri": new_parent.get("remote_uri", ""),
                                    "title": q_item.get("title", ""),
                                    "content": q_item.get("content", ""),
                                    "authority_tier": new_parent.get("authority_tier", 2),
                                    "mtime": new_parent.get("mtime", time.time()),
                                    "etag": new_parent.get("etag", ""),
                                    "embedding_blob": q_item.get("embedding_blob"),
                                    "doc_year": new_parent.get("doc_year", 0),
                                    "effective_date": new_parent.get("effective_date", ""),
                                    "effective_ts": new_parent.get("effective_ts", 0),
                                    "doc_priority_type": new_parent.get("doc_priority_type", "normal"),
                                    "topic_cluster_id": new_parent.get("topic_cluster_id", ""),
                                    "is_latest_in_cluster": new_parent.get("is_latest_in_cluster", 1),
                                    "chunk_role": "hype_question",
                                    "parent_chunk_id": new_pid,
                                    "section_path": new_parent.get("section_path", ""),
                                    "provenance_json": new_parent.get("provenance_json", ""),
                                })
            except Exception as reuse_err:
                logger.debug(f"[HyPE] 跨版本复用读取 {pdb} 跳过: {reuse_err}")
            finally:
                p_conn.close()

        if reused_records:
            now = time.time()
            conn = sqlite3.connect(str(db_path))
            try:
                cols = [col[1] for col in conn.execute("PRAGMA table_info(hybrid_chunks)").fetchall()]
                has_prov = "provenance_json" in cols
                has_trigger = bool(
                    conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='trg_hc_ai'").fetchone()
                )
                has_fts = bool(
                    conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hybrid_chunks_fts'").fetchone()
                )
                with conn:
                    for c in reused_records:
                        if has_prov:
                            cur = conn.execute(
                                """
                                INSERT OR REPLACE INTO hybrid_chunks (
                                    id, remote_uri, title, content, authority_tier, mtime, etag, embedding_blob,
                                    doc_year, effective_date, effective_ts, doc_priority_type, topic_cluster_id,
                                    is_latest_in_cluster, chunk_role, parent_chunk_id, section_path, created_at,
                                    provenance_json
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    c["id"], c["remote_uri"], c["title"], c["content"], c["authority_tier"],
                                    c["mtime"], c["etag"], c["embedding_blob"], c["doc_year"], c["effective_date"],
                                    c["effective_ts"], c["doc_priority_type"], c["topic_cluster_id"],
                                    c["is_latest_in_cluster"], c["chunk_role"], c["parent_chunk_id"],
                                    c["section_path"], now, c.get("provenance_json", ""),
                                ),
                            )
                        else:
                            cur = conn.execute(
                                """
                                INSERT OR REPLACE INTO hybrid_chunks (
                                    id, remote_uri, title, content, authority_tier, mtime, etag, embedding_blob,
                                    doc_year, effective_date, effective_ts, doc_priority_type, topic_cluster_id,
                                    is_latest_in_cluster, chunk_role, parent_chunk_id, section_path, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    c["id"], c["remote_uri"], c["title"], c["content"], c["authority_tier"],
                                    c["mtime"], c["etag"], c["embedding_blob"], c["doc_year"], c["effective_date"],
                                    c["effective_ts"], c["doc_priority_type"], c["topic_cluster_id"],
                                    c["is_latest_in_cluster"], c["chunk_role"], c["parent_chunk_id"],
                                    c["section_path"], now,
                                ),
                            )
                        # 若库内未建触发器，显式同步 FTS5 索引确保关键词可检
                        if has_fts and not has_trigger:
                            try:
                                conn.execute("DELETE FROM hybrid_chunks_fts WHERE chunk_id = ?", (c["id"],))
                                conn.execute(
                                    "INSERT INTO hybrid_chunks_fts(rowid, content, title, chunk_id, remote_uri) VALUES (?, ?, ?, ?, ?)",
                                    (cur.lastrowid, c["content"], c["title"], c["id"], c["remote_uri"]),
                                )
                            except Exception:
                                pass
            finally:
                conn.close()

        remaining = [p for p in candidates if p["id"] not in reused_parent_ids]
        return len(reused_records), remaining

    @classmethod
    async def _encode_texts_in_batches(
        cls,
        provider: Any,
        texts: list[str],
        batch_size: int = 32,
    ) -> list[Any] | None:
        """按 32 条每批调用 Embedding 接口计算向量，避免小碎片频繁 HTTP 请求。"""
        if not texts or not provider.is_available or provider.dimension <= 0:
            return None

        all_vecs: list[Any] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                vecs = await provider.encode_batch(batch)
                if vecs is None:
                    vecs = await provider.embed_texts(batch)
                if vecs is None or len(vecs) != len(batch):
                    return None
                all_vecs.extend(vecs)
            except Exception as e:
                logger.warning(f"[HyPE] 批量向量化异常，降级为文本索引: {e}")
                return None
        return all_vecs

    @classmethod
    async def process_kb_hype(
        cls,
        kb_id: str,
        version: str = "v1.0.0",
        batch_limit: int = 48,
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

        # 1. 拉取未处理 HyPE 的切片（默认批大小 48，适配打包与 32 条批量向量化）
        def _fetch_parents() -> list[dict[str, Any]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                cols = [c[1] for c in conn.execute("PRAGMA table_info(hybrid_chunks)").fetchall()]
                prov_select = ", provenance_json" if "provenance_json" in cols else ", '' as provenance_json"
                hash_select = ", content_hash" if "content_hash" in cols else ", '' as content_hash"
                sql = f"""
                SELECT id, remote_uri, title, content, authority_tier, mtime, etag,
                       doc_year, effective_date, effective_ts, doc_priority_type,
                       topic_cluster_id, is_latest_in_cluster, section_path{prov_select}{hash_select}
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

        # 2. 前置过滤：跳过 Tier 3 归档、正文 < 80 字碎片及二进制乱码切片（直接标记 skip_hype=1）
        prefilter_skip_ids: list[str] = []
        after_prefilter: list[dict[str, Any]] = []
        for p in parents_to_process:
            skip, reason = cls._should_skip_chunk_prefilter(p)
            if skip:
                prefilter_skip_ids.append(p["id"])
                logger.debug(f"[HyPE] 前置跳过切片 id={p['id']} ({reason})")
            else:
                after_prefilter.append(p)

        if prefilter_skip_ids:
            await cls._mark_chunks_skip_hype(db_path, prefilter_skip_ids)

        if not after_prefilter:
            # 本批全部被前置跳过，返回跳过数以驱动后台循环继续拉取下一批
            return len(prefilter_skip_ids)

        # 3. 跨版本 content_hash 复用：从前序历史版本直接拷贝已有 hype_question 与 embedding_blob
        reused_count, need_llm_parents = await asyncio.to_thread(
            cls._reuse_from_previous_versions, kb_id, version, db_path, after_prefilter
        )
        if reused_count > 0:
            logger.info(f"[HyPE] 跨版本哈希复用成功: 零开销迁移 {reused_count} 条口语问答及向量 (kb_id={kb_id}, ver={version})")

        if not need_llm_parents:
            return reused_count + len(prefilter_skip_ids)

        # 4. 拉取连接池并检查是否有可用轻量模型；无节点时严禁触发失败计数与熔断
        pool_accounts = await light_llm_router.fetch_pool_accounts()
        target_node = light_llm_router.resolve_target(pool_accounts)
        if target_node is None:
            logger.debug("[HyPE] 当前算力池无可用轻量模型节点，本轮静默挂起等待")
            return reused_count

        is_cloud = light_llm_router.is_cloud_target(target_node)
        conc_limit = cls._cloud_concurrency_limit if is_cloud else cls._local_concurrency_limit

        # 过滤出未熔断切片，并将内存中已熔断切片落库标记
        already_fused_ids = [
            p["id"] for p in need_llm_parents
            if cls._failed_attempts.get((kb_id, version, p["id"]), 0) >= cls._max_attempts
        ]
        active_parents = [
            p for p in need_llm_parents
            if cls._failed_attempts.get((kb_id, version, p["id"]), 0) < cls._max_attempts
        ]
        if not active_parents:
            if already_fused_ids:
                await cls._mark_chunks_skip_hype(db_path, already_fused_ids)
            return reused_count + len(prefilter_skip_ids)

        logger.info(
            f"[HyPE] 发现 {len(active_parents)} 个父切片待 {conc_limit} 路并发"
            f"({'云端' if is_cloud else '本地'})打包生成口语问答 (kb_id={kb_id}, ver={version})"
        )

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

        def _build_hype_dicts(parent: dict[str, Any], questions: list[str]) -> list[dict[str, Any]]:
            pid = parent["id"]
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
                    "provenance_json": parent.get("provenance_json", ""),
                })
            return local_hypes

        async def _handle_one(
            parent: dict[str, Any],
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
            while ForegroundChatTracker.is_active():
                logger.debug("[HyPE] 检测到前台活跃对话，后台索引增强自动让路休眠 1.5s...")
                await asyncio.sleep(1.5)

            pid = parent["id"]
            content = parent.get("content", "")
            fuse_key = (kb_id, version, pid)

            async with cls._get_concurrency_sem(is_cloud=is_cloud):
                local_rules: list[dict[str, Any]] = []
                try:
                    extracted = UniversalRuleExtractor.extract_rules_from_parent_chunk(
                        chunk=parent, kb_id=kb_id, profile=profile,
                    )
                    if extracted:
                        local_rules.extend(extracted)
                except Exception as r_err:
                    logger.debug(f"[HyPE] 抽取结构化规则卡片异常: {r_err}")

                questions, raw = await cls._generate_colloquial_questions(
                    content=content, accounts=pool_accounts,
                )
                if not questions:
                    is_model_refusal = any(
                        bad_kw in (raw or "")
                        for bad_kw in ("乱码", "加密数据", "无法提取有效信息", "无法提取出有效信息", "加密信息")
                    )
                    count = cls._failed_attempts.get(fuse_key, 0) + 1
                    cls._failed_attempts[fuse_key] = count
                    logger.warning(
                        f"[HyPE] parent_id={pid} 解析出 0 条问题 "
                        f"(第 {count}/{cls._max_attempts} 次)，原始返回: {repr((raw or '')[:200])}"
                    )
                    fused_id = pid if (count >= cls._max_attempts or is_model_refusal) else None
                    return [], local_rules, fused_id

                cls._failed_attempts.pop(fuse_key, None)
                return _build_hype_dicts(parent, questions), local_rules, None

        async def _handle_pack(
            pack: list[dict[str, Any]],
        ) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]]:
            if len(pack) == 1:
                return [await _handle_one(pack[0])]

            while ForegroundChatTracker.is_active():
                await asyncio.sleep(1.5)

            pack_rules: list[list[dict[str, Any]]] = []
            for p in pack:
                r_items: list[dict[str, Any]] = []
                try:
                    ext = UniversalRuleExtractor.extract_rules_from_parent_chunk(
                        chunk=p, kb_id=kb_id, profile=profile,
                    )
                    if ext:
                        r_items.extend(ext)
                except Exception:
                    pass
                pack_rules.append(r_items)

            async with cls._get_concurrency_sem(is_cloud=is_cloud):
                batch_parsed, _ = await cls._generate_batch_questions(
                    [str(p.get("content") or "") for p in pack],
                    accounts=pool_accounts,
                )

            if batch_parsed:
                out_list: list[tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]] = []
                missing_indices: list[int] = []
                for idx, p in enumerate(pack):
                    qs = batch_parsed.get(idx)
                    if qs:
                        cls._failed_attempts.pop((kb_id, version, p["id"]), None)
                        out_list.append((_build_hype_dicts(p, qs), pack_rules[idx], None))
                    else:
                        missing_indices.append(idx)
                # 仅对漏解析的个别切片降级单片兜底
                for m_idx in missing_indices:
                    res_single = await _handle_one(pack[m_idx])
                    out_list.append((res_single[0], pack_rules[m_idx], res_single[2]))
                return out_list

            # 整包 JSON 失败时降级为单片并发处理
            return list(await asyncio.gather(*[_handle_one(p) for p in pack]))

        packs = [
            active_parents[i : i + cls.LLM_PACK_SIZE]
            for i in range(0, len(active_parents), cls.LLM_PACK_SIZE)
        ]
        packed_results = await asyncio.gather(*[_handle_pack(pk) for pk in packs])
        results = [item for sub in packed_results for item in sub]

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

        # 5. 批量持久化结构化规则
        if rules_to_insert:
            saved_r_count = StructuredRuleExtractor.batch_save_rules(db_path=db_path, rules=rules_to_insert)
            logger.info(f"[HyPE] 成功抽取并写入 {saved_r_count} 条刚性业务规则卡片 (kb_id={kb_id})")

        if not hype_chunks_to_insert:
            return reused_count + len(prefilter_skip_ids) + len(fused_parent_ids)

        # 6. 攒批 32 条批量计算 1024 维向量
        texts = [c["content"] for c in hype_chunks_to_insert]
        embeddings = await cls._encode_texts_in_batches(provider, texts, batch_size=cls.EMBEDDING_BATCH_SIZE)

        # 写入数据库
        now = time.time()

        def _write_hype_records():
            conn = sqlite3.connect(str(db_path))
            try:
                # 检查是否存在 provenance_json 字段
                cols = [col[1] for col in conn.execute("PRAGMA table_info(hybrid_chunks)").fetchall()]
                has_prov = "provenance_json" in cols

                with conn:
                    from ..kb.embedding_codec import encode_embedding_f16
                    for idx, c in enumerate(hype_chunks_to_insert):
                        blob_data = None
                        if embeddings is not None and idx < len(embeddings):
                            blob_data = encode_embedding_f16(embeddings[idx])

                        if has_prov:
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO hybrid_chunks (
                                    id, remote_uri, title, content, authority_tier, mtime, etag, embedding_blob,
                                    doc_year, effective_date, effective_ts, doc_priority_type, topic_cluster_id,
                                    is_latest_in_cluster, chunk_role, parent_chunk_id, section_path, created_at,
                                    provenance_json
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                                    c.get("provenance_json", ""),
                                ),
                            )
                        else:
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
                    count = await cls.process_kb_hype(kb_id, version, batch_limit=48)
                    if count == 0:
                        break
                    await asyncio.sleep(0.3)

                # 后台增强完毕后，自动增量编纂高频核心实体进 Wiki Inbox（带 source_hash 幂等保护，不自动发布）
                try:
                    from ..wiki.entity_wiki_compiler import EntityWikiCompiler
                    await EntityWikiCompiler.compile_kb_entities_to_inbox(kb_id=kb_id)
                except Exception as wiki_err:
                    logger.debug(f"[HyPE] 自动触发 EntityWiki 编纂跳过: {wiki_err}")
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
                # 统计可进行口语问答提炼的目标切片总数（parent + 独立 child）
                cur.execute(
                    "SELECT count(*) FROM hybrid_chunks WHERE (chunk_role = 'parent' OR (chunk_role = 'child' AND (parent_chunk_id IS NULL OR parent_chunk_id = '')))"
                )
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
                        WHERE (chunk_role = 'parent' OR (chunk_role = 'child' AND (parent_chunk_id IS NULL OR parent_chunk_id = '')))
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

