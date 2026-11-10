"""
会话与消息请求/响应校验契约
"""
from typing import Any
from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    """新建会话"""
    title: str = Field(default="新会话", description="会话标题")
    model_name: str = Field(default="4 Argon", description="使用的默认 Gemini 模型")
    workspace_id: str = Field(default="default", description="归属的工作区 ID")


class ConversationUpdate(BaseModel):
    """修改会话属性"""
    title: str | None = None
    is_pinned: bool | None = None
    workspace_id: str | None = None


class MessageResponse(BaseModel):
    """消息详情响应"""
    id: int
    conversation_id: str
    role: str
    content: str
    executed_by_account: str | None
    token_count: int
    created_at: str
    turn_id: int = 1
    compressed_content: str | None = None
    tool_calls_json: str | None = None
    tool_call_id: str | None = None
    token_stats: dict[str, Any] | None = None


class ConversationResponse(BaseModel):
    """会话详情响应"""
    id: str
    title: str
    source_type: str
    source_account: str | None
    model_name: str
    is_pinned: bool
    workspace_id: str = "default"
    created_at: str
    updated_at: str
    message_count: int = 0
