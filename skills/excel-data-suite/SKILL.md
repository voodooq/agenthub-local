---
name: excel-data-suite
command: /excel
version: 2.0.0
category: 办公与文档
type: skill
environment:
  os:
    - all
  requires_gui: false
  requires_runtime:
    - python
description: Excel 高级报表构建与数据质量审计套件：支持多级合并表头专业报表生成、动态公式注入（SUMIFS/AVERAGE 等）、以及表格数据重复项、空值率与 IQR 异常值闭环审计。
---

# Excel 高级报表与数据质量审计套件 (Excel Data Suite)

## 一、双核心能力总览
本套件整合了商业级 Excel 表格的 **“智能生成”** 与 **“数据审计”** 两大能力：
1. **高级报表生成**：支持多级复合表头、自适应列宽、商务冷灰/深蓝配色母版、动态原生公式注入（SUM, AVERAGE, SUMIFS, VLOOKUP 等）。
2. **数据质量审计**：自动对本地 CSV/Excel 表格执行重复行检测、空值率统计、跨列业务逻辑矛盾排查、以及基于 IQR（四分位距）算法的数值异常点识别。

---

## 二、随包内置脚本工具
1. **构建专业 Excel 报表**：
   ```powershell
   python skills/excel-data-suite/scripts/build_excel.py --input <数据JSON/CSV> --output output/report.xlsx
   ```
2. **执行数据质量审计**：
   ```powershell
   python skills/excel-data-suite/scripts/audit_table.py --file <待审文件路径>
   ```
   审计结果将自动生成结构化 Markdown 报告并输出至 `output/audit/` 目录。
