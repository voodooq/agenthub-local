"""
O1: 业务查询返回数据清洗器 (DataResponseCleaner)
过滤聚合结果中的 null / 空字符串 / NONE 等脏值分组行；
内置单行纯聚合保护（无 GROUP BY 的单行汇总结果严禁误杀）。
"""
from __future__ import annotations

from typing import Any


class DataResponseCleaner:
    """清洗 Auto Ontology 返回的 rows 中的空值与无效分组行。"""

    DIRTY_STRINGS = {"", "NONE", "UNDEFINED", "NULL", "N/A", "NAN"}
    AGGREGATION_KEYWORDS = ("count", "sum", "avg", "max", "min", "total", "amount", "num", "ratio", "cnt", "fee")
    DIMENSION_KEYWORDS = ("name", "code", "type", "status", "province", "city", "region", "group", "year")

    @classmethod
    def _is_dirty_value(cls, val: Any) -> bool:
        if val is None:
            return True
        if isinstance(val, str) and val.strip().upper() in cls.DIRTY_STRINGS:
            return True
        return False

    @classmethod
    def is_dimension_column(cls, col_name: str) -> bool:
        """判断列名是否倾向于分组维度列而非数值/统计聚合列。"""
        col_lower = (col_name or "").lower()
        if any(kw in col_lower for kw in cls.AGGREGATION_KEYWORDS):
            return False
        return any(kw in col_lower for kw in cls.DIMENSION_KEYWORDS)

    @classmethod
    def clean_query_result(
        cls,
        rows: list[dict[str, Any]],
        primary_group_field: str | None = None,
        group_by_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """清洗数据行中的无效分组行；单行纯聚合结果默认放行保护。"""
        if not rows:
            return {
                "cleaned_rows": [],
                "total_raw_count": 0,
                "dirty_count": 0,
                "has_dirty_data": False,
                "bypassed": False,
            }

        check_fields = [f for f in (group_by_fields or []) if f]
        if not check_fields and primary_group_field:
            check_fields = [primary_group_field]

        # 单行纯聚合保护：只有 1 行且未显式指定分组维度时，严禁按首列误杀整行
        if len(rows) == 1 and not check_fields:
            return {
                "cleaned_rows": rows,
                "total_raw_count": 1,
                "dirty_count": 0,
                "has_dirty_data": False,
                "bypassed": True,
            }

        cleaned_rows: list[dict[str, Any]] = []
        dirty_count = 0

        for row in rows:
            if not isinstance(row, dict):
                dirty_count += 1
                continue

            is_dirty = False
            if check_fields:
                for field in check_fields:
                    if field in row and cls._is_dirty_value(row[field]):
                        is_dirty = True
                        break
            else:
                first_dim_col = next((c for c in row.keys() if cls.is_dimension_column(c)), None)
                if first_dim_col and cls._is_dirty_value(row[first_dim_col]):
                    is_dirty = True

            if is_dirty:
                dirty_count += 1
            else:
                cleaned_rows.append(row)

        return {
            "cleaned_rows": cleaned_rows,
            "total_raw_count": len(rows),
            "dirty_count": dirty_count,
            "has_dirty_data": dirty_count > 0,
            "bypassed": False,
        }
