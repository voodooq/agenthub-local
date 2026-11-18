"""
原生无头浏览器观察与交互智能体 (Browser Agent)
核心功能：
1. 懒加载 Playwright Chromium 实例，5 分钟闲置自动销毁释放内存；
2. 提取低 Token 消耗的精简无障碍树快照 (Accessibility Tree Snapshot)；
3. 实时捕获页面控制台报错日志与网络状态；
4. 暴露 browser_inspect_page 与 browser_interact_action 原生工具。
"""
import asyncio
from dataclasses import dataclass, field
import json
import re
import time
from typing import Any
from urllib.parse import urlparse

from ..core.logging_config import logger

try:
    from playwright.async_api import async_playwright, Browser, Page, Playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


class BrowserSessionManager:
    """无头浏览器单例会话管理器（带 300 秒闲置自动回收）。"""

    IDLE_TIMEOUT_SECONDS = 300.0

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._last_active: float = time.time()
        self._console_logs: list[str] = []
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None

    async def _ensure_browser(self) -> Any:
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("未安装 playwright，无法启动无头浏览器")

        self._last_active = time.time()
        if self._page is not None and not self._page.is_closed():
            return self._page

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        if self._browser is None:
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )

        context = await self._browser.new_context(viewport={"width": 1280, "height": 800})
        self._page = await context.new_page()

        # 监听控制台日志
        self._console_logs.clear()
        self._page.on("console", lambda msg: self._console_logs.append(f"[{msg.type}] {msg.text}"))
        self._page.on("pageerror", lambda err: self._console_logs.append(f"[error] {str(err)}"))

        # 启动闲置检测定时器
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._idle_reaper())

        return self._page

    async def _idle_reaper(self) -> None:
        while True:
            await asyncio.sleep(30.0)
            if time.time() - self._last_active > self.IDLE_TIMEOUT_SECONDS:
                await self.close()
                break

    async def close(self) -> None:
        async with self._lock:
            try:
                if self._page and not self._page.is_closed():
                    await self._page.close()
                if self._browser:
                    await self._browser.close()
                if self._playwright:
                    await self._playwright.stop()
            except Exception as e:
                logger.debug(f"关闭浏览器会话: {e}")
            finally:
                self._page = None
                self._browser = None
                self._playwright = None

    async def inspect_page(self, url: str, wait_seconds: float = 2.0) -> dict[str, Any]:
        """访问页面并提取无障碍树快照与控制台日志。"""
        async with self._lock:
            page = await self._ensure_browser()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                if wait_seconds > 0:
                    await asyncio.sleep(min(wait_seconds, 10.0))

                title = await page.title()
                # 获取无障碍快照
                snapshot = await page.accessibility.snapshot()

                # 精简快照
                compact_tree = self._compress_ax_node(snapshot) if snapshot else {}
                logs = list(self._console_logs)

                return {
                    "url": page.url,
                    "title": title,
                    "accessibility_tree": compact_tree,
                    "console_logs": logs[-20:],
                }
            except Exception as e:
                return {
                    "url": url,
                    "error": f"访问页面失败: {str(e)}",
                    "console_logs": list(self._console_logs)[-20:],
                }

    def _compress_ax_node(self, node: dict[str, Any] | None) -> dict[str, Any] | None:
        if not node:
            return None
        role = node.get("role", "")
        name = node.get("name", "").strip()
        value = node.get("value", "")

        # 过滤无意义节点
        if role in ("none", "presentation") and not name:
            return None

        clean_node: dict[str, Any] = {"role": role}
        if name:
            clean_node["name"] = name[:80]
        if value:
            clean_node["value"] = str(value)[:80]

        children = []
        for ch in node.get("children", []):
            compressed_ch = self._compress_ax_node(ch)
            if compressed_ch:
                children.append(compressed_ch)
        if children:
            clean_node["children"] = children[:15]
        return clean_node


# 单例
browser_session_manager = BrowserSessionManager()


async def browser_inspect_page(url: str, wait_seconds: float = 2.0) -> str:
    """无头浏览器页面验收检查工具。"""
    res = await browser_session_manager.inspect_page(url, wait_seconds=wait_seconds)
    if "error" in res:
        return f"页面访问异常: {res['error']}"

    lines = [
        f"### 🌐 网页渲染快照: [{res.get('title')}]({res.get('url')})\n",
        "#### 🌲 无障碍结构大纲:",
        "```json",
        json.dumps(res.get("accessibility_tree", {}), ensure_ascii=False, indent=2)[:3000],
        "```",
    ]
    logs = res.get("console_logs", [])
    if logs:
        lines.append("\n#### 🖥️ 控制台日志:")
        for l in logs[-10:]:
            lines.append(f"- {l}")
    else:
        lines.append("\n控制台未检出任何报错日志。")

    return "\n".join(lines)
