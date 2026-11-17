---
name: interactive-slides
command: /slides
version: 1.1.0
category: 办公与文档
type: skill
environment:
  os:
    - all
  requires_gui: false
description: 响应式 Web 交互演示幻灯片：基于 HTML、CSS 与 Chart.js 构建现代化网页版技术汇报、战略演示与高颜值数据可视化幻灯片。
---

# 响应式 Web 交互演示幻灯片 (Interactive Slides)

## 一、核心定位
与本地二进制 PPTX 不同，本技能专门用于构建**可在浏览器中交互展示、包含动态图表、支持响应式缩放与键盘左右翻页**的高质感单页/多页 Web 幻灯片。

## 二、技术规范
1. **渲染框架**：单文件自包含 HTML，引入 CDN 版本的 Tailwind CSS 与 Chart.js。
2. **排版比例**：支持 16:9 响应式容器，支持暗黑模式与全屏演示。
3. **交付产物**：输出至 `output/slides/<project_name>.html`。
