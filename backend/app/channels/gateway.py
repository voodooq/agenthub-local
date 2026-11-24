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
import random
import secrets
import time
from datetime import datetime
from typing import Any, TypedDict

from ..core.logging_config import logger
from ..repository.db import db_manager
from ..schema.access_control import CallerIdentity, parse_scope_input, resolve_caller_identity
from .base import InboundMessage, OutboundMessage
from .formatter import format_for_im_channel
from .registry import channel_registry

# 保护后台异步任务并发上限，防止长耗时 Ontology 查询堆积拖垮进程
_MAX_CONCURRENT_IM_TASKS = 8
# 连发消息动态防抖窗口（纯文本 1.5s，含图片/附件 4.5s）
_DEBOUNCE_TEXT_SECONDS = 1.5
_DEBOUNCE_MEDIA_SECONDS = 4.5
# 单一微信窗口静默超 2 小时自动开启全新短期上下文，防止多话题串台
_SESSION_SOFT_SPLIT_SECONDS = 7200.0
# 仅当单轮推理超过 18 秒（如真正在执行编译/深度分析/多步工具）才推送“转后台处理”并释放主锁
_LONG_TASK_DETACH_SECONDS = 18.0
_LONG_TASK_DETACH_PHRASES = (
    "这事儿稍微有点工程量，我先放后台接着跑，弄好了第一时间叫你~（想叫停随时回个「停」就行）",
    "收到，这个需要点时间处理，我先在后台慢慢干，你可以先忙别的，出结果直接发你（回「停」可中止）。",
    "正在后台全力处理中，估计还得一会儿，有结果立马推给你，期间有别的事随时吩咐（发「停」可取消）。",
    "先帮你挂在后台跑着啦，省得你一直干等，搞定后我马上把结果发过来（随时回个「停」就能中断）。",
    "这步稍微耗点时间，我转到后台继续弄，你先忙你的，弄完我喊你（要取消回个「停」即可）。",
    "好嘞，已经在后台加紧处理了，稍微等我一会儿，弄完立刻发给你（发「停」可随时打断）。",
)


class _DebounceBuffer(TypedDict):
    texts: list[str]
    attachments: list[Any]
    quote_texts: list[str]
    msg_ids: list[str]
    last_inbound: InboundMessage
    link: dict[str, Any]
    caller: CallerIdentity
    adapter: Any
    timer: asyncio.Task[Any] | None


class ChannelGateway:
    """AgentHub 统一多渠道 IM 调度总线。"""

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT_IM_TASKS)
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._debounce_buffers: dict[str, _DebounceBuffer] = {}
        self._active_session_conv: dict[str, str] = {}
        self._session_workdirs: dict[str, str] = {}
        self._last_detach_phrase_idx: int = -1

    def _pick_detach_notice(self) -> str:
        """随机挑选一条口语化的后台处理过渡话术，确保绝不与上一条连续重复。"""
        candidates = [i for i in range(len(_LONG_TASK_DETACH_PHRASES)) if i != self._last_detach_phrase_idx]
        idx = random.choice(candidates) if candidates else 0
        self._last_detach_phrase_idx = idx
        return _LONG_TASK_DETACH_PHRASES[idx]

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
    ) -> tuple[str, bool]:
        """为每个 (link_id, channel, chat_id, sender_id) 维护隔离会话，静默超 2 小时自动软切分新上下文。"""
        session_key = f"{channel_type}:{link_id}:{chat_id}:{sender_id}"
        now_ts = time.time()
        async with db_manager.get_connection() as conn:
            await self._ensure_gateway_tables(conn)
            cur = await conn.execute(
                "SELECT conversation_id, updated_at FROM dialogue_channel_sessions WHERE session_key = ? LIMIT 1",
                (session_key,),
            )
            row = await cur.fetchone()
            if row and row["conversation_id"]:
                prev_updated = float(row["updated_at"] or 0)
                if prev_updated > 0 and (now_ts - prev_updated) > _SESSION_SOFT_SPLIT_SECONDS:
                    new_conv_id = f"im_{secrets.token_hex(8)}"
                    await conn.execute(
                        "UPDATE dialogue_channel_sessions SET conversation_id = ?, updated_at = ? WHERE session_key = ?",
                        (new_conv_id, now_ts, session_key),
                    )
                    await conn.commit()
                    return new_conv_id, True

                conv_id = str(row["conversation_id"])
                await conn.execute(
                    "UPDATE dialogue_channel_sessions SET updated_at = ? WHERE session_key = ?",
                    (now_ts, session_key),
                )
                await conn.commit()
                return conv_id, False

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
            return conv_id, False

    async def _reset_session_conv_id(
        self, *, link_id: str, channel_type: str, sender_id: str, chat_id: str
    ) -> str:
        """响应 /new 指令强制切换新短期对话 ID，保留历史库与长期记忆。"""
        session_key = f"{channel_type}:{link_id}:{chat_id}:{sender_id}"
        now_ts = time.time()
        new_conv_id = f"im_{secrets.token_hex(8)}"
        async with db_manager.get_connection() as conn:
            await self._ensure_gateway_tables(conn)
            await conn.execute(
                """
                INSERT INTO dialogue_channel_sessions (
                    session_key, link_id, channel_type, sender_id, chat_id, conversation_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    updated_at = excluded.updated_at
                """,
                (session_key, link_id, channel_type, sender_id, chat_id, new_conv_id, now_ts),
            )
            await conn.commit()
        self._active_session_conv[session_key] = new_conv_id
        return new_conv_id

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
                conv_id, is_soft_split = await self._get_or_create_session_conv_id(
                    link_id=link["link_id"],
                    channel_type=inbound.channel_type,
                    sender_id=inbound.sender_id,
                    chat_id=inbound.chat_id,
                )
                session_key = f"{inbound.channel_type}:{link['link_id']}:{inbound.chat_id}:{inbound.sender_id}"
                self._active_session_conv[session_key] = conv_id
                im_ws_id = await self.ensure_im_link_workspace(
                    link_id=link["link_id"],
                    target_user_label=str(link.get("target_user_label") or ""),
                    expert_id=str(link.get("expert_id") or ""),
                )

                from ..service.personal_assistant_service import personal_assistant_service

                now_dt = datetime.now()
                weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
                now_str = f"{now_dt.strftime('%Y-%m-%d %H:%M:%S')} {weekday_names[now_dt.weekday()]}"
                session_stats = await personal_assistant_service.get_session_stats(
                    link_id=link["link_id"], sender_id=inbound.sender_id
                )
                split_note = (
                    "- **跨时段新话题切分**：距离上一条对话已超过 2 小时，系统已自动为你开启全新短期上下文；如需查找此前记录，请调用 `search_im_chat_history` 或 `manage_personal_notes`。\n"
                    if is_soft_split
                    else ""
                )
                last_time_line = (
                    f"- **上一条历史消息时间**：`{session_stats['last_msg_time_str']}`（请据此准确区分昨天、昨晚与今天，绝不可把已过一夜的事情继续当成“明天”）\n"
                    if session_stats.get("last_msg_time_str")
                    else ""
                )
                first_time_line = (
                    f"- **首次互动时间 / 累计轮数**：`{session_stats['first_msg_time_str']}` / 共 `{session_stats['total_rounds']}` 轮\n"
                    if session_stats.get("first_msg_time_str")
                    else ""
                )
                from .media_processor import media_processor
                from ..service.personal_workflow_service import personal_workflow_service

                media_caps = media_processor.get_capabilities_status()
                runtime_ctx_header = (
                    "### ⏱️ 当前实时时间与会话状态（最高基准事实）\n"
                    f"- **当前准确时间**：`{now_str}`\n"
                    f"{last_time_line}"
                    f"{first_time_line}"
                    f"{split_note}"
                    f"- **多模态感知状态**：{media_caps['summary']}\n"
                    "- **记忆事实区分规则**：提示词中若出现过往记忆、用户画像或备忘档案，均是系统替你持久化保留的历史记忆，绝非用户刚贴出来的“系统环境信息”；请自然承接，不要反问用户为什么贴出这些信息。\n"
                )

                chunks_acc: list[str] = []
                stream_buf = ""
                streamed_segments: list[str] = []
                has_tool_in_stream = False
                final_content = ""
                im_concise_prompt = (
                    "### 💬 当前为手机微信私人助理与实时对话场景（高情商、靠谱、不推诿）\n"
                    "1. **主动调用真实能力，严禁虚假拒答**：\n"
                    "   - 当用户让你**记备忘 / 添加待办 / 查看备忘**：必须主动调用 `manage_personal_notes`（或 `save_long_term_memory`）持久化落库，并明确回复“已帮你记下”，**严禁说“我记不住 / 下次就忘了 / 建议你自己记在笔记里”**；\n"
                    "   - 当用户让你**查以前的聊天记录 / 核对以前说过的话**：主动调用 `search_im_chat_history` 查真实落库记录并准确作答；\n"
                    "   - 当用户让你**设置定时提醒 / 添加计划任务 / 每天定时执行**：必须主动调用 `create_im_reminder`（支持单次如 `10分钟后`、`明早8点`，以及周期 Cron 如 `每天早上8:30`；若到点需查天气/待办/新闻或执行总结，请设 `run_as_agent=True`），查看或取消调用 `list_or_cancel_im_reminders`，**严禁说“不支持计划任务”**；\n"
                    "   - 当用户询问**天气或最新实时资讯 / 联网搜索**：你已直接拥有 `get_weather`、`web_search` 与 `web_fetch` 工具，必须主动调用获取真实结果后直给结论，**严禁回复“我没有时钟 / 暂时不能联网搜索 / 我查不到实时天气 / 你自己打开手机 App 看”**；\n"
                    "   - 当用户要求**生成 Word(.docx) / PDF(.pdf) / Markdown(.md) 文档或总结报告文件**：**必须直接调用 `export_and_send_document(title=..., content=..., format='docx'|'pdf'|'md')`** 生成真实文件，系统会自动把文档文件直接推送到用户手机微信！\n"
                    "   - 当用户询问**写程序 / 脚本 / 技术排错**：可以直接在微信里给出精简清晰的核心代码片段、排错脚本或技术方案思路，仅在需要整库构建或操作本机桌面软件时简短建议回到电脑端协同；\n"
                    "   - 当用户询问你**能否听语音 / 语音识别 / 看图片**，或直接发送语音条/图片时：网关已为你接入微信语音自动转文字与图片识别能力（OCR + 视觉理解），请如实肯定并欢迎用户直接发语音条或图片给你，**严禁回复“目前还不能做语音识别”或“我只能处理文字对话”**！\n"
                    "   - **备忘 vs 文档区分铁律**：如果用户只是口头让你记住计划/备忘/待办，调用 `manage_personal_notes(action='add', ...)` 或 `save_long_term_memory` 存入数据库；如果用户明确要求生成文档文件，调用 `export_and_send_document`；若已开启主人全能模式且需要写项目代码/脚本，可直接调用 `write_local_file`。\n"
                    "2. **温暖干练口语化（去掉冷漠客服腔）**：\n"
                    "   - 像一位贴身靠谱的资深私人助理兼同事在微信上聊天，自然、有温度、不机械道歉，日常回答控制精炼（常规 2~6 句话，150~300 字内；代码/方案除外）；\n"
                    "   - 不堆砌多级标题、背景铺垫或免责声明；\n"
                    "3. **静默查库、严禁泄露后台技术字眼**：\n"
                    "   - 调用任何工具查询时保持静默，**严禁输出“我将读取……”“我先查一下……”“正在读取第 X 切片……”等中间过程旁白**；\n"
                    "   - 引用规程或文档时只报名称，**严禁带 `.pdf`/`.docx`/`.xlsx` 扩展名、`doc_id`、`tool_ref`、切片号等内部字段**；\n"
                    "4. **关键事实务必准确**：时间、日期、数字与统计口径务必核实清楚后再回复。"
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
                    (global_active_model or "").strip(),
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

                is_owner_mode = int(link.get("owner_mode") or 0) == 1 and (
                    inbound.channel_type == "weixin_clawbot" or caller.role == "hq_admin"
                )
                if is_owner_mode:
                    claw_extra = (
                        "\n   - 【微信 Claw 托管主机全权限已开启】：你可以直接在 ReAct Loop 内无障碍执行 `execute_local_command`、`write_local_file`、`replace_in_file`、`start_background_process`、`stop_background_process`、`run_remote_ssh_command` 与 `export_and_send_document` 完成主机运维、文件操作、脚本运行与文档生成，无需停下请求确认！"
                        if inbound.channel_type == "weixin_clawbot"
                        else "\n   - 若某高危或敏感操作触发 L2 安全门禁挂起并生成 4 位确认码，立即把确认信息原样清晰转达给主人等待确认。"
                    )
                    im_concise_prompt += (
                        "\n5. **主人全能控制台模式（已启用）**：\n"
                        "   - 你可以直接调用 `list_managed_servers`、`inspect_server_health`、`run_remote_ssh_command`、`execute_local_command`、`write_local_file`、`replace_in_file`、`git_workspace_safe_ops`、`save_inbox_item`、`generate_daily_briefing`、`export_and_send_document` 等与电脑端对等的工具；\n"
                        "   - 修改任何代码前先调用 `git_workspace_safe_ops(action='checkpoint')` 保护现场，修改后尽量运行测试再汇报精简 Diff；"
                        + claw_extra
                    )

                # 多模态与引用消息预处理（无 GPU 纯 CPU 语音转写 + RapidOCR 识图 + Vision 载荷透传）
                media_res = await media_processor.process_inbound_media(inbound.raw_payload)
                media_attachments = list(media_res.get("image_attachments") or [])
                prompt_sections: list[str] = []
                quote_txt = str((inbound.raw_payload or {}).get("quote_text") or "").strip()
                if quote_txt:
                    prompt_sections.append(f"[引用历史消息]: {quote_txt[:400]}")
                if media_res.get("transcripts"):
                    prompt_sections.append("[语音转写]: " + "\n".join(media_res["transcripts"]))
                if media_res.get("ocr_texts"):
                    prompt_sections.append("[图片文字提取(OCR)]: " + "\n".join(media_res["ocr_texts"])[:1500])
                if inbound.text and inbound.text not in ("[收到图片消息]", "[收到语音消息]", "[收到媒体附件]"):
                    prompt_sections.append(inbound.text)
                elif not prompt_sections:
                    prompt_sections.append(inbound.text)
                eff_user_msg = "\n\n".join(prompt_sections)

                preferred_wd = getattr(self, "_session_workdirs", {}).get(session_key, "")
                if preferred_wd and is_owner_mode:
                    runtime_ctx_header += f"- **当前指定工作目录 (/cd)**：`{preferred_wd}`\n"

                full_im_system_prompt = f"{runtime_ctx_header}\n{identity_header}\n{im_concise_prompt}"

                exported_attachments: list[dict[str, Any]] = []
                async for ev in agent_runtime.chat_stream(
                    conversation_id=conv_id,
                    user_message=eff_user_msg,
                    model_name=im_model_name,
                    thinking_level=im_thinking_level,
                    context_rounds=12,
                    attachments=media_attachments or None,
                    system_override=full_im_system_prompt,
                    scene_mode="agent",
                    workspace_id=im_ws_id,
                    agent_id=selected_expert_id,
                    caller_channel="im_bot",
                    endpoint_token=inbound.endpoint_token,
                    im_context={
                        "link_id": str(link["link_id"]),
                        "channel_type": inbound.channel_type,
                        "sender_id": inbound.sender_id,
                        "chat_id": inbound.chat_id,
                    },
                    owner_mode=is_owner_mode,
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
                        for ef in (ev.get("export_files") or []):
                            if isinstance(ef, dict) and ef.get("file_path"):
                                exported_attachments.append(ef)

                raw_reply = (final_content or "".join(chunks_acc)).strip() or "（专家未返回有效内容）"
                formatted_text, out_chunks = format_for_im_channel(
                    raw_reply, max_chunk_chars=getattr(adapter, "max_chunk_chars", 1500)
                )

                # 若未分片流式推送且正文超过 500 字，自动提炼移动端摘要并生成 24h 阅读短链；若为微信且未附带导出文件，同步生成真实 .md 文件推送
                if not streamed_segments and len(formatted_text) > 500:
                    rep = await personal_workflow_service.create_shared_report(
                        title=f"{self_name} 详报 ({datetime.now().strftime('%m-%d %H:%M')})",
                        content_md=raw_reply,
                        link_id=link["link_id"],
                        sender_id=inbound.sender_id,
                    )
                    if inbound.channel_type == "weixin_clawbot" and not exported_attachments:
                        try:
                            auto_doc = await personal_workflow_service.export_document(
                                title=f"{self_name} 详报 ({datetime.now().strftime('%m-%d %H:%M')})",
                                content=raw_reply,
                                fmt="md",
                            )
                            if auto_doc.get("success"):
                                exported_attachments.append(
                                    {
                                        "file_path": str(auto_doc["file_path"]),
                                        "file_name": str(auto_doc["file_name"]),
                                    }
                                )
                        except Exception:
                            pass
                    compact_summary = formatted_text[:420].rstrip() + "..."
                    file_tip = (
                        f"📎 已同步生成完整文档 `{exported_attachments[0]['file_name']}` 发送给你。"
                        if exported_attachments
                        else f"📄 完整详报较长（共 {len(raw_reply)} 字），已生成 24 小时临时阅读页：{rep['path']}"
                    )
                    formatted_text = f"{compact_summary}\n\n{file_tip}"
                    out_chunks = [formatted_text]

                # 若前面已流式推送过部分段落，仅补发尚未推送的尾段；否则整段推送
                if streamed_segments:
                    if stream_buf.strip():
                        await _flush_stream_segment(stream_buf.strip())
                    if exported_attachments:
                        await adapter.send_reply(
                            inbound=inbound,
                            outbound=OutboundMessage(
                                raw_markdown="",
                                formatted_text="",
                                chunks=[],
                                attachments=exported_attachments,
                                metadata={"attachments_only": True},
                            ),
                            link_config=link,
                        )
                    push_res = {"pushed": True, "streamed_segments": len(streamed_segments)}
                else:
                    outbound = OutboundMessage(
                        raw_markdown=raw_reply,
                        formatted_text=formatted_text,
                        chunks=out_chunks,
                        attachments=exported_attachments,
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

    def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        """获取按会话隔离的串行锁，防止连发消息并发复读或流式串台。"""
        lock = self._session_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_key] = lock
        return lock

    async def _send_fast_reply(
        self,
        *,
        inbound: InboundMessage,
        link: dict[str, Any],
        text: str,
        adapter: Any,
    ) -> dict[str, Any]:
        """向 IM 通道即时返回零延迟文本消息（无需经过 LLM）。"""
        fmt_text, out_chunks = format_for_im_channel(
            text, max_chunk_chars=getattr(adapter, "max_chunk_chars", 1500)
        )
        outbound = OutboundMessage(
            raw_markdown=text,
            formatted_text=fmt_text,
            chunks=out_chunks or [fmt_text],
            metadata={"fast_command": True},
        )
        push_res = await adapter.send_reply(inbound=inbound, outbound=outbound, link_config=link)
        return {
            "status": "completed",
            "fast_command": True,
            "reply_text": fmt_text,
            "reply_chunks": out_chunks,
            "push_result": push_res,
        }

    async def _try_handle_slash_command(
        self,
        *,
        inbound: InboundMessage,
        link: dict[str, Any],
        caller: CallerIdentity,
        adapter: Any,
    ) -> dict[str, Any] | None:
        """在网关前置拦截 L2 确认回复、高频零延迟快捷指令与纯转发收藏链接。"""
        import re as _re
        from ..agent.runtime import agent_runtime
        from ..service.personal_assistant_service import personal_assistant_service
        from ..service.personal_workflow_service import personal_workflow_service
        from ..service.server_ops_service import server_ops_service

        raw = (inbound.text or "").strip()
        if not raw:
            return None

        session_key = f"{inbound.channel_type}:{link['link_id']}:{inbound.chat_id}:{inbound.sender_id}"
        low = raw.lower()
        is_owner_mode = int(link.get("owner_mode") or 0) == 1 and (
            inbound.channel_type == "weixin_clawbot" or caller.role == "hq_admin"
        )
        reply_text: str | None = None

        # 0. 优先拦截主人对 L2 高危操作的二次确认或取消回复
        if is_owner_mode:
            apr_res = await server_ops_service.resolve_approval_reply(
                sender_id=inbound.sender_id, link_id=link["link_id"], text=raw
            )
            if apr_res and apr_res.get("handled"):
                reply_text = str(apr_res.get("reply_text") or "已处理确认操作。")

        # 1. /stop 或 停/取消任务/中止 -> 秒级中止当前运行并清理挂起防抖队列
        if reply_text is None and low in ("/stop", "停", "停止", "取消任务", "中止"):
            buf = self._debounce_buffers.pop(session_key, None)
            if buf and buf.get("timer") and not buf["timer"].done():
                buf["timer"].cancel()
            conv_id = self._active_session_conv.get(session_key, "")
            if not conv_id:
                conv_id, _ = await self._get_or_create_session_conv_id(
                    link_id=link["link_id"],
                    channel_type=inbound.channel_type,
                    sender_id=inbound.sender_id,
                    chat_id=inbound.chat_id,
                )
            cancelled = agent_runtime.cancel_session_stream(conv_id)
            reply_text = (
                "已为你中止当前正在处理的任务。"
                if cancelled
                else "当前没有正在运行中的任务，随时告诉我下一步要做什么。"
            )
            if buf and buf.get("msg_ids"):
                for mid in buf["msg_ids"]:
                    await self._finish_dedup_slot(
                        channel_type=inbound.channel_type,
                        external_msg_id=mid,
                        status="completed",
                        reply_preview=reply_text,
                    )

        # 2. /new 或 /reset -> 开启新短期上下文（保留长期记忆）
        elif reply_text is None and low in ("/new", "/reset"):
            await self._reset_session_conv_id(
                link_id=link["link_id"],
                channel_type=inbound.channel_type,
                sender_id=inbound.sender_id,
                chat_id=inbound.chat_id,
            )
            reply_text = "已开启全新短期对话上下文，之前的备忘、待办和长期记忆都完整保留着。"

        # 3. /memo <内容> -> 秒存持久化备忘
        elif reply_text is None and low.startswith("/memo"):
            content = raw[5:].strip()
            if not content:
                notes = await personal_assistant_service.list_notes(
                    sender_id=inbound.sender_id, category="memo", limit=8
                )
                if not notes:
                    reply_text = "当前还没有备忘记录。发送 `/memo 备忘内容` 即可立刻记下。"
                else:
                    lines = [f"最近 {len(notes)} 条备忘："]
                    for idx, n in enumerate(notes, 1):
                        lines.append(f"{idx}. [{n['created_at_str']}] {n['content']}")
                    reply_text = "\n".join(lines)
            else:
                saved = await personal_assistant_service.add_note(
                    sender_id=inbound.sender_id,
                    content=content,
                    category="memo",
                    link_id=link["link_id"],
                )
                reply_text = f"已记下备忘（{saved['created_at_str']}）：{saved['content']}"

        # 4. /todo 或 /todo <内容> -> 秒查或新增待办
        elif reply_text is None and low.startswith("/todo"):
            content = raw[5:].strip()
            if not content:
                todos = await personal_assistant_service.list_notes(
                    sender_id=inbound.sender_id, category="todo", limit=10
                )
                if not todos:
                    reply_text = "当前没有未完成的待办事项。发送 `/todo 待办内容` 即可添加。"
                else:
                    lines = [f"当前共有 {len(todos)} 条待办："]
                    for idx, n in enumerate(todos, 1):
                        lines.append(f"{idx}. [{n['created_at_str']}] {n['content']}")
                    reply_text = "\n".join(lines)
            else:
                saved = await personal_assistant_service.add_note(
                    sender_id=inbound.sender_id,
                    content=content,
                    category="todo",
                    link_id=link["link_id"],
                )
                reply_text = f"已添加待办：{saved['content']}"

        # 5. /reminders 或 /tasks -> 查看当前定时提醒与计划任务；/remind 或 /task <时间> <内容> -> 秒设提醒/计划任务
        elif reply_text is None and low in ("/reminders", "/remind", "/tasks", "/task"):
            rems = await personal_assistant_service.list_im_reminders(
                link_id=link["link_id"], target_user_id=inbound.sender_id
            )
            if not rems:
                reply_text = "当前没有活跃定时提醒/计划任务。示例：`/task 10分钟后 喝水` 或 `/task 每天早上8:30 发我北京天气和今日待办`。"
            else:
                lines = [f"活跃定时提醒/计划任务（{len(rems)} 条）："]
                for idx, r in enumerate(rems, 1):
                    lines.append(f"{idx}. {r['display_content']}（下次：{r.get('next_run_at') or '-'}）")
                reply_text = "\n".join(lines)

        elif reply_text is None and (low.startswith("/remind ") or low.startswith("/task ")):
            rest = raw[8:].strip() if low.startswith("/remind ") else raw[6:].strip()
            parts = rest.split(None, 1)
            if len(parts) < 2:
                reply_text = "格式：`/task <时间> <内容>`，例如：`/task 30分钟后 检查服务器` 或 `/task 每天早上8:30 发我北京天气和待办`。"
            else:
                try:
                    ws_id = await self.ensure_im_link_workspace(
                        link_id=link["link_id"],
                        target_user_label=str(link.get("target_user_label") or ""),
                        expert_id=str(link.get("expert_id") or ""),
                    )
                    is_agent_task = any(
                        kw in parts[1]
                        for kw in ("天气", "简报", "晨报", "巡检", "汇总", "总结", "汇报", "新闻", "检查")
                    )
                    rem = await personal_assistant_service.create_im_reminder(
                        workspace_id=ws_id,
                        link_id=link["link_id"],
                        channel_type=inbound.channel_type,
                        target_user_id=inbound.sender_id,
                        trigger_expr=parts[0],
                        content=parts[1],
                        run_as_agent=is_agent_task,
                    )
                    mode_tag = "（AI 自动执行并推送）" if is_agent_task else ""
                    reply_text = f"已添加计划任务「{rem['content']}」{mode_tag}，将于 {rem['schedule_desc']} 微信通知你。"
                except ValueError as ve:
                    reply_text = str(ve)

        # 6. /status [别名] -> 主人模式下零延迟服务器健康体检
        elif reply_text is None and low.startswith("/status"):
            if not is_owner_mode:
                reply_text = "该渠道链接未开启主人控制台模式，暂不支持查看服务器运维状态。"
            else:
                srv_alias = raw[7:].strip()
                h_res = await server_ops_service.inspect_server_health(alias=srv_alias)
                reply_text = str(h_res.get("summary") or "服务器体检完成")

        # 7. /brief [城市] -> 秒出今日私人晨报
        elif reply_text is None and low.startswith("/brief"):
            city_arg = raw[6:].strip() or "北京"
            reply_text = await personal_workflow_service.build_daily_briefing(
                sender_id=inbound.sender_id, city=city_arg
            )

        # 8. /cd <目录或工作区> -> 切换代码工作区偏好
        elif reply_text is None and low.startswith("/cd"):
            if not is_owner_mode:
                reply_text = "该渠道未开启主人控制台模式，无法切换服务器工作区目录。"
            else:
                target_wd = raw[3:].strip()
                if not target_wd:
                    cur_wd = getattr(self, "_session_workdirs", {}).get(session_key) or "默认工作区"
                    reply_text = f"当前工作区偏好：`{cur_wd}`。发送 `/cd 路径` 可切换。"
                else:
                    if not hasattr(self, "_session_workdirs"):
                        self._session_workdirs = {}
                    self._session_workdirs[session_key] = target_wd
                    reply_text = f"已切换当前会话首选工作区/项目目录为：`{target_wd}`"

        # 9. /inbox <链接或关键词> 或纯转发 URL -> 自动入碎片收藏箱
        elif reply_text is None and (
            low.startswith("/inbox") or bool(_re.fullmatch(r"https?://\S+", raw))
        ):
            arg = raw if raw.startswith("http") else raw[6:].strip()
            if arg.startswith(("http://", "https://")):
                inb = await personal_workflow_service.save_inbox_item(
                    sender_id=inbound.sender_id, link_id=link["link_id"], url=arg
                )
                reply_text = (
                    f"📥 已收录至个人碎片收藏箱（{'/'.join(inb['tags'])}）：\n"
                    f"**{inb['title']}**\n"
                    f"{inb['summary']}"
                )
            else:
                hits = await personal_workflow_service.search_inbox_items(
                    sender_id=inbound.sender_id, keyword=arg, limit=6
                )
                if not hits:
                    reply_text = "收藏箱暂无匹配内容，直接把文章链接转发给我就行。"
                else:
                    lines = [f"收藏箱最近 {len(hits)} 条："]
                    for idx, it in enumerate(hits, 1):
                        lines.append(f"{idx}. [{it['created_at_str']}] {it['title']}\n   {it['summary'][:90]}")
                    reply_text = "\n".join(lines)

        # 10. /help -> 快捷指令清单
        elif reply_text is None and low == "/help":
            reply_text = (
                "常用零延迟快捷指令：\n"
                "- `/memo 内容`：秒存备忘（发 `/memo` 查列表）\n"
                "- `/todo 内容`：添加或查看待办\n"
                "- `/remind 时间 内容` 或 `/task 时间 内容`：添加微信计划任务/提醒（如 `/task 每天早上8:30 发我天气和待办`）\n"
                "- `/brief [城市]`：生成今日天气+待办+服务器晨报\n"
                "- `/inbox [链接或词]` 或直接转发文章链接：收入收藏箱/检索\n"
                "- `/status [服务器]`：服务器一键体检（主人模式）\n"
                "- `/cd 目录`：切换代码工作目录（主人模式）\n"
                "- `/new`：开启新话题；`/stop` 或 `停`：立刻中止当前任务"
            )

        if reply_text is None:
            return None

        res = await self._send_fast_reply(inbound=inbound, link=link, text=reply_text, adapter=adapter)
        await self._finish_dedup_slot(
            channel_type=inbound.channel_type,
            external_msg_id=inbound.external_msg_id,
            status="completed",
            reply_preview=reply_text,
        )
        return res

    async def _enqueue_debounced_turn(
        self,
        *,
        inbound: InboundMessage,
        link: dict[str, Any],
        caller: CallerIdentity,
        adapter: Any,
    ) -> None:
        """连发消息防抖合并：短时间内同一用户的碎片消息合并为一次推理，超过 8 秒转入后台并立即释放主会话锁。"""
        session_key = f"{inbound.channel_type}:{link['link_id']}:{inbound.chat_id}:{inbound.sender_id}"
        has_media = bool(
            (inbound.raw_payload or {}).get("attachments")
            or (inbound.raw_payload or {}).get("has_media")
            or str((inbound.raw_payload or {}).get("msg_type") or "").lower() in ("image", "file", "voice")
        )
        delay = _DEBOUNCE_MEDIA_SECONDS if has_media else _DEBOUNCE_TEXT_SECONDS

        existing_buf = self._debounce_buffers.get(session_key)
        if existing_buf is not None:
            prev_timer = existing_buf["timer"]
            if prev_timer is not None and not prev_timer.done():
                prev_timer.cancel()
            buf = existing_buf
        else:
            buf: _DebounceBuffer = {
                "texts": [],
                "attachments": [],
                "quote_texts": [],
                "msg_ids": [],
                "last_inbound": inbound,
                "link": link,
                "caller": caller,
                "adapter": adapter,
                "timer": None,
            }
            self._debounce_buffers[session_key] = buf

        if inbound.text and inbound.text not in ("[收到图片消息]", "[收到语音消息]", "[收到媒体附件]"):
            buf["texts"].append(inbound.text)
        elif inbound.text and not (inbound.raw_payload or {}).get("attachments"):
            buf["texts"].append(inbound.text)
        raw_atts = (inbound.raw_payload or {}).get("attachments") or []
        if isinstance(raw_atts, list) and raw_atts:
            buf["attachments"].extend(raw_atts)
        q_txt = str((inbound.raw_payload or {}).get("quote_text") or "").strip()
        if q_txt:
            buf["quote_texts"].append(q_txt)
        buf["msg_ids"].append(inbound.external_msg_id)
        buf["last_inbound"] = inbound
        buf["link"] = link
        buf["caller"] = caller
        buf["adapter"] = adapter

        async def _flush_after_delay() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

            cur_buf = self._debounce_buffers.pop(session_key, None)
            if not cur_buf:
                return
            merged_text = "\n".join(t for t in cur_buf["texts"] if t).strip()
            base_in: InboundMessage = cur_buf["last_inbound"]
            if not merged_text:
                merged_text = base_in.text
            merged_raw = dict(base_in.raw_payload or {})
            if cur_buf["attachments"]:
                merged_raw["attachments"] = cur_buf["attachments"]
            if cur_buf["quote_texts"]:
                merged_raw["quote_text"] = "\n".join(cur_buf["quote_texts"])

            msg_ids: list[str] = list(cur_buf["msg_ids"])
            merged_inbound = InboundMessage(
                channel_type=base_in.channel_type,
                endpoint_token=base_in.endpoint_token,
                external_msg_id=base_in.external_msg_id,
                sender_id=base_in.sender_id,
                sender_name=base_in.sender_name,
                chat_id=base_in.chat_id,
                text=merged_text,
                reply_context=base_in.reply_context,
                raw_payload=merged_raw,
            )

            exec_task = asyncio.create_task(
                self._execute_turn(
                    inbound=merged_inbound,
                    link=cur_buf["link"],
                    caller=cur_buf["caller"],
                    adapter=cur_buf["adapter"],
                )
            )
            lock = self._get_session_lock(session_key)
            await lock.acquire()
            detached_to_bg = False
            try:
                done_set, _ = await asyncio.wait({exec_task}, timeout=_LONG_TASK_DETACH_SECONDS)
                if not done_set:
                    detached_to_bg = True
                    try:
                        await self._send_fast_reply(
                            inbound=merged_inbound,
                            link=cur_buf["link"],
                            text=self._pick_detach_notice(),
                            adapter=cur_buf["adapter"],
                        )
                    except Exception:
                        pass
            finally:
                if lock.locked():
                    lock.release()

            async def _finalize_task(t: asyncio.Task[Any]) -> None:
                try:
                    res = await t
                    reply_preview = str(res.get("reply_text") or "")
                    for mid in msg_ids:
                        await self._finish_dedup_slot(
                            channel_type=merged_inbound.channel_type,
                            external_msg_id=mid,
                            status="completed",
                            reply_preview=reply_preview,
                        )
                except Exception as err:
                    for mid in msg_ids:
                        await self._finish_dedup_slot(
                            channel_type=merged_inbound.channel_type,
                            external_msg_id=mid,
                            status="failed",
                            reply_preview=str(err),
                        )

            if detached_to_bg:
                bg_fin = asyncio.create_task(_finalize_task(exec_task))
                self._bg_tasks.add(bg_fin)
                bg_fin.add_done_callback(self._bg_tasks.discard)
            else:
                await _finalize_task(exec_task)

        timer_task = asyncio.create_task(_flush_after_delay())
        buf["timer"] = timer_task
        self._bg_tasks.add(timer_task)
        timer_task.add_done_callback(self._bg_tasks.discard)

    async def handle_inbound(
        self,
        *,
        channel_type: str,
        endpoint_token: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """统一入站处理入口：支持快捷指令秒回、连发防抖合并、sync_reply 同步与 async_push 异步回推。"""
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

        # 高频快捷指令与 L2 确认零延迟直达执行（不走防抖与大模型）
        fast_res = await self._try_handle_slash_command(
            inbound=inbound, link=link, caller=caller, adapter=adapter
        )
        if fast_res is not None:
            return fast_res
        eff_mode = (mode or payload.get("mode") or adapter.default_mode or "sync_reply").strip()

        if eff_mode == "async_push":
            # 收到消息瞬间立即异步点亮“正在输入中...”，避免防抖窗口期间微信端无反馈
            asyncio.create_task(adapter.send_typing(inbound=inbound, is_typing=True, link_config=link))
            await self._enqueue_debounced_turn(
                inbound=inbound, link=link, caller=caller, adapter=adapter
            )
            return {
                "status": "accepted",
                "mode": "async_push",
                "external_msg_id": inbound.external_msg_id,
                "caller_role": caller.role,
                "caller_scope": caller.scope,
                "message": "已接收消息并进入防抖合并与后台串行处理队列。",
            }

        session_key = f"{inbound.channel_type}:{link['link_id']}:{inbound.chat_id}:{inbound.sender_id}"
        async with self._get_session_lock(session_key):
            return await self._execute_turn(
                inbound=inbound, link=link, caller=caller, adapter=adapter
            )


channel_gateway = ChannelGateway()

