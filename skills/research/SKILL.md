---
name: research
description: "权威一手资料调研：指派后台智能体深入官方文档、源码与规范标准进行权威技术调研并沉淀为 Markdown 报告。当需要调研新技术、陌生第三方 API 或库时使用。"
version: 1.0.0
category: 调研与分析
---
Spin up a **background agent** to do the research, so you keep working while it reads. Start it with a fresh context window.

Its job:

1. Investigate the question against **primary sources** (official docs, source code, specs, first-party APIs), not a secondary write-up of them. Follow every claim back to the source that owns it.
2. Write the findings to a single Markdown file, citing each claim's source.
3. Save it where the repo already keeps such notes; match the existing convention, and if there is none, put it somewhere sensible and say where.
