"""
系统全局核心配置模块
基于 Pydantic Settings 实现环境变量自适应与安全校验
"""
import json
import logging
import os
import sqlite3
import sys
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

from .runtime_paths import get_bundle_root, get_writable_app_root

logger = logging.getLogger("agenthub")


def has_sqlite_user_data(db_file: Path) -> bool:
    """只读探测 SQLite 数据库是否已包含用户业务数据（会话、工作区或纳管账号）。"""
    try:
        if not db_file.is_file() or db_file.stat().st_size == 0:
            return False
    except OSError:
        return False

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=2.0)
        cur = conn.cursor()
        tables = {
            row[0]
            for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "conversations" in tables and cur.execute("SELECT 1 FROM conversations LIMIT 1").fetchone():
            return True
        if "accounts" in tables and cur.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
            return True
        if "workspaces" in tables:
            ws_cnt = cur.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
            if int(ws_cnt or 0) > 1:
                return True
        return False
    except sqlite3.Error:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def resolve_default_db_path(data_dir: Path) -> str:
    """解析默认数据库路径，防止空白或误创建的 agenthub_local.db 屏蔽原有非空老库 gemini_hub_local.db。"""
    new_db = data_dir / "agenthub_local.db"
    legacy_db = data_dir / "gemini_hub_local.db"

    if not new_db.exists():
        return str(legacy_db) if legacy_db.exists() else str(new_db)

    # 若新库为空文件或纯空初始化占位库且老库确实存在用户数据，保护性回退至老库
    if legacy_db.exists() and not has_sqlite_user_data(new_db) and has_sqlite_user_data(legacy_db):
        logger.warning(
            "[Config] 检测到空白占位库 %s，已自动回退保护原有非空历史库 %s",
            new_db.name,
            legacy_db.name,
        )
        return str(legacy_db)

    return str(new_db)


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
            self.db_path = resolve_default_db_path(data_dir)

        if not self.profile_path:
            profile_dir = Path(self.db_path).parent
            self.profile_path = str(profile_dir / "profile.md")


settings = Settings()
