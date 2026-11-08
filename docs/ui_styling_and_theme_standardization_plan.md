# UI 全景样式规范与深浅主题自适应实施方案

## 一、背景与目标
根据用户指令 `/ui-styling`：
1. **全景深浅主题自适应（Dark/Light Mode）**：
   彻底根除各子页面（库 LibraryView、已安排 ScheduledView、技能与应用 SkillsView、对话式向导 ScheduledTaskDialogModal 等）以及各模态弹窗中硬编码的深黑色值（如 `bg-[#131314]`、`bg-zinc-900`、`border-zinc-800`、`text-white`、`text-zinc-400` 等），全面接入 Tailwind 语义设计令牌（Tokens）：
   - 背景容器：`bg-background text-foreground`
   - 卡片与表面：`bg-surface-card`、`bg-surface-100`、`bg-surface-200`、`bg-surface-50`
   - 边框与分割线：`border-border`、`hover:border-border-hover`
   - 前景与次级文本：`text-foreground`、`text-muted-foreground`
   - 品牌高亮与状态：`text-emerald-600 dark:text-emerald-400`、`bg-emerald-500/10`
   使得在「深色模式」、「浅色模式（Sage Minimal）」以及「跟随系统」下，所有界面均具备统一的设计语言、极佳的对比度与呼吸感。

2. **去除非必要图标与冗余元素（Visual Decluttering）**：
   去除堆砌在标题、列表项、操作按钮、Tab 栏等位置的非必要装饰性图标与 emoji（如标题处塞的 `📚`、`⏱️`，空状态塞的 `📂`，会话历史列表每行重复堆砌的 `MessageSquare`，Tab 按钮里的 emoji 等），让排版回归内容本身与清晰的视觉层级（Typography & Spacing First）。

3. **全量构建验证与部署可靠性**：
   确保 TypeScript 类型安全、Next.js 生产环境静态打包构建零报错，保障前后端与桌面打包的稳定部署。

---

## 二、涉及模块与重构要点

### 1. `frontend/src/components/LibraryView.tsx`（资产库）
- 消除根容器 `bg-[#131314]` 与内部所有 `zinc-*` 硬编码，替换为 `bg-background`、`bg-surface-card`、`border-border`、`text-foreground`、`text-muted-foreground`。
- 去除标题处多余 emoji（如 `📚`）。
- 面包屑地址栏简化：去掉 `💻`，保留清晰的面包屑层级与简洁图标。
- 空状态视觉简化：去除庞大彩色 emoji，使用纯净优雅的空状态排版。
- 保证卡片视图、详细信息列表视图（Table）在浅色模式与深色模式下均对比度极佳。

### 2. `frontend/src/components/ScheduledView.tsx`（已安排计划任务）
- 消除容器 `bg-[#131314]` 与全量 `zinc-*` 硬编码。
- 去除大标题处的 `⏱️`。
- 任务卡片、顶部横幅与状态指示接入语义 Token。

### 3. `frontend/src/components/ScheduledTaskDialogModal.tsx`（对话式向导弹窗）
- 弹窗背景与面板接入 `glass-modal`、`bg-surface-100`、`border-border`。
- Tab 切换去除 `💬`、`📋` 等非必要 emoji，保持现代极简。
- 输入框与结果卡片在浅色模式下具备清晰边框与易读文本。

### 4. `frontend/src/components/SkillsView.tsx`（技能中枢）
- 消除容器 `bg-[#131314]` 与全量 `zinc-*` 硬编码。
- 标签栏与技能卡片接入语义 Token。
- 去除多余装饰图标，突出技能命令、说明与 SOP 规程。

### 5. `frontend/src/components/Sidebar.tsx`（侧边栏会话历史流）
- 会话历史列表去除每行机械重复的 `MessageSquare` 图标，仅保留置顶状态的微小指示器和纯净的会话标题，大幅提升信息密度与视觉清爽度。

### 6. `frontend/src/app/page.tsx` 与其他弹窗微调
- 底部输入区快捷技能按钮去除硬编码 `bg-zinc-800`，改用语义化 Token。
- 确保所有页面在深浅色切换时无闪烁、无残留黑底白字。

