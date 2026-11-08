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

        # 2. 扫描 L2 工作区私有技能 (<workspace>/.agenthub/skills/)
        if self.workspace_root and self.workspace_root.exists():
            l2_dir = self.workspace_root / ".agenthub" / "skills"
            if l2_dir.exists() and l2_dir.is_dir():
                for folder in l2_dir.iterdir():
                    if folder.is_dir() and not folder.name.startswith("."):
                        skill_file = folder / "SKILL.md"
                        if skill_file.exists():
                            parsed = _parse_skill_md(skill_file)
                            skill_name = parsed.get("name", folder.name)
                            is_overriding = skill_name in skills_map

                            skills_map[skill_name] = {
                                "id": f"l2_{skill_name}",
                                "name": skill_name,
                                "command": parsed.get("command", f"/{skill_name}"),
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
        获取指定 Skill 的 SOP 明文与详细元数据
        """
        all_skills = self.list_all_skills()
        for sk in all_skills:
            if sk["name"].lower() == skill_name.lower():
                return sk
        return None

    def save_workspace_skill(
        self,
        skill_name: str,
        command: str,
        description: str,
        sop_content: str,
    ) -> dict[str, Any]:
        """
        在当前工作区物理路径下持久化保存一个 L2 专属 Skill (<workspace>/.agenthub/skills/<name>/SKILL.md)
        """
        if not self.workspace_root:
            raise ValueError("未指定工作区物理路径，无法创建工作区专属技能")

        clean_name = re.sub(r"[^\w\-]", "", skill_name.strip().lower())
        if not clean_name:
            raise ValueError("技能名称无效")

        target_dir = self.workspace_root / ".agenthub" / "skills" / clean_name
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

