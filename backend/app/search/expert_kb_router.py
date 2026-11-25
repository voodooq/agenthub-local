"""
AgentHub Local v1.5.9 数字专家知识隔离路由器与防幻觉熔断器 (Expert Knowledge Router)
规范参考: SPEC-1.5.9-EXPERT-KB-SERVICE
核心特性:
1. 专家专属知识库多对多绑定与权重优先融合；
2. 严格知识隔离 (strict_bound_only) 物理硬锁边界；
3. 严格依据作答防幻觉熔断 (strict_grounding): 未命中依据或触发 CRAG 低置信度时安全拒答；
4. 统一委托 MultiKBRetrievalRouter 执行 7 步检索流水线（CRAG、多实体对比、三元组/规则卡置顶与 [R]/[T]/[S] 引用编号）。
"""
from __future__ import annotations

from typing import Any

from ..core.logging_config import logger
from ..kb.manager import KBLifecycleManager
from ..repository.db import db_manager
from .multi_kb_router import MultiKBRetrievalRouter


GROUNDING_REFUSAL_TEMPLATE = (
    "抱歉，在当前专家挂载的现行业务规程与知识库中，暂未查询到与您问题相关的明确依据或条款，建议核对相关业务资料或联系管理员人工确认。"
)


class ExpertKnowledgeRouter:
    """数字专家知识底座隔离与防幻觉熔断路由器（纯调度壳，复用 MultiKBRetrievalRouter）。"""

    @classmethod
    async def get_expert_settings_and_kbs(cls, expert_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """获取专家元信息及绑定的知识库列表。"""
        expert_profile = None
        bound_kbs: list[dict[str, Any]] = []

        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM agent_profiles WHERE id = ?", (expert_id,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    expert_profile = dict(row)

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
        """为指定专家执行知识底座检索与防幻觉熔断。"""
        expert, bound_kbs = await cls.get_expert_settings_and_kbs(expert_id)
        if not expert:
            logger.warning(f"[ExpertKBRouter] 专家不存在: {expert_id}")
            return False, "", [], False

        isolation_mode = expert.get("kb_isolation_mode") or "strict_bound_only"
        strict_grounding = bool(expert.get("strict_grounding", 1))

        # 1. 严守发布治理红线：未发布知识库一律禁止前置读取
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

        if not target_kbs:
            if bound_kbs:
                unpub_names = "、".join([b.get("name") or b["kb_id"] for b in bound_kbs])
                logger.info(f"[ExpertKBRouter] 专家绑定的知识库 [{unpub_names}] 均未发布，不可对外提供检索，仅限测试环节。")
            return False, "", [], False

        # 2. 统一检索底座：直接委托 MultiKBRetrievalRouter 执行 7 步流水线
        max_top_k = max([int(t.get("top_k") or default_top_k) for t in target_kbs], default=default_top_k)
        target_ids = [t["kb_id"] for t in target_kbs]

        NON_RAG_INTENT_KEYWORDS = (
            "图表", "趋势图", "可视化", "折线图", "柱状图", "饼图", "甘特图",
            "整理", "汇总", "总结", "排版", "格式", "表格", "制图", "绘制", "直观呈现",
            "写代码", "脚本", "画出", "上述", "前面", "刚刚", "以上", "分析上述", "分析一下",
            "你好", "您好", "在吗", "帮我画", "呈现"
        )
        is_context_or_instruction = any(kw in query for kw in NON_RAG_INTENT_KEYWORDS)

        try:
            _, router_text, router_evidence = await MultiKBRetrievalRouter.search_mounted_kbs(
                query=query,
                expert_id=expert_id,
                session_mounted_kbs=target_ids,
                top_k=max_top_k,
                include_archived=False,
            )
        except Exception as e:
            logger.warning(f"[ExpertKBRouter] 委托 MultiKBRetrievalRouter 检索异常: {e}")
            return True, "", [], False

        is_crag_rejected = (not router_evidence) or ("[CRAG_NO_EVIDENCE]" in router_text)
        if is_crag_rejected:
            if is_context_or_instruction:
                logger.info(
                    f"[ExpertKBRouter] 用户提问为上下文/指令/可视化意图，跳过规程强阻断: {query[:35]}"
                )
                return True, "", [], False
            if strict_grounding:
                logger.info(
                    f"[ExpertKBRouter] CRAG 触发未收录拒答约束 (expert={expert_id}, query={query[:30]})"
                )
                weak_prompt = (
                    "> ⚠️ **[知识底座溯源提示]**：在当前绑定的规程资产库中，未检索到与您当前提问直接匹配的专门条款或数据。\n"
                    "> 请如实告知用户知识库暂无相关内容，优先核对上文对话记录或人工核验规程原文，严禁凭空虚构任何规则标准与数字金额。"
                )
                return True, weak_prompt, [], False

        return True, router_text, router_evidence, False
