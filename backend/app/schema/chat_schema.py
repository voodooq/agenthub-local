"""
流式对话核心请求契约
"""
from pydantic import BaseModel, Field


class AttachmentItem(BaseModel):
    name: str = Field(..., description="附件文件名")
    file_path: str | None = Field(default=None, description="后端本地物理路径")
    rel_path: str | None = Field(default=None, description="相对当前工作区的沙盒附件路径")
    url: str | None = Field(default=None, description="前端可访问的静态 URL")
    mime_type: str = Field(default="image/png", description="MIME 类型")
    size_str: str | None = Field(default=None, description="文件大小展示")
    category: str | None = Field(default=None, description="附件分类: image | pdf | office | text | file")


class ConfirmedFactItem(BaseModel):
    """前端交互对齐卡直通落盘的结构化事实项"""
    fact_key: str = Field(..., min_length=1, max_length=120, description="标准化事实键")
    fact_value: str = Field(..., min_length=1, max_length=2000, description="已确认的确定性值")
    category: str = Field(default="biz_rule", description="事实分类: user_pref | biz_rule | env_config")
    workspace_scope: str = Field(default="workspace", description="作用域: workspace(当前工作区) | global(全局)")


class ChatStreamRequest(BaseModel):
    """发起流式对话请求"""
    conversation_id: str | None = Field(default=None, description="会话ID，若为空则由后端自动生成新会话")
    message: str = Field(..., description="用户输入的 Prompt Markdown 文本")
    model_name: str = Field(default="4 Argon", description="请求的模型名称，默认最新 4 Argon")
    thinking_level: str = Field(default="high", description="思考等级：low(快速高效), medium(深度适中), high(超级详尽), off")
    system_override: str | None = Field(default=None, description="本次请求定制覆盖的 System Instruction")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    context_rounds: int = Field(default=10, description="上下文多轮对话记忆轮数 (0表示无记忆)")
    attachments: list[AttachmentItem] | None = Field(default=None, description="附带的图片或文档附件列表")
    scene_mode: str | None = Field(default=None, description="对话场景模式: oneshot(极速单次) | chat(标准对话) | agent(Agent任务)")
    incognito: bool | None = Field(default=None, description="是否开启无痕模式 (不读写长期记忆)")
    workspace_id: str | None = Field(default=None, description="归属的工作区 ID")
    execution_strategy: str = Field(default="direct", description="执行策略: direct(立即执行) | plan_first(计划优先/谋定而动)")
    clarify_mode: bool = Field(default=False, description="对话模式下是否开启需求前置澄清")
    approved_plan_steps: list[dict] | None = Field(default=None, description="用户在前端确认批准的步骤清单")
    confirmed_facts: list[ConfirmedFactItem] | None = Field(default=None, description="对齐卡确认的结构化事实参数列表")
    inquiry_confirmed: bool = Field(default=False, description="用户是否已在前端交互卡片完成分步确认")
    inquiry_action: str | None = Field(default=None, description="交互确认卡片触发动作: execute_now(确认后统一执行) | clarify_only(仅细化方案)")


class ToolApprovalRequest(BaseModel):
    """前端确认或拒绝灰名单终端命令的审批请求"""
    call_id: str = Field(..., description="工具调用唯一标识 ID")
    approved: bool = Field(..., description="True=允许执行, False=拒绝执行")


class StopChatRequest(BaseModel):
    """前端主动终止当前会话命令执行的请求"""
    conversation_id: str = Field(..., description="会话 ID")
