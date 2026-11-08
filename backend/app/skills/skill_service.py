"""
双层 Skill 沙盒与同名 Override 核心服务
- L1 全局底座技能：来自项目根目录 `skills/`
- L2 工作区私有技能：来自当前工作区物理路径 `<workspace>/.agenthub/skills/`
- 同名覆盖法则：L2 专属 Skill 高优覆盖同名 L1 Skill，并标记 is_override=True
"""
import re
from pathlib import Path
from typing import Any
from ..core.logging_config import logger


def _parse_skill_md(file_path: Path) -> dict[str, Any]:
    """
    解析 SKILL.md 中的 YAML Frontmatter 与 Markdown SOP 正文
    """
    if not file_path.exists():
        return {}

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"读取 Skill 文件失败 {file_path}: {e}")
        return {}

    meta = {
        "name": file_path.parent.name,
        "command": f"/{file_path.parent.name}",
        "description": "",
        "sop_content": content,
    }

    # 解析 --- frontmatter ---
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if match:
        fm_text = match.group(1)
        sop_body = match.group(2).strip()
        meta["sop_content"] = sop_body

        for line in fm_text.splitlines():
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip().lower()
                v = v.strip().strip("'\"")
                if k in ("name", "command", "description", "version"):
                    meta[k] = v

    return meta


class SkillService:
    """
    双层技能管理器
    """
    def __init__(self, workspace_root: str | None = None):
        self.project_root = Path.cwd().resolve()
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def list_all_skills(self) -> list[dict[str, Any]]:
        """
        获取当前工作区上下文下的全部生效技能（自动合并 L1 与 L2 并执行同名覆盖）
        """
        skills_map: dict[str, dict[str, Any]] = {}

        # 1. 扫描 L1 全局技能 (skills/)
        l1_dir = self.project_root / "skills"
        if l1_dir.exists() and l1_dir.is_dir():
            for folder in l1_dir.iterdir():
                if folder.is_dir() and not folder.name.startswith("."):
                    skill_file = folder / "SKILL.md"
                    if skill_file.exists():
                        parsed = _parse_skill_md(skill_file)
                        skill_name = parsed.get("name", folder.name)
                        skills_map[skill_name] = {
                            "id": f"l1_{skill_name}",
                            "name": skill_name,
                            "command": parsed.get("command", f"/{skill_name}"),
                            "description": parsed.get("description", ""),
                            "scope": "global",
                            "is_override": False,
                            "folder_path": str(folder),
                            "sop_content": parsed.get("sop_content", ""),
                        }

        # 2. 扫描 L2 工作区私有技能 (双轨扫描：优先 <workspace>/skills/，兼顾 <workspace>/.agenthub/skills/)
        if self.workspace_root and self.workspace_root.exists():
            l2_dirs = [
                self.workspace_root / "skills",
                self.workspace_root / ".agenthub" / "skills",
            ]
            for l2_dir in l2_dirs:
                if l2_dir.exists() and l2_dir.is_dir():
                    for folder in l2_dir.iterdir():
                        if folder.is_dir() and not folder.name.startswith("."):
                            skill_file = folder / "SKILL.md"
                            if skill_file.exists():
                                parsed = _parse_skill_md(skill_file)
                                skill_name = parsed.get("name", folder.name)
                                skill_cmd = parsed.get("command", f"/{skill_name}")

                                # 若 workspace/skills 已扫描入库，不被 .agenthub 历史旧副本覆写
                                if skill_name in skills_map and skills_map[skill_name]["scope"] == "workspace":
                                    continue

                                # 智能排除无效/残缺的历史占位技能：如果 SOP 仅包含向导未填写说明且有新技能，则略过
                                sop_raw = parsed.get("sop_content", "").strip()
                                if "将这段代码作为执行核心脚本说明粘贴进去" in sop_raw and len(sop_raw) < 80:
                                    logger.info(f"忽略未完成编辑的工作区残缺占位技能: {skill_name}")
                                    continue

                                is_overriding = skill_name in skills_map

                                skills_map[skill_name] = {
                                    "id": f"l2_{skill_name}",
                                    "name": skill_name,
                                    "command": skill_cmd,
                                    "description": parsed.get("description", ""),
                                    "scope": "workspace",
                                    "is_override": is_overriding,
                                    "folder_path": str(folder),
                                    "sop_content": parsed.get("sop_content", ""),
                                }

        # 排序：工作区专属置顶，同层按字母排序
        sorted_skills = sorted(
            skills_map.values(),
            key=lambda x: (0 if x["scope"] == "workspace" else 1, x["name"]),
        )
        return sorted_skills

    def get_skill_detail(self, skill_name: str) -> dict[str, Any] | None:
        """
        获取指定 Skill 的 SOP 明文与详细元数据（支持按 name 或 command 匹配）
        """
        all_skills = self.list_all_skills()
        norm = skill_name.strip().lower().lstrip("/")
        for sk in all_skills:
            if sk["name"].lower() == norm or sk["command"].lower().lstrip("/") == norm:
                return sk
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
                    shutil.rmtree(cdir, ignore_errors=True)
                    deleted = True
                    logger.info(f"成功清理工作区专属技能目录: {cdir}")
                except Exception as e:
                    logger.warning(f"删除工作区技能目录失败 {cdir}: {e}")

        # 2. 若直接匹配未命中，深度扫描目录内 SKILL.md 比对 command 或 name 字段
        if not deleted:
            l2_roots = [
                self.workspace_root / "skills",
                self.workspace_root / ".agenthub" / "skills",
            ]
            for l2_root in l2_roots:
                if l2_root.exists() and l2_root.is_dir():
                    for folder in l2_root.iterdir():
                        if folder.is_dir():
                            sk_file = folder / "SKILL.md"
                            if sk_file.exists():
                                parsed = _parse_skill_md(sk_file)
                                p_name = parsed.get("name", "").strip().lower()
                                p_cmd = parsed.get("command", "").strip().lower().lstrip("/")
                                if norm in (p_name, p_cmd):
                                    try:
                                        shutil.rmtree(folder, ignore_errors=True)
                                        deleted = True
                                        logger.info(f"成功按元数据深度匹配并清理技能目录: {folder}")
                                    except Exception as e:
                                        logger.warning(f"删除工作区技能目录失败 {folder}: {e}")

        return deleted

    def save_workspace_skill(
        self,
        skill_name: str,
        command: str,
        description: str,
        sop_content: str,
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

        file_body = (
            f"---\n"
            f"name: {clean_name}\n"
            f"command: {cmd}\n"
            f"description: {description.strip()}\n"
            f"---\n\n"
            f"{sop_content.strip()}\n"
        )
        skill_file.write_text(file_body, encoding="utf-8")
        logger.info(f"成功保存工作区专属技能: {skill_file}")

        return {
            "name": clean_name,
            "command": cmd,
            "description": description.strip(),
            "scope": "workspace",
            "file_path": str(skill_file),
        }

