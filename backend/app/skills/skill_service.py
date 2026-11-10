"""
双层 Skill 沙盒与同名 Override 核心服务
- L1 全局底座技能：来自项目根目录 `skills/`
- L2 工作区私有技能：来自当前工作区物理路径 `<workspace>/skills/` 或 `<workspace>/.agenthub/skills/`
- 同名覆盖法则：L2 专属 Skill 高优覆盖同名 L1 Skill，并标记 is_override=True
- 性能优化：列表扫描默认仅读取 Frontmatter 头部 (4KB) 并结合 (mtime_ns, size) 进程级缓存，详情再按需加载正文
"""
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any
from ..core.logging_config import logger
from ..core.runtime_paths import get_bundle_root, get_writable_app_root

# 仅解析摘要元数据时的最大读取字符数，避免长篇 SOP 全文读盘与正则开销
_HEADER_READ_CHARS = 4096
_CACHE_MAX_ENTRIES = 4096
_SNAPSHOT_TTL_SECONDS = 10.0
_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n(.*))?$", re.DOTALL)
_PLACEHOLDER_HINT = "将这段代码作为执行核心脚本说明粘贴进去"

_CACHE_LOCK = threading.RLock()
_CACHE_EPOCH = 0
_SKILL_HEADER_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_SKILL_FULL_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
# 列表级短窗口快照缓存: (l1_dir, workspace_root, include_content) -> (epoch, monotonic_ts, roots_mtime_sig, skills_list)
_LIST_SNAPSHOT_CACHE: dict[
    tuple[str, str, bool],
    tuple[int, float, tuple[int, ...], list[dict[str, Any]]],
] = {}


def _evict_cache_if_needed(cache: dict[str, Any]) -> None:
    """当进程内缓存超过上限时淘汰较早写入的一半条目，控制常驻内存"""
    if len(cache) >= _CACHE_MAX_ENTRIES:
        keys_to_drop = list(cache.keys())[: _CACHE_MAX_ENTRIES // 2]
        for k in keys_to_drop:
            cache.pop(k, None)


def invalidate_skill_file_cache(file_path: Path | str | None = None) -> None:
    """失效单个技能文件或全部技能缓存，并递增 epoch 清理列表级快照"""
    global _CACHE_EPOCH
    with _CACHE_LOCK:
        _CACHE_EPOCH += 1
        _LIST_SNAPSHOT_CACHE.clear()
        if file_path is None:
            _SKILL_HEADER_CACHE.clear()
            _SKILL_FULL_CACHE.clear()
            return
        key = str(Path(file_path).resolve())
        _SKILL_HEADER_CACHE.pop(key, None)
        _SKILL_FULL_CACHE.pop(key, None)


def _resolve_project_root() -> Path:
    """定位全局 L1 底座技能所属的项目根目录（优先持久化可写根与 CWD，不存在时回退只读 Bundle 与源码根）"""
    cwd = Path.cwd().resolve()
    writable_root = get_writable_app_root()
    if (writable_root / "skills").is_dir():
        return writable_root
    if (cwd / "skills").is_dir():
        return cwd
    bundle_root = get_bundle_root()
    if (bundle_root / "skills").is_dir():
        return bundle_root
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    if (repo_root / "skills").is_dir():
        return repo_root
    return writable_root


def _extract_frontmatter_fields(fm_text: str, default_name: str) -> dict[str, str]:
    """从 YAML Frontmatter 块提取核心展示字段"""
    parsed = {
        "name": default_name,
        "command": f"/{default_name}",
        "description": "",
        "version": "1.0.0",
    }
    for line in fm_text.splitlines():
        stripped = line.strip()
        if ":" not in stripped:
            continue
        k, v = stripped.split(":", 1)
        k = k.strip().lower()
        v = v.strip().strip("'\"")
        if k in ("name", "command", "description", "version", "updated_at") and v:
            if k == "version":
                from ..extensions.skill_loader import normalize_version_str
                parsed[k] = normalize_version_str(v)
            else:
                parsed[k] = v
    return parsed


def _parse_skill_md(
    file_path: Path,
    include_content: bool = True,
    file_stat: os.stat_result | None = None,
) -> dict[str, Any]:
    """
    解析 SKILL.md 中的 YAML Frontmatter 与（可选）Markdown SOP 正文。
    当 include_content=False 时仅读取文件头部并命中 (mtime_ns, size) 缓存。
    """
    try:
        st = file_stat or os.stat(file_path)
    except OSError as e:
        logger.warning(f"读取 Skill 文件状态失败 {file_path}: {e}")
        return {}

    cache_key = str(file_path.resolve())
    target_cache = _SKILL_FULL_CACHE if include_content else _SKILL_HEADER_CACHE
    with _CACHE_LOCK:
        cached = target_cache.get(cache_key)
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return dict(cached[2])

    try:
        if include_content:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        else:
            with file_path.open("r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read(_HEADER_READ_CHARS)
    except Exception as e:
        logger.warning(f"读取 Skill 文件失败 {file_path}: {e}")
        return {}

    folder_name = file_path.parent.name
    meta: dict[str, Any] = {
        "name": folder_name,
        "command": f"/{folder_name}",
        "description": "",
        "has_sop": st.st_size > 0,
    }

    match = _FRONTMATTER_RE.match(content)
    if match:
        fm_text = match.group(1)
        sop_body = (match.group(2) or "").strip()
        meta.update(_extract_frontmatter_fields(fm_text, folder_name))
    else:
        sop_body = content.strip()

    # 仅小型文件可能是历史遗留残缺占位模板，避免大文件产生额外扫描成本
    meta["_is_placeholder"] = (
        st.st_size <= 2048
        and _PLACEHOLDER_HINT in sop_body
        and len(sop_body) < 80
    )

    if include_content:
        meta["sop_content"] = sop_body

    with _CACHE_LOCK:
        _evict_cache_if_needed(target_cache)
        target_cache[cache_key] = (st.st_mtime_ns, st.st_size, dict(meta))

    return meta


class SkillService:
    """
    双层技能管理器
    """

    def __init__(self, workspace_root: str | None = None):
        self.project_root = _resolve_project_root()
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    @staticmethod
    def _build_skill_record(
        parsed: dict[str, Any],
        folder_path: str,
        scope: str,
        is_override: bool,
        include_content: bool,
    ) -> dict[str, Any]:
        skill_name = parsed["name"]
        prefix = "l2" if scope == "workspace" else "l1"
        playbook_file = Path(folder_path) / "knowledge" / "experience_playbook.md"
        has_playbook = playbook_file.is_file()
        record: dict[str, Any] = {
            "id": f"{prefix}_{skill_name}",
            "name": skill_name,
            "command": parsed.get("command", f"/{skill_name}"),
            "description": parsed.get("description", ""),
            "scope": scope,
            "is_override": is_override,
            "folder_path": folder_path,
            "has_sop": bool(parsed.get("has_sop", True)),
            "has_playbook": has_playbook,
            "version": parsed.get("version") or "1.0.0",
        }
        if parsed.get("updated_at"):
            record["updated_at"] = parsed["updated_at"]
        if include_content:
            sop_text = parsed.get("sop_content", "")
            if has_playbook:
                try:
                    pb_raw = playbook_file.read_text(encoding="utf-8", errors="replace").strip()
                    if pb_raw:
                        record["experience_highlights"] = pb_raw[-1800:]
                        sop_text = (
                            f"{sop_text}\n\n---\n\n### 💡 演进实战经验 (`knowledge/experience_playbook.md`)\n{pb_raw}"
                            if sop_text
                            else pb_raw
                        )
                except OSError:
                    pass
            record["sop_content"] = sop_text
        return record

    def _scan_root_skills(
        self,
        root_dir: Path,
        include_content: bool,
    ) -> list[tuple[str, dict[str, Any]]]:
        """使用 os.scandir 一次性获取目录项并解析有效技能文件"""
        if not root_dir.exists() or not root_dir.is_dir():
            return []

        items: list[tuple[str, dict[str, Any]]] = []
        try:
            with os.scandir(root_dir) as it:
                for entry in it:
                    if entry.name.startswith(".") or not entry.is_dir():
                        continue
                    skill_file = Path(entry.path) / "SKILL.md"
                    try:
                        st = os.stat(skill_file)
                    except OSError:
                        continue
                    parsed = _parse_skill_md(
                        skill_file,
                        include_content=include_content,
                        file_stat=st,
                    )
                    if parsed:
                        items.append((entry.path, parsed))
        except OSError as e:
            logger.warning(f"扫描技能目录失败 {root_dir}: {e}")
        return items

    @staticmethod
    def _get_roots_mtime_sig(dirs: list[Path]) -> tuple[int, ...]:
        """采集各技能根目录修改时间戳（微秒级），感知外部增删子目录变化"""
        sig: list[int] = []
        for d in dirs:
            try:
                sig.append(os.stat(d).st_mtime_ns)
            except OSError:
                sig.append(0)
        return tuple(sig)

    def list_all_skills(
        self,
        include_content: bool = False,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """
        获取当前工作区上下文下的全部生效技能（自动合并 L1 与 L2 并执行同名覆盖）。
        默认 include_content=False 仅返回轻量摘要字段；结合根目录 mtime + 短窗口列表快照实现 <1ms 响应。
        """
        l1_dir = self.project_root / "skills"
        ws_l2_dirs: list[Path] = []
        if self.workspace_root and self.workspace_root.exists():
            ws_l2_dirs = [
                self.workspace_root / "skills",
                self.workspace_root / ".agenthub" / "skills",
            ]

        snapshot_key = (str(l1_dir), str(self.workspace_root or ""), include_content)
        roots_sig = self._get_roots_mtime_sig([l1_dir, *ws_l2_dirs])
        now_ts = time.monotonic()

        with _CACHE_LOCK:
            scan_epoch = _CACHE_EPOCH
            if not force_refresh:
                snap = _LIST_SNAPSHOT_CACHE.get(snapshot_key)
                if (
                    snap
                    and snap[0] == scan_epoch
                    and (now_ts - snap[1]) < _SNAPSHOT_TTL_SECONDS
                    and snap[2] == roots_sig
                ):
                    return [dict(item) for item in snap[3]]

        skills_map: dict[str, dict[str, Any]] = {}

        # 1. 扫描 L1 全局技能 (skills/)
        for folder_path, parsed in self._scan_root_skills(l1_dir, include_content=include_content):
            skill_name = parsed["name"]
            skills_map[skill_name] = self._build_skill_record(
                parsed=parsed,
                folder_path=folder_path,
                scope="global",
                is_override=False,
                include_content=include_content,
            )

        # 2. 扫描 L2 工作区私有技能 (双轨扫描：优先 <workspace>/skills/，兼顾 <workspace>/.agenthub/skills/)
        for l2_dir in ws_l2_dirs:
            for folder_path, parsed in self._scan_root_skills(l2_dir, include_content=include_content):
                skill_name = parsed["name"]

                # 若 workspace/skills 已扫描入库，不被 .agenthub 历史旧副本覆写
                if skill_name in skills_map and skills_map[skill_name]["scope"] == "workspace":
                    continue

                # 跳过未编辑完整的残缺占位技能
                if parsed.get("_is_placeholder"):
                    logger.info(f"忽略未完成编辑的工作区残缺占位技能: {skill_name}")
                    continue

                is_overriding = skill_name in skills_map
                skills_map[skill_name] = self._build_skill_record(
                    parsed=parsed,
                    folder_path=folder_path,
                    scope="workspace",
                    is_override=is_overriding,
                    include_content=include_content,
                )

        # 排序：工作区专属置顶，同层按名称字母序
        sorted_skills = sorted(
            skills_map.values(),
            key=lambda x: (0 if x["scope"] == "workspace" else 1, x["name"]),
        )

        with _CACHE_LOCK:
            if scan_epoch == _CACHE_EPOCH:
                if len(_LIST_SNAPSHOT_CACHE) >= 256:
                    _LIST_SNAPSHOT_CACHE.clear()
                _LIST_SNAPSHOT_CACHE[snapshot_key] = (
                    scan_epoch,
                    time.monotonic(),
                    roots_sig,
                    [dict(item) for item in sorted_skills],
                )

        return sorted_skills

    def get_skill_detail(self, skill_name: str) -> dict[str, Any] | None:
        """
        获取指定 Skill 的 SOP 明文与详细元数据（优先按目录路径 O(1) 直达，未命中再通过轻量索引定位）。
        """
        raw_norm = (skill_name or "").strip().lstrip("/")
        if not raw_norm:
            return None

        # 安全基名，防止路径穿越
        safe_candidates = []
        for cand in (os.path.basename(raw_norm), os.path.basename(raw_norm.lower())):
            if cand and cand not in safe_candidates:
                safe_candidates.append(cand)

        l1_root = self.project_root / "skills"
        search_roots: list[tuple[Path, str]] = []
        if self.workspace_root and self.workspace_root.exists():
            search_roots.append((self.workspace_root / "skills", "workspace"))
            search_roots.append((self.workspace_root / ".agenthub" / "skills", "workspace"))
        search_roots.append((l1_root, "global"))

        for base_dir, scope in search_roots:
            if not base_dir.exists():
                continue
            for cand in safe_candidates:
                folder = base_dir / cand
                skill_file = folder / "SKILL.md"
                try:
                    st = os.stat(skill_file)
                except OSError:
                    continue
                parsed = _parse_skill_md(skill_file, include_content=True, file_stat=st)
                if not parsed or (scope == "workspace" and parsed.get("_is_placeholder")):
                    continue
                is_override = (
                    scope == "workspace"
                    and (l1_root / folder.name / "SKILL.md").exists()
                )
                return self._build_skill_record(
                    parsed=parsed,
                    folder_path=str(folder),
                    scope=scope,
                    is_override=is_override,
                    include_content=True,
                )

        # 目录名与命令名不同（如目录为 git-commit-helper，入参为 git），先从轻量缓存摘要定位真实路径
        norm_lower = raw_norm.lower()
        for sk in self.list_all_skills(include_content=False):
            if sk["name"].lower() == norm_lower or sk["command"].lower().lstrip("/") == norm_lower:
                folder_path = sk.get("folder_path")
                if folder_path:
                    skill_file = Path(folder_path) / "SKILL.md"
                    parsed = _parse_skill_md(skill_file, include_content=True)
                    if parsed:
                        return self._build_skill_record(
                            parsed=parsed,
                            folder_path=folder_path,
                            scope=sk["scope"],
                            is_override=bool(sk.get("is_override", False)),
                            include_content=True,
                        )
        return None

    def delete_workspace_skill(self, skill_name: str) -> bool:
        """
        删除工作区私有技能目录（彻底清除物理磁盘残留，支持按名称、指令或目录名匹配）
        """
        if not self.workspace_root or not self.workspace_root.exists():
            return False

        import shutil
        norm = re.sub(r"[^\w\-]", "", skill_name.strip().lower().lstrip("/"))
        deleted = False

        # 1. 尝试直接路径匹配
        candidate_dirs = [
            self.workspace_root / "skills" / norm,
            self.workspace_root / ".agenthub" / "skills" / norm,
        ]
        for cdir in candidate_dirs:
            if cdir.exists() and cdir.is_dir():
                try:
                    invalidate_skill_file_cache(cdir / "SKILL.md")
                    shutil.rmtree(cdir, ignore_errors=True)
                    deleted = True
                    logger.info(f"成功清理工作区专属技能目录: {cdir}")
                except Exception as e:
                    logger.warning(f"删除工作区技能目录失败 {cdir}: {e}")

        # 2. 若直接匹配未命中，扫描目录内 SKILL.md 头部比对 command 或 name 字段
        if not deleted:
            l2_roots = [
                self.workspace_root / "skills",
                self.workspace_root / ".agenthub" / "skills",
            ]
            for l2_root in l2_roots:
                if not l2_root.exists() or not l2_root.is_dir():
                    continue
                for folder_path, parsed in self._scan_root_skills(l2_root, include_content=False):
                    p_name = parsed.get("name", "").strip().lower()
                    p_cmd = parsed.get("command", "").strip().lower().lstrip("/")
                    if norm in (p_name, p_cmd):
                        try:
                            invalidate_skill_file_cache(Path(folder_path) / "SKILL.md")
                            shutil.rmtree(folder_path, ignore_errors=True)
                            deleted = True
                            logger.info(f"成功按元数据深度匹配并清理技能目录: {folder_path}")
                        except Exception as e:
                            logger.warning(f"删除工作区技能目录失败 {folder_path}: {e}")

        return deleted

    def save_workspace_skill(
        self,
        skill_name: str,
        command: str,
        description: str,
        sop_content: str,
        version: str = "1.0.0",
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        """
        在当前工作区物理路径下持久化保存一个 L2 专属 Skill
        """
        if not self.workspace_root:
            raise ValueError("未指定工作区物理路径，无法创建工作区专属技能")

        clean_name = re.sub(r"[^\w\-]", "", skill_name.strip().lower())
        if not clean_name:
            raise ValueError("技能名称无效")

        # 优先保存在工作区主流根目录 skills/
        target_dir = self.workspace_root / "skills" / clean_name
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_file = target_dir / "SKILL.md"

        cmd = command.strip()
        if not cmd.startswith("/"):
            cmd = f"/{cmd}"

        from ..extensions.skill_loader import normalize_version_str
        norm_ver = normalize_version_str(version)
        upd_line = f"updated_at: {updated_at.strip()}\n" if updated_at and updated_at.strip() else ""

        file_body = (
            f"---\n"
            f"name: {clean_name}\n"
            f"command: {cmd}\n"
            f"version: {norm_ver}\n"
            f"{upd_line}"
            f"description: {description.strip()}\n"
            f"---\n\n"
            f"{sop_content.strip()}\n"
        )
        skill_file.write_text(file_body, encoding="utf-8")
        invalidate_skill_file_cache(skill_file)
        logger.info(f"成功保存工作区专属技能: {skill_file}")

        return {
            "name": clean_name,
            "command": cmd,
            "version": norm_ver,
            "description": description.strip(),
            "scope": "workspace",
            "file_path": str(skill_file),
        }
