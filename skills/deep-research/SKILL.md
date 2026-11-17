---
name: deep-research
command: /research
version: 1.1.0
category: 调研与分析
type: skill
environment:
  os:
    - all
  requires_gui: false
description: 权威一手资料调研：指派后台智能体深入官方文档、源码与规范标准进行权威技术调研并沉淀为 Markdown 报告。当需要调研新技术、陌生第三方 API 或库时使用。
---

# 权威一手资料技术调研 (Deep Research)

## 一、核心原则
1. **只信赖权威一手信源**：官方文档、GitHub 官方仓库源码、W3C/RFC 规范标准或作者技术博客；严禁采信未经核实的二次转述二手技术文章。
2. **实测为准**：若存在 API 参数存疑或版本兼容性差异，通过简单最小复现脚本实际运行核验，以真实执行输出为准。
3. **沉淀报告**：调研成果以结构化 Markdown 输出，包含核心背景、版本特性比对、最小可用代码示例（PoC）与潜在避坑指南。

