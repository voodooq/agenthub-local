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

    # 4. 推导工具白名单、写入目录与专业 SOP
    allowed_tools: list[str] = ["read_local_file", "write_local_file", "load_skill_detail"]
    allowed_write_dirs: list[str] = []
    sop_lines: list[str] = [f"你是【{agent_name}】。用户原始需求：{raw}"]

    is_daily_report = any(k in raw for k in ("日报", "周报", "总结", "交互", "复盘", "记录"))
    if is_daily_report:
        for t in ("search_memory_history", "save_long_term_memory", "search_skill_knowledge"):
            if t not in allowed_tools:
                allowed_tools.append(t)
        allowed_write_dirs = ["output/reports"]
        sop_lines.extend([
            "执行规范：",
            "1. 优先调用 `search_memory_history(query='today', scope='messages', limit=50)` 收集当日全部会话与沙盒交互内容。",
            "2. 按项目/主题分类提炼已完成工作、核心结论、遇到的问题与后续待办。",
            "3. 使用 `write_local_file` 将完整 Markdown 日报归档至 `output/reports/daily_report_<日期>.md`。",
            "4. 若包含重要长期决策或关键进展，调用 `save_long_term_memory` 沉淀核心摘要。",
        ])
    else:
        if any(k in lower_raw for k in ("网页", "新闻", "搜索", "抓取", "行情", "调研", "官网")):
            allowed_tools.append("web_fetch")
        if any(k in lower_raw for k in ("脚本", "命令", "运行", "构建", "测试", "python", "excel", "ppt")):
            allowed_tools.extend(["execute_local_command", "run_skill_tool"])
        if any(k in raw for k in ("记忆", "历史", "回顾", "总结")):
            allowed_tools.extend(["search_memory_history", "save_long_term_memory"])
        if any(k in raw for k in ("报告", "文档", "归档", "记录")):
            allowed_write_dirs = ["output/reports"]
        sop_lines.append("请严格依托已授权工具与挂载技能高质量完成任务，并将最终成果输出至指定交付目录。")

    context_prompt = "\n".join(sop_lines)
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
