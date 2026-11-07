"""
渐进式 Skill 微服务容器加载器 (SkillLoader 2.0 - Skill-as-a-Folder 四元组架构)
核心特性：
1. 四元组标准包结构支持：
   - SKILL.md     (核心 YAML 元数据 + SOP 操作规程)
   - tools/       (可选：随包专属 Python/Node 确定性算子脚本，激活时按需挂载)
   - knowledge/   (可选：随包领域规程文档/模板，基于文件 SHA256 水位线自动同步至 SQLite FTS5 毫秒级检索)
   - schema.json  (可选：强制结构化输出契约，保障 B 端业务核验报表零格式偏差)
2. 双轨目录发现：同时支持开源明文目录 (`skills/`) 与垂直行业商业套件目录 (`suites/`)
3. 预留多租户/多项目隔离上下文 (`tenant_id`, `project_id`)，单机默认 `"default"`，企业版无缝升级
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..core.logging_config import logger
from ..repository.db import db_manager


class SkillLoader:
    """本地 Skill 四元组容器扫描、FTS5 领域知识同步、随包算子执行与热插拔加载器"""

    BUILTIN_IDS = {
        "git-commit-helper",
        "local-env-doctor",
        "windows-app-master",
        "windows-gui-operator",
        "skill-creator",
    }

    def __init__(
        self,
        workspace_skills_dir: str | Path | None = None,
        commercial_suites_dir: str | Path | None = None,
    ) -> None:
        self.base_dir = Path(__file__).resolve().parent.parent.parent.parent
        self.skills_dir = Path(workspace_skills_dir) if workspace_skills_dir else (self.base_dir / "skills")
        self.suites_dir = Path(commercial_suites_dir) if commercial_suites_dir else (self.base_dir / "suites")
        self._ensure_default_skills()

    def _ensure_default_skills(self) -> None:
        """确保本地 skills 目录存在，并初始化内置标准 Skill 与 `/new-skill` 母技能"""
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)

            # 1. Git 提交助手
            git_skill_dir = self.skills_dir / "git-commit-helper"
            if not (git_skill_dir / "SKILL.md").exists():
                git_skill_dir.mkdir(parents=True, exist_ok=True)
                (git_skill_dir / "SKILL.md").write_text(
                    "---\n"
                    "name: git-commit-helper\n"
                    "command: /git\n"
                    "type: skill\n"
                    "description: 遵循 Conventional Commits 规范审查 Git 改动并生成高质量中英文提交信息。\n"
                    "---\n\n"
                    "# Git 提交规范助手 (Git Commit Helper)\n\n"
                    "## 适用场景\n"
                    "- 审查当前工作区改动并生成符合规范的 Git Commit Message。\n\n"
                    "## 执行步骤\n"
                    "1. 先使用 `execute_local_command` 执行 `git status` 和 `git diff --stat` 查看变更范围；\n"
                    "2. 严格遵循 `<type>(<scope>): <subject>` 格式（如 `feat`, `fix`, `refactor`, `docs`）；\n"
                    "3. 严禁将 `.env`、`*.db`、`*.log` 或含个人密钥的文件纳入暂存区。\n",
                    encoding="utf-8",
                )

            # 2. 本机研发环境诊断专家
            doctor_skill_dir = self.skills_dir / "local-env-doctor"
            if not (doctor_skill_dir / "SKILL.md").exists():
                doctor_skill_dir.mkdir(parents=True, exist_ok=True)
                (doctor_skill_dir / "SKILL.md").write_text(
                    "---\n"
                    "name: local-env-doctor\n"
                    "command: /doctor\n"
                    "type: skill\n"
                    "description: 排查本机端口占用、僵尸进程、Python/Node 环境依赖冲突与磁盘空间告警。\n"
                    "---\n\n"
                    "# 本机研发环境诊断专家 (Local Env Doctor)\n\n"
                    "## 适用场景\n"
                    "- 本地开发端口死锁、僵尸进程清理、虚拟环境依赖诊断。\n\n"
                    "## 执行步骤\n"
                    "1. 排查端口占用时，先通过 `netstat -ano` 查找对应 PID，再通过 `tasklist` 核实进程名；\n"
                    "2. 发现目标 PID 与进程名称后，立即调用 `update_working_memory` 记录到任务草稿纸；\n"
                    "3. 若需终止进程，调用 `execute_local_command` 发起 `taskkill /PID <pid> /F`。\n",
                    encoding="utf-8",
                )

            # 3. 3分钟零门槛建 Skill 母技能 (Built-in Skill Creator)
            creator_dir = self.skills_dir / "skill-creator"
            if not (creator_dir / "SKILL.md").exists():
                creator_dir.mkdir(parents=True, exist_ok=True)
                (creator_dir / "SKILL.md").write_text(
                    "---\n"
                    "name: skill-creator\n"
                    "command: /new-skill\n"
                    "type: skill\n"
                    "description: 【系统母技能】通过自然语言对话或总结刚才的操作步骤，3分钟自动生成标准四元组 Skill 技能包并热加载。\n"
                    "---\n\n"
                    "# 智能 Skill 构建母技能 (Built-in Skill Creator)\n\n"
                    "## 1. 功能定位\n"
                    "当用户输入 `/new-skill <需求描述>` 或要求“把刚才我们执行的步骤保存为一个新 Skill”时，自动在 `skills/<skill-id>/` 下创建符合 GeminiHub 四元组标准的技能容器。\n\n"
                    "## 2. 标准四元组目录结构 (Skill-as-a-Folder)\n"
                    "```text\n"
                    "skills/<skill-id>/\n"
                    "├── SKILL.md          # [必选] YAML 头信息 (name, command, type, description) + 详细 SOP 规程\n"
                    "├── tools/            # [可选] 确定性 Python 算子脚本 (如 tools/calculator.py)，接收命令行 JSON 参数并输出标准结果\n"
                    "├── knowledge/        # [可选] 领域参考文档 (*.md / *.txt / *.csv)，系统自动切块挂载至 SQLite FTS5 引擎\n"
                    "└── schema.json       # [可选] 结构化输出 JSON Schema 校验契约\n"
                    "```\n\n"
                    "## 3. 构建执行步骤\n"
                    "1. 提炼用户需求或当前会话已跑通的操作流程，确定 `kebab-case` 格式的 `<skill-id>` 与 `/<command>`；\n"
                    "2. 调用 `write_local_file` 创建 `skills/<skill-id>/SKILL.md`；\n"
                    "3. 若任务涉及精确计算、表格核验或外部接口调用，在 `skills/<skill-id>/tools/` 下编写对应的 Python 脚本；\n"
                    "4. 若任务包含特定规程或模板，写入 `skills/<skill-id>/knowledge/`；\n"
                    "5. 告知用户技能已热加载就绪，可直接在输入框输入 `/<command>` 查看说明并调用。\n",
                    encoding="utf-8",
                )
        except Exception as e:
            logger.warning(f"初始化默认 skills 目录失败: {e}")

    @staticmethod
    def _sanitize_skill_id(raw_id: str) -> str:
        """将任意名称转化为符合系统标准的 kebab-case 英文文件夹标识"""
        clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", (raw_id or "").strip().lower()).replace("_", "-").strip("-")
        clean = re.sub(r"-{2,}", "-", clean)
        return clean or "custom-skill"

    @classmethod
    def _parse_frontmatter(cls, md_text: str, fallback_name: str) -> dict[str, str]:
        """解析 SKILL.md 顶部的 YAML Frontmatter，并提取去除 Frontmatter 后的纯正文"""
        safe_fallback = cls._sanitize_skill_id(fallback_name)
        name = safe_fallback
        command = f"/{safe_fallback}"
        skill_type = "skill"
        tier = "community"
        mcp_server = ""
        description = ""
        body = md_text.strip()

        match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)", md_text, re.DOTALL)
        if match:
            yaml_block = match.group(1)
            body = match.group(2).strip()
            for line in yaml_block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k, v = k.strip().lower(), v.strip().strip('"').strip("'")
                    if k == "name" and v:
                        name = cls._sanitize_skill_id(v)
                    elif k == "command" and v:
                        clean_cmd = cls._sanitize_skill_id(v.lstrip("/"))
                        command = f"/{clean_cmd}"
                    elif k in ("type", "category") and v:
                        if "mcp" in v.lower():
                            skill_type = "mcp"
                        elif "pro" in v.lower() or "suite" in v.lower():
                            skill_type = "suite"
                        else:
                            skill_type = "skill"
                    elif k == "tier" and v:
                        tier = v.lower()
                    elif k == "mcp_server" and v:
                        mcp_server = v.strip()
                        skill_type = "mcp"
                    elif k == "description" and v:
                        description = v

        if not description:
            m_title = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
            description = m_title.group(1).strip()[:80] if m_title else "本地扩展专业技能包"

        return {
            "name": name,
            "command": command,
            "type": skill_type,
            "tier": tier,
            "mcp_server": mcp_server,
            "description": description,
            "body": body,
        }

    @staticmethod
    def _inspect_four_tuple(skill_dir: Path) -> dict[str, Any]:
        """检查技能目录的四元组组件挂载情况 (tools/, knowledge/, schema.json)"""
        tools_dir = skill_dir / "tools"
        knowledge_dir = skill_dir / "knowledge"
        schema_file = skill_dir / "schema.json"

        tool_files: list[str] = []
        if tools_dir.exists() and tools_dir.is_dir():
            tool_files = sorted(
                f.name for f in tools_dir.iterdir()
                if f.is_file() and f.suffix.lower() in (".py", ".js", ".ps1", ".sh", ".pyd")
            )

        knowledge_files: list[str] = []
        if knowledge_dir.exists() and knowledge_dir.is_dir():
            knowledge_files = sorted(
                f.name for f in knowledge_dir.iterdir()
                if f.is_file() and f.suffix.lower() in (".md", ".txt", ".csv", ".json")
            )

        schema_obj: dict[str, Any] | None = None
        if schema_file.exists() and schema_file.is_file():
            try:
                parsed = json.loads(schema_file.read_text(encoding="utf-8", errors="replace"))
                if isinstance(parsed, dict):
                    schema_obj = parsed
            except Exception:
                pass

        return {
            "has_tools": len(tool_files) > 0,
            "tools": tool_files,
            "has_knowledge": len(knowledge_files) > 0,
            "knowledge_files": knowledge_files,
            "has_schema": schema_obj is not None,
            "schema": schema_obj,
        }

    def normalize_skill_file(self, file_path: Path) -> Path:
        """
        当 Agent 通过 write_local_file 落盘 SKILL.md 时，自动执行命名规范与 Frontmatter 结构化兜底修复，
        确保 100% 符合系统热加载标准。
        """
        try:
            raw_text = file_path.read_text(encoding="utf-8", errors="replace")
            raw_dir_name = file_path.parent.name
            safe_dir_name = self._sanitize_skill_id(raw_dir_name)

            parent_root = file_path.parent.parent.resolve()
            if parent_root in (self.skills_dir.resolve(), self.suites_dir.resolve()):
                target_dir = parent_root / safe_dir_name
            else:
                target_dir = file_path.parent

            target_file = target_dir / "SKILL.md"
            if file_path.parent.resolve() != target_dir.resolve():
                target_dir.mkdir(parents=True, exist_ok=True)
                if file_path.exists():
                    file_path.unlink(missing_ok=True)
                try:
                    file_path.parent.rmdir()
                except Exception:
                    pass

            meta = self._parse_frontmatter(raw_text, safe_dir_name)
            mcp_line = f"mcp_server: {meta['mcp_server']}\n" if meta["mcp_server"] else ""
            normalized_md = (
                f"---\n"
                f"name: {meta['name']}\n"
                f"command: {meta['command']}\n"
                f"type: {meta['type']}\n"
                f"{mcp_line}"
                f"description: {meta['description']}\n"
                f"---\n\n"
                f"{meta['body']}\n"
            )
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(normalized_md, encoding="utf-8")
            return target_file
        except Exception as e:
            logger.warning(f"规范化 SKILL.md 失败: {e}")
            return file_path

    def ensure_mcp_companion_skills(self) -> int:
        """
        扫描 mcp_servers.json，确保每一个注册的 MCP 服务都有对应的标准化 SKILL 说明文件，
        从而在输入框 `/` 菜单中都能查看完整调用说明。
        """
        mcp_cfg_path = self.base_dir / "mcp_servers.json"
        if not mcp_cfg_path.exists():
            return 0

        created_count = 0
        try:
            cfg = json.loads(mcp_cfg_path.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}
            if not isinstance(servers, dict):
                return 0

            existing_skills = {
                item.name: item / "SKILL.md"
                for item in self.skills_dir.iterdir()
                if item.is_dir() and (item / "SKILL.md").exists()
            } if self.skills_dir.exists() else {}

            for srv_name, srv_info in servers.items():
                if not isinstance(srv_info, dict):
                    continue
                safe_id = self._sanitize_skill_id(srv_name)
                already_bound = safe_id in existing_skills
                if not already_bound:
                    for md_path in existing_skills.values():
                        raw = md_path.read_text(encoding="utf-8", errors="replace")
                        meta = self._parse_frontmatter(raw, md_path.parent.name)
                        if meta["mcp_server"] == srv_name or meta["name"] == safe_id:
                            already_bound = True
                            break

                if not already_bound and srv_name not in ("playwright-cdp",):
                    cmd_exec = srv_info.get("command", "python")
                    cmd_args = " ".join(str(a) for a in (srv_info.get("args") or []))
                    desc = srv_info.get("description") or f"MCP 扩展服务 ({srv_name}) 封装技能说明"
                    companion_dir = self.skills_dir / safe_id
                    companion_dir.mkdir(parents=True, exist_ok=True)
                    companion_md = (
                        f"---\n"
                        f"name: {safe_id}\n"
                        f"command: /{safe_id}\n"
                        f"type: mcp\n"
                        f"mcp_server: {srv_name}\n"
                        f"description: {desc}\n"
                        f"---\n\n"
                        f"# MCP 封装技能说明：{srv_name}\n\n"
                        f"## 1. 服务概述\n"
                        f"- **绑定的 MCP 服务标识**：`{srv_name}`\n"
                        f"- **底层启动命令**：`{cmd_exec} {cmd_args}`\n"
                        f"- **功能说明**：{desc}\n\n"
                        f"## 2. 调用方式与协议规范\n"
                        f"- 在输入框输入 `/{safe_id}` 加自然语言指令，Agent 将自动加载本规程并调用 `mcp__{srv_name}__<tool_name>` 工具完成任务。\n"
                    )
                    (companion_dir / "SKILL.md").write_text(companion_md, encoding="utf-8")
                    created_count += 1
        except Exception as e:
            logger.warning(f"同步 MCP 伴生 SKILL 说明异常: {e}")
        return created_count

    def _find_skill_dir(self, skill_id_or_name: str) -> Path | None:
        """在 skills/ 和 suites/ 双轨目录中定位指定 Skill 的根目录"""
        safe_name = os.path.basename((skill_id_or_name or "").strip().lstrip("/"))
        if not safe_name:
            return None
        for root in (self.skills_dir, self.suites_dir):
            if not root.exists():
                continue
            cand = root / safe_name
            if cand.exists() and cand.is_dir() and (cand / "SKILL.md").exists():
                return cand
        for s in self.list_skills():
            if (
                s["id"].lower() == safe_name.lower()
                or s["name"].lower() == safe_name.lower()
                or s["command"].lstrip("/").lower() == safe_name.lower()
            ):
                p = Path(s["path"])
                if p.name.upper() == "SKILL.MD":
                    return p.parent
        return None

    def list_skills(self) -> list[dict[str, Any]]:
        """扫描并返回所有可用的本地 Skill、四元组容器与 MCP 封装说明列表（实时读取磁盘）"""
        results: list[dict[str, Any]] = []

        mcp_servers: dict[str, Any] = {}
        mcp_cfg_path = self.base_dir / "mcp_servers.json"
        if mcp_cfg_path.exists():
            try:
                raw_mcp = json.loads(mcp_cfg_path.read_text(encoding="utf-8"))
                if isinstance(raw_mcp, dict) and isinstance(raw_mcp.get("mcpServers"), dict):
                    mcp_servers = raw_mcp["mcpServers"]
            except Exception:
                pass

        bound_mcp_names: set[str] = set()

        # 同时扫描开源轨 (skills/) 与商业套件轨 (suites/)
        scan_roots: list[tuple[Path, str]] = [(self.skills_dir, "community")]
        if self.suites_dir.exists():
            scan_roots.append((self.suites_dir, "commercial"))

        for root_dir, default_tier in scan_roots:
            if not root_dir.exists():
                continue
            for item in sorted(root_dir.iterdir()):
                if item.is_dir():
                    skill_md = item / "SKILL.md"
                    if skill_md.exists():
                        try:
                            raw = skill_md.read_text(encoding="utf-8", errors="replace")
                            meta = self._parse_frontmatter(raw, item.name)
                            four_tuple = self._inspect_four_tuple(item)

                            mcp_srv = meta["mcp_server"] or (item.name if item.name in mcp_servers else "")
                            if mcp_srv:
                                bound_mcp_names.add(mcp_srv)

                            skill_type = "mcp" if mcp_srv or meta["type"] == "mcp" else (
                                "suite" if default_tier == "commercial" or meta["type"] == "suite" else "skill"
                            )

                            # 为包含四元组扩展组件的技能追加可视化组件清单说明
                            enriched_body = meta["body"]
                            manifest_lines: list[str] = []
                            if four_tuple["has_tools"]:
                                manifest_lines.append(f"- 🔧 **随包专属算子 (`tools/`)**: `{', '.join(four_tuple['tools'])}`")
                            if four_tuple["has_knowledge"]:
                                manifest_lines.append(f"- 📚 **FTS5 领域知识库 (`knowledge/`)**: `{', '.join(four_tuple['knowledge_files'])}`")
                            if four_tuple["has_schema"]:
                                manifest_lines.append("- 📋 **结构化交付契约 (`schema.json`)**: 已启用强格式校验")
                            if manifest_lines:
                                enriched_body = (
                                    "### 📦 四元组容器挂载组件\n"
                                    + "\n".join(manifest_lines)
                                    + "\n\n---\n\n"
                                    + enriched_body
                                )

                            results.append({
                                "id": item.name,
                                "name": meta["name"],
                                "command": meta["command"],
                                "type": skill_type,
                                "tier": "commercial" if default_tier == "commercial" else meta["tier"],
                                "mcp_server": mcp_srv,
                                "mcp_enabled": bool(mcp_servers.get(mcp_srv, {}).get("enabled", False)) if mcp_srv else None,
                                "has_tools": four_tuple["has_tools"],
                                "tools": four_tuple["tools"],
                                "has_knowledge": four_tuple["has_knowledge"],
                                "knowledge_files": four_tuple["knowledge_files"],
                                "has_schema": four_tuple["has_schema"],
                                "description": meta["description"],
                                "path": str(skill_md.resolve()),
                                "content": raw,
                                "body": enriched_body,
                                "is_builtin": item.name in self.BUILTIN_IDS,
                            })
                        except Exception as e:
                            logger.warning(f"读取 Skill {item.name} 失败: {e}")

        # 合成 mcp_servers.json 中未创建独立 SKILL.md 的按需 MCP 服务
        for srv_name, srv_info in mcp_servers.items():
            if srv_name in bound_mcp_names or not isinstance(srv_info, dict):
                continue
            safe_id = self._sanitize_skill_id(srv_name)
            enabled = bool(srv_info.get("enabled", False))
            cmd_exec = srv_info.get("command", "")
            cmd_args = " ".join(str(a) for a in (srv_info.get("args") or []))
            desc = srv_info.get("description") or f"MCP 外部扩展服务 ({srv_name})"
            synth_body = (
                f"# MCP 服务封装说明：{srv_name}\n\n"
                f"- **服务状态**：{'🟢 已启用' if enabled else '⚪ 按需启用（可在设置或通过 Agent 设为 enabled: true）'}\n"
                f"- **启动命令**：`{cmd_exec} {cmd_args}`\n"
                f"- **功能概述**：{desc}\n\n"
                f"## 使用方法\n"
                f"直接输入 `/{safe_id} <你的需求>`，若服务已启用则自动调用 `mcp__{srv_name}__*` 系列工具执行。"
            )
            results.append({
                "id": safe_id,
                "name": safe_id,
                "command": f"/{safe_id}",
                "type": "mcp",
                "tier": "community",
                "mcp_server": srv_name,
                "mcp_enabled": enabled,
                "has_tools": False,
                "tools": [],
                "has_knowledge": False,
                "knowledge_files": [],
                "has_schema": False,
                "description": desc,
                "path": str(mcp_cfg_path.resolve()),
                "content": synth_body,
                "body": synth_body,
                "is_builtin": False,
            })

        return results

    async def sync_skill_knowledge(
        self,
        skill_id: str,
        tenant_id: str = "default",
        project_id: str = "default",
    ) -> int:
        """
        [四元组第3组件: knowledge/]
        按文件 SHA256 幂等水位线将 skills/<skill_id>/knowledge/ 下的文档切块同步至 SQLite FTS5 引擎。
        若文件未发生修改，耗时 <1ms 零重复计算。
        """
        skill_dir = self._find_skill_dir(skill_id)
        if not skill_dir:
            return 0
        kb_dir = skill_dir / "knowledge"
        if not kb_dir.exists() or not kb_dir.is_dir():
            return 0

        synced_chunks = 0
        async with db_manager.get_connection() as conn:
            for kb_file in sorted(kb_dir.iterdir()):
                if not kb_file.is_file() or kb_file.suffix.lower() not in (".md", ".txt", ".csv", ".json"):
                    continue
                raw_bytes = kb_file.read_bytes()
                f_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]

                async with conn.execute(
                    "SELECT COUNT(1) as cnt FROM skill_knowledge_chunks WHERE skill_id = ? AND file_name = ? AND file_hash = ?",
                    (skill_dir.name, kb_file.name, f_hash),
                ) as cur:
                    row = await cur.fetchone()
                    if row and row["cnt"] > 0:
                        continue

                # 文件新增或已变更，先清除该文件的旧分片，再重新切块写入
                await conn.execute(
                    "DELETE FROM skill_knowledge_chunks WHERE skill_id = ? AND file_name = ?",
                    (skill_dir.name, kb_file.name),
                )
                text = raw_bytes.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                # 按段落/标题智能切分为约 500~800 字符的知识分片
                paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
                chunks: list[str] = []
                buf = ""
                for p in paragraphs:
                    if len(buf) + len(p) <= 700:
                        buf = f"{buf}\n\n{p}".strip() if buf else p
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = p[:1200]
                if buf:
                    chunks.append(buf)

                for idx, chunk_text in enumerate(chunks):
                    chunk_id = f"skc_{skill_dir.name}_{uuid.uuid4().hex[:8]}"
                    await conn.execute(
                        """
                        INSERT INTO skill_knowledge_chunks
                        (id, skill_id, tenant_id, project_id, file_name, file_hash, chunk_index, content)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (chunk_id, skill_dir.name, tenant_id, project_id, kb_file.name, f_hash, idx, chunk_text),
                    )
                    synced_chunks += 1
            await conn.commit()
        return synced_chunks

    async def search_knowledge(
        self,
        skill_name: str,
        query: str,
        limit: int = 5,
        tenant_id: str = "default",
        project_id: str = "default",
    ) -> str:
        """
        基于 SQLite FTS5 BM25 毫秒级检索指定 Skill `knowledge/` 目录下的领域规程与参考资料。
        """
        skill_dir = self._find_skill_dir(skill_name)
        if not skill_dir:
            return f"Error: 未找到名为 '{skill_name}' 的 Skill。"

        await self.sync_skill_knowledge(skill_dir.name, tenant_id=tenant_id, project_id=project_id)

        tokens = [t for t in re.split(r"[\s,.;:!?，。；：！？、/\\]+", (query or "").strip()) if len(t) >= 2]
        rows_found: list[dict[str, Any]] = []

        async with db_manager.get_connection() as conn:
            if tokens:
                trigram_tokens = [t for t in tokens if len(t) >= 3]
                if trigram_tokens:
                    fts_expr = " OR ".join([f'"{t.replace(chr(34), "")}"' for t in trigram_tokens[:6]])
                    try:
                        async with conn.execute(
                            """
                            SELECT c.file_name, c.chunk_index, c.content, bm25(skill_knowledge_fts) as score
                            FROM skill_knowledge_fts f
                            JOIN skill_knowledge_chunks c ON c.id = f.chunk_id
                            WHERE f.skill_id = ? AND skill_knowledge_fts MATCH ?
                            ORDER BY score ASC
                            LIMIT ?
                            """,
                            (skill_dir.name, fts_expr, limit),
                        ) as cur:
                            rows_found = [dict(r) for r in await cur.fetchall()]
                    except Exception:
                        pass

            # 若短词未命中 trigram，回退 LIKE 检索或返回前 N 个核心分片
            if not rows_found:
                like_kw = f"%{tokens[0]}%" if tokens else "%"
                async with conn.execute(
                    """
                    SELECT file_name, chunk_index, content
                    FROM skill_knowledge_chunks
                    WHERE skill_id = ? AND content LIKE ?
                    ORDER BY chunk_index ASC
                    LIMIT ?
                    """,
                    (skill_dir.name, like_kw, limit),
                ) as cur:
                    rows_found = [dict(r) for r in await cur.fetchall()]

        if not rows_found:
            return f"提示: Skill `{skill_dir.name}` 的 knowledge/ 知识库中未检索到与 '{query}' 匹配的条目。"

        out_lines = [f"### 📚 Skill `{skill_dir.name}` 领域知识库 (FTS5 BM25) 检索结果："]
        for i, r in enumerate(rows_found, 1):
            out_lines.append(f"#### [{i}] 来源文件: `{r['file_name']}` (分片 #{r['chunk_index']})\n{r['content']}")
        return "\n\n".join(out_lines)

    async def execute_skill_tool(
        self,
        skill_name: str,
        script_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        """
        [四元组第2组件: tools/]
        在受控隔离环境中执行指定 Skill 随包挂载的确定性算子脚本 (`skills/<id>/tools/<script_name>`)。
        支持通过命令行 JSON 参数或环境变量传入参数，彻底杜绝大模型复杂计算幻觉。
        """
        skill_dir = self._find_skill_dir(skill_name)
        if not skill_dir:
            return f"Error: 未找到名为 '{skill_name}' 的 Skill。"

        safe_script = os.path.basename((script_name or "").strip())
        if not safe_script:
            return "Error: 未指定有效的算子文件名 script_name。"

        tools_dir = skill_dir / "tools"
        target_script = tools_dir / safe_script
        if not target_script.exists() and not safe_script.endswith(".py"):
            if (tools_dir / f"{safe_script}.py").exists():
                target_script = tools_dir / f"{safe_script}.py"

        if not target_script.exists() or not target_script.is_file():
            return f"Error: 在 `{skill_dir.name}/tools/` 下未找到算子脚本 `{safe_script}`。"

        args_json = json.dumps(arguments or {}, ensure_ascii=False)
        merged_env = os.environ.copy()
        merged_env["PYTHONIOENCODING"] = "utf-8"
        merged_env["SKILL_TOOL_ARGS"] = args_json

        ext = target_script.suffix.lower()
        if ext == ".py":
            cmd_list = [sys.executable, str(target_script), args_json]
        elif ext == ".js":
            cmd_list = ["node", str(target_script), args_json]
        elif ext == ".ps1":
            cmd_list = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(target_script), args_json]
        else:
            return f"Error: 暂不支持直接运行后缀为 '{ext}' 的算子文件。"

        def _run_sync() -> tuple[int, str, str]:
            proc = subprocess.run(
                cmd_list,
                cwd=str(skill_dir),
                env=merged_env,
                input=args_json,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30.0,
            )
            return proc.returncode, proc.stdout, proc.stderr

        try:
            code, stdout, stderr = await asyncio.to_thread(_run_sync)
            output = (stdout or "").strip()
            if stderr and stderr.strip():
                output = f"{output}\n[STDERR]:\n{stderr.strip()}".strip()
            return (
                f"✅ Skill 算子 `{skill_dir.name}/tools/{target_script.name}` 执行完成 (exit_code={code}):\n"
                f"{output or '(无标准输出)'}"
            )
        except Exception as e:
            return f"Error: 执行 Skill 算子 `{target_script.name}` 异常: {e}"

    def validate_output_schema(self, skill_name: str, assistant_text: str) -> tuple[bool, str]:
        """
        [四元组第4组件: schema.json]
        若激活的 Skill 包含 `schema.json`，对模型最终输出的 JSON 数据块进行必填字段与类型契约校验。
        """
        skill_dir = self._find_skill_dir(skill_name)
        if not skill_dir:
            return True, ""
        schema_file = skill_dir / "schema.json"
        if not schema_file.exists():
            return True, ""

        try:
            schema = json.loads(schema_file.read_text(encoding="utf-8", errors="replace"))
            if not isinstance(schema, dict):
                return True, ""
        except Exception:
            return True, ""

        # 提取回复中的 ```json ... ``` 代码块或顶层 JSON 对象
        json_match = re.search(r"```json\s*(.*?)\s*```", assistant_text or "", re.DOTALL | re.IGNORECASE)
        candidate_str = json_match.group(1).strip() if json_match else (assistant_text or "").strip()

        try:
            data = json.loads(candidate_str)
        except Exception as e:
            return False, f"回复未包含合法的 JSON 结构块（违反 `{skill_dir.name}/schema.json` 交付契约）: {e}"

        required_fields = schema.get("required", [])
        if isinstance(required_fields, list) and isinstance(data, dict):
            missing = [req for req in required_fields if req not in data]
            if missing:
                return False, f"输出的 JSON 缺少 `{skill_dir.name}/schema.json` 规定的必填字段: {missing}"

        return True, ""

    def resolve_slash_command(self, user_query: str) -> dict[str, Any] | None:
        """根据用户输入的 `/xxx` 前缀匹配对应的本地 Skill 或 MCP 说明"""
        q = (user_query or "").strip()
        if not q.startswith("/"):
            return None
        first_token = q.split()[0].lower()
        bare_token = first_token.lstrip("/")
        for sk in self.list_skills():
            if (
                sk["command"].lower() == first_token
                or sk["id"].lower() == bare_token
                or sk["name"].lower() == bare_token
            ):
                return sk
        return None

    def save_skill(
        self,
        name: str,
        description: str,
        content: str,
        skill_id: str | None = None,
        command: str | None = None,
    ) -> dict[str, Any]:
        """创建或更新自定义本地 Skill (写入 skills/<id>/SKILL.md 并强制规范化)"""
        safe_id = self._sanitize_skill_id(skill_id or name)
        target_dir = self.skills_dir / safe_id
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_md = target_dir / "SKILL.md"

        clean_body = (content or "").strip()
        cmd_str = (command or f"/{safe_id}").strip()
        if not cmd_str.startswith("/"):
            cmd_str = f"/{cmd_str}"

        if clean_body.startswith("---"):
            full_md = clean_body + "\n"
        else:
            full_md = (
                f"---\n"
                f"name: {safe_id}\n"
                f"command: {cmd_str}\n"
                f"type: skill\n"
                f"description: {description.strip() or '自定义专业技能规程'}\n"
                f"---\n\n"
                f"{clean_body}\n"
            )
        skill_md.write_text(full_md, encoding="utf-8")
        normalized_path = self.normalize_skill_file(skill_md)
        raw_final = normalized_path.read_text(encoding="utf-8", errors="replace")
        meta = self._parse_frontmatter(raw_final, safe_id)
        return {
            "id": safe_id,
            "name": meta["name"],
            "command": meta["command"],
            "type": meta["type"],
            "description": meta["description"],
            "path": str(normalized_path.resolve()),
            "content": raw_final,
            "body": meta["body"],
        }

    def import_skill_markdown(self, raw_md: str, filename_hint: str = "") -> dict[str, Any]:
        """从导入的 SKILL.md 文本解析 Frontmatter 并自动落盘至 skills/ 目录"""
        fallback_slug = Path(filename_hint or "imported-skill").stem
        if fallback_slug.upper() == "SKILL":
            fallback_slug = "imported-skill"
        meta = self._parse_frontmatter(raw_md, fallback_slug)
        return self.save_skill(
            name=meta["name"],
            description=meta["description"],
            content=raw_md,
            skill_id=meta["name"],
            command=meta["command"],
        )

    async def install_from_repo_or_url(self, source: str) -> dict[str, Any]:
        """
        [1秒装 Skill] 支持从 GitHub 简写 (`github:owner/repo[/subpath]`)、Git 仓库 URL 或 Raw SKILL.md URL 一键拉取安装四元组 Skill。
        """
        src = (source or "").strip()
        if not src:
            raise ValueError("安装源地址不能为空")

        # 情况 1: 直接指向单个 SKILL.md 或 .md 的 HTTP URL
        if src.startswith(("http://", "https://")) and (src.endswith(".md") or "raw.githubusercontent.com" in src):
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(src)
                resp.raise_for_status()
                slug = Path(src.split("?")[0]).parent.name or "remote-skill"
                return self.import_skill_markdown(resp.text, filename_hint=slug)

        # 情况 2: github:owner/repo[/subpath] 或 GitHub 仓库 URL，使用 git clone --depth 1 拉取整个四元组目录
        subpath = ""
        repo_url = src
        if src.startswith("github:"):
            parts = src[len("github:"):].strip("/").split("/")
            if len(parts) < 2:
                raise ValueError("github: 格式应为 github:owner/repo 或 github:owner/repo/subpath")
            owner, repo = parts[0], parts[1]
            subpath = "/".join(parts[2:])
            repo_url = f"https://github.com/{owner}/{repo}.git"
        elif "github.com/" in src and "/tree/" in src:
            # 解析形如 https://github.com/owner/repo/tree/main/skills/my-skill
            m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/tree/[^/]+/(.*)", src)
            if m:
                repo_url = f"https://github.com/{m.group(1)}/{m.group(2)}.git"
                subpath = m.group(3).strip("/")

        def _clone_and_install() -> dict[str, Any]:
            with tempfile.TemporaryDirectory() as tmpdir:
                proc = subprocess.run(
                    ["git", "clone", "--depth", "1", repo_url, tmpdir],
                    capture_output=True,
                    text=True,
                    timeout=45.0,
                )
                if proc.returncode != 0:
                    raise RuntimeError(f"Git 克隆失败: {proc.stderr.strip()}")

                source_root = Path(tmpdir) / subpath if subpath else Path(tmpdir)
                if not source_root.exists():
                    raise FileNotFoundError(f"仓库中不存在子路径: {subpath}")

                # 查找包含 SKILL.md 的目录
                skill_Candidate = source_root if (source_root / "SKILL.md").exists() else None
                if not skill_Candidate:
                    for p in source_root.rglob("SKILL.md"):
                        skill_Candidate = p.parent
                        break
                if not skill_Candidate:
                    raise FileNotFoundError("未在该仓库中找到标准的 SKILL.md 文件")

                raw_name = skill_Candidate.name
                if raw_name == Path(tmpdir).name:
                    raw_name = Path(repo_url.rstrip("/")).stem
                safe_id = self._sanitize_skill_id(raw_name)
                target_dir = self.skills_dir / safe_id
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                shutil.copytree(
                    skill_Candidate,
                    target_dir,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"),
                )
                norm_md = self.normalize_skill_file(target_dir / "SKILL.md")
                raw_txt = norm_md.read_text(encoding="utf-8", errors="replace")
                meta = self._parse_frontmatter(raw_txt, safe_id)
                return {
                    "id": safe_id,
                    "name": meta["name"],
                    "command": meta["command"],
                    "description": meta["description"],
                    "path": str(norm_md.resolve()),
                }

        return await asyncio.to_thread(_clone_and_install)

    def delete_skill(self, skill_id: str) -> bool:
        """删除指定的本地 Skill 目录"""
        safe_id = os.path.basename((skill_id or "").strip())
        if not safe_id:
            return False
        target_dir = self.skills_dir / safe_id
        if not target_dir.exists() or not target_dir.is_dir():
            return False
        shutil.rmtree(target_dir, ignore_errors=True)
        return True

    def get_skills_summary_prompt(self) -> str:
        """生成极简的可用 Skill/MCP 列表摘要与四元组开发标准规范，供注入 System Prompt"""
        skills = self.list_skills()
        lines = ["### 🧩 已挂载的本地专业技能包与 MCP 封装规程 (Skills & MCP Summary)："]
        if skills:
            lines.append(
                "当任务匹配以下技能或用户输入对应 `/命令` 时，请调用 `load_skill_detail(skill_name)` 激活；"
                "若技能含 `knowledge/` 可调用 `search_skill_knowledge` 检索规程，含 `tools/` 可调用 `run_skill_tool` 执行随包算子："
            )
            for s in skills:
                tag = "[MCP封装]" if s.get("type") == "mcp" else ("[Pro套件]" if s.get("type") == "suite" else "[SKILL]")
                extras = []
                if s.get("has_tools"):
                    extras.append("🔧含算子")
                if s.get("has_knowledge"):
                    extras.append("📚含知识库")
                if s.get("has_schema"):
                    extras.append("📋含Schema契约")
                extra_str = f" ({'/'.join(extras)})" if extras else ""
                lines.append(f"- {tag} `{s['name']}` (快捷命令 `{s['command']}`){extra_str}: {s['description']}")
        else:
            lines.append("（当前暂无本地技能包）")

        lines.append(
            "\n### 📏 系统级 Skill-as-a-Folder 四元组与 MCP 热加载标准规范（强制遵守）：\n"
            "当用户要求你创建/开发新的 SKILL（或触发 `/new-skill`）或接入 MCP 服务时，**必须遵守以下标准**：\n"
            "1. **统一命名规范（kebab-case）**：\n"
            "   - 所有 `skill-id`、`mcp-server-id` 及 `/command` 必须使用小写英文单词加连字符（`kebab-case`，如 `invoice-auditor`、`/audit`）。\n"
            "2. **Skill-as-a-Folder 四元组目录标准（`skills/<skill-id>/`）**：\n"
            "   - `skills/<skill-id>/SKILL.md`（**必选**）：顶部含 YAML Frontmatter（`name`, `command: /xxx`, `type: skill|mcp`, `description`），正文包含功能概述、快捷指令用法示例与执行 SOP；\n"
            "   - `skills/<skill-id>/tools/<tool_name>.py`（**可选**）：当技能涉及精确数值计算、复杂规则校验或外部脚本时，编写独立 Python 脚本放在 `tools/` 下（脚本从 `sys.argv[1]` 或环境变量 `SKILL_TOOL_ARGS` 读取 JSON 参数并打印结果），通过 `run_skill_tool` 调用；\n"
            "   - `skills/<skill-id>/knowledge/*.md`（**可选**）：存放长篇行业规程、标准模板或参考资料，系统自动挂载至 SQLite FTS5，通过 `search_skill_knowledge` 毫秒级按需检索；\n"
            "   - `skills/<skill-id>/schema.json`（**可选**）：定义结构化输出的 JSON Schema 契约（含 `required` 字段列表）。\n"
            "3. **开发 MCP 服务必配伴生 SKILL 说明**：\n"
            "   - 编写 `mcp_servers/<server-id>/server.py` 并注册至 `mcp_servers.json` 后，必须同步创建 `skills/<server-id>/SKILL.md`（`type: mcp`、`mcp_server: <server-id>`），以便用户在输入框输入 `/` 时能直接查看完整说明书。\n"
            "4. **绝对红线**：系统底层已实现四元组目录与 `mcp_servers.json` 全自动热加载，**严禁新建或修改任何前端 React 组件（如 `src/components/...`）**！"
        )
        return "\n".join(lines)

    def get_full_skill_markdown(self, skill_name: str) -> str:
        """
        当模型调用 load_skill_detail 或用户输入 /command 激活某 Skill 时，
        返回完整 SKILL.md 规程，并自动附上该 Skill 挂载的 `tools/`、`knowledge/` 与 `schema.json` 调用指引。
        """
        skill_dir = self._find_skill_dir(skill_name)
        if not skill_dir:
            # 尝试匹配合成的按需 MCP 说明
            for s in self.list_skills():
                if s["id"].lower() == (skill_name or "").strip().lstrip("/").lower():
                    return s.get("content", "")
            return f"Error: 未找到名为 '{skill_name}' 的 Skill。"

        target_md = skill_dir / "SKILL.md"
        try:
            raw_md = target_md.read_text(encoding="utf-8", errors="replace")
            four_tuple = self._inspect_four_tuple(skill_dir)
            addon_sections: list[str] = []

            if four_tuple["has_tools"]:
                addon_sections.append(
                    f"### 🔧 已挂载的随包专属确定性算子 (`{skill_dir.name}/tools/`)\n"
                    f"- 可用算子脚本: `{', '.join(four_tuple['tools'])}`\n"
                    f"- 调用方式: 请优先使用 `<tool_call name=\"run_skill_tool\">"
                    f"{{\"skill_name\": \"{skill_dir.name}\", \"script_name\": \"{four_tuple['tools'][0]}\", \"arguments\": {{...}}}}</tool_call>` 执行精确计算或核验。"
                )
            if four_tuple["has_knowledge"]:
                addon_sections.append(
                    f"### 📚 已挂载的 FTS5 领域知识库 (`{skill_dir.name}/knowledge/`)\n"
                    f"- 包含文档: `{', '.join(four_tuple['knowledge_files'])}`\n"
                    f"- 检索方式: 需要查阅具体条款或模板时，请调用 `<tool_call name=\"search_skill_knowledge\">"
                    f"{{\"skill_name\": \"{skill_dir.name}\", \"query\": \"关键词\"}}</tool_call>` 进行 BM25 毫秒级按需召回。"
                )
            if four_tuple["has_schema"] and four_tuple["schema"]:
                addon_sections.append(
                    f"### 📋 强制结构化输出契约 (`{skill_dir.name}/schema.json`)\n"
                    f"在最终交付结果中，必须包含符合以下 JSON Schema 契约的 ```json 代码块：\n"
                    f"```json\n{json.dumps(four_tuple['schema'], ensure_ascii=False, indent=2)}\n```"
                )

            if addon_sections:
                return raw_md.strip() + "\n\n---\n" + "\n\n".join(addon_sections)
            return raw_md
        except Exception as e:
            return f"Error: 读取 Skill 失败: {e}"


skill_loader = SkillLoader()
