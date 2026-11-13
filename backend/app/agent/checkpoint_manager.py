"""双回滚时光机管理器 (Shadow Git + SQLite Checkpoints)

通过 `<workspace>/.agenthub/shadow_git` 维护完全独立于用户原生 `.git` 的影子仓库，
结合 SQLite `session_checkpoints` 表实现物理文件、草稿纸、消息历史与行为账本的原子回滚。
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.repository.db import db_manager

logger = logging.getLogger("agenthub.checkpoint")

# 默认强豁免清单：严禁影子仓库扫描或 `git clean` 误删用户依赖、数据库、附件与工作区内部状态
_SHADOW_EXCLUDES: tuple[str, ...] = (
    ".git/",
    ".agenthub/",
    "attachments/",
    ".agenthub/attachments/",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    "dist/",
    "build/",
    "data/",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    "*.sqlite",
    "*.sqlite3",
    "*.pyc",
    "*.log",
)

# `git clean` 显式附加排除参数，保证即使工作区缺少 .gitignore 也不会误删核心数据目录
_CLEAN_EXCLUDE_ARGS: list[str] = [
    arg
    for pattern in (
        ".git/",
        ".agenthub/",
        "attachments/",
        ".agenthub/attachments/",
        "node_modules/",
        ".venv/",
        "venv/",
        "__pycache__/",
        "dist/",
        "build/",
        "data/",
        "*.db",
        "*.db-wal",
        "*.db-shm",
        "*.sqlite",
        "*.sqlite3",
    )
    for arg in ("-e", pattern)
]


class ShadowCheckpointManager:
    """工作区代码与会话状态的双回滚时光机。

    工程安全保证：
    1. 按规范化工作区路径维护独立的 `asyncio.Lock`，避免并发工具执行引爆 Git index.lock；
    2. 全程运行在 `asyncio.to_thread` 中，绝不阻塞 SSE 事件循环；
    3. 所有写入 `messages` 的系统还原锚点统一使用 `role='model'`，满足 Gemini 对话交替约束。
    """

    _workspace_locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def _get_workspace_lock(cls, workspace_root: str) -> asyncio.Lock:
        """获取指定工作区目录专属的异步互斥锁。"""
        norm_key = str(Path(workspace_root).resolve()).lower()
        lock = cls._workspace_locks.get(norm_key)
        if lock is None:
            lock = asyncio.Lock()
            cls._workspace_locks[norm_key] = lock
        return lock

    @staticmethod
    def _shadow_git_dir(workspace_root: Path) -> Path:
        return workspace_root / ".agenthub" / "shadow_git"

    @staticmethod
    def _scratchpad_path(workspace_root: Path) -> Path:
        return workspace_root / ".agenthub" / "scratchpad.md"

    @staticmethod
    def _cleanup_stale_index_lock(shadow_git_dir: Path, force: bool = False) -> None:
        """安全清理影子仓库残留的僵尸 index.lock（如进程异常终止导致）。"""
        lock_file = shadow_git_dir / "index.lock"
        if not lock_file.exists():
            return
        try:
            age_seconds = time.time() - lock_file.stat().st_mtime
            if force or age_seconds > 3.0:
                lock_file.unlink(missing_ok=True)
                logger.warning("已清理影子 Git 僵尸锁文件: %s (force=%s)", lock_file, force)
        except OSError as exc:
            logger.warning("清理影子 Git index.lock 失败: %s", exc)

    @classmethod
    def _run_git_sync(
        cls,
        workspace_root: Path,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """在独立影子仓库上下文中同步执行 Git 子命令（供 asyncio.to_thread 调用）。"""
        shadow_git_dir = cls._shadow_git_dir(workspace_root)
        cls._cleanup_stale_index_lock(shadow_git_dir, force=False)

        cmd = [
            "git",
            f"--git-dir={shadow_git_dir}",
            f"--work-tree={workspace_root}",
            *args,
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            cmd,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=creationflags,
        )

        # 若仍因上次瞬间崩溃残留 index.lock 冲突，强制清理后重试一次
        if result.returncode != 0 and "index.lock" in (result.stderr or ""):
            cls._cleanup_stale_index_lock(shadow_git_dir, force=True)
            result = subprocess.run(
                cmd,
                cwd=str(workspace_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                creationflags=creationflags,
            )

        if check and result.returncode != 0:
            raise RuntimeError(
                f"Shadow Git 命令失败 ({' '.join(args)}): {(result.stderr or result.stdout).strip()}"
            )
        return result

    @classmethod
    def _ensure_shadow_repo_sync(cls, workspace_root: Path) -> None:
        """确保工作区影子仓库、忽略规则及局部 Git 身份已就绪。"""
        workspace_root.mkdir(parents=True, exist_ok=True)
        shadow_git_dir = cls._shadow_git_dir(workspace_root)
        shadow_git_dir.parent.mkdir(parents=True, exist_ok=True)

        if not (shadow_git_dir / "HEAD").exists():
            # NOTE: --separate-git-dir 或 --bare/init 组合下，直接用 GIT_DIR init 最简洁且零污染工作区
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            init_res = subprocess.run(
                ["git", "init", "--bare", str(shadow_git_dir)],
                cwd=str(workspace_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                creationflags=creationflags,
            )
            if init_res.returncode != 0:
                raise RuntimeError(f"初始化 Shadow Git 失败: {init_res.stderr.strip()}")

            cls._run_git_sync(workspace_root, ["config", "core.bare", "false"])
            cls._run_git_sync(workspace_root, ["config", "user.name", "AgentHub Shadow"])
            cls._run_git_sync(workspace_root, ["config", "user.email", "shadow@agenthub.local"])
            cls._run_git_sync(workspace_root, ["config", "core.quotepath", "false"])
            cls._run_git_sync(workspace_root, ["config", "core.autocrlf", "false"])
            cls._run_git_sync(workspace_root, ["config", "gc.auto", "0"])

        # 持续校准 info/exclude（不修改用户自带的 .gitignore，零污染项目仓库）
        info_dir = shadow_git_dir / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        exclude_file = info_dir / "exclude"
        existing_lines: set[str] = set()
        if exclude_file.exists():
            existing_lines = {
                line.strip()
                for line in exclude_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            }
        missing_patterns = [p for p in _SHADOW_EXCLUDES if p not in existing_lines]
        if missing_patterns:
            current_content = exclude_file.read_text(encoding="utf-8", errors="replace") if exclude_file.exists() else ""
            prefix = "\n" if current_content and not current_content.endswith("\n") else ""
            exclude_file.write_text(
                current_content + prefix + "\n".join(missing_patterns) + "\n",
                encoding="utf-8",
            )

    @classmethod
    def _create_git_snapshot_sync(cls, workspace_root: Path, message: str) -> tuple[str, str]:
        """同步创建工作区 Git 快照并读取当前 scratchpad 文本。"""
        cls._ensure_shadow_repo_sync(workspace_root)
        cls._run_git_sync(workspace_root, ["add", "-A"])

        has_head = cls._run_git_sync(
            workspace_root, ["rev-parse", "--verify", "HEAD"], check=False
        ).returncode == 0
        status_out = cls._run_git_sync(workspace_root, ["status", "--porcelain"]).stdout.strip()

        if status_out:
            cls._run_git_sync(workspace_root, ["commit", "-m", message, "--no-verify"])
        elif not has_head:
            cls._run_git_sync(
                workspace_root,
                ["commit", "--allow-empty", "-m", "AgentHub shadow baseline", "--no-verify"],
            )

        head_hash = cls._run_git_sync(workspace_root, ["rev-parse", "HEAD"]).stdout.strip()
        sp_path = cls._scratchpad_path(workspace_root)
        scratchpad_snapshot = ""
        if sp_path.exists():
            scratchpad_snapshot = sp_path.read_text(encoding="utf-8", errors="replace")
        return head_hash, scratchpad_snapshot

    @classmethod
    def _restore_git_snapshot_sync(
        cls,
        workspace_root: Path,
        target_hash: str,
        scratchpad_snapshot: str,
    ) -> None:
        """同步把磁盘物理文件与草稿纸重置到目标快照。"""
        cls._ensure_shadow_repo_sync(workspace_root)
        cls._run_git_sync(workspace_root, ["reset", "--hard", target_hash])
        # 安全清理未跟踪新增脏文件：保留标准 .gitignore 并强制豁免数据库/附件/内部目录
        cls._run_git_sync(workspace_root, ["clean", "-fd", *_CLEAN_EXCLUDE_ARGS])

        sp_path = cls._scratchpad_path(workspace_root)
        if scratchpad_snapshot:
            sp_path.parent.mkdir(parents=True, exist_ok=True)
            sp_path.write_text(scratchpad_snapshot, encoding="utf-8")
        elif sp_path.exists():
            sp_path.write_text("", encoding="utf-8")

    @classmethod
    async def create_checkpoint(
        cls,
        *,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
        turn_id: int,
        note: str = "",
    ) -> dict[str, Any]:
        """为当前工作区与会话建立一次双态检查点（文件快照 + 草稿纸 + SQLite 水位）。"""
        ws_path = Path(workspace_root).resolve()
        clean_note = (note or "").strip()[:120] or f"turn_{turn_id}"
        commit_msg = f"ckpt turn={turn_id}: {clean_note}"

        lock = cls._get_workspace_lock(str(ws_path))
        async with lock:
            git_hash, scratchpad_snapshot = await asyncio.to_thread(
                cls._create_git_snapshot_sync,
                ws_path,
                commit_msg,
            )

        ckpt_id = f"ckpt_{uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc).isoformat()
        safe_turn = max(0, int(turn_id))

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO session_checkpoints (
                    ckpt_id, conversation_id, workspace_id, git_hash,
                    turn_id, scratchpad_snapshot, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ckpt_id,
                    conversation_id,
                    workspace_id or "default",
                    git_hash,
                    safe_turn,
                    scratchpad_snapshot,
                    clean_note,
                    now,
                ),
            )
            await conn.commit()

        return {
            "ckpt_id": ckpt_id,
            "conversation_id": conversation_id,
            "workspace_id": workspace_id or "default",
            "git_hash": git_hash,
            "turn_id": safe_turn,
            "note": clean_note,
            "created_at": now,
        }

    @classmethod
    async def list_checkpoints(
        cls,
        conversation_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """获取指定会话最近保存的检查点清单（按时间倒序）。"""
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT ckpt_id, conversation_id, workspace_id, git_hash, turn_id, note, created_at
                FROM session_checkpoints
                WHERE conversation_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    async def rollback(
        cls,
        *,
        conversation_id: str,
        workspace_root: str,
        ckpt_id: str | None = None,
    ) -> dict[str, Any]:
        """执行代码与会话状态的原子级双回滚时光机。

        还原链路：
        1. 从 `session_checkpoints` 定位目标检查点（默认取当前会话最新检查点）；
        2. 还原影子 Git 物理工作区文件（安全排除数据库与附件）并恢复 `.agenthub/scratchpad.md`；
        3. 截断目标 Turn 之后的 `messages`、`event_ledger`、`tool_output_refs`；
        4. 回退 `conversations.last_extracted_turn_id` 与 `collapse_anchor_turn_id` 防记忆错位；
        5. 写入 `role='model'` 的 `[System State Restored]` 锚点，保持对话角色交替合法。
        """
        ws_path = Path(workspace_root).resolve()

        async with db_manager.get_connection() as conn:
            if ckpt_id:
                cursor = await conn.execute(
                    """
                    SELECT * FROM session_checkpoints
                    WHERE conversation_id = ? AND ckpt_id = ?
                    LIMIT 1
                    """,
                    (conversation_id, ckpt_id.strip()),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM session_checkpoints
                    WHERE conversation_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 1
                    """,
                    (conversation_id,),
                )
            ckpt_row = await cursor.fetchone()

        if ckpt_row is None:
            return {
                "ok": False,
                "error": f"未找到可回滚的检查点（conversation={conversation_id}, ckpt_id={ckpt_id or 'latest'}）",
            }

        target_ckpt_id = str(ckpt_row["ckpt_id"])
        target_hash = str(ckpt_row["git_hash"])
        target_turn_id = int(ckpt_row["turn_id"] or 0)
        scratchpad_snapshot = str(ckpt_row["scratchpad_snapshot"] or "")
        target_note = str(ckpt_row["note"] or "")

        # 1. 恢复磁盘物理工作区与草稿纸（持有工作区异步锁）
        lock = cls._get_workspace_lock(str(ws_path))
        async with lock:
            await asyncio.to_thread(
                cls._restore_git_snapshot_sync,
                ws_path,
                target_hash,
                scratchpad_snapshot,
            )

        # 2. 在单事务中原子回退 SQLite 对话历史、事件账本与提取水位
        restore_msg = (
            f"[System State Restored] 工作区文件、草稿纸与记忆上下文已成功回滚至检查点 "
            f"`{target_ckpt_id}` (Turn {target_turn_id}, 备注: {target_note}, 提交: {target_hash[:7]})。"
            "请基于当前磁盘上的真实文件状态继续后续任务。"
        )

        async with db_manager.get_connection() as conn:
            del_msg_cur = await conn.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND turn_id > ?",
                (conversation_id, target_turn_id),
            )
            deleted_messages = del_msg_cur.rowcount or 0

            del_ledger_cur = await conn.execute(
                "DELETE FROM event_ledger WHERE conversation_id = ? AND turn_id > ?",
                (conversation_id, target_turn_id),
            )
            deleted_ledger = del_ledger_cur.rowcount or 0

            await conn.execute(
                "DELETE FROM tool_output_refs WHERE conversation_id = ? AND turn_id > ?",
                (conversation_id, target_turn_id),
            )

            # 同时清理目标时间点之后新建的后续检查点，防止时间线分叉错乱
            await conn.execute(
                "DELETE FROM session_checkpoints WHERE conversation_id = ? AND turn_id > ?",
                (conversation_id, target_turn_id),
            )

            # 回退增量记忆提炼水位，避免下次提取时误判已消费 Turn
            await conn.execute(
                """
                UPDATE conversations
                SET last_extracted_turn_id = MIN(COALESCE(last_extracted_turn_id, 0), ?),
                    collapse_anchor_turn_id = MIN(COALESCE(collapse_anchor_turn_id, 0), ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (target_turn_id, target_turn_id, conversation_id),
            )

            # 写入系统还原锚点（NOTE: 严格使用 role='model'，对齐 engine_pool Gemini 交替校验）
            await conn.execute(
                """
                INSERT INTO messages (conversation_id, turn_id, role, content, token_count)
                VALUES (?, ?, 'model', ?, 32)
                """,
                (conversation_id, target_turn_id, restore_msg),
            )
            await conn.commit()

        logger.info(
            "[ShadowCheckpoint] 会话 %s 已回滚至 %s (turn=%d, hash=%s, deleted_msgs=%d, deleted_ledger=%d)",
            conversation_id,
            target_ckpt_id,
            target_turn_id,
            target_hash[:7],
            deleted_messages,
            deleted_ledger,
        )
        return {
            "ok": True,
            "ckpt_id": target_ckpt_id,
            "git_hash": target_hash,
            "target_turn_id": target_turn_id,
            "note": target_note,
            "deleted_messages": deleted_messages,
            "deleted_ledger": deleted_ledger,
            "restore_message": restore_msg,
        }

    @classmethod
    def _revert_files_from_shadow_sync(
        cls,
        workspace_root: Path,
        target_hash: str,
        file_paths: list[str],
    ) -> dict[str, Any]:
        """同步从影子 Git 指定 commit 恢复一组文件，若为新增文件则安全删除。"""
        cls._ensure_shadow_repo_sync(workspace_root)
        reverted_files: list[str] = []
        deleted_new_files: list[str] = []
        failed_files: list[str] = []

        if not file_paths:
            cls._run_git_sync(workspace_root, ["checkout", target_hash, "--", "."])
            return {
                "ok": True,
                "reverted_files": ["."],
                "deleted_new_files": [],
                "failed_files": [],
                "git_hash": target_hash,
            }

        ws_resolved = workspace_root.resolve()
        for raw_fp in file_paths:
            clean_fp = str(raw_fp or "").strip()
            if not clean_fp:
                continue
            cand = Path(clean_fp)
            if not cand.is_absolute():
                cand = (ws_resolved / clean_fp).resolve()
            else:
                cand = cand.resolve()

            try:
                rel_posix = cand.relative_to(ws_resolved).as_posix()
            except ValueError:
                failed_files.append(clean_fp)
                continue

            ls_res = cls._run_git_sync(
                workspace_root,
                ["ls-tree", "-r", "--name-only", target_hash, "--", rel_posix],
                check=False,
            )
            exists_in_snapshot = ls_res.returncode == 0 and bool((ls_res.stdout or "").strip())

            if exists_in_snapshot:
                co_res = cls._run_git_sync(
                    workspace_root,
                    ["checkout", target_hash, "--", rel_posix],
                    check=False,
                )
                if co_res.returncode == 0:
                    reverted_files.append(rel_posix)
                else:
                    failed_files.append(rel_posix)
            else:
                # NOTE: 该文件不在检查点快照中，说明属于本轮新建文件，撤销修改时将其安全移除
                try:
                    if cand.exists() and cand.is_file():
                        cand.unlink()
                    deleted_new_files.append(rel_posix)
                except OSError:
                    failed_files.append(rel_posix)

        return {
            "ok": len(failed_files) == 0 and (len(reverted_files) > 0 or len(deleted_new_files) > 0),
            "reverted_files": reverted_files,
            "deleted_new_files": deleted_new_files,
            "failed_files": failed_files,
            "git_hash": target_hash,
        }

    @classmethod
    async def revert_files_from_shadow(
        cls,
        workspace_root: str,
        *,
        conversation_id: str | None = None,
        file_paths: list[str] | None = None,
        ckpt_id: str | None = None,
        turn_id: int | None = None,
    ) -> dict[str, Any]:
        """在不回退会话消息历史的前提下，利用影子仓库对指定文件列表执行局部撤销修改。"""
        ws_path = Path(workspace_root).resolve()
        shadow_git_dir = cls._shadow_git_dir(ws_path)
        if not shadow_git_dir.exists():
            return {"ok": False, "error": "工作区尚无影子仓库快照"}

        target_hash: str | None = None
        async with db_manager.get_connection() as conn:
            if ckpt_id:
                cur = await conn.execute(
                    "SELECT git_hash FROM session_checkpoints WHERE ckpt_id = ? LIMIT 1",
                    (ckpt_id.strip(),),
                )
                row = await cur.fetchone()
                if row:
                    target_hash = str(row["git_hash"])
            elif conversation_id and turn_id is not None:
                # 取该轮次之前（turn_id < current_turn_id）最近的一个检查点，保证撤销后回到本轮开始前状态
                cur = await conn.execute(
                    """
                    SELECT git_hash FROM session_checkpoints
                    WHERE conversation_id = ? AND turn_id < ?
                    ORDER BY turn_id DESC, rowid DESC
                    LIMIT 1
                    """,
                    (conversation_id, int(turn_id)),
                )
                row = await cur.fetchone()
                if not row:
                    cur = await conn.execute(
                        """
                        SELECT git_hash FROM session_checkpoints
                        WHERE conversation_id = ?
                        ORDER BY created_at DESC, rowid DESC
                        LIMIT 1
                        """,
                        (conversation_id,),
                    )
                    row = await cur.fetchone()
                if row:
                    target_hash = str(row["git_hash"])
            elif conversation_id:
                cur = await conn.execute(
                    """
                    SELECT git_hash FROM session_checkpoints
                    WHERE conversation_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 1
                    """,
                    (conversation_id,),
                )
                row = await cur.fetchone()
                if row:
                    target_hash = str(row["git_hash"])

        if not target_hash:
            head_res = await asyncio.to_thread(
                cls._run_git_sync, ws_path, ["rev-parse", "--verify", "HEAD"], check=False
            )
            if head_res.returncode == 0 and (head_res.stdout or "").strip():
                target_hash = head_res.stdout.strip()

        if not target_hash:
            return {"ok": False, "error": "未找到可用于撤销修改的影子快照"}

        lock = cls._get_workspace_lock(str(ws_path))
        async with lock:
            return await asyncio.to_thread(
                cls._revert_files_from_shadow_sync,
                ws_path,
                target_hash,
                file_paths or [],
            )

