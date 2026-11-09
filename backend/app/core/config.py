"""
系统全局核心配置模块
基于 Pydantic Settings 实现环境变量自适应与安全校验
"""
import json
import os
import sys
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

from .runtime_paths import get_bundle_root, get_writable_app_root


def load_version_metadata(candidates: list[Path] | None = None) -> tuple[str, str]:
    """按优先级从统一版本配置文件读取版本号与版本后缀。

    Args:
        candidates: 可选覆盖候选路径列表（主要用于单元测试验证）。

    Returns:
        (version, edition): 例如 ("1.1.10", "Strata Edition")。
    """
    search_paths: list[Path]
    if candidates is not None:
        search_paths = list(candidates)
    else:
        bundle_root = get_bundle_root()
        search_paths = [
            bundle_root / "version.json",
            Path(__file__).resolve().parent.parent.parent.parent / "version.json",
            Path(__file__).resolve().parent / "version.json",
        ]

    for path in search_paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ver = str(data.get("version", "")).strip().lstrip("vV")
            edition = str(data.get("edition", "")).strip() or "Strata Edition"
            if ver:
                return ver, edition
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue

    return "0.0.0", "Strata Edition"


_DEFAULT_VERSION, _DEFAULT_EDITION = load_version_metadata()


class Settings(BaseSettings):
    """
    应用全局运行时配置项
    # NOTE: 优先读取环境变量，未配置时自适应检测 data 目录与全局 version.json
    """
    app_name: str = "AgentHub Local"
    app_version: str = _DEFAULT_VERSION
    app_edition: str = _DEFAULT_EDITION
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
            data_dir = get_writable_app_root() / "data"

            data_dir.mkdir(parents=True, exist_ok=True)
            # 默认使用 agenthub_local.db，兼容回退 gemini_hub_local.db
            new_db = data_dir / "agenthub_local.db"
            legacy_db = data_dir / "gemini_hub_local.db"
            if not new_db.exists() and legacy_db.exists():
                self.db_path = str(legacy_db)
            else:
                self.db_path = str(new_db)

        if not self.profile_path:
            profile_dir = Path(self.db_path).parent
            self.profile_path = str(profile_dir / "profile.md")


settings = Settings()
