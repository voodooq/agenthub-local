"""
AgentHub Local v1.5.8 多知识库隔离检索路由与门控中心 (Multi-KB Retrieval Router)
核心铁律：
1. 默认不盲搜门控 (Explicit Mount Gate): 只有当前专家或会话显式挂载了知识库，检索工具才激活；
2. 跨库物理隔离: 每个知识库严格访问自己独立的 index.db，绝不互相污染；
3. 库级优先级加权重排: W_priority = 1.15 - 0.05 * (rank - 1)。
"""
import asyncio
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger
from ..kb.manager import KBLifecycleManager
from ..kb.storage import KBStorageManager
from .embedding_provider import embedding_router, HAS_NUMPY, np
from .temporal_reranker import TemporalReranker
from .hybrid_engine import HybridSearchEngine
from .rule_extractor import UniversalRuleExtractor, StructuredRuleExtractor


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
                    if not k.get("is_published"):
                        logger.info(f"[MultiKBRouter] 挂载知识库 [{k.get('name')}] 未发布，跳过对外调用")
                        continue
                    targets.append({
                        "kb_id": kid,
                        "name": k["name"],
                        "version": k["active_version"],
                        "priority_rank": idx + 1,
                        "domain_type": k.get("domain_type") or "general",
                        "rule_profile_json": k.get("rule_profile_json") or "",
                    })
            if targets:
                return targets

        # 2. 其次使用专家绑定（本地沙盒对话与外部 @专家 均正常检索绑定知识库）
        if expert_id:
            bound = await KBLifecycleManager.get_expert_kbs(expert_id)
            if bound:
                return [
                    {
                        "kb_id": b["kb_id"],
                        "name": b["name"],
                        "version": b["effective_version"],
                        "priority_rank": b["priority_rank"],
                        "domain_type": b.get("domain_type") or "general",
                        "rule_profile_json": b.get("rule_profile_json") or "",
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

        # 阶段一：时效重排 (TemporalReranker) 仲裁最新生效版
        reranked_candidates = TemporalReranker.rerank_chunks(
            chunks=all_candidates,
            current_query=query,
            now_dt=datetime.now(),
        )

        # 阶段二：Small-to-Big 上下文还原与去重
        # 如果命中的子切片或口语提问关联了 parent_chunk_id，回溯提取完整的父切片
        final_top: list[dict[str, Any]] = []
        seen_parents: set[str] = set()

        for c in reranked_candidates:
            # 兼容性回溯兜底：若 parent_chunk_id 为空，回退为自身 id (Small-to-Big Fallback)
            pid = str(c.get("parent_chunk_id") or c.get("id") or "")
            kb_id = str(c.get("kb_id") or "")
            ver = str(c.get("version") or "v1.0.0")

            # 若有关联的父切片且未展示过，尝试提取完整父切片
            if pid and pid not in seen_parents and kb_id:
                db_path = KBStorageManager.get_version_db_path(kb_id, ver)
                if db_path.exists():
                    try:
                        conn = sqlite3.connect(str(db_path))
                        conn.row_factory = sqlite3.Row
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT id, title, content, section_path, doc_year, effective_date FROM hybrid_chunks WHERE id = ?",
                            (pid,)
                        )
                        parent_row = cur.fetchone()
                        conn.close()
                        if parent_row:
                            c_copy = dict(c)
                            c_copy["content"] = parent_row["content"]
                            c_copy["title"] = parent_row["title"]
                            c_copy["is_expanded_parent"] = (pid != c.get("id"))
                            seen_parents.add(pid)
                            final_top.append(c_copy)
                            if len(final_top) >= top_k:
                                break
                            continue
                    except Exception as p_err:
                        logger.debug(f"[Small-to-Big] 读取父切片失败，保留原切片: {p_err}")

            # 无父切片或未开启 Small-to-Big / 回溯失败时原样返回自身切片，零漏召回
            chunk_hash = str(c.get("id") or c.get("title") or "")
            if chunk_hash and chunk_hash not in seen_parents:
                seen_parents.add(chunk_hash)
                final_top.append(c)
                if len(final_top) >= top_k:
                    break

        # 格式化输出供 LLM 证据链参考（动态注入当前系统真实时间与权威规则卡）
        current_date_str = datetime.now().strftime("%Y年%m月%d日")
        lines = [
            f"> 💡 **[系统时效环境声明]**：当前系统运行时间为 **{current_date_str}**。如命中文档存在历史年份版本更迭，系统已根据最新生效日期完成优先级仲裁，请严格以最新规程为准。\n",
        ]

        # 阶段三：多维条件通用业务规则卡片匹配与置顶注入 (v1.6.0 Universal)
        matched_rules: list[dict[str, Any]] = []
        for target in targets:
            kid = target["kb_id"]
            ver = target["version"]
            domain = target.get("domain_type") or "general"
            profile_json = target.get("rule_profile_json") or ""
            profile = UniversalRuleExtractor.resolve_profile(domain, profile_json)

            if UniversalRuleExtractor.should_trigger_rule_search(query, profile):
                dbp = KBStorageManager.get_version_db_path(kid, ver)
                if dbp.exists():
                    r_list = UniversalRuleExtractor.query_matching_rules(
                        db_path=dbp,
                        kb_id=kid,
                        query=query,
                        profile=profile,
                        limit=3,
                    )
                    matched_rules.extend(r_list)

        if matched_rules:
            # 复合排序：多条件重合度得分 match_score 降序 + 生效日期降序 + 年份降序
            matched_rules.sort(
                key=lambda x: (
                    x.get("match_score", 0),
                    x.get("effective_date") or "",
                    x.get("doc_year") or 0,
                ),
                reverse=True,
            )
            rule_card_text = UniversalRuleExtractor.format_rules_for_prompt(matched_rules[:3])
            if rule_card_text:
                lines.append(rule_card_text)

        lines.append(f"### 已挂载知识库证据检索结果 (关键词: {query}, 命中文档数: {len(final_top)}):")
        for idx, c in enumerate(final_top, start=1):
            tier_label = "正式规程(Tier 1)" if c.get("authority_tier") == 1 else "常规资料(Tier 2)"
            eff_str = f", 生效日期: {c.get('effective_date')}" if c.get('effective_date') else ""
            latest_badge = " [最新版]" if c.get("is_latest_in_cluster") == 1 else ""
            lines.append(
                f"\n[{idx}] 来源: 【{c['kb_name']} {c['version']}】 - {c['title']}{latest_badge} ({tier_label}{eff_str}, 优先级权重: {c['priority_weight']:.2f})"
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
        """在单个独立 index.db 中执行向量相似度、FTS5 全文与时效元数据联合召回。"""
        results: list[dict[str, Any]] = []

        # 中文问句常见指令前缀与停用虚词表
        prompt_prefixes = (
            "帮我查一下", "帮我查询", "帮我看看", "帮我排查", "帮我查", "帮我找",
            "请帮我查", "请帮我", "请问一下", "请问", "我想查一下", "我想查询", "我想知道",
            "查询一下", "查询", "查一下", "看一下", "找一下", "检索一下", "检索",
            "了解一下", "分析一下", "汇总一下", "统计一下",
        )
        stop_words = {
            "帮我", "给我", "我们", "你们", "他们", "一下", "一个", "一些", "这个", "那个",
            "关于", "以及", "并且", "的话", "怎么", "如何", "在哪里", "怎样", "能不能", "可以吗",
            "是多少", "是什么", "有哪些", "有什么", "谁可以", "请介绍", "讲讲", "怎么弄",
            "的", "了", "在", "与", "和", "或", "等", "及", "至", "到", "吗", "呢", "吧", "呀",
        }

        # 尝试提前计算 query embedding
        provider = await embedding_router.get_provider()
        q_vec = None
        if provider.is_available and provider.dimension > 0:
            try:
                q_vec = await provider.encode_query(query.strip())
            except Exception as e:
                logger.debug(f"[MultiKB] 检索 Query 向量编码跳过: {e}")

        def _do_query():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                # 1. 结构化抽取高价值检索实体词与关键词
                q_text = query.strip()
                for p in sorted(prompt_prefixes, key=len, reverse=True):
                    if q_text.startswith(p):
                        q_text = q_text[len(p):].strip()
                        break

                clean_q = re.sub(r"[^\w\u4e00-\u9fa5\s]", " ", q_text).strip()
                raw_tokens = [w for w in clean_q.split() if w]

                # 提取明确年份 (如 2024, 2025)
                years = re.findall(r"\b(19\d\d|20\d\d)\b", clean_q)

                keywords: list[str] = []
                for y in years:
                    if y not in keywords:
                        keywords.append(y)

                # 提取去虚词后的实质短语
                for t in raw_tokens:
                    sub = t
                    for sw in stop_words:
                        sub = sub.replace(sw, " ")
                    for s in sub.split():
                        s_str = s.strip()
                        if len(s_str) >= 2 and s_str not in keywords:
                            keywords.append(s_str)

                # 按长词优先排序实体词（确保如「发票」、「发票明细」、「阿里云」排在前面）
                keywords.sort(key=len, reverse=True)

                # 补充 2-gram 细粒度子串（排除纯数字碎片如 '02', '20'）
                ngram_list: list[str] = []
                for t in raw_tokens:
                    if len(t) >= 2:
                        for i in range(len(t) - 1):
                            bg = t[i : i + 2]
                            if bg not in stop_words and bg not in keywords and bg not in ngram_list:
                                if not bg.isdigit():
                                    ngram_list.append(bg)

                # 优先保留高价值长实体词，末尾补充高质量 2-gram
                all_search_terms = keywords + ngram_list
                if not all_search_terms:
                    all_search_terms = [clean_q] if clean_q else [query]

                keywords = all_search_terms

                rows: list[sqlite3.Row] = []
                matched_ids: set[str] = set()

                # 2. 优先尝试 FTS5 全文索引匹配 (排除 chunk_role='parent' 直接召回)
                fts_candidates = [k for k in keywords if len(k) >= 2]
                if fts_candidates:
                    fts_query_str = " OR ".join([f'"{k}"' for k in fts_candidates[:10]])
                    try:
                        fts_sql = """
                        SELECT c.id, c.remote_uri, c.title, c.content, c.authority_tier, c.mtime,
                               c.doc_year, c.effective_date, c.effective_ts, c.doc_priority_type,
                               c.topic_cluster_id, c.is_latest_in_cluster, c.chunk_role,
                               COALESCE(NULLIF(c.parent_chunk_id, ''), c.id) AS parent_chunk_id,
                               c.embedding_blob, rank as fts_rank
                        FROM hybrid_chunks_fts f
                        JOIN hybrid_chunks c ON f.rowid = c.rowid
                        WHERE hybrid_chunks_fts MATCH ?
                          AND (c.chunk_role != 'parent' OR c.chunk_role IS NULL)
                        ORDER BY rank
                        LIMIT ?
                        """
                        cursor = conn.execute(fts_sql, (fts_query_str, top_k * 3))
                        rows = cursor.fetchall()
                        for r in rows:
                            matched_ids.add(r["id"])
                    except Exception:
                        rows = []

                # 3. 若 FTS5 召回量少，补充多关键词 LIKE 模糊匹配
                if len(rows) < top_k * 2 and keywords:
                    where_clauses = ["(content LIKE ? OR title LIKE ?)" for _ in keywords[:6]]
                    like_sql = f"""
                    SELECT id, remote_uri, title, content, authority_tier, mtime,
                           doc_year, effective_date, effective_ts, doc_priority_type,
                           topic_cluster_id, is_latest_in_cluster, chunk_role,
                           COALESCE(NULLIF(parent_chunk_id, ''), id) AS parent_chunk_id,
                           embedding_blob, NULL as fts_rank
                    FROM hybrid_chunks
                    WHERE ({" OR ".join(where_clauses)})
                      AND (chunk_role != 'parent' OR chunk_role IS NULL)
                    LIMIT ?
                    """
                    params: list[Any] = []
                    for k in keywords[:6]:
                        params.extend([f"%{k}%", f"%{k}%"])
                    params.append(top_k * 3)
                    cursor = conn.execute(like_sql, params)
                    more_rows = cursor.fetchall()
                    for mr in more_rows:
                        if mr["id"] not in matched_ids:
                            matched_ids.add(mr["id"])
                            rows.append(mr)

                # 4. 向量余弦相似度计算与加权打分
                scored_list = []
                import array
                q_list = [float(x) for x in q_vec] if q_vec is not None else None
                q_len = len(q_list) if q_list is not None else 0

                for r in rows:
                    if not include_archived and (r["authority_tier"] or 2) >= 3:
                        continue
                    tier = r["authority_tier"] or 2
                    tier_bonus = 1.25 if tier == 1 else 1.0

                    content_str = r["content"] or ""
                    title_str = r["title"] or ""

                    hit_cnt = sum(1 for kw in keywords if kw in content_str or kw in title_str)
                    base_score = min(1.0, 0.4 + 0.2 * hit_cnt)

                    if r["fts_rank"] is not None:
                        try:
                            fts_bonus = 1.0 / (abs(float(r["fts_rank"])) + 1.0)
                            base_score = max(base_score, fts_bonus)
                        except Exception:
                            pass

                    # 向量打分
                    cosine_sim = 0.0
                    blob = r["embedding_blob"]
                    if blob and q_list and q_len > 0:
                        try:
                            arr = array.array("f")
                            arr.frombytes(blob)
                            if len(arr) == q_len:
                                dot = sum(a * b for a, b in zip(arr, q_list))
                                cosine_sim = max(0.0, float(dot))
                                # 融合向量得分
                                base_score = 0.5 * base_score + 0.5 * cosine_sim
                        except Exception:
                            pass

                    # 如果是口语提问命中的切片，提升相关度激励
                    role = r["chunk_role"] or "child"
                    role_boost = 1.15 if role == "hype_question" else 1.0

                    final_score = base_score * tier_bonus * priority_weight * role_boost
                    scored_list.append((final_score, {
                        "id": r["id"],
                        "kb_id": kb_id,
                        "kb_name": kb_name,
                        "version": version,
                        "remote_uri": r["remote_uri"],
                        "title": r["title"],
                        "content": r["content"],
                        "authority_tier": tier,
                        "mtime": r["mtime"],
                        "doc_year": r["doc_year"],
                        "effective_date": r["effective_date"],
                        "effective_ts": r["effective_ts"],
                        "doc_priority_type": r["doc_priority_type"],
                        "topic_cluster_id": r["topic_cluster_id"],
                        "is_latest_in_cluster": r["is_latest_in_cluster"],
                        "chunk_role": role,
                        "parent_chunk_id": r["parent_chunk_id"],
                        "priority_weight": priority_weight,
                        "final_score": round(final_score, 4),
                        "cosine_similarity": round(cosine_sim, 4),
                    }))

                scored_list.sort(key=lambda x: x[0], reverse=True)
                results = [item[1] for item in scored_list[: top_k * 2]]
            finally:
                conn.close()
            return results

        return await asyncio.to_thread(_do_query)
