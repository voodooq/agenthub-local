---
name: code-architecture-review
command: /review
version: 2.0.0
category: 工程与架构
type: skill
environment:
  os:
    - all
  requires_gui: false
  requires_cli:
    - git
description: 代码工程审查与深度架构设计：基于《软件设计的哲学》进行深模块划分与接口设计，执行“代码规范（Standards）与需求达成度（Spec）”双视角审查，并生成高质量 Conventional Commits 提交。
---

# 代码工程审查与深度架构设计 (Code Architecture & Review)

## 一、深模块设计哲学 (Deep Modules Philosophy)
依据《A Philosophy of Software Design》（软件设计的哲学）：
1. **追求深模块（Deep Modules）**：接口尽量简单清晰，但内部封装强大的业务能力与复杂度；严禁设计大量仅仅转发调用的“浅模块（Shallow Modules）”。
2. **隐藏信息与最小暴露**：内部辅助逻辑、特定技术细节不对外部暴露；调用者只需要知道“做什么（What）”，不需要关心“怎么做（How）”。
3. **消除重复与信息泄漏**：避免跨层级的时间顺序依赖（Temporal Coupling）与全局变量隐式依赖。

---

## 二、双视角代码审查 (Dual-Perspective Review)
对分支变更或 PR 执行严格审查：
- **视角一：工程规范与代码味道 (Standards & Smells)**：
  - 命名意图是否明确、是否有裸 `except:`、是否有潜在的资源泄漏（未关闭连接/文件）；
  - 是否遵守最小侵入原则，有无不必要的格式化、重构或无关注释。
- **视角二：需求契约与边界达成度 (Spec & Edge Cases)**：
  - 是否 100% 满足了用户提出的需求规格与边界条件；
  - 是否有未处理的空指针、超时、并发锁与数据类型转换溢出。

---

## 三、Git 提交规范 (Conventional Commits)
审查通过后，严格按照 Conventional Commits 格式生成标准中英文提交信息：
- 格式：`<type>(<scope>): <subject>`
- 类型：
  - `feat`: 新增功能
  - `fix`: 修复缺陷
  - `refactor`: 代码重构（无功能变更且无 Bug 修复）
  - `test`: 自动化测试增补
  - `docs`: 文档变更
  - `perf`: 性能优化
- 提交前核验：严禁将敏感凭据、`.env`、日志文件或临时测试数据存入暂存区。
