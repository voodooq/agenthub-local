"""
多轮上下文动态组装器 (ContextBuilder) 与会话自愈器 (SessionHealer)
核心职责：
1. SessionHealer: 自动检测因断电/关闭窗口/用户打断而残留的未闭合 tool_calls，原地补插 Cancelled 兜底消息，杜绝 API 400 报错
2. Observation Masking: 历史轮次 (turn_id < current_turn_id) 的超长工具输出自动替换为单行 compressed_content 摘要
3. 按场景 (oneshot / chat / agent) 差异化装配：长期记忆 Top 5 + 工作记忆草稿纸 + 短期 Turn 滑动窗口 + 工具协议
"""
import json
import re
from typing import Any

from ..core.logging_config import logger
from ..repository.conversation_repository import ConversationRepository
from ..repository.message_repository import MessageRepository
from ..memory.long_term import long_term_memory
from ..extensions.skill_loader import skill_loader
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
        working_memory_raw = (conv.working_memory_json if conv else "{}") or "{}"
        rolling_summary = (conv.rolling_summary if conv else "") or ""
        current_cwd = (conv.current_cwd if conv else None)

        try:
            working_memory = json.loads(working_memory_raw)
        except Exception:
            working_memory = {}

        # ── 模式 A: ⚡ 极速单次模式 (oneshot) ──
        if scene_mode == "oneshot":
            sys_prompt = base_system_prompt or "你是 GeminiHub Local 极速研发助手，请直接、精炼地输出结果。"
            return {
                "system_instruction": sys_prompt,
                "history_messages": [],
                "referenced_memories": [],
                "scene_mode": "oneshot",
                "working_memory": {},
                "current_cwd": current_cwd,
            }

        # ── 模式 B/C: 💬 标准对话模式 (chat) 与 🛠️ Agent 任务模式 (agent) ──
        system_parts: list[str] = []
        if base_system_prompt:
            system_parts.append(base_system_prompt)
        else:
            system_parts.append("你是 GeminiHub Local 智能研发中枢，运行在用户本地私有环境中。")

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

        # 2. 检索并挂载长期记忆 Top 5（无痕模式下跳过）
        referenced_memories: list[dict[str, Any]] = []
        if not incognito:
            referenced_memories = await long_term_memory.retrieve_relevant(user_query, limit=5, min_confidence=0.5)
            if referenced_memories:
                mem_lines = [
                    f"- [{m['category']}] (置信度:{float(m['confidence']):.2f}) {m['content']}"
                    for m in referenced_memories
                ]
                system_parts.append("### 💡 用户长期偏好与本机环境记忆 (Long-Term Memory)：\n" + "\n".join(mem_lines))

        # 3. 挂载早期对话溢出浓缩摘要 (Rolling Summary)
        if rolling_summary.strip():
            system_parts.append(f"### 📜 更早期的历史对话浓缩背景 (Rolling Summary)：\n{rolling_summary.strip()}")

        # 4. 挂载按需专业技能摘要 (Skills Summary)
        skills_summary = skill_loader.get_skills_summary_prompt()
        if skills_summary:
            system_parts.append(skills_summary)

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

            for m in raw_msgs:
                # 关键降噪策略 (Observation Masking)：
                # 历史轮次 (turn_id < current_turn_id) 的工具执行输出一律使用单行折叠摘要，当前轮次保留完整输出
                if m.role == "tool":
                    if (m.turn_id or 1) < current_turn_id and m.compressed_content:
                        effective_text = m.compressed_content
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
            "scene_mode": scene_mode,
            "working_memory": working_memory,
            "current_cwd": current_cwd,
        }

