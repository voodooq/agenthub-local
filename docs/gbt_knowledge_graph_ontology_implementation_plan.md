# 基于中国国家标准（GB/T）的知识库与本体工程技术落地重构方案（v1.1 修订版）

## 一、 方案背景与国标体系对齐映射

为了使 AgentHub Local 当前的知识库切片、混合检索与轻量图谱引擎全面符合中国国家标准（GB/T）的工程规范与数据治理要求，本方案以当前系统的架构（轻量化私有化容器、纯 Python + SQLite 存储、BGE-M3 向量 / 本地 LightLLM 推理、HyPE 口语问答与通用规则抽取）为基座，深度对标并重构升级。

### 1. 核心国标体系映射矩阵

| 国家标准编号与名称 | 在系统中的对应技术模块 | 现有状态 | 目标达标状态 |
| :--- | :--- | :--- | :--- |
| **GB/T 37722-2019 / GB/T 42442**<br>《知识图谱技术框架》 | `LightGraphRAGEngine` & 本体模型 | 启发式纯文本三元组，无本体层模式约束 | 建立领域本体（Class/Property/Axiom），支持 W3C OWL 2 DL 与 RDF/TTL 导入导出 |
| **GB/T 36344-2018**<br>《信息技术 数据质量评价指标》 | `TextQualityGate` & 三级质量门禁 | 仅有简单长度、乱码、特殊符号字符级过滤 | 构建六维（规范、完整、准确、一致、时效、可访问）SHACL 契约自动化验证门禁（增量局部校验模式） |
| **GB/T 15237.1 / GB/T 10112**<br>《术语工作 词汇与原则》 | `seed_vocabulary` & 术语对齐引擎 | 自由文本实体提取，同义词硬编码归一化 | 种子词表受控抽取、SKOS 规范表达（`prefLabel`/`altLabel`）、编辑距离+BM25+向量三通道自适应加权映射 |
| **GB/T 36073-2018 (DCMM)**<br>《数据管理能力成熟度评估模型》 | 资产中枢、快照版本流转与审核流程 | 单一管理员一键发布，缺乏流程留痕 | 双模治理流（默认敏捷单人快速确认 + 审计验收严格三权分立模式切换），变更差异报告与受控基线发布 |
| **GB/T 22239-2019 (等保三级)**<br>《信息安全技术 网络安全等级保护》 | PROV-O 证据链追溯与敏感字段脱敏 | 仅记录 `remote_uri` 与 `etag` | 模态自适应锚点（PDF页码/Word段落/Excel单元格范围）+ 原文逐字证据（`text_span`）全链路审计溯源 |

---

## 二、 现状对标与差异化（Gap Analysis）及工程卡点纠偏

### 1. 四大核心技术鸿沟剖析

```
┌────────────────────────────────────────────────────────────────────────┐
│                   当前现状 (AgentHub Local)                           │
│  - 纯 SQLite hybrid_chunks + knowledge_triples (扁平三元组)          │
│  - 正则表达式 + 关键字匹配提取规则与实体                             │
│  - 单人一键训练、一键发布，无形式化一致性推理                         │
│  - 资产包 .ahkb (ZIP 内置 index.db + jsonl)                           │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ 对标升级 (经过工程真实性纠偏)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   国标目标 (GB/T 体系工程适配)                         │
│  - W3C OWL 2 RL/DL 本体模型 + SKOS 术语受控表 + RDF 1.1 / TTL 图谱    │
│  - 门禁机制：JSON Schema + 纯 Python owlrl/拓扑图环检测 + PySHACL 增量校验│
│  - 模态自适应 PROV-O (PDF页码偏移/Word段落序/Excel单元格 + text_span)   │
│  - DCMM 双模审批流 (默认敏捷单人一键留痕 / 等保验收多角色三权分立)   │
└────────────────────────────────────────────────────────────────────────┘
```

### 2. 核心技术卡点纠偏与工程解法（实战落地保障）

| 难点维度 | 理论方案盲区与踩坑点 | 务实工程解法（实战落地） |
| :--- | :--- | :--- |
| **1. 推理引擎的 JVM 依赖（高风险）** | 误以为 `owlready2` 是纯 Python 即可运行 HermiT/Pellet。**真相**：其底层推理强依赖 Java JRE，无 JVM 环境下直接报错崩坏。 | **极简纯 Python 路线（默认）**：采用 `owlrl`（纯 Python 实现的 RDFS / OWL 2 RL 闭包前向推导）+ NetworkX/自研 DFS 环检测（拦截 `rdfs:subClassOf` 循环继承）+ `pyshacl`。零 JVM 依赖，镜像体积与内存零开销。<br>*按需扩展*：若国标验收硬性要求 HermiT 报告，仅打入 `eclipse-temurin:17-jre-alpine`（150MB）作为离线一次性瞬态子进程拉起，跑完即毁。 |
| **2. PySHACL 全量校验 CPU 阻塞（中风险）** | 万级三元组开启 `inference="rdfs"` 和 `advanced=True` 时全量验证，纯 Python 计算可能耗时数分钟打满 CPU。 | **增量局部校验（Delta Validation）**：日常增量训练仅抽取“本次变更三元组 + 1跳邻居实体 + 涉及本体类定义”构成局部子图校验（耗时 < 1.5s）；全量校验仅在手动导出发布 `.ahkb 2.0` 正式基线包时异步触发。 |
| **3. 非 PDF 模态坐标失真（中风险）** | 强行要求所有格式具备 `page_number` 和线性 `char_start/char_end`。**真相**：Word 无固定绝对物理页码，Excel 单元格无连续线性字符偏移。 | **模态自适应 `location_anchor` + 逐字证据 `text_span` 硬校验**：<br>• PDF：`{"page": 12, "char_start": 104, "char_end": 180}`<br>• Word：`{"section_path": "3.2.1", "paragraph_index": 45}`<br>• Excel：`{"sheet": "收费标准", "cell_range": "B4:E4"}`<br>• 全模态强制保底：必填 `text_span`（原文原句证据片段）。 |
| **4. 三权分立的产品使用阻力（产品风险）** | 轻量私有化场景团队仅 1~2 人，强制三级串行审批会导致更新受阻甚至弃用。 | **架构支持三权，默认敏捷单人**：数据库与数据结构保留 `review_status`、`reviewer_id` 与完整审计流水；提供**【敏捷单人模式】**（默认，管理员一键发布自动补齐系统审计工单）与**【严格合规模式】**（等保验收时一键切换为多角色串行审批）。 |

---

## 三、 总体技术落地方案（分层重构设计）

```
                                    【GB/T 36073 DCMM 治理流程】
                          双模切换：默认敏捷单人一键留痕 / 等保多角色严格三权
                                                  │
┌─────────────────────────────────────────────────┼─────────────────────────────────────────────────┐
│ 1. 多模态数据接入与解析层 (GB/T 22239 等保合规)   ▼                                                 │
│    - PDF/Word/Excel 版面树还原 (多级标题 H1-H6, 表格单元格原子保护)                              │
│    - 模态自适应坐标锚定 (PDF页码偏移 / Word段落序号 / Excel行列范围 + 逐字证据 text_span) 写入 PROV-O│
└─────────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                  │
┌─────────────────────────────────────────────────▼─────────────────────────────────────────────────┐
│ 2. 种子词表与受控模式抽取层 (GB/T 15237 & GB/T 10112)                                             │
│    - 领域种子术语库 (seed_vocabulary / skos_terms.ttl) 强约束注入                                │
│    - 三通道加权对齐机制：                                                                         │
│      * 第一通道：精确匹配首选词 (prefLabel) 与同义别名 (altLabel) (权重 1.0)                     │
│      * 第二通道：字符编辑距离 (Levenshtein) + BM25 词形相似度 (防短词向量漂移)                     │
│      * 第三通道：BGE-M3 语义向量相似度 (加权综合分 >= 0.85 自动对齐; 0.70~0.85 进人工队列)         │
└─────────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                  │
┌─────────────────────────────────────────────────▼─────────────────────────────────────────────────┐
│ 3. 三级自动化质量门禁流水线 (GB/T 36344)                                                          │
│    ├── 第一道门 (语法/格式)：Pydantic v2 + JSON Schema 强类型与 PROV-O 逐字证据必填校验           │
│    ├── 第二道门 (逻辑一致性)：纯 Python owlrl (OWL 2 RL 闭包推导) + NetworkX 拓扑图环检测       │
│    └── 第三道门 (业务数据约束)：PySHACL 增量局部校验 (Delta Validation, 仅针对增量三元组子图)      │
└─────────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                  │
┌─────────────────────────────────────────────────▼─────────────────────────────────────────────────┐
│ 4. 双模存储与导出分发层 (GB/T 37722 & GB/T 8566)                                                  │
│    - 内部极速运行时：冷热分离架构，在线期沿用 SQLite CTE 原生递归两跳查询，保障 15ms 毫秒级极速响应│
│    - 国标标准化导出包：.ahkb 升级为 2.0 规范，包含：                                              │
│      * ontology.ttl (OWL 2 RL/DL 规范化本体模型)                                                 │
│      * validation_rules.shacl.ttl (声明式 SHACL 数据约束集)                                       │
│      * provenance.jsonl (PROV-O 全生命周期审计证据链)                                             │
│      * baseline_diff.json (跨版本差异比对与影响面报告)                                            │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 四、 核心技术模块设计与代码规范

### 1. 模态自适应 PROV-O 溯源数据模型 (`backend/app/kb/provenance.py`)

```python
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


class LocationAnchor(BaseModel):
    """模态自适应定位锚点：根据文件类型自适应坐标存储。"""
    modality: Literal["pdf", "docx", "xlsx", "txt", "markdown"]
    page_number: int | None = Field(default=None, description="PDF 物理页码")
    char_start: int | None = Field(default=None, description="PDF/纯文本字符起始偏移量")
    char_end: int | None = Field(default=None, description="PDF/纯文本字符终止偏移量")
    section_path: str = Field(default="正文", description="章节路径，如 1.2.3 或 规程标题")
    paragraph_index: int | None = Field(default=None, description="Word 文档段落顺序索引")
    sheet_name: str | None = Field(default=None, description="Excel 工作表名称")
    cell_range: str | None = Field(default=None, description="Excel 单元格坐标范围，如 B4:E10")


class StandardizedProvenance(BaseModel):
    """符合 W3C PROV-O 与等保三级审计要求的逐字溯源模型。"""
    doc_id: str = Field(..., description="文档稳定标识，如 MD5 前 16 位")
    doc_title: str = Field(..., description="文档真实文件名")
    anchor: LocationAnchor = Field(..., description="模态自适应定位锚点")
    text_span: str = Field(..., min_length=2, description="原文逐字证据片段（严格要求证据非空且与正文一致）")
    extractor_model: str = Field(default="rule_ast_v1.6", description="抽取引擎版本")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度评分")

    @model_validator(mode="after")
    def validate_anchor_consistency(self) -> "StandardizedProvenance":
        m = self.anchor.modality
        if m == "pdf" and self.anchor.page_number is None:
            raise ValueError("PDF 模态溯源必须包含 page_number")
        if m == "xlsx" and not self.anchor.sheet_name:
            raise ValueError("Excel 模态溯源必须包含 sheet_name")
        return self
```

### 2. 受控词表三通道加权对齐引擎 (`backend/app/kb/controlled_vocabulary.py`)

针对短词（2~4字）向量易漂移缺陷，采用 **“精确别名 (1.0) -> 编辑距离/BM25 词形 (0.3) + BGE-M3 语义向量 (0.7)”** 三通道加权判定：

```python
class ControlledVocabularyAligner:
    """遵循 GB/T 15237 & GB/T 10112 原则的领域受控词表对齐引擎。"""

    def __init__(
        self,
        seed_vocab: dict[str, list[str]],  # { "prefLabel": ["altLabel1", "altLabel2"] }
        auto_align_threshold: float = 0.85,
        review_threshold: float = 0.70,
    ):
        self.seed_vocab = seed_vocab
        self.auto_align_threshold = auto_align_threshold
        self.review_threshold = review_threshold
        self._flat_alt_map: dict[str, str] = {}
        for pref, alts in seed_vocab.items():
            self._flat_alt_map[pref.strip().lower()] = pref
            for a in alts:
                self._flat_alt_map[a.strip().lower()] = pref

    def align_concept(self, candidate: str, embed_func=None) -> tuple[str, str, float]:
        """
        Returns:
            (aligned_pref_label, status, score)
            status: "AUTO_ALIGNED" | "MANUAL_REVIEW" | "PROVISIONAL"
        """
        cand_norm = (candidate or "").strip().lower()
        if not cand_norm:
            return "", "PROVISIONAL", 0.0

        # 通道 1：精确首选词与同义别名秒级匹配 (权重 1.0)
        if cand_norm in self._flat_alt_map:
            return self._flat_alt_map[cand_norm], "AUTO_ALIGNED", 1.0

        # 通道 2：编辑距离词形相似度（防短词向量特征漂移）
        best_pref, text_sim = self._calculate_text_similarity(cand_norm)
        if text_sim >= 0.90:
            return best_pref, "AUTO_ALIGNED", text_sim

        # 通道 3：混合向量语义打分（若传入 embedding 计算函数）
        final_score = text_sim
        if embed_func:
            vector_sim = embed_func(candidate, best_pref)
            final_score = 0.3 * text_sim + 0.7 * vector_sim

        if final_score >= self.auto_align_threshold:
            return best_pref, "AUTO_ALIGNED", round(final_score, 4)
        elif final_score >= self.review_threshold:
            return best_pref, "MANUAL_REVIEW", round(final_score, 4)
        else:
            return candidate, "PROVISIONAL", round(final_score, 4)
```

---

## 五、 工程落地分阶段演进路线

### 阶段一：标准数据结构升级与溯源对齐（第 1—2 周，P0 优先级）
* **核心交付**：
  1. 落地 `backend/app/kb/provenance.py`，实现模态自适应 `LocationAnchor` 与 `StandardizedProvenance`；
  2. 落地 `backend/app/kb/controlled_vocabulary.py`，实现三通道受控词表对齐；
  3. 改造 `DocumentStreamExtractor` 与 `ParentChildASTChunker`，切片时注入逐字证据与坐标；
  4. 完善单元测试集，保障向后兼容性。

### 阶段二：轻量纯 Python 质量门禁与 SHACL 集成（第 3—4 周，P1 优先级）
* **核心交付**：
  1. 采用 `owlrl`（纯 Python OWL 2 RL 前向闭包）结合 NetworkX 实现零 JVM 依赖的拓扑环检测；
  2. 集成 `pyshacl`，实施**增量局部校验（Delta Validation）**，仅针对本次新增变更三元组运行，门禁耗时控制在 1.5 秒内；
  3. 内置 15 条标准规程通用 SHACL 约束规则模板（值域、数值范围、日期单调递增性）。

### 阶段三：DCMM 双模工作台与 `.ahkb 2.0` 标准资产包（第 5—6 周，P2 优先级）
* **核心交付**：
  1. 界面与后端增加“敏捷单人模式（默认）/ 严格合规模式”切换开关；
  2. 升级 `.ahkb` 打包引擎为 2.0 标准包（包含 `ontology.ttl`、`rules.shacl.ttl` 与 `provenance.jsonl`），支持无缝导入第三方国标图数据库。
