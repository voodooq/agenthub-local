# Loop 自主领航舱状态联动与闭环优化任务清单

- [ ] **任务 1：改造后端数据仓储层 `backend/app/repository/loop_run_repository.py`**
  - [ ] 在 `create_loop` 时自动归档当前工作区的所有未完结旧 Loop（设为 completed），避免孤儿任务挂起
  - [ ] 优化 `get_active_loop_by_workspace`，当存在已交付的 Loop 时，支持返回最新的一条（避免 UI 瞬间断档）
  - [ ] 新增 `record_tool_step` 方法，支持快速追加工具执行指令并递增 `current_iteration`

- [ ] **任务 2：改造核心微内核 `backend/app/agent/runtime.py` 实现执行态实时联动**
  - [ ] 在 `chat_stream` 初始阶段通过 `workspace_id` 与 `conversation_id` 自动关联对应的 `active_loop`
  - [ ] 在工具执行阶段（如 shell 命令、文件读写等），每完成一步自动将命令执行摘要追加到记事板，轮次递增，并推送 `loop_step` SSE 事件
  - [ ] 在流式输出完成（即模型给出最终自然语言结论，无更多工具调用）时，自动触发 Verifier DoD 校验
  - [ ] 将 Loop 状态置为 `completed`，把所有 DoD 打勾完成，推送 `loop_completed` SSE 事件并自动登记资产库

- [ ] **任务 3：增强状态机执行器 `backend/app/agent/loop_runner.py` 与接口 `backend/app/api/v1/loop.py`**
  - [ ] 优化 `step_verify`，完善动态 Markdown 生成，将执行记录、DoD 全部打勾并持久化到本地 `.agenthub/scratchpad.md`
  - [ ] 支持通过 `/api/v1/loop/{id}/action` 传入 `complete` 时平滑收尾

- [ ] **任务 4：升级前端 `LoopHUD.tsx` 与 `page.tsx` 交互响应**
  - [ ] 在 SSE 事件流监听中实时捕获 `loop_step` 和 `loop_completed` 事件，毫秒级就地更新 `activeLoop`
  - [ ] 在 `LoopHUD.tsx` 中增加已完成状态展示：显示绿色 `[✅ 任务已交付并验收]` 徽标与 `[✕ 归档收起]` 按钮
  - [ ] 在 `LoopHUD.tsx` 中支持展开查看工具调用的真实执行清单与 DoD 验证明细

- [ ] **任务 5：多语言补充与自动化测试验收**
  - [ ] 在 `frontend/src/utils/i18nDictionary.ts` 中维护新增的词条（中/繁/英/日）
  - [ ] 编写并运行 Python 测试用例 `scratch/test_loop_live_execution.py` 验证联动
  - [ ] 运行 Next.js 静态构建与打包发布验证
