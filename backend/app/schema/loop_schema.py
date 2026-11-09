"""
任务自主循环 (Task Loop) 与敏感操作拦截 Pydantic 数据规范
"""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


LoopStatus = Literal[
    "idle",
    "planning",
    "executing",
    "verifying",
    "reflecting",
    "waiting_approval",
    "completed",
    "failed",
    "paused",
]

PolicyLevel = Literal["L0", "L1", "L2"]


class PendingApprovalCall(BaseModel):
    """
    等待用户人工审批的工具调用详情
    """
    call_id: str = Field(..., description="调用唯一标识")
    tool_name: str = Field(..., description="调用的工具名称")
    arguments: dict[str, Any] = Field(default_factory=dict, description="调用参数字典")
    policy_level: PolicyLevel = Field("L2", description="安全拦截级别 (L0/L1/L2)")
    risk_reason: str = Field(..., description="触发拦截的高危原因描述")
    snapshot_path: str | None = Field(None, description="已生成的安全快照路径 (针对 L1/L2)")


class LoopStepItem(BaseModel):
    """
    DAG 子任务步骤项
    """
    step_id: int = Field(..., description="步骤序号")
    title: str = Field(..., description="步骤简述")
    status: Literal["pending", "running", "success", "failed", "skipped"] = "pending"
    detail: str | None = Field(None, description="执行明细或产出物路径")


class TaskLoopCreateRequest(BaseModel):
    """
    发起自主任务循环请求
    """
    workspace_id: str = Field(..., description="归属工作区 ID")
    conversation_id: str = Field(..., description="关联会话 ID")
    goal_prompt: str = Field(..., description="任务终极目标描述")
    max_iterations: int = Field(10, description="最大自愈迭代轮次 (默认 10)")


class TaskLoopApprovalAction(BaseModel):
    """
    用户审批反馈动作
    """
    action: Literal["approve_once", "approve_all", "modify_args", "reject"] = Field(
        ..., description="审批操作类型: 批准本次 / 本任务白名单 / 修改参数另存为 / 拒绝并指导改道"
    )
    modified_arguments: dict[str, Any] | None = Field(
        None, description="当 action=modify_args 时提供的新参数字典"
    )
    user_guidance: str | None = Field(
        None, description="当 action=reject 时用户给出的引导反思文本"
    )


class TaskLoopControlAction(BaseModel):
    """
    任务循环控制指令
    """
    action: Literal["pause", "resume", "abort", "complete", "archive"] = Field(
        ..., description="暂停 / 恢复 / 强停 / 完成验收 / 归档收起"
    )


class TaskLoopResponse(BaseModel):
    """
    任务循环状态响应
    """
    id: str
    workspace_id: str
    conversation_id: str
    goal_prompt: str
    scratchpad_md: str | None
    loop_status: LoopStatus
    current_iteration: int
    max_iterations: int
    pending_approval_call: PendingApprovalCall | None = None
    error_fingerprints: list[str] = Field(default_factory=list)
    created_at: datetime | str
    updated_at: datetime | str

