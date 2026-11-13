"""
数据库实体模型定义，对应 accounts, conversations, messages 及 FTS5 全文索引结构
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class AccountModel:
    """
    Google 算力账号实体
    # NOTE: auth_type 目前统一为 gemini_cli，通过独立 CLI 目录隔离完成 OAuth 授权
    """
    id: int | None
    name: str
    auth_type: str
    credentials: str
    priority: int
    status: str  # active, cooldown, disabled
    cooldown_until: str | None
    rpm_limit: int
    rpd_limit: int
    today_used: int
    total_tokens: int
    updated_at: str | None
    email: str | None = None
    avatar_url: str | None = None
    extra_meta: str | None = None
    cli_home_dir: str | None = None
    # NOTE: 兼容旧数据库结构中可能遗留的字段，避免 TypeError
    refresh_token: str | None = None
    expires_at: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> "AccountModel":
        """安全从 SQLite Row 字典中构造实体，自动忽略不存在的多余字段"""
        import dataclasses
        row_dict = dict(row)
        valid_field_names = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in row_dict.items() if k in valid_field_names}
        return cls(**filtered)


@dataclass
class ConversationModel:
    """
    会话主表实体（支持本地原生新建、公开分享克隆、Takeout 导入及微内核 Agent 状态机）
    """
    id: str
    title: str
    source_type: str  # local, web_sync, takeout
    source_account: str | None
    model_name: str
    is_pinned: bool
    created_at: str
    updated_at: str
    scene_mode: str = "agent"  # oneshot, chat, agent
    incognito: int = 0
    working_memory_json: str = "{}"
    rolling_summary: str = ""
    current_cwd: str | None = None
    workspace_id: str = "default"
    collapse_anchor_turn_id: int = 0
    anchored_summary: str = ""
    collapsed_tokens_saved: int = 0
    parent_conversation_id: str | None = None
    branched_from_message_id: int | None = None
    branched_from_turn_id: int | None = None

    @classmethod
    def from_row(cls, row: Any) -> "ConversationModel":
        import dataclasses
        row_dict = dict(row)
        valid_field_names = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in row_dict.items() if k in valid_field_names}
        return cls(**filtered)


@dataclass
class MessageModel:
    """
    对话消息实体
    # NOTE: executed_by_account 记录实际执行该条回复的云端账号名称，用于多账号接力溯源
    """
    id: int | None
    conversation_id: str
    role: str  # user, model, assistant, tool, system
    content: str
    executed_by_account: str | None
    token_count: int
    created_at: str
    turn_id: int = 1
    compressed_content: str | None = None
    tool_calls_json: str | None = None
    tool_call_id: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "MessageModel":
        import dataclasses
        row_dict = dict(row)
        valid_field_names = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in row_dict.items() if k in valid_field_names}
        return cls(**filtered)


@dataclass
class LongTermMemoryModel:
    """长期记忆画像实体"""
    id: str
    category: str  # env_info, preference, rule
    content: str
    confidence: float = 0.8
    hit_count: int = 0
    last_accessed_at: str | None = None
    created_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "LongTermMemoryModel":
        import dataclasses
        row_dict = dict(row)
        valid_field_names = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in row_dict.items() if k in valid_field_names}
        return cls(**filtered)