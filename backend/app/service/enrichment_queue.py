"""
后台索引与百科增强统一限流调度队列 (Background Enrichment Queue)
核心特性：
1. 将 HyPE 口语提炼、RAPTOR 分层摘要树与 Wiki 后台编译收口至同一限流队列；
2. 并发槽位限制 (max_concurrency <= 2)，按事件循环懒初始化 Semaphore 避免跨 Loop 冲突；
3. 前台绝对优先：复用 ForegroundChatTracker，检测到前台活跃对话时主动退避休眠 2.0s。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ..core.logging_config import logger
from ..search.hype_worker import ForegroundChatTracker


class BackgroundEnrichmentQueue:
    """统一后台增强限流调度队列。"""

    MAX_CONCURRENCY = 2
    _sems: dict[int, asyncio.Semaphore] = {}
    _active_tasks: set[str] = set()

    @classmethod
    def _get_sem(cls) -> asyncio.Semaphore:
        """获取当前事件循环绑定的限流信号量。"""
        loop_id = id(asyncio.get_running_loop())
        sem = cls._sems.get(loop_id)
        if sem is None:
            sem = asyncio.Semaphore(cls.MAX_CONCURRENCY)
            cls._sems[loop_id] = sem
        return sem

    @classmethod
    async def wait_for_foreground_idle(
        cls,
        poll_interval: float = 2.0,
        max_wait_rounds: int | None = None,
    ) -> int:
        """若存在活跃前台对话则持续退避休眠，返回实际退避轮次。"""
        waited_rounds = 0
        while ForegroundChatTracker.is_active():
            if max_wait_rounds is not None and waited_rounds >= max_wait_rounds:
                break
            waited_rounds += 1
            logger.debug(
                f"[EnrichmentQueue] 检测到前台活跃对话，后台增强任务让路休眠 {poll_interval}s (round={waited_rounds})"
            )
            await asyncio.sleep(poll_interval)
        return waited_rounds

    @classmethod
    async def run_with_limit(
        cls,
        task_key: str,
        coro_factory: Callable[[], Awaitable[Any]],
        poll_interval: float = 2.0,
    ) -> Any:
        """在限流信号量与前台优先退避保护下执行指定异步任务。"""
        await cls.wait_for_foreground_idle(poll_interval=poll_interval)
        async with cls._get_sem():
            await cls.wait_for_foreground_idle(poll_interval=poll_interval)
            cls._active_tasks.add(task_key)
            try:
                return await coro_factory()
            finally:
                cls._active_tasks.discard(task_key)

    @classmethod
    def enqueue(
        cls,
        task_key: str,
        coro_factory: Callable[[], Awaitable[Any]],
        poll_interval: float = 2.0,
    ) -> bool:
        """投递异步后台任务（同 key 去重），返回是否成功入队。"""
        if task_key in cls._active_tasks:
            return False

        async def _runner() -> None:
            cls._active_tasks.add(task_key)
            try:
                await cls.wait_for_foreground_idle(poll_interval=poll_interval)
                async with cls._get_sem():
                    await cls.wait_for_foreground_idle(poll_interval=poll_interval)
                    await coro_factory()
            except Exception as err:
                logger.error(f"[EnrichmentQueue] 后台任务 [{task_key}] 执行异常: {err}")
            finally:
                cls._active_tasks.discard(task_key)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_runner())
            return True
        except RuntimeError:
            return False

    @classmethod
    def get_queue_status(cls, kb_id: str | None = None) -> str:
        """返回指定知识库或全局的队列运行状态 ('running' | 'idle')。"""
        if not kb_id:
            return "running" if cls._active_tasks else "idle"
        has_kb_task = any(f":{kb_id}:" in key or key.endswith(f":{kb_id}") for key in cls._active_tasks)
        return "running" if has_kb_task else "idle"

