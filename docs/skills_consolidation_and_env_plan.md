# SKILL 重组精简、问题修复与环境自适应方案

## 一、方案背景与目标
针对现有 30 个 SKILL 存在功能切分过碎、重复冗余、部分脚本死链、以及缺乏运行环境区分的问题，依据“**不考虑向后兼容、追求高内聚低冗余、环境感知**”的核心原则进行全面重构。

- **精简目标**：将 30 个技能合并重组为 **15 个高质量高内聚技能**，大幅缩减提示词 Token 消耗并杜绝模型选型歧义。
- **修复目标**：修复 3 处死链与脚本缺失、替换 Linux 原生 Bash 脚本为跨平台实现、移除硬编码外部 CLI 依赖。
- **环境自适应目标**：在技能元数据、后端加载器与提示词注入层引入 `environment`（操作系统、运行时、CLI）环境感知机制，让 AgentHub 能根据当前宿主机环境（Windows / Linux / macOS）自动适配可用技能。

---

## 二、技能合并与精简重组映射表（30 ➔ 15）

| 序号 | 领域分类 | 重组后的新技能目录 | 整合来源（原技能） | 核心职责说明 | 环境适配 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | 视觉与设计 | `ui-ux-design-pro` | `ui-ux-pro-max`<br/>`ui-styling`<br/>`design-system` | **前端全栈 UI/UX 设计中枢**：包含全端组件规范、Tailwind、shadcn、Radix、三层 Token 体系与无障碍规范。 | 跨平台 (需要 Node.js) |
| 2 | 视觉与设计 | `brand-visual-design` | `design`<br/>`brand`<br/>`banner-design` | **品牌视觉与宣发物料设计**：Logo、CIP、企业视觉识别、社媒海报宣传图生成，移除死脚本，自包含生成逻辑。 | 跨平台 (Python + PIL) |
| 3 | 视觉与设计 | `generative-ui` | `generative-ui` | **动态交互 UI 渲染**：生成并预览交互式 HTML/SVG Widget。 | 跨平台 |
| 4 | 调研与分析 | `spec-architect` | `grill-me`<br/>`grill-with-docs`<br/>`to-spec`<br/>`to-tickets` | **产品技术规格与任务架构师**：反向审问推演（支持写入 docs/）➔ 生成标准技术规格书 ➔ 拆解敏捷 Tickets 依赖树。 | 跨平台 |
| 5 | 调研与分析 | `deep-research` | `research` | **权威一手资料技术调研**：官方文档、源码与规范标准深度调研。 | 跨平台 |
| 6 | 工程与架构 | `code-architecture-review`| `code-review`<br/>`codebase-design`<br/>`git-commit-helper` | **代码工程审查与架构设计**：深模块低耦合设计、双视角代码审查、提交规范生成。 | 跨平台 (Git) |
| 7 | 工程与架构 | `tdd` | `tdd` | **测试驱动开发**：红-绿-重构循环与单元测试编写。 | 跨平台 |
| 8 | 工程与架构 | `diagnosing-bugs` | `diagnosing-bugs` | **疑难缺陷假设排查**：复杂 Bug 证据链排查，重写为跨平台 Python/PowerShell 脚本。 | 跨平台 |
| 9 | 系统与自动化 | `windows-system-operator` | `windows-app-master`<br/>`windows-gui-operator`<br/>`local-env-doctor` | **Windows 桌面与运维控制中枢**：端口/进程排查、注册表寻路、URI协议、Docker管理与封闭GUI窗口自动化。 | 🪟 Windows 专享 |
| 10 | 办公与文档 | `excel-data-suite` | `office-excel`<br/>`data-audit` | **Excel 高级报表与数据审计**：多级表头公式报表生成与异常值/空值率数据质量审计闭环。 | 跨平台 (Python + openpyxl/pandas) |
| 11 | 办公与文档 | `office-ppt` | `office-ppt` | **本地商务 PPTX 演示文稿生成**：16:9 宽屏排版与母版配色生成。 | 跨平台 (Python + pptx) |
| 12 | 办公与文档 | `interactive-slides` | `slides` | **响应式 Web 交互演示幻灯片**：基于 HTML + Chart.js 的在线交互幻灯片。 | 跨平台 |
| 13 | 办公与文档 | `mail-assistant` | `email-assistant` | **通用多邮箱管理**：补齐 Python 原生 IMAP/SMTP 脚本，支持邮件检索与过滤。 | 跨平台 (Python 原生) |
| 14 | 办公与文档 | `knowledge-base-master` | `knowledge-base-master` | **知识库中枢与交互**：权重分级、去重清洗与 Markdown 知识图谱增删改查。 | 跨平台 |
| 15 | 插件与扩展 | `extension-creator` | `skill-creator`<br/>`plugin`<br/>`mcp-app-development`<br/>`permissioned-github` | **插件与技能开发者工具包**：自然语言 3 分钟创建新技能、MCP 协议脚手架、插件发布规范。移除 gh 强绑定，使用原生 Git/REST。 | 跨平台 |

---

## 三、有效性缺陷修复清单

1. **`brand-visual-design`（原 `banner-design` 修复）**：
   - 清理所有指向 `.claude/skills/...`、`screenshot.js` 等不存在的死路径。
   - 统一引用本目录下的 `scripts/`（Python 原生图像参数生成器）。
2. **`mail-assistant`（原 `email-assistant` 修复）**：
   - 在 `scripts/email_client.py` 中使用 Python 标准库 `imaplib`、`email`、`smtplib` 实现完整的邮箱连接与邮件检索，不再依赖缺失的外部脚本。
3. **`diagnosing-bugs` 跨平台修复**：
   - 将 `hitl-loop.template.sh` 改造为跨平台 Python 脚本 `hitl_loop.py`，并在 Windows 下支持通过 PowerShell 原生命令排查。
4. **移除外部 CLI 强依赖**：
   - 清除对 `gh`（GitHub CLI）与 `jq` 的强依赖，改用 Python 内置 `json` 解析与 Git 原生命令。

---

## 四、运行环境自适应机制设计

### 1. Frontmatter 元数据标准扩充
在技能的 `SKILL.md` 中增加 `environment` 声明：
```yaml
---
name: windows-system-operator
title: Windows 桌面与运维控制中枢
category: 系统与自动化
environment:
  os:
    - windows       # 可选: windows, linux, darwin, all
  requires_gui: true
  requires_runtime:
    - powershell
    - python
  requires_cli: []
---
```

### 2. 后端加载器环境感知 (`backend/app/extensions/skill_loader.py`)
- 获取宿主机系统信息（`sys.platform`：`win32` -> `windows`、`linux` -> `linux`、`darwin` -> `macos`）。
- 解析每个技能的 `environment.os`：
  - 若包含当前操作系统或声明为 `all` / `cross-platform`，标记 `is_compatible: true`；
  - 若为非当前操作系统（例如在 Linux Docker 中遇到 Windows 专属技能），标记 `is_compatible: false`，并在调度与系统提示词中自动跳过，避免无效调用。
- API 输出字段增加：
  - `compatible: bool`
  - `supported_os: list[str]`
  - `env_tags: list[str]`（如 `["Windows", "GUI", "Python"]`）

### 3. 前端交互增强
- 横向分类栏及技能卡片上增加系统环境图标（如 `🪟 Windows`、`🌐 跨平台`）。
- 默认高亮并优先呈现当前系统环境兼容的技能。

---

## 五、实施步骤与任务分解（Task List）

- [x] **Task 1: 环境感知体系与元数据规范升级**
  - 在 `skill_loader.py` 与 `skill_service.py` 中实现宿主机 OS 探测与兼容性过滤。
  - 前端类型（`types/index.ts`）与组件（`SkillCategoryTabs.tsx`、`SkillsView.tsx`、`SkillPickerModal.tsx`、`CommandAutocomplete.tsx`、`AgentsView.tsx`）支持展示环境标签。
- [x] **Task 2: 实施技能合并与冗余清理（分批重组）**
  - **Batch 1: 视觉类重组**（`ui-ux-pro-max` + `ui-styling` + `design-system` ➔ `ui-ux-design-pro`；`design` + `brand` + `banner-design` ➔ `brand-visual-design`；保留 `generative-ui`）。
  - **Batch 2: 需求与方案类重组**（`grill-me` + `grill-with-docs` + `to-spec` + `to-tickets` ➔ `spec-architect`；保留 `deep-research`）。
  - **Batch 3: 工程与系统类重组**（`code-review` + `codebase-design` + `git-commit-helper` ➔ `code-architecture-review`；Windows 3 合 1 ➔ `windows-system-operator`；保留 `tdd` 与 `diagnosing-bugs`）。
  - **Batch 4: 办公与扩展类重组**（Excel+Audit ➔ `excel-data-suite`；扩展 4 合 1 ➔ `extension-creator`；邮件助手更名并修复为 `mail-assistant`；保留 `interactive-slides` 与 `knowledge-base-master`）。
- [x] **Task 3: 问题修复与跨平台适配**
  - 修复死引用脚本，彻底剔除 `brand-visual-design` 中不存在的 `.claude/skills/...` 路径；
  - 编写自包含原生 Python 邮件客户端脚本 `skills/mail-assistant/scripts/email_client.py`；
  - 编写跨平台缺陷复现脚本 `skills/diagnosing-bugs/scripts/hitl_loop.py`，去除 Linux Bash 绑定；
  - 移除对外部 `gh` 与 `jq` CLI 工具的硬绑定，改用 Git 原生与 Python 内置支持。
- [x] **Task 4: 全量回归测试与验证**
  - 前端构建：`npm run build` 成功，TypeScript 类型检查零错误，静态导出 4/4 页面成功；
  - 后端单元测试：`pytest backend/tests/test_skills_consolidation_and_env.py` 4 个用例全部通过，相关 56 个核心用例全部通过。
  - 废弃冗余目录已完全清理，最终精准保留 15 个高内聚标准技能目录。

