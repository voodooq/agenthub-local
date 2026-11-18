"""
AgentHub Local v1.5.8 多知识库隔离检索路由与门控中心 (Multi-KB Retrieval Router)
核心铁律：
1. 默认不盲搜门控 (Explicit Mount Gate): 只有当前专家或会话显式挂载了知识库，检索工具才激活；
2. 跨库物理隔离: 每个知识库严格访问自己独立的 index.db，绝不互相污染；
3. 库级优先级加权重排: W_priority = 1.15 - 0.05 * (rank - 1)。
"""
import asyncio
from pathlib import Path
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger
from ..kb.manager import KBLifecycleManager
from ..kb.storage import KBStorageManager
from .hybrid_engine import HybridSearchEngine


class MultiKBRetrievalRouter:
    """多知识库隔离检索与优先级路由调度器。"""

    @classmethod
    async def resolve_target_kbs(
        cls,
        expert_id: str | None = None,
        session_mounted_kbs: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """解析当前会话/专家真正生效的知识库清单与版本策略。"""
        # 1. 优先使用会话级临时挂载覆盖
        if session_mounted_kbs:
            kbs_all = await KBLifecycleManager.list_knowledge_bases()
            kb_map = {k["kb_id"]: k for k in kbs_all}
            targets = []
            for idx, kid in enumerate(session_mounted_kbs):
                if kid in kb_map:
                    k = kb_map[kid]
                    targets.append({
                        "kb_id": kid,
                        "name": k["name"],
                        "version": k["active_version"],
                        "priority_rank": idx + 1,
                    })
            if targets:
                return targets

        # 2. 其次使用专家绑定
        if expert_id:
            bound = await KBLifecycleManager.get_expert_kbs(expert_id)
            if bound:
                return [
                    {
                        "kb_id": b["kb_id"],
                        "name": b["name"],
                        "version": b["effective_version"],
                        "priority_rank": b["priority_rank"],
                    }
                    for b in bound
                ]

        return []

    @classmethod
    async def search_mounted_kbs(
        cls,
        query: str,
        expert_id: str | None = None,
        session_mounted_kbs: list[str] | None = None,
        top_k: int = 8,
        include_archived: bool = False,
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        """执行显式挂载知识库联合检索。

        Returns:
            (is_gated, formatted_text, raw_chunks)
        """
        targets = await cls.resolve_target_kbs(expert_id, session_mounted_kbs)

        # 铁律：未显式挂载任何知识库时，直接门控阻断，保持纯净通用上下文
        if not targets:
            return (
                False,
                "【知识库门控提示】当前会话或专家未挂载任何知识库，已保持纯净通用对话上下文（未执行盲搜）。",
                [],
            )

        all_candidates: list[dict[str, Any]] = []

        # 并行检索各个独立知识库 index.db
        for target in targets:
            kb_id = target["kb_id"]
            ver = target["version"]
            rank = target["priority_rank"]
            kb_name = target["name"]
            db_path = KBStorageManager.get_version_db_path(kb_id, ver)

            if not db_path.exists():
                logger.warning(f"知识库 [{kb_name}] 版本 [{ver}] 数据库文件不存在，跳过检索: {db_path}")
                continue

            try:
                # 优先级加权系数 W_priority = 1.15 - 0.05 * (rank - 1)
                priority_weight = max(0.8, 1.15 - 0.05 * (rank - 1))
                kb_chunks = await cls._search_single_index_db(
                    db_path=db_path,
                    query=query,
                    top_k=top_k,
                    kb_id=kb_id,
                    kb_name=kb_name,
                    version=ver,
                    priority_weight=priority_weight,
                    include_archived=include_archived,
                )
                all_candidates.extend(kb_chunks)
            except Exception as e:
                logger.warning(f"知识库 [{kb_name}] 检索异常: {e}")

        if not all_candidates:
            kb_names = "、".join([t["name"] for t in targets])
            return True, f"在已挂载知识库【{kb_names}】中未检索到与「{query}」直接相关的规程切片。", []

        # 按综合加权分降序排序并截取 Top-K
        all_candidates.sort(key=lambda x: x["final_score"], reverse=True)
        final_top = all_candidates[:top_k]

        # 格式化输出供 LLM 证据链参考
        lines = [f"### 已挂载知识库证据检索结果 (关键词: {query}, 命中文档数: {len(final_top)}):"]
        for idx, c in enumerate(final_top, start=1):
            tier_label = "正式规程(Tier 1)" if c["authority_tier"] == 1 else "常规资料(Tier 2)"
            lines.append(
                f"\n[{idx}] 来源: 【{c['kb_name']} {c['version']}】 - {c['title']} ({tier_label}, 优先级权重: {c['priority_weight']:.2f})"
            )
            lines.append(f"内容:\n{c['content']}")

        return True, "\n".join(lines), final_top

    @classmethod
    async def _search_single_index_db(
        cls,
        db_path: Path,
        query: str,
        top_k: int,
        kb_id: str,
        kb_name: str,
        version: str,
        priority_weight: float,
        include_archived: bool,
    ) -> list[dict[str, Any]]:
        """在单个独立 index.db 中执行 FTS5 + 词法/语义检索并施加优先级。"""
        results: list[dict[str, Any]] = []

        def _do_query():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                clean_q = " ".join([w for w in query.split() if len(w.strip()) > 0])
                if not clean_q:
                    clean_q = query

                rows = []
                # FTS5 trigram 仅在查询词 >= 3 字符时有效
                if len(clean_q.strip()) >= 3:
                    try:
                        fts_sql = """
                        SELECT c.id, c.remote_uri, c.title, c.content, c.authority_tier, c.mtime,
                               rank as fts_rank
                        FROM hybrid_chunks_fts f
                        JOIN hybrid_chunks c ON f.rowid = c.rowid
                        WHERE hybrid_chunks_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                        """
                        cursor = conn.execute(fts_sql, (clean_q, top_k * 2))
                        rows = cursor.fetchall()
                    except Exception:
                        rows = []

                if rows:
                    for r in rows:
                        if not include_archived and r["authority_tier"] >= 3:
                            continue
                        tier = r["authority_tier"]
                        tier_bonus = 1.25 if tier == 1 else 1.0
                        base_score = 1.0 / (abs(r["fts_rank"]) + 1.0)
                        final_score = base_score * tier_bonus * priority_weight

                        results.append({
                            "id": r["id"],
                            "kb_id": kb_id,
                            "kb_name": kb_name,
                            "version": version,
                            "remote_uri": r["remote_uri"],
                            "title": r["title"],
                            "content": r["content"],
                            "authority_tier": tier,
                            "mtime": r["mtime"],
                            "priority_weight": priority_weight,
                            "final_score": final_score,
                        })
                else:
                    # 短词或 trigram 未命中时，自动回退 LIKE 模糊查询
                    like_sql = """
                    SELECT id, remote_uri, title, content, authority_tier, mtime
                    FROM hybrid_chunks
                    WHERE content LIKE ? OR title LIKE ?
                    LIMIT ?
                    """
                    cursor = conn.execute(like_sql, (f"%{clean_q}%", f"%{clean_q}%", top_k))
                    for r in cursor.fetchall():
                        if not include_archived and r["authority_tier"] >= 3:
                            continue
                        tier = r["authority_tier"]
                        tier_bonus = 1.25 if tier == 1 else 1.0
                        results.append({
                            "id": r["id"],
                            "kb_id": kb_id,
                            "kb_name": kb_name,
                            "version": version,
                            "remote_uri": r["remote_uri"],
                            "title": r["title"],
                            "content": r["content"],
                            "authority_tier": tier,
                            "mtime": r["mtime"],
                            "priority_weight": priority_weight,
                            "final_score": 0.8 * tier_bonus * priority_weight,
                        })
            finally:
                conn.close()
            return results

        return await asyncio.to_thread(_do_query)
