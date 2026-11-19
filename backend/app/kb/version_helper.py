"""
知识库语义化版本管理与自愈工具
实现：
1. 语义化版本号解析、格式化与自动递增计算（Semantic Versioning）；
2. 历史重复快照版本平滑自愈（自动修复相同 v1.0.0 的脏数据）。
"""
import re
from pathlib import Path
from typing import Any
import aiosqlite

from ..core.logging_config import logger


def parse_semver(v: str | None) -> tuple[int, int, int]:
    """解析语义化版本号字符串，如 'v1.0.2' -> (1, 0, 2)；若无法匹配则返回 (1, 0, 0)。"""
    if not v:
        return (1, 0, 0)
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(v))
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m2 = re.search(r"(\d+)\.(\d+)", str(v))
    if m2:
        return (int(m2.group(1)), int(m2.group(2)), 0)
    m3 = re.search(r"(\d+)", str(v))
    if m3:
        return (int(m3.group(1)), 0, 0)
    return (1, 0, 0)


def format_semver(major: int, minor: int, patch: int) -> str:
    """格式化标准语义化版本号字符串，如 (1, 0, 1) -> 'v1.0.1'。"""
    return f"v{major}.{minor}.{patch}"


def get_next_version(existing_versions: list[str], bump: str = "patch") -> str:
    """根据已有版本列表，自动计算下一个严格递增且不重复的语义化版本号。

    Args:
        existing_versions: 已存在的版本号列表，例如 ['v1.0.0', 'v1.0.1']
        bump: 递增类型，可选 'patch'（默认递增末位）、'minor'（次版本递增）、'major'（主版本递增）

    Returns:
        str: 新版本号，如 'v1.0.1' 或 'v1.1.0'
    """
    valid_versions = [v for v in existing_versions if v and isinstance(v, str)]
    if not valid_versions:
        return "v1.0.0"

    parsed = [parse_semver(v) for v in valid_versions]
    # 按语义版本降序排序，找出当前最大版本
    parsed.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    max_major, max_minor, max_patch = parsed[0]

    existing_set = set(valid_versions)

    if bump == "major":
        target = (max_major + 1, 0, 0)
    elif bump == "minor":
        target = (max_major, max_minor + 1, 0)
    else:  # patch
        target = (max_major, max_minor, max_patch + 1)

    candidate = format_semver(*target)
    # 确保在已存在集合中绝对不重复
    while candidate in existing_set:
        target = (target[0], target[1], target[2] + 1)
        candidate = format_semver(*target)

    return candidate


async def heal_duplicate_snapshot_versions(conn: aiosqlite.Connection, kb_id: str | None = None) -> int:
    """自愈清洗历史快照中重复的版本号（例如多个相同 v1.0.0）。

    按创建时间升序排列，将重复的快照版本号重命名为递增的新版本号并更新数据库。
    """
    healed_count = 0
    query = "SELECT kb_id, snapshot_id, version_tag, created_at FROM kb_version_snapshots"
    params: tuple[Any, ...] = ()
    if kb_id:
        query += " WHERE kb_id = ?"
        params = (kb_id,)
    query += " ORDER BY created_at ASC, snapshot_id ASC"

    async with conn.execute(query, params) as cursor:
        rows = await cursor.fetchall()

    kb_snapshots_map: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        kid = r["kb_id"]
        kb_snapshots_map.setdefault(kid, []).append(dict(r))

    for kid, snaps in kb_snapshots_map.items():
        used_tags: set[str] = set()
        for idx, snap in enumerate(snaps):
            tag = snap.get("version_tag") or "v1.0.0"
            if tag in used_tags:
                # 冲突！计算下一个不重复的版本号
                new_tag = get_next_version(list(used_tags), bump="patch")
                await conn.execute(
                    "UPDATE kb_version_snapshots SET version_tag = ? WHERE snapshot_id = ?",
                    (new_tag, snap["snapshot_id"]),
                )
                logger.info(
                    f"[VersionHealer] 自动修复知识库 [{kid}] 重复快照版本: {snap['snapshot_id']} 从 {tag} -> {new_tag}"
                )
                used_tags.add(new_tag)
                healed_count += 1
            else:
                used_tags.add(tag)

    if healed_count > 0:
        await conn.commit()
    return healed_count
