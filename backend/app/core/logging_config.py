"""
系统标准日志配置模块
遵循工程规范：统一通过 logging 分级输出，同时支持终端流与本地持久化滚动文件日志
"""
import logging
import sys
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler
from .config import settings


def setup_logging() -> None:
    """
    配置应用全局日志格式、控制台输出流与本地日志文件
    """
    log_format = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # 确定日志持久化目录
    log_dir = Path(settings.db_path).parent / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "geminihub.log"
    except Exception:
        log_file = Path("geminihub.log")

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]

    try:
        # 单个日志文件最大 10MB，最多保留 5 个备份切片
        file_handler = RotatingFileHandler(
            str(log_file),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(log_format, date_format))
        handlers.append(file_handler)
    except Exception as e:
        print(f"初始化日志文件失败: {e}", file=sys.stderr)

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=handlers,
    )

    # 抑制部分第三方库过量心跳日志
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


logger = logging.getLogger("geminihub")
