---
name: skill-creator
command: /new-skill
type: skill
description: 【系统母技能】通过自然语言对话或总结刚才的操作步骤，3分钟自动生成标准四元组 Skill 技能包并热加载。
---

# 智能 Skill 构建母技能 (Built-in Skill Creator)

## 1. 功能定位
当用户输入 `/new-skill <需求描述>` 或要求“把刚才我们执行的步骤保存为一个新 Skill”时，自动在 `skills/<skill-id>/` 下创建符合 AgentHub 四元组标准的技能容器。

## 2. 标准四元组目录结构 (Skill-as-a-Folder)
```text
skills/<skill-id>/
├── SKILL.md          # [必选] YAML 头信息 (name, command, type, description) + 详细 SOP 规程
├── tools/            # [可选] 确定性 Python 算子脚本 (如 tools/calculator.py)，接收命令行 JSON 参数并输出标准结果
├── knowledge/        # [可选] 领域参考文档 (*.md / *.txt / *.csv)，系统自动切块挂载至 SQLite FTS5 引擎
└── schema.json       # [可选] 结构化输出 JSON Schema 校验契约
```

## 3. 构建执行步骤
1. 提炼用户需求或当前会话已跑通的操作流程，确定 `kebab-case` 格式的 `<skill-id>` 与 `/<command>`；
2. 调用 `write_local_file` 创建 `skills/<skill-id>/SKILL.md`；
3. 若任务涉及精确计算、表格核验或外部接口调用，在 `skills/<skill-id>/tools/` 下编写对应的 Python 脚本；
4. 若任务包含特定规程或模板，写入 `skills/<skill-id>/knowledge/`；
5. 告知用户技能已热加载就绪，可直接在输入框输入 `/<command>` 查看说明并调用。
