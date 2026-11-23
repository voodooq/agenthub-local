"""
AgentHub Local v1.5.9 数字专家知识隔离路由器与防幻觉熔断器 (Expert Knowledge Router)
规范参考: SPEC-1.5.9-EXPERT-KB-SERVICE
核心特性:
1. 专家专属知识库多对多绑定与权重优先融合；
2. 严格知识隔离 (strict_bound_only) 物理硬锁边界；
3. 严格依据作答防幻觉熔断 (strict_grounding): 未命中依据或相关度低于 min_relevance_score 时安全拒答；
4. Small-to-Big 父切片回溯与动态系统当前时间时效仲裁。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger
from ..kb.manager import KBLifecycleManager
from ..kb.storage import KBStorageManager
from ..repository.db import db_manager
from .temporal_reranker import TemporalReranker
from .multi_kb_router import MultiKBRetrievalRouter


GROUNDING_REFUSAL_TEMPLATE = (
    "抱歉，在当前专家挂载的现行业务规程与知识库中，暂未查询到与您问题相关的明确依据或条款，建议核对相关业务资料或联系管理员人工确认。"
)


class ExpertKnowledgeRouter:
    """数字专家知识底座隔离与防幻觉熔断路由器。"""

    @classmethod
    async def get_expert_settings_and_kbs(cls, expert_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """获取专家元信息及绑定的知识库列表。"""
        expert_profile = None
        bound_kbs: list[dict[str, Any]] = []

        async with db_manager.get_connection() as conn:
            # 1. 查询专家配置
            async with conn.execute(
                "SELECT * FROM agent_profiles WHERE id = ?", (expert_id,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    expert_profile = dict(row)

            # 2. 查询绑定的知识库
            async with conn.execute(
                """
                SELECT ekb.kb_id, ekb.priority_weight, ekb.top_k_override,
                       kb.name, kb.active_version, kb.current_version, kb.embedding_model, kb.embedding_dim, kb.is_published
                FROM expert_knowledge_bases ekb
                JOIN knowledge_bases kb ON ekb.kb_id = kb.kb_id
                WHERE ekb.expert_id = ?
                ORDER BY ekb.priority_weight DESC, ekb.created_at ASC
                """,
                (expert_id,),
            ) as cur:
                rows = await cur.fetchall()
                bound_kbs = [dict(r) for r in rows]

        return expert_profile, bound_kbs

    @classmethod
    async def search_for_expert(
        cls,
        expert_id: str,
        query: str,
        session_mounted_kbs: list[str] | None = None,
        default_top_k: int = 6,
        is_external_api: bool = False,
    ) -> tuple[bool, str, list[dict[str, Any]], bool]:
        """为指定专家执行知识底座检索与防幻觉熔断。

        Args:
            expert_id: 目标专家唯一标识
            query: 用户提问
            session_mounted_kbs: 会话临时外挂知识库
            default_top_k: 默认召回候选切片数
            is_external_api: 是否为对外开放的第三方 API Gateway 调用。
                             若是对外 API，严格限制未发布版本不可对外调用；
                             若是 AgentHub 本地系统内部（沙盒内部与外部聊天 @专家 协同），
                             两个通道均正常支持检索绑定的知识库。

        Returns:
            tuple[bool, str, list[dict], bool]:
                - has_knowledge: 是否配置并尝试检索知识库
                - prompt_or_refusal: 格式化后的上下文注入提示词或安全拒答模板
                - citations: 命中的依据切片列表
                - is_grounding_blocked: 是否触发了 strict_grounding 安全拒答熔断
        """
        expert, bound_kbs = await cls.get_expert_settings_and_kbs(expert_id)
        if not expert:
            logger.warning(f"[ExpertKBRouter] 专家不存在: {expert_id}")
            return False, "", [], False

        isolation_mode = expert.get("kb_isolation_mode") or "strict_bound_only"
        strict_grounding = bool(expert.get("strict_grounding", 1))
        min_relevance_score = float(expert.get("min_relevance_score") or 0.42)

        # 1. 确定最终检索的目标知识库清单
        # 严守发布治理红线：未发布知识库仅限后台“知识库测试 (Playground)”使用，
        # 无论前台本地沙盒、IM 渠道还是外部 API，一律禁止读取未发布知识库！
        target_kbs: list[dict[str, Any]] = []

        if bound_kbs:
            for b in bound_kbs:
                if not b.get("is_published"):
                    logger.info(
                        f"[ExpertKBRouter] 知识库 [{b.get('name')}] 未发布（处于草稿/测试状态），已拦截专家检索"
                    )
                    continue
                target_kbs.append({
                    "kb_id": b["kb_id"],
                    "name": b.get("name") or b["kb_id"],
                    "version": b.get("current_version") or b.get("active_version") or "v1.0.0",
                    "priority_weight": float(b.get("priority_weight") or 1.0),
                    "top_k": int(b.get("top_k_override") or default_top_k),
                })

        # 若允许临时外挂知识库 (bound_plus_at) 且传入了 session_mounted_kbs
        if isolation_mode == "bound_plus_at" and session_mounted_kbs:
            existing_ids = {t["kb_id"] for t in target_kbs}
            all_kbs = await KBLifecycleManager.list_knowledge_bases()
            kb_map = {k["kb_id"]: k for k in all_kbs}
            for kid in session_mounted_kbs:
                if kid not in existing_ids and kid in kb_map:
                    k = kb_map[kid]
                    if not k.get("is_published"):
                        logger.info(f"[ExpertKBRouter] 外挂知识库 [{k.get('name')}] 未发布，已拦截专家检索")
                        continue
                    target_kbs.append({
                        "kb_id": kid,
                        "name": k.get("name") or kid,
                        "version": k.get("current_version") or k.get("active_version") or "v1.0.0",
                        "priority_weight": 0.9,
                        "top_k": default_top_k,
                    })

        # 铁律：专家未绑定任何知识库时，返回纯净上下文（不瞎搜）
        if not target_kbs:
            if bound_kbs:
                unpub_names = "、".join([b.get("name") or b["kb_id"] for b in bound_kbs])
                logger.info(f"[ExpertKBRouter] 专家绑定的知识库 [{unpub_names}] 均未发布，不可对外提供检索，仅限测试环节。")
            return False, "", [], False

        # 2. 并行检索绑定的独立 index.db
        all_candidates: list[dict[str, Any]] = []
        for tkb in target_kbs:
            kid = tkb["kb_id"]
            ver = tkb["version"]
            pw = tkb["priority_weight"]
            kname = tkb["name"]
            tk = tkb["top_k"]

            db_path = KBStorageManager.get_version_db_path(kid, ver)
            if not db_path.exists():
                continue

            try:
                chunks = await MultiKBRetrievalRouter._search_single_index_db(
                    db_path=db_path,
                    query=query,
                    top_k=tk,
                    kb_id=kid,
                    kb_name=kname,
                    version=ver,
                    priority_weight=pw,
                    include_archived=False,
                )
                all_candidates.extend(chunks)
            except Exception as e:
                logger.warning(f"[ExpertKBRouter] 专家知识库 [{kname}] 检索异常: {e}")

        # 3. 结果时效重排 (TemporalReranker) 仲裁最新生效版
        reranked = TemporalReranker.rerank_chunks(
            chunks=all_candidates,
            current_query=query,
            now_dt=datetime.now(),
        )

        # 4. 意图自适应判定与溯源评估 (Strict Grounding Evaluation)
        NON_RAG_INTENT_KEYWORDS = (
            "图表", "趋势图", "可视化", "折线图", "柱状图", "饼图", "甘特图",
            "整理", "汇总", "总结", "排版", "格式", "表格", "制图", "绘制", "直观呈现",
            "写代码", "脚本", "画出", "上述", "前面", "刚刚", "以上", "分析上述", "分析一下",
            "你好", "您好", "在吗", "帮我画", "呈现"
        )
        is_context_or_instruction = any(kw in query for kw in NON_RAG_INTENT_KEYWORDS)

        top_score = reranked[0].get("final_score", 0.0) if reranked else 0.0
        is_grounding_weak = (not reranked) or (top_score < min_relevance_score)

        if is_grounding_weak:
            if is_context_or_instruction:
                logger.info(
                    f"[ExpertKBRouter] 用户提问为上下文/指令/可视化意图，跳过规程强阻断: {query[:35]}"
                )
                # 不阻断，不返回拒答模板，让模型正常根据上文多轮对话或技能作答
                return True, "", [], False
            elif strict_grounding:
                logger.info(
                    f"[ExpertKBRouter] 规程检索置信度偏低: Top-1 得分 {top_score:.3f} < 阈值 {min_relevance_score} (expert={expert_id}, query={query[:30]})"
                )
                # 不再直接杀死会话，而是返回软性约束提示，由 runtime 注入给大模型如实说明
                weak_prompt = (
                    "> ⚠️ **[知识底座溯源提示]**：在当前绑定的规程资产库中，未检索到与您当前提问直接匹配的专门条款（最高置信得分偏低）。\n"
                    "> 请优先核对上文对话记录或人工核验规程原文，严禁凭空虚构任何规则标准与数字金额。"
                )
                return True, weak_prompt, [], False

        # 5. Small-to-Big 上下文回溯与还原
        final_top: list[dict[str, Any]] = []
        seen_parents: set[str] = set()

        for c in reranked:
            pid = str(c.get("parent_chunk_id") or "").strip()
            kid = str(c.get("kb_id") or "").strip()
            ver = str(c.get("version") or "").strip()

            if kid and ver:
                db_path = KBStorageManager.get_version_db_path(kid, ver)
                if pid and pid not in seen_parents and db_path.exists():
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
                            c_copy["is_expanded_parent"] = True
                            seen_parents.add(pid)
                            final_top.append(c_copy)
                            if len(final_top) >= default_top_k:
                                break
                            continue
                    except Exception as p_err:
                        logger.debug(f"[Small-to-Big] 专家检索父切片回溯失败: {p_err}")

            raw_chunk_hash = c.get("id") or c.get("title")
            chunk_hash = str(raw_chunk_hash or "").strip()
            if chunk_hash and chunk_hash not in seen_parents:
                seen_parents.add(chunk_hash)
                final_top.append(c)
                if len(final_top) >= default_top_k:
                    break

        # 6. 构造高保真证据链与当前系统真实时间
        current_date_str = datetime.now().strftime("%Y年%m月%d日")
        lines = [
            f"> 💡 **[数字专家知识底座与当前时间基准]**：当前北京时间为 **{current_date_str}**。以下内容为专家已绑定的规程资产，若命中存在历史年份版本更迭，系统已按最新生效日期加权，请严格以最新规程为准作答。\n",
            f"### 专家专属依据库检索结果 (命中数: {len(final_top)}):",
        ]
        for idx, c in enumerate(final_top, start=1):
            tier_label = "正式规程(Tier 1)" if c.get("authority_tier") == 1 else "常规资料(Tier 2)"
            eff_str = f", 生效日期: {c.get('effective_date')}" if c.get('effective_date') else ""
            latest_badge = " [最新版]" if c.get("is_latest_in_cluster") == 1 else ""
            lines.append(
                f"\n[{idx}] 来源: 【{c['kb_name']} {c['version']}】 - {c['title']}{latest_badge} ({tier_label}{eff_str})"
            )
            lines.append(f"内容:\n{c['content']}")

        return True, "\n".join(lines), final_top, False

