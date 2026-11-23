"""
全局活跃模型与思考等级状态管理模块
支持全系统（Web 对话、IM 微信渠道、任务调度、后台专家）统一感知与自适应当前模型。
具备内存缓存与本地 JSON 持久化能力。
"""
import json
import logging
from pathlib import Path
from typing import Any

from ..core.config import settings

logger = logging.getLogger("agenthub")

_GLOBAL_ACTIVE_MODEL: str = ""
_GLOBAL_THINKING_LEVEL: str = "high"


def _get_active_model_file_path() -> Path:
    db_p = Path(settings.db_path)
    data_dir = db_p.parent if db_p.parent.exists() else Path(__file__).resolve().parent.parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "active_model_selection.json"


def get_global_active_model() -> tuple[str, str]:
    """获取当前全局选择的模型与思考等级。

    Returns:
        (model_name, thinking_level): 例如 ("Gemini 3.8 Flash", "high")
    """
    global _GLOBAL_ACTIVE_MODEL, _GLOBAL_THINKING_LEVEL

    if _GLOBAL_ACTIVE_MODEL:
        return _GLOBAL_ACTIVE_MODEL, _GLOBAL_THINKING_LEVEL or "high"

    # 尝试从本地持久化文件读取
    fpath = _get_active_model_file_path()
    if fpath.exists():
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                m = str(data.get("model") or "").strip()
                tl = str(data.get("thinking_level") or "high").strip().lower()
                if m:
                    _GLOBAL_ACTIVE_MODEL = m
                    _GLOBAL_THINKING_LEVEL = tl if tl in ("low", "medium", "high", "xhigh") else "high"
                    return _GLOBAL_ACTIVE_MODEL, _GLOBAL_THINKING_LEVEL
        except Exception as e:
            logger.debug(f"读取 active_model_selection.json 提示: {e}")

    # 未曾手动持久化时保持为空字符串，交由算力调度层按用户当前实际启用的通道自动选择
    return "", _GLOBAL_THINKING_LEVEL or "high"


def set_global_active_model(model: str, thinking_level: str = "high") -> tuple[str, str]:
    """设置并持久化当前全局选择的模型与思考等级。

    Args:
        model: 模型标识符或展示名（如 "Gemini 3.8 Flash"）
        thinking_level: 思考深度等级 ("low" | "medium" | "high")

    Returns:
        (model_name, thinking_level)
    """
    global _GLOBAL_ACTIVE_MODEL, _GLOBAL_THINKING_LEVEL

    clean_model = (model or "").strip()
    clean_tl = (thinking_level or "high").strip().lower()
    if clean_tl not in ("low", "medium", "high", "xhigh"):
        clean_tl = "high"

    if clean_model:
        _GLOBAL_ACTIVE_MODEL = clean_model
        _GLOBAL_THINKING_LEVEL = clean_tl
        try:
            fpath = _get_active_model_file_path()
            payload = {
                "model": _GLOBAL_ACTIVE_MODEL,
                "thinking_level": _GLOBAL_THINKING_LEVEL,
            }
            fpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"持久化 active_model_selection.json 失败: {e}")

    return _GLOBAL_ACTIVE_MODEL, _GLOBAL_THINKING_LEVEL

