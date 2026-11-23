"""
用户对话 Wiki 记忆提炼与增量向量化训练流水线（Phase 4）。
将 dialogue_wiki_records 中尚未训练的真实 IM 对话记录：
1. 提炼结构化记忆摘要写入 extracted_memory；
2. 生成向量（优先调用本地/在线 Embedding 模型并压缩为 float16，无模型时降级纯 FTS5 索引）；
3. 写入该专家专属的独立对话知识库（kb_dialogue_{expert_id}），并更新 vector_status='trained' 与 memory_count。
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from ..core.logging_config import logger
from ..kb.embedding_codec import encode_embedding_f16
from ..kb.storage import KBStorageManager
from ..repository.db import db_manager


def _extract_memory_summary(sender_name: str, user_msg: str, assistant_reply: str) -> str:
    """从单轮问答提炼紧凑、可检索的结构化对话记忆条目。"""
    q_clean = " ".join((user_msg or "").split())[:140]
    a_clean = " ".join((assistant_reply or "").split())[:260]
    return f"[{sender_name}] 问：{q_clean} → 结论：{a_clean}"


async def train_dialogue_records_for_link(link_id: str) -> dict[str, Any]:
    """执行指定渠道链接历史对话的记忆提炼与增量向量化训练。"""
    async with db_manager.get_connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM dialogue_channel_links WHERE link_id = ? LIMIT 1",
            (link_id,),
        )
        link_row = await cur.fetchone()
        if not link_row:
            raise ValueError("未找到对应的渠道对接链接")
        link = dict(link_row)

        rec_cur = await conn.execute(
            """
            SELECT * FROM dialogue_wiki_records
            WHERE link_id = ? AND COALESCE(vector_status, 'pending') != 'trained'
            ORDER BY created_at ASC
            LIMIT 200
            """,
            (link_id,),
        )
        pending_rows = [dict(r) for r in await rec_cur.fetchall()]

    now_ts = time.time()
    if not pending_rows:
        async with db_manager.get_connection() as conn:
            await conn.execute(
                "UPDATE dialogue_channel_links SET last_trained_at = ? WHERE link_id = ?",
                (now_ts, link_id),
            )
            await conn.commit()
        return {
            "status": "up_to_date",
            "trained_count": 0,
            "kb_id": f"kb_dialogue_{link['expert_id']}",
            "message": "当前渠道链接暂无新增待训练对话记录。",
            "last_trained_at": now_ts,
        }

    # 1. 提炼结构化记忆文本
    memories: list[str] = []
    for r in pending_rows:
        mem = _extract_memory_summary(
            str(r.get("sender_name") or r.get("sender_id") or "用户"),
            str(r.get("user_message") or ""),
            str(r.get("assistant_reply") or ""),
        )
        memories.append(mem)

    # 2. 尝试计算 float16 语义向量（若未配置在线/本地 Embedding 则优雅降级为纯 FTS5 索引）
    blobs: list[bytes | None] = [None] * len(memories)
    try:
        from ..search.embedding_router import embedding_router

        provider = await embedding_router.get_provider()
        if provider and provider.is_available:
            vecs = await provider.encode_batch(memories)
            if vecs is not None:
                for idx, v in enumerate(vecs):
                    if idx < len(blobs):
                        blobs[idx] = encode_embedding_f16(v)
    except Exception as emb_err:
        logger.debug(f"[DialogueTrainer] 向量计算降级为 FTS5 索引: {emb_err}")

    # 3. 写入专家专属对话知识库 index.db
    kb_id = f"kb_dialogue_{link['expert_id']}"
    chunks_to_write: list[dict[str, Any]] = []
    for idx, r in enumerate(pending_rows):
        rec_id = str(r["record_id"])
        mem_text = memories[idx]
        chunk_id = f"dlg_{hashlib.md5(rec_id.encode('utf-8')).hexdigest()[:16]}"
        chunks_to_write.append(
            {
                "chunk_id": chunk_id,
                "remote_uri": f"dialogue://{link['channel_type']}/{link_id}/{rec_id}",
                "title": f"对话记忆 · {link.get('target_user_label') or link_id}",
                "content": mem_text,
                "authority_tier": 2,
                "mtime": float(r.get("created_at") or now_ts),
                "embedding_blob": blobs[idx],
                "chunk_role": "child",
                "section_path": f"用户对话/{link['channel_type']}/{r.get('sender_name') or '用户'}",
            }
        )

    KBStorageManager.upsert_chunks_to_version_db(
        kb_id=kb_id,
        version="v1.0.0",
        chunks=chunks_to_write,
    )

    # 4. 更新对话记录状态与链接统计计数
    async with db_manager.get_connection() as conn:
        for idx, r in enumerate(pending_rows):
            await conn.execute(
                """
                UPDATE dialogue_wiki_records
                SET extracted_memory = ?, vector_status = 'trained'
                WHERE record_id = ?
                """,
                (memories[idx], r["record_id"]),
            )
        await conn.execute(
            """
            UPDATE dialogue_channel_links
            SET memory_count = memory_count + ?, last_trained_at = ?
            WHERE link_id = ?
            """,
            (len(pending_rows), now_ts, link_id),
        )
        await conn.commit()

    vectorized_cnt = sum(1 for b in blobs if b is not None)
    return {
        "status": "trained",
        "trained_count": len(pending_rows),
        "vectorized_count": vectorized_cnt,
        "kb_id": kb_id,
        "message": f"已提炼 {len(pending_rows)} 条对话记忆并写入专属对话知识库 {kb_id}（向量化 {vectorized_cnt} 条）。",
        "last_trained_at": now_ts,
    }

