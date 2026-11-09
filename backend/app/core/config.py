"""
系统全局核心配置模块
基于 Pydantic Settings 实现环境变量自适应与安全校验
"""
import os
import sys
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    应用全局运行时配置项
    # NOTE: 优先读取环境变量，未配置时自适应检测 data 目录
    """
    app_name: str = "AgentHub Local"
    app_version: str = "1.1.2"
    # NOTE: 默认仅绑定本机回环地址，防止意外对外暴露
    host: str = "127.0.0.1"
    port: int = 8686

    # 算力池调度参数 - 默认使用最新 4 Argon / gemini-2.5-pro 高级推理模型
    auto_failover_retry: int = 3
    default_model: str = "4 Argon"
    default_thinking_level: str = "high"
    default_cooldown_seconds: int = 60

    # 日志与安全
    log_level: str = "INFO"
    websidecar_url: str = os.getenv("WEBSIDECAR_URL", "http://web-bridge:8000")
    # NOTE: secret_key 从环境变量读取；若未配置则在启动时自动生成随机值并打印日志（不写入数据库）
    secret_key: str = ""
    # 本机访问令牌，由 main.py 启动时动态生成并注入，不持久化
    access_token: str = ""

    # 路径配置
    db_path: str = ""
    profile_path: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 若环境变量中未配置 SECRET_KEY，则自动生成随机值
        if not self.secret_key:
            self.secret_key = secrets.token_hex(32)

        if not self.db_path:
            if Path("/app/data").exists() or Path("/app").exists():
                data_dir = Path("/app/data")
            elif getattr(sys, "frozen", False):
                # 独立便携运行环境：数据存放在可执行文件所在目录下的 data/
                data_dir = Path(sys.executable).resolve().parent / "data"
            else:
                data_dir = Path(__file__).resolve().parent.parent.parent.parent / "data"

            data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = str(data_dir / "gemini_hub_local.db")

        if not self.profile_path:
            profile_dir = Path(self.db_path).parent
            self.profile_path = str(profile_dir / "profile.md")


settings = Settings()
