# 国标体系升级：写链路闭环与读链路对齐实施方案 (v1.3)

本方案落实“**写链路（新建/增量）零脏数据**”与“**读链路（查询调用）全对齐**”两大核心目标，吸纳多知识库隔离、增量更新级联与长词优先子串扫描等生产级工程纠偏，建立纯净、健壮、高保真的国标化（GB/T）知识库全生命周期闭环。

---

## 一、 写链路把控：新建与增量不出问题的 4 道防线

### 1. 新建知识库纯净 DDL 初始化 (`storage.py`)
在 `KBStorageManager.init_version_db_schema` 中，原生落盘国标扩展字段：
* **`hybrid_chunks` 表**：
  * `provenance_json TEXT NOT NULL DEFAULT ''`：存放标准 W3C PROV-O 证据链（含模态自适应坐标与 `text_span` 逐字证据）。
* **`knowledge_triples` 表**：
  * 原生增加：
    * `id INTEGER PRIMARY KEY AUTOINCREMENT`
    * `subject TEXT NOT NULL`
    * `predicate TEXT NOT NULL`
    * `object TEXT NOT NULL`
    * `chunk_id TEXT NOT NULL DEFAULT ''`
    * `doc_id TEXT NOT NULL DEFAULT ''`
    * `axiom_type TEXT NOT NULL DEFAULT 'FACT'`
    * `alignment_status TEXT NOT NULL DEFAULT 'AUTO_ALIGNED'`（`AUTO_ALIGNED` / `MANUAL_REVIEW` / `PROVISIONAL`）
    * `confidence REAL NOT NULL DEFAULT 1.0`
    * `review_status TEXT NOT NULL DEFAULT 'AUTO_APPROVED'`（敏捷模式默认 `AUTO_APPROVED`，严格模式落 `PENDING`，流转为 `APPROVED` / `REJECTED`）
    * `provenance_json TEXT NOT NULL DEFAULT ''`
    * `created_at REAL NOT NULL`
    * `updated_at REAL NOT NULL DEFAULT 0.0`
  * 唯一索引修正：**`UNIQUE(subject, predicate, object, doc_id)`**（同文档内严格去重，跨文档各自保留独立证据链，防止跨文档更新误删）。
  * 建立索引：`idx_kt_sub`, `idx_kt_obj`, `idx_kt_chunk`, `idx_kt_doc`, `idx_kt_status`。

### 2. `doc_id` 唯一生成口径与级联先清后建
* **`doc_id` 统一口径**：在 `provenance.py` 中收口唯一静态方法 `compute_doc_id(remote_uri: str) -> str`（统一做规范化哈希截断），切片、入库、删除、查询全部唯一调用此函数。内容变更仅通过 `etag` 判定，绝不改变 `doc_id`。
* **级联彻底清理**：在 `KBStorageManager.delete_chunks_by_remote_uris` 中：
  * 针对目标 `remote_uris` 计算对应 `doc_id` 列表；
  * 在删除 `hybrid_chunks` 旧切片的同时，级联执行：
    ```sql
    DELETE FROM knowledge_triples WHERE chunk_id IN (...) OR doc_id IN (...);
    DELETE FROM kb_structured_rules WHERE doc_id IN (...);
    ```
  * 彻底杜绝旧事实残留。

### 3. 首次构建被 `strict` 门禁拦截的空指针与状态防护 (`knowledge.py`)
* 若新知识库从未发布过活跃版本（`prev_ver` 不存在），在 Stage 3 门禁被 `strict` 拦截时：
  * 保持 `active_version` 为空；
  * 标记版本记录为 `UNPUBLISHED`（或 `REJECTED`），记录具体拦截违规详情；
  * 清理未提交的 staging 临时目录；
  * 查询接口安全返回空态（`kb_status: "UNPUBLISHED"`），杜绝空指针或 500 异常。

### 4. 三元组抽取强制绑定受控词表与 PROV-O 父切片溯源
* 切片入库抽取三元组时，继承当前切片的 `StandardizedProvenance`；
* 通过按知识库隔离的 `get_aligner(kb_id)` 对主客体与谓词进行归一化；
* 写入 `knowledge_triples` 并绑定父切片 PROV-O 证据与对应 `doc_id`。

---

## 二、 读链路对齐：查询与外部调用接口的 3 个关键同步点

### 1. 查询侧别名归一化（长词优先子串扫描 + 并集扩展）
* **多库隔离工厂**：在 `controlled_vocabulary.py` 中提供 `get_aligner(kb_id: str)`，按知识库独立加载与缓存，支持词表文件 `mtime` 热重载，无专属词表时回退默认词表；
* **长词优先扫描**：在 `LightGraphRAGEngine.find_anchor_entities` 中，按别名长度倒序从问句中扫描命中词（纯内存扫描 < 0.5ms）；
* **并集扩展**：命中后将标准首选词 `prefLabel` 与原词一并纳入候选实体集合（Union），双保险召回；
* **过滤驳回边**：递归 CTE 查询固定过滤 `review_status != 'REJECTED'`；
* **输出聚合**：查询输出时按 `(subject, predicate, object)` 聚合，合并保留多篇文档的引用来源。

### 2. 检索响应 Schema 规范化透出 `provenance` 与 `citation_label`
* 统一在 `provenance.py` 提供 `format_citation_label(prov)`（PDF 输出页码、Excel 输出工作表与单元格、Word 输出章节段落）；
* `storage.py` 中所有混合检索、向量检索与 FTS5 查询的 SQL 显式查出 `provenance_json` 并反序列化；
* `multi_kb_router.py` 在每个返回切片中透出 `provenance` 与 `citation_label`，现有字段 100% 保持兼容。

### 3. 版本指针隔离与优雅兜底
* 统一通过 `KBStorageManager.get_active_db_path(kb_id)` 路由；若处于 `UNPUBLISHED` 状态，优雅返回空结果与状态说明。

---

## 三、 端到端验证设计 (`test_gbt_e2e_pipeline.py`)

模拟完整生命周期流：
1. **新建测试库**：验证 DDL 包含纯净国标字段与 `(s, p, o, doc_id)` 唯一索引；
2. **首次同步**：传入多模态规程切片完成首发构建，验证生成 PROV-O 锚点坐标及受控词表三元组；
3. **别名检索与溯源验证**：用自然语言长句（如“请问退赛怎么退费”）发起检索，验证长词优先扫描命中“撤销报名”，图谱返回聚合来源，切片包含准确 `citation_label`；
4. **同文档增量更新与级联清理**：更新文档内容，验证旧切片与旧三元组被原子彻底清理，新三元组更新；同时多文档同事实不会被误删；
5. **门禁拦截防护**：在 `strict` 模式下注入非法负金额规程，验证被门禁阻断，活跃版本指针不发生漂移，线上服务不受影响。
