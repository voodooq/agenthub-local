"""AgentHub Local - Git Shadow 快照与无痕秒级回滚 (Git Checkpoint)

利用 Git 底层影子索引 (GIT_INDEX_FILE)，在执行物理写盘前生成当前文件系统的秒级快照 Tree Hash。
绝不污染用户真实的分支分支指针、暂存区 (.git/index) 或产生无用 Commit。
支持用户前端一键无损撤销修改。
"""

import os
import subprocess
from pathlib import Path
from typing import Optional


def create_shadow_snapshot(workspace_dir: Path) -> Optional[str]:
    """在执行物理写盘前生成当前工作区的无痕快照。

    Args:
        workspace_dir: 工作区物理根目录。

    Returns:
        成功返回 40 位 Git Tree Hash，若非 Git 仓库或执行异常返回 None。
    """
    git_dir = workspace_dir / ".git"
    if not git_dir.exists():
        return None

    # 使用专属的影子索引文件，与用户正在工作的 .git/index 彻底物理隔离
    shadow_index = git_dir / "agenthub_shadow.index"
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(shadow_index)

    try:
        # 将工作区当前真实物理状态同步至影子暂存区
        add_res = subprocess.run(
            ["git", "add", "-A"],
            cwd=workspace_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if add_res.returncode != 0:
            return None

        # 从影子暂存区生成不可变的树对象 (Tree Object)
        write_res = subprocess.run(
            ["git", "write-tree"],
            cwd=workspace_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if write_res.returncode == 0:
            tree_hash = write_res.stdout.strip()
            return tree_hash if len(tree_hash) == 40 else None
        return None
    except Exception:
        return None


def restore_shadow_snapshot(
    workspace_dir: Path, tree_hash: str, target_file: Optional[str] = None
) -> bool:
    """一键将单文件或整个工作区恢复到指定 Tree Hash 状态。

    Args:
        workspace_dir: 工作区根目录。
        tree_hash: 快照的 40 位 Tree Hash。
        target_file: 指定恢复的文件相对路径，若为 None 则恢复整个工作区。

    Returns:
        是否恢复成功。
    """
    if not (workspace_dir / ".git").exists() or not tree_hash:
        return False

    cmd = ["git", "checkout", tree_hash, "--", target_file or "."]
    try:
        res = subprocess.run(
            cmd,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return res.returncode == 0
    except Exception:
        return False


def is_file_in_tree_snapshot(
    workspace_dir: Path, tree_hash: str, target_file: str
) -> bool:
    """检查指定文件在 Tree Hash 快照中是否存在。"""
    if not (workspace_dir / ".git").exists() or not tree_hash or not target_file:
        return False
    try:
        res = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", tree_hash, "--", target_file],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return res.returncode == 0 and bool(res.stdout.strip())
    except Exception:
        return False

