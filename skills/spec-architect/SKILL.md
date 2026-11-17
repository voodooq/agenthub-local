---
name: spec-architect
command: /spec
version: 2.0.0
category: 调研与分析
type: skill
environment:
  os:
    - all
  requires_gui: false
description: 产品技术规格与任务架构师：涵盖苏格拉底式反向审问推演（挖掘边界与并发隐患）、Feature Spec 技术规格书编写、以及带阻塞依赖图谱的敏捷子任务卡（Tickets）拆解全流程。
---

# 产品技术规格与任务架构师 (Spec Architect)

## 一、核心定位与执行流水线
当用户提出新功能想法、架构调整或复杂需求时，激活此技能按照标准化三阶段流水线推进：

```
[阶段一：反向审问推演] ➔ [阶段二：编写技术规格书 (Spec)] ➔ [阶段三：拆解任务依赖卡 (Tickets)]
```

---

## 二、阶段一：苏格拉底式反向审问 (Grill & Deduce)
1. **原则**：绝不盲目顺从不切实际的设想，像资深架构师一样刨根问底，挖掘以下边界条件：
   - **并发与竞态**：同时发生修改时如何加锁或保证数据一致？
   - **异常与兜底**：网络断开、超时或下游服务返回 5xx 时如何降级与重试？
   - **性能与规模**：数据量从 100 条激增至 100 万条时系统瓶颈在哪里？
2. **文档持久化**：
   - 复杂设计方案的推演结果与架构决策记录，应整理沉淀至项目根目录的 `docs/` 目录下（如 `docs/architecture_decision_record.md`）。

---

## 三、阶段二：结构化技术规格书 (Feature Spec)
在方案充分推演收敛后，直接输出结构化的 Feature Spec，包含以下六大章节：
1. **背景与目标 (Context & Objectives)**：解决什么痛点、核心指标 (SLO/KPI)。
2. **架构总览与时序交互 (Architecture & Sequence)**：Mermaid 时序图与数据流向。
3. **API 与数据模型契约 (API & Data Schema)**：输入参数、返回值 Schema、数据库表结构变更。
4. **安全与权限审计 (Security & Auth)**：身份校验、数据脱敏、越权防范。
5. **部署与监控指标 (Deployment & Observability)**：环境变量、关键指标与告警阈值。
6. **未决问题与风险备忘 (Open Issues & Risks)**。

---

## 四、阶段三：敏捷子任务卡拆解 (Tickets Decomposition)
将审核通过的 Feature Spec 细化拆解为可独立开发验证的任务清单：
- **纵向切片**：每个 Ticket 包含一个可独立部署和验证的垂直切片（禁止横向按层级大杂烩切分）；
- **明确依赖关系**：标注 `Blocked by: #ID`，形成清晰的有向无环图 (DAG)；
- **验收标准 (DoD)**：每个 Ticket 必须包含严格的代码验收标准与自动化单测覆盖要求。

