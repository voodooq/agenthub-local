"""
多轮上下文动态组装器 (ContextBuilder) 与会话自愈器 (SessionHealer)
核心职责：
1. SessionHealer: 自动检测因断电/关闭窗口/用户打断而残留的未闭合 tool_calls，原地补插 Cancelled 兜底消息，杜绝 API 400 报错
2. Zone 1 / Zone 2 / Zone 3 物理隔离：
   - Zone 1 (Immutable Prefix): 固定人设 + 物理红线 + 字典序工具协议 + 已固化历史摘要 (Anchored Summary)，字节级强冻结
   - Zone 2 (Append-Only History): 锚点之后的热区消息，只增不改（Write-Once，取消逐轮左滑驱逐与轮内回改）
   - Zone 3 (Tail Injection): 动态记忆、L4 事实、工作草稿纸、冷区 FTS5 召回与策略门控，仅当次发送尾注，跨轮零残留
3. Chunk Collapse & Cold Retrieval: 协同 LocalContextCompressor 与 SearchRepository 实现 8K 本地模型极限脱水与冷热双路记忆
"""
import asyncio
import json
import re
from typing import Any

from ..core.logging_config import logger
from ..repository.conversation_repository import ConversationRepository
from ..repository.message_repository import MessageRepository
from ..repository.search_repository import SearchRepository
from ..memory.experience_store import episodic_experience_store
from ..memory.fact_manager import fact_manager
from ..memory.long_term import long_term_memory
from ..memory.policy_guard import MemoryPolicyGuard
from ..extensions.skill_loader import skill_loader
from ..skills.skill_service import SkillService
from ..service.attachment_service import attachment_service
from .context_compressor import LocalContextCompressor, estimate_tokens
from .tool_protocol import ToolProtocolAdapter

_SKILL_DEV_INTENT_KEYWORDS = (
    "/new-skill",
    "skill",
    "mcp",
    "技能",
    "热加载",
    "四元组",
    "skill.md",
    "mcp_servers.json",
    "创建工具",
    "开发工具",
)


def _should_inject_skill_dev_spec(user_query: str) -> bool:
    """宽松匹配技能/MCP 开发意图，仅命中时才注入四元组开发规范"""
    q_lower = (user_query or "").lower()
    return any(kw in q_lower for kw in _SKILL_DEV_INTENT_KEYWORDS)


class SessionHealer:
    """会话消息链自愈器：修复未闭合的 tool_calls"""

    @staticmethod
    async def heal_session(conversation_id: str) -> int:
        """
        检查指定会话的历史消息，若发现 assistant/model 发出了 tool_calls 但缺少对应 tool_call_id 的结果消息，
        自动补插一条 Cancelled 闭合消息，保证消息链完整合法。

        Returns:
            int: 自动修复补全的工具调用条数
        """
        messages = await MessageRepository.get_by_conversation(conversation_id, limit=500)
        if not messages:
            return 0

        existing_tool_call_ids = {
            m.tool_call_id for m in messages if m.role == "tool" and m.tool_call_id
        }

        healed_count = 0
        for msg in messages:
            if msg.role in ("assistant", "model") and msg.tool_calls_json:
                try:
                    t_calls = json.loads(msg.tool_calls_json)
                    if not isinstance(t_calls, list):
                        continue
                except Exception:
                    continue

                for tc in t_calls:
                    call_id = tc.get("id")
                    func_name = tc.get("function", {}).get("name", "unknown_tool")
                    if call_id and call_id not in existing_tool_call_ids:
                        cancel_text = f"Cancelled: 工具 [{func_name}] (ID: {call_id}) 因用户中断、跳过审批或会话重启而未执行。"
                        compressed = f"[已折叠中断命令 `{func_name}`: 用户中断或跳过执行]"
                        await MessageRepository.save_message(
                            conversation_id=conversation_id,
                            turn_id=msg.turn_id or 1,
                            role="tool",
                            content=cancel_text,
                            compressed_content=compressed,
                            tool_call_id=call_id,
                        )
                        existing_tool_call_ids.add(call_id)
                        healed_count += 1
                        logger.info(f"[SessionHealer] 已自动自愈补全会话 {conversation_id} 未闭合的调用 {call_id}")

        return healed_count


class ContextBuilder:
    """三层记忆与多模态上下文动态组装器"""

    @classmethod
    async def build(
        cls,
        conversation_id: str,
        current_turn_id: int,
        user_query: str,
        tools: list[dict[str, Any]] | None = None,
        context_rounds_override: int | None = None,
        base_system_prompt: str | None = None,
        execution_strategy: str = "direct",
        clarify_mode: bool = False,
        approved_plan_steps: list[dict[str, Any]] | None = None,
        inquiry_confirmed: bool = False,
        inquiry_action: str | None = None,
        has_prior_execution_unlock: bool = False,
        provider_info: dict[str, Any] | None = None,
        model_name: str = "",
        frozen_tail_prompt: str | None = None,
        mcp_catalog_prompt: str | None = None,
        swarm_mode: bool = False,
        swarm_role: str | None = None,
        swarm_member_name: str | None = None,
        swarm_brief: str | None = None,
        grounding_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        为当前推理步骤构建严格物理分区的三层上下文载荷 (Zone 1 + Zone 2 + Zone 3)。

        Returns:
            dict: {
                "system_instruction": Zone 1 绝对不动前缀系统指令,
                "history_messages": Zone 2 只增不改热区历史消息列表,
                "tail_injection_prompt": Zone 3 仅当次发送生效的动态尾部注入载荷（绝不写入 DB/Zone 2）,
                "referenced_memories": 本次命中的长期记忆列表,
                "active_facts": 本次挂载的确定性事实视图,
                "cold_history_hits": 冷区 FTS5 召回片段列表,
                "collapse_anchor_turn_id": 当前生效的分块坍缩锚点 turn_id,
                "collapsed_triggered": 本次组装是否触发了分块坍缩,
                "matched_skill_id": ...,
                "matched_skill_ids": ...,
                "scene_mode": 当前会话场景模式,
                "working_memory": 当前工作记忆字典,
                "current_cwd": 当前工作目录
            }
        """
        # 1. 先执行会话自愈，闭合任何悬挂的 tool_calls
        await SessionHealer.heal_session(conversation_id)

        conv = await ConversationRepository.get_by_id(conversation_id)
        scene_mode = (conv.scene_mode if conv else "agent") or "agent"
        incognito = bool(conv.incognito) if conv else False
        workspace_id = (getattr(conv, "workspace_id", None) if conv else None) or "default"
        working_memory_raw = (conv.working_memory_json if conv else "{}") or "{}"
        rolling_summary = (conv.rolling_summary if conv else "") or ""
        collapse_anchor_turn_id = int(getattr(conv, "collapse_anchor_turn_id", 0) or 0)
        anchored_summary = (getattr(conv, "anchored_summary", "") or "") if conv else ""
        current_cwd = (conv.current_cwd if conv else None)

        try:
            working_memory = json.loads(working_memory_raw)
        except Exception:
            working_memory = {}

        # ── 模式 A: ⚡ 极速单次模式 (oneshot) ──
        if scene_mode == "oneshot":
            sys_prompt = base_system_prompt or "你是 AgentHub Local 极速研发助手，请直接、精炼地输出结果。"
            safe_sys, _ = MemoryPolicyGuard.sanitize_text(sys_prompt)
            return {
                "system_instruction": safe_sys,
                "history_messages": [],
                "tail_injection_prompt": "",
                "referenced_memories": [],
                "active_facts": [],
                "cold_history_hits": [],
                "injected_case_ids": [],
                "collapse_anchor_turn_id": 0,
                "collapsed_triggered": False,
                "matched_skill_id": None,
                "matched_skill_ids": [],
                "scene_mode": "oneshot",
                "working_memory": {},
                "current_cwd": current_cwd,
            }

        # ── Zone 1 (Immutable Prefix): 静态人设 + 物理能力红线 + 字典序工具协议 ──
        normalized_action = (inquiry_action or "").strip().lower() or None
        explicit_execute_unlock = bool(
            approved_plan_steps
            or (inquiry_confirmed and normalized_action == "execute_now")
        )
        explicit_clarify_lock = (
            normalized_action == "clarify_only"
            and not approved_plan_steps
        )
        # 对话模式下需求澄清解锁落盘：若收到明确方案确认与执行授权，自动视同 Agent 任务态挂载工具与落盘铁律
        effective_scene_mode = "agent" if (explicit_execute_unlock and scene_mode == "chat") else scene_mode

        zone1_static_parts: list[str] = []
        if base_system_prompt:
            zone1_static_parts.append(base_system_prompt)
        else:
            zone1_static_parts.append("你是 AgentHub Local 智能研发中枢，运行在用户本地私有环境中。")

        if effective_scene_mode == "chat":
            zone1_static_parts.append(
                "### ⚠️ 运行模式与物理能力红线（强制遵守）：\n"
                "你当前处于【💬 标准对话模式 / 纯文本咨询】。在此模式下，你【没有】被挂载任何本地磁盘写入工具、Shell 执行工具或操作系统修改权限！\n"
                "1. 【严禁虚构已执行操作】：绝对禁止声称“我已经为你写入了文件”、“已在本地创建了目录/代码”、“已执行了命令”或“请刷新查看磁盘”！此类虚构言论属于严重的 AI 代码幻觉，受到系统安全审计监控！\n"
                "2. 【诚实交付代码】：你编写的所有代码必须直接在回复的 Markdown 代码块中完整呈现，并明确提示用户：“当前处于标准对话模式，我无法直接修改本地磁盘，请手动复制上方代码保存；若需我自动为你落地到本地文件，请点击输入框下方切换至【Agent 任务】模式”。"
            )
        elif effective_scene_mode == "agent":
            zone1_static_parts.append(
                "### ⚠️ Agent 任务模式落盘真实性与自主自愈铁律（强制遵守）：\n"
                "1. **真实调用与禁止先声明**：所有文件创建、修改与终端执行必须通过 `<tool_call>` 调用工具（如 `write_local_file`, `read_local_file`, `read_tool_ref`, `execute_local_command` 等）完成；严禁在未调用或未收到真实工具返回前宣称已落盘或已执行。\n"
                "2. **自主闭环自愈（严禁报错卡死退出）**：遇到命令报错（`exit_code != 0`、`ENOENT` 等），必须分析根因并主动调工具自愈（如 `--prefix <相对路径>`、补全文件、切换路径），直到跑通交付。\n"
                "3. **真实附件红线与 Fail-Fast（严禁 Mock 替代）**：只读附件目录（`./attachments/`、`.agenthub/attachments/`）严禁写入/篡改；缺失文件、Sheet 不匹配或解析异常时，严禁生成任何 Mock/模拟测试数据兜底，脚本必须 Fail-Fast（抛 `FileNotFoundError` 非零退出）并立即停机如实报告预期路径与原因。\n"
                "4. **极速执行与代码规范**：\n"
                "   - **复用优先**：Skill/工作区已有 `scripts/*.py`、`tools/*.py` 时必须优先直接执行（`run_skill_tool` 或 `execute_local_command`），仅在报错且无法调参时做最小化修复；\n"
                "   - **局部补丁优先**：修改已有文件首选 `replace_in_file`（`SEARCH/REPLACE`），严禁全量重写数百行代码；超长文件先用 `get_repo_skeleton` 或 `read_local_file` 切片读取；\n"
                "   - **单步并行与原地修改**：同一回复可并列输出多个 `<tool_call>` 并发执行；必须原地覆盖原文件，严禁生成 `_v1`/`_v2`/`_fixed` 副本；\n"
                "   - **大文件与诊断**：长源码用 `write_local_file` 落盘，严禁塞入 `python -c`；数据探索须用单个紧凑 Python 脚本一次性打印全部元信息与样例；\n"
                "   - **过程与交付分离**：调工具步骤仅附简短旁白，严禁提前撰写完整报告或编造 `[终端/工具输出]:` / `[Gemini]:` 标签。\n"
                "5. **最新上传附件唯一基准**：本轮若上传新附件，强制以该最新附件为唯一起始基准，历史旧数据与 Working Memory 摘要一律作废；复用脚本前必须核验并更新输入路径指向最新附件；仅在未传新附件且用户特指继续旧数据时沿用旧数据。\n"
                "6. **单层 `output/` 交付红线**：所有最终产出物必须直接保存于一级 `output/` 目录（如 `output/xxx.xlsx`），严禁 `output/output/` 多层嵌套或重复 `.xlsx` 等文件名后缀，交付链接（`file:///.../output/xxx`）须与磁盘单层路径 100% 一致。\n"
                "7. **长耗时下载与断点续传**：下载大文件/大模型或易断流包时优先使用 `resumable_download`（自带 Range 断点续传、异常退避重试、直连/代理/备用源切换及确认不可达安全断开保留断点）；重型命令（`uv sync`/`pip install`/`npm install`/`git clone`）观察期未完也会自动转后台返回 `job_id`。请调用 `check_background_command(job_id, wait_seconds=20)` 持续跟进，状态为 `running` 时切勿强杀；若最终确认断网断开，请如实报告保留的 `.part` 断点路径。"
            )
            if tools:
                protocol_prompt = ToolProtocolAdapter.build_xml_protocol_prompt(tools)
                if protocol_prompt:
                    zone1_static_parts.append(protocol_prompt)
            if mcp_catalog_prompt and mcp_catalog_prompt.strip():
                zone1_static_parts.append(
                    "### 🔌 可用 MCP 扩展能力目录（默认仅挂载目录摘要；如需使用某服务，请先调用 `discover_mcp_tools(server_names=[...])` 按需加载其完整参数 Schema）：\n"
                    + mcp_catalog_prompt.strip()
                )

        if grounding_config and grounding_config.get("strict_mode"):
            zone1_static_parts.append(
                "### 🛡️ 专家严谨工作与事实基准铁律（强制执行）：\n"
                "1. 【事实唯一基准】：所有数据、指标与结论必须 100% 源于真实工具调用回包或本地文件读取，严禁无依据推演。\n"
                "2. 【缺失如实拒答 (Fail-Fast)】：工具未查到数据或文件缺失时，必须如实回答“无相关记录”，严禁编造任何模拟假数据。\n"
                "3. 【禁止虚假声明】：严禁在未成功调用工具并获得有效返回前宣称“已查询/已完成/已保存”。\n"
                "4. 【证据溯源】：输出核心结论时尽量附带真实引用的文件或工具事实依据。"
            )
            custom_rules = str(grounding_config.get("custom_rules") or "").strip()
            if custom_rules:
                zone1_static_parts.append(
                    f"### 📋 专家专属业务防虚构红线：\n<custom_guardrails>\n{custom_rules[:500]}\n</custom_guardrails>"
                )

        zone1_base_prompt = "\n\n".join(zone1_static_parts)

        # ── Zone 3 (Tail Injection): 动态上下文集中放尾部，绝不污染 Zone 1 与 Zone 2 ──
        active_facts: list[dict[str, Any]] = []
        referenced_memories: list[dict[str, Any]] = []
        cold_history_hits: list[dict[str, Any]] = []
        injected_case_ids: list[str] = []
        matched_skills: list[dict[str, Any]] = []
        matched_skill: dict[str, Any] | None = None

        # 先解析技能命中元信息（供外层日志与展示），文本块在无 frozen_tail_prompt 时写入 Zone 3
        try:
            matched_skills = await asyncio.to_thread(skill_loader.resolve_slash_commands, user_query, current_cwd)
            matched_skill = matched_skills[0] if matched_skills else None
        except Exception:
            matched_skills = []
            matched_skill = None

        if frozen_tail_prompt is not None:
            # 同一 Turn 内 ReAct step_idx >= 1：严格锁死 step_idx == 0 生成的 Zone 3 尾注，守护轮内 KV Cache
            tail_injection_prompt = frozen_tail_prompt
        else:
            zone3_parts: list[str] = []
            if current_cwd:
                zone3_parts.append(f"当前本地终端持久化工作目录 (CWD): `{current_cwd}`")

            # 原生蜂群协作规约（仅在 swarm_mode=True 或角色为 Swarm 成员时按需注入，绝不污染普通模式）
            if swarm_mode or swarm_role:
                if swarm_role == "worker":
                    worker_name = swarm_member_name or "worker"
                    brief_line = f"\n你的职责简报：{swarm_brief}" if swarm_brief else ""
                    zone3_parts.append(
                        "<swarm_worker>\n"
                        f"你是蜂群成员 `{worker_name}`（角色：worker）。{brief_line}\n"
                        "协作铁律：\n"
                        "1. 收到任务后直接调用本地/技能工具完成交付，严禁空谈。\n"
                        "2. 需要与其他成员沟通或遇到阻塞时调用 `swarm_post(text=\"@成员名 ...\")`。\n"
                        "3. 任务完成后必须调用 `swarm_post(text=\"@coordinator [done] <产出物路径与关键结果摘要>\", kind=\"done\")` 汇报，"
                        "随后可调用 `swarm_wait(since=<已读最大seq>)` 挂起等待后续指令。\n"
                        "</swarm_worker>"
                    )
                else:
                    # 动态读取系统已注册的全部活跃专家名录（官方 + 个人），供 Coordinator 根据意图自动拆解并行派发
                    roster_lines: list[str] = []
                    try:
                        from ..repository.agent_repository import AgentProfileRepository
                        active_agents = await AgentProfileRepository.list_agents(status="active")
                        for ag in active_agents:
                            ag_id = ag.get("id") or ""
                            ag_name = ag.get("name") or ag_id
                            ag_desc = (ag.get("description") or "").strip()
                            if len(ag_desc) > 55:
                                ag_desc = ag_desc[:55] + "..."
                            dirs = ag.get("allowed_write_dirs") or []
                            dir_note = f" | 产出目录: {','.join(dirs)}" if dirs else ""
                            roster_lines.append(f"  - `agent_id=\"{ag_id}\"` ({ag_name}): {ag_desc}{dir_note}")
                    except Exception as roster_err:
                        logger.debug(f"[ContextBuilder] 加载 Swarm 专家名录跳过: {roster_err}")

                    roster_block = (
                        "\n可用专家名录（调用 `swarm_control(action=\"add\", agent_id=...)` 可直接继承其技能、工具白名单与写入边界）：\n"
                        + "\n".join(roster_lines)
                        if roster_lines
                        else ""
                    )

                    zone3_parts.append(
                        "<swarm_coordinator>\n"
                        "当前处于【蜂群协作协调者 (Swarm Coordinator)】模式。\n"
                        "协作与自动并行拆解铁律：\n"
                        "1. **自动拆解与严禁单干**：收到用户请求后，立即分析任务涉及的专业领域，拆解为互不冲突的独立子任务（或有向接力流水线）。\n"
                        "2. **同轮并行派发多专家**：优先从下方「可用专家名录」中匹配对应 `agent_id`（无匹配专家时可不传 `agent_id` 定制通用 Worker），"
                        "在**同一轮回复中连续调用多个 `swarm_control(action=\"add\", name=..., agent_id=..., brief=...)`**，"
                        "让多个 Worker 协程**多线程并行启动**。`brief` 必须写明输入路径、负责范围、禁止修改的目录及 `[done]` 汇报要求。\n"
                        "3. **零轮询等待与接力**：派发完成后调用 `swarm_wait(since=...)` 挂起等待汇报；"
                        "若有上下游依赖（如报表依赖审计结论），收到前置 `[done]` 后调用 `swarm_post(text=\"@下游专家 ...\")` 唤醒接力。\n"
                        "4. **验收与汇总**：所有节点均回报 `[done]` 后，核查产物文件并向用户汇总最终结果与交付链接。"
                        f"{roster_block}\n"
                        "</swarm_coordinator>"
                    )

            # Plan-First / Execute-Unlock / Clarify 状态门控放到 Zone 3，避免状态切换时击穿 Zone 1 前缀缓存
            effective_planning_gate = (
                effective_scene_mode == "agent"
                and execution_strategy == "plan_first"
                and not explicit_execute_unlock
                and (not has_prior_execution_unlock or explicit_clarify_lock)
            )

            if effective_scene_mode == "agent":
                if effective_planning_gate:
                    clarify_follow_tip = (
                        "\n0. **【上一轮澄清结论已汇入本轮上下文】**：用户上一轮选择先补充方案条件，请基于用户已确认结论完善方案；"
                        "本轮若仍有待拍板配置或最终实施步骤，请在当前唯一一张 `interactive_inquiry` 卡片内一次性完整收口，切勿继续碎片化追问！"
                        if explicit_clarify_lock
                        else ""
                    )
                    zone3_parts.append(
                        "### 📋【谋定而后动 (Plan-First)】前置对齐与方案规划硬性铁律（强制执行）：\n"
                        "写盘与命令执行当前处于**物理锁定态**（仅挂载只读工具，可静默检索/读文件定位真实代码），动工前必须先完成方案对齐："
                        f"{clarify_follow_tip}\n"
                        "1. **Markdown 输出结构**：先输出【实施方案（Implementation Plan）】与带编号和工具安全级别（`L0` 只读 / `L1` 受控文件 / `L2` 终端测试）的【分步执行步骤清单（Step-by-Step Task List）】。\n"
                        "2. **`interactive_inquiry` 语法与字段格式硬约束**：\n"
                        "   - 必须使用 ` ```interactive_inquiry ` 围栏代码块包裹合法 JSON（严禁 XML `<interactive_inquiry>` 或 `[interactive_inquiry]`）。\n"
                        "   - **单卡一次性收集全部确认项**：所有待确认参数、填空与 `plan_steps` 须在当前唯一卡片中一次列出，严禁多轮挤牙膏。\n"
                        '   - **严禁编造仅有单个无意义批准选项的空转伪选择题**：仅当存在至少 2 个实质差异分支时才填 `questions`；若仅审批步骤，请设 `"questions": []` 仅输出 `plan_steps`。每题必须使用 `"id"`、`"type"` (`"single_choice"`/`"multi_choice"`)、`"label"` 与对象数组 `"options": [{"value","label","desc"}]`。\n'
                        '   - 最小合法模板（`fill_template` 用 `{{参数名:默认值}}`；跨轮长期规则可为题目附 `"fact_key"`、`"fact_category"` (`user_pref|biz_rule|env_config`)、`"fact_scope"` (`workspace|global`)）：\n'
                        "```interactive_inquiry\n"
                        "{\n"
                        '  "inquiry_id": "inq_unique_id",\n'
                        '  "phase": "clarification 或 plan_approval",\n'
                        '  "title": "方案确认标题",\n'
                        '  "summary": "一句话核心说明",\n'
                        '  "questions": [\n'
                        "    {\n"
                        '      "id": "q1",\n'
                        '      "type": "single_choice",\n'
                        '      "label": "确认项标题",\n'
                        '      "options": [\n'
                        '        {"value": "opt_a", "label": "选项A", "desc": "推荐说明"},\n'
                        '        {"value": "opt_b", "label": "选项B", "desc": "备选说明"}\n'
                        "      ],\n"
                        '      "default": "opt_a"\n'
                        "    }\n"
                        "  ],\n"
                        '  "fill_template": {"label": "补充信息：", "template_text": "端口 {{PORT:8080}}，目录 {{TARGET_DIR:./output}}"},\n'
                        '  "plan_steps": [{"step": 1, "title": "步骤1简述", "tool_level": "L0"}]\n'
                        "}\n"
                        "```\n"
                        "3. **严禁跳过审批宣称落盘**：方案未获用户前端审批前，绝对禁止声明已落地修改。"
                    )
                elif explicit_execute_unlock:
                    steps_block = ""
                    if approved_plan_steps:
                        steps_summary = json.dumps(approved_plan_steps, ensure_ascii=False, indent=2)
                        steps_block = (
                            "用户同时核对并批准了以下实施步骤清单：\n"
                            f"```json\n{steps_summary}\n```\n"
                        )
                    zone3_parts.append(
                        "### 🚀【全部前置项已分步确认完毕 - 解锁全量工具立即执行到底（强制铁律）】：\n"
                        "用户已在前端交互向导中逐项确认完毕全部需求参数、配置选型与执行决策，写盘与本地终端执行权限现已**全部物理解锁**。\n"
                        f"{steps_block}"
                        "**【禁止二次弹窗确认红线】**：\n"
                        "1. 本轮回复**严禁再次输出任何 `interactive_inquiry` 卡片**！\n"
                        "2. 严禁停下来反问用户「是否立即执行」、「请点击确认」或重复输出实施方案征求同意！\n"
                        "3. 你必须立即在本轮连续输出 `<tool_call>` 标签真实调用 `write_local_file`、`execute_local_command` 等工具完成全部代码编写、脚本运行、产物生成与核验验证，直到最终成果物理交付完成再向用户汇报结果！"
                    )
                elif explicit_clarify_lock:
                    zone3_parts.append(
                        "### 🧭【继续细化方案（暂未触发写盘执行）】：\n"
                        "用户本轮选择了仅提交补充信息并先细化确认方案，请结合用户答案完善分析、澄清疑问或给出包含完整配置项与执行步骤的单张 `interactive_inquiry` 对齐卡，暂不改动业务交付文件。"
                    )
                elif has_prior_execution_unlock:
                    zone3_parts.append(
                        "### ⚡【同会话方案已解锁 - 清晰指令直接执行到底】：\n"
                        "当前会话此前已通过方案审批并已进入具身执行阶段（全量读写与终端工具现已保持开放）。\n"
                        "1. 若用户本轮指令目标清晰、可执行，**请立即直接调用 `<tool_call>` 完成修改、运行与校验交付**，严禁退回规划态或输出仅有「确认并立即执行」的空转确认卡！\n"
                        '2. 仅当新要求属于颠覆性重构且确实存在 **至少 2 项真实不可自动推断的互斥分支** 时，方可使用标准 ` ```interactive_inquiry ` 代码块一次性对齐所有分歧。'
                    )
            elif scene_mode == "chat" and clarify_mode:
                zone3_parts.append(
                    "### 🧭【需求前置主动澄清模式 (Clarify Mode)】：\n"
                    "用户开启了需求澄清模式。当用户的提问涉及多方案分歧、前提假设不明确或缺少场景约束时，切勿盲目输出长篇泛泛而谈的理论；\n"
                    '应先在正文中提炼核心考量，并在末尾通过**唯一一张**标准 ` ```interactive_inquiry ` 围栏代码块卡片完整逐条列出全部待确认问题，数量不限，供用户按步骤逐项确认完毕后再统一提交解答。'
                )

            # L4 确定性事实 + 长期记忆 + 冷区 FTS5 召回（无痕模式下严格跳过）
            if not incognito:
                raw_facts = await fact_manager.get_active_facts(workspace_id)
                if raw_facts:
                    fact_lines: list[str] = []
                    for f in raw_facts:
                        safe_val, _ = MemoryPolicyGuard.sanitize_text(str(f.get("fact_value") or ""))
                        fact_copy = dict(f)
                        fact_copy["fact_value"] = safe_val
                        active_facts.append(fact_copy)
                        scope_tag = "工作区契约" if f.get("scope") == "workspace" else "全局偏好"
                        fact_lines.append(
                            f"- `[{f.get('category', 'biz_rule')} | {scope_tag}]` `{f['fact_key']}` = `{safe_val}`"
                        )
                    zone3_parts.append(
                        "### 📌 确定性活跃事实视图 (L4 Active Fact View，工作区契约高优生效)：\n"
                        + "\n".join(fact_lines)
                    )

                referenced_memories = await long_term_memory.retrieve_relevant(
                    user_query, limit=5, min_confidence=0.5
                )
                if referenced_memories:
                    mem_lines: list[str] = []
                    for m in referenced_memories:
                        safe_content, _ = MemoryPolicyGuard.sanitize_text(str(m.get("content") or ""))
                        mem_lines.append(
                            f"- [{m['category']}] (置信度:{float(m['confidence']):.2f}) {safe_content}"
                        )
                    zone3_parts.append("### 💡 用户长期偏好与本机环境记忆 (Long-Term Memory)：\n" + "\n".join(mem_lines))

                # L5 实战微经验分级精准召回 (>=0.80 强指令并纳入赏罚反馈，0.65~0.80 提示参考，<0.65 过滤)
                try:
                    matched_cases = await episodic_experience_store.search_for_context(
                        user_query,
                        workspace_id=workspace_id,
                        limit=3,
                        min_score=0.65,
                    )
                    if matched_cases:
                        exp_lines: list[str] = []
                        for c in matched_cases:
                            safe_scene, _ = MemoryPolicyGuard.sanitize_text(c.scene_summary)
                            safe_root, _ = MemoryPolicyGuard.sanitize_text(c.root_cause)
                            safe_sol, _ = MemoryPolicyGuard.sanitize_text(c.verified_solution)
                            if c.match_score >= 0.80:
                                injected_case_ids.append(c.id)
                                exp_lines.append(
                                    f"- 🔴 **【历史高匹配避坑指南】(匹配度:{c.match_score:.2f}, 复用{c.hit_count}次)** {safe_scene}\n"
                                    f"  - 核心根因：{safe_root or '见历史排查记录'}\n"
                                    f"  - 已验证最优解法：{safe_sol}"
                                )
                            else:
                                exp_lines.append(
                                    f"- 🟡 **【相关实战经验参考】(匹配度:{c.match_score:.2f})** {safe_scene} -> {safe_sol[:160]}"
                                )
                        if exp_lines:
                            zone3_parts.append(
                                "### ⚡ L5 实战微经验与避坑先验 (Episodic Experience)：\n" + "\n".join(exp_lines)
                            )
                except Exception as exp_exc:
                    logger.warning(f"[ContextBuilder] 检索 L5 实战微经验失败（已跳过）: {exp_exc}")

                # 冷区 FTS5 召回（仅针对 turn_id <= collapse_anchor_turn_id 的已归档冷记忆）
                if collapse_anchor_turn_id > 0:
                    cold_history_hits = await SearchRepository.search_conversation_cold_history(
                        conversation_id=conversation_id,
                        query=user_query,
                        max_turn_id=collapse_anchor_turn_id,
                        limit=2,
                    )
                    if cold_history_hits:
                        cold_lines = ["<Relevant_History>"]
                        for hit in cold_history_hits:
                            cold_lines.append(
                                f"- [Turn {hit['turn_id']} · {hit['role']}]: {hit['snippet']}"
                            )
                        cold_lines.append("</Relevant_History>")
                        zone3_parts.append(
                            "### 🔎 相关早期历史片段召回 (冷区检索仅供参考)：\n" + "\n".join(cold_lines)
                        )

            # 按需专业技能摘要与激活规程（四元组开发规范仅在命中技能/MCP 开发意图时按需注入）
            include_dev_spec = _should_inject_skill_dev_spec(user_query)
            skills_summary = await asyncio.to_thread(
                skill_loader.get_skills_summary_prompt,
                include_dev_spec=include_dev_spec,
            )
            if skills_summary:
                zone3_parts.append(skills_summary)

            if current_cwd:
                def _list_ws_skills() -> list[dict[str, Any]]:
                    svc = SkillService(workspace_root=current_cwd)
                    return [s for s in svc.list_all_skills(include_content=False) if s.get("scope") == "workspace"]

                try:
                    ws_skills = await asyncio.to_thread(_list_ws_skills)
                    if ws_skills:
                        ws_lines = ["### 🗂️ 当前工作区专属技能 (Workspace L2 Skills，高优生效)："]
                        for ws_sk in ws_skills:
                            ovr = " (覆盖全局)" if ws_sk.get("is_override") else ""
                            ws_lines.append(
                                f"- [工作区{ovr}] `{ws_sk['name']}` (快捷命令 `{ws_sk['command']}`): {ws_sk.get('description', '')}"
                            )
                        zone3_parts.append("\n".join(ws_lines))
                except Exception as e:
                    logger.debug(f"[ContextBuilder] 扫描工作区专属技能跳过: {e}")

            if len(matched_skills) == 1:
                full_skill_md = await asyncio.to_thread(
                    skill_loader.get_full_skill_markdown,
                    matched_skills[0]["id"],
                    current_cwd,
                )
                zone3_parts.append(
                    f"### 🎯 已由斜杠指令 `{matched_skills[0]['command']}` 直接激活的四元组技能规程 (`{matched_skills[0]['id']}`)：\n"
                    f"请严格遵循以下技能规程处理用户请求：\n{full_skill_md}"
                )
            elif len(matched_skills) >= 2:
                combo_sections: list[str] = [
                    "### 🤝 【多 Skill 联合协同编排模式已激活】",
                    f"用户本轮共指定协同调用 **{len(matched_skills)} 个** 专业技能联合完成同一项任务，请按协同链路通盘统筹、逐个落实：",
                ]
                for idx, m_sk in enumerate(matched_skills, 1):
                    full_skill_md = await asyncio.to_thread(
                        skill_loader.get_full_skill_markdown,
                        m_sk["id"],
                        current_cwd,
                    )
                    sk_ver = m_sk.get("version") or "1.0.0"
                    combo_sections.append(
                        f"#### 🔗 协同技能 #{idx}: `{m_sk['command']}` (`{m_sk['id']}` · `v{sk_ver}`)\n"
                        f"{full_skill_md}"
                    )
                combo_sections.append(
                    "#### 📋 多 Skill 联合协作规范：\n"
                    "1. **依赖串联与数据流衔接**：先理清各技能上下游关系，将上游 Skill 的真实输出或产物文件路径作为下游 Skill 的输入，严禁跳过或编造数据；\n"
                    "2. **按需跨 Skill 调度**：涉及专属脚本或领域知识时，分别指定对应 `skill_name` 调用 `run_skill_tool` 与 `search_skill_knowledge`；\n"
                    "3. **合并交付与契约兼容**：若多个技能均携带 `schema.json` 或输出要求，需合并满足所有契约并在末尾完整汇总交付结果。"
                )
                zone3_parts.append("\n\n".join(combo_sections))

            # 自动探测当前最新轮次用户是否附带了新上传附件，以及用户是否显式要求使用历史旧数据
            latest_uploaded_att_names: list[str] = []
            try:
                latest_user_msgs = await MessageRepository.get_by_conversation(conversation_id, limit=5, from_latest=True)
                for lu_m in latest_user_msgs:
                    if lu_m.role == "user" and lu_m.content:
                        m_anc = attachment_service.ANCHOR_PATTERN.search(lu_m.content)
                        if m_anc:
                            parsed_anc = json.loads(m_anc.group(1))
                            if isinstance(parsed_anc, list) and parsed_anc:
                                latest_uploaded_att_names = [
                                    str(item.get("name") or "附件") for item in parsed_anc if item.get("name")
                                ]
                        break
            except Exception as scan_att_err:
                logger.debug(f"[ContextBuilder] 检测最新用户附件跳过: {scan_att_err}")

            user_q_lower = (user_query or "").lower()
            user_explicitly_requests_prior = any(
                k in user_q_lower
                for k in ["之前的数据", "历史数据", "上一次的数据", "上次的数据", "上次传的", "之前传的", "原有数据", "沿用之前", "基于上一次"]
            )

            # 当前工作区沙盒真实附件清单
            effective_ws_root = current_cwd
            if not effective_ws_root and workspace_id:
                try:
                    from ..repository.workspace_repository import WorkspaceRepository

                    effective_ws_root = await WorkspaceRepository.get_root_path_by_id(workspace_id)
                except Exception:
                    effective_ws_root = None
            if effective_ws_root:
                try:
                    sandbox_atts = await asyncio.to_thread(
                        attachment_service.list_workspace_sandbox_attachments,
                        effective_ws_root,
                        15,
                    )
                    if sandbox_atts:
                        if latest_uploaded_att_names and not user_explicitly_requests_prior:
                            att_header = (
                                f"### 📎 当前工作区沙盒已有真实附件清单（⚡ 本轮最新上传基准附件: `{', '.join(latest_uploaded_att_names)}`，所有分析推演必须以此最新文件为唯一输入原料）:"
                            )
                        else:
                            att_header = (
                                "### 📎 当前工作区沙盒已有真实附件清单（只读原料区，若未指定且无新上传则以按修改时间倒序排列的第一份为默认输入）:"
                            )
                        att_lines = [att_header]
                        for item in sandbox_atts:
                            att_lines.append(
                                f"- `{item['rel_path']}` ({item['size_str']}) — 绝对路径: `{item['file_path']}`"
                            )
                        zone3_parts.append("\n".join(att_lines))
                    elif scene_mode == "agent":
                        zone3_parts.append(
                            "### 📎 当前工作区沙盒附件目录状态:\n"
                            "- 当前沙盒 `attachments/` 目录暂为空（若需分析新文件请提示用户上传，严禁自行写入任何模拟/Mock 输入文件）。"
                        )
                except Exception as e:
                    logger.debug(f"[ContextBuilder] 扫描工作区沙盒附件跳过: {e}")

            # Working Memory 工作记忆草稿纸
            if scene_mode == "agent" and working_memory and working_memory != {}:
                if latest_uploaded_att_names and not user_explicitly_requests_prior:
                    wm_prefix = (
                        "### 📌 当前多步任务工作草稿纸 (Working Memory Scratchpad)：\n"
                        f"⚠️【最新附件基准变更警示】：检测到用户最新上传了全新附件（`{', '.join(latest_uploaded_att_names)}`）！\n"
                        "以下此前记录的工作记忆中，凡涉及历史旧文件的提取字段、数据统计与中间结论已全面作废！请严格以最新附件为准重新执行真实读取并覆盖更新工作记忆；切勿将旧数据当成本次分析的基础！\n"
                        "任务取得新进展时请调用 `update_working_memory` 更新，任务彻底完成时传 `task_completed=true` 清空：\n"
                    )
                else:
                    wm_prefix = (
                        "### 📌 当前多步任务工作草稿纸 (Working Memory Scratchpad)：\n"
                        "以下为你在此前步骤中记录的关键状态，请直接参考，切勿重复询问用户已知信息；"
                        "任务取得新进展时请调用 `update_working_memory` 更新，任务彻底完成时传 `task_completed=true` 清空：\n"
                    )
                zone3_parts.append(f"{wm_prefix}```json\n{json.dumps(working_memory, ensure_ascii=False, indent=2)}\n```")

            if not latest_uploaded_att_names and user_explicitly_requests_prior:
                zone3_parts.append(
                    "### 💡 数据基准指引：用户明确指示使用历史已有数据或上一轮分析结果，请基于历史对话与工作记忆中的数据推进后续分析。"
                )

            tail_injection_prompt = "\n\n".join(p for p in zone3_parts if p and p.strip())

        # ── Zone 2 (Append-Only Hot History): 仅读取坍缩锚点之后的热区消息，严禁逐轮左滑驱逐与跨轮二次改写 ──
        min_turn_id = max(1, collapse_anchor_turn_id + 1)
        raw_msgs = await MessageRepository.get_by_turn_window(conversation_id, min_turn_id)

        history_list: list[dict[str, Any]] = []
        for m in raw_msgs:
            if m.role == "tool":
                # 热区严格 Append-Only：统一使用入库时定型的 m.content（Write-Once），严禁跨轮切换为 compressed_content 破坏 KV Cache
                effective_text = m.content or m.compressed_content or "[工具执行完成]"
                history_list.append(
                    {
                        "role": "tool",
                        "turn_id": m.turn_id or 1,
                        "content": effective_text,
                        "tool_call_id": m.tool_call_id,
                    }
                )
            else:
                raw_c = m.content or ""
                if m.role == "user":
                    raw_c = attachment_service.render_attachments_for_llm_history(raw_c)
                if m.role in ("assistant", "model"):
                    raw_c = re.sub(r"\[调用工具:\s*[^\]]+\]", "", raw_c).strip()
                    if m.tool_calls_json and "<tool_call" not in raw_c:
                        try:
                            tc_list = json.loads(m.tool_calls_json)
                            if isinstance(tc_list, list):
                                xml_calls = "\n".join(
                                    f'<tool_call name="{tc.get("function", {}).get("name", "")}">{tc.get("function", {}).get("arguments", "{}")}</tool_call>'
                                    for tc in tc_list
                                )
                                raw_c = (raw_c + "\n" + xml_calls).strip() if raw_c else xml_calls
                        except Exception:
                            pass

                history_list.append(
                    {
                        "role": m.role,
                        "turn_id": m.turn_id or 1,
                        "content": raw_c,
                        "tool_calls_json": m.tool_calls_json,
                    }
                )

        # 触发双模分块坍缩检查（仅在热区积压超限或达 70%/75% Token 阈值时一次性打包旧轮次并固化进 Zone 1）
        collapse_result = await LocalContextCompressor.maybe_collapse_history(
            conversation_id=conversation_id,
            current_turn_id=current_turn_id,
            workspace_id=workspace_id,
            incognito=incognito,
            collapse_anchor_turn_id=collapse_anchor_turn_id,
            anchored_summary=anchored_summary,
            legacy_rolling_summary=rolling_summary,
            zone1_base_prompt=zone1_base_prompt,
            zone2_messages=history_list,
            zone3_tail_prompt=tail_injection_prompt,
            current_user_query=user_query,
            provider_info=provider_info,
            model_name=model_name,
            context_rounds_override=context_rounds_override,
        )

        final_anchored_summary = str(collapse_result.get("anchored_summary") or "").strip()
        final_zone2 = collapse_result.get("zone2_messages", history_list)
        final_anchor_turn = int(collapse_result.get("collapse_anchor_turn_id") or collapse_anchor_turn_id)
        collapsed_triggered = bool(collapse_result.get("collapsed_triggered", False))
        policy = collapse_result.get("policy") or {}

        # 若本轮刚刚触发了分块坍缩且非无痕模式，立即利用推进后的新锚点补充冷区召回（确保刚归档的知识本轮可用）
        if collapsed_triggered and not incognito and final_anchor_turn > 0 and not cold_history_hits and frozen_tail_prompt is None:
            cold_history_hits = await SearchRepository.search_conversation_cold_history(
                conversation_id=conversation_id,
                query=user_query,
                max_turn_id=final_anchor_turn,
                limit=2,
            )
            if cold_history_hits:
                cold_lines = ["<Relevant_History>"]
                for hit in cold_history_hits:
                    cold_lines.append(f"- [Turn {hit['turn_id']} · {hit['role']}]: {hit['snippet']}")
                cold_lines.append("</Relevant_History>")
                cold_block = "### 🔎 相关早期历史片段召回 (冷区检索仅供参考)：\n" + "\n".join(cold_lines)
                tail_injection_prompt = f"{tail_injection_prompt}\n\n{cold_block}" if tail_injection_prompt else cold_block

        # 将已固化的 Anchored Summary 拼入 Zone 1 末尾（日常非坍缩轮次该片段字节完全冻结不变）
        zone1_final_parts = [zone1_base_prompt]
        if final_anchored_summary and not incognito:
            safe_summary, _ = MemoryPolicyGuard.sanitize_text(final_anchored_summary)
            zone1_final_parts.append(f"### 📜 L3 Session Summary (已固化历史摘要 Anchored Summary)：\n{safe_summary}")
        final_system_instruction = "\n\n".join(p for p in zone1_final_parts if p)

        # 极端保底防爆阀（仅作用于当前渲染列表，绝不改动数据库原始消息）：
        # 1) 本地/云端按上限 88% 控制 Token；2) gemini_cli 通道额外守护 48K 渲染字符软上限，防止下游 64K 粗暴腰斩
        max_ctx_limit = int(policy.get("max_context_tokens") or 8192)
        hard_ceiling = int(max_ctx_limit * 0.88)
        is_cli_provider = str((provider_info or {}).get("provider_type") or "").strip().lower() == "gemini_cli"
        cli_char_ceiling = 48_000

        est_now = (
            estimate_tokens(final_system_instruction)
            + estimate_tokens(tail_injection_prompt)
            + estimate_tokens(user_query)
            + sum(estimate_tokens(str(item.get("content") or "")) + 8 for item in final_zone2)
        )
        est_chars_now = (
            len(final_system_instruction)
            + len(tail_injection_prompt)
            + len(user_query)
            + sum(len(str(item.get("content") or "")) + 48 for item in final_zone2)
        )

        def _over_guard_limit() -> bool:
            return est_now > hard_ceiling or (is_cli_provider and est_chars_now > cli_char_ceiling)

        if _over_guard_limit() and len(final_zone2) > 2:
            # 逼近窗口硬上限时，先批量折叠历史文件写入以释放空间，尽量避免破坏热区正文
            guarded_zone2 = LocalContextCompressor.fold_historical_file_writes(
                final_zone2,
                current_turn_id=current_turn_id,
            )
            est_now = (
                estimate_tokens(final_system_instruction)
                + estimate_tokens(tail_injection_prompt)
                + estimate_tokens(user_query)
                + sum(estimate_tokens(str(item.get("content") or "")) + 8 for item in guarded_zone2)
            )
            est_chars_now = (
                len(final_system_instruction)
                + len(tail_injection_prompt)
                + len(user_query)
                + sum(len(str(item.get("content") or "")) + 48 for item in guarded_zone2)
            )
            total_n = len(guarded_zone2)

            def _trim_item_text(item_dict: dict[str, Any], head_len: int, tail_len: int, notice: str) -> None:
                nonlocal est_now, est_chars_now
                orig_txt = str(item_dict.get("content") or "")
                truncated = f"{orig_txt[:head_len]}\n... {notice} ...\n{orig_txt[-tail_len:]}"
                if len(truncated) < len(orig_txt):
                    est_now -= max(0, estimate_tokens(orig_txt) - estimate_tokens(truncated))
                    est_chars_now -= len(orig_txt) - len(truncated)
                    item_dict["content"] = truncated

            # 第一轮优先裁剪历史旧工具结果
            for idx, item_copy in enumerate(guarded_zone2):
                if not _over_guard_limit():
                    break
                if (
                    item_copy.get("role") == "tool"
                    and int(item_copy.get("turn_id") or 1) < int(current_turn_id)
                    and idx < total_n - 4
                    and len(str(item_copy.get("content") or "")) > 600
                ):
                    _trim_item_text(
                        item_copy,
                        260,
                        160,
                        "[应急上下文保护已省略过长旧输出，如需详情请调用 read_tool_ref]",
                    )

            # 第二轮仅当仍超标时，按从老到新对非当前轮次历史超大正文/代码回复做视图摘要裁剪
            for idx, item_copy in enumerate(guarded_zone2):
                if not _over_guard_limit():
                    break
                if (
                    int(item_copy.get("turn_id") or 1) < int(current_turn_id)
                    and idx < total_n - 2
                    and len(str(item_copy.get("content") or "")) > 1200
                ):
                    _trim_item_text(
                        item_copy,
                        400,
                        240,
                        "[应急上下文保护已省略历史长篇代码/文本（共节省冗余前缀），如需查看完整内容请读取对应工作区文件]",
                    )

            # 第三轮极端兜底：仅当最近 1 个已完结轮次单条本身已爆炸（如 > 8,000 字）且仍超限，保留较宽首尾防撞 64K 截断
            for item_copy in guarded_zone2:
                if not _over_guard_limit():
                    break
                if (
                    int(item_copy.get("turn_id") or 1) < int(current_turn_id)
                    and len(str(item_copy.get("content") or "")) > 8000
                ):
                    _trim_item_text(
                        item_copy,
                        4000,
                        2500,
                        "[应急上下文保护已折叠超大单步历史产物，完整文件已落盘于工作区]",
                    )

            final_zone2 = guarded_zone2

        return {
            "system_instruction": final_system_instruction,
            "history_messages": final_zone2,
            "tail_injection_prompt": tail_injection_prompt,
            "referenced_memories": referenced_memories,
            "active_facts": active_facts,
            "cold_history_hits": cold_history_hits,
            "injected_case_ids": injected_case_ids,
            "collapse_anchor_turn_id": final_anchor_turn,
            "collapsed_triggered": collapsed_triggered,
            "compression_policy": policy,
            "matched_skill_id": matched_skill["id"] if matched_skill else None,
            "matched_skill_ids": [s["id"] for s in matched_skills],
            "scene_mode": effective_scene_mode,
            "working_memory": working_memory,
            "current_cwd": current_cwd,
        }

