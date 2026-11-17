"""
只读数据审计确定性算子 (Data Audit Operator)
支持 CSV / Excel 文件的重复项、空值、跨列逻辑一致性与 IQR 异常值检测，
并将 Markdown 与 JSON 审计报告输出至 output/audit/ 目录。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def _load_params() -> dict[str, Any]:
    """兼容 SKILL_TOOL_ARGS 环境变量、位置 JSON 参数与 CLI 参数解析"""
    env_raw = os.environ.get("SKILL_TOOL_ARGS", "").strip()
    if env_raw:
        return json.loads(env_raw)

    if len(sys.argv) >= 2 and sys.argv[1].lstrip().startswith("{"):
        return json.loads(sys.argv[1])

    parser = argparse.ArgumentParser(description="只读数据审计脚本")
    parser.add_argument("--input", dest="input_path", required=True, help="输入 CSV/XLSX 文件路径")
    parser.add_argument("--sheet", dest="sheet_name", default="0", help="Excel Sheet 名称或索引")
    parser.add_argument("--keys", dest="key_columns", nargs="*", default=[], help="主键列名列表")
    parser.add_argument("--rules", dest="rules_json", default="[]", help="跨列规则 JSON 数组")
    parser.add_argument("--output-dir", dest="output_dir", default="output/audit", help="输出目录")
    parser.add_argument("--report-name", dest="report_name", default="", help="输出文件名前缀")
    args = parser.parse_args()

    sheet: str | int = int(args.sheet_name) if args.sheet_name.isdigit() else args.sheet_name
    return {
        "input_path": args.input_path,
        "sheet_name": sheet,
        "key_columns": args.key_columns,
        "rules": json.loads(args.rules_json),
        "output_dir": args.output_dir,
        "report_name": args.report_name,
    }


def _read_dataframe(input_path: Path, sheet_name: str | int = 0) -> pd.DataFrame:
    """只读加载 CSV 或 Excel 数据表"""
    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                return pd.read_csv(input_path, encoding=enc)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(input_path, encoding="utf-8", errors="replace")
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(input_path, sheet_name=sheet_name)
    raise ValueError(f"不支持的文件格式: {suffix}（仅支持 .csv/.xlsx/.xlsm/.xls）")


def run_audit(params: dict[str, Any]) -> dict[str, Any]:
    """执行全量只读数据审计并落盘 Markdown 与 JSON 报告"""
    input_path = Path(str(params["input_path"])).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    sheet_name = params.get("sheet_name", 0)
    df = _read_dataframe(input_path, sheet_name=sheet_name)
    total_rows = int(len(df))
    total_cols = int(len(df.columns))

    # 1. 全行重复项与主键重复项检测
    dup_mask = df.duplicated(keep=False)
    full_dup_rows = [int(i) + 2 for i in df.index[dup_mask].tolist()[:20]]
    key_columns = [c for c in (params.get("key_columns") or []) if c in df.columns]
    key_dup_count = 0
    key_dup_rows: list[int] = []
    if key_columns:
        key_mask = df.duplicated(subset=key_columns, keep=False)
        key_dup_count = int(key_mask.sum())
        key_dup_rows = [int(i) + 2 for i in df.index[key_mask].tolist()[:20]]

    # 2. 空值与缺失率统计
    null_stats: list[dict[str, Any]] = []
    for col in df.columns:
        null_cnt = int(df[col].isna().sum())
        null_rate = round((null_cnt / total_rows * 100.0), 2) if total_rows > 0 else 0.0
        if null_cnt > 0:
            null_stats.append({
                "column": str(col),
                "null_count": null_cnt,
                "null_rate_pct": null_rate,
            })

    # 3. 数值列 IQR 异常值检测
    outlier_stats: list[dict[str, Any]] = []
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    for col in numeric_cols:
        series = df[col].dropna()
        if len(series) < 4:
            continue
        q1 = float(series.quantile(0.25))
        q3 = float(series.quantile(0.75))
        iqr = q3 - q1
        if iqr == 0:
            continue
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        out_mask = (series < lower) | (series > upper)
        out_cnt = int(out_mask.sum())
        if out_cnt > 0:
            outlier_stats.append({
                "column": str(col),
                "outlier_count": out_cnt,
                "lower_bound": round(lower, 4),
                "upper_bound": round(upper, 4),
                "min_val": float(series.min()),
                "max_val": float(series.max()),
            })

    # 4. 跨列逻辑一致性校验
    rule_results: list[dict[str, Any]] = []
    for idx, rule in enumerate(params.get("rules") or []):
        if isinstance(rule, str):
            rule_name = f"rule_{idx + 1}"
            expr = rule
        else:
            rule_name = str(rule.get("name") or f"rule_{idx + 1}")
            expr = str(rule.get("expr") or "").strip()
        if not expr:
            continue
        try:
            passed_mask = df.eval(expr, engine="python")
            violated_idx = df.index[~passed_mask.fillna(False)].tolist()
            rule_results.append({
                "name": rule_name,
                "expr": expr,
                "status": "passed" if not violated_idx else "failed",
                "violation_count": len(violated_idx),
                "sample_rows": [int(i) + 2 for i in violated_idx[:20]],
            })
        except Exception as exc:
            rule_results.append({
                "name": rule_name,
                "expr": expr,
                "status": "error",
                "error": str(exc),
                "violation_count": 0,
                "sample_rows": [],
            })

    report = {
        "source_file": str(input_path),
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "total_rows": total_rows,
            "total_columns": total_cols,
            "columns": [str(c) for c in df.columns],
            "full_duplicate_rows": int(dup_mask.sum()),
            "key_columns": key_columns,
            "key_duplicate_rows": key_dup_count,
            "columns_with_nulls": len(null_stats),
            "columns_with_outliers": len(outlier_stats),
            "rules_failed": sum(1 for r in rule_results if r["status"] == "failed"),
        },
        "duplicates": {
            "full_duplicate_sample_excel_rows": full_dup_rows,
            "key_duplicate_sample_excel_rows": key_dup_rows,
        },
        "nulls": null_stats,
        "outliers": outlier_stats,
        "logic_rules": rule_results,
    }

    output_dir = Path(str(params.get("output_dir") or "output/audit"))
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = str(params.get("report_name") or "").strip() or f"{input_path.stem}_audit"

    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# 数据质量审计报告：{input_path.name}",
        "",
        f"- **审计时间 (UTC)**：{report['audited_at']}",
        f"- **数据规模**：{total_rows} 行 × {total_cols} 列",
        f"- **全行重复记录数**：{report['summary']['full_duplicate_rows']}",
        f"- **主键重复记录数**：{key_dup_count}（主键列：`{', '.join(key_columns) or '未指定'}`）",
        "",
        "## 1. 空值检测",
    ]
    if null_stats:
        md_lines.append("| 列名 | 缺失行数 | 缺失率 (%) |")
        md_lines.append("| :--- | ---: | ---: |")
        for item in null_stats:
            md_lines.append(f"| {item['column']} | {item['null_count']} | {item['null_rate_pct']}% |")
    else:
        md_lines.append("未发现空值列。")

    md_lines.extend(["", "## 2. 数值异常值检测 (IQR)"])
    if outlier_stats:
        md_lines.append("| 列名 | 异常值数量 | 正常下界 | 正常上界 | 最小值 | 最大值 |")
        md_lines.append("| :--- | ---: | ---: | ---: | ---: | ---: |")
        for item in outlier_stats:
            md_lines.append(
                f"| {item['column']} | {item['outlier_count']} | "
                f"{item['lower_bound']} | {item['upper_bound']} | {item['min_val']} | {item['max_val']} |"
            )
    else:
        md_lines.append("未检测到 IQR 离群异常值。")

    md_lines.extend(["", "## 3. 跨列逻辑校验"])
    if rule_results:
        md_lines.append("| 规则名称 | 表达式 | 状态 | 违规行数 | 样本行号 |")
        md_lines.append("| :--- | :--- | :--- | ---: | :--- |")
        for r in rule_results:
            rows_str = ", ".join(str(x) for x in r["sample_rows"]) or "-"
            md_lines.append(f"| {r['name']} | `{r['expr']}` | {r['status']} | {r['violation_count']} | {rows_str} |")
    else:
        md_lines.append("未配置跨列逻辑校验规则。")

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return {
        "status": "success",
        "json_report": str(json_path),
        "markdown_report": str(md_path),
        "summary": report["summary"],
    }


def main() -> None:
    """算子主入口"""
    params = _load_params()
    result = run_audit(params)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
