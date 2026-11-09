"""
智能体双层循环编排运行时 (AgentRuntime)
核心架构：
- 外层循环 (Inter-Turn): 会话级多轮对话演进，维护 turn_id 递增、场景模式切换与回合后异步记忆提炼
- 内层循环 (Intra-Turn ReAct): 单次提问内的多步“推理 -> 工具拦截 -> 安全审批/执行 -> Observation入库 -> 再次推理”闭环
"""
import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

from ..core.logging_config import logger
from ..repository.conversation_repository import ConversationRepository
from ..repository.message_repository import MessageRepository
from ..repository.search_repository import SearchRepository
from ..service.engine_pool import engine_pool
from ..service.audit_service import audit_service
from ..service.attachment_service import attachment_service
from ..memory.fact_manager import fact_manager
from ..memory.ledger import event_ledger
from ..memory.long_term import long_term_memory
from ..memory.policy_guard import MemoryPolicyGuard
from ..memory.session_summary import session_summary_manager
from ..memory.workspace_fts import WorkspaceFTSEngine
from ..extensions.skill_loader import skill_loader
from ..extensions.mcp_manager import mcp_manager
from .shell_executor import shell_executor
from .tool_protocol import ToolProtocolAdapter
from .context_builder import ContextBuilder
from .ui_operator import ui_operator
from ..repository.loop_run_repository import TaskLoopRepository
from ..skills.experience_distiller import ExecutionTrace, experience_distiller


class AgentRuntime:
    """微内核智能体双层循环编排器"""

    DEFAULT_REACT_STEPS = 16
    MAX_REACT_STEPS = 40  # 任务深度自愈与多步推进时的绝对动态扩展上限

    @staticmethod
    async def _ingest_confirmed_facts(
        *,
        confirmed_facts: list[Any] | None,
        workspace_id: str,
        conversation_id: str,
        turn_id: int,
        incognito: bool,
        scene_mode: str,
    ) -> list[dict[str, Any]]:
        """
        将前端交互对齐卡提交的结构化确认事实直接写入 event_ledger 与 memory_facts（0 额外 LLM Token）。
        """
        if incognito or scene_mode == "oneshot" or not confirmed_facts:
            return []

        normalized_items: list[dict[str, str]] = []
        for item in confirmed_facts:
            d = item if isinstance(item, dict) else item.model_dump()
            f_key = str(d.get("fact_key") or "").strip()
            f_val = str(d.get("fact_value") or "").strip()
            if not f_key or not f_val:
                continue
            normalized_items.append({
                "fact_key": f_key,
                "fact_value": f_val,
                "category": str(d.get("category") or "biz_rule").strip(),
                "workspace_scope": str(d.get("workspace_scope") or "workspace").strip().lower(),
            })

        if not normalized_items:
            return []

        evt = await event_ledger.append_event(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            event_type="inquiry_confirmed",
            actor="user",
            payload={"facts": normalized_items},
            incognito=False,
        )
        source_evt_id = evt["id"] if evt else None

        saved_facts: list[dict[str, Any]] = []
        for item in normalized_items:
            target_ws = "GLOBAL" if item["workspace_scope"] == "global" else workspace_id
            try:
                res = await fact_manager.upsert_fact(
                    fact_key=item["fact_key"],
                    fact_value=item["fact_value"],
                    workspace_id=target_ws,
                    category=item["category"],
                    source_type="inquiry",
                    source_event_id=source_evt_id,
                    incognito=False,
                    scene_mode=scene_mode,
                    verified=True,
                )
                if res.get("fact"):
                    saved_facts.append(res["fact"])
            except Exception as e:
                logger.warning(f"[AgentRuntime] 对齐卡确认事实落盘失败 ({item['fact_key']}): {e}")

        return saved_facts

    @staticmethod
    async def _execute_search_memory_history(
        *,
        query: str,
        scope: str = "all",
        limit: int = 5,
        workspace_id: str = "default",
        target_cwd: str | None = None,
        incognito: bool = False,
    ) -> str:
        """
        执行按需 FTS5 历史对话、事实变更历史与工作区文档/源码联合召回（全量结果均经隐私脱敏）。
        """
        clean_q = (query or "").strip()
        if not clean_q:
            return "Error: search_memory_history 参数 query 不能为空。"

        clean_scope = (scope or "all").strip().lower()
        if clean_scope not in ("messages", "facts_history", "workspace", "all"):
            clean_scope = "all"
        safe_limit = max(1, min(int(limit or 5), 20))

        sections: list[str] = []

        # 1. 历史消息 FTS5 BM25（无痕模式跳过）
        if not incognito and clean_scope in ("messages", "all"):
            msg_hits = await SearchRepository.search_messages(clean_q, limit=safe_limit)
            if msg_hits:
                lines = ["#### 💬 历史会话消息召回 (messages_fts)"]
                for hit in msg_hits[:safe_limit]:
                    raw_snip = re.sub(r"<[^>]+>", "", str(hit.get("highlighted_snippet") or hit.get("content") or ""))
                    safe_snip, _ = MemoryPolicyGuard.sanitize_text(raw_snip[:220])
                    title = str(hit.get("conversation_title") or hit.get("conversation_id") or "")
                    lines.append(
                        f"- **[{hit.get('role', 'user')}]** (会话 `{title}`, {hit.get('created_at', '')}): {safe_snip}"
                    )
                sections.append("\n".join(lines))

        # 2. 双时间轴历史事实 + 非结构化长期记忆（无痕模式跳过）
        if not incognito and clean_scope in ("facts_history", "all"):
            fact_hits = await fact_manager.search_history(clean_q, workspace_id=workspace_id, limit=safe_limit)
            lt_hits_all = await long_term_memory.list_all()
            lt_matched = [
                m for m in lt_hits_all
                if clean_q.lower() in str(m.get("content") or "").lower()
                or clean_q.lower() in str(m.get("category") or "").lower()
            ][:safe_limit]

            if fact_hits or lt_matched:
                lines = ["#### 📜 确定性事实历史与长期画像 (memory_facts & long_term_memories)"]
                for f in fact_hits:
                    safe_val, _ = MemoryPolicyGuard.sanitize_text(str(f.get("fact_value") or ""))
                    status_tag = "当前生效" if f.get("is_active") else f"已归档 @{f.get('valid_to')}"
                    lines.append(
                        f"- `[{f.get('workspace_id')}:{f.get('fact_key')}]` = `{safe_val}` ({status_tag}, 来源: {f.get('source_type')})"
                    )
                for m in lt_matched:
                    safe_c, _ = MemoryPolicyGuard.sanitize_text(str(m.get("content") or ""))
                    lines.append(f"- `[LTM:{m.get('category')}]` {safe_c}")
                sections.append("\n".join(lines))

        # 3. 当前工作区代码与文档 FTS5（需物理目录存在）
        if target_cwd and clean_scope in ("workspace", "all"):
            cwd_path = Path(target_cwd)
            if cwd_path.exists() and cwd_path.is_dir():
                def _run_ws_search() -> list[dict[str, Any]]:
                    index_db = cwd_path / ".agenthub" / "index.db"
                    if not index_db.exists():
                        return []
                    engine = WorkspaceFTSEngine(str(cwd_path))
                    return engine.search(clean_q, limit=safe_limit)

                try:
                    ws_hits = await asyncio.to_thread(_run_ws_search)
                except Exception as e:
                    logger.debug(f"[AgentRuntime] WorkspaceFTSEngine 检索跳过: {e}")
                    ws_hits = []

                if ws_hits:
                    lines = ["#### 📁 工作区文档/源码片段召回 (workspace_fts)"]
                    for wh in ws_hits[:safe_limit]:
                        raw_s = re.sub(r"</?b>", "", str(wh.get("snippet") or ""))
                        safe_s, _ = MemoryPolicyGuard.sanitize_text(raw_s[:240])
                        lines.append(f"- `{wh.get('rel_path')}`: {safe_s}")
                    sections.append("\n".join(lines))

        if not sections:
            return f"未在范围 `{clean_scope}` 内检索到与 '{clean_q}' 相关的历史记忆或工作区片段。"

        return f"### 🔍 按需记忆与历史溯源检索结果 (`query=\"{clean_q}\"`)\n\n" + "\n\n".join(sections)

    @staticmethod
    async def _run_post_task_distillation(
        *,
        workspace_id: str,
        conversation_id: str,
        loop_id: str | None,
        scene_mode: str,
        incognito: bool,
        user_goal: str,
        active_skill_ids: list[str],
        recovered_error_count: int,
        total_tool_steps: int,
        circuit_breaker_triggered: bool,
        final_summary: str,
        produced_artifacts: list[str],
        workspace_root: str | None,
    ) -> dict[str, Any] | None:
        """汇总本轮执行轨迹并调用 ExperienceDistiller 评估是否产生经验自进化提议"""
        if incognito or scene_mode != "agent" or circuit_breaker_triggered:
            return None
        try:
            intervention_res = await event_ledger.list_events(
                conversation_id=conversation_id,
                loop_id=loop_id,
                event_type="user_intervention",
                limit=5,
            )
            had_intervention = bool(intervention_res.get("total", 0) > 0)
            trace = ExecutionTrace(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                loop_id=loop_id,
                scene_mode=scene_mode,
                incognito=incognito,
                user_goal=user_goal,
                active_skill_ids=list(dict.fromkeys(active_skill_ids)),
                recovered_error_count=recovered_error_count,
                had_user_intervention=had_intervention,
                total_tool_steps=total_tool_steps,
                circuit_breaker_triggered=circuit_breaker_triggered,
                final_success=True,
                final_summary=final_summary,
                produced_artifacts=list(dict.fromkeys(produced_artifacts)),
                workspace_root=workspace_root,
            )
            return await experience_distiller.on_task_complete(trace)
        except Exception as e:
            logger.debug(f"[AgentRuntime] Post-Task 经验提炼跳过: {e}")
            return None

    async def _persist_approved_plan(
        self,
        workspace_root: str | None,
        approved_steps: list[dict[str, Any]],
        goal_text: str,
    ) -> None:
        """将用户审批通过的步骤清单落盘写入工作区 .agenthub/scratchpad.md 并同步工作记忆"""
        if not workspace_root:
            return
        try:
            hub_dir = Path(workspace_root) / ".agenthub"
            hub_dir.mkdir(parents=True, exist_ok=True)
            scratch_file = hub_dir / "scratchpad.md"

            lines = [
                "# 任务自主推演工作草稿纸 (Task Scratchpad)",
                f"\n**目标**: {goal_text}\n",
                "## 经用户审批确认的实施步骤清单 (Step-by-Step Task List)\n",
            ]
            for idx, item in enumerate(approved_steps, 1):
                step_no = item.get("step", idx)
                title = item.get("title") or item.get("label") or f"步骤 {step_no}"
                tool_lvl = item.get("tool_level", "L1")
                lines.append(f"- [ ] 步骤 {step_no}: {title} `[{tool_lvl}]`")

            lines.append("\n---\n*提示: Agent 将严格按照上述经审批的步骤推进并更新勾选状态。*\n")
            scratch_file.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"[AgentRuntime] 已成功将审批步骤持久化至 {scratch_file}")
        except Exception as e:
            logger.warning(f"[AgentRuntime] 持久化审批步骤至 scratchpad.md 失败: {e}")

    async def chat_stream(
        self,
        conversation_id: str | None,
        user_message: str,
        model_name: str = "4 Argon",
        thinking_level: str = "high",
        system_override: str | None = None,
        temperature: float = 0.7,
        context_rounds: int = 10,
        attachments: list[Any] | None = None,
        scene_mode: str | None = None,
        incognito: bool | None = None,
        workspace_id: str | None = None,
        execution_strategy: str = "direct",
        clarify_mode: bool = False,
        approved_plan_steps: list[dict[str, Any]] | None = None,
        confirmed_facts: list[Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        执行双层循环流式推理与工具编排，向外输出标准化 SSE 字典事件流。
        """
        conv_id = conversation_id
        is_new_conv = False
        effective_user_message = (user_message or "").strip()
        first_att_name = ""
        if attachments:
            first_att = attachments[0]
            first_att_name = str(first_att.get("name") if isinstance(first_att, dict) else getattr(first_att, "name", "") or "").strip()
        if not effective_user_message and attachments:
            effective_user_message = "请结合附带的文件或图片内容进行分析解答。"
        user_message = effective_user_message or user_message
        if first_att_name and (not (user_message or "").strip() or user_message == "请结合附带的文件或图片内容进行分析解答。"):
            short_title = f"[附件] {first_att_name}"[:24]
        else:
            short_title = (user_message or "").strip().split("\n")[0][:24] or "新会话"
        ws_id = workspace_id or "default"
        target_cwd = None
        try:
            from ..repository.workspace_repository import WorkspaceRepository
            ws = await WorkspaceRepository.get_by_id(ws_id)
            if ws and ws.get("root_path"):
                target_cwd = ws["root_path"]
        except Exception:
            pass

        # 1. 确保会话存在并同步最新场景/无痕状态与工作区
        if not conv_id:
            conv_id = str(uuid.uuid4())
            is_new_conv = True
            await ConversationRepository.create({
                "id": conv_id,
                "title": short_title,
                "model_name": model_name,
                "scene_mode": scene_mode or "agent",
                "incognito": 1 if incognito else 0,
                "workspace_id": ws_id,
                "current_cwd": target_cwd,
            })
        else:
            conv_existing = await ConversationRepository.get_by_id(conv_id)
            if not conv_existing:
                is_new_conv = True
                await ConversationRepository.create({
                    "id": conv_id,
                    "title": short_title,
                    "model_name": model_name,
                    "scene_mode": scene_mode or "agent",
                    "incognito": 1 if incognito else 0,
                    "workspace_id": ws_id,
                    "current_cwd": target_cwd,
                })
            elif scene_mode is not None or incognito is not None:
                await ConversationRepository.update_agent_state(
                    conv_id,
                    scene_mode=scene_mode,
                    incognito=incognito,
                )

        conv_obj = await ConversationRepository.get_by_id(conv_id)
        active_scene = (conv_obj.scene_mode if conv_obj else (scene_mode or "agent")) or "agent"
        active_incognito = bool(conv_obj.incognito) if conv_obj else bool(incognito)
        ws_id = (getattr(conv_obj, "workspace_id", None) if conv_obj else None) or ws_id

        # 2. 计算当前大轮次编号 (turn_id) 并率先落盘用户提问（带附件真实绝对路径锚点，防止多步/跨轮路径失忆）
        max_turn = await MessageRepository.get_max_turn_id(conv_id)
        current_turn_id = max_turn + 1
        persisted_user_content = attachment_service.append_attachments_anchor(user_message, attachments)

        await MessageRepository.save_message(
            conversation_id=conv_id,
            role="user",
            content=persisted_user_content,
            turn_id=current_turn_id,
        )
        await ConversationRepository.touch(conv_id)

        is_planning_phase = (
            active_scene == "agent"
            and execution_strategy == "plan_first"
            and not approved_plan_steps
        )

        # 2.5 非无痕且非单次模式下：追加 user_input 账本事件并执行对齐卡 confirmed_facts 0-Token 直通入库
        if not active_incognito and active_scene != "oneshot":
            try:
                await event_ledger.append_event(
                    workspace_id=ws_id,
                    conversation_id=conv_id,
                    turn_id=current_turn_id,
                    event_type="user_input",
                    actor="user",
                    payload={
                        "message": (user_message or "")[:1200],
                        "scene_mode": active_scene,
                        "execution_strategy": execution_strategy,
                    },
                    incognito=False,
                )
            except Exception as evt_err:
                logger.debug(f"[AgentRuntime] 记录 user_input 账本跳过: {evt_err}")

            if confirmed_facts:
                await self._ingest_confirmed_facts(
                    confirmed_facts=confirmed_facts,
                    workspace_id=ws_id,
                    conversation_id=conv_id,
                    turn_id=current_turn_id,
                    incognito=active_incognito,
                    scene_mode="plan_first" if is_planning_phase else active_scene,
                )

        # 3. 推送第一条会话初始化握手事件
        yield {
            "type": "conv_init",
            "conversation_id": conv_id,
            "is_new": is_new_conv,
            "title": short_title,
            "turn_id": current_turn_id,
            "scene_mode": active_scene,
            "incognito": active_incognito,
        }

        PLANNING_SAFE_TOOLS = {
            "read_local_file",
            "search_memory_history",
            "search_skill_knowledge",
            "load_skill_detail",
            "web_fetch",
            "update_working_memory",
        }

        # 4. 准备当前场景可用的工具集合
        active_tools: list[dict[str, Any]] = []
        if active_scene == "agent":
            all_builtin = ToolProtocolAdapter.get_builtin_tools()
            if is_planning_phase:
                # 🛡️ 物理只读硬门控：规划态仅向模型暴露 L0 只读与记忆探测工具，写盘与终端完全隔离
                active_tools.extend([t for t in all_builtin if t.get("name") in PLANNING_SAFE_TOOLS])
            else:
                active_tools.extend(all_builtin)
                try:
                    mcp_tools = await mcp_manager.get_enabled_tools()
                    active_tools.extend(mcp_tools)
                except Exception as e:
                    logger.warning(f"加载 MCP 工具集异常: {e}")

        # 若用户已批准方案，将执行步骤持久化写入工作区 .agenthub/scratchpad.md
        if approved_plan_steps and target_cwd:
            await self._persist_approved_plan(
                workspace_root=target_cwd,
                approved_steps=approved_plan_steps,
                goal_text=user_message.strip().split("\n")[0][:120],
            )

        # 规划态步数收敛限制为最多 4 步，用于探测目录和输出方案卡片
        current_max_steps = (4 if is_planning_phase else self.DEFAULT_REACT_STEPS) if active_scene == "agent" else 1
        final_account: str | None = None
        accumulated_visible_text = ""
        effective_model_name = model_name
        last_step_error_info: dict[str, Any] | None = None
        consecutive_stalls = 0
        allow_step_extension = not is_planning_phase
        command_history: list[dict[str, Any]] = []
        active_skill_ids: list[str] = []
        recovered_error_count = 0
        total_tool_steps = 0
        circuit_breaker_triggered = False
        produced_artifacts: list[str] = []

        # 4.5 关联工作区长程自主领航舱 (TaskLoopRunner 闭环驱动)
        active_loop_obj = None
        tracked_loop_id: str | None = None
        if active_scene == "agent" and ws_id and not is_planning_phase:
            try:
                active_loop_obj = await TaskLoopRepository.get_active_loop_by_workspace(ws_id, include_completed=False)
                if not active_loop_obj:
                    # 兜底自动初始化当前工作区的自主执行记录
                    active_loop_obj = await TaskLoopRepository.create_loop(
                        workspace_id=ws_id,
                        conversation_id=conv_id,
                        goal_prompt=user_message.strip().split("\n")[0][:120] or "执行自主推演任务",
                        max_iterations=10,
                    )
                if active_loop_obj:
                    tracked_loop_id = active_loop_obj.id
            except Exception as e:
                logger.warning(f"初始化任务自主领航舱实例异常: {e}")

        # 5. 进入单轮内 ReAct 思考与工具执行循环 (Intra-Turn Adaptive Loop)
        step_idx = 0
        while step_idx < current_max_steps:
            # 步数自适应动态扩展检查：当步数接近预算、任务仍在深度自愈且未触发死循环熔断时，自动扩展步数
            if active_scene == "agent" and allow_step_extension and step_idx >= current_max_steps - 2:
                if current_max_steps < self.MAX_REACT_STEPS:
                    current_max_steps = min(current_max_steps + 8, self.MAX_REACT_STEPS)
                    yield {
                        "type": "step_extend",
                        "step": step_idx + 1,
                        "max_steps": current_max_steps,
                        "message": f"任务正在深度自主推演与闭环攻坚中，已自动扩展执行预算至第 {current_max_steps} 步...",
                    }

            ctx = await ContextBuilder.build(
                conversation_id=conv_id,
                current_turn_id=current_turn_id,
                user_query=user_message,
                tools=active_tools if active_scene == "agent" else None,
                context_rounds_override=0 if active_scene == "oneshot" else context_rounds,
                base_system_prompt=system_override,
                execution_strategy=execution_strategy,
                clarify_mode=clarify_mode,
                approved_plan_steps=approved_plan_steps,
            )

            # 首步向前端推送命中的长期记忆溯源、活跃确定性事实视图与当前任务草稿纸
            if step_idx == 0:
                matched_sk_id = str(ctx.get("matched_skill_id") or "").strip()
                if matched_sk_id and matched_sk_id not in active_skill_ids:
                    active_skill_ids.append(matched_sk_id)
                if ctx.get("active_facts"):
                    yield {
                        "type": "fact_refs",
                        "facts": ctx["active_facts"],
                    }
                if ctx.get("referenced_memories"):
                    yield {
                        "type": "memory_refs",
                        "memories": [
                            {
                                "id": m["id"],
                                "category": m["category"],
                                "content": m["content"],
                                "confidence": m["confidence"],
                            }
                            for m in ctx["referenced_memories"]
                        ],
                    }
                if ctx.get("working_memory"):
                    yield {
                        "type": "scratchpad_update",
                        "working_memory": ctx["working_memory"],
                    }

            # 构造本步传给算力池的 pre_built_history 与 step_prompt
            history_for_step = list(ctx.get("history_messages", []))
            if step_idx == 0:
                # 剥离刚刚入库的当前用户消息，交由 execute_stream_with_failover 作为最新 user_prompt 附带附件发送
                if history_for_step and history_for_step[-1].get("role") == "user":
                    history_for_step = history_for_step[:-1]
                step_prompt = user_message
                step_attachments = attachments
            else:
                if last_step_error_info:
                    if last_step_error_info.get("circuit_breaker"):
                        failed_c = last_step_error_info.get("failed_cmd", "")
                        step_prompt = (
                            f"[🚨 语义级死循环物理熔断已触发 - 强制切换执行路径]:\n"
                            f"系统检测到你连续多次尝试执行完全相同的失败命令: `{failed_c}`！\n"
                            f"【死循环熔断强制铁律】:\n"
                            f"1. 严禁再次尝试该相同命令！自动步数扩展权限已被系统安全锁定；\n"
                            f"2. 必须立即切换为完全不同的工具或实现路径（例如: 换用 Python 脚本操作文件、检查工作目录或权限）；\n"
                            f"3. 若确认该问题属于外部不可解物理障碍（如系统内核锁死文件、缺少系统管理员权限、网络断开），请立即停止发起任何工具调用，直接向用户详细陈述排查事实与物理卡点，请求人工支持！"
                        )
                    else:
                        err_msg = last_step_error_info.get("summary", "执行返回异常或报错")
                        step_prompt = (
                            f"[🚨 第一性原理自愈与深度推演调度]:\n"
                            f"上一步执行返回异常: {err_msg}。\n"
                            f"【自主攻坚铁律 - 绝不允许卡住或直接退出】:\n"
                            f"1. 仔细阅读上方工具返回的真实报错（特别是诊断日志与 STDERR 堆栈）；\n"
                            f"2. 深度分析报错根因（例如: 若出现 `.Name : The term '.Name' is not recognized`，说明在 PowerShell 双引号内 `$_` 被外层提前展开丢失，应在单引号内书写脚本块，或改用标准的 Python 脚本 `python -c ...` 执行扫描与清理；若缺少依赖或文件请检查相对路径与 `--prefix`）；\n"
                            f"3. 紧扣用户最终目标，自己构思可靠的替代解决方案；\n"
                            f"4. 严禁向用户复述错误或只输出声明！所有工具调用均会自动确认执行，必须立即输出标准 `<tool_call>` 标签执行具体命令纠偏自愈，直到彻底达成目标：\n"
                            f"```xml\n"
                            f'<tool_call name="execute_local_command">\n'
                            f'{{"command": "修正后的终端命令或 Python 替代执行脚本", "reason": "分析日志与语法根因后的自愈攻坚操作"}}\n'
                            f"</tool_call>\n"
                            f"```"
                        )
                else:
                    step_prompt = (
                        "[系统工作流推进]: 上述工具已成功执行并返回真实结果（见上方记录）。\n"
                        "请基于上述真实结果深度思考并推进实现用户的最终目标：\n"
                        "- 若任务尚未彻底达成（例如刚完成扫描，仍需进一步分析、删除无效项、修改文件或验证），请立即输出下一个 `<tool_call>` 继续执行；\n"
                        "- 只有当用户的目标已经物理完成并验证通过时，才向用户输出最终完整的成果解答。"
                    )

                # 轮内防失忆与工作草稿纸联动提醒：当步数进入第 4 步及以后，早期输出开始微缩折叠
                if active_scene == "agent" and step_idx >= 3:
                    step_prompt += (
                        "\n\n💡 [系统防失忆提醒]: 前期步骤的详细终端输出已按滑动窗口微缩折叠。"
                        "若你查到了关键路径、失效列表或状态，请务必通过 `update_working_memory` 写入草稿纸，以防多步汇总时遗忘！"
                    )

                step_attachments = None

            step_raw_text = ""
            encountered_stream_error = False

            async for event in engine_pool.execute_stream_with_failover(
                conversation_id=conv_id,
                user_prompt=step_prompt,
                model_name=model_name,
                thinking_level=thinking_level,
                system_override=ctx.get("system_instruction"),
                temperature=temperature,
                work_dir=ctx.get("current_cwd"),
                context_rounds=context_rounds,
                attachments=step_attachments,
                pre_built_history=history_for_step,
            ):
                etype = event.get("type")
                if etype == "model_downgrade":
                    effective_model_name = event.get("current_model", effective_model_name)
                    yield event
                elif etype == "stream_reset":
                    # 网络中途断流重连接力：立即清空当前步骤已累加的半截文本，与前端气泡绝对对齐
                    step_raw_text = ""
                    yield event
                elif etype == "chunk":
                    chunk_str = event.get("content", "")
                    step_raw_text += chunk_str
                    final_account = event.get("account", final_account)
                    yield event
                elif etype == "done":
                    step_raw_text = event.get("full_content", step_raw_text)
                    final_account = event.get("account", final_account)
                elif etype == "error":
                    encountered_stream_error = True
                    yield event
                    break
                else:
                    # 透传 start / relay / tool_request 等事件
                    yield event

            if encountered_stream_error:
                return

            # 6. 解析本步输出是否包含工具调用 (<tool_call>)
            clean_text, tool_calls = ToolProtocolAdapter.parse_xml_tool_calls(step_raw_text)

            # ── 关键反停滞与伪调用拦截（Anti-Stall & Steer）带精准白名单 ──
            raw_stripped = (step_raw_text or "").strip()

            # 白名单判据：模型礼貌向用户发起确认提问（如询问是否允许删除），或完成了正常完整总结
            is_user_confirmation_request = bool(re.search(
                r"(?:是否允许|请问是否|请确认|需要您确认|是否继续|是否需要|是否删除|请选择|如需继续|？|\?)",
                raw_stripped
            ))
            has_explicit_pseudo_tags = bool(re.search(
                r"(?:\[调用工具|\[tool_call|<tool_call|<function=|</tool_code>|```xml\s*<tool_call)",
                raw_stripped,
                re.IGNORECASE
            ))
            is_bare_json_command = bool(re.search(r'\{\s*"command"\s*:', raw_stripped))

            # 仅当命中机器伪标签，或上步报错且模型输出极简敷衍且非提问时，才触发强制推偏
            is_abnormal_stall = (
                has_explicit_pseudo_tags
                or is_bare_json_command
                or (last_step_error_info is not None and len(raw_stripped) < 60 and not is_user_confirmation_request)
            )

            # 情况 A：模型未发起真实工具调用
            if not tool_calls or active_scene != "agent":
                if active_scene == "agent" and is_abnormal_stall and not is_user_confirmation_request and consecutive_stalls < 3 and step_idx < current_max_steps - 1:
                    consecutive_stalls += 1
                    logger.warning(f"[AgentRuntime] 捕获模型伪工具调用声明/卡顿: '{raw_stripped}'，激活强制纠偏推进器 (第{consecutive_stalls}次)")
                    last_step_error_info = {
                        "tool": "steering",
                        "summary": "模型输出了未执行的伪工具声明，未包含系统能够执行的具体 <tool_call> 标签与有效参数",
                    }
                    step_idx += 1
                    continue

                final_saved_text = clean_text if clean_text else step_raw_text
                if final_saved_text:
                    await MessageRepository.save_message(
                        conversation_id=conv_id,
                        role="model",
                        content=final_saved_text,
                        executed_by_account=final_account,
                        token_count=len(final_saved_text) // 3,
                        turn_id=current_turn_id,
                    )
                    await ConversationRepository.touch(conv_id)
                    if conv_id and not active_incognito:
                        long_term_memory.schedule_extraction(conv_id, delay_seconds=180)
                accumulated_visible_text += ("\n\n" + final_saved_text if accumulated_visible_text else final_saved_text)

                # ── 任务自主闭环：模型输出最终答复且无后续工具调用，自动验收通过并归档 ──
                if active_loop_obj:
                    try:
                        summary_snippet = (final_saved_text or accumulated_visible_text).strip().split("\n")[0][:80]
                        completed_loop = await TaskLoopRepository.complete_loop(
                            loop_id=active_loop_obj.id,
                            summary=summary_snippet or "模型已完成自主闭环并输出交付结论",
                        )
                        if completed_loop:
                            if target_cwd:
                                try:
                                    ws_sp = Path(target_cwd) / ".agenthub" / "scratchpad.md"
                                    ws_sp.parent.mkdir(parents=True, exist_ok=True)
                                    ws_sp.write_text(completed_loop.scratchpad_md or "", encoding="utf-8")
                                except Exception:
                                    pass
                            yield {
                                "type": "loop_completed",
                                "loop_id": completed_loop.id,
                                "iteration": completed_loop.current_iteration,
                                "max_iterations": completed_loop.max_iterations,
                                "status": "completed",
                                "scratchpad_md": completed_loop.scratchpad_md,
                            }
                            active_loop_obj = None
                    except Exception as e:
                        logger.warning(f"自主循环自动验收完成异常: {e}")

                # Post-Task 经验自动提炼钩子（满足规则 A/B/C 时生成 Diff 提议推送前端）
                if active_scene == "agent" and not active_incognito and not is_planning_phase:
                    exp_proposal = await self._run_post_task_distillation(
                        workspace_id=ws_id,
                        conversation_id=conv_id,
                        loop_id=tracked_loop_id,
                        scene_mode=active_scene,
                        incognito=active_incognito,
                        user_goal=user_message.strip().split("\n")[0][:140],
                        active_skill_ids=active_skill_ids,
                        recovered_error_count=recovered_error_count,
                        total_tool_steps=total_tool_steps,
                        circuit_breaker_triggered=circuit_breaker_triggered,
                        final_summary=final_saved_text[:300] if final_saved_text else "",
                        produced_artifacts=produced_artifacts,
                        workspace_root=ctx.get("current_cwd") or target_cwd,
                    )
                    if exp_proposal:
                        yield {
                            "type": "experience_proposal",
                            "proposal": exp_proposal,
                        }

                yield {
                    "type": "done",
                    "full_content": accumulated_visible_text.strip(),
                    "account": final_account,
                    "model": effective_model_name,
                }
                break

            consecutive_stalls = 0

            # 情况 B：模型发起了工具调用 -> 规范化工具调用 XML 示范并落盘（杜绝带偏后续历史）
            accumulated_visible_text += ("\n\n" + clean_text if (accumulated_visible_text and clean_text) else clean_text)
            formatted_tc_xml = "\n".join(
                f'<tool_call name="{tc.get("function", {}).get("name", "")}">{tc.get("function", {}).get("arguments", "{}")}</tool_call>'
                for tc in tool_calls
            )
            saved_content = (clean_text + "\n" + formatted_tc_xml).strip() if clean_text else formatted_tc_xml

            await MessageRepository.save_message(
                conversation_id=conv_id,
                role="model",
                content=saved_content,
                executed_by_account=final_account,
                token_count=len(step_raw_text) // 3,
                turn_id=current_turn_id,
                tool_calls_json=json.dumps(tool_calls, ensure_ascii=False),
            )

            # 逐个执行本步拦截到的工具调用
            for tc in tool_calls:
                call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                func_info = tc.get("function", {})
                func_name = func_info.get("name", "")
                raw_args = func_info.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    args = {}

                tool_output = ""
                compressed = ""

                # 🛡️ 物理只读硬门控拦截：若处于规划阶段，绝对禁止执行任何破坏性写入或终端命令
                if is_planning_phase and func_name not in PLANNING_SAFE_TOOLS:
                    deny_reason = (
                        f"Denied: 【安全只读硬门控】当前处于「谋定而后动 (Plan-First)」方案规划阶段，写盘与终端执行权限已被物理锁定。"
                        f"工具 [{func_name}] 禁止调用。请先向用户展示完整的【实施方案】与【分步执行步骤清单】，"
                        f"并在末尾输出 ```interactive_inquiry 协议块等待用户确认批准。"
                    )
                    compressed = f"[只读门控硬拦截 `{func_name}`]"
                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "status": "denied",
                        "output": deny_reason,
                        "compressed": compressed,
                    }
                    await MessageRepository.save_message(
                        conversation_id=conv_id,
                        turn_id=current_turn_id,
                        role="tool",
                        content=deny_reason,
                        compressed_content=compressed,
                        tool_call_id=call_id,
                    )
                    last_step_error_info = {
                        "tool": func_name,
                        "summary": deny_reason,
                    }
                    continue

                # ── 工具 1: 受控本地终端命令 (execute_local_command) ──
                if func_name == "execute_local_command":
                    cmd_str = str(args.get("command", "")).strip()
                    reason_str = str(args.get("reason", "执行本地终端诊断或操作")).strip()
                    norm_cmd = re.sub(r"\s+", " ", cmd_str)

                    # 语义级死循环物理硬阻断：若模型在收到熔断警告后仍顽固输出同一条失败命令，直接封禁执行
                    is_hard_loop_blocked = bool(
                        last_step_error_info
                        and last_step_error_info.get("circuit_breaker")
                        and last_step_error_info.get("failed_cmd") == norm_cmd
                    )

                    sec_level = "DENY" if is_hard_loop_blocked else shell_executor.check_security(cmd_str)

                    if sec_level == "DENY":
                        deny_reason = (
                            f"Denied: 【死循环物理硬阻断】命令 `{cmd_str}` 已经触发死循环熔断，系统已物理封禁重复执行此失败操作。"
                            f"请立即停止调用工具，直接向用户详细陈述排查事实与物理卡点，说明无法自动完成的原因并请求人工介入。"
                            if is_hard_loop_blocked
                            else f"Denied: 命令 `{cmd_str}` 触发系统高危安全黑名单已被强制拦截。严禁执行破坏性指令，请改用安全的替代方式回答用户。"
                        )
                        await audit_service.log(
                            event_type="shell_command_denied",
                            actor="agent",
                            detail={"command": cmd_str, "reason": reason_str, "loop_blocked": is_hard_loop_blocked},
                            approved=False,
                        )
                        tool_output = deny_reason
                        compressed = f"[死循环物理硬阻断 `{norm_cmd[:45]}`]" if is_hard_loop_blocked else f"[安全红线拦截高危命令 `{cmd_str[:50]}`]"
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": cmd_str,
                            "reason": reason_str,
                            "status": "denied",
                            "output": tool_output,
                            "compressed": compressed,
                        }

                    else:
                        # 全自动确认直通执行（包含 CONFIRM 与 SAFE 级命令），无需用户手动点击确认
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": cmd_str,
                            "reason": reason_str,
                            "security": "AUTO_CONFIRMED" if sec_level == "CONFIRM" else "SAFE",
                        }
                        exec_res = await shell_executor.run(
                            command=cmd_str,
                            cwd=ctx.get("current_cwd"),
                            session_id=conv_id,
                        )
                        tool_output = exec_res["output"]
                        new_cwd = exec_res["new_cwd"]
                        if new_cwd and new_cwd != ctx.get("current_cwd"):
                            ctx["current_cwd"] = new_cwd
                            await ConversationRepository.update_agent_state(conv_id, current_cwd=new_cwd)

                        compressed = shell_executor.compress_output(
                            cmd_str, tool_output, exec_res["exit_code"]
                        )
                        yield {
                            "type": "tool_end",
                            "call_id": call_id,
                            "name": func_name,
                            "command": cmd_str,
                            "reason": reason_str,
                            "status": "completed",
                            "exit_code": exec_res["exit_code"],
                            "output": tool_output,
                            "compressed": compressed,
                            "cwd": new_cwd,
                        }

                # ── 工具 1.5: 按需 FTS5 记忆与工作区历史检索 (search_memory_history) ──
                elif func_name == "search_memory_history":
                    mem_query = str(args.get("query", "")).strip()
                    mem_scope = str(args.get("scope", "all")).strip()
                    mem_limit = int(args.get("limit", 5) or 5)
                    yield {
                        "type": "tool_start",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"search_memory_history('{mem_query}', scope='{mem_scope}')",
                        "reason": f"按需 FTS5 检索历史记忆与工作区轨迹: {mem_query}",
                        "security": "SAFE",
                    }
                    tool_output = await self._execute_search_memory_history(
                        query=mem_query,
                        scope=mem_scope,
                        limit=mem_limit,
                        workspace_id=ws_id,
                        target_cwd=ctx.get("current_cwd") or target_cwd,
                        incognito=active_incognito,
                    )
                    compressed = f"[已按需检索历史记忆 ({mem_scope}): '{mem_query[:40]}']"
                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"search_memory_history('{mem_query[:40]}')",
                        "reason": f"按需检索历史记忆: {mem_query}",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                    }

                # ── 工具 2: 更新任务工作草稿纸 (update_working_memory) ──
                elif func_name == "update_working_memory":
                    current_wm = dict(ctx.get("working_memory") or {})
                    if args.get("task_completed") is True:
                        current_wm = {}
                        tool_output = "任务标记为已完成，工作记忆草稿纸已归档并清空。"
                        compressed = "[已清空归档任务工作草稿纸]"
                    else:
                        if args.get("goal"):
                            current_wm["goal"] = str(args["goal"])
                        if isinstance(args.get("notes"), dict):
                            existing_notes = current_wm.get("notes")
                            if not isinstance(existing_notes, dict):
                                existing_notes = {}
                            existing_notes.update(args["notes"])
                            current_wm["notes"] = existing_notes
                        tool_output = f"工作记忆草稿纸已更新: {json.dumps(current_wm, ensure_ascii=False)}"
                        compressed = f"[已更新工作草稿纸: {json.dumps(current_wm, ensure_ascii=False)[:80]}]"

                    ctx["working_memory"] = current_wm
                    await ConversationRepository.update_agent_state(
                        conv_id,
                        working_memory_json=json.dumps(current_wm, ensure_ascii=False),
                    )
                    yield {
                        "type": "scratchpad_update",
                        "working_memory": current_wm,
                    }
                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": "update_working_memory",
                        "reason": "更新当前任务关键状态草稿",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                    }

                # ── 工具 2.5: 直接写入长期记忆库 (save_long_term_memory) ──
                elif func_name == "save_long_term_memory":
                    cat = str(args.get("category", "preference")).strip()
                    mem_content = str(args.get("content", "")).strip()
                    pinned = bool(args.get("is_pinned", True))
                    if active_incognito:
                        tool_output = "Skipped: 当前处于无痕模式 (incognito=True)，禁止写入长期记忆库。"
                        compressed = "[无痕模式跳过写入长期记忆]"
                    elif not mem_content:
                        tool_output = "Error: 长期记忆内容 content 不能为空。"
                        compressed = "[长期记忆写入失败: 内容为空]"
                    else:
                        safe_mem_content, _ = MemoryPolicyGuard.sanitize_text(mem_content)
                        saved_mem = await long_term_memory.add_memory(
                            category=cat,
                            content=safe_mem_content,
                            confidence=0.98,
                            source="user_explicit",
                            is_pinned=1 if pinned else 0,
                        )
                        tool_output = f"✅ 已成功写入本地 SQLite 长期记忆库 (ID: `{saved_mem['id']}`, 分类: `{cat}`, 置顶: `{pinned}`): {safe_mem_content}"
                        compressed = f"[已植入长期记忆 ({cat}): {safe_mem_content[:45]}...]"
                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"save_long_term_memory({cat})",
                        "reason": "持久化写入本地长期记忆库",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                    }

                # ── 工具 3: 按需加载 Skill 详情 (load_skill_detail) ──
                elif func_name == "load_skill_detail":
                    skill_name = str(args.get("skill_name", "")).strip()
                    eff_ws_root = ctx.get("current_cwd") or target_cwd
                    tool_output = skill_loader.get_full_skill_markdown(
                        skill_name,
                        workspace_root=eff_ws_root,
                    )
                    clean_sid = skill_name.lstrip("/")
                    if clean_sid and clean_sid not in active_skill_ids and not str(tool_output).startswith("Error:"):
                        active_skill_ids.append(clean_sid)
                    compressed = f"[已加载专业技能 `{skill_name}` 操作规程与四元组清单]"
                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"load_skill_detail({skill_name})",
                        "reason": f"读取技能包 {skill_name} 详细指南",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                    }

                # ── 工具 3.1: FTS5 检索 Skill 领域知识库 (search_skill_knowledge) ──
                elif func_name == "search_skill_knowledge":
                    sk_name = str(args.get("skill_name", "")).strip()
                    kb_query = str(args.get("query", "")).strip()
                    eff_ws_root = ctx.get("current_cwd") or target_cwd
                    yield {
                        "type": "tool_start",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"search_skill_knowledge({sk_name}, '{kb_query}')",
                        "reason": f"FTS5 毫秒级检索技能 {sk_name} 的领域知识库",
                        "security": "SAFE",
                    }
                    tool_output = await skill_loader.search_knowledge(
                        sk_name,
                        kb_query,
                        workspace_root=eff_ws_root,
                    )
                    clean_sid = sk_name.lstrip("/")
                    if clean_sid and clean_sid not in active_skill_ids:
                        active_skill_ids.append(clean_sid)
                    compressed = f"[已检索 `{sk_name}` 领域知识库: 关键词 '{kb_query}']"
                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"search_skill_knowledge({sk_name})",
                        "reason": f"检索技能知识库: {kb_query}",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                    }

                # ── 工具 3.2: 执行 Skill 随包确定性算子 (run_skill_tool) ──
                elif func_name == "run_skill_tool":
                    sk_name = str(args.get("skill_name", "")).strip()
                    scr_name = str(args.get("script_name", "")).strip()
                    scr_args = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
                    eff_ws_root = ctx.get("current_cwd") or target_cwd
                    yield {
                        "type": "tool_start",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"run_skill_tool({sk_name}/tools/{scr_name})",
                        "reason": f"运行技能 {sk_name} 随包专属算子 {scr_name}",
                        "security": "AUTO_CONFIRMED",
                    }
                    tool_output = await skill_loader.execute_skill_tool(
                        sk_name,
                        scr_name,
                        scr_args,
                        workspace_root=eff_ws_root,
                    )
                    clean_sid = sk_name.lstrip("/")
                    if clean_sid and clean_sid not in active_skill_ids:
                        active_skill_ids.append(clean_sid)
                    compressed = f"[已运行随包算子 `{sk_name}/tools/{scr_name}`]"
                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"run_skill_tool({sk_name}/{scr_name})",
                        "reason": f"执行技能专属算子 {scr_name}",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                    }

                # ── 工具: 物理写入本地文件 (write_local_file - 绝不幻觉) ──
                elif func_name == "write_local_file":
                    raw_path = str(args.get("file_path", "")).strip()
                    file_content = str(args.get("content", ""))
                    overwrite = bool(args.get("overwrite", True))
                    current_work_dir = Path(ctx.get("current_cwd") or os.getcwd()).resolve()

                    target_file = Path(raw_path)
                    if not target_file.is_absolute():
                        target_file = (current_work_dir / target_file).resolve()

                    rel_display = str(target_file)
                    try:
                        rel_display = str(target_file.relative_to(current_work_dir))
                    except Exception:
                        pass

                    # 全自动确认直通写入物理文件，无需用户手动点击确认
                    yield {
                        "type": "tool_start",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"write_file -> {rel_display} ({len(file_content)} 字符)",
                        "reason": f"真实落盘本地文件 {rel_display}",
                        "security": "AUTO_CONFIRMED",
                    }
                    try:
                        if target_file.exists() and not overwrite:
                            tool_output = f"Error: 文件 `{rel_display}` 已存在且 overwrite=False，取消写入。"
                            compressed = f"[写入取消: 文件已存在 `{rel_display}`]"
                        else:
                            target_file.parent.mkdir(parents=True, exist_ok=True)
                            target_file.write_text(file_content, encoding="utf-8")
                            hotplug_note = ""
                            if target_file.name.upper() == "SKILL.MD":
                                target_file = skill_loader.normalize_skill_file(target_file)
                                hotplug_note = "\n🎯 [SKILL 标准校验与热插拔已激活]: 已自动规范化 YAML Frontmatter 与 kebab-case 命名，前端输入框 `/` 菜单现已可实时调用并查看该技能说明。"
                            elif target_file.name.lower() == "mcp_servers.json":
                                created_docs = skill_loader.ensure_mcp_companion_skills()
                                active_tools = ToolProtocolAdapter.get_builtin_tools() + await mcp_manager.get_enabled_tools()
                                doc_tip = f"（已自动补全 {created_docs} 个 MCP 伴生 SKILL 说明文档）" if created_docs else ""
                                hotplug_note = f"\n🔌 [MCP 热插拔已重载]: 已重新扫描 mcp_servers.json，当前可用工具总数: {len(active_tools)}{doc_tip}。"
                            tool_output = (
                                f"✅ 成功物理落盘文件: `{target_file}`\n"
                                f"文件大小: {len(file_content.encode('utf-8'))} 字节\n"
                                f"状态: 真实写入磁盘已验证完成。{hotplug_note}"
                            )
                            compressed = f"[已真实物理落盘文件 `{rel_display}` ({len(file_content)} 字符)]"

                            # 自动联动登记到当前工作区资产库
                            try:
                                from ..repository.artifact_repository import ArtifactRepository
                                f_type = ArtifactRepository._detect_file_type(target_file)
                                await ArtifactRepository.register_artifact(
                                    workspace_id=ws_id,
                                    file_path=str(target_file),
                                    file_name=target_file.name,
                                    file_type=f_type,
                                    file_size=len(file_content.encode("utf-8")),
                                    conversation_id=conv_id,
                                    source="ai_generated",
                                    summary=f"AI 自主编写落盘: {target_file.name}",
                                )
                            except Exception as art_err:
                                logger.warning(f"资产库落盘自动登记异常: {art_err}")
                    except Exception as write_err:
                        tool_output = f"Error: 写入文件 `{target_file}` 失败: {write_err}"
                        compressed = f"[写入文件异常: {str(write_err)[:40]}]"

                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"write_file({rel_display})",
                        "reason": f"写入本地文件 {rel_display}",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                        "cwd": str(current_work_dir),
                    }

                # ── 工具: 物理读取本地文件 (read_local_file) ──
                elif func_name == "read_local_file":
                    raw_path = str(args.get("file_path", "")).strip()
                    current_work_dir = Path(ctx.get("current_cwd") or os.getcwd()).resolve()
                    target_file = Path(raw_path)
                    if not target_file.is_absolute():
                        target_file = (current_work_dir / target_file).resolve()
                        if not target_file.exists():
                            # 双层寻址回退：若当前沙盒工作区内不存在，自动在 AgentHub 根目录寻找底座技能包与公共文件
                            proj_root = Path(__file__).resolve().parent.parent.parent.parent
                            candidate = (proj_root / raw_path).resolve()
                            if candidate.exists() and candidate.is_file():
                                target_file = candidate

                    rel_display = str(target_file)
                    try:
                        rel_display = str(target_file.relative_to(current_work_dir))
                    except Exception:
                        pass

                    yield {
                        "type": "tool_start",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"read_file({rel_display})",
                        "reason": f"读取本地文件内容: {rel_display}",
                        "security": "SAFE",
                    }
                    if not target_file.exists() or not target_file.is_file():
                        tool_output = f"Error: 目标文件不存在: `{target_file}`"
                        compressed = f"[读取失败: 文件不存在 `{rel_display}`]"
                    else:
                        try:
                            content_text = target_file.read_text(encoding="utf-8", errors="replace")
                            tool_output = content_text
                            compressed = f"[成功读取文件 `{rel_display}`: 共 {len(content_text)} 字符]"
                        except Exception as read_err:
                            tool_output = f"Error: 读取文件失败: {read_err}"
                            compressed = f"[读取文件异常: {str(read_err)[:40]}]"

                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"read_file({rel_display})",
                        "reason": f"读取本地文件 {rel_display}",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                    }

                # ── 工具: 实时网络正文抓取与微服务健康探测 (web_fetch) ──
                elif func_name == "web_fetch":
                    target_url = str(args.get("url", "")).strip()
                    extract_main = bool(args.get("extract_main_text", True))
                    from ..extensions.web_tools import web_tools

                    yield {
                        "type": "tool_start",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"web_fetch({target_url})",
                        "reason": f"访问并提纯目标网页/探测服务: {target_url}",
                        "security": "SAFE",
                    }
                    fetch_res = await web_tools.fetch_url(target_url, extract_main_text=extract_main)
                    tool_output = fetch_res["content"]
                    st_code = fetch_res["status_code"]
                    compressed = f"[已抓取 `{target_url[:45]}`: HTTP {st_code}, 共 {len(tool_output)} 字符]"

                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"web_fetch({target_url})",
                        "reason": f"抓取网络正文: {target_url}",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                    }

                # ── 工具: Windows UI 自动化操作 (ui_action) ──
                elif func_name == "ui_action":
                    act = str(args.get("action", "list")).strip().lower()
                    target_win = str(args.get("target_window", "")).strip()
                    selector = str(args.get("control_selector", "")).strip()
                    val = str(args.get("value", ""))

                    is_read_only = act in ["list", "inspect"]
                    sec_level = "SAFE" if is_read_only else "AUTO_CONFIRMED"

                    yield {
                        "type": "tool_start",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"ui_action({act}, target='{target_win}', selector='{selector}')",
                        "reason": f"Windows 桌面 UI 操作: {act}",
                        "security": sec_level,
                    }

                    if not is_read_only:
                        await audit_service.log(
                            event_type=f"ui_{act.lower()}",
                            actor="agent",
                            detail={
                                "action": act,
                                "window": target_win,
                                "selector": selector,
                                "value": val,
                                "security_level": sec_level,
                            },
                            approved=True,
                        )

                    if act == "list":
                        wins = ui_operator.list_windows(pattern=target_win)
                        if not wins:
                            tool_output = f"当前未找到匹配 '{target_win}' 的可见窗口。"
                        else:
                            win_lines = [f"| HWND | 标题 | 窗口类名 |", "| :--- | :--- | :--- |"]
                            for w in wins:
                                win_lines.append(f"| {w['hwnd']} | {w['title']} | `{w['class_name']}` |")
                            tool_output = "### 🖥️ 当前可见桌面窗口列表：\n" + "\n".join(win_lines)
                        compressed = f"[已枚举可见窗口: 共 {len(wins)} 个]"

                    elif act == "inspect":
                        tool_output = ui_operator.inspect_window(target_win)
                        compressed = f"[已分析窗口 `{target_win}` 控件树: {len(tool_output.splitlines())} 行]"

                    elif act in ["click", "input", "focus"]:
                        action_res = ui_operator.perform_action(
                            target=target_win,
                            control_selector=selector,
                            action=act,
                            value=val,
                            press_enter=bool(args.get("press_enter", False)),
                        )
                        tool_output = json.dumps(action_res, ensure_ascii=False, indent=2)
                        st_txt = "成功" if action_res.get("success") else "失败"
                        compressed = f"[UI {act} {st_txt}: {action_res.get('message') or action_res.get('error')}]"

                    else:
                        tool_output = f"Error: 不支持的 UI action: '{act}'。有效选项为 list, inspect, click, input, focus。"
                        compressed = f"[UI 操作参数错误: {act}]"

                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"ui_action({act})",
                        "reason": f"桌面 UI 操作: {act}",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                    }

                # ── 工具 4: MCP 扩展工具 (mcp__<server>__<tool>) ──
                elif func_name.startswith("mcp__"):
                    yield {
                        "type": "tool_start",
                        "call_id": call_id,
                        "name": func_name,
                        "command": f"{func_name}({json.dumps(args, ensure_ascii=False)})",
                        "reason": "调用 MCP 外部扩展工具",
                        "security": "MCP",
                    }
                    tool_output = await mcp_manager.call_mcp_tool(func_name, args)
                    is_err = tool_output.startswith("Error:")
                    compressed = shell_executor.compress_output(
                        command=f"{func_name}({json.dumps(args, ensure_ascii=False)[:30]})",
                        raw_output=tool_output,
                        exit_code=1 if is_err else 0,
                    )
                    yield {
                        "type": "tool_end",
                        "call_id": call_id,
                        "name": func_name,
                        "command": func_name,
                        "reason": "调用 MCP 外部扩展工具",
                        "status": "completed",
                        "output": tool_output,
                        "compressed": compressed,
                    }

                else:
                    tool_output = f"Error: 未知的工具名称 `{func_name}`。"
                    compressed = f"[未知工具调用 `{func_name}`]"

                # 智能异常探测：判断该步骤是否遇到报错，并为下一步自愈循环激活调度器
                prev_step_error = last_step_error_info
                out_lower = (tool_output or "").lower()
                is_step_abnormal = any(
                    err_kw in out_lower
                    for err_kw in (
                        "npm error", "enoent", "error:", "traceback", "syntaxerror",
                        "command not found", "cannot find module", "failed", "denied"
                    )
                )
                if is_step_abnormal:
                    # 语义级死循环判定 (LoopBreaker)
                    is_circuit_breaker = False
                    if func_name == "execute_local_command":
                        cmd_entry = str(args.get("command", "")).strip()
                        norm_entry = re.sub(r"\s+", " ", cmd_entry)
                        command_history.append({"cmd": norm_entry, "abnormal": True})
                        # 规则 1: 连续 2 次执行完全相同的命令均异常
                        if len(command_history) >= 2 and command_history[-1]["cmd"] == command_history[-2]["cmd"] and command_history[-2]["abnormal"]:
                            is_circuit_breaker = True
                        # 规则 2: 最近 5 步中出现 3 次相同失败命令
                        recent_same = [h for h in command_history[-5:] if h["cmd"] == norm_entry and h["abnormal"]]
                        if len(recent_same) >= 3:
                            is_circuit_breaker = True

                        if is_circuit_breaker:
                            allow_step_extension = False
                            circuit_breaker_triggered = True
                            logger.warning(f"[AgentRuntime] 触发死循环熔断 (LoopBreaker): {norm_entry}")

                    last_step_error_info = {
                        "tool": func_name,
                        "summary": compressed or (tool_output.strip().split("\n")[0][:120]),
                        "circuit_breaker": is_circuit_breaker,
                        "failed_cmd": re.sub(r"\s+", " ", str(args.get("command", "")).strip()) if func_name == "execute_local_command" else "",
                    }
                else:
                    if func_name == "execute_local_command":
                        cmd_entry = str(args.get("command", "")).strip()
                        command_history.append({"cmd": re.sub(r"\s+", " ", cmd_entry), "abnormal": False})
                    last_step_error_info = None
                    if not str(tool_output or "").startswith("Error:"):
                        total_tool_steps += 1
                        if func_name == "write_local_file":
                            fp = str(args.get("file_path") or "").strip()
                            if fp and fp not in produced_artifacts:
                                produced_artifacts.append(fp)
                        if prev_step_error is not None:
                            recovered_error_count += 1

                # 将工具真实输出与单行折叠摘要同时落盘（当前 Turn 用完整 output，后续 Turn 自动用 compressed）
                await MessageRepository.save_message(
                    conversation_id=conv_id,
                    role="tool",
                    content=tool_output,
                    compressed_content=compressed,
                    turn_id=current_turn_id,
                    tool_call_id=call_id,
                )

                # 追加写入 Event Ledger（tool_executed / artifact_produced / tool_error_recovered）
                if not active_incognito and active_scene != "oneshot":
                    step_skill_id = str(args.get("skill_name") or "").strip().lstrip("/") or None
                    current_loop_id = tracked_loop_id or (active_loop_obj.id if active_loop_obj else None)
                    try:
                        await event_ledger.append_event(
                            workspace_id=ws_id,
                            conversation_id=conv_id,
                            turn_id=current_turn_id,
                            loop_id=current_loop_id,
                            event_type="tool_executed",
                            actor="agent",
                            skill_id=step_skill_id,
                            payload={
                                "tool": func_name,
                                "args": args,
                                "compressed": compressed,
                                "is_error": is_step_abnormal,
                                "output_preview": (tool_output or "")[:400],
                            },
                            incognito=False,
                        )
                        if (
                            func_name == "write_local_file"
                            and not is_step_abnormal
                            and not str(tool_output).startswith("Error:")
                        ):
                            await event_ledger.append_event(
                                workspace_id=ws_id,
                                conversation_id=conv_id,
                                turn_id=current_turn_id,
                                loop_id=current_loop_id,
                                event_type="artifact_produced",
                                actor="agent",
                                skill_id=step_skill_id,
                                payload={
                                    "file_path": str(args.get("file_path") or ""),
                                    "compressed": compressed,
                                },
                                incognito=False,
                            )
                        if prev_step_error is not None and not is_step_abnormal:
                            await event_ledger.append_event(
                                workspace_id=ws_id,
                                conversation_id=conv_id,
                                turn_id=current_turn_id,
                                loop_id=current_loop_id,
                                event_type="tool_error_recovered",
                                actor="agent",
                                skill_id=step_skill_id,
                                payload={
                                    "failed_tool": prev_step_error.get("tool"),
                                    "failed_summary": prev_step_error.get("summary"),
                                    "recovery_tool": func_name,
                                    "recovery_args": args,
                                    "recovery_compressed": compressed,
                                },
                                incognito=False,
                            )
                    except Exception as ledger_err:
                        logger.debug(f"[AgentRuntime] 写入工具账本事件跳过: {ledger_err}")

                # ── 具身状态联动：推进 Loop 轮次并动态记事板 ──
                if active_loop_obj:
                    try:
                        summary_text = compressed or (str(args.get("command", "")) if func_name == "execute_local_command" else f"{func_name}")
                        is_succ = not is_step_abnormal
                        updated_loop = await TaskLoopRepository.record_tool_step(
                            loop_id=active_loop_obj.id,
                            tool_name=func_name,
                            summary=summary_text,
                            is_success=is_succ,
                        )
                        if updated_loop:
                            active_loop_obj = updated_loop
                            if target_cwd:
                                try:
                                    ws_sp = Path(target_cwd) / ".agenthub" / "scratchpad.md"
                                    ws_sp.parent.mkdir(parents=True, exist_ok=True)
                                    ws_sp.write_text(updated_loop.scratchpad_md or "", encoding="utf-8")
                                except Exception:
                                    pass
                            yield {
                                "type": "loop_step",
                                "loop_id": updated_loop.id,
                                "iteration": updated_loop.current_iteration,
                                "max_iterations": updated_loop.max_iterations,
                                "status": updated_loop.loop_status,
                                "scratchpad_md": updated_loop.scratchpad_md,
                            }
                    except Exception as e:
                        logger.warning(f"自主循环步进同步异常: {e}")

            step_idx += 1

            # 若已到达自适应最大步数上限（最高动态扩展至 40 步），给出收尾完成信号
            if step_idx >= current_max_steps:
                if active_loop_obj:
                    try:
                        completed_loop = await TaskLoopRepository.complete_loop(
                            loop_id=active_loop_obj.id,
                            summary=f"已达当前执行步数上限 ({step_idx} 步)，已输出阶段性成果",
                        )
                        if completed_loop:
                            yield {
                                "type": "loop_completed",
                                "loop_id": completed_loop.id,
                                "iteration": completed_loop.current_iteration,
                                "max_iterations": completed_loop.max_iterations,
                                "status": "completed",
                                "scratchpad_md": completed_loop.scratchpad_md,
                            }
                            active_loop_obj = None
                    except Exception:
                        pass
                if active_scene == "agent" and not active_incognito and not is_planning_phase:
                    exp_proposal = await self._run_post_task_distillation(
                        workspace_id=ws_id,
                        conversation_id=conv_id,
                        loop_id=tracked_loop_id,
                        scene_mode=active_scene,
                        incognito=active_incognito,
                        user_goal=user_message.strip().split("\n")[0][:140],
                        active_skill_ids=active_skill_ids,
                        recovered_error_count=recovered_error_count,
                        total_tool_steps=total_tool_steps,
                        circuit_breaker_triggered=circuit_breaker_triggered,
                        final_summary=f"已执行 {step_idx} 步任务",
                        produced_artifacts=produced_artifacts,
                        workspace_root=ctx.get("current_cwd") or target_cwd,
                    )
                    if exp_proposal:
                        yield {
                            "type": "experience_proposal",
                            "proposal": exp_proposal,
                        }
                yield {
                    "type": "done",
                    "full_content": accumulated_visible_text.strip() or f"已完成深度推演与自主闭环执行（共执行 {step_idx} 步）。若需进一步处理请继续告知。",
                    "account": final_account,
                }

        # 7. 回合后异步记忆提炼、增量会话摘要推进与自然衰减维护（非阻塞后台任务）
        if active_scene != "oneshot" and not active_incognito:
            asyncio.create_task(self._post_turn_memory_maintenance(user_message))
            window_limit = 12 if active_scene == "chat" else 10
            asyncio.create_task(
                session_summary_manager.maybe_update_summary(
                    conv_id,
                    workspace_id=ws_id,
                    current_turn_id=current_turn_id,
                    window_turns=window_limit,
                    incognito=active_incognito,
                )
            )

    @staticmethod
    async def _post_turn_memory_maintenance(user_message: str) -> None:
        """
        回合后后台轻量级长期记忆捕获与 30 天指数衰减维护。
        当用户在对话中明确表达个人技术栈偏好、工作规范或环境特征时自动入库（经过隐私脱敏）。
        """
        try:
            text = (user_message or "").strip()
            if not text or len(text) > 500:
                return

            # 匹配用户显式声明偏好或规范的句式（零额外 Token 消耗）
            explicit_patterns = [
                (r"(?:请记住|记住|以后都|默认使用|我的偏好是|统一使用|严禁使用)[：:\s]*(.+)", "rule", 0.92),
                (r"(?:我习惯用|我喜欢用|我常用|我们项目用的是)[：:\s]*(.+)", "preference", 0.85),
                (r"(?:我的本机环境是|我的系统是|本地路径在|开发环境是)[：:\s]*(.+)", "env_info", 0.88),
            ]
            for pat, cat, conf in explicit_patterns:
                m = re.search(pat, text)
                if m:
                    raw_fact = m.group(0).strip()[:160]
                    fact, _ = MemoryPolicyGuard.sanitize_text(raw_fact)
                    if not fact.strip():
                        break
                    # 查重避免重复写入完全相同的句子
                    existing = await long_term_memory.list_all()
                    if not any(e["content"] == fact for e in existing):
                        await long_term_memory.add_memory(category=cat, content=fact, confidence=conf)
                        logger.info(f"[LongTermMemory] 自动捕获用户长期画像 [{cat}]: {fact}")
                    break

            await long_term_memory.apply_time_decay()
        except Exception as e:
            logger.debug(f"后台记忆维护异常: {e}")


# 全局单例
agent_runtime = AgentRuntime()

