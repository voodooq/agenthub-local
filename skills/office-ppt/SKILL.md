---
name: office-ppt
command: /ppt
version: 1.1.0
category: 办公与文档
type: skill
environment:
  os:
    - all
  requires_gui: false
  requires_runtime:
    - python
description: PPT 演示架构师技能：支持 16:9 宽屏排版、商务母版配色、要点精简结构与原生 python-pptx 二进制文件生成。
---
# PPT 演示文稿架构规范 (Office PPT SOP)

## 1. 适用场景与输出约束
- 将数据分析结论、审计报告或业务汇报材料转化为结构清晰、高管可读的 `.pptx` 演示文稿。
- **输出目录约束**：所有生成的 `.pptx` 文件必须落盘至 `output/slides/` 目录。

## 2. 排版与设计原则
1. **16:9 宽屏画布**：统一采用 `13.333 × 7.5` 英寸宽屏比例，预留充足留白与页脚页码。
2. **母版配色规范**：主色调（深海蓝 `#0F2942`）、辅助色（科技青 `#0EA5E9`）、浅灰画布（`#F8FAFC`），支持切换 `navy` / `slate` / `emerald` 主题。
3. **要点精简法则**：单页不超过 5 条要点，每条要点尽量控制在 1~2 行内，避免大段文字堆砌。
4. **原生数据图表**：支持在 `chart` 版式中嵌入原生可编辑柱状图（`bar`）、折线图（`line`）或饼图（`pie`）。

## 3. 执行步骤
1. 调用 `run_skill_tool` 执行 `skill_name="office-ppt"`、`script_name="scripts/build_ppt.py"`，传入 JSON 参数：
   ```json
   {
     "output_path": "output/slides/executive_brief.pptx",
     "theme": "navy",
     "slides": [
       {
         "layout": "cover",
         "title": "2026 Q3 业务经营与数据审计汇报",
         "subtitle": "数据驱动决策 · 自动化协同交付",
         "meta": "汇报团队：Office Swarm | 2026-11"
       },
       {
         "layout": "bullets",
         "title": "核心审计结论摘要",
         "bullets": [
           "共审计 12,480 条交易记录，数据完整率达 99.4%",
           "华东区营收同比增长 24.5%，位居各大区首位",
           "识别 3 笔高额离群订单，已标记复核"
         ]
       },
       {
         "layout": "chart",
         "title": "各大区季度销售额对比",
         "chart_type": "bar",
         "categories": ["华东", "华南", "华北", "西南"],
         "series": [
           {"name": "Q2 销售额", "values": [980, 760, 620, 410]},
           {"name": "Q3 销售额", "values": [1250, 890, 710, 480]}
         ],
         "takeaway": "华东区 Q3 环比增长 27.6%，贡献主要增量。"
       }
     ]
   }
   ```
2. 确认生成的 `.pptx` 路径与页数后汇报交付结果。
