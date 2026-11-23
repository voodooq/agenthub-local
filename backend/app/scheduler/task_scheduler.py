"""
无人值守计划任务调度器 (TaskScheduler)
功能：
1. 纯 Python 标准 5 段 Cron 解析（分 时 日 月 周）及 @hourly/@daily/@weekly 等宏支持
2. 后台周期巡检 scheduled_tasks 表，计算持久化 next_run_at，到点异步派发（防同分钟重入）
3. 真实无头调用 AgentRuntime 执行任务，落盘 scheduled_task_runs 运行历史、耗时与完整结果/错误
4. 执行完成后调用 Windows 原生 Toast 桌面通知
"""
import asyncio
import os
import subprocess
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Any

from ..core.logging_config import logger
from ..core.runtime_paths import get_silent_subprocess_kwargs
from ..repository.db import db_manager


def send_windows_toast(title: str, message: str) -> None:
    """通过 Windows PowerShell 发送原生系统桌面通知。"""
    if os.name != "nt":
        return

    try:
        clean_title = title.replace('"', '""').replace("'", "''")
        clean_msg = message.replace('"', '""').replace("'", "''")
        ps_cmd = (
            f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; '
            f'$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); '
            f'$textNodes = $template.GetElementsByTagName("text"); '
            f'$textNodes.Item(0).AppendChild($template.CreateTextNode("{clean_title}")) > $null; '
            f'$textNodes.Item(1).AppendChild($template.CreateTextNode("{clean_msg}")) > $null; '
            f'$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AgentHub Local"); '
            f'$notification = [Windows.UI.Notifications.ToastNotification]::new($template); '
            f'$notifier.Show($notification);'
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **get_silent_subprocess_kwargs(),
        )
    except Exception as e:
        logger.warning(f"发送 Windows Toast 通知失败: {e}")


class CronExpression:
    """纯 Python 标准 5 段 Cron 表达式解析与调度计算器。"""

    ALIASES: dict[str, str] = {
        "@yearly": "0 0 1 1 *",
        "@annually": "0 0 1 1 *",
        "@monthly": "0 0 1 * *",
        "@weekly": "0 0 * * 0",
        "@daily": "0 0 * * *",
        "@midnight": "0 0 * * *",
        "@hourly": "0 * * * *",
        "yearly": "0 0 1 1 *",
        "annually": "0 0 1 1 *",
        "monthly": "0 0 1 * *",
        "weekly": "0 0 * * 0",
        "daily": "0 0 * * *",
        "hourly": "0 * * * *",
    }

    def __init__(
        self,
        raw_expr: str,
        minutes: frozenset[int],
        hours: frozenset[int],
        days: frozenset[int],
        months: frozenset[int],
        weekdays: frozenset[int],
        dom_is_wildcard: bool,
        dow_is_wildcard: bool,
    ) -> None:
        self.raw_expr = raw_expr
        self.minutes = minutes
        self.hours = hours
        self.days = days
        self.months = months
        self.weekdays = weekdays
        self.dom_is_wildcard = dom_is_wildcard
        self.dow_is_wildcard = dow_is_wildcard

    @classmethod
    def parse(cls, expr: str) -> "CronExpression":
        """解析标准 5 段 Cron 表达式或常用宏别名，非法格式抛出 ValueError。"""
        cleaned = (expr or "").strip()
        if not cleaned:
            raise ValueError("Cron 表达式不能为空")

        normalized = cls.ALIASES.get(cleaned.lower(), cleaned)
        parts = normalized.split()
        if len(parts) != 5:
            raise ValueError(f"无效的 Cron 表达式（需包含 5 段或合法宏）: {expr}")

        min_part, hour_part, dom_part, month_part, dow_part = parts
        minutes = cls._parse_field(min_part, 0, 59, is_dow=False)
        hours = cls._parse_field(hour_part, 0, 23, is_dow=False)
        days = cls._parse_field(dom_part, 1, 31, is_dow=False)
        months = cls._parse_field(month_part, 1, 12, is_dow=False)
        weekdays = cls._parse_field(dow_part, 0, 7, is_dow=True)

        return cls(
            raw_expr=expr,
            minutes=minutes,
            hours=hours,
            days=days,
            months=months,
            weekdays=weekdays,
            dom_is_wildcard=(dom_part.strip() == "*"),
            dow_is_wildcard=(dow_part.strip() == "*"),
        )

    @staticmethod
    def _parse_field(field_str: str, min_val: int, max_val: int, *, is_dow: bool) -> frozenset[int]:
        """解析单个 Cron 字段，支持 *、*/n、a-b、a-b/n 与逗号组合。"""
        result: set[int] = set()
        tokens = [t.strip() for t in field_str.split(",") if t.strip()]
        if not tokens:
            raise ValueError(f"Cron 字段为空: {field_str}")

        for token in tokens:
            step = 1
            range_part = token
            if "/" in token:
                segments = token.split("/")
                if len(segments) != 2 or not segments[1].isdigit():
                    raise ValueError(f"无效的 Cron 步长语法: {token}")
                range_part = segments[0]
                step = int(segments[1])
                if step <= 0:
                    raise ValueError(f"Cron 步长必须大于 0: {token}")

            if range_part == "*":
                start, end = min_val, max_val
            elif "-" in range_part:
                bounds = range_part.split("-")
                if len(bounds) != 2 or not bounds[0].isdigit() or not bounds[1].isdigit():
                    raise ValueError(f"无效的 Cron 范围语法: {token}")
                start, end = int(bounds[0]), int(bounds[1])
                if start < min_val or end > max_val or start > end:
                    raise ValueError(f"Cron 范围越界 ({min_val}-{max_val}): {token}")
            elif range_part.isdigit():
                val = int(range_part)
                if val < min_val or val > max_val:
                    raise ValueError(f"Cron 数值越界 ({min_val}-{max_val}): {token}")
                start = val
                end = max_val if "/" in token else val
            else:
                raise ValueError(f"无法识别的 Cron 片段: {token}")

            for v in range(start, end + 1, step):
                # 周日允许写成 0 或 7，统一归约到 0
                result.add(0 if (is_dow and v == 7) else v)

        return frozenset(result)

    def matches(self, dt: datetime) -> bool:
        """判断给定日期时间（精确到分钟）是否命中该 Cron 表达式。"""
        if dt.minute not in self.minutes or dt.hour not in self.hours or dt.month not in self.months:
            return False

        # Python weekday(): 周一=0 .. 周日=6；转换为 Cron: 周日=0, 周一=1 .. 周六=6
        cron_dow = (dt.weekday() + 1) % 7
        dom_match = dt.day in self.days
        dow_match = cron_dow in self.weekdays

        # 标准 Vixie Cron 语义：日与周均非通配符时取 OR，否则取 AND
        if not self.dom_is_wildcard and not self.dow_is_wildcard:
            return dom_match or dow_match
        return dom_match and dow_match

    def next_run_after(self, after_dt: datetime, max_iterations: int = 525600) -> datetime | None:
        """从 after_dt 的下一分钟开始，查找下一个匹配的调度时间点。"""
        candidate = after_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(max_iterations):
            if candidate.month not in self.months:
                # 快速跳过不匹配的月份
                next_month = candidate.month + 1
                next_year = candidate.year
                if next_month > 12:
                    next_month = 1
                    next_year += 1
                candidate = candidate.replace(year=next_year, month=next_month, day=1, hour=0, minute=0)
                continue
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        return None


def compute_next_run_iso(cron_expr: str, after_dt: datetime | None = None) -> str | None:
    """计算指定 Cron 表达式的下次运行时间 ISO 字符串，表达式非法时返回 None。"""
    try:
        cron = CronExpression.parse(cron_expr)
        base_dt = after_dt or datetime.now()
        nxt = cron.next_run_after(base_dt)
        return nxt.isoformat() if nxt else None
    except Exception:
        return None


class TaskScheduler:
    """计划任务后台守护循环与无头执行器。"""

    _instance: "TaskScheduler | None" = None

    def __init__(self) -> None:
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._running_tasks: set[str] = set()
        self._dispatched_minutes: dict[str, str] = {}
        self._bg_tasks: set[asyncio.Task] = set()

    @classmethod
    def get_instance(cls) -> "TaskScheduler":
        if cls._instance is None:
            cls._instance = TaskScheduler()
        return cls._instance

    def ensure_started(self) -> None:
        """确保后台调度巡检循环已在当前事件循环中启动。"""
        if self._loop_task is not None and not self._loop_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._loop_task = loop.create_task(self.start_loop())

    async def _resolve_headless_model(self, workspace_id: str) -> str:
        """解析无头计划任务应使用的已启用模型，避免硬编码回退导致未配置 Google 时报错。"""
        active_models: list[Any] = []
        active_ids: list[str] = []
        try:
            from ..api.v1.models import _build_active_models_response

            resp = await _build_active_models_response()
            active_models = list(resp.models or [])
            active_ids = [str(m.id) for m in active_models if getattr(m, "id", None)]
        except Exception as e:
            logger.debug(f"读取系统已启用模型列表失败，尝试数据库回退: {e}")

        active_id_set = set(active_ids)

        try:
            async with db_manager.get_connection() as conn:
                # 1. 优先使用工作区显式设置的模型偏好（需在当前已启用模型集合内，或无可用列表时兜底）
                async with conn.execute(
                    "SELECT model_preference FROM workspaces WHERE id = ?;",
                    (workspace_id,),
                ) as cursor:
                    ws_row = await cursor.fetchone()
                    ws_pref = str(ws_row["model_preference"]).strip() if (ws_row and ws_row["model_preference"]) else ""
                    if ws_pref and (not active_id_set or ws_pref in active_id_set):
                        return ws_pref

                # 2. 回退使用该工作区最近活跃会话正在使用的可用模型
                if active_id_set:
                    async with conn.execute(
                        "SELECT model_name FROM conversations WHERE workspace_id = ? AND model_name IS NOT NULL AND model_name != '' ORDER BY updated_at DESC LIMIT 1;",
                        (workspace_id,),
                    ) as cursor:
                        conv_row = await cursor.fetchone()
                        recent_model = str(conv_row["model_name"]).strip() if (conv_row and conv_row["model_name"]) else ""
                        if recent_model and recent_model in active_id_set:
                            return recent_model
        except Exception as e:
            logger.warning(f"读取工作区/会话模型偏好失败，回退系统可用默认模型: {e}")

        # 3. 回退到当前系统已启用模型列表中的默认项或首项
        for m in active_models:
            if getattr(m, "is_default", False) and getattr(m, "id", None):
                return str(m.id)
        if active_ids:
            return active_ids[0]
        return ""

    async def _execute_headless_agent(
        self,
        workspace_id: str,
        prompt: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """调用 AgentRuntime 在指定工作区下无头执行提示词（支持绑定专家配置）并返回完整输出与状态。"""
        from ..agent.runtime import agent_runtime

        model_name = await self._resolve_headless_model(workspace_id)
        system_override: str | None = None
        allowed_tools: list[str] | None = None
        allowed_write_dirs: list[str] | None = None

        if agent_id:
            try:
                from ..repository.agent_repository import AgentProfileRepository
                profile = await AgentProfileRepository.get_agent(agent_id)
                if profile:
                    cand_model = str(profile.get("base_model") or "").strip()
                    if cand_model and cand_model.lower() != "auto":
                        model_name = cand_model
                    parts: list[str] = []
                    if profile.get("context_prompt"):
                        parts.append(str(profile["context_prompt"]).strip())
                    elif profile.get("description"):
                        parts.append(str(profile["description"]).strip())
                    if profile.get("skill_ids"):
                        parts.append(f"优先调用已挂载技能: {', '.join(profile['skill_ids'])}")
                    if parts:
                        system_override = "\n\n".join(parts)
                    if profile.get("allowed_tools"):
                        allowed_tools = list(profile["allowed_tools"])
                    if profile.get("allowed_write_dirs"):
                        allowed_write_dirs = list(profile["allowed_write_dirs"])
            except Exception as prof_err:
                logger.warning(f"计划任务加载绑定专家 ({agent_id}) 失败，使用工作区默认配置: {prof_err}")

        output_chunks: list[str] = []
        error_messages: list[str] = []
        conversation_id: str | None = None

        async for event in agent_runtime.chat_stream(
            conversation_id=None,
            user_message=prompt,
            model_name=model_name,
            workspace_id=workspace_id,
            system_override=system_override,
            scene_mode="agent",
            execution_strategy="direct",
            clarify_mode=False,
            allowed_tools=allowed_tools,
            allowed_write_dirs=allowed_write_dirs,
        ):
            etype = event.get("type")
            if etype == "conv_init":
                conversation_id = event.get("conversation_id") or conversation_id
            elif etype == "stream_reset":
                output_chunks.clear()
            elif etype == "chunk":
                content = event.get("content")
                if content:
                    output_chunks.append(str(content))
            elif etype == "error":
                err = str(event.get("content") or event.get("message") or "智能体执行返回错误")
                error_messages.append(err)
            elif etype == "loop_failed":
                reason = str(event.get("reason") or event.get("message") or "自主循环执行失败")
                error_messages.append(reason)

        output_text = "".join(output_chunks).strip()
        if error_messages:
            return {
                "success": False,
                "output_text": output_text,
                "error_message": "\n".join(error_messages),
                "conversation_id": conversation_id,
            }

        return {
            "success": True,
            "output_text": output_text or "（任务已完成，模型未返回文本正文）",
            "error_message": None,
            "conversation_id": conversation_id,
        }

    async def run_task(self, task_id: str, trigger_type: str = "manual") -> dict[str, Any]:
        """真实无头执行单个计划任务，记录运行历史与完整状态。"""
        if task_id in self._running_tasks:
            return {"success": False, "message": "任务正在执行中，请勿重复触发"}

        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?;", (task_id,)) as cursor:
                task = await cursor.fetchone()
                if not task:
                    return {"success": False, "message": "任务不存在"}

        original_status = task["status"] if task["status"] in ("active", "paused") else "active"
        workspace_id = task["workspace_id"]
        task_name = task["name"]
        prompt = task["prompt_template"]
        cron_expr = task["cron_expr"]
        bound_agent_id = task.get("agent_id") if isinstance(task, dict) else (task["agent_id"] if "agent_id" in task.keys() else None)

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        started_dt = datetime.now()
        started_at = started_dt.isoformat()

        self._running_tasks.add(task_id)
        logger.info(f"开始执行计划任务 [{task_name}] (ID: {task_id}, trigger: {trigger_type})")

        try:
            async with db_manager.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO scheduled_task_runs (
                        id, task_id, workspace_id, trigger_type, status, started_at
                    ) VALUES (?, ?, ?, ?, 'running', ?);
                    """,
                    (run_id, task_id, workspace_id, trigger_type, started_at),
                )
                await conn.execute(
                    "UPDATE scheduled_tasks SET status = 'running' WHERE id = ?;",
                    (task_id,),
                )
                await conn.commit()

            if str(prompt or "").startswith("__SYS_KB_SYNC__:"):
                kb_target = str(prompt).split(":", 1)[1].strip()
                mount_id = kb_target[3:] if kb_target.startswith("kb_") else kb_target
                from ..api.v1.knowledge import run_incremental_sync

                sync_res = await run_incremental_sync(mount_id=mount_id, source_tag="auto")
                final_status = "success" if sync_res.get("success", True) else "failed"
                output_text = str(sync_res.get("message") or "知识库增量同步巡检完成")
                error_message = None if final_status == "success" else output_text
                conversation_id = None
            else:
                exec_result = await self._execute_headless_agent(workspace_id, prompt, agent_id=bound_agent_id)
                final_status = "success" if exec_result.get("success") else "failed"
                output_text = exec_result.get("output_text") or ""
                error_message = exec_result.get("error_message")
                conversation_id = exec_result.get("conversation_id")

        except Exception as exc:
            final_status = "failed"
            output_text = ""
            conversation_id = None
            tb_tail = traceback.format_exc(limit=3).strip()
            error_message = f"{type(exc).__name__}: {exc}\n{tb_tail}" if tb_tail else f"{type(exc).__name__}: {exc}"
            logger.error(f"计划任务 [{task_name}] 执行异常: {exc}")

        finally:
            self._running_tasks.discard(task_id)

        finished_dt = datetime.now()
        finished_at = finished_dt.isoformat()
        duration_ms = max(0, int((finished_dt - started_dt).total_seconds() * 1000))
        next_run_at = compute_next_run_iso(cron_expr, finished_dt) if original_status == "active" else None
        last_output_value = output_text if final_status == "success" else (output_text or error_message or "")

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE scheduled_task_runs
                SET status = ?, finished_at = ?, duration_ms = ?, output_text = ?, error_message = ?, conversation_id = ?
                WHERE id = ?;
                """,
                (final_status, finished_at, duration_ms, output_text, error_message, conversation_id, run_id),
            )
            await conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = ?, last_run_at = ?, next_run_at = COALESCE(?, next_run_at),
                    last_status = ?, last_output = ?, last_duration_ms = ?, last_error = ?, last_conversation_id = ?
                WHERE id = ?;
                """,
                (
                    original_status,
                    finished_at,
                    next_run_at,
                    final_status,
                    last_output_value,
                    duration_ms,
                    error_message,
                    conversation_id,
                    task_id,
                ),
            )
            await conn.commit()

        if final_status == "success":
            toast_msg = (output_text[:120] + "...") if len(output_text) > 120 else output_text
            send_windows_toast(title=f"AgentHub 任务完成: {task_name}", message=toast_msg)
        else:
            err_preview = (error_message or "执行失败")[:120]
            send_windows_toast(title=f"AgentHub 任务失败: {task_name}", message=err_preview)

        return {
            "success": final_status == "success",
            "run_id": run_id,
            "status": final_status,
            "duration_ms": duration_ms,
            "output": output_text,
            "error": error_message,
            "conversation_id": conversation_id,
            "message": error_message if final_status == "failed" else "执行成功",
        }

    async def tick_once(self, now: datetime | None = None) -> list[str]:
        """执行单次调度巡检：刷新 next_run_at 并将到点任务异步派发执行。"""
        current_dt = (now or datetime.now()).replace(second=0, microsecond=0)
        minute_key = current_dt.strftime("%Y-%m-%dT%H:%M")
        dispatched_ids: list[str] = []

        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM scheduled_tasks WHERE status = 'active';"
            ) as cursor:
                active_tasks = await cursor.fetchall()

            for task in active_tasks:
                task_id = task["id"]
                cron_expr = task["cron_expr"]
                try:
                    cron = CronExpression.parse(cron_expr)
                except ValueError as e:
                    logger.warning(f"跳过非法 Cron 表达式任务 {task_id} ({cron_expr}): {e}")
                    continue

                next_dt = cron.next_run_after(current_dt)
                next_iso = next_dt.isoformat() if next_dt else None

                # 若 next_run_at 为空或已过时，持久化刷新
                existing_next = task["next_run_at"]
                should_trigger = cron.matches(current_dt)
                already_triggered = (
                    task["last_triggered_minute"] == minute_key
                    or self._dispatched_minutes.get(task_id) == minute_key
                    or task_id in self._running_tasks
                )

                if should_trigger and not already_triggered:
                    self._dispatched_minutes[task_id] = minute_key
                    await conn.execute(
                        """
                        UPDATE scheduled_tasks
                        SET last_triggered_minute = ?, next_run_at = ?
                        WHERE id = ?;
                        """,
                        (minute_key, next_iso, task_id),
                    )
                    bg = asyncio.create_task(self.run_task(task_id, trigger_type="cron"))
                    self._bg_tasks.add(bg)
                    bg.add_done_callback(self._bg_tasks.discard)
                    dispatched_ids.append(task_id)
                elif not existing_next or existing_next < current_dt.isoformat():
                    await conn.execute(
                        "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?;",
                        (next_iso, task_id),
                    )

            await conn.commit()

        return dispatched_ids

    async def start_loop(self) -> None:
        """启动后台巡检轮询器（每 15 秒检查一次，防止跨分钟漂移漏触发）。"""
        if self._running:
            return
        self._running = True
        logger.info("计划任务后台守护线程已启动")

        try:
            while self._running:
                try:
                    await self.tick_once()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"调度巡检异常: {e}")
                await asyncio.sleep(15)
        finally:
            self._running = False
