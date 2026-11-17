"""
Swarm 原生蜂群异步事件驱动引擎
基于 asyncio.Condition 实现零轮询唤醒、Worker 独立 conv_id 隔离执行、
AgentProfile 权限继承与级联终止清理。
"""
import asyncio
from dataclasses import dataclass, field
import re
import uuid
from typing import Any, AsyncGenerator

from ..core.logging_config import logger
from ..repository.conversation_repository import ConversationRepository
from ..repository.swarm_repository import SwarmRepository
from .shell_executor import shell_executor


_MENTION_RE = re.compile(r"@([a-zA-Z0-9_\-\u4e00-\u9fff]+)")


@dataclass
class _SwarmRuntimeState:
    """单个 Swarm 实例的内存并发控制状态。"""
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    worker_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    workspace_id: str = "default"
    cwd: str | None = None
    default_model: str = "4 Argon"


class SwarmManager:
    """原生蜂群协作管理器（全局单例）"""

    def __init__(self) -> None:
        self._states: dict[str, _SwarmRuntimeState] = {}

    def _get_state(self, swarm_id: str) -> _SwarmRuntimeState:
        if swarm_id not in self._states:
            self._states[swarm_id] = _SwarmRuntimeState()
        return self._states[swarm_id]

    @staticmethod
    def parse_mentions(text: str) -> list[str]:
        """从消息文本中提取去重保序的 @mention 名称列表。"""
        seen: set[str] = set()
        result: list[str] = []
        for m in _MENTION_RE.finditer(text or ""):
            name = m.group(1).strip()
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result

    async def _broadcast(self, swarm_id: str, event: dict[str, Any]) -> None:
        """向所有活跃 SSE 订阅者广播蜂群事件。"""
        state = self._get_state(swarm_id)
        stale: list[asyncio.Queue[dict[str, Any]]] = []
        for q in list(state.subscribers):
            try:
                q.put_nowait(event)
            except Exception:
                stale.append(q)
        for q in stale:
            state.subscribers.discard(q)

    async def _broadcast_swarm_snapshot(self, swarm_id: str) -> None:
        """广播当前 Swarm 最新成员与 head_seq 状态快照。"""
        swarm = await SwarmRepository.get_swarm(swarm_id)
        if not swarm:
            return
        members = await SwarmRepository.list_members(swarm_id)
        await self._broadcast(
            swarm_id,
            {
                "type": "swarm_update",
                "swarm_id": swarm_id,
                "swarm": swarm,
                "members": members,
            },
        )

    async def subscribe(self, swarm_id: str) -> AsyncGenerator[dict[str, Any], None]:
        """订阅指定 Swarm 的实时事件流（swarm_update / swarm_message / agent_turn）。"""
        state = self._get_state(swarm_id)
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        state.subscribers.add(q)
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            state.subscribers.discard(q)

    async def get_or_create_swarm(
        self,
        session_id: str,
        *,
        swarm_id: str | None = None,
        title: str = "Swarm 协作",
        workspace_id: str = "default",
        cwd: str | None = None,
        default_model: str = "4 Argon",
    ) -> dict[str, Any]:
        """获取或创建关联主会话的 Swarm 实例。"""
        swarm = None
        if swarm_id:
            swarm = await SwarmRepository.get_swarm(swarm_id)
        if not swarm:
            swarm = await SwarmRepository.get_by_session(session_id)
        if not swarm or swarm.get("status") == "killed":
            swarm = await SwarmRepository.create_swarm(
                session_id=session_id,
                title=title,
                swarm_id=swarm_id,
            )
        state = self._get_state(swarm["id"])
        state.workspace_id = workspace_id or "default"
        state.cwd = cwd
        state.default_model = default_model or "4 Argon"
        return swarm

    async def add_member(
        self,
        swarm_id: str,
        *,
        name: str,
        brief: str = "",
        role: str = "worker",
        agent_id: str | None = None,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        allowed_write_dirs: list[str] | None = None,
        skill_ids: list[str] | None = None,
        system_prompt: str = "",
        workspace_id: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """
        向 Swarm 添加（或重用）成员并启动异步 Worker 协程。
        若传入 agent_id，自动从 AgentProfileRepository 继承模型、Skill、系统提示词与权限隔离配置。
        """
        state = self._get_state(swarm_id)
        eff_ws_id = workspace_id or state.workspace_id or "default"
        eff_cwd = cwd if cwd is not None else state.cwd
        eff_model = model
        eff_tools = allowed_tools
        eff_dirs = allowed_write_dirs
        eff_skills = list(skill_ids or [])
        eff_prompt = system_prompt or ""

        if agent_id:
            try:
                from ..repository.agent_repository import AgentProfileRepository
                profile = await AgentProfileRepository.get_agent(agent_id)
                if profile:
                    if not eff_model:
                        cand_model = str(profile.get("base_model") or profile.get("model_name") or profile.get("model") or "").strip()
                        if cand_model and cand_model.lower() != "auto":
                            eff_model = cand_model
                    if eff_tools is None and profile.get("allowed_tools"):
                        eff_tools = list(profile["allowed_tools"])
                    if eff_dirs is None and profile.get("allowed_write_dirs"):
                        eff_dirs = list(profile["allowed_write_dirs"])
                    if not eff_skills and profile.get("skill_ids"):
                        eff_skills = list(profile["skill_ids"])
                    if not eff_prompt:
                        eff_prompt = str(profile.get("context_prompt") or profile.get("system_prompt") or "")
            except Exception as prof_err:
                logger.warning(f"[SwarmManager] 读取 AgentProfile({agent_id}) 失败，使用默认配置: {prof_err}")

        existing = await SwarmRepository.get_member(swarm_id, name)
        conv_id = existing.get("conv_id") if existing else None
        if not conv_id:
            conv_id = f"swarm_{swarm_id}_{name}_{uuid.uuid4().hex[:6]}"
            await ConversationRepository.create({
                "id": conv_id,
                "title": f"[Swarm:{name}]",
                "model_name": eff_model or state.default_model,
                "scene_mode": "agent",
                "incognito": 0,
                "workspace_id": eff_ws_id,
                "current_cwd": eff_cwd,
            })

        member = await SwarmRepository.upsert_member(
            swarm_id=swarm_id,
            name=name,
            role=role,
            agent_id=agent_id,
            conv_id=conv_id,
            model=eff_model or state.default_model,
            state="waiting",
            allowed_tools=eff_tools,
            allowed_write_dirs=eff_dirs,
            skill_ids=eff_skills,
            system_prompt=eff_prompt,
            brief=brief,
        )

        if role == "worker":
            self._ensure_worker_task(swarm_id, name)

        await self._broadcast_swarm_snapshot(swarm_id)

        if brief.strip():
            await self.post_message(
                swarm_id,
                sender="coordinator",
                text=f"@{name} {brief.strip()}",
                kind="brief",
            )

        return member

    def _ensure_worker_task(self, swarm_id: str, name: str) -> None:
        """确保指定 Worker 的事件循环协程正在运行。"""
        state = self._get_state(swarm_id)
        existing_task = state.worker_tasks.get(name)
        if existing_task and not existing_task.done():
            return
        state.worker_tasks[name] = asyncio.create_task(
            self._worker_loop(swarm_id, name),
            name=f"swarm-worker-{swarm_id}-{name}",
        )

    async def _worker_loop(self, swarm_id: str, name: str) -> None:
        """Worker 成员的零轮询事件驱动主循环。"""
        state = self._get_state(swarm_id)
        try:
            while True:
                member = await SwarmRepository.get_member(swarm_id, name)
                if not member or member.get("state") == "killed":
                    break

                last_seq = int(member.get("last_seq") or 0)
                messages, head_seq = await SwarmRepository.read_messages(
                    swarm_id,
                    since=last_seq,
                    limit=50,
                )

                # 过滤出点名自己的消息或全局广播（跳过自己发的消息）
                relevant = [
                    m for m in messages
                    if m.get("sender") != name
                    and (name in (m.get("mentions") or []) or "all" in (m.get("mentions") or []))
                ]

                if not relevant:
                    if head_seq > last_seq:
                        await SwarmRepository.update_member_state(
                            swarm_id, name, "waiting", last_seq=head_seq
                        )
                    elif member.get("state") != "waiting":
                        await SwarmRepository.update_member_state(swarm_id, name, "waiting")
                    await self._broadcast_swarm_snapshot(swarm_id)

                    async with state.cond:
                        # 二次检查防止通知丢失
                        check_msgs, _ = await SwarmRepository.read_messages(
                            swarm_id, since=head_seq, limit=10
                        )
                        has_new = any(
                            m.get("sender") != name
                            and (name in (m.get("mentions") or []) or "all" in (m.get("mentions") or []))
                            for m in check_msgs
                        )
                        if not has_new:
                            await state.cond.wait()
                    continue

                max_msg_seq = max(int(m["seq"]) for m in relevant)
                await SwarmRepository.update_member_state(
                    swarm_id, name, "working", last_seq=max_msg_seq
                )
                await self._broadcast_swarm_snapshot(swarm_id)

                await self._execute_worker_turn(swarm_id, member, relevant)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[SwarmManager] Worker({swarm_id}/{name}) 循环异常退出: {exc}", exc_info=True)
            try:
                await SwarmRepository.update_member_state(swarm_id, name, "stopped")
                await self._broadcast_swarm_snapshot(swarm_id)
            except Exception:
                pass

    async def _execute_worker_turn(
        self,
        swarm_id: str,
        member: dict[str, Any],
        incoming_messages: list[dict[str, Any]],
    ) -> None:
        """为被唤醒的 Worker 启动独立 AgentRuntime 推理并转发 SSE 事件。"""
        from .runtime import AgentRuntime

        state = self._get_state(swarm_id)
        name = member["name"]
        conv_id = member["conv_id"]

        msg_lines = [
            f"[#{m['seq']} {m['sender']}]: {m['text']}"
            for m in incoming_messages
        ]
        skill_hint = ""
        if member.get("skill_ids"):
            skill_hint = f"\n[已装配技能]: {', '.join(member['skill_ids'])}"

        prompt = (
            f"你收到了来自蜂群消息板的最新点名消息：{skill_hint}\n"
            + "\n".join(msg_lines)
            + "\n\n请立即按职责完成任务，完成后调用 `swarm_post` 以 `@coordinator [done] ...` 汇报。"
        )

        # 若成员配置了白名单，确保其保留蜂群通信原语
        eff_tools: list[str] | None = member.get("allowed_tools")
        if eff_tools is not None and len(eff_tools) > 0:
            merged = list(eff_tools)
            for swarm_t in ("swarm_post", "swarm_read", "swarm_wait"):
                if swarm_t not in merged:
                    merged.append(swarm_t)
            eff_tools = merged

        runtime = AgentRuntime()
        async for evt in runtime.chat_stream(
            conversation_id=conv_id,
            user_message=prompt,
            model_name=member.get("model") or state.default_model,
            system_override=member.get("system_prompt") or None,
            workspace_id=state.workspace_id,
            scene_mode="agent",
            allowed_tools=eff_tools,
            allowed_write_dirs=member.get("allowed_write_dirs"),
            swarm_mode=True,
            swarm_id=swarm_id,
            swarm_member_name=name,
            swarm_role="worker",
            swarm_brief=member.get("brief") or "",
        ):
            await self._broadcast(
                swarm_id,
                {
                    "type": "agent_turn",
                    "swarm_id": swarm_id,
                    "member_name": name,
                    "agent_id": member.get("agent_id"),
                    "event": evt,
                },
            )

    async def post_message(
        self,
        swarm_id: str,
        *,
        sender: str,
        text: str,
        kind: str = "message",
    ) -> dict[str, Any]:
        """向 Swarm 消息板发帖，解析 @mention 并唤醒目标成员。"""
        mentions = self.parse_mentions(text)
        msg = await SwarmRepository.post_message(
            swarm_id=swarm_id,
            sender=sender,
            text=text,
            kind=kind,
            mentions=mentions,
        )

        # 若被 @mention 的成员当前处于 stopped 状态，自动复活其任务循环
        members = await SwarmRepository.list_members(swarm_id)
        for mem in members:
            if (
                mem.get("role") == "worker"
                and mem.get("state") == "stopped"
                and (mem["name"] in mentions or "all" in mentions)
            ):
                await SwarmRepository.update_member_state(swarm_id, mem["name"], "waiting")
                self._ensure_worker_task(swarm_id, mem["name"])

        state = self._get_state(swarm_id)
        async with state.cond:
            state.cond.notify_all()

        await self._broadcast(
            swarm_id,
            {
                "type": "swarm_message",
                "swarm_id": swarm_id,
                "message": msg,
            },
        )
        return msg

    async def wait_for_messages(
        self,
        swarm_id: str,
        *,
        member_name: str,
        since: int = 0,
        mentions_only: bool = True,
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any]:
        """
        零轮询挂起等待新消息到达。
        供 swarm_wait 工具在执行分支中调用。
        """
        state = self._get_state(swarm_id)
        member = await SwarmRepository.get_member(swarm_id, member_name)
        if member and member.get("state") != "killed":
            await SwarmRepository.update_member_state(swarm_id, member_name, "waiting")
            await self._broadcast_swarm_snapshot(swarm_id)

        deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout_seconds or 120.0))
        while True:
            msgs, head_seq = await SwarmRepository.read_messages(swarm_id, since=since, limit=50)
            matched = [
                m for m in msgs
                if m.get("sender") != member_name
                and (
                    not mentions_only
                    or member_name in (m.get("mentions") or [])
                    or "all" in (m.get("mentions") or [])
                )
            ]
            if matched:
                if member and member.get("state") != "killed":
                    max_seq = max(int(m["seq"]) for m in matched)
                    await SwarmRepository.update_member_state(
                        swarm_id, member_name, "working", last_seq=max_seq
                    )
                    await self._broadcast_swarm_snapshot(swarm_id)
                return {
                    "messages": matched,
                    "head": head_seq,
                    "timed_out": False,
                }

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                if member and member.get("state") != "killed":
                    await SwarmRepository.update_member_state(swarm_id, member_name, "working")
                    await self._broadcast_swarm_snapshot(swarm_id)
                return {
                    "messages": [],
                    "head": head_seq,
                    "timed_out": True,
                }

            async with state.cond:
                # 再次确认是否有并发刚写入的消息
                recheck_msgs, recheck_head = await SwarmRepository.read_messages(
                    swarm_id, since=since, limit=50
                )
                recheck_matched = [
                    m for m in recheck_msgs
                    if m.get("sender") != member_name
                    and (
                        not mentions_only
                        or member_name in (m.get("mentions") or [])
                        or "all" in (m.get("mentions") or [])
                    )
                ]
                if recheck_matched:
                    continue
                try:
                    await asyncio.wait_for(state.cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return {
                        "messages": [],
                        "head": recheck_head,
                        "timed_out": True,
                    }

    async def kill_member(self, swarm_id: str, name: str) -> dict[str, Any] | None:
        """永久终止指定 Swarm 成员并级联清理其子会话流、进程树与后台下载任务。"""
        state = self._get_state(swarm_id)
        member = await SwarmRepository.update_member_state(swarm_id, name, "killed")

        task = state.worker_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass

        if member and member.get("conv_id"):
            conv_id = str(member["conv_id"])
            try:
                from .runtime import AgentRuntime
                AgentRuntime.cancel_session_stream(conv_id)
            except Exception:
                pass
            try:
                shell_executor.cancel_session_processes(conv_id)
            except Exception:
                pass
            try:
                shell_executor.background_manager.cancel_session_jobs(conv_id)
            except Exception:
                pass

        async with state.cond:
            state.cond.notify_all()
        await self._broadcast_swarm_snapshot(swarm_id)
        return member

    async def kill_swarm(self, swarm_id: str) -> None:
        """级联终止整个 Swarm 及其所有成员和后台任务。"""
        members = await SwarmRepository.list_members(swarm_id)
        for m in members:
            if m.get("state") != "killed":
                await self.kill_member(swarm_id, m["name"])
        await SwarmRepository.update_swarm(swarm_id, status="killed")
        await self._broadcast_swarm_snapshot(swarm_id)


swarm_manager = SwarmManager()
