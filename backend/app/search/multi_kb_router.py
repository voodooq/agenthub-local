"""
AgentHub Local v1.5.8 多知识库隔离检索路由与门控中心 (Multi-KB Retrieval Router)
核心铁律：
1. 默认不盲搜门控 (Explicit Mount Gate): 只有当前专家或会话显式挂载了知识库，检索工具才激活；
2. 跨库物理隔离: 每个知识库严格访问自己独立的 index.db，绝不互相污染；
3. 库级优先级加权重排: W_priority = 1.15 - 0.05 * (rank - 1)。
"""
import asyncio
from datetime import datetime
import json
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
from .intent_router import QueryIntentRouter
from .rule_extractor import UniversalRuleExtractor, StructuredRuleExtractor


# 装饰性标点正则（圆点变体、全半角括号与引号及其前后空白），严禁包含小数点(.)、星号(*)、百分比(%)和连字符(-)
_DECORATIVE_PUNCT_RE = re.compile(r"\s*[·•・‧（）()\"“”]+\s*")


def normalize_entity_key(text: str) -> str:
    """折叠实体名中的装饰性标点与多余空白（保留 . * % - 等语义符号），用于去重分桶与兼容比对。"""
    clean = _DECORATIVE_PUNCT_RE.sub("", (text or "").strip())
    return re.sub(r"\s+", " ", clean).strip()


def to_like_wildcard_pattern(term: str) -> str:
    """查询侧 LIKE 通配展开：将装饰性标点及其前后空格折叠为 %，完整保留 . * % - 等语义符号。"""
    raw = (term or "").strip()
    if not raw:
        return "%"
    collapsed = _DECORATIVE_PUNCT_RE.sub("%", raw)
    collapsed = re.sub(r"%+", "%", collapsed).strip("%")
    return f"%{collapsed}%" if collapsed else f"%{raw}%"


class MultiKBRetrievalRouter:
    """多知识库隔离检索与优先级路由调度器。"""

    # 按 Embedding 模型分布差异配置 CRAG 阈值（默认基于 BGE-M3 实测分布校准）
    CRAG_MODEL_THRESHOLDS: dict[str, dict[str, float]] = {
        "bge-m3": {
            "fallback_score": 0.48,
            "fallback_cosine": 0.38,
            "reject_cosine": 0.55,
        },
        "default": {
            "fallback_score": 0.48,
            "fallback_cosine": 0.38,
            "reject_cosine": 0.55,
        },
    }

    CRAG_FALLBACK_SCORE_THRESHOLD: float = 0.48
    CRAG_FALLBACK_COSINE_THRESHOLD: float = 0.38
    CRAG_REJECT_COSINE_THRESHOLD: float = 0.55

    @classmethod
    def _resolve_crag_thresholds(cls, targets: list[dict[str, Any]]) -> dict[str, float]:
        """按知识库挂载的 Embedding 模型或单库覆盖配置解析 CRAG 阈值。"""
        base = dict(cls.CRAG_MODEL_THRESHOLDS["default"])
        for t in targets:
            model_name = str(t.get("embedding_model") or "").lower()
            for key, cfg in cls.CRAG_MODEL_THRESHOLDS.items():
                if key != "default" and key in model_name:
                    base.update(cfg)
                    break
            # 支持单库元数据按需覆盖阈值
            if t.get("crag_reject_cosine") is not None:
                base["reject_cosine"] = float(t["crag_reject_cosine"])
        return base

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
                        "is_published": bool(k.get("is_published")),
                    })
            if targets:
                return targets

        # 2. 其次使用专家绑定（严格执行发布门控：未发布知识库仅限后台测试，严禁前台专家读取）
        if expert_id:
            bound = await KBLifecycleManager.get_expert_kbs(expert_id)
            if bound:
                published_bound: list[dict[str, Any]] = []
                for b in bound:
                    if not b.get("is_published"):
                        logger.info(
                            f"[MultiKBRouter] 专家绑定知识库 [{b.get('name')}] 未发布，已严格拦截前台读取"
                        )
                        continue
                    published_bound.append(
                        {
                            "kb_id": b["kb_id"],
                            "name": b["name"],
                            "version": b["effective_version"],
                            "priority_rank": b["priority_rank"],
                            "domain_type": b.get("domain_type") or "general",
                            "rule_profile_json": b.get("rule_profile_json") or "",
                            "is_published": True,
                        }
                    )
                return published_bound

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

        intent = QueryIntentRouter.classify(query)
        all_candidates: list[dict[str, Any]] = []

        # 并行检索各个独立知识库 index.db（按意图三路分流）
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
                if intent == "cross_version_compare":
                    l2_hits = await cls._fetch_cross_version_l2(
                        db_path=db_path, kb_id=kb_id, kb_name=kb_name, version=ver, priority_weight=priority_weight
                    )
                    if l2_hits:
                        all_candidates.extend(l2_hits)
                        continue
                elif intent == "macro_process_summary":
                    macro_hits = await cls._fetch_macro_summaries(
                        db_path=db_path, kb_id=kb_id, kb_name=kb_name, version=ver, priority_weight=priority_weight
                    )
                    if macro_hits:
                        all_candidates.extend(macro_hits)
                        detail_hits = await cls._search_single_index_db(
                            db_path=db_path,
                            query=query,
                            top_k=2,
                            kb_id=kb_id,
                            kb_name=kb_name,
                            version=ver,
                            priority_weight=priority_weight * 0.9,
                            include_archived=include_archived,
                        )
                        all_candidates.extend(detail_hits[:2])
                        continue

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

                # 对比意图增强：若识别出多个独立实体（如“无锡”与“东安湖”），按实体分别补查 2 个专属切片，杜绝单实体霸占全部配额
                contrast_entities = [e for e in cls._extract_specific_entities(query) if len(e) >= 2]
                if len(contrast_entities) >= 2:
                    for ce in contrast_entities[:3]:
                        sub_chunks = await cls._search_single_index_db(
                            db_path=db_path,
                            query=f"{ce} 日期 地点 规程",
                            top_k=2,
                            kb_id=kb_id,
                            kb_name=kb_name,
                            version=ver,
                            priority_weight=priority_weight * 0.98,
                            include_archived=include_archived,
                        )
                        all_candidates.extend(sub_chunks)
            except Exception as e:
                logger.warning(f"知识库 [{kb_name}] 检索异常: {e}")

        # CRAG 一级评估：按挂载库 Embedding 模型解析阈值，统计初筛指标并按需触发纯向量降级扩召
        crag_cfg = cls._resolve_crag_thresholds(targets)
        max_score = max((float(c.get("final_score", 0.0)) for c in all_candidates), default=0.0)
        max_cosine = max((float(c.get("cosine_similarity", 0.0)) for c in all_candidates), default=0.0)
        max_hit_cnt = max((int(c.get("hit_cnt", 0)) for c in all_candidates), default=0)

        need_l1_fallback = (not all_candidates) or (
            max_score < crag_cfg["fallback_score"]
            and max_cosine < crag_cfg["fallback_cosine"]
            and max_hit_cnt == 0
        )
        if need_l1_fallback:
            logger.info(
                f"[CRAG-L1] 触发纯向量降级扩召: query='{query[:40]}', "
                f"max_score={max_score:.4f}, max_cosine={max_cosine:.4f}, candidates={len(all_candidates)}"
            )
            seen_cand_ids = {str(c.get("id") or "") for c in all_candidates}
            for target in targets:
                kb_id = target["kb_id"]
                ver = target["version"]
                rank = target["priority_rank"]
                kb_name = target["name"]
                db_path = KBStorageManager.get_version_db_path(kb_id, ver)
                if not db_path.exists():
                    continue
                priority_weight = max(0.8, 1.15 - 0.05 * (rank - 1))
                vec_hits = await cls._vector_fallback_search(
                    db_path=db_path,
                    query=query,
                    top_k=min(5, top_k),
                    kb_id=kb_id,
                    kb_name=kb_name,
                    version=ver,
                    priority_weight=priority_weight,
                    include_archived=include_archived,
                )
                for vh in vec_hits:
                    if str(vh.get("id") or "") not in seen_cand_ids:
                        seen_cand_ids.add(str(vh.get("id") or ""))
                        all_candidates.append(vh)

            max_score = max((float(c.get("final_score", 0.0)) for c in all_candidates), default=0.0)
            max_cosine = max((float(c.get("cosine_similarity", 0.0)) for c in all_candidates), default=0.0)

        logger.info(
            f"[RetrievalMetrics] query='{query[:40]}', candidates={len(all_candidates)}, "
            f"max_score={max_score:.4f}, max_cosine={max_cosine:.4f}, max_hit_cnt={max_hit_cnt}"
        )

        # 阶段一：时效重排 (TemporalReranker) 仲裁最新生效版
        reranked_candidates = (
            TemporalReranker.rerank_chunks(
                chunks=all_candidates,
                current_query=query,
                now_dt=datetime.now(),
            )
            if all_candidates
            else []
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

        # 阶段三：多维条件通用业务规则卡片匹配 (v1.6.0 Universal)
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

        # 阶段四：知识图谱精准实体事实卡片提取 (Knowledge Graph Triples)
        all_triples: list[dict[str, Any]] = []
        for target in targets:
            kid = target["kb_id"]
            ver = target["version"]
            dbp = KBStorageManager.get_version_db_path(kid, ver)
            if dbp.exists():
                k_triples = cls._fetch_matching_triples(db_path=dbp, query=query, limit=6)
                all_triples.extend(k_triples)

        # CRAG 二级拒答裁决：必须在查完规则卡与三元组后进行最终判定，防止误杀纯图谱/规则命中
        # 若关键词 0 命中且向量余弦低于 reject_cosine，即使乘以权威系数后 total_score 偏高仍判定为低置信度
        is_low_confidence = (
            not final_top
            or (max_hit_cnt <= 1 and max_score < 1.05 and max_cosine < crag_cfg["reject_cosine"])
        )
        if is_low_confidence and not matched_rules and not all_triples:
            kb_names = "、".join([t["name"] for t in targets])
            logger.warning(
                f"[CRAG-L2] 触发置信度拒答短路: query='{query[:40]}', "
                f"max_score={max_score:.4f}, max_cosine={max_cosine:.4f}, "
                f"reject_threshold={crag_cfg['reject_cosine']:.2f}, hit_cnt={max_hit_cnt}"
            )
            return (
                True,
                f"[CRAG_NO_EVIDENCE] 在已挂载知识库【{kb_names}】中未收录与「{query}」相关的规定或数据。"
                "请直接如实告知用户当前知识库暂无相关内容，严禁脱离上下文自由发挥或编造。",
                [],
            )

        # 格式化输出供 LLM 证据链参考（动态注入当前系统真实时间、引用编号与防越界约束）
        current_date_str = datetime.now().strftime("%Y年%m月%d日")
        available_refs: list[str] = []
        if matched_rules:
            available_refs.extend([f"[R{i}]" for i in range(1, len(matched_rules[:3]) + 1)])
        if all_triples:
            available_refs.extend([f"[T{i}]" for i in range(1, len(all_triples[:8]) + 1)])
        if final_top:
            available_refs.extend([f"[S{i}]" for i in range(1, len(final_top) + 1)])
        refs_hint = "、".join(available_refs) if available_refs else "无"

        lines = [
            f"> 💡 **[系统时效环境声明]**：当前系统运行时间为 **{current_date_str}**。如命中文档存在历史年份版本更迭，系统已根据最新生效日期完成优先级仲裁，请严格以最新规程为准。",
            f"> 📎 **[句级引用标注铁律]**：回答中的关键数字、日期、金额、标准及条款结论后，请标注对应来源编号（当前可用编号：{refs_hint}）。**严禁编造上下文中不存在的编号**，也不要在无实质信息的衔接句后堆砌编号。\n",
        ]

        unified_evidence: list[dict[str, Any]] = []

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
            top_rules = matched_rules[:3]
            rule_card_text = UniversalRuleExtractor.format_rules_for_prompt(top_rules)
            if rule_card_text:
                lines.append(rule_card_text)
            for r_idx, r in enumerate(top_rules, start=1):
                unified_evidence.append({
                    "id": f"rule_{r_idx}",
                    "citation_id": f"R{r_idx}",
                    "evidence_type": "rule",
                    "title": f"结构化规则卡 [{r.get('rule_category', 'rule')}]",
                    "remote_uri": f"kb://rules/R{r_idx}",
                    "content": str(r.get("rule_summary") or ""),
                    "authority_tier": 1,
                    "final_score": 1.0,
                })

        if all_triples:
            triple_lines = [
                "> 📌 **[知识图谱核心实体事实 (Knowledge Graph Triples)]**：",
            ]
            top_triples = all_triples[:8]
            for t_idx, t in enumerate(top_triples, start=1):
                triple_lines.append(f"> * [T{t_idx}] **{t['subject']}** ──【{t['predicate']}】──> `{t['object']}`")
                unified_evidence.append({
                    "id": f"triple_{t_idx}",
                    "citation_id": f"T{t_idx}",
                    "evidence_type": "triple",
                    "title": f"知识图谱: {t['subject']}",
                    "remote_uri": f"kb://triples/T{t_idx}",
                    "content": f"{t['subject']} {t['predicate']} {t['object']}",
                    "authority_tier": 1,
                    "final_score": 1.0,
                })
            lines.append("\n".join(triple_lines) + "\n")

        lines.append(f"### 已挂载知识库证据检索结果 (关键词: {query}, 命中文档数: {len(final_top)}):")
        for idx, c in enumerate(final_top, start=1):
            c["citation_id"] = f"S{idx}"
            c["evidence_type"] = "chunk"
            tier_label = "正式规程(Tier 1)" if c.get("authority_tier") == 1 else "常规资料(Tier 2)"
            eff_str = f", 生效日期: {c.get('effective_date')}" if c.get('effective_date') else ""
            latest_badge = " [最新版]" if c.get("is_latest_in_cluster") == 1 else ""
            lines.append(
                f"\n[S{idx}] 来源: 【{c['kb_name']} {c['version']}】 - {c['title']}{latest_badge} ({tier_label}{eff_str}, 优先级权重: {c['priority_weight']:.2f})"
            )
            lines.append(f"内容:\n{c['content']}")

        # 统一证据池：正文切片在前，规则卡与图谱三元组同步封装，消除 Self-Critique 误报
        return True, "\n".join(lines), final_top + unified_evidence

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
            "请对比一下", "请对比", "对比一下", "对比",
        )
        stop_words = {
            "帮我", "给我", "我们", "你们", "他们", "一下", "一个", "一些", "这个", "那个",
            "关于", "以及", "并且", "的话", "怎么", "如何", "在哪里", "怎样", "能不能", "可以吗",
            "是多少", "是什么", "有哪些", "有什么", "谁可以", "请介绍", "讲讲", "怎么弄",
            "它们", "各自", "分别", "哪天", "什么", "哪些", "为何", "为什么", "具体", "请问",
            "对比", "规程", "要求", "规定",
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

                # 提取去虚词后的实质专有实体（地名、站名、马房、救护车等专用核心名词）
                specific_entities = cls._extract_specific_entities(query)
                keywords: list[str] = list(specific_entities)

                for y in years:
                    if y not in keywords:
                        keywords.append(y)

                # 提取去虚词后的实质短语
                sports_category_words = (
                    "场地障碍赛", "场地障碍", "盛装舞步", "三项赛", "耐力赛",
                    "锦标赛", "公开赛", "挑战赛", "城市系列", "巡回赛", "联赛", "总决赛",
                )
                for t in raw_tokens:
                    sub = t
                    for sw in stop_words:
                        sub = sub.replace(sw, " ")
                    for s in sub.split():
                        s_str = s.strip()
                        if len(s_str) >= 2 and s_str not in keywords:
                            keywords.append(s_str)
                        # 识别专有赛事站次/地名（如无锡站 -> 无锡，东安湖站 -> 东安湖）
                        if s_str.endswith("站") and len(s_str) > 2:
                            base_ent = s_str[:-1]
                            if base_ent not in keywords:
                                keywords.append(base_ent)
                        # 剥离复合项目词后提取核心实体
                        for sc in sports_category_words:
                            if sc in s_str and len(s_str) > len(sc):
                                pure_ent = s_str.replace(sc, "").strip()
                                if len(pure_ent) >= 2 and pure_ent not in keywords:
                                    keywords.append(pure_ent)

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

                has_prov = False
                try:
                    cols = [col[1] for col in conn.execute("PRAGMA table_info(hybrid_chunks)").fetchall()]
                    has_prov = "provenance_json" in cols
                except Exception:
                    pass
                prov_col_fts = ", c.provenance_json" if has_prov else ", '' as provenance_json"
                prov_col_like = ", provenance_json" if has_prov else ", '' as provenance_json"

                rows: list[sqlite3.Row] = []
                matched_ids: set[str] = set()

                # 2. 优先尝试 FTS5 全文索引匹配 (针对 trigram 分词器，必须过滤 len(k) >= 3)
                fts_candidates = [k for k in keywords if len(k) >= 3]
                if fts_candidates:
                    fts_query_str = " OR ".join([f'"{k}"' for k in fts_candidates[:10]])
                    try:
                        fts_sql = f"""
                        SELECT c.id, c.remote_uri, c.title, c.content, c.authority_tier, c.mtime,
                               c.doc_year, c.effective_date, c.effective_ts, c.doc_priority_type,
                               c.topic_cluster_id, c.is_latest_in_cluster, c.chunk_role,
                               COALESCE(NULLIF(c.parent_chunk_id, ''), c.id) AS parent_chunk_id,
                               c.embedding_blob{prov_col_fts}, rank as fts_rank
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

                # 3. 针对 2 字符短实体词 (trigram 致盲区：如“深圳”、“马房”、“无锡”) 强制执行精准 LIKE 召回
                short_kws = [k for k in keywords if len(k) == 2]
                if len(short_kws) >= 2:
                    # 优先交叉精准匹配 (例如: 深圳 AND 马房)
                    cross_clauses = ["content LIKE ?" for _ in short_kws[:3]]
                    cross_sql = f"""
                    SELECT id, remote_uri, title, content, authority_tier, mtime,
                           doc_year, effective_date, effective_ts, doc_priority_type,
                           topic_cluster_id, is_latest_in_cluster, chunk_role,
                           COALESCE(NULLIF(parent_chunk_id, ''), id) AS parent_chunk_id,
                           embedding_blob{prov_col_like}, -1.0 as fts_rank
                    FROM hybrid_chunks
                    WHERE ({" AND ".join(cross_clauses)})
                      AND (chunk_role != 'parent' OR chunk_role IS NULL)
                    LIMIT ?
                    """
                    cross_params = [to_like_wildcard_pattern(k) for k in short_kws[:3]] + [top_k * 2]
                    try:
                        for cr in conn.execute(cross_sql, cross_params).fetchall():
                            if cr["id"] not in matched_ids:
                                matched_ids.add(cr["id"])
                                rows.append(cr)
                    except Exception:
                        pass

                # 4. 补充多关键词 LIKE 模糊匹配（确保短词与泛词并存时不被 FTS 粗粒度结果阻断）
                if (len(rows) < top_k * 2 or short_kws) and keywords:
                    where_clauses = ["(content LIKE ? OR title LIKE ?)" for _ in keywords[:6]]
                    like_sql = f"""
                    SELECT id, remote_uri, title, content, authority_tier, mtime,
                           doc_year, effective_date, effective_ts, doc_priority_type,
                           topic_cluster_id, is_latest_in_cluster, chunk_role,
                           COALESCE(NULLIF(parent_chunk_id, ''), id) AS parent_chunk_id,
                           embedding_blob{prov_col_like}, NULL as fts_rank
                    FROM hybrid_chunks
                    WHERE ({" OR ".join(where_clauses)})
                      AND (chunk_role != 'parent' OR chunk_role IS NULL)
                    LIMIT ?
                    """
                    params: list[Any] = []
                    for k in keywords[:6]:
                        pat = to_like_wildcard_pattern(k)
                        params.extend([pat, pat])
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

                    # 向量打分（兼容 float16 半精度与旧版 float32 向量）
                    cosine_sim = 0.0
                    blob = r["embedding_blob"]
                    if blob and q_list and q_len > 0:
                        try:
                            from ..kb.embedding_codec import decode_embedding
                            arr = decode_embedding(blob, q_len)
                            if arr is not None and len(arr) == q_len:
                                dot = sum(float(a) * b for a, b in zip(arr, q_list))
                                cosine_sim = max(0.0, float(dot))
                                # 融合向量得分
                                base_score = 0.5 * base_score + 0.5 * cosine_sim
                        except Exception:
                            pass

                    # Wiki 高权威反哺与口语提问激励加权
                    role = r["chunk_role"] or "child"
                    if role == "wiki_page":
                        role_boost = 1.25
                    elif role == "hype_question":
                        role_boost = 1.15
                    else:
                        role_boost = 1.0

                    prov_raw = str(r["provenance_json"] or "") if has_prov else ""
                    prov_obj = None
                    if prov_raw:
                        try:
                            prov_obj = json.loads(prov_raw)
                        except Exception:
                            prov_obj = None
                    from ..kb.provenance import format_citation_label
                    citation_str = format_citation_label(prov_obj)

                    # 核心专有实体命中特异度加权 (如同时命中“深圳”+“马房”，优先置顶)
                    entity_boost = 1.0
                    if specific_entities:
                        all_hit = all(se in content_str or se in title_str for se in specific_entities)
                        any_hit = any(se in content_str or se in title_str for se in specific_entities)
                        if all_hit and len(specific_entities) >= 2:
                            entity_boost = 1.35
                        elif any_hit:
                            entity_boost = 1.15

                    final_score = base_score * tier_bonus * priority_weight * role_boost * entity_boost
                    scored_list.append((final_score, {
                        "id": r["id"],
                        "chunk_id": r["id"],
                        "kb_id": kb_id,
                        "kb_name": kb_name,
                        "version": version,
                        "remote_uri": r["remote_uri"],
                        "source": r["title"],
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
                        "provenance_json": prov_raw,
                        "provenance": prov_obj,
                        "citation_label": citation_str,
                        "score": round(final_score, 4),
                        "priority_weight": priority_weight,
                        "final_score": round(final_score, 4),
                        "cosine_similarity": round(cosine_sim, 4),
                        "hit_cnt": hit_cnt,
                    }))

                scored_list.sort(key=lambda x: x[0], reverse=True)
                results = [item[1] for item in scored_list[: top_k * 2]]
            finally:
                conn.close()
            return results

        return await asyncio.to_thread(_do_query)

    @classmethod
    async def _vector_fallback_search(
        cls,
        db_path: Path,
        query: str,
        top_k: int,
        kb_id: str,
        kb_name: str,
        version: str,
        priority_weight: float,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """CRAG 一级降级：不限实体与关键词，纯向量全库扫描扩召。"""
        provider = await embedding_router.get_provider()
        if not provider.is_available or provider.dimension <= 0:
            return []
        try:
            q_vec = await provider.encode_query(query.strip())
        except Exception:
            return []
        if q_vec is None:
            return []

        q_list = [float(x) for x in q_vec]
        q_len = len(q_list)
        if q_len <= 0:
            return []

        def _scan_vectors() -> list[dict[str, Any]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                cols = [col[1] for col in conn.execute("PRAGMA table_info(hybrid_chunks)").fetchall()]
                prov_col = ", provenance_json" if "provenance_json" in cols else ", '' as provenance_json"
                sql = f"""
                SELECT id, remote_uri, title, content, authority_tier, mtime,
                       doc_year, effective_date, effective_ts, doc_priority_type,
                       topic_cluster_id, is_latest_in_cluster, chunk_role,
                       COALESCE(NULLIF(parent_chunk_id, ''), id) AS parent_chunk_id,
                       embedding_blob{prov_col}
                FROM hybrid_chunks
                WHERE embedding_blob IS NOT NULL
                  AND (chunk_role != 'parent' OR chunk_role IS NULL)
                """
                rows = conn.execute(sql).fetchall()
                from ..kb.embedding_codec import decode_embedding
                from ..kb.provenance import format_citation_label

                scored: list[tuple[float, dict[str, Any]]] = []
                for r in rows:
                    if not include_archived and (r["authority_tier"] or 2) >= 3:
                        continue
                    blob = r["embedding_blob"]
                    if not blob:
                        continue
                    arr = decode_embedding(blob, q_len)
                    if arr is None or len(arr) != q_len:
                        continue
                    cosine_sim = max(0.0, float(sum(float(a) * b for a, b in zip(arr, q_list))))
                    tier = r["authority_tier"] or 2
                    tier_bonus = 1.25 if tier == 1 else 1.0
                    role = r["chunk_role"] or "child"
                    role_boost = 1.25 if role == "wiki_page" else (1.15 if role == "hype_question" else 1.0)
                    final_score = cosine_sim * tier_bonus * priority_weight * role_boost

                    prov_raw = str(r["provenance_json"] or "")
                    prov_obj = None
                    if prov_raw:
                        try:
                            prov_obj = json.loads(prov_raw)
                        except Exception:
                            prov_obj = None

                    scored.append((final_score, {
                        "id": r["id"],
                        "chunk_id": r["id"],
                        "kb_id": kb_id,
                        "kb_name": kb_name,
                        "version": version,
                        "remote_uri": r["remote_uri"],
                        "source": r["title"],
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
                        "provenance_json": prov_raw,
                        "provenance": prov_obj,
                        "citation_label": format_citation_label(prov_obj),
                        "score": round(final_score, 4),
                        "priority_weight": priority_weight,
                        "final_score": round(final_score, 4),
                        "cosine_similarity": round(cosine_sim, 4),
                        "hit_cnt": 0,
                    }))

                scored.sort(key=lambda x: x[0], reverse=True)
                return [item[1] for item in scored[:top_k]]
            finally:
                conn.close()

        return await asyncio.to_thread(_scan_vectors)

    @classmethod
    async def _fetch_cross_version_l2(
        cls,
        db_path: Path,
        kb_id: str,
        kb_name: str,
        version: str,
        priority_weight: float,
    ) -> list[dict[str, Any]]:
        """跨版本对比意图专路：按 topic_cluster_id 提取最新与次新两版 summary_l2。"""
        def _query() -> list[dict[str, Any]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT id, remote_uri, title, content, authority_tier, mtime,
                           doc_year, effective_date, effective_ts, doc_priority_type,
                           topic_cluster_id, is_latest_in_cluster, chunk_role, parent_chunk_id
                    FROM hybrid_chunks
                    WHERE chunk_role = 'summary_l2'
                    ORDER BY doc_year DESC, effective_ts DESC, mtime DESC
                    """
                ).fetchall()
                if not rows:
                    return []

                per_cluster_count: dict[str, int] = {}
                selected: list[dict[str, Any]] = []
                for idx, r in enumerate(rows):
                    cluster = r["topic_cluster_id"] or "__default__"
                    cnt = per_cluster_count.get(cluster, 0)
                    if cnt >= 2:
                        continue
                    per_cluster_count[cluster] = cnt + 1
                    score = max(0.85, 1.0 - idx * 0.02) * priority_weight
                    selected.append({
                        "id": r["id"],
                        "kb_id": kb_id,
                        "kb_name": kb_name,
                        "version": version,
                        "remote_uri": r["remote_uri"],
                        "title": r["title"],
                        "content": r["content"],
                        "authority_tier": r["authority_tier"] or 1,
                        "mtime": r["mtime"],
                        "doc_year": r["doc_year"],
                        "effective_date": r["effective_date"],
                        "effective_ts": r["effective_ts"],
                        "doc_priority_type": r["doc_priority_type"],
                        "topic_cluster_id": r["topic_cluster_id"],
                        "is_latest_in_cluster": r["is_latest_in_cluster"],
                        "chunk_role": "summary_l2",
                        "parent_chunk_id": r["id"],
                        "priority_weight": priority_weight,
                        "final_score": round(score, 4),
                        "cosine_similarity": 1.0,
                    })
                return selected
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

    @classmethod
    async def _fetch_macro_summaries(
        cls,
        db_path: Path,
        kb_id: str,
        kb_name: str,
        version: str,
        priority_weight: float,
    ) -> list[dict[str, Any]]:
        """全流程/宏观总结意图专路：优先提取 summary_l2 (Top-1) + summary_l1 (Top-4)。"""
        def _query() -> list[dict[str, Any]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                l2_rows = conn.execute(
                    """
                    SELECT id, remote_uri, title, content, authority_tier, mtime,
                           doc_year, effective_date, effective_ts, doc_priority_type,
                           topic_cluster_id, is_latest_in_cluster
                    FROM hybrid_chunks
                    WHERE chunk_role = 'summary_l2'
                    ORDER BY is_latest_in_cluster DESC, effective_ts DESC
                    LIMIT 1
                    """
                ).fetchall()
                l1_rows = conn.execute(
                    """
                    SELECT id, remote_uri, title, content, authority_tier, mtime,
                           doc_year, effective_date, effective_ts, doc_priority_type,
                           topic_cluster_id, is_latest_in_cluster
                    FROM hybrid_chunks
                    WHERE chunk_role = 'summary_l1'
                    ORDER BY is_latest_in_cluster DESC, effective_ts DESC
                    LIMIT 4
                    """
                ).fetchall()
                results: list[dict[str, Any]] = []
                for role, group in (("summary_l2", l2_rows), ("summary_l1", l1_rows)):
                    base = 1.05 if role == "summary_l2" else 0.98
                    for r in group:
                        results.append({
                            "id": r["id"],
                            "kb_id": kb_id,
                            "kb_name": kb_name,
                            "version": version,
                            "remote_uri": r["remote_uri"],
                            "title": r["title"],
                            "content": r["content"],
                            "authority_tier": r["authority_tier"] or 1,
                            "mtime": r["mtime"],
                            "doc_year": r["doc_year"],
                            "effective_date": r["effective_date"],
                            "effective_ts": r["effective_ts"],
                            "doc_priority_type": r["doc_priority_type"],
                            "topic_cluster_id": r["topic_cluster_id"],
                            "is_latest_in_cluster": r["is_latest_in_cluster"],
                            "chunk_role": role,
                            "parent_chunk_id": r["id"],
                            "priority_weight": priority_weight,
                            "final_score": round(base * priority_weight, 4),
                            "cosine_similarity": 1.0,
                        })
                return results
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

    @classmethod
    def _extract_specific_entities(cls, query: str) -> list[str]:
        """从用户问句中剥离通用问答虚词与泛化分类词，精准提取跨行业核心实体（地名、部门、项目、财务要素等）。"""
        func_words = [
            "帮我查一下", "帮我查询", "请帮我查", "请对比一下", "请对比", "对比一下", "对比", "请问一下", "请问", "帮我", "一下",
            "它们", "各自", "分别", "哪天", "哪一天", "什么", "具体", "如何", "怎么", "怎样", "是多少", "有哪些", "有什么", "需要什么",
            "怎么办理", "如何申请", "可以吗", "能不能", "有限额吗", "有哪些明确要求", "有什么明确要求", "有哪些规定",
            "一共有", "总共有", "总共", "一共", "共有", "多少", "几间", "几人", "几个", "几辆", "多少钱", "几天",
            "举办", "举行", "时间", "日期", "场地", "地点", "规程", "规则", "制度", "规定", "要求", "标准", "费用",
            "场地障碍赛", "场地障碍", "盛装舞步", "三项赛", "耐力赛", "锦标赛", "公开赛", "挑战赛", "城市系列", "巡回赛", "联赛", "总决赛",
            "年度", "全国", "国际", "分级", "公司", "企业",
            "的", "了", "在", "与", "和", "或", "等", "及", "至", "到", "吗", "呢", "吧", "呀", "间", "个", "套", "辆", "元", "块"
        ]
        sub = query
        # 剥离明确年份 (如 2024年, 2025年)
        sub = re.sub(r"\b(19\d\d|20\d\d)年?", " ", sub)

        for fw in sorted(func_words, key=len, reverse=True):
            sub = sub.replace(fw, " ")

        clean_sub = re.sub(r"[^\w\u4e00-\u9fa5\s]", " ", sub)
        tokens = [w.strip() for w in clean_sub.split() if len(w.strip()) >= 2]

        entities: list[str] = []
        for t in tokens:
            if t.isdigit() or re.fullmatch(r"(19|20)\d\d", t):
                continue
            if t.upper() in ("CSI1", "CSI2", "CSI3", "CSI4", "CSI5", "FEI"):
                continue
            if t not in entities:
                entities.append(t)
            if t.endswith("站") and len(t) > 2:
                base = t[:-1]
                if base not in entities:
                    entities.append(base)
        return entities

    @classmethod
    def _fetch_matching_triples(
        cls,
        db_path: Path,
        query: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """从独立知识库版本库中提取与查询高相关的结构化图谱三元组事实。"""
        if not db_path.exists():
            return []
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            has_table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_triples'").fetchone()
            if not has_table:
                return []

            entities = cls._extract_specific_entities(query)
            if not entities:
                return []

            priority_preds = {
                # 通用与时效/主体谓词
                "时间", "日期", "生效日期", "截止日期", "地点", "主办单位", "承办单位", "责任部门",
                # 体育赛事谓词
                "比赛时间", "报名截止时间", "收费标准", "级别", "大奖赛级别", "参赛资格",
                # 财务、报销与合同制度谓词
                "金额", "合同金额", "限额", "报销标准", "补贴标准", "税率", "发票类型",
                "审批人", "审批流程", "付款方式", "报销条件", "开户行", "账号",
            }

            all_triples: list[dict[str, Any]] = []
            seen: set[tuple[str, str, str]] = set()

            # 多实体分桶均衡查询：保证每个实体都能分得三元组配额（防单实体霸占）
            limit_per_ent = max(2, limit // max(1, len(entities[:4])))
            for ent in entities[:4]:
                sql = f"""
                    SELECT subject, predicate, object, alignment_status, axiom_type
                    FROM knowledge_triples
                    WHERE review_status != 'REJECTED'
                      AND (subject LIKE ? OR object LIKE ?)
                    LIMIT 30
                """
                pat_ent = to_like_wildcard_pattern(ent)
                rows = conn.execute(sql, (pat_ent, pat_ent)).fetchall()
                scored: list[tuple[float, dict[str, Any]]] = []
                for r in rows:
                    key = (r["subject"], r["predicate"], r["object"])
                    if key in seen:
                        continue
                    score = 1.0
                    if r["predicate"] in priority_preds:
                        score += 3.0
                    if any(
                        p in r["predicate"]
                        for p in (
                            "时间", "日期", "地点", "费", "标准", "要求", "人数", "配备",
                            "金额", "限额", "税", "发票", "审批", "流程", "比例", "期限",
                            "生效", "截止", "条件", "方式", "职责", "部门",
                        )
                    ):
                        score += 2.0
                    if r["alignment_status"] in ("ALIGNED", "AUTO_ALIGNED"):
                        score += 1.5
                    scored.append((score, dict(r)))

                scored.sort(key=lambda x: x[0], reverse=True)
                for _, r in scored[:limit_per_ent]:
                    key = (r["subject"], r["predicate"], r["object"])
                    seen.add(key)
                    all_triples.append(r)

            return all_triples[:limit]
        except Exception as e:
            logger.debug(f"[GraphRAG] 提取图谱三元组异常: {e}")
            return []
        finally:
            conn.close()

    @classmethod
    def _parse_slice_index_and_doc_id(cls, content: str, fallback_uri: str, fallback_title: str) -> tuple[int, int, str]:
        """从标准化切片内容中提取 (slice_idx, slice_total, doc_id)。"""
        m_idx = re.search(r"【切片】(\d+)/(\d+)", content or "")
        s_idx = int(m_idx.group(1)) if m_idx else 0
        s_total = int(m_idx.group(2)) if m_idx else 0
        m_doc = re.search(r"\[指针：上一片=([a-zA-Z0-9_-]+)#", content or "")
        d_id = m_doc.group(1) if m_doc else KBStorageManager._derive_doc_id(fallback_uri, fallback_title)
        return s_idx, s_total, d_id

    @classmethod
    async def _fetch_doc_child_slices(
        cls,
        doc_id_or_title: str,
        expert_id: str | None = None,
        session_mounted_kbs: list[str] | None = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """在已授权的知识库中按 doc_id、remote_uri 或标题定位目标文档的全部 child 切片（按序号升序）。"""
        target_kbs = await cls.resolve_target_kbs(expert_id=expert_id, session_mounted_kbs=session_mounted_kbs)
        if not target_kbs:
            return False, []

        needle = (doc_id_or_title or "").strip()
        if not needle:
            return True, []

        def _query() -> list[dict[str, Any]]:
            for kb in target_kbs:
                db_path = KBStorageManager.get_version_db_path(kb["kb_id"], kb["version"])
                if not db_path.exists():
                    continue
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        """
                        SELECT id, remote_uri, title, section_path, content, authority_tier
                        FROM hybrid_chunks
                        WHERE chunk_role = 'child'
                          AND (content LIKE ? OR remote_uri LIKE ? OR title LIKE ?)
                        ORDER BY rowid ASC
                        """,
                        (f"%上一片={needle}#%", f"%{needle}%", f"%{needle}%"),
                    ).fetchall()
                    if not rows:
                        continue
                    items: list[dict[str, Any]] = []
                    for pos, r in enumerate(rows, 1):
                        txt = str(r["content"] or "")
                        s_idx, s_total, d_id = cls._parse_slice_index_and_doc_id(
                            txt, str(r["remote_uri"] or ""), str(r["title"] or "")
                        )
                        items.append({
                            "kb_id": kb["kb_id"],
                            "kb_name": kb["name"],
                            "version": kb["version"],
                            "doc_id": d_id,
                            "remote_uri": str(r["remote_uri"] or ""),
                            "title": str(r["title"] or ""),
                            "section_path": str(r["section_path"] or ""),
                            "slice_index": s_idx or pos,
                            "slice_total": s_total or len(rows),
                            "content": txt,
                            "authority_tier": r["authority_tier"] or 1,
                        })
                    items.sort(key=lambda x: x["slice_index"])
                    return items
                finally:
                    conn.close()
            return []

        matched = await asyncio.to_thread(_query)
        return True, matched

    @classmethod
    async def list_doc_toc(
        cls,
        doc_id_or_title: str,
        expert_id: str | None = None,
        session_mounted_kbs: list[str] | None = None,
    ) -> tuple[bool, str]:
        """列出指定文档全部 child 切片的【切片序号】、【章节】与前 40 字摘要目录。"""
        has_gate, slices = await cls._fetch_doc_child_slices(
            doc_id_or_title=doc_id_or_title,
            expert_id=expert_id,
            session_mounted_kbs=session_mounted_kbs,
        )
        if not has_gate:
            return False, "当前未挂载任何知识库，无法查阅文档目录。"
        if not slices:
            return True, f"未在已挂载知识库中找到标识为 `{doc_id_or_title}` 的文档切片。"

        first = slices[0]
        lines = [
            f"### 📑 文档切片目录：`{first['title']}` (doc_id=`{first['doc_id']}`, 共 {len(slices)} 片 | 来源库: {first['kb_name']} {first['version']})",
            "如需读取指定章节原文，请调用 `read_kb_slices(doc_id=\"{doc_id}\", start_index=..., end_index=...)`：\n".replace("{doc_id}", first["doc_id"]),
        ]
        for s in slices:
            body = KBStorageManager.strip_chunk_wrappers(s["content"]).replace("\n", " ").strip()
            lines.append(f"- **[{s['slice_index']}/{s['slice_total']}]** `{s['section_path']}` — {body[:45]}...")
        return True, "\n".join(lines)

    @classmethod
    async def read_doc_slices(
        cls,
        doc_id_or_title: str,
        start_index: int = 1,
        end_index: int = 5,
        expert_id: str | None = None,
        session_mounted_kbs: list[str] | None = None,
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        """按 (doc_id, start_index, end_index) 直接连续读取指定序号范围的切片完整正文。"""
        has_gate, slices = await cls._fetch_doc_child_slices(
            doc_id_or_title=doc_id_or_title,
            expert_id=expert_id,
            session_mounted_kbs=session_mounted_kbs,
        )
        if not has_gate:
            return False, "当前未挂载任何知识库，无法读取切片。", []
        if not slices:
            return True, f"未找到 doc_id=`{doc_id_or_title}` 的文档切片。", []

        s_idx = max(1, start_index or 1)
        e_idx = max(s_idx, min(end_index or s_idx, s_idx + 11))  # 单次最多连续读取 12 片
        selected = [s for s in slices if s_idx <= s["slice_index"] <= e_idx]
        if not selected:
            return True, f"文档 `{slices[0]['title']}` (doc_id=`{slices[0]['doc_id']}`) 共 {len(slices)} 片，范围 {s_idx}~{e_idx} 无命中。", []

        lines = [
            f"### 📖 连续切片精读：`{selected[0]['title']}` (doc_id=`{selected[0]['doc_id']}`, 切片 {s_idx}~{e_idx}/{selected[0]['slice_total']})\n"
        ]
        for s in selected:
            lines.append(s["content"])
            lines.append("")
        return True, "\n".join(lines), selected

    @classmethod
    async def list_mounted_docs(
        cls,
        expert_id: str | None = None,
        session_mounted_kbs: list[str] | None = None,
        filter_keyword: str = "",
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        """按当前专家挂载且已发布(is_published=1)的知识库，去重列出全部文档的 doc_id/文件名/范围/年份/切片数。"""
        target_kbs = await cls.resolve_target_kbs(
            expert_id=expert_id,
            session_mounted_kbs=session_mounted_kbs,
        )
        if not target_kbs:
            return False, "当前未挂载任何知识库，无法列出文档清单。", []

        published_kbs = [k for k in target_kbs if k.get("is_published", False)]
        if not published_kbs:
            return False, "当前挂载的知识库均未发布正式版本，已受发布隔离门控拦截。", []

        kw = (filter_keyword or "").strip().lower()

        def _scan() -> list[dict[str, Any]]:
            docs_out: list[dict[str, Any]] = []
            seen_doc_keys: set[tuple[str, str]] = set()
            for kb in published_kbs:
                db_path = KBStorageManager.get_version_db_path(kb["kb_id"], kb["version"])
                if not db_path.exists():
                    continue
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        """
                        SELECT remote_uri, title, doc_year, topic_cluster_id, content
                        FROM hybrid_chunks
                        WHERE chunk_role = 'child' OR chunk_role IS NULL
                        ORDER BY rowid ASC
                        """
                    ).fetchall()
                    grouped: dict[str, dict[str, Any]] = {}
                    for r in rows:
                        uri = str(r["remote_uri"] or "")
                        title = str(r["title"] or Path(uri).name or "未命名文档")
                        txt = str(r["content"] or "")
                        _, _, doc_id = cls._parse_slice_index_and_doc_id(txt, uri, title)
                        if doc_id not in grouped:
                            m_scope = re.search(r"【范围】([^|\n]+)", txt)
                            scope_name = (
                                m_scope.group(1).strip()
                                if m_scope
                                else str(r["topic_cluster_id"] or Path(title).stem or "通用").strip()
                            )
                            m_year = re.search(r"【年份】([^|\n]+)", txt)
                            year_str = (
                                m_year.group(1).strip()
                                if m_year
                                else (str(r["doc_year"]) if int(r["doc_year"] or 0) > 0 else "通用")
                            )
                            grouped[doc_id] = {
                                "kb_id": kb["kb_id"],
                                "kb_name": kb["name"],
                                "version": kb["version"],
                                "doc_id": doc_id,
                                "remote_uri": uri,
                                "title": title,
                                "scope_name": scope_name,
                                "year": year_str,
                                "slice_count": 0,
                            }
                        grouped[doc_id]["slice_count"] += 1

                    for d in grouped.values():
                        if kw and (
                            kw not in d["title"].lower()
                            and kw not in d["scope_name"].lower()
                            and kw not in d["year"].lower()
                        ):
                            continue
                        key = (kb["kb_id"], d["doc_id"])
                        if key not in seen_doc_keys:
                            seen_doc_keys.add(key)
                            docs_out.append(d)
                finally:
                    conn.close()
            return docs_out

        docs = await asyncio.to_thread(_scan)
        if not docs:
            return True, "当前已发布知识库中未匹配到任何文档记录。", []

        lines = [
            f"### 📚 已发布知识库文档清单（共 {len(docs)} 份文档，仅返回元信息不含正文）\n"
        ]
        for idx, d in enumerate(docs, 1):
            lines.append(
                f"{idx}. **【范围】{d['scope_name']}** | 【年份】{d['year']} | 【文件名】`{d['title']}` | "
                f"doc_id=`{d['doc_id']}` | 切片数={d['slice_count']}（来源库：{d['kb_name']} {d['version']}）"
            )
        lines.append("\n如需查看某份文档的详细章节结构，请调用 `list_kb_doc_toc(doc_id=\"...\")`。")
        return True, "\n".join(lines), docs
