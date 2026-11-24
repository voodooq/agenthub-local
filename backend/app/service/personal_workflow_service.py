"""
私人助理高阶自动化工作流服务（PersonalWorkflowService）。
职责：
1. 微信转发即收藏碎片收集箱（im_inbox_items：抓取网页正文、自动提炼摘要打标并入库检索）；
2. 远程代码优化 Git 安全闭环（检查状态 / 隔离分支或检查点 / 手机端精简 Diff / 一键安全回滚）；
3. 每日晨报与晚间复盘（融合今日天气 + 待办 Top 5 + 托管服务器健康状态）；
4. 服务器阈值巡检主动告警，以及超长回复的 24 小时临时鉴权分享阅读报告（im_shared_reports）。
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.logging_config import logger
from ..repository.db import db_manager


class PersonalWorkflowService:
    """碎片收藏箱、Git 代码安全工作流、每日简报与短链报告服务。"""

    @staticmethod
    async def ensure_tables(conn: Any) -> None:
        """幂等创建收藏箱与短链分享表。"""
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS im_shared_reports (
                token TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content_md TEXT NOT NULL,
                link_id TEXT NOT NULL DEFAULT '',
                sender_id TEXT NOT NULL DEFAULT '',
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS im_inbox_items (
                id TEXT PRIMARY KEY,
                sender_id TEXT NOT NULL DEFAULT 'default',
                link_id TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                content_md TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_im_inbox_owner ON im_inbox_items(sender_id, created_at DESC)"
        )
        await conn.commit()

    # ────────────────────────── 1. 微信碎片收藏箱 (Inbox) ──────────────────────────

    async def save_inbox_item(
        self,
        *,
        sender_id: str,
        link_id: str = "",
        url: str,
        user_note: str = "",
    ) -> dict[str, Any]:
        """抓取并提纯文章链接正文，生成摘要与标签并存入个人收藏箱。"""
        from ..extensions.web_tools import web_tools

        target_url = (url or "").strip()
        if not target_url:
            raise ValueError("请提供需要收藏的网页或文章链接")

        fetch_res = await web_tools.fetch_url(target_url, extract_main_text=True, timeout=18.0)
        body_md = str(fetch_res.get("content") or "").strip()
        status_code = int(fetch_res.get("status_code") or 500)

        title = target_url
        first_line = body_md.splitlines()[0].strip() if body_md else ""
        if first_line.startswith("#"):
            title = first_line.lstrip("#").strip()[:120] or target_url
        elif status_code >= 400:
            title = f"链接收藏 ({target_url[:60]})"

        clean_plain = re.sub(r"^#+\s*", "", body_md, flags=re.MULTILINE)
        clean_plain = re.sub(r"\s+", " ", clean_plain).strip()
        summary = clean_plain[:260] + ("..." if len(clean_plain) > 260 else "")
        if user_note.strip():
            summary = f"备注：{user_note.strip()} | {summary}"

        tags: list[str] = []
        low_all = f"{title} {target_url} {clean_plain[:500]}".lower()
        for kw, tag_label in (
            ("nginx", "Nginx"),
            ("docker", "Docker"),
            ("python", "Python"),
            ("fastapi", "FastAPI"),
            ("linux", "Linux"),
            ("react", "React"),
            ("ai", "AI"),
            ("github", "GitHub"),
            ("redis", "Redis"),
            ("sql", "数据库"),
        ):
            if kw in low_all and tag_label not in tags:
                tags.append(tag_label)
        if not tags:
            tags.append("网页收藏")

        item_id = f"inb_{uuid.uuid4().hex[:10]}"
        now_ts = time.time()
        owner = (sender_id or "default").strip() or "default"

        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            await conn.execute(
                """
                INSERT INTO im_inbox_items (
                    id, sender_id, link_id, source_url, title, summary, content_md, tags_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    owner,
                    link_id or "",
                    str(fetch_res.get("url") or target_url),
                    title,
                    summary or "(暂无摘要)",
                    body_md[:15000],
                    json.dumps(tags, ensure_ascii=False),
                    now_ts,
                ),
            )
            await conn.commit()

        # 顺手沉淀一份至长期记忆以便自然语言随口追问命中
        try:
            from ..memory.long_term import LongTermMemoryManager

            date_s = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d")
            await LongTermMemoryManager.add_memory(
                category="inbox_article",
                content=f"[收藏 {date_s}] {title} ({target_url})：{summary}",
                confidence=0.9,
                source="im_inbox",
                is_pinned=False,
            )
        except Exception:
            pass

        return {
            "id": item_id,
            "title": title,
            "source_url": str(fetch_res.get("url") or target_url),
            "summary": summary,
            "tags": tags,
            "status_code": status_code,
            "created_at_str": datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M"),
        }

    async def search_inbox_items(
        self,
        *,
        sender_id: str,
        keyword: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """检索用户的碎片收藏箱记录。"""
        owner = (sender_id or "default").strip() or "default"
        safe_lim = max(1, min(30, int(limit or 10)))
        sql = "SELECT id, source_url, title, summary, tags_json, created_at FROM im_inbox_items WHERE sender_id = ?"
        params: list[Any] = [owner]
        if keyword.strip():
            kw = f"%{keyword.strip()}%"
            sql += " AND (title LIKE ? OR summary LIKE ? OR content_md LIKE ? OR tags_json LIKE ?)"
            params.extend([kw, kw, kw, kw])
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(safe_lim)

        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            cur = await conn.execute(sql, tuple(params))
            rows = await cur.fetchall()

        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags_json") or "[]")
            except Exception:
                d["tags"] = []
            ts = float(d.get("created_at") or time.time())
            d["created_at_str"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            out.append(d)
        return out

    # ────────────────────────── 2. Git 代码优化安全工作流 ──────────────────────────

    @staticmethod
    def _run_git(workdir: str, args: list[str]) -> tuple[int, str, str]:
        p = Path(workdir)
        if not p.exists():
            return -1, "", f"目录不存在: {workdir}"
        proc = subprocess.run(
            ["git", *args],
            cwd=str(p),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()

    async def git_safe_ops(
        self,
        *,
        action: str,
        workdir: str,
        branch_label: str = "im-opt",
    ) -> dict[str, Any]:
        """执行代码优化前隔离分支检查点、手机端精简 Diff 或一键安全回滚。"""
        act = (action or "diff").strip().lower()
        target_dir = (workdir or "").strip() or str(Path.cwd())

        rc, top_dir, err = await asyncio.to_thread(self._run_git, target_dir, ["rev-parse", "--show-toplevel"])
        if rc != 0:
            return {"success": False, "error": f"目录 {target_dir} 不是有效 Git 仓库: {err}"}

        _, cur_branch, _ = await asyncio.to_thread(self._run_git, top_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
        _, st_short, _ = await asyncio.to_thread(self._run_git, top_dir, ["status", "--short"])

        if act == "checkpoint":
            if cur_branch in ("main", "master"):
                safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", branch_label).strip("-") or "im-opt"
                new_br = f"{safe_label}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                rc_b, _, err_b = await asyncio.to_thread(self._run_git, top_dir, ["checkout", "-b", new_br])
                if rc_b != 0:
                    return {"success": False, "error": f"创建安全分支失败: {err_b}"}
                return {
                    "success": True,
                    "branch": new_br,
                    "previous_branch": cur_branch,
                    "message": f"已在安全隔离分支 `{new_br}` 就绪（原主分支 `{cur_branch}` 受保护），未提交改动状态：{st_short or '干净'}",
                }
            return {
                "success": True,
                "branch": cur_branch,
                "message": f"当前已在特性分支 `{cur_branch}`，工作区状态：\n{st_short or '干净'}",
            }

        if act == "rollback":
            rc_r, _, err_r = await asyncio.to_thread(self._run_git, top_dir, ["checkout", "--", "."])
            if rc_r != 0:
                return {"success": False, "error": f"工作区回滚失败: {err_r}"}
            return {"success": True, "branch": cur_branch, "message": f"已将分支 `{cur_branch}` 下所有未提交的代码改动安全回滚还原。"}

        # 默认 diff：输出精简变更统计与文件清单
        _, diff_stat, _ = await asyncio.to_thread(self._run_git, top_dir, ["diff", "--stat"])
        return {
            "success": True,
            "branch": cur_branch,
            "status_short": st_short or "（工作区干净，暂无未提交改动）",
            "diff_stat": diff_stat or "（无 Diff 统计）",
        }

    # ────────────────────────── 3. 每日晨报 & 服务器主动告警巡检 ──────────────────────────

    async def build_daily_briefing(
        self,
        *,
        sender_id: str = "default",
        city: str = "北京",
    ) -> str:
        """聚合生成每日晨报/复盘（天气 + 待办 Top 5 + 托管服务器健康状态）。"""
        from ..extensions.web_tools import web_tools
        from .personal_assistant_service import personal_assistant_service
        from .server_ops_service import server_ops_service

        now_dt = datetime.now()
        w_res = await web_tools.get_weather(city=city or "北京", days=1)
        todos = await personal_assistant_service.list_notes(sender_id=sender_id, category="todo", status="active", limit=5)
        srv_res = await server_ops_service.inspect_server_health()

        weather_line = (
            str(w_res.get("summary_text") or "").splitlines()[0]
            if w_res.get("success")
            else f"{city}天气暂不可用"
        )

        if todos:
            todo_lines = [f"  {i}. {t['content']}" for i, t in enumerate(todos, 1)]
            todo_block = "\n".join(todo_lines)
        else:
            todo_block = "  暂无积压待办，今天节奏很轻松。"

        if srv_res.get("has_alert"):
            srv_block = f"⚠️ 服务器存在告警：\n{srv_res.get('summary')}"
        else:
            reports = srv_res.get("reports") or []
            srv_block = f"全部服务器运行正常（共检查 {len(reports)} 台）。"

        return (
            f"☀️ 每日简报（{now_dt.strftime('%Y-%m-%d %H:%M')}）\n\n"
            f"1. **天气**：{weather_line}\n\n"
            f"2. **待办（{len(todos)} 项）**：\n{todo_block}\n\n"
            f"3. **服务器**：{srv_block}"
        )

    async def run_server_health_watch(
        self,
        mem_warn: float = 90.0,
        disk_warn: float = 85.0,
    ) -> dict[str, Any]:
        """巡检所有托管服务器并在超阈值时构造告警消息。"""
        from .server_ops_service import server_ops_service

        res = await server_ops_service.inspect_server_health(mem_warn=mem_warn, disk_warn=disk_warn)
        if res.get("has_alert"):
            return {
                "has_alert": True,
                "summary": f"🚨 服务器异常告警（{datetime.now().strftime('%H:%M')}）：\n\n{res.get('summary')}",
            }
        return {"has_alert": False, "summary": "所有托管服务器指标正常"}

    # ────────────────────────── 4. 超长输出 24h 临时鉴权短链 ──────────────────────────

    async def create_shared_report(
        self,
        *,
        title: str,
        content_md: str,
        link_id: str = "",
        sender_id: str = "",
        ttl_hours: int = 24,
    ) -> dict[str, Any]:
        """为超长代码 Diff 或服务器报告创建 24 小时临时鉴权网页/Markdown 阅读链接。"""
        token = secrets.token_urlsafe(18)
        now_ts = time.time()
        exp_ts = now_ts + max(1, int(ttl_hours)) * 3600.0
        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            await conn.execute(
                """
                INSERT INTO im_shared_reports (token, title, content_md, link_id, sender_id, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (token, (title or "完整详报")[:120], content_md, link_id or "", sender_id or "", exp_ts, now_ts),
            )
            await conn.commit()
        return {
            "token": token,
            "path": f"/api/v1/personal-assistant/reports/{token}",
            "expires_at": exp_ts,
        }

    async def get_shared_report(self, token: str) -> dict[str, Any] | None:
        """读取未过期的临时报告。"""
        clean_tok = (token or "").strip()
        if not clean_tok:
            return None
        now_ts = time.time()
        async with db_manager.get_connection() as conn:
            await self.ensure_tables(conn)
            cur = await conn.execute(
                "SELECT token, title, content_md, expires_at, created_at FROM im_shared_reports WHERE token = ? LIMIT 1",
                (clean_tok,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        if float(d.get("expires_at") or 0) < now_ts:
            return None
        return d


personal_workflow_service = PersonalWorkflowService()

