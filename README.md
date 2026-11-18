# AgentHub Local (本地多源算力中心与自主智能体工作台)

<p align="center">
  <img src="https://img.shields.io/badge/Version-v1.5.5-emerald?style=flat-square" alt="Version" />
  <img src="https://img.shields.io/badge/Edition-Strata%20Edition-indigo?style=flat-square" alt="Edition" />
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&style=flat-square" alt="Python Version" />
  <img src="https://img.shields.io/badge/FastAPI-0.111%2B-009688?logo=fastapi&style=flat-square" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Next.js-14.2-black?logo=next.js&style=flat-square" alt="Next.js" />
  <img src="https://img.shields.io/badge/SQLite-WAL%20%2B%20FTS5-003B57?logo=sqlite&style=flat-square" alt="SQLite" />
  <img src="https://img.shields.io/badge/i18n-中%20%7C%20繁%20%7C%20EN%20%7C%20JA-purple?style=flat-square" alt="i18n" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License" />
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Docker-brightgreen?style=flat-square" alt="Platform" />
</p>

> **数据与算力彻底解耦，告别单一模型绑定**：
> AgentHub Local（`v1.5.5 · Strata Edition`）是专为极客与研发团队打造的**本地优先（Local-First）全场景智能体底座**。
> 所有会话历史、上下文快照、工作草稿、专属技能规程与长期记忆 100% 存留于本地原生 SQLite 数据库（WAL 并发模式 + FTS5 trigram 倒排全文检索）。无论接入本地私有大模型（Ollama、vLLM、llama.cpp），还是外部商业与开源 API（DeepSeek、Claude、OpenAI、Google AI、Antigravity CLI），均充当无状态算力执行节点，零数据外泄，实现真正的个人私有化 AI 智能枢纽。

---

## 🏗️ 核心架构视图 (Architecture Overview)

```mermaid
flowchart TD
    subgraph UI["全景交互层 (Next.js 14.2 + React 18 + Strata 设计)"]
        WS["工作区切换器 WorkspaceSwitcher (虚拟沙盒 / 本地物理目录)"]
        Mode["模式二元重构 TopModeSwitcher (对话模式 / 任务模式 + 澄清向导)"]
        HUD["Loop 自主领航舱 LoopHUD (会话强隔离 / DAG 步进 / DoD 质检)"]
        Cards["交互卡片群 (ApprovalCard / DiffViewerCard / DeliverablesCard / TokenMetricsFooterBar)"]
        Views["功能枢纽 (LibraryView / ScheduledView / SkillsView / MemoryHubModal)"]
        SysUI["系统治理与升级 (RightActionDrawer 硬件监控 / UpdateModal 自动升级)"]
    end

    subgraph Engine["后端微内核与多源算力引擎 (FastAPI + aiosqlite)"]
        Runtime["AgentRuntime 执行主控 (盘中纠偏 / 伪落盘拦截 / 独立交付拆分)"]
        LoopEng["TaskLoopRunner 状态机 (Planner -> Executor -> Verifier -> Reflector + 错误指纹熔断)"]
        Policy["PolicyInterceptor 三级网关 (L0 只读 / L1 快照写 / L2 高危 HITL 阻断)"]
        Pool["EnginePoolService 算力池 (多通道冷却熔断 / CLI 动态模型嗅探 discover_cli_models)"]
    end

    subgraph MemorySurgery["上下文工程、代码手术刀与双回滚时光机"]
        Ctx["三区上下文与 KV Cache 守护 (ContextBuilder + LocalContextCompressor)"]
        Mem["分层记忆与 L5 实战微经验库 (FactManager + EpisodicExperienceStore)"]
        FTS["零拷贝增量 FTS5 引擎 (workspace_fts.py)"]
        Surgery["Code Surgery 代码手术刀 (ast_skeleton.py + code_surgery.py)"]
        Rollback["双回滚时光机 (git_checkpoint.py 单步快照 + ShadowCheckpointManager 全会话回滚)"]
    end

    subgraph EcoMonitor["扩展生态、实时监控与自动化升级"]
        MCP["远程 MCP 与跨网协同 (MCPManager Streamable HTTP/SSE + mcp_file_bridge)"]
        Skills["双层 Skill 沙盒与自进化 (skill_loader.py + SkillAuditor + experience_distiller)"]
        Probe["CrossPlatformHardwareProbe (collector.py / nvidia-smi / 网络吞吐)"]
        Sched["计划任务守护器 (task_scheduler.py Cron + Toast)"]
        Updater["AutoUpdaterService (GitHub Releases 检查 / 流式下载 / 无版本号覆盖重启)"]
    end

    WS --> FTS
    WS --> Skills
    Mode --> Runtime
    Runtime --> LoopEng
    Runtime --> Pool
    LoopEng --> Policy
    Policy --> Cards
    LoopEng --> HUD
    Runtime --> Ctx
    Ctx --> Mem
    Runtime --> Surgery
    Surgery --> Rollback
    Runtime --> MCP
    Probe --> SysUI
    Updater --> SysUI
    Sched --> Views
```

---

## 🌟 核心技术方案与特性矩阵

### 1. ⚡ 多源算力池化、CLI 动态模型识别与全链路水表
* **多源无状态算力调度 (`EnginePoolService` · `gemini_client.py`)**：
  * 统一池化本地私有引擎（Ollama、vLLM、`llama.cpp` / `llama-server`）与云端 API（DeepSeek、Claude、OpenAI、Google AI、Antigravity CLI）。
  * 内置模型级 **180 秒冷却熔断锁**与地区网络受限 **300 秒隔离**，遭遇 429 或连续 503 自动切换备选通道；自适应 Kimi/Moonshot 等模型参数（如 `temperature=1`）。
* **CLI 账号模型动态嗅探与聚合 (`agent_runner.py` · `ModelSelector` · `SettingsModal`)**：
  * 新增 `discover_cli_models` 与 `parse_agy_models_output`，按活跃 CLI 账号隔离目录（`GEMINI_CLI_HOME`）实时执行 `agy models` 探测已解锁模型，自动过滤无权限模型。
  * 自动将多思考档位变体（`-low` / `-medium` / `-high` / `-xhigh`）聚合为主模型条目，保留无后缀单档位模型；`resolve_agy_model_flag` 支持动态变体映射与档位平滑回退，支持未来新模型免改代码自动识别。
  * 在「设置 → 模型」提供「刷新识别模型」按钮（对接 `GET /api/v1/models/active?refresh=true`），引入 `known_candidate_ids` 感知新解锁模型自动浮现。
* **全链路性能微水表 (`TokenMetricsFooterBar`)**：
  * 深度适配 `llama-server`（`/health`、`/props`、`/tokenize`、`timings`），每条回复底部渲染单行实时水表，展示上下文水位、KV Cache 命中率、脱水节省比例及 TTFT / TPS。

---

### 2. 📂 通用工作区机制与零拷贝增量 FTS5 引擎
* **双模态工作区 (`WorkspaceSwitcher`)**：
  * **托管虚拟工作区**：用于临时问答与实验，数据安全存放于隔离沙盒内。
  * **本地物理目录工作区**：直接挂载电脑上已有的代码或资料目录，**零迁移成本接入**。
* **物理 CWD 强锁定与专属契约 (`WORKSPACE.md`)**：Agent 执行命令与文件写入严格锁定在工作区根路径下，禁止跨目录越界写；支持为每个工作区维护 `WORKSPACE.md` 技术栈与业务术语契约。
* **零拷贝增量 FTS5 (`workspace_fts.py`)**：扫描前自动解析 `.gitignore` 与 `.agenthubignore`，基于 `mtime + size + sha256` 水位线防抖建立 trigram BM25 全文分词索引，索引独立存放于 `<workspace>/.agenthub/index.db`，绝不污染工程源码。

---

### 3. 🔄 Loop 自主循环工程与会话强隔离
* **`Planner -> Executor -> Verifier -> Reflector` 自驱状态机 (`loop_runner.py`)**：
  * **Planner** 拆解目标为 DAG 子任务并同步写入 `<workspace>/.agenthub/scratchpad.md`；**Executor** 具身执行；**Verifier** 进行 DoD 独立验收；**Reflector** 遇错自愈调整策略。
  * **错误指纹死循环熔断**：提取 `hash(error_type + error_message)`，连续 3 次遭遇相同错误自动触发熔断保护。
* **Loop 领航舱会话强隔离 (`LoopHUD`)**：
  * `GET /api/v1/loop/workspace/{ws_id}/active` 与前端 SSE 分发按 `conversation_id` 强隔离，切换对话或空白会话瞬间清零领航舱；采用三层纯闲聊判定与惰性建 Loop，纯文本闲聊彻底零触发、零闪烁。
* **需求澄清与自动升舱 (`InteractiveInquiryCard`)**：在对话模式下启用需求澄清后，支持分步确认引导（含 `Tab` 跳选占位模板）；用户点击批准计划或立即执行时，自动升舱为 Agent 执行态并物理落盘。

---

### 4. 🛡️ 三级敏感操作网关、HITL 四键审批与 15s 倒计时自动封锁
* **三级敏感操作拦截网关 (`policy_interceptor.py` · `runtime.py` · `ApprovalCard`)**：
  * 🟢 **L0（只读安全）**：读取、扫描与无副作用查询 $\rightarrow$ 静默自动放行。
  * 🟡 **L1（工作区受控写）**：工作区内新建或修改文件 $\rightarrow$ 自动创建轻量安全快照后放行。
  * 🔴 **L2（高危拦截）**：跨目录越界写、物理删除、高危 Shell 指令（如 `rm -rf`, `del /f`, `format`, `drop database` 等） $\rightarrow$ **程序级硬挂起循环**，向前端下发 `waiting_approval` 事件并呼出四键审批卡片：
    * ① **批准本次 (approve_once)**：仅对当前这步操作豁免放行；
    * ② **本任务白名单 (approve_all)**：将该工具或特征加入会话白名单，后续免审批；
    * ③ **改参另存为 (modify_args)**：支持在前端直接编辑 JSON 目标参数（如改存至安全目录），以新参数放行执行；
    * ④ **拒绝并改道 (reject)**：驳回高危操作并输入改道建议，驱动模型反思换用安全方案。
  * ⏱️ **15 秒超时自动封锁**：审批卡片集成平滑倒计时进度条，**若 15 秒内无任何人工操作，系统自动判定为驳回并执行物理封锁**，彻底杜绝无值守场景下的越权风险。
* **CLI 原生工具越权自愈与长流抗卡死 (`agent_runner.py` · `shell_executor.py`)**：
  * 拦截 CLI 原生越权工具调用并触发 `cli_native_tool_denied`，在 `AgentRuntime` 内进行同轮最多 2 次无感盘中纠偏，引导模型回归标准 `<tool_call>` 协议。
  * `_AsyncLineReader` 配备 16 MiB 子进程缓冲区与 300,000 字符安全限流；`LocalShellExecutor` 对超过编码阈值的超长脚本（Windows `-EncodedCommand` > 24 KB / POSIX > 64 KB）自动转为临时 `.ps1`/`.sh` 文件执行并清理，彻底消除 `[WinError 206]`。

---

### 5. 🎯 专家防幻觉与严谨度配置系统 (Grounding & Anti-Hallucination)
* **双模式严谨度动态调谐 (`GroundingConfig` · `context_builder.py` · `runtime.py`)**：
  * **🎯 严谨工作模式**：专为工作任务（日报归纳、数据审计、Excel报表、PPT架构、代码审查）设计，默认强制锁定 `temperature=0.1`，高密度注入事实唯一基准铁律；
  * **💡 自由构想模式**：适用于创意脑暴与文案生成，默认 `temperature=0.7`，兼顾灵活性与创造力；
  * **推理模型能力嗅探**：自适应识别固定温度推理模型（如 o1、o3、DeepSeek-R1），避免强设 temperature 导致 API 400 报错。
* **程序级真实工具调用核验状态机 (`verify_tool_call`)**：
  * 杜绝“未调工具却谎称已完成”的凭空捏造。单轮内严格追踪真实工具调用状态机，若模型无有效工具调用却输出“已为您查询/已收集整理完毕”等虚假声明，后端直接硬拦截并驳回重新生成。
* **空数据 Fail-Fast 熔断机制 (`fail_fast_on_missing`)**：
  * 当检索日志或文件返回空数据（`[]`、`""`、`FileNotFoundError`）时，系统自动切断模型的惯性脑补，强行执行拒答熔断，要求必须如实告知用户客观事实。
* **官方专家预设全量加固与数据审计最小权限**：
  * 官方 7 大核心专家（数据审计、Excel 报表、PPT 架构、代码架构师等）全量预设专属防幻觉与严谨度配置，数据审计专家工具集强制锁定为只读，禁用任何命令执行与随意写盘。
* **单色扁平化视觉标准 (Monochrome Flat Design)**：
  * 全面摒弃高饱和彩色标签与彩绘图块，全界面改用灰白单色几何图标（`Shield`, `Sliders`, `FileCheck`）与极简暗色细线框，沉稳专业。

---

### 6. 🔪 Code Surgery 精准代码手术刀与 AST 语法门禁
* **Tree-sitter 代码大纲与语法门禁 (`tools/ast_skeleton.py`)**：
  * 轻量提取函数、类、方法签名与结构大纲，避免整文件读取浪费上下文；写盘前强制执行 `validate_syntax_before_write`（Python / JS / TS），阻断语法损坏写入。
* **带行号切片读取与三级容错局部补丁 (`tools/code_surgery.py`)**：
  * `read_local_file_sliced` 支持 `start_line` / `end_line` 窗口读取与 `123 |` 行号对齐。
  * `apply_search_replace_blocks` 支持多块 `<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE` 原子补丁，提供 **Level 1 精确匹配 $\rightarrow$ Level 2 缩进与空行容错对齐 $\rightarrow$ Level 3 相似度诊断提示**，并回传标准 Unified Diff。
* **行级红绿 Diff 卡片与一键撤销 (`DiffViewerCard`)**：前端直观渲染行级增删比对、快照 Tree Hash 与「一键撤销修改」交互按钮。

---

### 7. 🧠 三区上下文压缩、L5 实战微经验库与影子 Git 双回滚时光机
* **三区上下文工程与 KV Cache 守护 (`context_builder.py` · `context_compressor.py`)**：
  * 物理划分 **Zone 1（恒定不动前缀）+ Zone 2（只增不改热区 + 分块坍缩）+ Zone 3（跨轮零残留尾注）**，结合 36k 云端软上限、44K/48K CLI 防爆阀、同路径历史写入折叠与两级漏斗脱水（超长工具输出转为 `ToolRef` + `read_tool_ref` 按行回查），最大化 KV Cache 命中率。
* **四层记忆体系与 L5 静默实战微经验库 (`experience_store.py` · `fact_manager.py` · `long_term.py`)**：
  * 整合滑动窗口摘要（`session_summary.py`）、双时间轴确定性事实（`FactManager`）、180s 闲置水位锁长时提炼（`long_term.py`）与只增不改行为账本（`ledger.py`），全程经 `MemoryPolicyGuard`（`policy_guard.py`）脱敏凭据。
  * **L5 `EpisodicExperienceStore`**：基于 SQLite `FTS5 trigram` 与**三路正交锚点（实体标签 + 报错特征码 + 口语同义词簇）**，在 Zone 3 按阈值分级注入（`>= 0.80` 强采纳、`0.65~0.80` 参考提示、`< 0.65` 过滤）；工具遇错时即时检索避坑解法；回合结束后静默提炼微经验并执行复用加权（`+0.03`）与失败降权（`-0.15`）达尔文淘汰。
* **影子 Git 双回滚时光机与 0-Token 斜杠指令 (`git_checkpoint.py` · `checkpoint_manager.py`)**：
  * **单步快照**：`git_checkpoint.py` 通过 `agenthub_shadow.index` 毫秒级生成 40 位 Tree Hash 快照，配合 `POST /api/v1/workspaces/{id}/rollback` 一键单步还原。
  * **全会话时光机**：`ShadowCheckpointManager` 在 `<workspace>/.agenthub/shadow_git` 维护独立镜像仓库，支持 `/ckpt`、`/rollback`、`/remember`、`/distill` 零 Token 斜杠管理命令，`/rollback` 可原子回滚磁盘源码、草稿纸、SQLite 消息与行为账本。

---

### 8. 🧩 技能高内聚体系、操作系统静默适配与无标化
* **高内聚核心模块精简与双层隔离 (`skill_loader.py` · `SkillsView` · `SopDrawer`)**：
  * 摒弃细碎分散脚本，将 30+ 零碎技能重构收敛为 15 个高内聚核心模块（Windows 体系全量合并至 `windows-system-operator`，工程与架构收敛至 `code-architecture-review` 等）；
  * 支持 **L1 全局底座 Skill（`skills/`）** 与 **L2 工作区私有 Skill（`<workspace>/skills/`）**，L2 同名自动高优覆盖 L1。
* **宿主机环境静默自适应与彻底去标**：
  * 后端加载时自动感知当前操作系统（Windows / macOS / Linux），不匹配当前环境的技能完全静默隐藏；
  * 前端卡片彻底移除彩色「跨平台 / Windows」徽章，界面达到极致纯净，减少用户认知负荷。
* **供应链安全审计与无错沉淀门控 (`skill_auditor.py` · `experience_distiller.py`)**：
  * 导入三方 Skill 时通过 `SkillAuditor` 遍历 AST，阻断 `os.system`、`subprocess`、`eval`、硬编码密钥与越狱注入；
  * `evaluate_skill_distill_eligibility` 严格拦截未完成或末尾带报错的残缺轨迹，仅完整无错工作流开放沉淀，并支持语义化版本（`patch`/`minor`）原子迭代。

---

### 9. 🌐 远程 MCP 协议全栈支持与跨网安全文件协同
* **全协议 MCP 接入 (`mcp_manager.py` · `MemoryHubModal`)**：
  * 在本地 `_StdioMCPConnection` 基础上原生扩展 `_RemoteHttpMCPConnection`，全面支持 **`Streamable HTTP`**（含 `Mcp-Session-Id` 有/无状态连接、404 自动重连与异步 `DELETE` 清理）及 **`Legacy HTTP + SSE`**（强制同源校验与禁止跨站重定向防凭据泄露）。
  * 兼容 Claude Desktop（`mcpServers`）、VS Code（`servers`）与 Windsurf（`serverUrl`）配置格式导入，支持 `${ENV_VAR}` 运行时环境变量展开与 Headers 密钥脱敏掩码保护。
* **跨网安全文件协同桥接 (`mcp_file_bridge.py`)**：
  * 提供 `mcp_upload_workspace_file` 与 `mcp_download_artifact_to_workspace` 通用工具：上传支持自定义表单字段 `upload_file_field` 与体积校验；下载采用 `aiter_bytes()` 真流式体积熔断、严格 `2xx` 与反误写校验，落盘后自动注册资产库。

---

### 10. 📚 资产库归档、磁盘物理核验与独立交付呈现
* **反伪落盘幻觉与物理存在性核验 (`artifacts.py` · `AgentRuntime` · `DeliverablesCard`)**：
  * 拦截无工具纯叙事宣称已写入却未真实落盘的幻觉（触发 `stream_reset` 纠偏）；新增 `POST /api/v1/artifacts/verify-paths` 批量核验工作区文件真实大小与修改时间，零落盘气泡绝不渲染误导性绿色交付卡片。
  * 强制分离「执行过程」与「最终交付正文」，防止 `update_working_memory(task_completed=true)` 导致交付正文混入折叠区，并在底栏渲染「生成文件直达」卡片。
* **沙盒隔离上传与双向溯源 (`attachment_service.py` · `LibraryView`)**：上传文件按 SHA-256 去重隔离于 `.agenthub/attachments/`；资产库支持一键唤起系统资源管理器高亮定位物理文件，并支持反查来源会话与 Prompt。

---

### 11. 🖥️ 系统实时硬件监控与智能治理中枢
* **跨平台实时硬件探针 (`probe/collector.py` · `RightActionDrawer`)**：
  * 打开右侧「系统实时监控与智能治理」抽屉后，每 3 秒自动轮询 `/api/v1/system/snapshot`，实时展示 CPU 使用率/核数/主频、物理内存与 Swap、全分区磁盘占用及 Top 6 高占用进程。
  * `collector.get_gpu_metrics` 接入无窗口 `nvidia-smi` CLI 探测（原生适配 Windows/Linux NVIDIA 显卡负载、显存与核心温度）；新增 `get_network_metrics` 计算真实实时上/下行吞吐速率与累计流量（GB）。
  * 集成一键体检高占用进程、进程「提请治理」、端口占用排查及待审批系统治理/经验复盘清单交互闭环。

---

### 12. ⏰ 无人值守计划任务与全景四语种国际化 (i18n)
* **后台计划任务 (`task_scheduler.py` · `ScheduledView`)**：支持 Cron 表达式与日常周期调度，支持 `[立即测试]` 静默跑通，任务完成后自动调用 Windows 原生 Toast 桌面通知。
* **四语种国际化 (`i18nDictionary.ts`)**：全量界面与交互组件原生支持 **简体中文 (zh-CN)**、**繁体中文 (zh-TW)**、**英语 (en)**、**日语 (ja)**。

---

## 📦 多客户端无版本号发行包与自动升级

### 1. 预编译离线发行包
在 GitHub Releases 页面，我们为不同操作系统与使用场景提供预编译产物（自 `v1.4.2` 起，桌面端单文件与 DMG 镜像全面采用**无版本号文件名**，消除跨版本升级后本地残留旧版本号的歧义）：

| 客户端版本 | 适用系统 | 包名称 | 运行方式 |
| :--- | :--- | :--- | :--- |
| **Windows 独立单文件桌面版** | Windows 10/11 x64 | `AgentHub-Local-windows-x64.exe` | 免安装免解压，单文件双击直接运行（同级目录存数据，无权限时存 `%LOCALAPPDATA%\AgentHubLocal`） |
| **macOS 独立桌面安装版** | macOS Apple Silicon (ARM64) | `AgentHub-Local-macos-arm64.dmg` | 打开镜像将 `AgentHub.app` 拖入 `Applications`；首次运行如遇未签名提示，右键「打开」或在终端执行 `xattr -cr /Applications/AgentHub.app` |
| **轻量便携包（Windows / 通用）** | Windows / 跨平台 | `AgentHub-Local-v1.4.6-portable.zip` | 不含 Python 二进制运行库（约 `~2.2 MB`），解压装好 `requirements.txt` 后运行 `启动AgentHub桌面版.bat` |
| **轻量便携包（macOS / Linux）** | macOS / Linux | `AgentHub-Local-v1.4.6-portable.tar.gz` | 不含 Python 二进制运行库（约 `~2.2 MB`），解压装好依赖后运行 `./启动AgentHub桌面版.sh` |
| **Docker 容器镜像** | Linux 服务器 / 跨平台容器 | `docker-compose.yml` / `backend/Dockerfile` | 见下方 [Linux 服务器 Docker 部署与远程访问指南](#-linux-服务器-docker-部署与远程访问指南) |

### 2. GitHub 自动检查升级与平滑覆盖重启 (`AutoUpdaterService` · `UpdateModal`)
* **静默检查与更新提醒**：客户端启动时自动静默检查 `voodooq/agenthub-local` 最新 Release（亦支持在「设置 → 关于」手动检查），发现新版本后在侧栏与顶栏展示提醒徽标，并在 `UpdateModal` 中渲染 Markdown 升级日志。
* **流式下载与无版本号平滑落位**：点击「立即升级」自动匹配当前平台产物并流式下载，实时展示百分比进度、已下载体积与速率；内置 `.version` 缓存标记校验防止同名包跨版本复用旧缓存，并通过 `normalize_windows_target_exe` 在从历史带版本号 EXE 升级时自动重命名为 `AgentHub-Local-windows-x64.exe`。
* **数据零覆写重启**：通过 `/api/v1/system/update/*` 接口编排一键重启替换加载新版本，严格保护 `data/` 数据库与用户个人配置不被覆写。

---

## 🛠️ GitHub Releases 自动打包机制 (CI/CD)

本项目配置了跨平台并行 GitHub Actions 自动化打包流水线（`.github/workflows/release.yml`）：

```mermaid
sequenceDiagram
    autonumber
    actor Dev as 开发者
    participant GH as GitHub Remote
    participant FE as build-frontend (ubuntu)
    participant Win as build-windows-exe (windows-latest)
    participant Mac as build-macos-dmg (macos-latest)
    participant Port as package-portable (ubuntu)
    participant Rel as GitHub Releases 页面

    Dev->>GH: git tag v1.4.6 && git push origin v1.4.6
    GH->>FE: 安装 Node 22 并导出静态前端 out/
    FE->>Port: 分发 out/ -> 生成 portable.zip & portable.tar.gz
    FE->>Win: 分发 out/ -> PyInstaller --onefile 生成 AgentHub-Local-windows-x64.exe
    FE->>Mac: 分发 out/ -> PyInstaller + hdiutil 生成 AgentHub-Local-macos-arm64.dmg
    Port->>Rel: 仅过滤下载 release-* 汇聚 4 类独立资产创建 Release
    Win->>Rel: 挂载独立单文件 EXE
    Mac->>Rel: 挂载 macOS ARM64 DMG
```

### 如何发布新版本
只需在 `version.json` 更新版本号并运行 `python sync_version.py`（或直接运行 `python sync_version.py <version>`），随后在本地执行以下命令：
```bash
git tag -a v1.4.6 -m "Release v1.4.6"
git push origin master
git push origin v1.4.6
```
推送后，GitHub 会在云端全自动执行编译、矩阵打包并挂载 4 类干净的发行资产，几分钟内即可在 Releases 页面看到生成的下载文件。

---

## 💻 源码本地启动与开发指南

如果您希望从源码启动开发或调试：

### 1. 环境准备
* Python $\ge$ 3.10
* Node.js $\ge$ 18

### 2. 安装依赖
```bash
# 后端依赖安装
pip install -r requirements.txt

# 前端依赖安装
cd frontend
npm install
cd ..
```

### 3. 启动方式
```bash
# Windows 用户直接双击运行（或带控制台日志的调试模式）：
启动AgentHub桌面版.bat
启动AgentHub_调试模式(带控制台日志).bat

# macOS / Linux 用户运行：
./启动AgentHub桌面版.sh

# 或直接使用 Python 启动原生桌面窗体：
python desktop_app.py
```

---

## 🐳 Linux 服务器 Docker 部署与远程访问指南

如果您申请了在线 Linux 虚拟主机（VPS / 云服务器），推荐使用 Docker 方式部署后端与内置静态前端（仓库 `frontend/out/` 已预构建完整静态产物，可由后端 FastAPI 直接挂载托管）。

### 1. 服务器环境准备
确保 Linux 主机已安装 **Docker Engine** 与 **Docker Compose v2** 插件：
```bash
docker --version
docker compose version
```

### 2. 拉取代码并准备数据与配置
```bash
git clone https://github.com/voodooq/agenthub-local.git
cd agenthub-local

# 拷贝环境变量模板（按需修改 TZ、代理或故障转移参数）
cp .env.example .env

# 创建本地持久化目录与 CLI 账号目录
mkdir -p data ~/.agenthub/accounts
```

### 3. 一键构建并启动容器（Docker Compose 单容器全栈托管）
仓库根目录 `docker-compose.yml` 已配置为单容器全栈托管模式（自动挂载 `frontend/out` 静态产物至 `/app/static`，由后端 FastAPI 统一提供 Web UI 与 `/api/*` 服务，占用更低、无需额外前端容器）：

```bash
# 首次构建镜像并后台启动
docker compose up -d --build
```

> **提示**：
> - 若您的 Linux 主机需要通过网络代理访问外部大模型 API，可在 `.env` 中配置 `HTTP_PROXY` 与 `HTTPS_PROXY` 后执行 `docker compose up -d` 自动注入。
> - 若需拉取新版本代码并更新容器（`./data` 中的 SQLite 数据库与记忆完整保留）：
>   ```bash
>   git pull
>   docker compose up -d --build
>   ```

### 4. 安全远程访问方式（关键）
为防范 DNS 重绑定攻击与未授权访问，后端安全中间件（`LocalOnlyMiddleware`）**仅允许 `Host` 为 `localhost` / `127.0.0.1`**，且容器端口默认仅监听服务器回环地址 `127.0.0.1:8686`（**严禁直接将 8686 端口裸露到公网防火墙**）。

- **方式 A：SSH 端口转发隧道（最推荐，零公网暴露风险）**
  在您**本地电脑**的终端中执行：
  ```bash
  ssh -N -L 8686:127.0.0.1:8686 root@<您的Linux服务器IP>
  ```
  保持该终端运行，随后直接在本地电脑浏览器打开：**`http://127.0.0.1:8686`** 即可使用，系统会自动完成本机令牌（`X-Access-Token`）握手。

- **方式 B：Nginx 反向代理 + HTTPS（需公网域名访问时）**
  若必须通过公网域名访问，请务必在 Nginx 上启用 HTTPS 与 Basic Auth 访问控制，并将转发 `Host` 头重写为 `127.0.0.1`（同时开启 SSE 流式响应所需的 `proxy_buffering off`）：
  ```nginx
  location / {
      proxy_pass http://127.0.0.1:8686;
      proxy_set_header Host 127.0.0.1:8686;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_buffering off;
      proxy_cache off;
      proxy_read_timeout 600s;
  }
  ```

### 5. 常用运维与排错命令
```bash
# 查看运行日志（含启动生成的 X-Access-Token 与模型探测状态）
docker compose logs -f

# 查看容器状态或重启服务
docker compose ps
docker compose restart

# 进入容器内部调试或登录 Gemini CLI 账号
docker compose exec backend bash
```

---

## 🧭 版本演进总览

### 🚀 v1.4.12（最新）— 专家防幻觉与严谨度体系、L2 拦截 15 秒倒计时自动封锁与单色扁平化设计
- **L2 高危敏感操作 15 秒倒计时自动封锁四键审批卡（`runtime.py` · `policy_interceptor.py` · `ApprovalCard.tsx`）**：
  - 彻底打通 L2 级敏感操作（跨目录越界写、物理删除、高危 Shell 指令）程序级硬挂起；
  - 呼出单色扁平化四键审批卡（批准本次 / 本任务白名单 / 改参另存为 / 拒绝并改道），并集成 15 秒平滑递减倒计时；
  - 15 秒内无任何人工操作时，自动触发超时封锁并阻断越权执行，杜绝无人值守风险。
- **专家防幻觉与严谨度配置系统（`GroundingConfig` · `context_builder.py` · `runtime.py` · `AgentsView.tsx`）**：
  - 推出「🎯 严谨工作模式」（锁定 T=0.1，防虚构强约束）与「💡 自由构想模式」（T=0.7），支持 o1/o3/DeepSeek-R1 等固定温度推理模型能力嗅探；
  - 真实工具调用硬核验（`verify_tool_call`）：程序级追踪单轮真实工具状态机，硬拦截未调工具却谎称已完成的虚假声明；
  - 空数据 Fail-Fast 熔断（`fail_fast_on_missing`）：无数据如实拒答，禁止脑补；
  - 官方 7 大核心专家全量严谨加固，数据审计专家锁定只读最小权限白名单；
  - 全界面统一遵循单色扁平化（Monochrome Flat Icons）设计标准。

### 🚀 v1.4.11 — 技能操作系统静默自动适配与彻底无标化
- **宿主机环境静默感知与自动过滤（`skill_loader.py` · `skill_service.py`）**：
  - 加载技能时静默探测当前宿主机系统（Windows / macOS / Linux），不匹配环境的技能完全静默隐藏；
  - 彻底移除技能卡片上的彩色「跨平台 / Windows」徽章，界面极简纯净。

### 🚀 v1.4.10 — 技能体系高内聚整合与环境感知修复
- 将 30+ 零散细碎技能重构整合为 15 个高内聚核心模块，修复跨平台路径引用与坏味道。

### 🚀 v1.4.7 ~ v1.4.9 — 跨端打包、系统稳定性与版本发布流水线加固
- 优化 GitHub Actions 跨平台发行包流水线，完善环境依赖隔离与启动自愈。

### 🚀 v1.4.6 — Web / Docker 远程友好交互（浏览器内预览/下载与服务器目录浏览）
- **生成文件远程无感预览与下载（`artifacts` · `apiClient` · `page` · `DeliverablesCard` · `LibraryView`）**：
  - 新增 `_is_headless_remote_env()` 自动识别无头 Linux / Docker 容器环境，彻底消除点击生成文件时报错 `No such file or directory: 'xdg-open'`；自动切换为前端右侧画布内预览或带 `X-Access-Token` 鉴权的浏览器文件下载。
- **服务器目录浏览器替代原生弹窗（`workspaces` · `ServerFolderBrowser` · `WorkspaceSwitcher` · `Sidebar`）**：
  - 非 Windows 环境调用 `/api/v1/workspaces/select-folder` 时不再唤起 `powershell` 报错；新增 `GET /api/v1/workspaces/browse-dirs` 接口与 `ServerFolderBrowser` 组件，支持在 Web 弹窗中点选服务器/容器目录及工作区书签快速跳转。

### 🚀 v1.4.5 — 计划任务启动即巡检与动态默认模型自适应
- **后端启动自动激活 Cron 调度器（`backend/main.py`）**：在 FastAPI `lifespan` 启动钩子中自动调用 `TaskScheduler.get_instance().ensure_started()`，无需手动点开「已安排」页面即可全天候按 Cron 自动触发任务。
- **无头任务动态模型自适应（`TaskScheduler._resolve_headless_model`）**：在工作区未单独指定模型时，自动按“工作区有效偏好 → 最近活跃会话模型 → 系统当前已启用算力通道默认/首个模型”智能选模，彻底解决仅配置第三方/本地算力（如 NVIDIA、DeepSeek、Ollama）而未配置 Google 账号时无法执行计划任务的问题。

### 🚀 v1.4.4 — Token 优化 Phase 2/3（协议/规则瘦身、动态思考、读去重、旁路门控与防空转拦截）
- **S4（静态前缀与规范瘦身，`tool_protocol` · `context_builder` · `skill_loader`）**：
  - `build_xml_protocol_prompt()` 改用高密度紧凑签名格式并新增 `build_single_tool_schema_hint()`（参数出错时自动回补该单工具完整 JSON Schema）；重构 Agent 铁律与 `plan_first` 提示词；拆分 `get_skill_dev_spec_prompt()` 仅在命中技能/MCP 开发意图时按需注入，常规 Agent 轮次实测固定前缀节省 ~3,788 tokens。
- **S6（ReAct 步内动态思考档位调节，`runtime`）**：
  - 新增 `_resolve_step_thinking_level()`：常规顺畅推进步降载为 `medium`，最终汇总步与纯简单问答降载为 `low`；一旦发生空回复、解析失败或工具报错立即锁回 `high`。
- **S7（读文件去重与终端输出前置降噪，`runtime` · `shell_executor`）**：
  - 新增基于磁盘 `mtime_ns + size` 与热区窗口校验的 `session_file_read_cache` 读文件去重指针；`shell_executor` 新增 `_sanitize_terminal_output()` 零成本剥离 ANSI 颜色码、`\r` 覆盖进度条行及连续空行。
- **S8（旁路记忆启发式前置门控，`long_term` · `experience_distiller`）**：
  - 180s 闲置记忆提取增加偏好/配置信号词门控；无自愈且无人工纠偏的纯多步任务直接走规则字段落盘，免调后台 LLM。
- **S10（无写操作穿插的重复调用拦截护栏，`runtime`）**：
  - 轮内连续重复发起相同只读/失败工具调用时直接拦截提示换路，发生写操作或状态变更立即清空窗口。

### 🚀 v1.4.3 — Token 优化 Phase 1（KV Cache 连续性与 MCP 按需加载）、Linux/Docker 自动更新及计划任务真调度执行
- **Token 优化 Phase 1（`context_builder` · `context_compressor` · `runtime` · `tool_protocol` · `engine_pool` · `provider_adapter` · `mcp_manager`）**：
  - **S1（Zone 2 严格 Append-Only）**：热区渲染统一保持 Write-Once 一致性，取消常规跨轮突变为 `compressed_content` 与逐轮折叠 `write_local_file`，仅在分块坍缩或逼近硬上限时批量折叠，修复跨轮 KV Cache 击穿。
  - **S2（轮内前缀稳定 + Anthropic Prompt Caching）**：`is_forced_final_step` 保留 `active_tools` 锁死 Zone 1 并拦截出站 `<tool_call>`；生图指引与确定性 `rand_seed` 迁至 Zone 3；`stream_anthropic` 自动注入 `cache_control: {"type": "ephemeral"}`（遇 400 自动降级记忆节点）。
  - **S3（MCP 两段式目录与按需 Schema 延迟挂载）**：启用 MCP 工具超过 5 个时默认仅注入轻量目录摘要，通过内置元工具 `discover_mcp_tools` 及关键词/历史/直调单调递增激活 Server，大幅削减空闲 Schema Token 消耗。
- **Linux 与 Docker 自动更新全链路打通（`AutoUpdaterService` · `UpdateModal` · `docker-compose.yml`）**：
  - 新增 `is_running_in_docker()`、`detect_docker_update_capability()`、`_apply_docker_archive()`（精准映射 `backend -> /app`、`frontend/out -> /app/static` 及原地覆写 `version.json`）与 PID 1 优雅退出交由 `restart: unless-stopped` 自动拉起；完善 `.tar.gz` 解压保留 `.sh` `+x` 权限与 Linux 便携/源码 `uvicorn` 平滑重启。
  - 移除 `docker-compose.yml` 中 `/app/static` 与 `/app/version.json` 的 `:ro` 只读限制，并在 `UpdateModal` 中适配 Docker 运行形态与只读挂载检测提示。
- **计划任务真实 Cron 调度、无头执行与状态详情（`TaskScheduler` · `scheduled` · `ScheduledView`）**：
  - 实现纯 Python 标准 5 段 Cron 解析（含 `*`、`*/n`、`a-b`、`a,b` 及 `@hourly`/`@daily` 等别名）与 `start_loop()` 防同分钟重入自动巡检；`run_task()` 真实无头调用 `agent_runtime.chat_stream`，捕获 `running` / `success` / `failed` 状态、耗时与完整输出落库至 `scheduled_task_runs`。
  - 新增 `GET /api/v1/scheduled/{task_id}/runs` 接口；前端 `ScheduledView` 升级为展示状态徽章、上次/下次运行时间、耗时及完整执行历史详情弹窗。

### 🚀 v1.5.5 — 认知大脑、云端 LLM Wiki、输出前自审与生产治理全栈 (Strata Edition)
- **四维一体本地检索大脑（`HybridSearchEngine` · `LightGraphRAGEngine` · `AgenticRetrievalLoop`）**：
  - **三级自适应 Embedding**：Tier 1 优先探活复用本机 Ollama/`llama-server` 端点（0 MB 额外内存） $\rightarrow$ Tier 2 本地加载 24MB INT8 量化 ONNX `bge-small-zh-v1.5`（限 2 线程） $\rightarrow$ Tier 3 极限离线纯 FTS5 trigram 自动降级。
  - **Tree-sitter AST 代码与 Markdown 分块**：按完整类与函数边界切分，注入上下文面包屑头；
  - **SQLite WAL 向量存取与 NumPy 余弦点积**：原生 BLOB 存储结合纯 NumPy 矩阵点积（20,000 切片耗时 < 4ms），结合 FTS5 与 RRF 倒数排名融合，支持动态 Query 意图权重路由（代码符号 0.7:0.3，自然语言 0.3:0.7）；
  - **轻量 SQLite 图 RAG**：启发式三元组抽取，纯 SQLite 原生 `WITH RECURSIVE` 递归 CTE 执行 2~3 跳双向子图扩展（单次上限 30 条关系边），封装 `query_knowledge_graph` 工具；
  - **智能体 RAG 多跳追问自检闭环**：最多 3 跳证据链覆盖度自检，检测未展开的附件或关联规则指针并自动改写 Query 定向补全检索。
- **CloudVFS 云端虚拟知识库与时序权威冲突仲裁（`CloudVFS` · `ConflictResolver`）**：
  - **三大云端适配器**：企业微信微盘/知识库（`wecom_drive.py`）、WebDAV 兼容 Alist/坚果云/群晖 NAS（`webdav_provider.py`）、S3/阿里云 OSS/MinIO（`oss_provider.py`）；
  - **阅后即焚流式解析**：基于 `pypdf`、原生 `zipfile` DOCX 提取与 `openpyxl` 内存流式切片入库，零本地文件持久化堆积，内置 24 小时 TTL 本地 LRU 章节精读缓存；
  - **多源版本冲突仲裁**：自动判定 Tier 1~3 权威等级与业务生效时间戳 `effective_time`，归一化主题簇 `topic_cluster_id` 自动标记旧版本 `is_superseded=1`，跨源 SHA-256 去重，重排阶段叠加权威与新鲜度系数，自动包装“时空身份证”头部并显性披露版本条款冲突。
- **输出前自审与动态置信度治理（`SelfCritiqueCritic` · `ConfidenceEvaluator`）**：
  - **输出前事实核对**：严谨模式下对草稿事实、数字、日期、条款进行 Critic 自审，检出矛盾触发最多 1 次后台无感重写修订（Re-draft）；
  - **动态置信度评分（`0.00 ~ 1.00`）**：综合检索、版本一致性、Critic 与工具完整性生成分值与归因标签，通过 SSE 推送 `confidence_report`；
  - **低置信度人工介入门控（HITL）**：严谨模式下置信度 `< 0.60` 触发 `low_confidence_confirmation` 挂起确认卡片。
- **L5 经验库语义升级、无头浏览器与多模态**：
  - L5 实战微经验库持久化向量至 `experience_embeddings`，召回接入双路 RRF 并保留历史复用奖惩闭环；
  - 基于 Playwright 封装懒加载无头浏览器（5 分钟闲置自动回收），提取精简无障碍树快照与控制台日志；
  - 多模态图片结构化表格/实体提取器与轻量离线语音转写管道（按需加载 `faster-whisper`）；
  - 前端单色扁平组件 `CloudWikiMountModal.tsx`、`ConfidenceBadgeBar.tsx`、`LowConfidenceConfirmCard.tsx`（纯实底零毛玻璃穿透）。

### 🚀 v1.5.1 — MCP 服务双中枢管理、全栈严格类型安全加固与极致纯实底体验
- **MCP 服务双中枢管理（`McpServerManager` · `SkillsView` · `MemoryHubModal`）**：
  - 将原本缺失的 MCP 接入、查看、编辑、测试、删除与批量导入功能深度重构为独立通用中枢组件，并在「技能与应用 → 关联应用 (MCP)」与「记忆中枢 → 关联应用与 MCP」中实现双入口无缝互通。
  - 动态识别并直观呈现本地 STDIO 与远程 Streamable-HTTP / SSE 真实已接入服务，支持就地一键测试连通性、查看延迟毫秒数及可用工具明细；
  - 提供新建接入向导（参数/环境变量/Headers/超时配置）、就地编辑保存、启停状态切换及二次确认安全删除；全面兼容导入 Claude Desktop / Cursor / `mcp_servers.json` 配置文件。
- **全栈严格类型安全加固与编译器陷阱排查**：
  - 彻底排查并修复 `shell_executor.py` 中 `communicate()` 在复杂 kwargs 推导下的 `tuple[bytes, ...]` 类型兼容；
  - 修复 `runtime.py` 内部 `PendingApprovalCall` 强类型 Literal 构造与局部 `import json` 作用域屏蔽导致的 `UnboundLocalError`；
  - 修正 `office-ppt`（`build_ppt.py` 工厂函数与类类型区隔）与 `excel-data-suite`（`audit_table.py` 参数 `encoding_errors`、Series 掩码处理、`build_excel.py` 工作表存在性安全窄化）的类型注解。
- **全站纯实底防透光与单色扁平化（`/ui-ux-pro-max`）全面生效**：
  - 彻底拔除全站 `backdrop-blur-*` 虚化毛玻璃，全局统一遮罩为纯黑 `bg-black/80`，所有抽屉与弹窗容器施加 `opacity: 1 !important` 纯实心底色，杜绝文字透光穿透与晕影重叠；
  - 全面收敛图标为极简单色扁平（Monochrome Flat）矢量风格。

### 🚀 v1.5.0 — 后台长驻进程托管池、只读并发子智能体、防幻觉三道防线与极致单色实底 UI
- **后台长驻进程托管池（`BackgroundProcessManager` · `processes` · `RightActionDrawer`）**：
  - 赋予 Agent 异步拉起长驻任务（如 `npm run dev`、`uvicorn`、长时间数据流脚本）的闭环生命周期管理，解决传统同步等待阻塞问题。
  - 内置 1000 行固定容量环形日志缓冲区与 200 行独立错误高优环，支持按行数切片非阻塞读取；集成回环端口正则嗅探器（严格限定 `(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::\]):(\d{2,5})` 并保序去重）。
  - 支持启动 3 秒窗口早期退出崩溃快速失败检测（Fail-Fast）；跨平台子进程树连带强制销毁（Windows `CREATE_NEW_PROCESS_GROUP` + `taskkill /F /T /PID`，POSIX `killpg` 防孤儿僵尸进程）。
  - 单工作区并发上限（5个）熔断保护，集成 FastAPI `lifespan` 优雅关机回收与会话结束/工作区切换自动垃圾回收钩子；提供标准 RESTful API（`GET /api/v1/processes`、`GET /logs`、`POST /stop`）。
  - 前端在「系统监控与治理」抽屉中集成「活跃长驻托管进程」卡片与单色实时强杀按钮。
- **只读并发子智能体派发器（`SubAgentSpawner` · `dispatch_research_subagents` · `LoopHUD`）**：
  - 支持主 Agent 一次性派发 2~3 个并发子任务展开多角度源码/文档/网络只读调研，构建微型隔离上下文与纯净独立历史。
  - 核心硬编码只读工具白名单（`READONLY_SUBAGENT_TOOLS`），彻底剔除一切文件写、重构、Shell 及外设工具，杜绝并发写冲突。
  - 多源算力动态并发嗅探：检测到当前使用本地推理引擎（Ollama、`llama-server`）时自动降级为单任务串行（并发度 1）以保护本地 GPU 显存，云端模型自动启用 3 任务并发；内置 6 步与 60 秒硬超时防死锁熔断。
  - 聚合器标准化 Markdown 结论直出，严格实施 $\le 1500$ Token 单任务软截断；在 `LoopHUD` 中透出并发调研指示胶囊与全链路计费透传。
- **专家防幻觉与严谨度三道防线（`Grounding Guardrails` · `runtime` · `AgentsView`）**：
  - 专家沙盒提供「严谨工作模式」（T=0.1、事实锚定）与「自由构想模式」（T=0.7、发散创意）快速切换与采样温度微调。
  - 落地三重防线约束：未检索到数据如实拒答（Fail-Fast）、工具调用真实性审计（未调用前严禁宣称完成）、输出结论强制附带事实依据与引用。
  - 运行时程序级状态机审计：拦截未执行真实工具调用即声明任务完成的欺骗性输出。
- **全站单色扁平化（Monochrome Flat）与 100% 纯实心底色规范（`/ui-ux-pro-max`）**：
  - 全面清除前端所有抽屉与弹窗遮罩中的 `backdrop-blur-*` 毛玻璃模糊，彻底消除背景文字重影与透光穿透。
  - 全局统一遮罩为高对比度纯色压暗 `bg-black/80`，抽屉与弹窗容器强制 100% 纯实心底色（`bg-surface-card` 与 `opacity: 1 !important`），保证文字阅读对比度 $\ge 4.5:1$。
  - 全站图标全面贯彻单色扁平化设计，剔除花哨彩色图标与 emoji，回归极客沉浸体验。

### 🏛️ 历史里程碑演进 (v1.0 ~ v1.4.x 架构演进归档)
- **v1.4.x（硬件监控、远程 MCP 原生协议与动态模型识别）**：系统实时硬件监控抽屉（CPU/内存/GPU/网络吞吐/进程治理）、无版本号桌面安装包与平滑自动更新；远程 MCP 原生接入（Streamable HTTP/SSE）与跨网安全文件协同；基于 CLI 账号动态嗅探已解锁模型与一键刷新识别。
- **v1.3.x（代码手术刀、L5 实战微经验与影子 Git 双回滚）**：Tree-sitter AST 代码大纲与局部补丁手术刀（`code_surgery`）；毫秒级单步 Git 快照与全会话影子 Git 时光机；Loop 会话强隔离、交付分离与防伪落盘物理存在性核验。
- **v1.2.x（三区上下文与 KV Cache 守护、性能微水表及多源算力稳定化）**：Zone 1/2/3 物理隔离上下文与工具长输出旁路脱水；`llama-server` 深度适配与全链路单行性能微水表；180s 冷却熔断与长文本半屏编辑。
- **v1.1.x（项目工作区中枢、Loop 自主循环与四层记忆治理）**：虚拟沙盒与物理目录零迁移挂载；增量 FTS5 trigram 倒排全文检索；Planner-Executor-Verifier-Reflector 状态机与 L0/L1/L2 三级敏感操作 HITL 审批网关；四层本地记忆体系。
- **v1.0.x（本地多源算力解耦与自主智能体底座奠基）**：原生 SQLite WAL 纯本地持久化；本地私有引擎（Ollama/vLLM/llama.cpp）与商业 API 池化解耦；桌面级原生工作台与多语言国际化支持。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源发布。
欢迎提交 Issue 与 Pull Request 共同完善 AgentHub Local 极客生态！
