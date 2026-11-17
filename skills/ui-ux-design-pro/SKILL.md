---
name: ui-ux-design-pro
command: /ui-ux
version: 2.0.0
category: 视觉与设计
type: skill
environment:
  os:
    - all
  requires_gui: false
  requires_runtime:
    - node
    - python
  requires_cli:
    - npm
description: 全端 UI/UX 设计与样式中枢：涵盖 Web/移动端设计系统、三层 Token 架构（基础→语义→组件）、Tailwind CSS 与 shadcn/ui 组件库定制及无障碍交互规范。
---

# 全端 UI/UX 设计与样式中枢 (UI/UX Design Pro)

## 一、核心定位与适用场景
当需要进行前端界面设计、审查交互体验、制定统一设计系统、编写 Tailwind CSS / shadcn 样式或管理三层 Design Token 时激活此技能。

---

## 二、三层 Token 架构规范
严格遵循 **“基础层 (Primitive) ➔ 语义层 (Semantic) ➔ 组件层 (Component)”** 映射：
1. **基础层 (Primitive)**：纯粹的色阶与物理数值（如 `blue-500: #3b82f6`, `spacing-4: 16px`）。严禁在业务组件中直接裸写基础 Token。
2. **语义层 (Semantic)**：具备业务含义与明暗主题自适应能力的抽象（如 `color-primary`, `bg-surface`, `text-muted`）。
3. **组件层 (Component)**：专属于特定 UI 部件的变量（如 `btn-primary-bg: var(--color-primary)`）。

---

## 三、UI 组件设计守则 (React + Tailwind + Radix / shadcn)
1. **对比度与无障碍 (WCAG 2.1 AA)**：
   - 正文与背景对比度不得低于 `4.5:1`，大标题与高亮徽标不得低于 `3:1`；
   - 严禁在浅色主题下使用浅灰字，深色主题下使用深灰字；必须保证双主题下字体高对比度阅读。
2. **响应式与间距节奏**：
   - 间距统一采用 4px 网格系统（`p-2`, `p-4`, `p-6`, `gap-3` 等）；
   - 页面断点使用移动优先原则：`sm:` (640px), `md:` (768px), `lg:` (1024px), `xl:` (1280px)。
3. **状态完整性**：
   - 所有交互组件必须显式定义 `hover:`, `active:`, `focus-visible:ring-2`, `disabled:opacity-50` 四态。

---

## 四、本地数据与脚本工具
本技能随包携带了全栈设计规范与数据：
- 查询本地设计库：`python skills/ui-ux-design-pro/scripts/search.py "<关键词>"`
- 生成/验证 Design Tokens：`node skills/ui-ux-design-pro/scripts/validate-tokens.cjs`
