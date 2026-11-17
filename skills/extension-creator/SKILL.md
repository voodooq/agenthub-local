---
name: extension-creator
command: /ext
version: 2.0.0
category: 插件与扩展
type: skill
environment:
  os:
    - all
  requires_gui: false
  requires_runtime:
    - python
    - node
  requires_cli:
    - git
description: 插件与技能扩展开发者工具包：支持通过自然语言 3 分钟自动生成标准四元组 Skill 技能包、MCP 协议服务脚手架与插件打包发布规范，原生集成 Git 协作管理。
---

# 插件与技能扩展开发者工具包 (Extension & Skill Creator)

## 一、自然语言 3 分钟极速构建技能 (Skill Creator)
当用户提出“帮我写个 XX 专家技能”或希望将刚才的重复步骤固化为技能时：
1. **生成四元组标准目录**（放置于 `skills/<skill-name>/`）：
   - `SKILL.md`：核心规程（包含标准化 Frontmatter，包含 `name`, `command`, `version`, `category`, `environment`）；
   - `scripts/`（可选）：随包执行确定性 Python/Node 脚本；
   - `knowledge/`（可选）：随包领域知识规程；
   - `schema.json`（可选）：强约束 JSON 交付契约。
2. **环境声明规范**：
   ```yaml
   environment:
     os: ["all"]          # 或 ["windows"] / ["linux"]
     requires_gui: false
     requires_runtime: ["python"]
     requires_cli: ["git"]
   ```

---

## 二、MCP 协议应用开发规范 (MCP Development)
开发 MCP（Model Context Protocol）扩展服务时：
1. **传输模式选择**：
   - 本地轻量进程：推荐 `stdio`（通过标准输入输出交互，如 Python 脚本）；
   - 远程与多客户端：采用 `streamable-http` / `sse` 模式。
2. **JSON 数据处理与调试**：
   - 使用 Python 内置模块格式化 JSON 数据（替代系统环境可能缺失的 `jq`）：
     ```powershell
     python -m json.tool input.json
     ```

---

## 三、Git 原生协作与版本控制 (替代外部 gh CLI)
在宿主机未安装 GitHub CLI (`gh`) 时，严格使用 Git 原生命令行与标准 REST API 完成版本控制操作：
```powershell
git status -s
git diff
git add <files>
git commit -m "<message>"
git push origin <branch>
```
严禁无端假设用户已安装第三方专用 CLI 工具。

