# AgentHub Local (本地多源算力中心与自主智能体工作台)

<p align="center">
  <img src="https://img.shields.io/badge/Version-v1.1.4-emerald?style=flat-square" alt="Version" />
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&style=flat-square" alt="Python Version" />
  <img src="https://img.shields.io/badge/FastAPI-0.111%2B-009688?logo=fastapi&style=flat-square" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Next.js-14.2-black?logo=next.js&style=flat-square" alt="Next.js" />
  <img src="https://img.shields.io/badge/SQLite-WAL%20%2B%20FTS5-003B57?logo=sqlite&style=flat-square" alt="SQLite" />
  <img src="https://img.shields.io/badge/i18n-中%20%7C%20繁%20%7C%20EN%20%7C%20JA-purple?style=flat-square" alt="i18n" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License" />
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Docker-brightgreen?style=flat-square" alt="Platform" />
</p>

> **数据与算力彻底解耦，告别单一模型绑定**：
> AgentHub Local 是专为极客与研发团队打造的**本地优先（Local-First）全场景智能体底座**。
> 所有会话历史、上下文快照、工作草稿、专属技能规程与长期记忆 100% 存留于本地原生 SQLite 数据库（WAL 并发模式 + FTS5 trigram 倒排全文检索）。无论接入本地私有大模型（Ollama、vLLM、Llama.cpp、Nvidia 实例），还是外部商业与开源 API（DeepSeek、Claude、OpenAI、SiliconFlow、Google AI），均充当无状态算力执行节点，零数据外泄，实现真正的个人私有化 AI 智能枢纽。

---

## ⚡ v1.1.4 技能加载极速性能优化与 0ms 响应

针对本地挂载数百个四元组 Skill 后的列表加载与弹出延迟问题，完成前后端全链路架构升级：
1. **后端轻量摘要 + 三级高速缓存 (`SkillService` & `SkillLoader`)**：
   - `GET /api/v1/skills` 与 `GET /api/v1/extensions/skills` 默认仅读取 `SKILL.md` 前 4KB 解析 YAML Frontmatter 摘要（响应体积缩减 **>98.9%**，400 个 30KB 技能从 `10.6MB` 降至 `103.4KB`）；
   - 新增 **文件 `(mtime_ns, size)` 缓存 + 根目录 `mtime` 签名 10 秒列表快照 + 写入/删除 Epoch 联动失效**，热请求响应压至 **`<1ms`**（实测 `0.18ms ~ 0.23ms`），并支持 `?refresh=true` 穿透强刷；
   - 全部磁盘扫描与 Prompt 摘要组装通过 `asyncio.to_thread` 卸载执行，搭配 `WorkspaceRepository.get_root_path_by_id()` 轻 SQL 查询，零阻塞异步事件循环。
2. **SOP / 说明书按需懒加载与 O(1) 直达**：
   - 新增 `GET /api/v1/extensions/skills/{skill_id}`，并在 `SopDrawer`、`CommandAutocomplete` 与 `MemoryHubModal` 中改为点击具体技能时按需拉取完整 Markdown 正文（单条详情实测 `<1ms`）。
3. **前端 SWR 0ms 秒开、静默预热与分批渲染**：
   - 新增 `frontend/src/utils/skillCache.ts`（内存 + `sessionStorage` 双层 SWR 缓存与并发去重），应用初始化及工作区切换时后台静默预热，点击 `/ 技能` 弹窗 0ms 即时渲染；
   - 修复 `CommandAutocomplete` 输入 `/` 后每敲字重复扫盘问题，并分别对 `SkillPickerModal`（首屏 80 条 + 展开全部）与斜杠下拉列表（上限 60 条）进行大列表 DOM 渲染裁剪。

---

## 🛠️ v1.1.3 稳定性与交互体验修复

基于 `v1.1.2`「谋定而后动」底座，修复高频交互细节并提升状态稳定性：
1. **弹窗状态与滚动位置锁定**：解耦弹窗 ESC 监听、初始化逻辑与外部回调函数及后台 3 秒静默轮询的副作用依赖，彻底解决在「记忆中枢 / 性格与 Skill 选择」卡片切换、设置弹窗、`AccountModal` 切页或向导步骤操作时，选项或滚动位置意外跳回初始状态的问题；同时 `MemoryHubModal` 仅在首次加载显示骨架占位，静默刷新不卸载已渲染列表。
2. **模型手动选择持久化保护**：在主工作台通过 `hasUserSelectedModelRef` 记录用户手动切换的模型（如自定义算力或不同版本模型），防止后台账号池自动刷新时将模型重置为默认推荐项。
3. **流式交互对齐卡 React Hooks 修复与防抖**：调整 `InteractiveInquiryCard` Hook 调用顺序消除条件 Hook 违规，并以 `hasInitializedRef` 确保流式 JSON 从半包转为完整数据时仅初始化一次默认选项，不会覆盖用户的即时勾选。

---

## 🚀 v1.1.2 重大升级：谋定而后动 (Plan-First) 与交互式对齐机制

在 `v1.1.0` 自主循环（Loop Engineering）与工作区底座之上，`v1.1.2` 引入**前置意图对齐（Pre-Execution Alignment）**机制，彻底解决 Agent 盲目动手写盘破坏代码与来回返工痛点：
1. **模式降维与策略切换**：
   - ⚡ 任务模式下支持 `[⚡ 直接执行 | 📋 谋定而动]` 策略切换；
   - 💬 对话模式下提供 `[🧭 需求澄清]` 开关；
2. **物理只读硬门控 (Gatekeeper)**：
   - 方案未获批准前，系统物理拦截写盘与终端执行工具，仅暴露 L0 安全只读工具，允许静默扫描代码以精准定位真实路径与行号；
3. **交互式对齐协议 (`interactive_inquiry`) 与流式骨架屏**：
   - 流式半包防抖渲染呼吸骨架屏；支持单选、多选与留空填空模板，支持单题点击直发与组合提交；
4. **留空填空模板与 `Tab` 键跳跃**：
   - 标准语法 `{{参数名:默认值}}`，一键填入输入框后按 `Tab` 键在占位符之间自动循环跳选覆盖；
5. **无缝衔接 Loop 引擎**：
   - 用户点击【🚀 批准方案并开始执行】后，自动将步骤清单写入 `.agenthub/scratchpad.md`，点亮 LoopHUD 并解锁具身执行。

---

## 🚀 v1.1.0 重大架构演进 (What's New in v1.1.0)

从 `v1.0.0` 单会话工具升级至 `v1.1.0` 项目级工作区中枢 + 无人值守调度 + 具身自愈底座：

```mermaid
flowchart TD
    subgraph UI ["全景信息架构 (Google Gemini 式极客降噪)"]
        WS["📂 工作区上下文切换器 (Workspace Switcher)<br/>(通用虚拟沙盒 / 本地已有物理目录零迁移挂载)"]
        Hub["🧭 核心功能枢纽<br/>[💬 对话与任务 | 📚 库 | ⏱️ 已安排 | 🧩 技能与应用]"]
        Mode["💊 模式二元重构<br/>[💬 对话模式] (只读探讨) | [⚡ 任务模式] (具身执行)"]
        HUD["🛰️ Loop 自主领航舱 (HUD)<br/>(DAG步骤指示器 · DoD质检灯 · 暂停/恢复/强停)"]
        Gate["🛡️ HITL 敏感操作四按键审批卡片<br/>(✅批准本次 | 🛡️本任务白名单 | ✏️改参另存为 | ❌拒绝改道)"]
    end

    subgraph CoreEngine ["微内核与自主循环引擎 (Loop Engineering)"]
        LoopEng["🔄 TaskLoopRunner 状态机<br/>(Planner -> Executor -> Verifier -> Reflector)<br/>错误指纹防死循环熔断保护"]
        Policy["🛡️ PolicyInterceptor 三级敏感操作网关<br/>🟢 L0只读放行 / 🟡 L1工作区受控快照写 / 🔴 L2高危阻断挂起"]
        FTS["⚡ 零拷贝增量 FTS5 引擎<br/>(.gitignore 过滤 + 水位线防抖 + BM25 就地召回)"]
        Skills["🧩 双层 Skill 沙盒与同名 Override<br/>(L1 全局底座技能 vs L2 工作区专属私有技能)"]
        Artifacts["📚 资产库自动注册器<br/>(落盘自动归档 + Windows 原生资源管理器高亮定位)"]
        Sched["⏰ 计划任务后台守护器<br/>(Cron/日常周期调度 + Windows 原生 Toast 桌面通知)"]
    end

    WS --> FTS
    WS --> Skills
    Mode --> LoopEng
    LoopEng --> Policy
    Policy --> Gate
    LoopEng --> HUD
    LoopEng --> Artifacts
    Sched --> UI
```

---

## 🌟 核心特性矩阵

### 1. 📂 通用工作区机制 (Workspace Hub & 物理目录绑定)
* **零业务硬编码**：全面支持用户自定义任意名称、任意业务属性的工作组（如 Web 全栈、自动化运维、法律/财务核算、个人知识库等）。
* **双模态工作区**：
  * **托管虚拟工作区**：用于临时问答与实验，数据安全存放于沙盒内；
  * **本地物理目录工作区**：直接挂载电脑上已有的代码或资料目录，**零迁移成本接入**。
* **物理 CWD 强锁定与防越界**：Agent 执行命令与写入文件严格锁定在工作区根路径下，禁止跨目录越界写；提供严格路径越界安全防御。
* **专属背景契约 (`WORKSPACE.md`)**：每个工作区可维护专属的技术栈规范或业务术语，AI 问答天然贴合项目语境。

---

### 2. 🔄 Loop 自主循环工程与防死循环熔断 (Loop Engineering)
* **`Planner -> Executor -> Verifier -> Reflector` 自驱状态机**：
  * **Planner**：自动拆解复杂目标为 DAG 子任务，实时同步写入 `<workspace>/.agenthub/scratchpad.md`；
  * **Executor**：在工作区物理路径下具身执行工具或命令；
  * **Verifier**：独立客观验收 (DoD 判定与返回码校验)；
  * **Reflector**：遭遇未通过或报错时自愈反思并调整策略。
* **错误指纹死循环熔断**：提取 `hash(error_type + error_message)` 错误指纹，**连续 3 次遭遇相同错误自动触发熔断保护**，杜绝死循环空转与 Token 浪费。
* **🧭 顶部 Loop 自主领航舱 (HUD)**：置顶展示子任务 DAG 步进、DoD 质检状态灯、当前迭代轮次，支持随时一键 `[暂停]`、`[继续]`、`[强停]`。

---

### 3. 🛡️ 三级敏感操作拦截网关与 HITL 人工审批 (PolicyInterceptor)
* 🟢 **L0（只读安全）**：文件读取、目录扫描、代码分析、无副作用查询 $\rightarrow$ 静默自动放行；
* 🟡 **L1（工作区受控写）**：在工作区物理路径内新建或修改文件 $\rightarrow$ 执行前自动在 `<workspace>/.agenthub/snapshots/` 创建轻量时间戳备份快照后放行；
* 🔴 **L2（高危拦截）**：跨目录越界写入、物理删除文件、不可逆破坏性 Shell 指令 $\rightarrow$ 挂起当前循环并呼出审批卡片：
  * **✅ 批准本次 (Approve Once)**：仅放行本次调用；
  * **🛡️ 本任务白名单 (Approve All)**：在当前任务生命周期内对该工具免密放行；
  * **✏️ 改参另存为 (Modify Args)**：人工在线修改参数并放行；
  * **❌ 拒绝并改道 (Reject & Reroute)**：驳回操作并附带引导建议，由 Reflector 重新规划。

---

### 4. ⚡ 本地物理目录零拷贝增量 FTS5 引擎 (Incremental FTS5)
* **智能规则过滤**：扫描前强制解析用户目录的 `.gitignore` 与 `.agenthubignore`，自动剔除 `node_modules`、`.git`、`dist`、超大日志及二进制文件；
* **增量水位线与防抖**：基于文件 `mtime + size + sha256` 维护水位线，仅对改动文件建立 trigram BM25 全文分词索引；
* **存储完全隔离**：索引统一存放于 `<workspace>/.agenthub/index.db`，绝不污染用户工程源码。

---

### 5. 🧩 双层 Skill 沙盒与同名高优 Override
* **L1 底座全局 Skill (`skills/`)**：随程序分发的通用环境体检、代码规范与文档助手；
* **L2 工作区私有 Skill (`<workspace>/skills/` 与 `<workspace>/.agenthub/skills/`)**：仅当前工作区可见，可随项目 Git 仓库协同提交；
* **同名覆盖法则**：L2 存在同名 Skill 时自动覆盖 L1，并在 UI 上标出 `[工作区重写]`；
* **`/` 极速响应与 SOP 按需抽屉**：采用 4KB Frontmatter 轻量摘要 + 列表级快照缓存与前端 SWR 0ms 秒开缓存，海量技能下输入 `/` 或点击技能按钮瞬间弹出，点击 `[📖 SOP]` 再按需懒加载完整执行规程。

---

### 6. 📚 资产库统一归档与会话双向溯源 (LibraryView)
* **自动登记**：对话中使用 `[📥 一键落盘到工作区]` 或由 Loop 任务生成的文件、报表与可视化图表，自动向资产库登记元数据；
* **原生定位**：点击卡片一键唤起 Windows 原生资源管理器（`explorer /select`）高亮定位目标物理文件；
* **双向溯源**：资产可反查生成它的来源会话与 Prompt 历史。

---

### 7. ⏰ 无人值守计划任务中枢 (ScheduledView)
* **定时静默调度**：支持以 Cron 表达式或日常周期（daily、hourly、每周一等）定义计划任务；
* **桌面原生通知**：任务在后台执行完成后，自动调用 Windows 原生 Toast 桌面通知，实时提醒执行概况；
* **在线测试**：支持在控制台点击 `[立即测试]` 并在后台静默跑通全流程。

---

### 8. 🌍 全景四语种国际化 (i18n)
* 全量交互组件、工作区切换器、Loop 领航舱、审批卡片、SOP 抽屉及计划任务均已在 `i18nDictionary.ts` 中维护映射：
  * **简体中文 (zh-CN)**
  * **繁体中文 (zh-TW)**
  * **英语 (en)**
  * **日语 (ja)**

---

## 📦 多客户端离线包下载与运行

在 GitHub Releases 页面，我们为不同操作系统与使用场景提供了预编译离线包：

| 客户端版本 | 适用系统 | 包名称 | 运行方式 |
| :--- | :--- | :--- | :--- |
| **Windows 独立便携桌面版** | Windows 10/11 x64 | `AgentHub-Local-v1.1.4-windows-x64.zip` | 解压后双击 `启动AgentHub桌面版.bat` 即开即用 |
| **跨平台便携运行包** | macOS / Linux / Win | `AgentHub-Local-v1.1.4-portable.tar.gz` | 解压后执行 `python desktop_app.py` 或 `uvicorn` |
| **Docker 容器镜像** | 跨平台容器环境 | `docker-compose.yml` | `docker compose up -d` |

---

## 🛠️ GitHub Releases 自动打包机制 (CI/CD)

本项目配置了完整的 GitHub Actions 自动化打包流水线（`.github/workflows/release.yml`）：

```mermaid
sequenceDiagram
    autonumber
    actor Dev as 开发者
    participant Git as 本地 Git 仓库
    participant GH as GitHub Remote
    participant Actions as GitHub Actions Runner (windows-latest)
    participant Releases as GitHub Releases 页面

    Dev->>Git: git tag v1.1.4
    Dev->>GH: git push origin v1.1.4
    GH->>Actions: 触发 release.yml 流水线
    Actions->>Actions: 安装 Node 20 & Python 3.12
    Actions->>Actions: 编译并导出 Next.js 静态前端 (out/)
    Actions->>Actions: 打包 Windows 便携包 (*-windows-x64.zip)
    Actions->>Actions: 打包跨平台源码包 (*-portable.tar.gz)
    Actions->>Releases: 自动创建 Release v1.1.4 并上传分发产物
    Releases-->>Dev: 用户可在页面直接下载执行文件
```

### 如何发布新版本：
只需在本地执行以下三条命令：
```bash
git tag v1.1.4
git push origin master
git push origin v1.1.4
```
推送后，GitHub 会在云端全自动执行编译、矩阵打包并挂载 release 附件，几分钟内即可在 Releases 页面看到生成的下载文件！

---

## 💻 源码本地启动与开发

如果您希望从源码启动开发或调试：

### 1. 环境准备
- Python $\ge$ 3.10
- Node.js $\ge$ 18

### 2. 安装依赖
```bash
# 后端依赖安装
pip install -r requirements.txt

# 前端依赖安装
cd frontend
npm install
cd ..
```

### 3. 一键启动
```bash
# Windows 用户直接双击运行：
启动AgentHub桌面版.bat

# 或命令行方式启动原生桌面窗体：
python desktop_app.py
```

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源发布。
欢迎提交 Issue 与 Pull Request 共同完善 AgentHub Local 极客生态！
