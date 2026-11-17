"""
专家配置持久化仓储层 (Agent Profile Repository)
负责官方内置专家与用户自定义专家的建表、幂等预置与 CRUD 管理
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..core.logging_config import logger
from .db import db_manager

# 专家配置表建表 SQL，由 db.py 与仓储初始化统一复用
AGENT_PROFILES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    base_model TEXT NOT NULL DEFAULT '',
    context_prompt TEXT NOT NULL DEFAULT '',
    skill_ids_json TEXT NOT NULL DEFAULT '[]',
    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
    allowed_write_dirs_json TEXT NOT NULL DEFAULT '[]',
    grounding_config_json TEXT NOT NULL DEFAULT '{}',
    tools_count INTEGER NOT NULL DEFAULT 0,
    ownership TEXT NOT NULL DEFAULT 'custom' CHECK (ownership IN ('official', 'custom')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'draft')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_profiles_ownership ON agent_profiles(ownership);
CREATE INDEX IF NOT EXISTS idx_agent_profiles_status ON agent_profiles(status);
"""

# 系统预置官方专家清单（含原有 4 个核心专家 + 3 个办公协同专家，全面注入防幻觉与严谨度配置）
OFFICIAL_AGENT_SEEDS: list[dict[str, Any]] = [
    {
        "id": "agent_code_architect",
        "name": "代码重构与架构导师",
        "description": "基于《软件设计的哲学》进行深模块划分与双视角代码审查，结合 TDD 保证高质量交付。",
        "base_model": "",
        "context_prompt": (
            "你是代码重构与架构导师，基于《软件设计的哲学》指导深模块设计、"
            "消除浅模块复杂度，并严格执行红-绿-重构 TDD 与双视角代码审查。"
        ),
        "skill_ids": ["codebase-design", "code-review", "tdd", "diagnosing-bugs"],
        "allowed_tools": [
            "read_local_file",
            "get_repo_skeleton",
            "write_local_file",
            "replace_in_file",
            "execute_local_command",
            "load_skill_detail",
            "search_skill_knowledge",
            "search_memory_history",
        ],
        "allowed_write_dirs": [],
        "tools_count": 8,
        "ownership": "official",
        "status": "active",
        "grounding_config": {
            "strict_mode": True,
            "temperature": 0.1,
            "fail_fast_on_missing": True,
            "verify_tool_call": True,
            "require_evidence": True,
            "custom_rules": "重构与修改代码前必须通过 read_local_file 或 get_repo_skeleton 充分调研现有代码，严禁凭空盲写；改动后必须运行单测验证。",
        },
    },
    {
        "id": "agent_deep_researcher",
        "name": "深度研究专家 (Deep Researcher)",
        "description": "结合多维度全网一手资料与官方权威文档进行深度调研，沉淀结构化技术与行业研报。",
        "base_model": "",
        "context_prompt": (
            "你是深度研究专家 (Deep Researcher)，专注于多维度全网权威资料调研与结构化研究报告沉淀。"
            "所有论断必须基于一手信源与真实证据。"
        ),
        "skill_ids": ["research", "generative_ui", "slides"],
        "allowed_tools": [
            "read_local_file",
            "web_fetch",
            "write_local_file",
            "load_skill_detail",
            "search_skill_knowledge",
            "search_memory_history",
        ],
        "allowed_write_dirs": [],
        "tools_count": 6,
        "ownership": "official",
        "status": "active",
        "grounding_config": {
            "strict_mode": True,
            "temperature": 0.2,
            "fail_fast_on_missing": True,
            "verify_tool_call": True,
            "require_evidence": True,
            "custom_rules": "所有论断必须有一手官方文档或 web_fetch 抓取结果为事实依据，禁止无证据猜测推论。",
        },
    },
    {
        "id": "agent_ui_ux_master",
        "name": "UI/UX 前端体验设计师",
        "description": "专注于响应式高质感界面打造、设计系统规范维护与 Radix/Tailwind 样式调优。",
        "base_model": "",
        "context_prompt": (
            "你是 UI/UX 前端体验设计专家，专注于构建高质感现代 Web 界面、"
            "Tailwind 样式调优与组件设计规范。"
        ),
        "skill_ids": ["ui-ux-pro-max", "ui-styling", "design-system"],
        "allowed_tools": [
            "read_local_file",
            "write_local_file",
            "replace_in_file",
            "load_skill_detail",
            "search_skill_knowledge",
        ],
        "allowed_write_dirs": [],
        "tools_count": 5,
        "ownership": "official",
        "status": "active",
        "grounding_config": {
            "strict_mode": False,
            "temperature": 0.6,
            "fail_fast_on_missing": False,
            "verify_tool_call": False,
            "require_evidence": False,
            "custom_rules": "界面与样式设计必须严格遵循单色扁平化与暗黑模式适配原则，杜绝刺眼无规则色彩。",
        },
    },
    {
        "id": "agent_plugin_builder",
        "name": "Plugin & MCP 构建专家",
        "description": "负责标准化 MCP 协议服务端构建、插件脚手架生成与自动化规范坏味道排查。",
        "base_model": "",
        "context_prompt": (
            "你是 Plugin & MCP 构建专家，负责标准化 MCP 协议服务端开发、"
            "自定义 Skill 打包与工具测试。"
        ),
        "skill_ids": ["plugin", "generative_ui"],
        "allowed_tools": [
            "read_local_file",
            "write_local_file",
            "replace_in_file",
            "execute_local_command",
            "discover_mcp_tools",
            "load_skill_detail",
            "search_skill_knowledge",
        ],
        "allowed_write_dirs": [],
        "tools_count": 7,
        "ownership": "official",
        "status": "active",
        "grounding_config": {
            "strict_mode": True,
            "temperature": 0.1,
            "fail_fast_on_missing": True,
            "verify_tool_call": True,
            "require_evidence": True,
            "custom_rules": "所有 MCP 工具定义与 Schema 必须通过 JSON Schema 严格校验，严禁输出非标准字段与格式偏差。",
        },
    },
    {
        "id": "data-auditor",
        "name": "数据审计专家 (Data Auditor)",
        "description": "专注于只读数据审计：重复项检测、空值扫描、逻辑与跨列一致性校验、异常值识别，输出结构化 Markdown/JSON 报告。",
        "base_model": "",
        "context_prompt": (
            "你是数据审计专家 (Data Auditor)。严格以只读方式分析数据源，严禁修改原始业务文件。"
            "优先调用 data-audit 技能的 scripts/audit_table.py 确定性算子执行重复项、空值、跨列逻辑与异常值检测，"
            "并将审计报告输出至 output/audit/ 目录。"
        ),
        "skill_ids": ["data-audit"],
        "allowed_tools": [
            "read_local_file",
            "get_repo_skeleton",
            "load_skill_detail",
            "search_skill_knowledge",
            "run_skill_tool",
            "write_local_file",
        ],
        "allowed_write_dirs": ["output/audit"],
        "tools_count": 6,
        "ownership": "official",
        "status": "active",
        "grounding_config": {
            "strict_mode": True,
            "temperature": 0.0,
            "fail_fast_on_missing": True,
            "verify_tool_call": True,
            "require_evidence": True,
            "custom_rules": "严格只读审计，严禁修改任何原始业务数据；若数据源不存在或内容为空，必须立即如实报告，严禁编造模拟假样本。",
        },
    },
    {
        "id": "excel-builder",
        "name": "Excel 报表构建专家 (Excel Builder)",
        "description": "专注于多表头专业报表生成、动态公式注入（SUMIFS/AVERAGE 等）、自适应列宽、表头冻结与千分位格式渲染。",
        "base_model": "",
        "context_prompt": (
            "你是 Excel 报表构建专家 (Excel Builder)。负责将结构化数据与审计结论转化为专业 Excel 报表。"
            "优先调用 office-excel 技能的 scripts/build_excel.py 算子注入动态公式、冻结窗格与千分位数字格式，"
            "输出至 output/sheets/ 目录。"
        ),
        "skill_ids": ["office-excel"],
        "allowed_tools": [
            "read_local_file",
            "load_skill_detail",
            "search_skill_knowledge",
            "run_skill_tool",
            "write_local_file",
            "execute_local_command",
        ],
        "allowed_write_dirs": ["output/sheets"],
        "tools_count": 6,
        "ownership": "official",
        "status": "active",
        "grounding_config": {
            "strict_mode": True,
            "temperature": 0.1,
            "fail_fast_on_missing": True,
            "verify_tool_call": True,
            "require_evidence": True,
            "custom_rules": "报表公式与数值必须严格对齐原始源表，最终交付必须输出至 output/sheets/ 目录，必须通过脚本真实执行生成。",
        },
    },
    {
        "id": "ppt-architect",
        "name": "PPT 演示架构师 (PPT Architect)",
        "description": "专注于 16:9 宽屏演示文稿架构、母版配色排版、要点精简提炼与高颜值数据图表嵌入，输出专业 .pptx 文件。",
        "base_model": "",
        "context_prompt": (
            "你是 PPT 演示架构师 (PPT Architect)。负责将分析报告与核心结论提炼为高管级 16:9 演示文稿。"
            "遵循每页单主题、要点精简原则，优先调用 office-ppt 技能的 scripts/build_ppt.py 算子生成 .pptx 至 output/slides/ 目录。"
        ),
        "skill_ids": ["office-ppt"],
        "allowed_tools": [
            "read_local_file",
            "load_skill_detail",
            "search_skill_knowledge",
            "run_skill_tool",
            "write_local_file",
            "execute_local_command",
        ],
        "allowed_write_dirs": ["output/slides"],
        "tools_count": 6,
        "ownership": "official",
        "status": "active",
        "grounding_config": {
            "strict_mode": True,
            "temperature": 0.2,
            "fail_fast_on_missing": True,
            "verify_tool_call": True,
            "require_evidence": True,
            "custom_rules": "幻灯片核心结论与要点必须来自真实文档提炼，严禁凭空假造业务成果；交付产物输出至 output/slides/ 目录。",
        },
    },
]


def _utc_now_iso() -> str:
    """生成 UTC ISO8601 时间戳字符串"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_json_loads_list(raw: str | None) -> list[str]:
    """安全反序列化字符串列表 JSON，异常时回退空列表"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item is not None]
    except (ValueError, TypeError):
        pass
    return []


def _safe_json_loads_dict(raw: str | None) -> dict[str, Any]:
    """安全反序列化字典 JSON，异常时回退空字典"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass
    return {}


def _row_to_agent_dict(row: Any) -> dict[str, Any]:
    """将数据库行映射为标准专家字典"""
    item = dict(row)
    skill_ids = _safe_json_loads_list(item.pop("skill_ids_json", "[]"))
    allowed_tools = _safe_json_loads_list(item.pop("allowed_tools_json", "[]"))
    allowed_write_dirs = _safe_json_loads_list(item.pop("allowed_write_dirs_json", "[]"))
    raw_gc = _safe_json_loads_dict(item.pop("grounding_config_json", "{}"))

    if not raw_gc:
        # 回退默认值：官方专家从种子中对齐，自定义专家默认 strict_mode=False
        if item.get("ownership") == "official":
            seed = next((s for s in OFFICIAL_AGENT_SEEDS if s["id"] == item.get("id")), None)
            raw_gc = (seed.get("grounding_config") or {}).copy() if seed else {}
        else:
            raw_gc = {
                "strict_mode": False,
                "temperature": 0.7,
                "fail_fast_on_missing": False,
                "verify_tool_call": False,
                "require_evidence": False,
                "custom_rules": "",
            }

    item["skill_ids"] = skill_ids
    item["allowed_tools"] = allowed_tools
    item["allowed_write_dirs"] = allowed_write_dirs
    item["grounding_config"] = raw_gc
    item["tools_count"] = int(item.get("tools_count") or len(allowed_tools))
    return item


class AgentProfileRepository:
    """专家配置异步 SQLite 仓储"""

    # 记录已完成初始化的数据库路径，支持测试环境动态切换 db_path
    _initialized_db_path: str | None = None

    @classmethod
    async def ensure_initialized(cls) -> None:
        """幂等创建 agent_profiles 表并预置官方专家数据"""
        if cls._initialized_db_path == db_manager.db_path:
            return

        async with db_manager.get_connection() as conn:
            await conn.executescript(AGENT_PROFILES_SCHEMA_SQL)

            # 动态平滑迁移：检查现有表中是否缺失 grounding_config_json 列
            async with conn.execute("PRAGMA table_info(agent_profiles)") as cursor:
                cols = [row[1] for row in await cursor.fetchall()]
                if "grounding_config_json" not in cols:
                    await conn.execute(
                        "ALTER TABLE agent_profiles ADD COLUMN grounding_config_json TEXT NOT NULL DEFAULT '{}'"
                    )

            now = _utc_now_iso()
            for seed in OFFICIAL_AGENT_SEEDS:
                skill_ids_json = json.dumps(seed["skill_ids"], ensure_ascii=False)
                allowed_tools_json = json.dumps(seed["allowed_tools"], ensure_ascii=False)
                allowed_write_dirs_json = json.dumps(seed["allowed_write_dirs"], ensure_ascii=False)
                grounding_config_json = json.dumps(seed.get("grounding_config") or {}, ensure_ascii=False)
                tools_count = int(seed.get("tools_count") or len(seed["allowed_tools"]))
                await conn.execute(
                    """
                    INSERT INTO agent_profiles (
                        id, name, description, base_model, context_prompt,
                        skill_ids_json, allowed_tools_json, allowed_write_dirs_json,
                        grounding_config_json, tools_count, ownership, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'official', ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        base_model = excluded.base_model,
                        context_prompt = excluded.context_prompt,
                        skill_ids_json = excluded.skill_ids_json,
                        allowed_tools_json = excluded.allowed_tools_json,
                        allowed_write_dirs_json = excluded.allowed_write_dirs_json,
                        grounding_config_json = excluded.grounding_config_json,
                        tools_count = excluded.tools_count,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    WHERE agent_profiles.ownership = 'official'
                    """,
                    (
                        seed["id"],
                        seed["name"],
                        seed["description"],
                        seed["base_model"],
                        seed["context_prompt"],
                        skill_ids_json,
                        allowed_tools_json,
                        allowed_write_dirs_json,
                        grounding_config_json,
                        tools_count,
                        seed.get("status", "active"),
                        now,
                        now,
                    ),
                )
            await conn.commit()

        cls._initialized_db_path = db_manager.db_path
        logger.debug("AgentProfileRepository 初始化及官方专家预置完成")

    @classmethod
    async def list_agents(
        cls,
        ownership: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询专家列表（官方专家优先排序，按更新时间倒序）"""
        await cls.ensure_initialized()
        conditions: list[str] = []
        params: list[Any] = []
        if ownership in ("official", "custom"):
            conditions.append("ownership = ?")
            params.append(ownership)
        if status in ("active", "draft"):
            conditions.append("status = ?")
            params.append(status)

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
        SELECT * FROM agent_profiles
        {where_sql}
        ORDER BY CASE WHEN ownership = 'official' THEN 0 ELSE 1 END, updated_at DESC, name ASC
        """
        async with db_manager.get_connection() as conn:
            async with conn.execute(sql, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                return [_row_to_agent_dict(row) for row in rows]

    @classmethod
    async def get_agent(cls, agent_id: str) -> dict[str, Any] | None:
        """按 ID 查询单个专家配置"""
        await cls.ensure_initialized()
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM agent_profiles WHERE id = ?",
                (agent_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return _row_to_agent_dict(row) if row else None

    @classmethod
    async def create_agent(cls, data: dict[str, Any]) -> dict[str, Any]:
        """创建自定义专家配置"""
        await cls.ensure_initialized()
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("专家名称不能为空")

        agent_id = str(data.get("id") or "").strip() or f"agent_{uuid.uuid4().hex[:12]}"
        skill_ids = [str(x) for x in (data.get("skill_ids") or []) if str(x).strip()]
        allowed_tools = [str(x) for x in (data.get("allowed_tools") or []) if str(x).strip()]
        allowed_write_dirs = [str(x) for x in (data.get("allowed_write_dirs") or []) if str(x).strip()]
        tools_count = int(data.get("tools_count") if data.get("tools_count") is not None else len(allowed_tools))
        status = data.get("status") if data.get("status") in ("active", "draft") else "active"

        # 默认严谨工作模式优先
        raw_gc = data.get("grounding_config")
        if not isinstance(raw_gc, dict):
            raw_gc = {
                "strict_mode": True,
                "temperature": 0.1,
                "fail_fast_on_missing": True,
                "verify_tool_call": True,
                "require_evidence": True,
                "custom_rules": "",
            }
        grounding_config_json = json.dumps(raw_gc, ensure_ascii=False)
        now = _utc_now_iso()

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO agent_profiles (
                    id, name, description, base_model, context_prompt,
                    skill_ids_json, allowed_tools_json, allowed_write_dirs_json,
                    grounding_config_json, tools_count, ownership, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'custom', ?, ?, ?)
                """,
                (
                    agent_id,
                    name,
                    str(data.get("description") or "").strip(),
                    str(data.get("base_model") or "").strip(),
                    str(data.get("context_prompt") or "").strip(),
                    json.dumps(skill_ids, ensure_ascii=False),
                    json.dumps(allowed_tools, ensure_ascii=False),
                    json.dumps(allowed_write_dirs, ensure_ascii=False),
                    grounding_config_json,
                    tools_count,
                    status,
                    now,
                    now,
                ),
            )
            await conn.commit()

        created = await cls.get_agent(agent_id)
        if not created:
            raise RuntimeError(f"创建专家后回读失败: {agent_id}")
        return created

    @classmethod
    async def update_agent(cls, agent_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """更新自定义专家配置；若目标为官方专家则抛出 PermissionError"""
        await cls.ensure_initialized()
        existing = await cls.get_agent(agent_id)
        if not existing:
            return None
        if existing["ownership"] == "official":
            raise PermissionError("官方内置专家受保护，禁止修改")

        name = str(data["name"]).strip() if "name" in data and data["name"] is not None else existing["name"]
        if not name:
            raise ValueError("专家名称不能为空")

        description = (
            str(data["description"]).strip()
            if "description" in data and data["description"] is not None
            else existing["description"]
        )
        base_model = (
            str(data["base_model"]).strip()
            if "base_model" in data and data["base_model"] is not None
            else existing["base_model"]
        )
        context_prompt = (
            str(data["context_prompt"]).strip()
            if "context_prompt" in data and data["context_prompt"] is not None
            else existing["context_prompt"]
        )
        skill_ids = (
            [str(x) for x in data["skill_ids"] if str(x).strip()]
            if "skill_ids" in data and data["skill_ids"] is not None
            else existing["skill_ids"]
        )
        allowed_tools = (
            [str(x) for x in data["allowed_tools"] if str(x).strip()]
            if "allowed_tools" in data and data["allowed_tools"] is not None
            else existing["allowed_tools"]
        )
        allowed_write_dirs = (
            [str(x) for x in data["allowed_write_dirs"] if str(x).strip()]
            if "allowed_write_dirs" in data and data["allowed_write_dirs"] is not None
            else existing["allowed_write_dirs"]
        )
        tools_count = (
            int(data["tools_count"])
            if "tools_count" in data and data["tools_count"] is not None
            else (len(allowed_tools) if "allowed_tools" in data else existing["tools_count"])
        )
        status = (
            data["status"]
            if "status" in data and data["status"] in ("active", "draft")
            else existing["status"]
        )

        grounding_config = (
            data["grounding_config"]
            if "grounding_config" in data and isinstance(data["grounding_config"], dict)
            else (existing.get("grounding_config") or {})
        )
        grounding_config_json = json.dumps(grounding_config, ensure_ascii=False)
        now = _utc_now_iso()

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE agent_profiles
                SET name = ?, description = ?, base_model = ?, context_prompt = ?,
                    skill_ids_json = ?, allowed_tools_json = ?, allowed_write_dirs_json = ?,
                    grounding_config_json = ?, tools_count = ?, status = ?, updated_at = ?
                WHERE id = ? AND ownership = 'custom'
                """,
                (
                    name,
                    description,
                    base_model,
                    context_prompt,
                    json.dumps(skill_ids, ensure_ascii=False),
                    json.dumps(allowed_tools, ensure_ascii=False),
                    json.dumps(allowed_write_dirs, ensure_ascii=False),
                    grounding_config_json,
                    tools_count,
                    status,
                    now,
                    agent_id,
                ),
            )
            await conn.commit()

        return await cls.get_agent(agent_id)

    @classmethod
    async def delete_agent(cls, agent_id: str) -> bool:
        """删除自定义专家配置；若目标为官方专家则抛出 PermissionError"""
        await cls.ensure_initialized()
        existing = await cls.get_agent(agent_id)
        if not existing:
            return False
        if existing["ownership"] == "official":
            raise PermissionError("官方内置专家受保护，禁止删除")

        async with db_manager.get_connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM agent_profiles WHERE id = ? AND ownership = 'custom'",
                (agent_id,),
            )
            await conn.commit()
            return cursor.rowcount > 0
