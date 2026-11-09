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
├── SKILL.md                             # [必选] YAML 头信息 (name, command, type, description) + 详细 SOP 规程
├── tools/                               # [可选] 确定性 Python 算子脚本 (如 tools/calculator.py)，接收命令行 JSON 参数并输出标准结果
├── knowledge/                           # [可选] 领域参考文档 (*.md / *.txt / *.csv)，系统自动切块挂载至 SQLite FTS5 引擎
│   └── experience_playbook.md           # [演进] 历史实战排错与人工纠偏经验账本，由任务后经验提炼器经确认合入
└── schema.json                          # [可选] 结构化参数/输出 JSON Schema 校验契约
```

## 3. 构建执行步骤
1. 提炼用户需求或当前会话已跑通的操作流程，去除过程中的试错弯路，仅保留经过验证的最短有效动作序列，确定 `kebab-case` 格式的 `<skill-id>` 与 `/<command>`；
2. 对可复用的环境或业务参数提取为 `{{参数名:默认值}}` 模板占位符与 `schema.json` 字段定义；
3. 调用 `write_local_file` 创建 `skills/<skill-id>/SKILL.md`（若在特定项目沉淀则优先落盘至工作区 `skills/<skill-id>/`）；
4. 若任务涉及精确计算、表格核验或外部接口调用，在 `skills/<skill-id>/tools/` 下编写对应的 Python 脚本；
5. 若任务包含特定规程、避坑经验或模板，写入 `skills/<skill-id>/knowledge/`（含 `experience_playbook.md`）并同步至 FTS5 索引；
6. 告知用户技能已热加载就绪，可直接在输入框输入 `/<command>` 查看说明并调用。
