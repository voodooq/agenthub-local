"""
统一多渠道调度总线（ChannelGateway）。
核心职责：
1. 落库级幂等去重（dialogue_ingress_dedup：processing/completed 直接 ACK，failed 允许重试）；
2. 服务端身份锁定（按 endpoint_token 解析 dialogue_channel_links，严格 Fail-Closed）；
3. 独立会话映射（dialogue_channel_sessions：按 channel + link_id + chat_id + sender_id 隔离上下文）；
4. 双执行模式（sync_reply 同步返回 / async_push 立即 202 ACK + 后台限流异步回推）；
5. 问答自动沉淀至 dialogue_wiki_records 并递增 message_count。
"""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any

from ..core.logging_config import logger
from ..repository.db import db_manager
from ..schema.access_control import CallerIdentity, parse_scope_input, resolve_caller_identity
from .base import InboundMessage, OutboundMessage
from .formatter import format_for_im_channel
from .registry import channel_registry

# 保护后台异步任务并发上限，防止长耗时 Ontology 查询堆积拖垮进程
_MAX_CONCURRENT_IM_TASKS = 8


class ChannelGateway:
    """AgentHub 统一多渠道 IM 调度总线。"""

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT_IM_TASKS)
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    @staticmethod
    async def _ensure_gateway_tables(conn: Any) -> None:
        """幂等初始化渠道幂等去重表、会话映射表与对话沉淀表。"""
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialogue_ingress_dedup (
                dedup_key TEXT PRIMARY KEY,
                channel_type TEXT NOT NULL,
                external_msg_id TEXT NOT NULL,
                link_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                reply_preview TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialogue_channel_sessions (
                session_key TEXT PRIMARY KEY,
                link_id TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await conn.commit()

    async def _get_active_link(self, endpoint_token: str) -> dict[str, Any] | None:
        """根据服务端签发的 endpoint_token 查找已激活的渠道对接配置。"""
        async with db_manager.get_connection() as conn:
            try:
                cur = await conn.execute(
                    """
                    SELECT * FROM dialogue_channel_links
                    WHERE endpoint_token = ? AND status = 'active'
                    LIMIT 1
                    """,
                    (endpoint_token.strip(),),
                )
                row = await cur.fetchone()
                if not row:
                    return None
                item = dict(row)
                import json as _json
                try:
                    item["channel_config"] = _json.loads(item.get("channel_config_json") or "{}")
                except Exception:
                    item["channel_config"] = {}
                try:
                    e_ids = _json.loads(item.get("expert_ids_json") or "[]")
                except Exception:
                    e_ids = []
                item["expert_ids"] = (
                    [str(x) for x in e_ids if str(x).strip()]
                    if isinstance(e_ids, list) and e_ids
                    else ([str(item["expert_id"])] if item.get("expert_id") else [])
                )
                return item
            except Exception:
                return None

    async def _claim_dedup_slot(
        self, *, channel_type: str, external_msg_id: str, link_id: str
    ) -> tuple[bool, str]:
        """原子抢占消息幂等槽位。
        Returns:
            (acquired, existing_status)
        """
        dedup_key = f"{channel_type}:{external_msg_id}"
        now_ts = time.time()
        async with db_manager.get_connection() as conn:
            await self._ensure_gateway_tables(conn)
            cur = await conn.execute(
                "SELECT status FROM dialogue_ingress_dedup WHERE dedup_key = ? LIMIT 1",
                (dedup_key,),
            )
            row = await cur.fetchone()
            if row:
                st = str(row["status"] or "")
                if st in ("processing", "completed"):
                    return False, st
                # failed 状态允许重试
                await conn.execute(
                    "UPDATE dialogue_ingress_dedup SET status = 'processing', updated_at = ? WHERE dedup_key = ?",
                    (now_ts, dedup_key),
                )
                await conn.commit()
                return True, "retrying"

            await conn.execute(
                """
                INSERT INTO dialogue_ingress_dedup (
                    dedup_key, channel_type, external_msg_id, link_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'processing', ?, ?)
                """,
                (dedup_key, channel_type, external_msg_id, link_id, now_ts, now_ts),
            )
            await conn.commit()
            return True, "claimed"

    async def _finish_dedup_slot(
        self, *, channel_type: str, external_msg_id: str, status: str, reply_preview: str = ""
    ) -> None:
        dedup_key = f"{channel_type}:{external_msg_id}"
        async with db_manager.get_connection() as conn:
            await self._ensure_gateway_tables(conn)
            await conn.execute(
                """
                UPDATE dialogue_ingress_dedup
                SET status = ?, reply_preview = ?, updated_at = ?
                WHERE dedup_key = ?
                """,
                (status, reply_preview[:200], time.time(), dedup_key),
            )
            await conn.commit()

    async def ensure_im_link_workspace(
        self,
        *,
        link_id: str,
        target_user_label: str,
        expert_id: str = "",
    ) -> str:
        """为每个 IM 渠道机器人维护独立的工作沙盒（以设置的名字命名），并将历史与新对话统一归集到该沙盒。"""
        from ..repository.workspace_repository import WorkspaceRepository

        ws_id = f"ws_im_{link_id}"
        clean_label = (target_user_label or "").strip() or link_id
        desired_name = f"[IM] {clean_label}"

        existing_ws = await WorkspaceRepository.get_by_id(ws_id)
        if not existing_ws:
            await WorkspaceRepository.create(
                {
                    "id": ws_id,
                    "name": desired_name,
                    "type": "virtual",
                    "agent_id": expert_id or None,
                }
            )
        elif existing_ws.get("name") != desired_name:
            await WorkspaceRepository.update(ws_id, {"name": desired_name})

        # 将该机器人名下已有的 IM 会话（含此前落在 default 工作区的历史会话）统一归位至该专属沙盒
        async with db_manager.get_connection() as conn:
            await self._ensure_gateway_tables(conn)
            await conn.execute(
                """
                UPDATE conversations
                SET workspace_id = ?
                WHERE id IN (
                    SELECT conversation_id FROM dialogue_channel_sessions WHERE link_id = ?
                ) AND (workspace_id IS NULL OR workspace_id = 'default' OR workspace_id != ?)
                """,
                (ws_id, link_id, ws_id),
            )
            await conn.commit()
        return ws_id

    async def _get_or_create_session_conv_id(
        self, *, link_id: str, channel_type: str, sender_id: str, chat_id: str
    ) -> str:
        """为每个 (link_id, channel, chat_id, sender_id) 维护隔离且稳定的 conversation_id。"""
        session_key = f"{channel_type}:{link_id}:{chat_id}:{sender_id}"
        now_ts = time.time()
        async with db_manager.get_connection() as conn:
            await self._ensure_gateway_tables(conn)
            cur = await conn.execute(
                "SELECT conversation_id FROM dialogue_channel_sessions WHERE session_key = ? LIMIT 1",
                (session_key,),
            )
            row = await cur.fetchone()
            if row and row["conversation_id"]:
                conv_id = str(row["conversation_id"])
                await conn.execute(
                    "UPDATE dialogue_channel_sessions SET updated_at = ? WHERE session_key = ?",
                    (now_ts, session_key),
                )
                await conn.commit()
                return conv_id

            conv_id = f"im_{secrets.token_hex(8)}"
            await conn.execute(
                """
                INSERT INTO dialogue_channel_sessions (
                    session_key, link_id, channel_type, sender_id, chat_id, conversation_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_key, link_id, channel_type, sender_id, chat_id, conv_id, now_ts),
            )
            await conn.commit()
            return conv_id

    async def _save_dialogue_record(
        self,
        *,
        link_id: str,
        expert_id: str,
        channel_type: str,
        sender_id: str,
        sender_name: str,
        user_message: str,
        assistant_reply: str,
    ) -> str:
        """将完整问答写入 dialogue_wiki_records 并递增 dialogue_channel_links.message_count。"""
        record_id = f"drec_{secrets.token_hex(8)}"
        now_ts = time.time()
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO dialogue_wiki_records (
                    record_id, link_id, expert_id, channel_type,
                    sender_id, sender_name, user_message, assistant_reply,
                    extracted_memory, vector_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 'pending', ?)
                """,
                (
                    record_id,
                    link_id,
                    expert_id,
                    channel_type,
                    sender_id,
                    sender_name,
                    user_message,
                    assistant_reply,
                    now_ts,
                ),
            )
            await conn.execute(
                "UPDATE dialogue_channel_links SET message_count = message_count + 1 WHERE link_id = ?",
                (link_id,),
            )
            await conn.commit()
        return record_id

    async def _execute_turn(
        self,
        *,
        inbound: InboundMessage,
        link: dict[str, Any],
        caller: CallerIdentity,
        adapter: Any,
    ) -> dict[str, Any]:
        """在限流信号量保护下驱动 AgentRuntime 完成单轮推理、出站排版、回推与落库。"""
        from ..agent.runtime import agent_runtime

        async with self._sem:
            typing_stop = asyncio.Event()
            # 零延迟先点亮一次“对方正在输入中...”，再每 3 秒续期
            try:
                await adapter.send_typing(inbound=inbound, is_typing=True, link_config=link)
            except Exception:
                pass

            async def _typing_heartbeat() -> None:
                while not typing_stop.is_set():
                    try:
                        await asyncio.wait_for(typing_stop.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        try:
                            await adapter.send_typing(inbound=inbound, is_typing=True, link_config=link)
                        except Exception:
                            pass

            typing_task = asyncio.create_task(_typing_heartbeat())
            try:
                conv_id = await self._get_or_create_session_conv_id(
                    link_id=link["link_id"],
                    channel_type=inbound.channel_type,
                    sender_id=inbound.sender_id,
                    chat_id=inbound.chat_id,
                )
                im_ws_id = await self.ensure_im_link_workspace(
                    link_id=link["link_id"],
                    target_user_label=str(link.get("target_user_label") or ""),
                    expert_id=str(link.get("expert_id") or ""),
                )
                chunks_acc: list[str] = []
                stream_buf = ""
                streamed_segments: list[str] = []
                has_tool_in_stream = False
                final_content = ""
                im_concise_prompt = (
                    "### 💬 当前为手机微信 / IM 实时聊天场景（严格遵守精简口语与渠道边界规范）\n"
                    "1. **简短直给结论**：像熟悉业务的同事在微信里回复一样，自然口语化、直接说重点，常规问题控制在 2~5 句话（150~250 字以内）；\n"
                    "2. **禁止公文与代码噪音**：不要写背景铺垫、长篇报告、免责声明、多级标题（###）、复杂 Markdown 表格或代码块；\n"
                    "3. **严格遵守 IM 渠道能力边界**：\n"
                    "   - **严禁输出任何斜杠命令**（如 `/excel`、`/gen-ui`、`/brand`、`/win-ops`、`/spec`、`/task` 等）；\n"
                    "   - **严禁提及桌面端或底层技术概念**（如「切换任务模式 / Task Mode」「Web 界面」「`output/` 目录」「`write_local_file` 权限」「MCP」「Docker」等）；\n"
                    "   - 被问“你能干嘛”时，仅介绍当前专家在微信里的真实能力（业务规程与百科咨询、授权范围内的数据核实、文字方案与思路梳理）；\n"
                    "   - 遇到微信端无法直接完成的需求（如直接生成图片/画画、导出本机文件、操作电脑软件），只需一句话礼貌说明“目前微信端主要支持文字问答与业务咨询，暂不支持直接出图或操作电脑文件”，不要诱导用户发指令或切模式；\n"
                    "4. **禁止播报检索过程与泄露后台信息**：\n"
                    "   - 调用知识库工具查资料时请静默查询，**严禁输出“我将读取……”“我先帮您查……”“正在读取第 X 切片……”等中间过程旁白**；\n"
                    "   - 引用来源时只需自然说出规程或文档名称（如“根据《昆明D赛竞赛规程》”），**严禁带 `.pdf`/`.docx`/`.xlsx` 等文件扩展名**，也**严禁出现 `doc_id`、`tool_ref`、切片编号（第 X 至 Y 切片）等任何后台技术信息**；\n"
                    "5. **关键事实必须准**：电话、时间、费用、人数等核心数字务必准确；明细很多时先报汇总，再补一句「需要看详细清单可以告诉我」。"
                )

                async def _flush_stream_segment(seg_raw: str) -> None:
                    fmt_seg, seg_chunks = format_for_im_channel(
                        seg_raw, max_chunk_chars=getattr(adapter, "max_chunk_chars", 1500)
                    )
                    if not fmt_seg:
                        return
                    streamed_segments.append(fmt_seg)
                    seg_out = OutboundMessage(
                        raw_markdown=seg_raw,
                        formatted_text=fmt_seg,
                        chunks=seg_chunks or [fmt_seg],
                        metadata={"stream_segment": len(streamed_segments)},
                    )
                    await adapter.send_reply(inbound=inbound, outbound=seg_out, link_config=link)
                    # 微信客户端每收到一条消息会自动熄灭输入状态，流式中间段发完后立即重新点亮
                    if not typing_stop.is_set():
                        try:
                            await adapter.send_typing(inbound=inbound, is_typing=True, link_config=link)
                        except Exception:
                            pass

                # 多专家路由：严格只在当前链接授权勾选的 expert_ids 集合内按问题内容挑选最匹配的专家
                from ..repository.agent_repository import AgentProfileRepository
                from ..kb.manager import KBLifecycleManager
                from ..service.active_model_state import get_global_active_model
                import re as _re

                authorized_experts: list[str] = link.get("expert_ids") or [link["expert_id"]]
                selected_expert_id = authorized_experts[0]
                expert_row: dict[str, Any] | None = None
                authorized_profiles: list[dict[str, Any]] = []

                q_norm = _re.sub(r"[\s，。！？、,.!?]+", "", inbound.text.lower())
                q_bi = {q_norm[i : i + 2] for i in range(max(0, len(q_norm) - 1))}
                best_score = -1

                for cand_id in authorized_experts:
                    cand_row = await AgentProfileRepository.get_agent(cand_id)
                    if not cand_row:
                        continue
                    kb_names_list: list[str] = []
                    try:
                        kbs = await KBLifecycleManager.get_expert_kbs(cand_id)
                        kb_names_list = [str(k.get("name") or "").strip() for k in kbs if k.get("is_published") and k.get("name")]
                    except Exception:
                        pass
                    kb_names_str = " ".join(kb_names_list)

                    authorized_profiles.append(
                        {
                            "id": cand_id,
                            "name": str(cand_row.get("name") or cand_id).strip(),
                            "description": str(cand_row.get("description") or "").strip(),
                            "kb_names": "、".join(kb_names_list),
                            "row": cand_row,
                        }
                    )

                    if expert_row is None:
                        selected_expert_id = cand_id
                        expert_row = cand_row

                    if len(authorized_experts) > 1:
                        profile_txt = _re.sub(
                            r"[\s，。！？、,.!?]+",
                            "",
                            f"{cand_row.get('name', '')}{cand_row.get('description', '')}{kb_names_str}".lower(),
                        )
                        p_bi = {profile_txt[i : i + 2] for i in range(max(0, len(profile_txt) - 1))}
                        score = len(q_bi & p_bi)
                        if score > best_score:
                            best_score = score
                            selected_expert_id = cand_id
                            expert_row = cand_row

                # 动态自适应模型：根据用户当前实际启用的模型池自动匹配，绝不硬编码固定模型
                global_active_model, global_active_tl = get_global_active_model()
                preferred_candidates = [
                    str(link.get("model_preference") or link.get("model_name") or "").strip(),
                    str((expert_row or {}).get("base_model") or "").strip(),
                    str(global_active_model or "").strip(),
                ]
                im_model_name = next((c for c in preferred_candidates if c), "")
                try:
                    from ..api.v1.models import _build_active_models_response

                    active_resp = await _build_active_models_response()
                    user_models = list(active_resp.models or [])
                    if user_models:
                        matched_user_model = None
                        for cand_m in preferred_candidates:
                            if not cand_m:
                                continue
                            matched_user_model = next(
                                (um for um in user_models if um.id == cand_m or um.name == cand_m),
                                None,
                            )
                            if matched_user_model:
                                break
                        if matched_user_model:
                            im_model_name = matched_user_model.id
                        else:
                            default_um = next((um for um in user_models if um.is_default), user_models[0])
                            im_model_name = default_um.id
                except Exception as _m_err:
                    logger.debug(f"[IMGateway] 动态匹配用户可用模型回退自动调度: {_m_err}")
                im_thinking_level = (
                    str(link.get("thinking_level") or "").strip().lower()
                    or global_active_tl
                    or "high"
                )
                bot_display_name = str(link.get("target_user_label") or "").strip()
                expert_name = str((expert_row or {}).get("name") or link.get("expert_name") or "数字专家").strip()
                expert_sys_prompt = str((expert_row or {}).get("system_prompt") or "").strip()

                if len(authorized_profiles) > 1:
                    self_name = bot_display_name or "综合数字助手"
                    cap_lines: list[str] = []
                    for idx, prof in enumerate(authorized_profiles, 1):
                        p_desc = prof["description"].replace("\n", " ")[:90]
                        line = f"{idx}. **{prof['name']}**"
                        if p_desc:
                            line += f"：{p_desc}"
                        if prof["kb_names"]:
                            line += f"（知识库：{prof['kb_names']}）"
                        cap_lines.append(line)
                    cap_matrix_text = "\n".join(cap_lines)

                    identity_header = (
                        f"### 🪪 你的专属身份与综合能力设定（最高优先级）\n"
                        f"- **你的名字**：你叫 **「{self_name}」**。\n"
                        f"- **你的综合定位**：你是一个融合了 **{len(authorized_profiles)} 项专家技能** 的全能综合数字助手，**绝不仅仅局限于其中某一个单一头衔**。\n"
                        f"- **自我介绍与能力提炼铁律**：\n"
                        f"  1. 当用户问“你叫什么名字”“你是谁”“自我介绍一下”“你能干嘛”“除了XX还能干嘛”时，**必须直接以「{self_name}」自称**，并且**必须自动提炼、归纳概括下方全部 {len(authorized_profiles)} 项能力矩阵**（例如归类为：办公报表与演示、技术开发与设计、深度研究与数据审计、赛事规程与财务咨询等），**严禁只报出第一个专家名字**！\n"
                        f"  2. **严禁自称 Antigravity、Gemini、Google DeepMind 或提及任何底层模型/厂商名称**。\n"
                        f"  3. 日常连续对话请直接自然回答问题，**不要每条回复开头都机械重复“我叫{self_name}……”**。\n"
                        f"\n### 🧩 已授权综合能力矩阵（共 {len(authorized_profiles)} 项，回答自我介绍/能力咨询时请全面提炼）\n"
                        f"{cap_matrix_text}\n"
                    )
                    if expert_sys_prompt and best_score > 0:
                        identity_header += f"\n### 🎯 当前匹配主责专家（{expert_name}）业务规范\n{expert_sys_prompt}\n"
                else:
                    self_name = bot_display_name or expert_name
                    identity_header = (
                        f"### 🪪 你的专属身份与名字设定（最高优先级）\n"
                        f"- **你的名字**：你叫 **「{self_name}」**"
                        + (f"（定位：{expert_name}）" if bot_display_name and bot_display_name != expert_name else "")
                        + "。\n"
                        f"- **身份回答铁律**：当用户问“你叫什么名字”“你是谁”时，**必须直接以「{self_name}」自称**（例如：“我叫{self_name}，是您的{expert_name}”），**严禁自称 Antigravity、Gemini、Google DeepMind 或提及任何底层模型/厂商名称**；日常连续追问请直接回答，不要每条消息开头都机械重复自报家门。\n"
                    )
                    if expert_sys_prompt:
                        identity_header += f"\n### 🎯 绑定专家角色职责\n{expert_sys_prompt}\n"

                full_im_system_prompt = f"{identity_header}\n{im_concise_prompt}"

                async for ev in agent_runtime.chat_stream(
                    conversation_id=conv_id,
                    user_message=inbound.text,
                    model_name=im_model_name,
                    thinking_level=im_thinking_level,
                    context_rounds=4,
                    system_override=full_im_system_prompt,
                    scene_mode="agent",
                    workspace_id=im_ws_id,
                    agent_id=selected_expert_id,
                    caller_channel="im_bot",
                    endpoint_token=inbound.endpoint_token,
                ):
                    ev_type = ev.get("type")
                    if ev_type in ("tool_start", "tool_end", "start"):
                        # 进入工具执行或新 ReAct 步骤时清空上一中间步骤的过渡文本缓冲，绝不把工具前过渡语发到 IM
                        chunks_acc.clear()
                        stream_buf = ""
                    elif ev_type == "stream_reset":
                        chunks_acc.clear()
                        stream_buf = ""
                    elif ev_type == "chunk" and isinstance(ev.get("content"), str):
                        piece = ev["content"]
                        chunks_acc.append(piece)
                        stream_buf += piece
                        # 严防泄露 <tool_call>、后台技术标识与工具调用前过渡独白（如“我将读取/查询/检索...”）：
                        # 只有确认无任何 `<` 标签、无代码块、无后台 ID/切片词且非工具前过渡独白时，才按段落实时流式推送
                        low_buf = stream_buf.lower()
                        unsafe_tag = (
                            "<" in stream_buf
                            or "```" in stream_buf
                            or any(
                                p in stream_buf
                                for p in (
                                    "我将读取",
                                    "我将查询",
                                    "我将检索",
                                    "我将为您查询",
                                    "我将为您检索",
                                    "我将为您在",
                                    "我先帮您",
                                    "我先查询",
                                    "我先查一下",
                                    "我先读取",
                                    "正在读取",
                                    "正在为您查询",
                                    "正在为您检索",
                                    "正在查询",
                                    "正在检索",
                                    "切片",
                                )
                            )
                            or any(
                                k in low_buf
                                for k in (
                                    "tool_call",
                                    "doc_id",
                                    "tool_ref",
                                    ".pdf",
                                    ".docx",
                                    ".xlsx",
                                )
                            )
                        )
                        if not unsafe_tag:
                            if "\n\n" in stream_buf and len(stream_buf.strip()) >= 25:
                                parts = stream_buf.split("\n\n")
                                ready = "\n\n".join(parts[:-1]).strip()
                                stream_buf = parts[-1]
                                if ready:
                                    await _flush_stream_segment(ready)
                            elif len(stream_buf) >= 60 and stream_buf[-1] in ("。", "！", "？", "\n"):
                                ready = stream_buf.strip()
                                stream_buf = ""
                                if ready:
                                    await _flush_stream_segment(ready)
                    elif ev_type == "done":
                        final_content = str(
                            ev.get("final_step_content") or ev.get("full_content") or "".join(chunks_acc)
                        )

                raw_reply = (final_content or "".join(chunks_acc)).strip() or "（专家未返回有效内容）"
                formatted_text, out_chunks = format_for_im_channel(
                    raw_reply, max_chunk_chars=getattr(adapter, "max_chunk_chars", 1500)
                )

                # 若前面已流式推送过部分段落，仅补发尚未推送的尾段；否则整段推送
                if streamed_segments:
                    if stream_buf.strip():
                        await _flush_stream_segment(stream_buf.strip())
                    push_res = {"pushed": True, "streamed_segments": len(streamed_segments)}
                else:
                    outbound = OutboundMessage(
                        raw_markdown=raw_reply,
                        formatted_text=formatted_text,
                        chunks=out_chunks,
                        metadata={
                            "caller_role": caller.role,
                            "caller_scope": caller.scope,
                            "conversation_id": conv_id,
                        },
                    )
                    push_res = await adapter.send_reply(
                        inbound=inbound, outbound=outbound, link_config=link
                    )

                record_id = await self._save_dialogue_record(
                    link_id=link["link_id"],
                    expert_id=selected_expert_id,
                    channel_type=inbound.channel_type,
                    sender_id=inbound.sender_id,
                    sender_name=inbound.sender_name,
                    user_message=inbound.text,
                    assistant_reply=formatted_text,
                )
                await self._finish_dedup_slot(
                    channel_type=inbound.channel_type,
                    external_msg_id=inbound.external_msg_id,
                    status="completed",
                    reply_preview=formatted_text,
                )
                return {
                    "status": "completed",
                    "record_id": record_id,
                    "conversation_id": conv_id,
                    "caller_role": caller.role,
                    "caller_scope": caller.scope,
                    "reply_text": formatted_text,
                    "reply_chunks": out_chunks,
                    "push_result": push_res,
                }
            except Exception as err:
                logger.error(f"[ChannelGateway] 渠道消息处理异常 ({inbound.external_msg_id}): {err}")
                await self._finish_dedup_slot(
                    channel_type=inbound.channel_type,
                    external_msg_id=inbound.external_msg_id,
                    status="failed",
                    reply_preview=str(err),
                )
                raise
            finally:
                typing_stop.set()
                typing_task.cancel()
                try:
                    await adapter.send_typing(inbound=inbound, is_typing=False, link_config=link)
                except Exception:
                    pass

    async def handle_inbound(
        self,
        *,
        channel_type: str,
        endpoint_token: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """统一入站处理入口：支持 sync_reply（同步等待结果）与 async_push（立即 ACK 后台回推）。"""
        # 确保默认适配器已注册
        from . import adapters as _adapters  # noqa: F401

        link = await self._get_active_link(endpoint_token)
        if not link:
            raise ValueError("无效或已停用的渠道专用对接令牌 (endpoint_token)")

        adapter = channel_registry.get(channel_type)
        if not adapter:
            raise ValueError(f"未找到渠道适配器: {channel_type}")

        inbound = await adapter.parse_inbound(
            endpoint_token=endpoint_token,
            payload=payload,
            headers=headers,
            link_config=link,
        )

        # 幂等抢占：重复送达直接返回 ACK，不重复查数也不重复写记录
        acquired, dedup_state = await self._claim_dedup_slot(
            channel_type=inbound.channel_type,
            external_msg_id=inbound.external_msg_id,
            link_id=link["link_id"],
        )
        if not acquired:
            return {
                "status": "duplicate_ignored",
                "dedup_state": dedup_state,
                "external_msg_id": inbound.external_msg_id,
                "message": f"消息 {inbound.external_msg_id} 已处于 {dedup_state} 状态，已幂等去重跳过。",
            }

        caller = await resolve_caller_identity(
            channel="im_bot",
            endpoint_token=endpoint_token,
        )
        eff_mode = (mode or payload.get("mode") or adapter.default_mode or "sync_reply").strip()

        if eff_mode == "async_push":
            task = asyncio.create_task(
                self._execute_turn(inbound=inbound, link=link, caller=caller, adapter=adapter)
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            return {
                "status": "accepted",
                "mode": "async_push",
                "external_msg_id": inbound.external_msg_id,
                "caller_role": caller.role,
                "caller_scope": caller.scope,
                "message": "已接收消息并进入后台异步处理队列。",
            }

        return await self._execute_turn(
            inbound=inbound, link=link, caller=caller, adapter=adapter
        )


channel_gateway = ChannelGateway()

