"""
专家配置管理接口 (Agents API)
提供官方预置专家查询及自定义专家的持久化增删改查能力
"""
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...repository.agent_repository import AgentProfileRepository
from ...scheduler.intent_parser import ScheduleIntentParser

router = APIRouter(prefix="/agents", tags=["agents"])


class GroundingConfigSchema(BaseModel):
    """专家防幻觉与严谨度配置 Schema"""

    strict_mode: bool = Field(True, description="是否开启严谨工作模式（推荐工作场景使用）")
    temperature: float = Field(0.1, ge=0.0, le=1.0, description="推荐采样温度（严谨模式建议 0.0~0.2）")
    fail_fast_on_missing: bool = Field(True, description="未检索到真实数据时立即如实拒答，禁止脑补")
    verify_tool_call: bool = Field(True, description="禁止在未成功执行工具并收到回包前宣称已完成")
    require_evidence: bool = Field(True, description="输出结论时必须附带真实引用来源与数据出处")
    custom_rules: str = Field("", max_length=500, description="自定义防虚构业务红线（上限 500 字符）")


class AgentNaturalGenerateRequest(BaseModel):
    """自然语言一键生成专家与联动定时任务请求体"""

    user_prompt: str = Field(..., min_length=2, max_length=2000, description="用户口语化需求描述")
    workspace_id: str = Field("default", description="当前工作区 ID")


class AgentCreateRequest(BaseModel):
    """创建自定义专家请求体"""

    id: str | None = Field(None, description="可选自定义 ID，留空则自动生成")
    name: str = Field(..., min_length=1, max_length=120, description="专家名称")
    description: str = Field("", max_length=1000, description="专家职责简介")
    base_model: str = Field("", max_length=120, description="默认底座模型（留空表示跟随当前会话模型）")
    context_prompt: str = Field("", max_length=8000, description="专家角色系统提示词")
    skill_ids: list[str] = Field(default_factory=list, description="挂载的技能 ID 列表")
    allowed_tools: list[str] = Field(default_factory=list, description="允许调用的工具白名单")
    allowed_write_dirs: list[str] = Field(default_factory=list, description="允许写入的相对目录白名单")
    grounding_config: GroundingConfigSchema | None = Field(
        default_factory=GroundingConfigSchema, description="防幻觉与严谨度配置"
    )
    tools_count: int | None = Field(None, ge=0, description="可用工具数量展示值")
    status: Literal["active", "draft"] = Field("active", description="专家状态")


class AgentUpdateRequest(BaseModel):
    """更新自定义专家请求体"""

    name: str | None = Field(None, min_length=1, max_length=120, description="专家名称")
    description: str | None = Field(None, max_length=1000, description="专家职责简介")
    base_model: str | None = Field(None, max_length=120, description="默认底座模型")
    context_prompt: str | None = Field(None, max_length=8000, description="专家角色系统提示词")
    skill_ids: list[str] | None = Field(None, description="挂载的技能 ID 列表")
    allowed_tools: list[str] | None = Field(None, description="允许调用的工具白名单")
    allowed_write_dirs: list[str] | None = Field(None, description="允许写入的相对目录白名单")
    grounding_config: GroundingConfigSchema | None = Field(None, description="防幻觉与严谨度配置")
    tools_count: int | None = Field(None, ge=0, description="可用工具数量展示值")
    status: Literal["active", "draft"] | None = Field(None, description="专家状态")


@router.post("/generate-from-prompt")
async def generate_agent_from_prompt(req: AgentNaturalGenerateRequest) -> dict[str, Any]:
    """根据用户自然语言描述，智能解析生成专家配置、工具白名单、技能集及可选定时调度计划"""
    raw = req.user_prompt.strip()
    lower_raw = raw.lower()

    # 1. 识别是否包含定时执行意图
    cron_expr, human_schedule, time_spans = ScheduleIntentParser._extract_schedule(raw)
    has_schedule = bool(time_spans) or bool(
        re.search(r"(定时|每天|每日|每周|每月|每小时|准时|几点|:\d{2}|点整|点半)", raw)
    )

    # 2. 提取或生成专家名称
    name_match = re.search(
        r"(?:我要个|我想要个|创建个|创建一个|新建一个|帮我建个|做一个|需要一个)?\s*([^\s，,。；;]{2,14}?(?:专家|助手|管家|导师|架构师|专员))",
        raw,
    )
    if name_match:
        agent_name = name_match.group(1).strip()
    elif any(k in raw for k in ("日报", "周报", "总结", "交互")):
        agent_name = "日报总结专家"
    elif any(k in lower_raw for k in ("excel", "表格", "报表")):
        agent_name = "报表处理专家"
    elif any(k in lower_raw for k in ("ppt", "幻灯片", "汇报")):
        agent_name = "汇报演示专家"
    elif any(k in raw for k in ("审计", "核查", "校验")):
        agent_name = "数据核查专家"
    else:
        agent_name = "智能专员"

    # 3. 匹配系统真实可用技能
    matched_skills: list[str] = []
    try:
        from ...extensions.skill_loader import SkillLoader
        installed_skills = SkillLoader().list_skills(workspace_id=req.workspace_id)
        for sk in installed_skills:
            sid = str(sk.get("id") or "")
            sname = str(sk.get("name") or "").lower()
            sdesc = str(sk.get("description") or "").lower()
            if not sid:
                continue
            if any(kw in lower_raw for kw in (sid.lower(), sname)) or (
                ("excel" in lower_raw or "表格" in raw) and "excel" in sid.lower()
            ) or (
                ("ppt" in lower_raw or "幻灯片" in raw) and "ppt" in sid.lower()
            ) or (
                ("审计" in raw or "核查" in raw) and "audit" in sid.lower()
            ) or (
                ("调研" in raw or "研究" in raw) and "research" in sid.lower()
            ):
                if sid not in matched_skills:
                    matched_skills.append(sid)
    except Exception:
        pass

    # 4. 推导工具白名单、写入目录与专业防幻觉 SOP
    allowed_tools: list[str] = ["read_local_file", "write_local_file", "load_skill_detail"]
    allowed_write_dirs: list[str] = []
    sop_lines: list[str] = [f"你是【{agent_name}】。用户原始需求：{raw}"]

    is_daily_report = any(k in raw for k in ("日报", "周报", "总结", "交互", "复盘", "记录"))
    is_work_related = is_daily_report or any(
        k in raw for k in ("报表", "excel", "ppt", "表格", "审计", "核查", "代码", "重构", "工作", "分析", "数据")
    )

    if is_daily_report:
        for t in ("search_memory_history", "save_long_term_memory", "search_skill_knowledge"):
            if t not in allowed_tools:
                allowed_tools.append(t)
        allowed_write_dirs = ["output/reports"]
        sop_lines.extend([
            "",
            "### 核心执行规约（防幻觉与事实基准）：",
            "1. 【事实唯一基准】：优先调用 `search_memory_history(query='today', scope='messages', limit=50)` 收集当日全部会话与沙盒交互真实内容。所有日报内容必须 100% 源于读取到的记录。",
            "2. 【缺失如实拒答 (Fail-Fast)】：若检索结果为空（今日无新增对话或操作记录），必须立即如实回复“今日工作区暂无新增交互记录，无需生成虚构日报”，严禁为了交差而编造假任务与指标！",
            "3. 【分类提炼】：有真实数据时，按项目与主题结构化提炼完成事项、核心结论与待办。",
            "4. 【真实落盘】：使用 `write_local_file` 将 Markdown 日报保存至 `output/reports/daily_report_<日期>.md`，必须收到真实成功回包后再向用户交付。",
        ])
    else:
        if any(k in lower_raw for k in ("网页", "新闻", "搜索", "抓取", "行情", "调研", "官网")):
            allowed_tools.append("web_fetch")
        if any(k in lower_raw for k in ("脚本", "命令", "运行", "构建", "测试", "python", "excel", "ppt")):
            allowed_tools.extend(["execute_local_command", "run_skill_tool"])
        if any(k in raw for k in ("记忆", "历史", "回顾", "总结")):
            allowed_tools.extend(["search_memory_history", "save_long_term_memory"])
        if any(k in raw for k in ("报告", "文档", "归档", "记录", "表格", "审计")):
            allowed_write_dirs = ["output/reports"]
        sop_lines.extend([
            "",
            "### 核心执行规约（防幻觉与事实基准）：",
            "1. 严格依托已授权工具与真实数据完成任务，严禁无事实依据凭空推演。",
            "2. 数据缺失或文件不存在时必须如实说明情况（Fail-Fast），严禁自行生成 Mock/虚构数据欺骗用户。",
            "3. 任何最终成果必须真实执行工具输出至指定交付目录。",
        ])

    context_prompt = "\n".join(sop_lines)

    # 推荐防幻觉配置
    grounding_config = {
        "strict_mode": is_work_related,
        "temperature": 0.1 if is_work_related else 0.7,
        "fail_fast_on_missing": True,
        "verify_tool_call": True,
        "require_evidence": is_work_related,
        "custom_rules": "未检索到本地真实数据或文件时直接报告无记录，严禁编造任何模拟/测试假数据。" if is_work_related else "",
    }

    schedule_info: dict[str, Any] | None = None
    if has_schedule:
        parsed_sched = ScheduleIntentParser.parse(raw)
        schedule_info = {
            "enabled": True,
            "name": f"{agent_name} · 定时自动执行",
            "cron_expr": cron_expr,
            "human_schedule": human_schedule,
            "prompt_template": (
                f"请严格以【{agent_name}】身份执行预定任务：\n"
                f"{parsed_sched.get('prompt_template') or raw}"
            ),
        }

    return {
        "success": True,
        "draft": {
            "name": agent_name,
            "description": f"基于自然语言需求生成：{raw[:120]}",
            "base_model": "",
            "context_prompt": context_prompt,
            "skill_ids": matched_skills,
            "allowed_tools": allowed_tools,
            "allowed_write_dirs": allowed_write_dirs,
            "grounding_config": grounding_config,
            "schedule": schedule_info,
        },
    }


@router.get("/")
@router.get("")
async def list_agents(
    ownership: Literal["official", "custom"] | None = Query(None, description="按归属过滤"),
    status: Literal["active", "draft"] | None = Query(None, description="按状态过滤"),
) -> dict:
    """获取全部专家列表（官方专家 + 用户自定义专家）"""
    items = await AgentProfileRepository.list_agents(ownership=ownership, status=status)
    return {"agents": items, "total": len(items)}


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    """获取单个专家配置详情"""
    item = await AgentProfileRepository.get_agent(agent_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"未找到专家: {agent_id}")
    return {"agent": item}


@router.post("/")
@router.post("")
async def create_agent(req: AgentCreateRequest) -> dict:
    """新建自定义专家并持久化至数据库"""
    try:
        created = await AgentProfileRepository.create_agent(req.model_dump())
        return {"success": True, "agent": created}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=409, detail="专家 ID 已存在")
        raise HTTPException(status_code=500, detail=f"创建专家失败: {e}")


@router.put("/{agent_id}")
async def update_agent(agent_id: str, req: AgentUpdateRequest) -> dict:
    """更新自定义专家配置；官方内置专家禁止修改并返回 403"""
    try:
        payload = req.model_dump(exclude_unset=True)
        updated = await AgentProfileRepository.update_agent(agent_id, payload)
        if not updated:
            raise HTTPException(status_code=404, detail=f"未找到专家: {agent_id}")
        return {"success": True, "agent": updated}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str) -> dict:
    """删除自定义专家配置；官方内置专家禁止删除并返回 403"""
    try:
        deleted = await AgentProfileRepository.delete_agent(agent_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"未找到专家: {agent_id}")
        return {"success": True, "deleted_id": agent_id}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
