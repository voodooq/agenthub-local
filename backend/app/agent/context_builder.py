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
                "1. 你拥有真实的本地工具调用能力（支持 write_local_file, read_local_file, read_tool_ref, execute_local_command 等）。\n"
                "2. 所有针对本地磁盘的文件创建、修改与终端执行，【必须且只能】通过输出标准 `<tool_call>` 标签真实调用工具完成！\n"
                "3. 【严禁未执行先声明】：严禁在尚未输出 `<tool_call>` 或尚未收到真实的工具执行返回之前，在文本中宣称“已为你创建了某文件”、“已在本地落盘”。\n"
                "4. 只有当工具真实执行成功并返回结果后，你才能告知用户文件已落盘并展示真实操作结果。\n"
                "5. 【自主闭环与异常自愈铁律（严禁报错卡死退出）】：在本地开发、依赖安装、代码构建与测试运行过程中，若遇到终端命令报错（如 exit_code != 0、ENOENT 找不到 package.json、缺少路径参数等），【绝对严禁中途卡住或直接退出】！你必须基于报错信息第一性原理分析根因，主动调用工具自愈（如使用 `--prefix <相对路径>` 指定子目录执行、补全缺失文件、切换路径或执行修复脚本），直到整个功能与依赖全部完整跑通并交付验证！\n"
                "6. 【真实输入附件红线 —— 严禁伪造与 Mock 替代（零容忍违规）】：\n"
                "   - 用户上传的原始文档/Excel/图片统一落盘在工作区只读附件目录（虚拟工作区 `./attachments/`，物理挂载工程 `./.agenthub/attachments/`）。严禁调用 `write_local_file` 或编写脚本向附件目录写入/篡改文件；\n"
                "   - 若遇到预期输入文件缺失、路径报错、Sheet 名称不匹配或解析异常，**绝对严禁私自生成任何 Mock / 模拟 / 测试样例输入文件来“兜底”完成分析**！编写的数据处理脚本必须坚持 Fail-Fast 原则（缺失即抛 `FileNotFoundError` 并以非零码退出），严禁内嵌“不存在就造默认测试数据继续运行”的伪分支；\n"
                "   - 确系真实文件缺失或无法解析时，必须立即停机并向用户如实报告预期路径与缺失原因。\n"
                "7. 【⚡ 极速执行与代码规范（强制遵循，违者终止任务）】：\n"
                "   - 【已有文件修改首选 replace_in_file】：修改已有代码或脚本时，【必须优先且强烈推荐使用 `replace_in_file`】提交标准的 `SEARCH/REPLACE` 补丁，绝对严禁对数百行的已有代码调用 `write_local_file` 全量重写！对于超长文件先调用 `get_repo_skeleton` 获取结构大纲或用 `read_local_file` 切片读取！\n"
                "   - 【单步多工具并行调用】：若一个步骤需要执行多项操作（如读取多个文件、写入多个配置或串行多个命令），你必须在【同一次回复】中连续并列输出多个 `<tool_call>` 标签一次性并发提交系统执行，严禁一次只调用一个工具来回拉扯 5~10 轮！\n"
                "   - 【原地修改文件，严禁创建副本】：必须原地覆盖原有文件名（如 `render.py`），绝对严禁创建类似 `render_v1.py`、`render_v2.py`、`render_fixed.py` 等衍生副本，违者判定为严重的代码污染；\n"
                "   - 【Python 一次性诊断】：面对数据探索或环境探测，禁止写一个脚本看 Sheet1、再写一个脚本看 Sheet2、再写一个看列名；必须在一个紧凑的 Python 脚本中一次性打印出所有元信息与关键样例，一步完成诊断！"
            )
            if tools:
                protocol_prompt = ToolProtocolAdapter.build_xml_protocol_prompt(tools)
                if protocol_prompt:
                    zone1_static_parts.append(protocol_prompt)

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
                        "当前任务已启用【谋定而后动】策略。动工执行任何破坏性写入、文件修改或终端命令之前，必须先完成方案梳理与用户对齐！"
                        f"{clarify_follow_tip}\n"
                        "1. 【物理安全硬门控处于锁定态】：当前阶段系统仅挂载了只读工具（如 read_local_file、read_tool_ref、检索等），写盘与修改已被安全锁定。你可以在本阶段先静默调用只读工具查看项目文件与目录结构，以确保制定的方案严谨且精准定位真实代码。\n"
                        "2. 【输出结构规范】：\n"
                        "   - 先输出结构清晰的 Markdown【实施方案（Implementation Plan）】与【分步执行步骤清单（Step-by-Step Task List）】；\n"
                        "   - 步骤必须按序号标注（如 步骤 1、步骤 2...），并标注对应的工具安全级别（L0 只读 / L1 本地受控文件操作 / L2 终端执行与测试）；\n"
                        "3. 【交互式对齐协议规范 (```interactive_inquiry)】：\n"
                        '   - **【语法与字段格式硬约束】**：必须使用标准 Markdown 三反引号围栏代码块 ` ```interactive_inquiry ` 包裹合法 JSON；**严禁**使用 XML `<interactive_inquiry>...</interactive_inquiry>` 或 `[interactive_inquiry]` 标签！每道题必须使用 `"id"`（勿用 `question_id`）、`"type"` (`"single_choice"` / `"multi_choice"`)、`"label"`（勿用 `question`）以及对象数组 `"options": [{"value": "...", "label": "...", "desc": "..."}]`（严禁纯字符串数组）；\n'
                        "   - **【单卡一次性收集全部确认项，严禁挤牙膏二次弹卡】**：如果任务存在多个参数设置、路线分歧、填空项或待审批步骤，**必须按问题实际需要，在当前唯一一张 `interactive_inquiry` 卡片的 `questions`、`fill_template` 与 `plan_steps` 中一次性全部列出（不人为限缩数量，切勿拆多轮）**；前端会自动通过分步向导引导用户逐项确认完毕后统一点击执行；\n"
                        "   - **【严禁编造仅有单个无意义批准选项的空转伪选择题】**：`questions` 仅在确实存在 **至少 2 个有实质差异的可选分支或配置参数** 时才使用；绝对禁止在 `questions` 中制造只有一个「确认并立即执行」选项的无意义伪问题！若仅需用户审批步骤清单而无真实多分支分歧，请设 `\"questions\": []`（或省略 `questions`）仅输出 `plan_steps`；\n"
                        "   - 若需求尚存不确定点或缺少关键参数，可在末尾输出带 `questions` / `fill_template` 的标准 JSON 协议块；若已可直接启动实施，请在末尾输出带 `phase: \"plan_approval\"` 与完整 `plan_steps` 的协议块（可同时携带 `questions` 与 `fill_template`，前端会在向导中逐步引导用户确认完再统一开始执行）：\n"
                        "```interactive_inquiry\n"
                        "{\n"
                        '  "inquiry_id": "inq_unique_id",\n'
                        '  "phase": "clarification 或 plan_approval",\n'
                        '  "title": "方案与意图确认标题",\n'
                        '  "summary": "一句话核心说明",\n'
                        '  "questions": [\n'
                        '    {\n'
                        '      "id": "q1",\n'
                        '      "type": "single_choice 或 multi_choice",\n'
                        '      "label": "确认项 1 标题说明",\n'
                        '      "options": [\n'
                        '        {"value": "opt_a", "label": "选项A名称", "desc": "推荐理由或说明"},\n'
                        '        {"value": "opt_b", "label": "选项B名称", "desc": "备选说明"}\n'
                        "      ],\n"
                        '      "default": "opt_a"\n'
                        '    },\n'
                        '    {\n'
                        '      "id": "q2",\n'
                        '      "type": "multi_choice",\n'
                        '      "label": "确认项 2 多选说明",\n'
                        '      "options": [\n'
                        '        {"value": "opt_1", "label": "选项1名称", "desc": "说明"},\n'
                        '        {"value": "opt_2", "label": "选项2名称", "desc": "说明"}\n'
                        '      ]\n'
                        "    }\n"
                        "  ],\n"
                        '  "fill_template": {\n'
                        '    "label": "若需用户补充信息，提供留空填空模板：",\n'
                        '    "template_text": "配置参数示例：端口号为 {{PORT:8080}}，目标路径为 {{TARGET_DIR:./output}}。"\n'
                        "  },\n"
                        '  "plan_steps": [\n'
                        '    {"step": 1, "title": "步骤 1 简述", "tool_level": "L0"},\n'
                        '    {"step": 2, "title": "步骤 2 简述", "tool_level": "L1"}\n'
                        "  ]\n"
                        "}\n"
                        "```\n"
                        "   - 提示：留空填空模板中，使用 `{{参数名:默认值或提示}}` 作为标准占位符；仅针对需要跨轮长期复用的规则/偏好，在 `questions` 项中附带可选字段 `\"fact_key\"`、`\"fact_category\"` (`user_pref|biz_rule|env_config`) 与 `\"fact_scope\"` (`workspace|global`)。\n"
                        "4. 【严禁跳过方案强行写盘】：方案未获得用户在前端确认审批通过前，绝对禁止声明已落地修改！"
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

            # 按需专业技能摘要与激活规程
            skills_summary = await asyncio.to_thread(skill_loader.get_skills_summary_prompt)
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
                        att_lines = [
                            "### 📎 当前工作区沙盒已有真实附件清单（只读原料区，严禁写入或伪造）:"
                        ]
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
                zone3_parts.append(
                    "### 📌 当前多步任务工作草稿纸 (Working Memory Scratchpad)：\n"
                    "以下为你在此前步骤中记录的关键状态，请直接参考，切勿重复询问用户已知信息；"
                    "任务取得新进展时请调用 `update_working_memory` 更新，任务彻底完成时传 `task_completed=true` 清空：\n"
                    f"```json\n{json.dumps(working_memory, ensure_ascii=False, indent=2)}\n```"
                )

            tail_injection_prompt = "\n\n".join(p for p in zone3_parts if p and p.strip())

        # ── Zone 2 (Append-Only Hot History): 仅读取坍缩锚点之后的热区消息，严禁逐轮左滑驱逐与跨轮二次改写 ──
        min_turn_id = max(1, collapse_anchor_turn_id + 1)
        raw_msgs = await MessageRepository.get_by_turn_window(conversation_id, min_turn_id)

        history_list: list[dict[str, Any]] = []
        for m in raw_msgs:
            if m.role == "tool":
                # 针对非当前轮次的历史工具输出，优先使用旁路脱水后的压缩摘要，避免老日志占满上下文
                if (m.turn_id or 1) < int(current_turn_id) and m.compressed_content:
                    effective_text = m.compressed_content
                else:
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
        # 若单轮或最近 3 轮总工具返回依然超过上限 88%，截断热区最老的超大非当前轮次超长文本避免本地服务 400 Context Overflow
        max_ctx_limit = int(policy.get("max_context_tokens") or 8192)
        hard_ceiling = int(max_ctx_limit * 0.88)
        est_now = (
            estimate_tokens(final_system_instruction)
            + estimate_tokens(tail_injection_prompt)
            + estimate_tokens(user_query)
            + sum(estimate_tokens(str(item.get("content") or "")) + 8 for item in final_zone2)
        )
        if est_now > hard_ceiling and len(final_zone2) > 2:
            # 仅拷贝字典，不污染外部引用；优先只保留最近轮次原文，对更早工具消息做应急视图裁剪
            guarded_zone2: list[dict[str, Any]] = []
            for idx, item in enumerate(final_zone2):
                item_copy = dict(item)
                is_old_tool = (
                    item_copy.get("role") == "tool"
                    and int(item_copy.get("turn_id") or 1) < int(current_turn_id)
                    and idx < len(final_zone2) - 4
                )
                if is_old_tool and len(str(item_copy.get("content") or "")) > 600 and est_now > hard_ceiling:
                    orig_txt = str(item_copy.get("content") or "")
                    truncated = orig_txt[:260] + "\n... [应急上下文保护已省略过长旧输出，如需详情请调用 read_tool_ref] ...\n" + orig_txt[-160:]
                    est_now -= max(0, estimate_tokens(orig_txt) - estimate_tokens(truncated))
                    item_copy["content"] = truncated
                guarded_zone2.append(item_copy)
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

