---
name: regulation-biz-reconciliation
command: /reconcile
version: 1.0.0
category: 业务审计与对账
type: skill
environment:
  os:
    - all
  requires_gui: false
  requires_runtime:
    - python
description: 规程 Wiki × 业务流水确定性对账规范：仅在 Task 模式下生效，固化「抽取规程规则 → Auto Ontology 术语与实时流水拉取 → 本地 Python 确定性逐行对账导出 Excel → 溯源汇报与 Wiki 沉淀」四步闭环。
---

# 规程 Wiki × 业务流水确定性对账规范 (Regulation-Biz Reconciliation)

> **适用前提**：必须处于 **Task（任务）模式**，且当前专家已开启知识库检索与 Auto Ontology 业务数据查询权限。

---

## 标准 4 步执行范式（强制遵守）

### Step 1｜抽取规程与业务口径红线
1. 优先调用 `list_kb_docs` / `search_cloud_wiki` / `read_kb_slices` 检索专家绑定的规程知识库、Wiki 与规则卡（`UniversalRuleExtractor`）。
2. 明确提取以下硬性基准：
   - **费用与金额标准**（如报名费单价、优惠减免条件）；
   - **资格与门槛限制**（如组别出生年份边界、积分要求、兼项与人数上限）；
   - **业务数据口径红线**（如金额单位是否为分需 `/100` 转元、是否需过滤父订单/退费订单、唯一去重主键字段）。

### Step 2｜对齐术语并拉取实时业务流水
1. 先调用 `search_biz_ontology_terms` 对齐目标业务表与字段口径（如“有效报名”、“已缴费状态值”）。
2. 再调用 `query_biz_data_via_ontology` 拉取真实业务流水，**并记录工具返回的实际执行 `sql_code`**。
3. 若返回结果超过 **20 行**，工具会自动将全量结果落盘至 `output/_tmp/ontology_query_<timestamp>.json`，直接提取该路径供下一步脚本读取。

### Step 3｜本地 Python 确定性对账（严禁大模型心算）
* **红线**：**绝对禁止由大模型直接心算汇总金额、人数或逐条肉眼比对**！
* 必须在沙盒中使用 `write_local_file` 编写并执行本地 Python 对账脚本（通过 `execute_local_command` 运行）：
  1. 读取 Step 2 落盘的 JSON 明细（或嵌入少量结构化数据）；
  2. 按选手/订单主键逐行与 Step 1 提取的规程阈值进行**确定性代码比对**，标记异常类型（如：漏缴/欠费、超额收费、组别不符、积分未达标、重复报名）；
  3. 使用 `openpyxl` / `pandas` 将**对账汇总表**与**异常明细表**导出为带表头样式、自动列宽的 `.xlsx` 文件，直接保存至一级 `output/` 目录（如 `output/reconciliation_report.xlsx`）。

### Step 4｜溯源汇报与 Wiki 沉淀指引
在最终回复中必须清晰呈现：
1. **对账汇总表**（总核对笔数、合规笔数、异常笔数及分类统计）；
2. **异常清单产物路径**（`output/xxx.xlsx`）；
3. **实际执行的 `sql_code` 原文**（与过程数据口径说明）；
4. 提示用户可直接点击消息下方 **「存到Wiki」**，系统将自动以 `data_snapshot`（数据快照）归档至 Wiki 收件箱审阅留存。

