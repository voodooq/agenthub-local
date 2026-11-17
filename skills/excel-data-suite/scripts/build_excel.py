"""
Excel 专业报表构建确定性算子 (Office Excel Builder)
支持多级合并表头、动态公式注入 (SUM / AVERAGE / SUMIFS)、
千分位/百分比数字格式、表头冻结与中英文自适应列宽。
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

_HEADER_FILL = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
_GROUP_FILL = PatternFill(start_color="2B4C7E", end_color="2B4C7E", fill_type="solid")
_ZEBRA_FILL = PatternFill(start_color="F5F8FC", end_color="F5F8FC", fill_type="solid")
_SUMMARY_FILL = PatternFill(start_color="E8EEF5", end_color="E8EEF5", fill_type="solid")

_TITLE_FONT = Font(name="微软雅黑", size=14, bold=True, color="1E3A5F")
_HEADER_FONT = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
_BODY_FONT = Font(name="微软雅黑", size=10, color="1F2937")
_SUMMARY_FONT = Font(name="微软雅黑", size=10, bold=True, color="1E3A5F")

_THIN_SIDE = Side(style="thin", color="D1D5DB")
_CELL_BORDER = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)

_NUMBER_FORMATS = {
    "integer": "#,##0",
    "currency": "#,##0.00",
    "number": "#,##0.00",
    "percent": "0.00%",
}


def _load_params() -> dict[str, Any]:
    """兼容 SKILL_TOOL_ARGS、位置 JSON 参数与 CLI 参数"""
    env_raw = os.environ.get("SKILL_TOOL_ARGS", "").strip()
    if env_raw:
        return json.loads(env_raw)
    if len(sys.argv) >= 2 and sys.argv[1].lstrip().startswith("{"):
        return json.loads(sys.argv[1])

    parser = argparse.ArgumentParser(description="生成专业 Excel 报表")
    parser.add_argument("--input-json", required=True, help="报表配置 JSON 文件路径")
    parser.add_argument("--output", default="", help="覆盖输出 .xlsx 路径")
    args = parser.parse_args()
    data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    if args.output:
        data["output_path"] = args.output
    return data


def _display_width(value: Any) -> int:
    """估算中英文混排文本的终端/Excel 显示宽度"""
    text = str(value or "")
    width = 0
    for ch in text:
        width += 2 if ord(ch) > 127 else 1
    return width


def _build_sheet(wb: Workbook, spec: dict[str, Any], is_first: bool) -> dict[str, Any]:
    """构建单个工作表：标题横幅、多级表头、数据行、动态公式与 SUMIFS 汇总"""
    sheet_name = str(spec.get("name") or "Sheet1")[:31]
    ws = wb.active if (is_first and wb.active is not None) else wb.create_sheet(title=sheet_name)
    assert ws is not None
    ws.title = sheet_name

    columns = spec.get("columns") or []
    rows = spec.get("rows") or []
    col_count = max(len(columns), 1)
    current_row = 1

    # 1. 可选主标题横幅
    title = str(spec.get("title") or "").strip()
    if title:
        ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=col_count)
        cell = ws.cell(row=current_row, column=1, value=title)
        cell.font = _TITLE_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[current_row].height = 28
        current_row += 1

    # 2. 可选多级分组表头
    group_headers = spec.get("group_headers") or []
    if group_headers:
        col_cursor = 1
        for grp in group_headers:
            span = max(int(grp.get("span") or 1), 1)
            end_col = min(col_cursor + span - 1, col_count)
            if end_col > col_cursor:
                ws.merge_cells(
                    start_row=current_row,
                    start_column=col_cursor,
                    end_row=current_row,
                    end_column=end_col,
                )
            for c_idx in range(col_cursor, end_col + 1):
                g_cell = ws.cell(
                    row=current_row,
                    column=c_idx,
                    value=grp.get("label", "") if c_idx == col_cursor else None,
                )
                g_cell.fill = _GROUP_FILL
                g_cell.font = _HEADER_FONT
                g_cell.alignment = Alignment(horizontal="center", vertical="center")
                g_cell.border = _CELL_BORDER
            col_cursor = end_col + 1
        ws.row_dimensions[current_row].height = 22
        current_row += 1

    # 3. 明细表头行
    header_row_idx = current_row
    for c_idx, col in enumerate(columns, start=1):
        h_cell = ws.cell(row=header_row_idx, column=c_idx, value=col.get("label", col.get("key", "")))
        h_cell.fill = _HEADER_FILL
        h_cell.font = _HEADER_FONT
        h_cell.alignment = Alignment(horizontal="center", vertical="center")
        h_cell.border = _CELL_BORDER
    ws.row_dimensions[header_row_idx].height = 24
    current_row += 1

    # 冻结表头（确保滚动时表头始终可见）
    ws.freeze_panes = ws.cell(row=current_row, column=1)

    # 4. 写入数据行与行内公式
    data_start_row = current_row
    col_key_to_letter = {col["key"]: get_column_letter(i) for i, col in enumerate(columns, start=1)}

    for r_offset, row_data in enumerate(rows):
        excel_row = data_start_row + r_offset
        is_zebra = r_offset % 2 == 1
        for c_idx, col in enumerate(columns, start=1):
            key = col["key"]
            col_type = col.get("type", "text")
            formula_tpl = col.get("formula_template")
            if formula_tpl:
                val = formula_tpl.format(row=excel_row, **col_key_to_letter)
            else:
                val = row_data.get(key)

            d_cell = ws.cell(row=excel_row, column=c_idx, value=val)
            d_cell.font = _BODY_FONT
            d_cell.border = _CELL_BORDER
            if is_zebra:
                d_cell.fill = _ZEBRA_FILL

            if col_type in _NUMBER_FORMATS:
                d_cell.number_format = _NUMBER_FORMATS[col_type]
                d_cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                d_cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[excel_row].height = 20

    data_end_row = data_start_row + len(rows) - 1
    current_row = data_end_row + 1

    # 5. 合计/均值动态公式行
    has_agg = any(col.get("agg") in ("SUM", "AVERAGE") for col in columns)
    if rows and has_agg:
        for c_idx, col in enumerate(columns, start=1):
            col_letter = get_column_letter(c_idx)
            agg = col.get("agg", "NONE")
            col_type = col.get("type", "text")
            if c_idx == 1 and agg not in ("SUM", "AVERAGE"):
                s_val: Any = "合计 / 均值"
            elif agg in ("SUM", "AVERAGE"):
                s_val = f"={agg}({col_letter}{data_start_row}:{col_letter}{data_end_row})"
            else:
                s_val = ""

            s_cell = ws.cell(row=current_row, column=c_idx, value=s_val)
            s_cell.fill = _SUMMARY_FILL
            s_cell.font = _SUMMARY_FONT
            s_cell.border = _CELL_BORDER
            if col_type in _NUMBER_FORMATS and agg in ("SUM", "AVERAGE"):
                s_cell.number_format = _NUMBER_FORMATS[col_type]
                s_cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                s_cell.alignment = Alignment(horizontal="center" if c_idx == 1 else "left", vertical="center")
        ws.row_dimensions[current_row].height = 22
        current_row += 2

    # 6. 可选 SUMIFS 分类汇总子表
    summary_spec = spec.get("summary_table")
    if summary_spec and rows:
        group_key = summary_spec.get("group_by_col")
        sum_key = summary_spec.get("sum_col")
        if group_key in col_key_to_letter and sum_key in col_key_to_letter:
            grp_letter = col_key_to_letter[group_key]
            sum_letter = col_key_to_letter[sum_key]
            sub_title = summary_spec.get("title") or f"按 {group_key} 汇总 (SUMIFS)"
            ws.cell(row=current_row, column=1, value=sub_title).font = _SUMMARY_FONT
            current_row += 1

            for idx, h_text in enumerate(("分组维度", "汇总值"), start=1):
                hc = ws.cell(row=current_row, column=idx, value=h_text)
                hc.fill = _HEADER_FILL
                hc.font = _HEADER_FONT
                hc.border = _CELL_BORDER
            current_row += 1

            seen_groups: list[Any] = []
            for r in rows:
                g_val = r.get(group_key)
                if g_val is not None and g_val not in seen_groups:
                    seen_groups.append(g_val)

            for g_val in seen_groups:
                c1 = ws.cell(row=current_row, column=1, value=g_val)
                c1.font = _BODY_FONT
                c1.border = _CELL_BORDER

                formula = (
                    f"=SUMIFS({sum_letter}${data_start_row}:{sum_letter}${data_end_row},"
                    f"{grp_letter}${data_start_row}:{grp_letter}${data_end_row},A{current_row})"
                )
                c2 = ws.cell(row=current_row, column=2, value=formula)
                c2.font = _BODY_FONT
                c2.border = _CELL_BORDER
                c2.number_format = "#,##0.00"
                c2.alignment = Alignment(horizontal="right", vertical="center")
                current_row += 1

    # 7. 自适应列宽（限制最小 10、最大 48，避免超宽文本撑爆视图）
    for c_idx in range(1, col_count + 1):
        max_w = 10
        for r_idx in range(header_row_idx, ws.max_row + 1):
            cell_val = ws.cell(row=r_idx, column=c_idx).value
            if cell_val is None:
                continue
            text = str(cell_val)
            # 公式单元格按千分位展示预留宽度
            if text.startswith("="):
                max_w = max(max_w, 16)
            else:
                max_w = max(max_w, _display_width(text) + 4)
        ws.column_dimensions[get_column_letter(c_idx)].width = min(max_w, 48)

    return {"sheet": sheet_name, "rows": len(rows), "columns": len(columns)}


def build_excel(params: dict[str, Any]) -> dict[str, Any]:
    """根据配置构建完整 Excel 工作簿并落盘"""
    raw_out = str(params.get("output_path") or "output/sheets/report.xlsx").strip()
    out_path = Path(raw_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    sheets = params.get("sheets") or []
    if not sheets:
        raise ValueError("sheets 不能为空")

    sheet_stats = []
    for idx, spec in enumerate(sheets):
        sheet_stats.append(_build_sheet(wb, spec, is_first=(idx == 0)))

    wb.save(out_path)
    return {
        "status": "success",
        "output_path": str(out_path),
        "sheets": sheet_stats,
    }


def main() -> None:
    """算子主入口"""
    params = _load_params()
    result = build_excel(params)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
