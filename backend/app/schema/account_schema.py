"""
账号管理与状态胶囊数据契约
基于 Pydantic 规范进行参数校验与响应序列化
"""
from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    """创建或导入账号"""
    name: str = Field(..., description="账号名称，例如：主号-Pro")
    auth_type: str = Field(default="api_key", description="认证类型：api_key, oauth_google_one, web_cookie")
    credentials: str = Field(..., description="API Key 明文或 Access Token")
    priority: int = Field(default=1, description="调度优先级，数值越小优先级越高")
    rpm_limit: int = Field(default=15, description="每分钟请求数软上限")
    rpd_limit: int = Field(default=1500, description="每日请求数软上限")
    email: str | None = Field(default=None, description="Google 账号邮箱")
    avatar_url: str | None = Field(default=None, description="用户头像链接")


class AccountUpdate(BaseModel):
    """更新账号属性与状态"""
    name: str | None = None
    credentials: str | None = None
    priority: int | None = None
    status: str | None = None
    rpm_limit: int | None = None
    rpd_limit: int | None = None


class AccountResponse(BaseModel):
    """账号完整详情响应"""
    id: int
    name: str
    auth_type: str
    priority: int
    status: str
    cooldown_until: str | None
    rpm_limit: int
    rpd_limit: int
    today_used: int
    total_tokens: int
    updated_at: str | None
    email: str | None = None
    avatar_url: str | None = None
    has_refresh_token: bool = False
    expires_at: int | None = None


class AccountStatusPill(BaseModel):
    """顶部状态胶囊（Status Pill）轻量响应"""
    active_account_name: str
    auth_type: str
    status: str
    today_used: int
    current_rpm: int
    rpm_limit: int
    is_warning: bool  # 水位是否超过 90%
    is_relayed: bool = False
    last_relay_info: str | None = None
    email: str | None = None
    avatar_url: str | None = None


class OAuthUrlResponse(BaseModel):
    """OAuth 授权跳转 URL 响应"""
    authorization_url: str
    state: str
    client_id_configured: bool


class OAuthConfigRequest(BaseModel):
    """自定义 OAuth 客户端配置"""
    client_id: str = Field(..., description="Google Cloud OAuth 2.0 Client ID")
    client_secret: str = Field(..., description="Google Cloud OAuth 2.0 Client Secret")


class GoogleOneSessionBindRequest(BaseModel):
    """通过网页端会话或书签一键绑定 Google One"""
    name: str | None = Field(default=None, description="自定义账号名称")
    email: str | None = Field(default=None, description="Google 邮箱")
    credentials: str = Field(..., description="网页端会话凭据或 Token")
    avatar_url: str | None = Field(default=None, description="头像地址")
    extra_meta: str | None = Field(default=None, description="附加元数据")


class AntigravityDetectResponse(BaseModel):
    """Antigravity 本地环境探测结果响应"""
    detected: bool
    installed_path: str | None = None
    accounts_file_path: str | None = None
    active_email: str | None = None
    old_emails: list[str] = []
    total_accounts: int = 0
    database_path: str | None = None
    database_exists: bool = False


class AntigravitySyncRequest(BaseModel):
    """将 Antigravity 本地账号同步至算力池"""
    import_active: bool = Field(default=True, description="是否同步当前 active 账号")
    import_old: bool = Field(default=True, description="是否同步历史 old 账号列表")
