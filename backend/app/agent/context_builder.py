"""
多轮上下文动态组装器 (ContextBuilder) 与会话自愈器 (SessionHealer)
核心职责：
1. SessionHealer: 自动检测因断电/关闭窗口/用户打断而残留的未闭合 tool_calls，原地补插 Cancelled 兜底消息，杜绝 API 400 报错
2. Observation Masking: 历史轮次 (turn_id < current_turn_id) 的超长工具输出自动替换为单行 compressed_content 摘要
3. 按场景 (oneshot / chat / agent) 差异化装配：长期记忆 Top 5 + 工作记忆草稿纸 + 短期 Turn 滑动窗口 + 工具协议
"""
import asyncio
import json
import re
from typing import Any

from ..core.logging_config import logger
from ..repository.conversation_repository import ConversationRepository
from ..repository.message_repository import MessageRepository
from ..memory.fact_manager import fact_manager
from ..memory.long_term import long_term_memory
from ..memory.policy_guard import MemoryPolicyGuard
from ..memory.session_summary import session_summary_manager
from ..extensions.skill_loader import skill_loader
from ..skills.skill_service import SkillService
from ..service.attachment_service import attachment_service
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
    ) -> dict[str, Any]:
        """
        为当前推理步骤构建完整上下文载荷。

        Returns:
            dict: {
                "system_instruction": 组装后的完整系统指令,
                "history_messages": 经过滑动窗口与 Observation Masking 降噪后的历史消息列表,
                "referenced_memories": 本次命中的长期记忆列表 (供前端气泡展示溯源提示),
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
                "referenced_memories": [],
                "active_facts": [],
                "scene_mode": "oneshot",
                "working_memory": {},
                "current_cwd": current_cwd,
            }

        # ── 模式 B/C: 💬 标准对话模式 (chat) 与 🛠️ Agent 任务模式 (agent) ──
        system_parts: list[str] = []
        if base_system_prompt:
            system_parts.append(base_system_prompt)
        else:
            system_parts.append("你是 AgentHub Local 智能研发中枢，运行在用户本地私有环境中。")

        # 核心防幻觉物理红线
        if scene_mode == "chat":
            system_parts.append(
                "### ⚠️ 运行模式与物理能力红线（强制遵守）：\n"
                "你当前处于【💬 标准对话模式 / 纯文本咨询】。在此模式下，你【没有】被挂载任何本地磁盘写入工具、Shell 执行工具或操作系统修改权限！\n"
                "1. 【严禁虚构已执行操作】：绝对禁止声称“我已经为你写入了文件”、“已在本地创建了目录/代码”、“已执行了命令”或“请刷新查看磁盘”！此类虚构言论属于严重的 AI 代码幻觉，受到系统安全审计监控！\n"
                "2. 【诚实交付代码】：你编写的所有代码必须直接在回复的 Markdown 代码块中完整呈现，并明确提示用户：“当前处于标准对话模式，我无法直接修改本地磁盘，请手动复制上方代码保存；若需我自动为你落地到本地文件，请点击输入框下方切换至【Agent 任务】模式”。"
            )
        elif scene_mode == "agent":
            system_parts.append(
                "### ⚠️ Agent 任务模式落盘真实性与自主自愈铁律（强制遵守）：\n"
                "1. 你拥有真实的本地工具调用能力（支持 write_local_file, read_local_file, execute_local_command 等）。\n"
                "2. 所有针对本地磁盘的文件创建、修改与终端执行，【必须且只能】通过输出标准 `<tool_call>` 标签真实调用工具完成！\n"
                "3. 【严禁未执行先声明】：严禁在尚未输出 `<tool_call>` 或尚未收到真实的工具执行返回之前，在文本中宣称“已为你创建了某文件”、“已在本地落盘”。\n"
                "4. 只有当工具真实执行成功并返回结果后，你才能告知用户文件已落盘并展示真实操作结果。\n"
                "5. 【自主闭环与异常自愈铁律（严禁报错卡死退出）】：在本地开发、依赖安装、代码构建与测试运行过程中，若遇到终端命令报错（如 exit_code != 0、ENOENT 找不到 package.json、缺少路径参数等），【绝对严禁中途卡住或直接退出】！你必须基于报错信息第一性原理分析根因，主动调用工具自愈（如使用 `--prefix <相对路径>` 指定子目录执行、补全缺失文件、切换路径或执行修复脚本），直到整个功能与依赖全部完整跑通并交付验证！"
            )

        if current_cwd:
            system_parts.append(f"当前本地终端持久化工作目录 (CWD): `{current_cwd}`")

        # ── 谋定而动 (Plan-First) 与交互对齐协议注入 ──
        if scene_mode == "agent":
            if execution_strategy == "plan_first" and not approved_plan_steps:
                system_parts.append(
                    "### 📋【谋定而后动 (Plan-First)】前置对齐与方案规划硬性铁律（强制执行）：\n"
                    "当前任务已启用【谋定而后动】策略。动工执行任何破坏性写入、文件修改或终端命令之前，必须先完成方案梳理与用户对齐！\n"
                    "1. 【物理安全硬门控处于锁定态】：当前阶段系统仅挂载了只读工具（如 read_local_file、检索等），写盘与修改已被安全锁定。你可以在本阶段先静默调用只读工具查看项目文件与目录结构，以确保制定的方案严谨且精准定位真实代码。\n"
                    "2. 【输出结构规范】：\n"
                    "   - 先输出结构清晰的 Markdown【实施方案（Implementation Plan）】与【分步执行步骤清单（Step-by-Step Task List）】；\n"
                    "   - 步骤必须按序号标注（如 步骤 1、步骤 2...），并标注对应的工具安全级别（L0 只读 / L1 本地受控文件操作 / L2 终端执行与测试）；\n"
                    "3. 【交互式对齐协议规范 (```interactive_inquiry)】：\n"
                    "   - 若需求存在技术路线分歧、多方案选择或缺少关键参数，必须在回复末尾附带如下标准 JSON 协议块（包含单选/多选/留空模板）：\n"
                    "   - 若方案完整无歧义，直接在末尾输出带 `phase: \"plan_approval\"` 的标准 JSON 协议块，供用户一键审批启动执行：\n"
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
                    '      "label": "选择题标题说明",\n'
                    '      "options": [\n'
                    '        {"value": "opt_a", "label": "选项A名称", "desc": "推荐理由或说明"},\n'
                    '        {"value": "opt_b", "label": "选项B名称", "desc": "备选说明"}\n'
                    "      ],\n"
                    '      "default": "opt_a"\n'
                    "    }\n"
                    "  ],\n"
                    '  "fill_template": {\n'
                    '    "label": "若需用户补充信息，提供留空填空模板：",\n'
                    '    "template_text": "配置参数示例：端口号为 {{PORT:8080}}，目标路径为 {{TARGET_DIR:./output}}。"\n'
                    "  },\n"
                    '  "plan_steps": [\n'
                    '    {"step": 1, "title": "步骤简述", "tool_level": "L0"},\n'
                    '    {"step": 2, "title": "步骤简述", "tool_level": "L1"}\n'
                    "  ]\n"
                    "}\n"
                    "```\n"
                    "   - 提示：留空填空模板中，使用 `{{参数名:默认值或提示}}` 作为标准占位符，前端支持光标高亮和 Tab 键快速跳转填空；仅针对需要跨轮长期复用的规则/偏好，在 `questions` 项中附带可选字段 `\"fact_key\"`、`\"fact_category\"` (`user_pref|biz_rule|env_config`) 与 `\"fact_scope\"` (`workspace|global`)。\n"
                    "4. 【严禁跳过方案强行写盘】：方案未获得用户在前端确认审批通过前，绝对禁止声明已落地修改！"
                )
            elif approved_plan_steps:
                steps_summary = json.dumps(approved_plan_steps, ensure_ascii=False, indent=2)
                system_parts.append(
                    "### 🚀【方案已获用户审批批准 - 全速具身执行指令】：\n"
                    "用户已在前端卡片中确认批准了以下实施步骤清单，写盘与终端执行权限现已全部解锁：\n"
                    f"```json\n{steps_summary}\n```\n"
                    "请严格对照上述批准的步骤清单，逐一调用 `<tool_call>` 真实执行落盘、修改、测试与自愈验证，直到全量步骤彻底达成！"
                )
        elif scene_mode == "chat" and clarify_mode:
            system_parts.append(
                "### 🧭【需求前置主动澄清模式 (Clarify Mode)】：\n"
                "用户开启了需求澄清模式。当用户的提问涉及多方案分歧、前提假设不明确或缺少场景约束时，切勿盲目输出长篇泛泛而谈的理论；\n"
                "应先在正文中提炼核心考量，并在末尾输出 ```interactive_inquiry 结构化卡片（包含 phase: 'clarification' 与 questions 选择题或 fill_template 模板；可长期复用的配置问题可附加 fact_key / fact_category / fact_scope），供用户一键对齐上下文后再展开深度解答。"
            )

        # 2. [L4 确定性事实视图] 挂载 Active Fact View（工作区契约严格覆盖 GLOBAL，无痕模式跳过）
        active_facts: list[dict[str, Any]] = []
        referenced_memories: list[dict[str, Any]] = []
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
                system_parts.append(
                    "### 📌 确定性活跃事实视图 (L4 Active Fact View，工作区契约高优生效)：\n"
                    + "\n".join(fact_lines)
                )

            # 3. [L4 非结构化画像补充] 检索并挂载长期记忆 Top 5
            referenced_memories = await long_term_memory.retrieve_relevant(user_query, limit=5, min_confidence=0.5)
            if referenced_memories:
                mem_lines: list[str] = []
                for m in referenced_memories:
                    safe_content, _ = MemoryPolicyGuard.sanitize_text(str(m.get("content") or ""))
                    mem_lines.append(
                        f"- [{m['category']}] (置信度:{float(m['confidence']):.2f}) {safe_content}"
                    )
                system_parts.append("### 💡 用户长期偏好与本机环境记忆 (Long-Term Memory)：\n" + "\n".join(mem_lines))

            # 4. [L3 增量会话摘要] 优先挂载 session_summaries，无记录时兼容回退 rolling_summary
            summary_record = await session_summary_manager.get_summary(conversation_id)
            effective_summary = (
                (summary_record.get("summary_markdown") or "").strip()
                if summary_record
                else rolling_summary.strip()
            )
            if effective_summary:
                safe_summary, _ = MemoryPolicyGuard.sanitize_text(effective_summary)
                system_parts.append(
                    f"### 📜 更早期的历史对话增量浓缩摘要 (L3 Session Summary)：\n{safe_summary}"
                )

        # 4. 挂载按需专业技能摘要 (Skills Summary) 与斜杠指令直通激活（放到线程池防止大目录扫描卡顿）
        skills_summary = await asyncio.to_thread(skill_loader.get_skills_summary_prompt)
        if skills_summary:
            system_parts.append(skills_summary)

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
                    system_parts.append("\n".join(ws_lines))
            except Exception as e:
                logger.debug(f"[ContextBuilder] 扫描工作区专属技能跳过: {e}")

        # 若用户输入了 /command 或 /skill-name，自动预加载对应 Skill 的完整四元组规程（含 tools/、knowledge/、schema.json 与避坑经验摘要）
        matched_skill = await asyncio.to_thread(skill_loader.resolve_slash_command, user_query, current_cwd)
        if matched_skill:
            full_skill_md = await asyncio.to_thread(
                skill_loader.get_full_skill_markdown,
                matched_skill["id"],
                current_cwd,
            )
            system_parts.append(
                f"### 🎯 已由斜杠指令 `{matched_skill['command']}` 直接激活的四元组技能规程 (`{matched_skill['id']}`)：\n"
                f"请严格遵循以下技能规程处理用户请求：\n{full_skill_md}"
            )

        # 5. Agent 任务模式下：挂载工作记忆草稿纸 (Working Memory Scratchpad) 与工具协议
        if scene_mode == "agent":
            if working_memory and working_memory != {}:
                system_parts.append(
                    "### 📌 当前多步任务工作草稿纸 (Working Memory Scratchpad)：\n"
                    "以下为你在此前步骤中记录的关键状态，请直接参考，切勿重复询问用户已知信息；"
                    "任务取得新进展时请调用 `update_working_memory` 更新，任务彻底完成时传 `task_completed=true` 清空：\n"
                    f"```json\n{json.dumps(working_memory, ensure_ascii=False, indent=2)}\n```"
                )
            if tools:
                protocol_prompt = ToolProtocolAdapter.build_xml_protocol_prompt(tools)
                if protocol_prompt:
                    system_parts.append(protocol_prompt)

        # 6. 组装短期记忆滑动窗口（按 Turn 截取，而非零散消息条数）
        default_turns = 12 if scene_mode == "chat" else 10
        max_turns = context_rounds_override if context_rounds_override is not None else default_turns

        history_list: list[dict[str, Any]] = []
        if max_turns > 0:
            min_turn_id = max(1, current_turn_id - max_turns)
            raw_msgs = await MessageRepository.get_by_turn_window(conversation_id, min_turn_id)

            # 轮内滑动窗口降噪 (Intra-Turn Rolling Mask):
            # 统计当前轮次中的所有工具消息，仅保留最近 4 步的完整 output，早期步数自动切换为 compressed 摘要
            current_turn_tool_msgs = [
                m for m in raw_msgs if m.role == "tool" and (m.turn_id or 1) == current_turn_id
            ]
            recent_tool_ids = {
                id(m) for m in current_turn_tool_msgs[-4:]
            }

            for m in raw_msgs:
                # 双层降噪策略 (Observation Masking)：
                # 1. 历史轮次 (turn_id < current_turn_id) 一律使用单行折叠摘要；
                # 2. 当前轮次内部 (turn_id == current_turn_id) 超过最近 4 步的前期工具输出同样自动轻量折叠，杜绝 40 步长程任务 Token 膨胀
                if m.role == "tool":
                    is_current_turn = (m.turn_id or 1) == current_turn_id
                    if not is_current_turn or id(m) not in recent_tool_ids:
                        effective_text = m.compressed_content or (
                            f"[已折叠前期工具输出: 共 {len(m.content or '')} 字符]" if m.content else "[工具执行完成]"
                        )
                    else:
                        effective_text = m.content

                    history_list.append({
                        "role": "tool",
                        "turn_id": m.turn_id or 1,
                        "content": effective_text,
                        "tool_call_id": m.tool_call_id,
                    })
                else:
                    raw_c = m.content or ""
                    if m.role == "user":
                        raw_c = attachment_service.render_attachments_for_llm_history(raw_c)
                    # 消除可能带偏大模型的模式模仿文本 [调用工具: ...]
                    if m.role in ("assistant", "model"):
                        raw_c = re.sub(r"\[调用工具:\s*[^\]]+\]", "", raw_c).strip()
                        # 若消息带有 tool_calls_json，并且 content 中尚未包含 <tool_call>，补齐标准 XML 示范
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

                    history_list.append({
                        "role": m.role,
                        "turn_id": m.turn_id or 1,
                        "content": raw_c,
                        "tool_calls_json": m.tool_calls_json,
                    })

        return {
            "system_instruction": "\n\n".join(system_parts),
            "history_messages": history_list,
            "referenced_memories": referenced_memories,
            "active_facts": active_facts,
            "matched_skill_id": matched_skill["id"] if matched_skill else None,
            "scene_mode": scene_mode,
            "working_memory": working_memory,
            "current_cwd": current_cwd,
        }

