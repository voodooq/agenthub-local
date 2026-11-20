"""
RAPTOR 分层摘要树构建器 (Hierarchical Summary Builder)
核心功能：
1. 将文档父切片按一级 section_path 聚合提炼 200~300 字结构化章节要点 (summary_l1)；
2. 汇总全篇 summary_l1 提炼 500 字“一页纸总览” (summary_l2)，包含文档全称、生效日期、核心变更点与关键费用/时间；
3. 全程通过 BackgroundEnrichmentQueue + LightLLMRouter 执行，物理屏蔽主力模型。
"""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..core.logging_config import logger
from ..kb.storage import KBStorageManager
from ..providers.light_llm_router import light_llm_router
from ..service.enrichment_queue import BackgroundEnrichmentQueue
from .embedding_provider import HAS_NUMPY, embedding_router, np

_L1_SYSTEM_PROMPT = (
    "你是企业规程摘要助手。请根据提供的章节原文，提炼 200~300 字的结构化章节要点。"
    "必须保留具体数字、年龄/组别限制、金额、截止时间与办理条件，不要废话。"
)

_L2_SYSTEM_PROMPT = (
    "你是文档宏观画像提炼专家。请汇总下方各章节要点，生成不超过 500 字的“一页纸总览”。"
    "必须明确包含：1) 文档全称与适用范围；2) 生效日期或年份版本；3) 核心变更点或核心规则；4) 关键费用标准与时间节点。"
)


def _encode_blob(vec: Any) -> bytes:
    """将单条向量编码为 float32 二进制流。"""
    if HAS_NUMPY and np is not None and hasattr(vec, "astype"):
        return vec.astype(np.float32).tobytes()
    import array
    return array.array("f", (float(x) for x in vec)).tobytes()


class HierarchicalSummaryBuilder:
    """RAPTOR L1/L2 分层摘要树异步构建器。"""

    @classmethod
    async def build_summaries_for_version(
        cls,
        kb_id: str,
        version: str = "v1.0.0",
    ) -> dict[str, int]:
        """为指定知识库版本构建 summary_l1 与 summary_l2 摘要节点。"""
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        if not db_path.exists():
            return {"l1_created": 0, "l2_created": 0}

        pool_accounts = await light_llm_router.fetch_pool_accounts()
        if light_llm_router.resolve_target(pool_accounts) is None:
            logger.debug("[RAPTOR] 当前算力池无可用轻量模型节点，跳过分层摘要构建")
            return {"l1_created": 0, "l2_created": 0}

        def _load_parents_and_existing() -> tuple[list[dict[str, Any]], set[str]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                parents = [
                    dict(r)
                    for r in conn.execute(
                        """
                        SELECT id, remote_uri, title, content, authority_tier, mtime, etag,
                               doc_year, effective_date, effective_ts, doc_priority_type,
                               topic_cluster_id, is_latest_in_cluster, section_path
                        FROM hybrid_chunks
                        WHERE (chunk_role = 'parent' OR (chunk_role = 'child' AND (parent_chunk_id IS NULL OR parent_chunk_id = '')))
                        ORDER BY remote_uri ASC, id ASC
                        """
                    ).fetchall()
                ]
                existing_ids = {
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM hybrid_chunks WHERE chunk_role IN ('summary_l1', 'summary_l2')"
                    ).fetchall()
                }
                return parents, existing_ids
            finally:
                conn.close()

        parents, existing_ids = await asyncio.to_thread(_load_parents_and_existing)
        if not parents:
            return {"l1_created": 0, "l2_created": 0}

        # 按文档 (remote_uri) -> 一级标题 (section_root) 分组
        docs_map: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for p in parents:
            uri = str(p.get("remote_uri") or p.get("title") or "default_doc")
            sec_raw = str(p.get("section_path") or "").strip()
            sec_l1 = sec_raw.split("/")[0].strip() if sec_raw else (p.get("title") or "正文")
            docs_map[uri][sec_l1].append(p)

        chunks_to_upsert: list[dict[str, Any]] = []
        l1_created = 0
        l2_created = 0

        for uri, sec_groups in docs_map.items():
            doc_l1_texts: list[str] = []
            sample_meta = next(iter(next(iter(sec_groups.values()))))
            doc_title = str(sample_meta.get("title") or Path(uri).name)

            for sec_name, sec_chunks in sec_groups.items():
                l1_id = f"raptor_l1_{hashlib.md5(f'{uri}:{sec_name}'.encode('utf-8')).hexdigest()[:16]}"
                combined_sec = "\n\n".join(c.get("content", "") for c in sec_chunks)[:3500]
                if not combined_sec.strip():
                    continue

                if l1_id in existing_ids:
                    doc_l1_texts.append(f"【{sec_name}】\n{combined_sec[:260]}")
                    continue

                await BackgroundEnrichmentQueue.wait_for_foreground_idle()
                l1_summary = await light_llm_router.complete(
                    prompt=f"文档名称: {doc_title}\n章节: {sec_name}\n\n章节原文:\n{combined_sec}",
                    accounts=pool_accounts,
                    system_prompt=_L1_SYSTEM_PROMPT,
                    temperature=0.2,
                    max_tokens=512,
                )
                if not l1_summary or not l1_summary.strip():
                    continue

                doc_l1_texts.append(f"【{sec_name}】\n{l1_summary.strip()}")
                chunks_to_upsert.append({
                    "id": l1_id,
                    "remote_uri": uri,
                    "title": f"{doc_title} - {sec_name} (章节要点)",
                    "content": l1_summary.strip(),
                    "authority_tier": sample_meta.get("authority_tier", 1),
                    "mtime": sample_meta.get("mtime", 0),
                    "etag": sample_meta.get("etag", ""),
                    "doc_year": sample_meta.get("doc_year", 0),
                    "effective_date": sample_meta.get("effective_date", ""),
                    "effective_ts": sample_meta.get("effective_ts", 0),
                    "doc_priority_type": sample_meta.get("doc_priority_type", "normal"),
                    "topic_cluster_id": sample_meta.get("topic_cluster_id", ""),
                    "is_latest_in_cluster": sample_meta.get("is_latest_in_cluster", 1),
                    "chunk_role": "summary_l1",
                    "parent_chunk_id": l1_id,
                    "section_path": sec_name,
                    "skip_hype": 1,
                })
                l1_created += 1

            # 生成全篇一页纸画像 (summary_l2)
            l2_id = f"raptor_l2_{hashlib.md5(uri.encode('utf-8')).hexdigest()[:16]}"
            if doc_l1_texts and l2_id not in existing_ids:
                await BackgroundEnrichmentQueue.wait_for_foreground_idle()
                eff_hint = sample_meta.get("effective_date") or str(sample_meta.get("doc_year") or "")
                l2_summary = await light_llm_router.complete(
                    prompt=(
                        f"文档全称: {doc_title}\n生效日期/年份: {eff_hint or '未知'}\n\n"
                        f"各章节要点汇总:\n" + "\n\n".join(doc_l1_texts)[:4000]
                    ),
                    accounts=pool_accounts,
                    system_prompt=_L2_SYSTEM_PROMPT,
                    temperature=0.2,
                    max_tokens=700,
                )
                if l2_summary and l2_summary.strip():
                    chunks_to_upsert.append({
                        "id": l2_id,
                        "remote_uri": uri,
                        "title": f"{doc_title} (全篇一页纸总览)",
                        "content": l2_summary.strip(),
                        "authority_tier": sample_meta.get("authority_tier", 1),
                        "mtime": sample_meta.get("mtime", 0),
                        "etag": sample_meta.get("etag", ""),
                        "doc_year": sample_meta.get("doc_year", 0),
                        "effective_date": sample_meta.get("effective_date", ""),
                        "effective_ts": sample_meta.get("effective_ts", 0),
                        "doc_priority_type": sample_meta.get("doc_priority_type", "normal"),
                        "topic_cluster_id": sample_meta.get("topic_cluster_id", ""),
                        "is_latest_in_cluster": sample_meta.get("is_latest_in_cluster", 1),
                        "chunk_role": "summary_l2",
                        "parent_chunk_id": l2_id,
                        "section_path": "全篇总览",
                        "skip_hype": 1,
                    })
                    l2_created += 1

        if not chunks_to_upsert:
            return {"l1_created": 0, "l2_created": 0}

        # 批量向量化并入库
        provider = await embedding_router.get_provider()
        if provider.is_available and provider.dimension > 0:
            try:
                texts = [c["content"] for c in chunks_to_upsert]
                vecs = await provider.encode_batch(texts)
                if vecs is None:
                    vecs = await provider.embed_texts(texts)
                if vecs is not None and len(vecs) == len(chunks_to_upsert):
                    for idx, c in enumerate(chunks_to_upsert):
                        c["embedding_blob"] = _encode_blob(vecs[idx])
            except Exception as emb_err:
                logger.warning(f"[RAPTOR] 批量向量化失败，降级为纯文本入库: {emb_err}")

        await asyncio.to_thread(
            KBStorageManager.upsert_chunks_to_version_db,
            kb_id=kb_id,
            version=version,
            chunks=chunks_to_upsert,
        )
        logger.info(
            f"[RAPTOR] 分层摘要树构建完成 (kb_id={kb_id}, ver={version}, L1={l1_created}, L2={l2_created})"
        )
        return {"l1_created": l1_created, "l2_created": l2_created}

    @classmethod
    def schedule_background_raptor(cls, kb_id: str, version: str = "v1.0.0") -> bool:
        """通过统一前台优先限流队列异步调度 RAPTOR 分层摘要构建。"""
        task_key = f"raptor:{kb_id}:{version}"
        return BackgroundEnrichmentQueue.enqueue(
            task_key,
            lambda: cls.build_summaries_for_version(kb_id=kb_id, version=version),
        )

