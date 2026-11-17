---
name: slides
description: "图表与战略演示幻灯片：基于 Chart.js 与响应式 HTML 构建具备结构化文案与数据可视化的演示页面。当需要制作技术汇报、项目总结或演示幻灯片时使用。"
argument-hint: "[topic] [slide-count]"
metadata:
  author: claudekit
  version: "1.0.0"
version: 1.0.0
category: 办公与文档
---
# Slides

Strategic HTML presentation design with data visualization.

## When to Use

- Marketing presentations and pitch decks
- Data-driven slides with Chart.js
- Strategic slide design with layout patterns
- Copywriting-optimized presentation content

## Subcommands

| Subcommand | Description | Reference |
|------------|-------------|-----------|
| `create` | Create strategic presentation slides | `references/create.md` |

## References (Knowledge Base)

| Topic | File |
|-------|------|
| Layout Patterns | `references/layout-patterns.md` |
| HTML Template | `references/html-template.md` |
| Copywriting Formulas | `references/copywriting-formulas.md` |
| Slide Strategies | `references/slide-strategies.md` |

## Routing

1. Parse subcommand from `$ARGUMENTS` (first word)
2. Load corresponding `references/{subcommand}.md`
3. Execute with remaining arguments
