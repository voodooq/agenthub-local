"""
智能体双层循环编排运行时 (AgentRuntime)
核心架构：
- 外层循环 (Inter-Turn): 会话级多轮对话演进，维护 turn_id 递增、场景模式切换与回合后异步记忆提炼
- 内层循环 (Intra-Turn ReAct): 单次提问内的多步“推理 -> 工具拦截 -> 安全审批/执行 -> Observation入库 -> 再次推理”闭环
"""
import asyncio
import json
import re
import uuid
from typing import Any, AsyncGenerator

from ..core.logging_config import logger
from ..repository.conversation_repository import ConversationRepository
from ..repository.message_repository import MessageRepository
from ..service.engine_pool import engine_pool
from ..service.audit_service import audit_service
from ..memory.long_term import long_term_memory
from ..extensions.skill_loader import skill_loader
from ..extensions.mcp_manager import mcp_manager
from .shell_executor import shell_executor
from .tool_protocol import ToolProtocolAdapter
from .context_builder import ContextBuilder


class AgentRuntime:
    """微内核智能体双层循环编排器"""

    MAX_REACT_STEPS = 6

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
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        执行双层循环流式推理与工具编排，向外输出标准化 SSE 字典事件流。
        """
        conv_id = conversation_id
        is_new_conv = False
        short_title = (user_message or "").strip().split("\n")[0][:24] or "新会话"

        # 1. 确保会话存在并同步最新场景/无痕状态
        if not conv_id:
            conv_id = str(uuid.uuid4())
            is_new_conv = True
            await ConversationRepository.create({
                "id": conv_id,
                "title": short_title,
                "model_name": model_name,
                "scene_mode": scene_mode or "agent",
                "incognito": 1 if incognito else 0,
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

        # 2. 计算当前大轮次编号 (turn_id) 并率先落盘用户提问
        max_turn = await MessageRepository.get_max_turn_id(conv_id)
        current_turn_id = max_turn + 1

        await MessageRepository.save_message(
            conversation_id=conv_id,
            role="user",
            content=user_message,
            turn_id=current_turn_id,
        )
        await ConversationRepository.touch(conv_id)

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

        # 4. 准备当前场景可用的工具集合
        active_tools: list[dict[str, Any]] = []
        if active_scene == "agent":
            active_tools.extend(ToolProtocolAdapter.get_builtin_tools())
            try:
                mcp_tools = await mcp_manager.get_enabled_tools()
                active_tools.extend(mcp_tools)
            except Exception as e:
                logger.warning(f"加载 MCP 工具集异常: {e}")

        max_steps = self.MAX_REACT_STEPS if active_scene == "agent" else 1
        final_account: str | None = None
        accumulated_visible_text = ""

        # 5. 进入单轮内 ReAct 思考与工具执行循环 (Intra-Turn Loop)
        for step_idx in range(max_steps):
            ctx = await ContextBuilder.build(
                conversation_id=conv_id,
                current_turn_id=current_turn_id,
                user_query=user_message,
                tools=active_tools if active_scene == "agent" else None,
                context_rounds_override=0 if active_scene == "oneshot" else context_rounds,
                base_system_prompt=system_override,
            )

            # 首步向前端推送命中的长期记忆溯源与当前任务草稿纸
            if step_idx == 0:
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
                step_prompt = (
                    "[系统工作流推进]: 上述工具已执行完毕并返回真实结果（见上方记录）。"
                    "请基于真实结果继续分析：若任务尚未完成需继续操作，请继续输出 `<tool_call>`；"
                    "若任务已完成，请直接输出面向用户的最终完整解答。"
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
                if etype == "chunk":
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

            # 情况 A：模型未发起工具调用（或非 agent 模式），直接收尾完成本轮对话
            if not tool_calls or active_scene != "agent":
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
                accumulated_visible_text += ("\n\n" + final_saved_text if accumulated_visible_text else final_saved_text)
                yield {
                    "type": "done",
                    "full_content": accumulated_visible_text.strip(),
                    "account": final_account,
                }
                break

            # 情况 B：模型发起了工具调用 -> 先将模型的 tool_calls 消息落盘（便于断电自愈）
            accumulated_visible_text += ("\n\n" + clean_text if (accumulated_visible_text and clean_text) else clean_text)
            await MessageRepository.save_message(
                conversation_id=conv_id,
                role="model",
                content=clean_text or f"[调用工具: {', '.join(tc['function']['name'] for tc in tool_calls)}]",
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

                # ── 工具 1: 受控本地终端命令 (execute_local_command) ──
                if func_name == "execute_local_command":
                    cmd_str = str(args.get("command", "")).strip()
                    reason_str = str(args.get("reason", "执行本地终端诊断或操作")).strip()
                    sec_level = shell_executor.check_security(cmd_str)

                    if sec_level == "DENY":
                        await audit_service.log(
                            event_type="shell_command_denied",
                            actor="agent",
                            detail={"command": cmd_str, "reason": reason_str},
                            approved=False,
                        )
                        tool_output = (
                            f"Denied: 命令 `{cmd_str}` 触发系统高危安全黑名单已被强制拦截。"
                            f"严禁执行破坏性指令，请改用安全的替代方式回答用户。"
                        )
                        compressed = f"[安全红线拦截高危命令 `{cmd_str[:50]}`]"
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

                    elif sec_level == "CONFIRM":
                        # 推送灰名单审批请求事件，挂起协程等待前端用户点击「允许」或「拒绝」
                        yield {
                            "type": "require_approval",
                            "call_id": call_id,
                            "name": func_name,
                            "command": cmd_str,
                            "reason": reason_str,
                            "cwd": ctx.get("current_cwd"),
                        }
                        approved = await shell_executor.wait_for_user_approval(call_id, timeout=180.0)
                        await audit_service.log(
                            event_type="shell_command_approval",
                            actor="user",
                            detail={"command": cmd_str, "reason": reason_str, "call_id": call_id},
                            approved=approved,
                        )

                        if not approved:
                            tool_output = (
                                f"Rejected: 用户在前端拒绝（或超时未批准）执行命令 `{cmd_str}`。"
                                f"请不要重复尝试该命令，直接向用户说明情况或提供手动执行建议。"
                            )
                            compressed = f"[用户已拒绝执行命令 `{cmd_str[:50]}`]"
                            yield {
                                "type": "tool_end",
                                "call_id": call_id,
                                "name": func_name,
                                "command": cmd_str,
                                "reason": reason_str,
                                "status": "rejected",
                                "output": tool_output,
                                "compressed": compressed,
                            }
                        else:
                            yield {
                                "type": "tool_start",
                                "call_id": call_id,
                                "name": func_name,
                                "command": cmd_str,
                                "reason": reason_str,
                                "security": "APPROVED",
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

                    else:
                        # SAFE 白名单只读命令：自动直通执行
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": func_name,
                            "command": cmd_str,
                            "reason": reason_str,
                            "security": "SAFE",
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

                # ── 工具 3: 按需加载 Skill 详情 (load_skill_detail) ──
                elif func_name == "load_skill_detail":
                    skill_name = str(args.get("skill_name", "")).strip()
                    tool_output = skill_loader.get_full_skill_markdown(skill_name)
                    compressed = f"[已加载专业技能 `{skill_name}` 操作规程]"
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
                    compressed = f"[已执行 MCP 工具 `{func_name}`: 输出 {len(tool_output)} 字符]"
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

                # 将工具真实输出与单行折叠摘要同时落盘（当前 Turn 用完整 output，后续 Turn 自动用 compressed）
                await MessageRepository.save_message(
                    conversation_id=conv_id,
                    role="tool",
                    content=tool_output,
                    compressed_content=compressed,
                    turn_id=current_turn_id,
                    tool_call_id=call_id,
                )

            # 若已到达最大步数上限，给出收尾完成信号
            if step_idx == max_steps - 1:
                yield {
                    "type": "done",
                    "full_content": accumulated_visible_text.strip() or "已达到单轮最大自动执行步数上限（6步），请确认后继续。",
                    "account": final_account,
                }

        # 7. 回合后异步记忆提炼与自然衰减维护（非阻塞后台任务）
        if active_scene != "oneshot" and not active_incognito:
            asyncio.create_task(self._post_turn_memory_maintenance(user_message))

    @staticmethod
    async def _post_turn_memory_maintenance(user_message: str) -> None:
        """
        回合后后台轻量级长期记忆捕获与 30 天指数衰减维护。
        当用户在对话中明确表达个人技术栈偏好、工作规范或环境特征时自动入库。
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
                    fact = m.group(0).strip()[:160]
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

