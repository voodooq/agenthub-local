"""
Skill 版本快照留存与一键回滚服务。

职责：
1. 在任意写入/覆盖 SKILL.md 的路径前，将当前版本自动快照到 `<skill_dir>/.versions/`；
2. 维护轻量索引 `<skill_dir>/.versions/manifest.json`，按时间倒序提供历史版本列表；
3. 支持按 `version_id` 读取历史快照原文与正文，并支持一键安全回滚（回滚前自动备份当前状态）。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.logging_config import logger

# 隐藏版本目录名，天然被四元组扫描与技能列表扫描跳过
VERSIONS_DIR_NAME = ".versions"
MANIFEST_FILE_NAME = "manifest.json"
MAX_SNAPSHOTS_PER_SKILL = 20

# 仅允许安全字符构成 version_id，防止路径穿越
_SAFE_VERSION_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,120}$")
_VERSION_LOCK = threading.RLock()


def _compute_sha256(text: str) -> str:
    """计算文本内容的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_skill_meta(raw_md: str, fallback_name: str) -> dict[str, str]:
    """解析 SKILL.md 的 Frontmatter 元数据（延迟导入避免模块循环依赖）。"""
    from ..extensions.skill_loader import SkillLoader

    return SkillLoader._parse_frontmatter(raw_md, fallback_name)


def _read_manifest(versions_dir: Path) -> list[dict[str, Any]]:
    """读取版本清单列表，若损坏或不存在则返回空列表。"""
    manifest_path = versions_dir / MANIFEST_FILE_NAME
    if not manifest_path.is_file():
        return []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict) and item.get("version_id")]
    except Exception as exc:
        logger.warning(f"[SkillVersionService] 读取版本清单失败 {manifest_path}: {exc}")
    return []


def _write_manifest(versions_dir: Path, entries: list[dict[str, Any]]) -> None:
    """原子写入版本清单 manifest.json。"""
    versions_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = versions_dir / MANIFEST_FILE_NAME
    tmp_path = versions_dir / f".{MANIFEST_FILE_NAME}.{uuid.uuid4().hex[:6]}.tmp"
    tmp_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(manifest_path)


def _validate_version_id(version_id: str) -> str:
    """校验并规范化 version_id，拦截非法路径字符。"""
    clean_id = (version_id or "").strip()
    if not clean_id or ".." in clean_id or "/" in clean_id or "\\" in clean_id:
        raise ValueError("非法的版本标识")
    if not _SAFE_VERSION_ID_RE.match(clean_id):
        raise ValueError("版本标识包含非法字符")
    return clean_id


def snapshot_skill_before_write(
    skill_dir: Path,
    source: str = "manual",
) -> dict[str, Any] | None:
    """在覆盖或更新 SKILL.md 之前，自动保存当前磁盘版本的历史快照。

    Args:
        skill_dir: 技能根目录路径。
        source: 触发快照的来源标识（如 write_local_file / replace_in_file / api_save / distill / rollback_backup）。

    Returns:
        新创建或复用的最新快照记录；若当前不存在有效 SKILL.md 则返回 None。
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None

    try:
        raw_text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning(f"[SkillVersionService] 读取待快照文件失败 {skill_md}: {exc}")
        return None

    if not raw_text.strip():
        return None

    content_hash = _compute_sha256(raw_text)
    meta = _parse_skill_meta(raw_text, skill_dir.name)
    ver_str = meta.get("version") or "1.0.0"
    desc_str = (meta.get("description") or "").strip()
    size_bytes = len(raw_text.encode("utf-8"))

    versions_dir = skill_dir / VERSIONS_DIR_NAME
    with _VERSION_LOCK:
        entries = _read_manifest(versions_dir)
        # 若当前磁盘内容与最新快照完全一致且文件仍存在，避免重复生成冗余快照
        if entries:
            latest = entries[0]
            latest_file = versions_dir / f"{latest['version_id']}.md"
            if latest.get("sha256") == content_hash and latest_file.is_file():
                return dict(latest)

        now_dt = datetime.now(timezone.utc)
        ts_compact = now_dt.strftime("%Y%m%dT%H%M%SZ")
        safe_ver = re.sub(r"[^a-zA-Z0-9_.-]+", "_", ver_str).strip("_") or "1.0.0"
        version_id = f"v{safe_ver}_{ts_compact}_{uuid.uuid4().hex[:6]}"

        versions_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = versions_dir / f"{version_id}.md"
        snapshot_path.write_text(raw_text, encoding="utf-8")

        record: dict[str, Any] = {
            "version_id": version_id,
            "version": ver_str,
            "created_at": now_dt.isoformat(),
            "source": source,
            "description": desc_str,
            "size_bytes": size_bytes,
            "sha256": content_hash,
        }
        entries.insert(0, record)

        # 超出保留上限时清理最旧的历史快照文件
        if len(entries) > MAX_SNAPSHOTS_PER_SKILL:
            obsolete = entries[MAX_SNAPSHOTS_PER_SKILL:]
            entries = entries[:MAX_SNAPSHOTS_PER_SKILL]
            for old_item in obsolete:
                old_id = str(old_item.get("version_id") or "")
                if old_id and _SAFE_VERSION_ID_RE.match(old_id):
                    (versions_dir / f"{old_id}.md").unlink(missing_ok=True)

        _write_manifest(versions_dir, entries)
        return dict(record)


def list_skill_versions(skill_dir: Path) -> dict[str, Any]:
    """列出指定技能目录下的历史版本快照（按时间倒序）。

    Args:
        skill_dir: 技能根目录路径。

    Returns:
        包含 skill_id、current_version 与 versions 列表的字典。
    """
    skill_md = skill_dir / "SKILL.md"
    current_version = "1.0.0"
    current_sha256 = ""
    if skill_md.is_file():
        try:
            raw_cur = skill_md.read_text(encoding="utf-8", errors="replace")
            current_sha256 = _compute_sha256(raw_cur)
            meta = _parse_skill_meta(raw_cur, skill_dir.name)
            current_version = meta.get("version") or "1.0.0"
        except OSError:
            pass

    versions_dir = skill_dir / VERSIONS_DIR_NAME
    with _VERSION_LOCK:
        raw_entries = _read_manifest(versions_dir)

    valid_versions: list[dict[str, Any]] = []
    for item in raw_entries:
        vid = str(item.get("version_id") or "").strip()
        if not vid or not _SAFE_VERSION_ID_RE.match(vid):
            continue
        snap_file = versions_dir / f"{vid}.md"
        if not snap_file.is_file():
            continue
        size_val = int(item.get("size_bytes") or snap_file.stat().st_size)
        valid_versions.append({
            "version_id": vid,
            "version": str(item.get("version") or "1.0.0"),
            "created_at": str(item.get("created_at") or ""),
            "source": str(item.get("source") or "manual"),
            "description": str(item.get("description") or ""),
            "size_bytes": size_val,
            "size": size_val,
            "is_current": bool(current_sha256 and item.get("sha256") == current_sha256),
        })

    return {
        "skill_id": skill_dir.name,
        "current_version": current_version,
        "versions": valid_versions,
    }


def get_skill_version_detail(skill_dir: Path, version_id: str) -> dict[str, Any] | None:
    """获取指定历史版本快照的完整内容与解析后正文。

    Args:
        skill_dir: 技能根目录路径。
        version_id: 目标版本快照标识。

    Returns:
        包含原文 content、body、description、version 等字段的字典；未找到则返回 None。
    """
    clean_vid = _validate_version_id(version_id)
    versions_dir = skill_dir / VERSIONS_DIR_NAME
    snap_file = (versions_dir / f"{clean_vid}.md").resolve()
    if snap_file.parent != versions_dir.resolve() or not snap_file.is_file():
        return None

    raw_text = snap_file.read_text(encoding="utf-8", errors="replace")
    meta = _parse_skill_meta(raw_text, skill_dir.name)
    size_val = len(raw_text.encode("utf-8"))

    manifest_entry: dict[str, Any] = {}
    with _VERSION_LOCK:
        for item in _read_manifest(versions_dir):
            if item.get("version_id") == clean_vid:
                manifest_entry = item
                break

    return {
        "skill_id": skill_dir.name,
        "version_id": clean_vid,
        "version": str(manifest_entry.get("version") or meta.get("version") or "1.0.0"),
        "created_at": str(manifest_entry.get("created_at") or ""),
        "source": str(manifest_entry.get("source") or "manual"),
        "description": meta.get("description") or str(manifest_entry.get("description") or ""),
        "size_bytes": size_val,
        "size": size_val,
        "content": raw_text,
        "body": meta.get("body") or "",
    }


def rollback_skill_version(skill_dir: Path, version_id: str) -> dict[str, Any]:
    """将技能的 SKILL.md 回滚到指定的历史快照版本，并强制失效进程内缓存。

    Args:
        skill_dir: 技能根目录路径。
        version_id: 目标历史版本快照标识。

    Returns:
        回滚结果字典，包含 rolled_back_to 与最新解析元数据。
    """
    clean_vid = _validate_version_id(version_id)
    versions_dir = skill_dir / VERSIONS_DIR_NAME
    snap_file = (versions_dir / f"{clean_vid}.md").resolve()
    if snap_file.parent != versions_dir.resolve() or not snap_file.is_file():
        raise FileNotFoundError(f"未找到指定历史版本: {clean_vid}")

    target_content = snap_file.read_text(encoding="utf-8", errors="replace")
    if not target_content.strip():
        raise ValueError(f"历史版本 {clean_vid} 内容为空，无法回滚")

    # 回滚前自动为当前磁盘版本留存备份，保证回滚动作本身可逆
    snapshot_skill_before_write(skill_dir, source="rollback_backup")

    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(target_content, encoding="utf-8")

    from ..extensions.skill_loader import skill_loader
    from .skill_service import invalidate_skill_file_cache

    normalized_path = skill_loader.normalize_skill_file(skill_md)
    skill_loader.invalidate_cache()
    invalidate_skill_file_cache(normalized_path)

    final_raw = normalized_path.read_text(encoding="utf-8", errors="replace")
    meta = _parse_skill_meta(final_raw, skill_dir.name)
    return {
        "rolled_back_to": clean_vid,
        "version": meta.get("version") or "1.0.0",
        "description": meta.get("description") or "",
        "content": final_raw,
        "body": meta.get("body") or "",
        "path": str(normalized_path.resolve()),
    }
