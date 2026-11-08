# Loop 自主领航舱状态联动与技能闭环优化任务清单

- [x] **任务 1：改造后端数据仓储层 `backend/app/repository/loop_run_repository.py`**
  - [x] 在 `create_loop` 时自动归档当前工作区的所有未完结旧 Loop（设为 completed），避免孤儿任务挂起
  - [x] 优化 `get_active_loop_by_workspace`，当存在已交付的 Loop 时，支持返回最新的一条（避免 UI 瞬间断档）
  - [x] 新增 `record_tool_step` 方法，支持快速追加工具执行指令并递增 `current_iteration`

- [x] **任务 2：改造核心微内核 `backend/app/agent/runtime.py` 实现执行态实时联动**
  - [x] 在 `chat_stream` 初始阶段通过 `workspace_id` 与 `conversation_id` 自动关联对应的 `active_loop`
  - [x] 在工具执行阶段（如 shell 命令、文件读写等），每完成一步自动将命令执行摘要追加到记事板，轮次递增，并推送 `loop_step` SSE 事件
  - [x] 在流式输出完成（即模型给出最终自然语言结论，无更多工具调用）时，自动触发 Verifier DoD 校验
  - [x] 将 Loop 状态置为 `completed`，把所有 DoD 打勾完成，推送 `loop_completed` SSE 事件并自动登记资产库

- [x] **任务 3：增强状态机执行器 `backend/app/agent/loop_runner.py` 与接口 `backend/app/api/v1/loop.py`**
  - [x] 优化 `step_verify`，完善动态 Markdown 生成，将执行记录、DoD 全部打勾并持久化到本地 `.agenthub/scratchpad.md`
  - [x] 支持通过 `/api/v1/loop/{id}/action` 传入 `complete` 时平滑收尾

- [x] **任务 4：升级前端 `LoopHUD.tsx` 与 `page.tsx` 交互响应**
  - [x] 在 SSE 事件流监听中实时捕获 `loop_step` 和 `loop_completed` 事件，毫秒级就地更新 `activeLoop`
  - [x] 在 `LoopHUD.tsx` 中增加已完成状态展示：显示绿色 `[✅ 任务已交付并验收]` 徽标与 `[✕ 归档收起]` 按钮
  - [x] 在 `LoopHUD.tsx` 中支持展开查看工具调用的真实执行清单与 DoD 验证明细

- [x] **任务 5：资产库物理目录增量自动同步与画布预览**
  - [x] 实现 `ArtifactRepository.sync_physical_workspace_artifacts`，打开资产库时反向深度扫描工作区物理磁盘
  - [x] 打通 `write_local_file` 工具落盘即时登记 hook
  - [x] 在资产库界面增加 `[🔄 扫描同步]` 与 `[📂 打开文件夹]`，实现一键打开操作系统资源管理器

- [x] **任务 6：工作区技能双轨扫描、指令匹配与全生命周期管控**
  - [x] 深度排查并修复 `SkillService`，支持双轨扫描 `skills/` 与 `.agenthub/skills/`，以真实工程目录 `skills/` 为第一优先
  - [x] 智能过滤向导遗留的残缺占位旧技能，并物理清理沙盒工作区内遗留的历史废弃草稿
  - [x] 增加技能删除接口 `DELETE /api/v1/skills/{name}?workspace_id=...`，支持深度匹配物理目录并清除
  - [x] 前端 `SkillPickerModal.tsx` 与 `SkillsView.tsx` 严格使用真实 `sk.command`（如 `/mail-assistant`）匹配展示与回填
  - [x] 技能中枢新增 `[🔄 扫描同步]` 与工作区专属技能 `[🗑️ 删除/移除]` 按钮，SOP 抽屉增加 `[复制指令]` 与删除入口

- [x] **任务 7：多语言、CI 流水线修复与离线多端包构建发布**
  - [x] 修复 GitHub Actions 流水线 `.github/workflows/release.yml`，增加安全检查与通用路径容错
  - [x] 运行本地 `package_release.py`，成功打包 Windows 客户端包与跨平台便携包
  - [x] 单元测试 `test_skills_workspace_lifecycle.py` 与端到端回归 100% 通过
