---
name: data-audit
command: /data-audit
type: skill
version: 1.0.0
description: 只读数据审计专家技能：自动执行表格重复项、空值率、跨列逻辑一致性与 IQR 异常值检测，输出结构化 Markdown/JSON 审计报告至 output/audit/。
---

# 只读数据审计规范 (Data Audit SOP)

## 1. 适用场景与安全边界
- 适用于对 `.csv`、`.xlsx`、`.xls` 业务/财务/运营明细表进行自动化质量审计。
- **严格只读原则**：严禁修改、覆盖或删除原始输入数据文件；所有审计产物必须输出至 `output/audit/` 目录。

## 2. 核心审计维度
1. **重复项审计**：全行完全重复检测，以及指定业务主键列（`key_columns`）的重复记录扫描。
2. **空值与完整性**：逐列统计缺失值数量、缺失率（%）与首个缺失行索引。
3. **异常值检测**：对数值列采用 IQR 四分位距法（`[Q1 - 1.5*IQR, Q3 + 1.5*IQR]`）识别离群值，输出上下界与异常记录数。
4. **跨列逻辑校验**：支持自定义业务规则表达式（如 `net_amount <= gross_amount`、`quantity > 0`），定位违规行号。

## 3. 执行步骤
1. 调用 `run_skill_tool` 执行 `skill_name="data-audit"`、`script_name="scripts/audit_table.py"`，传入 JSON 参数：
   ```json
   {
     "input_path": "data/sales.xlsx",
     "sheet_name": 0,
     "key_columns": ["order_id"],
     "rules": [
       {"name": "实付不超过应付", "expr": "paid_amount <= total_amount"},
       {"name": "数量大于零", "expr": "qty > 0"}
     ],
     "output_dir": "output/audit"
   }
   ```
2. 检查算子返回的 JSON 摘要与生成的 `.md` / `.json` 报告路径。
3. 若需进一步生成报表或汇报，将审计结论摘要交由 `@excel-builder` 或 `@ppt-architect` 接力处理。
