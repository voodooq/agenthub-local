"""
渐进式 Skill 加载器 (SkillLoader)
平时仅向 System Prompt 暴露极简的 (name, description) 摘要（仅占数十 Token）；
当模型判定需调用某项技能时，通过 load_skill_detail 动态注入完整 SKILL.md，避免撑爆上下文窗口。
"""
import os
import re
from pathlib import Path
from typing import Any

from ..core.logging_config import logger


class SkillLoader:
    """本地 Skill 扫描与按需加载器"""

    def __init__(self, workspace_skills_dir: str | Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        self.skills_dir = Path(workspace_skills_dir) if workspace_skills_dir else (base_dir / "skills")
        self._ensure_default_skills()

    def _ensure_default_skills(self) -> None:
        """确保本地 skills 目录存在，并初始化内置标准示例 Skill"""
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            git_skill_dir = self.skills_dir / "git-commit-helper"
            if not (git_skill_dir / "SKILL.md").exists():
                git_skill_dir.mkdir(parents=True, exist_ok=True)
                (git_skill_dir / "SKILL.md").write_text(
                    "---\n"
                    "name: git-commit-helper\n"
                    "description: 遵循 Conventional Commits 规范审查 Git 改动并生成高质量中英文提交信息。\n"
                    "---\n\n"
                    "# Git 提交规范助手 (Git Commit Helper)\n\n"
                    "1. 先使用 `execute_local_command` 执行 `git status` 和 `git diff --stat` 查看变更范围；\n"
                    "2. 严格遵循 `<type>(<scope>): <subject>` 格式（如 `feat`, `fix`, `refactor`, `docs`）；\n"
                    "3. 严禁将 `.env`、`*.db`、`*.log` 或含个人密钥的文件纳入暂存区。\n",
                    encoding="utf-8",
                )

            doctor_skill_dir = self.skills_dir / "local-env-doctor"
            if not (doctor_skill_dir / "SKILL.md").exists():
                doctor_skill_dir.mkdir(parents=True, exist_ok=True)
                (doctor_skill_dir / "SKILL.md").write_text(
                    "---\n"
                    "name: local-env-doctor\n"
                    "description: 排查本机端口占用、僵尸进程、Python/Node 环境依赖冲突与磁盘空间告警。\n"
                    "---\n\n"
                    "# 本机研发环境诊断专家 (Local Env Doctor)\n\n"
                    "1. 排查端口占用时，先通过 `netstat -ano` 查找对应 PID，再通过 `tasklist` 核实进程名；\n"
                    "2. 发现目标 PID 与进程名称后，立即调用 `update_working_memory` 记录到任务草稿纸；\n"
                    "3. 若需终止进程，调用 `execute_local_command` 发起 `taskkill /PID <pid> /F`（由系统灰名单弹窗请用户审批）。\n",
                    encoding="utf-8",
                )
        except Exception as e:
            logger.warning(f"初始化默认 skills 目录失败: {e}")

    @staticmethod
    def _parse_frontmatter(md_text: str, fallback_name: str) -> dict[str, str]:
        """解析 SKILL.md 顶部的 YAML Frontmatter"""
        name = fallback_name
        description = "本地扩展专业技能包"
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", md_text, re.DOTALL)
        if match:
            yaml_block = match.group(1)
            for line in yaml_block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k, v = k.strip().lower(), v.strip().strip('"').strip("'")
                    if k == "name" and v:
                        name = v
                    elif k == "description" and v:
                        description = v
        return {"name": name, "description": description}

    def list_skills(self) -> list[dict[str, Any]]:
        """扫描并返回所有可用的本地 Skill 元信息列表"""
        results: list[dict[str, Any]] = []
        if not self.skills_dir.exists():
            return results

        for item in sorted(self.skills_dir.iterdir()):
            if item.is_dir():
                skill_md = item / "SKILL.md"
                if skill_md.exists():
                    try:
                        raw = skill_md.read_text(encoding="utf-8", errors="replace")
                        meta = self._parse_frontmatter(raw, item.name)
                        results.append({
                            "id": item.name,
                            "name": meta["name"],
                            "description": meta["description"],
                            "path": str(skill_md.resolve()),
                            "content": raw,
                            "is_builtin": item.name in ("git-commit-helper", "local-env-doctor"),
                        })
                    except Exception as e:
                        logger.warning(f"读取 Skill {item.name} 失败: {e}")
        return results

    @staticmethod
    def _sanitize_skill_id(raw_id: str) -> str:
        """将任意名称转化为安全的文件夹标识"""
        clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", (raw_id or "").strip().lower()).strip("-")
        return clean or "custom-skill"

    def save_skill(
        self,
        name: str,
        description: str,
        content: str,
        skill_id: str | None = None,
    ) -> dict[str, Any]:
        """创建或更新自定义本地 Skill (写入 skills/<id>/SKILL.md)"""
        safe_id = self._sanitize_skill_id(skill_id or name)
        target_dir = self.skills_dir / safe_id
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_md = target_dir / "SKILL.md"

        clean_body = (content or "").strip()
        if clean_body.startswith("---"):
            full_md = clean_body + "\n"
        else:
            full_md = (
                f"---\n"
                f"name: {name.strip() or safe_id}\n"
                f"description: {description.strip() or '自定义专业技能规程'}\n"
                f"---\n\n"
                f"{clean_body}\n"
            )
        skill_md.write_text(full_md, encoding="utf-8")
        meta = self._parse_frontmatter(full_md, safe_id)
        return {
            "id": safe_id,
            "name": meta["name"],
            "description": meta["description"],
            "path": str(skill_md.resolve()),
            "content": full_md,
        }

    def import_skill_markdown(self, raw_md: str, filename_hint: str = "") -> dict[str, Any]:
        """从导入的 SKILL.md 文本解析 Frontmatter 并自动落盘至 skills/ 目录"""
        fallback_slug = Path(filename_hint or "imported-skill").stem
        if fallback_slug.upper() == "SKILL":
            fallback_slug = "imported-skill"
        meta = self._parse_frontmatter(raw_md, "")
        # 若 Frontmatter 中未指定 name，则尝试提取首行 # 标题或使用文件名兜底
        if not meta["name"]:
            m_title = re.search(r"^#\s+(.+)$", raw_md, re.MULTILINE)
            if m_title:
                meta["name"] = m_title.group(1).strip()[:40]
            else:
                meta["name"] = fallback_slug
        return self.save_skill(
            name=meta["name"],
            description=meta["description"],
            content=raw_md,
            skill_id=meta["name"],
        )

    def delete_skill(self, skill_id: str) -> bool:
        """删除指定的本地 Skill 目录"""
        import shutil
        safe_id = os.path.basename((skill_id or "").strip())
        if not safe_id:
            return False
        target_dir = self.skills_dir / safe_id
        if not target_dir.exists() or not target_dir.is_dir():
            return False
        shutil.rmtree(target_dir, ignore_errors=True)
        return True

    def get_skills_summary_prompt(self) -> str:
        """生成极简的可用 Skill 列表摘要（仅数十 Token），供注入 System Prompt"""
        skills = self.list_skills()
        if not skills:
            return ""
        lines = ["### 已挂载的按需专业技能包 (Skills Summary)："]
        lines.append("当任务匹配以下技能时，请先调用 `load_skill_detail(skill_name)` 获取完整操作规程：")
        for s in skills:
            lines.append(f"- `{s['name']}`: {s['description']}")
        return "\n".join(lines)

    def get_full_skill_markdown(self, skill_name: str) -> str:
        """当模型调用 load_skill_detail 时，按需读取指定 Skill 的完整 Markdown 正文"""
        safe_name = os.path.basename((skill_name or "").strip())
        if not safe_name:
            return "Error: 未指定有效的 skill_name"

        target_md = self.skills_dir / safe_name / "SKILL.md"
        if not target_md.exists():
            # 尝试按 frontmatter name 查找
            for s in self.list_skills():
                if s["name"].lower() == safe_name.lower() or s["id"].lower() == safe_name.lower():
                    target_md = Path(s["path"])
                    break

        if not target_md.exists():
            return f"Error: 未找到名为 '{skill_name}' 的 Skill。"

        try:
            return target_md.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Error: 读取 Skill 失败: {e}"


skill_loader = SkillLoader()

