---
name: office-excel
command: /office-excel
type: skill
version: 1.0.0
description: Excel 报表构建专家技能：支持多级合并表头、动态公式注入（SUM/AVERAGE/SUMIFS）、千分位格式、表头冻结与自适应列宽，输出至 output/sheets/。
---

# Excel 专业报表构建规范 (Office Excel SOP)

## 1. 适用场景与输出约束
- 将结构化数据、财务台账或数据审计汇总结果生成可直接交付的 `.xlsx` 专业报表。
- **输出目录约束**：所有生成的 `.xlsx` 文件必须落盘至 `output/sheets/` 目录。

## 2. 核心工程特性
1. **多表头与标题横幅**：支持主标题行 + 分组多级表头（自动合并单元格并居中对齐）。
2. **动态公式注入**：禁止硬编码合计/均值，自动注入 `=SUM(...)`、`=AVERAGE(...)`、`=SUMIFS(...)` 原生 Excel 公式。
3. **专业格式化**：
   - 金额/数量列自动应用千分位格式（`#,##0.00` 或 `#,##0`），百分比列应用 `0.00%`。
   - 表头采用深海蓝底白字加粗样式，数据区启用斑马纹与细边框。
   - 自动冻结表头窗格（`freeze_panes`），并按中英文字符宽度自适应列宽。

## 3. 执行步骤
1. 调用 `run_skill_tool` 执行 `skill_name="office-excel"`、`script_name="scripts/build_excel.py"`，传入 JSON 参数：
   ```json
   {
     "output_path": "output/sheets/quarterly_report.xlsx",
     "sheets": [
       {
         "name": "销售明细",
         "title": "2026 Q3 区域销售汇总表",
         "group_headers": [
           {"label": "基础信息", "span": 2},
           {"label": "业绩指标", "span": 3}
         ],
         "columns": [
           {"key": "region", "label": "大区", "type": "text"},
           {"key": "product", "label": "产品线", "type": "text"},
           {"key": "revenue", "label": "销售额", "type": "currency", "agg": "SUM"},
           {"key": "cost", "label": "成本", "type": "currency", "agg": "SUM"},
           {"key": "margin", "label": "毛利率", "type": "percent", "agg": "AVERAGE"}
         ],
         "rows": [
           {"region": "华东", "product": "AI 套件", "revenue": 1250000, "cost": 720000, "margin": 0.424}
         ],
         "summary_table": {
           "group_by_col": "region",
           "sum_col": "revenue",
           "title": "按大区 SUMIFS 汇总"
         }
       }
     ]
   }
   ```
2. 验证输出路径与 Sheet 汇总结果后，向用户或下游 `@ppt-architect` 汇报交付路径。
