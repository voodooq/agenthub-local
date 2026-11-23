"""
SQLite 混合检索引擎 (Hybrid Search Engine)
核心功能：
1. 原生 SQLite WAL 存储切片元数据与 BLOB 向量；
2. NumPy 矩阵点积余弦相似度召回 + FTS5 trigram 全文召回；
3. RRF (Reciprocal Rank Fusion) 倒数排名融合；
4. 动态 Query 意图权重路由（代码符号查询 vs 自然语言查询）；
5. Pod 隔离标识 (pod_id / org_entity_id) 支持；
6. 低优先级后台异步增量向量化队列。
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger
from .embedding_provider import (
    BaseEmbeddingProvider,
    embedding_router,
    normalize_l2,
    HAS_NUMPY,
    np,
)
from .conflict_resolver import ConflictResolver


class HybridSearchEngine:
    """工作区混合检索引擎。"""

    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root).resolve()
        self.db_dir = self.workspace_root / ".agenthub"
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.db_dir / "index.db"
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 60000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self) -> None:
        """初始化混合切片表、FTS5 虚表、图谱表与云盘水位线表。"""
        with closing(self._get_connection()) as conn:
            with conn:
                # 1. 混合检索切片表
                conn.execute("""
                CREATE TABLE IF NOT EXISTS hybrid_chunks (
                    chunk_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type      TEXT NOT NULL DEFAULT 'local',
                    pod_id           TEXT NOT NULL DEFAULT 'global',
                    org_entity_id    TEXT NOT NULL DEFAULT '',
                    remote_uri       TEXT NOT NULL,
                    title            TEXT NOT NULL,
                    section_path     TEXT DEFAULT '',
                    start_line       INTEGER DEFAULT 0,
                    end_line         INTEGER DEFAULT 0,
                    content_text     TEXT NOT NULL,
                    content_hash     TEXT NOT NULL,
                    topic_cluster_id TEXT NOT NULL,
                    authority_tier   INTEGER NOT NULL DEFAULT 2,
                    effective_time   REAL NOT NULL,
                    is_superseded    INTEGER NOT NULL DEFAULT 0,
                    model_id         TEXT NOT NULL,
                    embedding_blob   BLOB,
                    updated_at       REAL NOT NULL,
                    UNIQUE(pod_id, remote_uri, section_path, start_line)
                );
                """)

                conn.execute("CREATE INDEX IF NOT EXISTS idx_hybrid_chunks_pod_topic ON hybrid_chunks(pod_id, topic_cluster_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hybrid_chunks_hash ON hybrid_chunks(content_hash);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hybrid_chunks_source ON hybrid_chunks(source_type, pod_id);")

                # 2. FTS5 全文索引虚表
                conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS hybrid_chunks_fts USING fts5(
                    title,
                    section_path,
                    content_text,
                    content='hybrid_chunks',
                    content_rowid='chunk_id',
                    tokenize='trigram'
                );
                """)

                # 3. 虚表同步触发器
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_hybrid_chunks_ai AFTER INSERT ON hybrid_chunks BEGIN
                    INSERT INTO hybrid_chunks_fts(rowid, title, section_path, content_text)
                    VALUES (new.chunk_id, new.title, new.section_path, new.content_text);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_hybrid_chunks_ad AFTER DELETE ON hybrid_chunks BEGIN
                    INSERT INTO hybrid_chunks_fts(hybrid_chunks_fts, rowid, title, section_path, content_text)
                    VALUES ('delete', old.chunk_id, old.title, old.section_path, old.content_text);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_hybrid_chunks_au AFTER UPDATE ON hybrid_chunks BEGIN
                    INSERT INTO hybrid_chunks_fts(hybrid_chunks_fts, rowid, title, section_path, content_text)
                    VALUES ('delete', old.chunk_id, old.title, old.section_path, old.content_text);
                    INSERT INTO hybrid_chunks_fts(rowid, title, section_path, content_text)
                    VALUES (new.chunk_id, new.title, new.section_path, new.content_text);
                END;
                """)

                # 4. 知识图谱三元组表
                conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_triples (
                    triple_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    pod_id           TEXT NOT NULL DEFAULT 'global',
                    subject          TEXT NOT NULL,
                    predicate        TEXT NOT NULL,
                    object           TEXT NOT NULL,
                    source_uri       TEXT NOT NULL,
                    authority_tier   INTEGER NOT NULL DEFAULT 2,
                    effective_time   REAL NOT NULL,
                    confidence       REAL NOT NULL DEFAULT 1.0,
                    UNIQUE(pod_id, subject, predicate, object, source_uri)
                );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_triples_sub ON knowledge_triples(pod_id, subject);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_triples_obj ON knowledge_triples(pod_id, object);")

                # 5. 云端虚拟文件系统增量水位线表
                conn.execute("""
                CREATE TABLE IF NOT EXISTS cloud_vfs_sync_state (
                    mount_id         TEXT NOT NULL,
                    remote_uri       TEXT NOT NULL,
                    etag_or_sha      TEXT NOT NULL,
                    remote_mtime     REAL NOT NULL,
                    authority_tier   INTEGER NOT NULL DEFAULT 2,
                    last_synced_at   REAL NOT NULL,
                    PRIMARY KEY (mount_id, remote_uri)
                );
                """)

    @staticmethod
    def compute_sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_topic_cluster_id(filename_or_title: str) -> str:
        """剥离文件名中版本号与日期后缀，提取标准化主题指纹。"""
        name = Path(filename_or_title).stem.lower()
        # 移除 v1.0, _v2, _最终版, _修订版, 2026-11-10, (1) 等
        name = re.sub(r"[_\-\.]?v?\d+(\.\d+)*", "", name)
        name = re.sub(r"\d{4}[年\-_]\d{1,2}[月\-_]\d{1,2}(日)?", "", name)
        name = re.sub(r"[\(_（]?(最终版|修订版|草稿|初稿|定稿|备份|copy)[\)）]?", "", name)
        name = re.sub(r"\s+", "_", name).strip("_-")
        return name or "general_topic"

    async def upsert_chunks(
        self,
        chunks: list[dict[str, Any]],
        provider: BaseEmbeddingProvider | None = None,
    ) -> int:
        """批量写入或更新切片。

        Args:
            chunks: 切片字典列表。
            provider: 指定 Embedding 提供商，为 None 时自动获取。

        Returns:
            int: 成功入库的切片数。
        """
        if not chunks:
            return 0

        if provider is None:
            provider = await embedding_router.get_provider()

        texts_to_embed = [c["content_text"] for c in chunks]
        embeddings: Any | None = None

        if provider.is_available and provider.dimension > 0:
            try:
                embeddings = await provider.encode_batch(texts_to_embed)
                if embeddings is None:
                    embeddings = await provider.embed_texts(texts_to_embed)
            except Exception as e:
                logger.warning(f"切片批量向量化失败，降级为 NULL blob: {e}")
                embeddings = None

        now = time.time()
        inserted = 0

        with closing(self._get_connection()) as conn:
            with conn:
                for idx, c in enumerate(chunks):
                    text = c["content_text"]
                    c_hash = c.get("content_hash") or self.compute_sha256(text)
                    t_cluster = c.get("topic_cluster_id") or self.normalize_topic_cluster_id(c["title"])
                    pod_id = c.get("pod_id", "global")
                    org_id = c.get("org_entity_id", "")
                    s_type = c.get("source_type", "local")
                    remote_uri = c["remote_uri"]
                    title = c["title"]
                    sec_path = c.get("section_path", "")
                    start_l = c.get("start_line", 0)
                    end_l = c.get("end_line", 0)
                    auth_tier = c.get("authority_tier") or ConflictResolver.resolve_authority_tier(remote_uri)
                    eff_time = c.get("effective_time") or ConflictResolver.extract_effective_time(text, title, fallback_mtime=now)
                    is_sup = c.get("is_superseded", 0)

                    blob_data = None
                    if embeddings is not None and idx < len(embeddings):
                        vec = embeddings[idx]
                        if HAS_NUMPY and np is not None and hasattr(vec, "astype"):
                            blob_data = vec.astype(np.float32).tobytes()
                        else:
                            import array
                            blob_data = array.array("f", (float(x) for x in vec)).tobytes()

                    conn.execute("""
                    INSERT INTO hybrid_chunks (
                        source_type, pod_id, org_entity_id, remote_uri, title,
                        section_path, start_line, end_line, content_text, content_hash,
                        topic_cluster_id, authority_tier, effective_time, is_superseded,
                        model_id, embedding_blob, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pod_id, remote_uri, section_path, start_line) DO UPDATE SET
                        content_text = excluded.content_text,
                        content_hash = excluded.content_hash,
                        topic_cluster_id = excluded.topic_cluster_id,
                        authority_tier = excluded.authority_tier,
                        effective_time = excluded.effective_time,
                        is_superseded = excluded.is_superseded,
                        model_id = excluded.model_id,
                        embedding_blob = excluded.embedding_blob,
                        updated_at = excluded.updated_at;
                    """, (
                        s_type, pod_id, org_id, remote_uri, title,
                        sec_path, start_l, end_l, text, c_hash,
                        t_cluster, auth_tier, eff_time, is_sup,
                        provider.model_id, blob_data, now
                    ))
                    inserted += 1

                # 自动触发同主题新旧版本替代链标记
                affected_topics = {c.get("topic_cluster_id") or self.normalize_topic_cluster_id(c["title"]) for c in chunks}
                for t in affected_topics:
                    ConflictResolver.mark_superseded_in_db(conn, chunks[0].get("pod_id", "global"), t)

        return inserted

    @staticmethod
    def route_query_intent(query: str) -> tuple[float, float]:
        """动态意图权重路由：判定 Query 是代码符号查询还是自然语言语义查询。

        Returns:
            tuple[float, float]: (w_fts, w_vec)
        """
        # 代码符号特征：下划线、点号调用、驼峰、括号、路径斜杠、特殊符号
        code_symbol_pattern = re.compile(r"[_/\\\.::\(\)<>\{\}\[\]`\$#]")
        has_symbols = bool(code_symbol_pattern.search(query))
        has_camel_case = bool(re.search(r"[a-z][A-Z]", query))

        # 自然语言意图特征词
        nl_question_words = ["如何", "怎么", "为什么", "介绍", "总结", "区别", "方案", "步骤", "规范", "规程", "费用", "要求"]
        is_nl_question = any(w in query for w in nl_question_words) or ("?" in query or "？" in query)

        if (has_symbols or has_camel_case) and not is_nl_question:
            # 偏重词法与精确符号定位
            return (0.7, 0.3)
        elif is_nl_question:
            # 偏重语义泛化理解
            return (0.3, 0.7)
        return (0.5, 0.5)

    async def search(
        self,
        query: str,
        top_k: int = 10,
        pod_id: str = "global",
        source_type: str | None = None,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        """执行混合检索（向量点积 + FTS5 trigram + RRF 融合排序）。

        Args:
            query: 搜索文本。
            top_k: 返回数量上限。
            pod_id: 隔离域。
            source_type: 过滤来源类型。
            include_superseded: 是否包含已过时版本。

        Returns:
            list[dict[str, Any]]: 融合排序后的切片列表。
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        provider = await embedding_router.get_provider()
        w_fts, w_vec = self.route_query_intent(clean_query)

        if not provider.is_available or provider.dimension == 0:
            w_fts = 1.0
            w_vec = 0.0

        vec_ranks: dict[int, int] = {}  # chunk_id -> rank (1-based)
        vec_scores: dict[int, float] = {}
        fts_ranks: dict[int, int] = {}  # chunk_id -> rank (1-based)
        chunk_cache: dict[int, dict[str, Any]] = {}

        # 1. 向量相似度召回 (优先 NumPy 矩阵加速，无 NumPy 自动走原生零依赖点积)
        if w_vec > 0.0:
            try:
                q_vec = await provider.encode_query(clean_query)
                q_len = len(q_vec) if q_vec is not None else 0
                if q_vec is not None and q_len > 0:
                    with closing(self._get_connection()) as conn:
                        sql = """
                        SELECT chunk_id, embedding_blob, authority_tier, effective_time, is_superseded,
                               topic_cluster_id, source_type, pod_id, remote_uri, title, section_path, start_line, end_line, content_text
                        FROM hybrid_chunks
                        WHERE pod_id = ? AND embedding_blob IS NOT NULL
                        """
                        params: list[Any] = [pod_id]
                        if source_type:
                            sql += " AND source_type = ?"
                            params.append(source_type)
                        if not include_superseded:
                            sql += " AND is_superseded = 0"

                        rows = conn.execute(sql, params).fetchall()

                    if rows:
                        import array
                        import heapq

                        chunk_ids: list[int] = []
                        valid_vectors: list[Any] = []
                        q_list: list[float] = [float(x) for x in q_vec]

                        from ..kb.embedding_codec import decode_embedding
                        for r in rows:
                            cid = r["chunk_id"]
                            chunk_cache[cid] = dict(r)
                            blob = r["embedding_blob"]
                            if blob:
                                arr = decode_embedding(blob, q_len)
                                if arr is not None and len(arr) == q_len:
                                    chunk_ids.append(cid)
                                    valid_vectors.append(arr)

                        if valid_vectors:
                            # 1.1 若存在 NumPy，优先尝试进行矩阵加速计算
                            if HAS_NUMPY and np is not None:
                                try:
                                    matrix = np.array(valid_vectors, dtype=np.float32)
                                    scores = matrix @ np.array(q_list, dtype=np.float32)
                                    top_indices = np.argsort(-scores)[: top_k * 3]
                                    for rank_idx, idx_val in enumerate(top_indices):
                                        cid = chunk_ids[idx_val]
                                        vec_ranks[cid] = rank_idx + 1
                                        vec_scores[cid] = float(scores[idx_val])
                                except Exception:
                                    vec_ranks.clear()
                                    vec_scores.clear()

                            # 1.2 若无 NumPy 或 NumPy 计算异常，使用 Python 原生点积与堆排序
                            if not vec_ranks:
                                scores_list = [
                                    sum(a * b for a, b in zip(v, q_list))
                                    for v in valid_vectors
                                ]
                                top_indices_heap = heapq.nlargest(
                                    min(top_k * 3, len(scores_list)),
                                    range(len(scores_list)),
                                    key=lambda i: scores_list[i],
                                )
                                for rank_idx, idx_val in enumerate(top_indices_heap):
                                    cid = chunk_ids[idx_val]
                                    vec_ranks[cid] = rank_idx + 1
                                    vec_scores[cid] = float(scores_list[idx_val])
            except Exception as e:
                logger.warning(f"向量召回异常，自动降级为全词法: {e}")
                w_fts = 1.0
                w_vec = 0.0

        # 2. FTS5 全文词法召回
        if w_fts > 0.0:
            with closing(self._get_connection()) as conn:
                # 清洗 FTS5 query 字符
                fts_query = re.sub(r"[^\w\u4e00-\u9fa5\s]", " ", clean_query).strip()
                tokens = [t for t in fts_query.split() if t]
                if tokens:
                    # 拆分子词，若中文短语较长拆出 3-gram 增强模糊召回
                    expanded_tokens: list[str] = []
                    for t in tokens:
                        expanded_tokens.append(t)
                        if len(t) >= 4 and any('\u4e00' <= char <= '\u9fa5' for char in t):
                            for i in range(len(t) - 2):
                                expanded_tokens.append(t[i:i+3])

                    sql = """
                    SELECT c.chunk_id, c.authority_tier, c.effective_time, c.is_superseded,
                           c.topic_cluster_id, c.source_type, c.pod_id, c.remote_uri, c.title, c.section_path,
                           c.start_line, c.end_line, c.content_text, bm25(hybrid_chunks_fts) as rank_score
                    FROM hybrid_chunks_fts
                    JOIN hybrid_chunks c ON c.chunk_id = hybrid_chunks_fts.rowid
                    WHERE hybrid_chunks_fts MATCH ? AND c.pod_id = ?
                    """
                    where_tail = ""
                    params_base: list[Any] = [pod_id]
                    if source_type:
                        where_tail += " AND c.source_type = ?"
                        params_base.append(source_type)
                    if not include_superseded:
                        where_tail += " AND c.is_superseded = 0"
                    where_tail += f" ORDER BY rank_score ASC LIMIT {top_k * 3}"

                    # 优先精确 AND 匹配
                    formatted_and = " AND ".join(tokens)
                    try:
                        fts_rows = conn.execute(sql.replace("AND c.pod_id = ?", f"AND c.pod_id = ?{where_tail}"), [formatted_and] + params_base).fetchall()
                    except Exception:
                        fts_rows = []

                    # 若精确 AND 未命中，使用 OR 模糊泛化召回
                    if not fts_rows:
                        formatted_or = " OR ".join(expanded_tokens[:10])
                        try:
                            fts_rows = conn.execute(sql.replace("AND c.pod_id = ?", f"AND c.pod_id = ?{where_tail}"), [formatted_or] + params_base).fetchall()
                        except Exception:
                            fts_rows = []

                    for rank_idx, r in enumerate(fts_rows):
                        cid = r["chunk_id"]
                        fts_ranks[cid] = rank_idx + 1
                        if cid not in chunk_cache:
                            chunk_cache[cid] = dict(r)

        # 3. RRF (Reciprocal Rank Fusion) 融合计算
        all_cids = set(vec_ranks.keys()) | set(fts_ranks.keys())
        if not all_cids:
            return []

        rrf_scored_items: list[tuple[float, dict[str, Any]]] = []
        k_const = 60.0

        for cid in all_cids:
            item = chunk_cache.get(cid)
            if not item:
                continue

            r_vec = vec_ranks.get(cid, 999999)
            r_fts = fts_ranks.get(cid, 999999)

            score_vec = (w_vec / (k_const + r_vec)) if r_vec < 999999 else 0.0
            score_fts = (w_fts / (k_const + r_fts)) if r_fts < 999999 else 0.0
            base_rrf = score_vec + score_fts

            # 融合基础分
            item_out = dict(item)
            item_out["rrf_score"] = base_rrf
            item_out["cosine_similarity"] = vec_scores.get(cid, 0.0)
            item_out["vec_rank"] = r_vec if r_vec < 999999 else None
            item_out["fts_rank"] = r_fts if r_fts < 999999 else None

            # 阶段二：叠加权威权重与时序新鲜度因子
            arbitrated = ConflictResolver.compute_arbitrated_score(item_out)
            item_out["arbitrated_score"] = arbitrated

            # 排除 embedding_blob 减少传输消耗
            item_out.pop("embedding_blob", None)

            rrf_scored_items.append((arbitrated, item_out))

        # 阶段二点五：微经验 (L5 Experience) 双路召回融合 (v1.5.5)
        try:
            from ..memory.experience_store import episodic_experience_store
            exp_cases = await episodic_experience_store.search_for_context(
                clean_query,
                workspace_id=pod_id if pod_id != "global" else "GLOBAL",
                limit=2,
            )
            for c in exp_cases:
                exp_score = 0.85 * float(c.confidence)
                rrf_scored_items.append((
                    exp_score,
                    {
                        "chunk_id": f"exp_{c.id}",
                        "title": f"实战微经验: {c.scene_summary[:30]}",
                        "section_path": c.task_category,
                        "source_type": "experience",
                        "authority_tier": 2,
                        "effective_time": c.updated_at or c.created_at,
                        "is_superseded": 0,
                        "content_text": f"【场景】{c.scene_summary}\n【根因】{c.root_cause}\n【方案】{c.verified_solution}",
                        "remote_uri": f"experience://{c.id}",
                        "rrf_score": exp_score,
                        "arbitrated_score": exp_score,
                    }
                ))
        except Exception as exp_err:
            logger.debug(f"微经验召回跳过: {exp_err}")

        rrf_scored_items.sort(key=lambda x: x[0], reverse=True)
        top_results = [it[1] for it in rrf_scored_items[:top_k]]

        # 生成时空身份证包裹
        for idx, res in enumerate(top_results, 1):
            res["spatial_temporal_text"] = ConflictResolver.format_spatial_temporal_id(res, idx)

        return top_results

    def get_knowledge_stats(self) -> dict[str, int]:
        """查询底层知识切片、图谱三元组与云盘同步状态指标。"""
        with closing(self._get_connection()) as conn:
            try:
                chunk_cnt = conn.execute("SELECT COUNT(*) as c FROM hybrid_chunks").fetchone()["c"]
            except Exception:
                chunk_cnt = 0
            try:
                triple_cnt = conn.execute("SELECT COUNT(*) as c FROM knowledge_triples").fetchone()["c"]
            except Exception:
                triple_cnt = 0
            try:
                cloud_cnt = conn.execute("SELECT COUNT(DISTINCT mount_id) as c FROM cloud_vfs_sync_state").fetchone()["c"]
            except Exception:
                cloud_cnt = 0

        return {
            "total_chunks": int(chunk_cnt),
            "total_triples": int(triple_cnt),
            "synced_cloud_mounts": int(cloud_cnt),
        }
