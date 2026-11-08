"""
工作区 (Workspace) 请求与响应契约模型
支持通用自定义工作区：虚拟沙盒目录与本地已有物理工程目录就地挂载
"""
from typing import Literal
from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    """新建或挂载工作区请求"""
    name: str = Field(..., min_length=1, max_length=100, description="用户自定义工作区名称")
    type: Literal["virtual", "physical"] = Field(default="virtual", description="工作区类型：virtual(内部沙盒) 或 physical(挂载本地目录)")
    root_path: str | None = Field(default=None, description="物理目录绝对路径。若为 virtual 且未传，则自动在 data/workspaces/<id> 下创建")
    context_prompt: str | None = Field(default=None, description="工作区专属背景契约说明或 System Prompt")
    model_preference: str | None = Field(default=None, description="该工作区优先使用的模型名称")
    security_level: str = Field(default="standard", description="安全策略等级：standard / high / relaxed")


class WorkspaceUpdate(BaseModel):
    """更新工作区元数据请求"""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    context_prompt: str | None = None
    model_preference: str | None = None
    security_level: str | None = None


class WorkspaceResponse(BaseModel):
    """工作区详细响应"""
    id: str
    name: str
    type: str
    root_path: str
    context_prompt: str | None = None
    model_preference: str | None = None
    security_level: str = "standard"
    fts_last_indexed_at: str | None = None
    created_at: str
    conversation_count: int = 0
    is_path_accessible: bool = True


class WorkspaceSaveFileRequest(BaseModel):
    """在工作区内落盘代码或文档文件"""
    rel_path: str = Field(..., min_length=1, description="相对工作区根目录的相对路径，例如 src/app.py 或 notes.md")
    content: str = Field(..., description="文件完整文本内容")
    overwrite: bool = Field(default=True, description="若目标文件存在是否允许覆盖")

