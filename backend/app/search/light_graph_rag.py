"""
轻量 SQLite 图 RAG (Light Graph RAG)
核心功能：
1. 启发式三元组 (subject, predicate, object) 结构化抽取与去重入库；
2. 纯 SQLite 原生 WITH RECURSIVE 递归 CTE 2~3 跳双向子图扩展；
3. 环路死循环防护与最大边数门限 (<= 30)；
4. 格式化图谱路径输出与溯源文档定位，封装 query_knowledge_graph 工具接口。
"""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger


@dataclass
class KnowledgeTriple:
    subject: str
    predicate: str
    object: str
    source_uri: str
    authority_tier: int = 2
    effective_time: float = 0.0
    confidence: float = 1.0


class LightGraphRAGEngine:
    """基于纯 SQLite 原生递归 CTE 的轻量图 RAG 引擎。"""

    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root).resolve()
        self.db_path = self.workspace_root / ".agenthub" / "index.db"

    def _init_db(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_triples (
            triple_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            pod_id           TEXT NOT NULL DEFAULT 'global',
            subject          TEXT NOT NULL,
            predicate        TEXT NOT NULL,
            object           TEXT NOT NULL,
            source_uri       TEXT NOT NULL,
            authority_tier   INTEGER NOT NULL DEFAULT 2,
            effective_time   REAL NOT NULL,
            confidence       REAL NOT NULL DEFAULT 1.0,
            UNIQUE(pod_id, subject, predicate, object, source_uri)
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_triples_sub ON knowledge_triples(pod_id, subject);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_triples_obj ON knowledge_triples(pod_id, object);")

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 60000;")
        self._init_db(conn)
        return conn

    PREDICATE_SYNONYM_MAP: dict[str, str] = {
        # 主办与组织架构
        "主办方": "主办单位",
        "承办方": "承办单位",
        "协办方": "协办单位",
        "支持单位": "指导单位",
        "指导机构": "指导单位",
        # 费用标准
        "报名费": "收费标准",
        "报名费用": "收费标准",
        "参赛费": "收费标准",
        "收费标准": "收费标准",
        "押金": "收费标准",
        "押金标准": "收费标准",
        # 比赛时间
        "开票日期": "开票日期",
        "开票时间": "开票日期",
        "比赛时间": "比赛时间",
        "比赛日期": "比赛时间",
        "竞赛时间": "比赛时间",
        "报名截止时间": "报名截止时间",
        "报名截止日期": "报名截止时间",
        # 项目与组别
        "比赛项目": "竞赛项目",
        "参赛项目": "竞赛项目",
        "组别设置": "竞赛组别",
        "比赛组别": "竞赛组别",
        "参赛组别": "竞赛组别",
        # 资格与要求
        "报名条件": "参赛资格",
        "报名资格": "参赛资格",
        "参赛要求": "参赛资格",
    }

    @classmethod
    def normalize_predicate(cls, pred: str) -> str:
        """谓词同义词与核心语义标准化。"""
        clean_p = pred.strip()
        return cls.PREDICATE_SYNONYM_MAP.get(clean_p, clean_p)

    @classmethod
    def extract_triples_from_text(
        cls,
        text: str, source_uri: str, authority_tier: int = 2, effective_time: float = 0.0
    ) -> list[KnowledgeTriple]:
        """从文本段落中启发式抽取核心关系三元组。"""
        triples: list[KnowledgeTriple] = []
        eff = effective_time or time.time()
        doc_name = source_uri.split("/")[-1].split(".")[0].strip()

        # 1. 结构化键值抽取 (支持各类前置数字与汉字序号)
        kv_pattern = re.compile(
            r"^[\s\-\*\d一二三四五六七八九十（）\(\)\.、]*([A-Za-z0-9_\u4e00-\u9fa5]{2,15})[\s]*[:：][\s]*([^\r\n]{2,80})$",
            re.M,
        )
        for match in kv_pattern.finditer(text):
            k, v = match.group(1).strip(), match.group(2).strip()
            if not k.startswith("http") and not v.startswith("http") and len(k) >= 2 and len(v) >= 2:
                # 若键名为组别（如初中组、U12组），主体设为该组别
                if any(group_kw in k for group_kw in ("组", "队", "年龄", "资格")):
                    sub = f"{doc_name}·{k}"
                    pred = "规则要求"
                    obj = v
                else:
                    sub = doc_name
                    pred = cls.normalize_predicate(k)
                    obj = v

                triples.append(
                    KnowledgeTriple(
                        subject=sub,
                        predicate=pred,
                        object=obj,
                        source_uri=source_uri,
                        authority_tier=authority_tier,
                        effective_time=eff,
                    )
                )

        # 2. 中文常见谓词抽取：X的Y是Z / X负责Y / X包含Y / X隶属于Y / X兼项限制Y
        patterns = [
            (re.compile(r"([A-Za-z0-9_\u4e00-\u9fa5]{2,15})的([A-Za-z0-9_\u4e00-\u9fa5]{2,10})为?是?([A-Za-z0-9_\u4e00-\u9fa5]{2,30})"), 1, 2, 3),
            (re.compile(r"([A-Za-z0-9_\u4e00-\u9fa5]{2,15})(负责|主管|对接|承办|协办|主办|归属|隶属|兼项限制|规定|设)([A-Za-z0-9_\u4e00-\u9fa5]{2,30})"), 1, 2, 3),
            (re.compile(r"([A-Za-z0-9_\u4e00-\u9fa5]{2,15})(费用|标准|金额|限额|电话|地址|地点)为?[:：]?([A-Za-z0-9_\u4e00-\u9fa5\d]{2,30})"), 1, 2, 3),
        ]

        for pat, s_idx, p_idx, o_idx in patterns:
            for match in pat.finditer(text):
                sub = match.group(s_idx).strip()
                pred = cls.normalize_predicate(match.group(p_idx).strip())
                obj = match.group(o_idx).strip()
                if sub and pred and obj and sub != obj:
                    triples.append(
                        KnowledgeTriple(
                            subject=sub,
                            predicate=pred,
                            object=obj,
                            source_uri=source_uri,
                            authority_tier=authority_tier,
                            effective_time=eff,
                        )
                    )

        return triples

    def upsert_triples(self, triples: list[KnowledgeTriple], pod_id: str = "global") -> int:
        """批量写入或更新三元组。"""
        if not triples:
            return 0

        inserted = 0
        with closing(self._get_connection()) as conn:
            with conn:
                for item in triples:
                    if isinstance(item, tuple):
                        t = KnowledgeTriple(
                            subject=item[0],
                            predicate=item[1],
                            object=item[2],
                            source_uri=item[3] if len(item) > 3 else "",
                            authority_tier=item[4] if len(item) > 4 else 2,
                            effective_time=item[5] if len(item) > 5 else 0.0,
                            confidence=item[6] if len(item) > 6 else 1.0,
                        )
                    else:
                        t = item
                    conn.execute("""
                    INSERT INTO knowledge_triples (
                        pod_id, subject, predicate, object, source_uri,
                        authority_tier, effective_time, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pod_id, subject, predicate, object, source_uri) DO UPDATE SET
                        authority_tier = excluded.authority_tier,
                        effective_time = excluded.effective_time,
                        confidence = excluded.confidence;
                    """, (
                        pod_id, t.subject, t.predicate, t.object, t.source_uri,
                        t.authority_tier, t.effective_time, t.confidence
                    ))
                    inserted += 1
        return inserted

    def find_anchor_entities(self, seed: str, pod_id: str = "global") -> list[str]:
        """定位图谱起点实体：长词优先子串扫描对齐 + 双向包含匹配。"""
        clean_seed = seed.strip()
        if not clean_seed:
            return []

        # 1. 接入受控词表：长词优先子串扫描 + 并集扩展（纯内存 < 0.5ms）
        from ..kb.controlled_vocabulary import get_aligner
        aligner = get_aligner(pod_id)
        matched_prefs = aligner.scan_query_terms(clean_seed)

        # 候选实体池（初始包含原词及扫描到的标准首选词，双保险召回）
        anchor_seeds = {clean_seed} | matched_prefs
        candidate_anchors: set[str] = set(anchor_seeds)

        with closing(self._get_connection()) as conn:
            # 检查 review_status 列
            has_review = False
            try:
                cols = [c[1] for c in conn.execute("PRAGMA table_info(knowledge_triples)").fetchall()]
                has_review = "review_status" in cols
            except Exception:
                pass
            review_filter = "AND review_status != 'REJECTED'" if has_review else ""

            for s in anchor_seeds:
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT entity FROM (
                        SELECT subject as entity FROM knowledge_triples 
                        WHERE pod_id = ? {review_filter} AND (subject = ? OR subject LIKE '%' || ? || '%' OR (length(subject) >= 2 AND instr(?, subject) > 0))
                        UNION
                        SELECT object as entity FROM knowledge_triples 
                        WHERE pod_id = ? {review_filter} AND (object = ? OR object LIKE '%' || ? || '%' OR (length(object) >= 2 AND instr(?, object) > 0))
                    )
                    ORDER BY length(entity) DESC
                    LIMIT 5;
                    """,
                    (pod_id, s, s, s, pod_id, s, s, s),
                ).fetchall()
                for r in rows:
                    candidate_anchors.add(r["entity"])

        return sorted(list(candidate_anchors), key=lambda x: len(x), reverse=True)[:8]

    def query_subgraph(
        self,
        seed_entity: str,
        max_hops: int = 2,
        pod_id: str = "global",
        max_edges: int = 30,
    ) -> list[dict[str, Any]]:
        """基于 SQLite 原生 WITH RECURSIVE 执行双向 2~3 跳子图查询。

        Args:
            seed_entity: 起点实体名称或包含实体的问句。
            max_hops: 最大递归跳数（限制 <= 3）。
            pod_id: 隔离域。
            max_edges: 返回边数量上限。

        Returns:
            list[dict[str, Any]]: 关系边列表，包含起点、终点、谓词与跳数。
        """
        clean_seed = seed_entity.strip()
        if not clean_seed:
            return []

        # 智能实体模糊锚定
        anchors = self.find_anchor_entities(clean_seed, pod_id=pod_id)
        if not anchors:
            anchors = [clean_seed]

        hops_limit = max(1, min(max_hops, 3))
        placeholders = ",".join("?" for _ in anchors)

        # 检查是否具备 review_status 过滤字段
        has_review = False
        with closing(self._get_connection()) as conn:
            try:
                cols = [c[1] for c in conn.execute("PRAGMA table_info(knowledge_triples)").fetchall()]
                has_review = "review_status" in cols
            except Exception:
                pass
        review_filter_where = "AND review_status != 'REJECTED'" if has_review else ""
        review_filter_join = "AND t.review_status != 'REJECTED'" if has_review else ""

        # 双向扩展：同时沿着 subject -> object 出边与 object -> subject 入边扩展
        # 使用 visited 字符串防止环路: '|' || next_node || '|'
        sql = f"""
        WITH RECURSIVE graph_cte(cur_node, subject, predicate, object, source_uri, authority_tier, hop, visited) AS (
            -- 初始出边与入边 (hop = 1)
            SELECT 
                object AS cur_node,
                subject, predicate, object, source_uri, authority_tier,
                1 AS hop,
                '|' || subject || '|' || object || '|' AS visited
            FROM knowledge_triples
            WHERE pod_id = ? {review_filter_where} AND (subject IN ({placeholders}) OR subject LIKE '%' || ? || '%')
            
            UNION ALL
            
            SELECT 
                subject AS cur_node,
                subject, predicate, object, source_uri, authority_tier,
                1 AS hop,
                '|' || object || '|' || subject || '|' AS visited
            FROM knowledge_triples
            WHERE pod_id = ? {review_filter_where} AND (object IN ({placeholders}) OR object LIKE '%' || ? || '%')
            
            UNION ALL
            
            -- 递归扩展 (hop <= max_hops)
            SELECT 
                t.object AS cur_node,
                t.subject, t.predicate, t.object, t.source_uri, t.authority_tier,
                g.hop + 1,
                g.visited || t.object || '|'
            FROM knowledge_triples t
            JOIN graph_cte g ON t.subject = g.cur_node
            WHERE t.pod_id = ? {review_filter_join}
              AND g.hop < {hops_limit}
              AND instr(g.visited, '|' || t.object || '|') = 0
        )
        SELECT subject, predicate, object, source_uri, authority_tier, MIN(hop) as min_hop
        FROM graph_cte
        GROUP BY subject, predicate, object, source_uri
        ORDER BY min_hop ASC, authority_tier ASC
        LIMIT ?;
        """

        params = (
            [pod_id] + anchors + [clean_seed]
            + [pod_id] + anchors + [clean_seed]
            + [pod_id, max_edges * 2]
        )

        with closing(self._get_connection()) as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
                # 聚合去重：按 (subject, predicate, object) 聚合多篇文档的来源，杜绝同事实被覆盖
                aggregated: dict[tuple[str, str, str], dict[str, Any]] = {}
                for r in rows:
                    key = (r["subject"], r["predicate"], r["object"])
                    if key not in aggregated:
                        aggregated[key] = {
                            "subject": r["subject"],
                            "predicate": r["predicate"],
                            "object": r["object"],
                            "min_hop": r["min_hop"],
                            "authority_tier": r["authority_tier"],
                            "source_uris": [r["source_uri"]] if r["source_uri"] else [],
                            "source_uri": r["source_uri"] or "",
                        }
                    else:
                        existing = aggregated[key]
                        if r["source_uri"] and r["source_uri"] not in existing["source_uris"]:
                            existing["source_uris"].append(r["source_uri"])
                        if r["min_hop"] < existing["min_hop"]:
                            existing["min_hop"] = r["min_hop"]
                        if r["authority_tier"] < existing["authority_tier"]:
                            existing["authority_tier"] = r["authority_tier"]

                return list(aggregated.values())[:max_edges]
            except Exception as e:
                logger.warning(f"SQLite 递归 CTE 图谱查询异常: {e}")
                return []

    def format_subgraph_markdown(self, seed_entity: str, edges: list[dict[str, Any]]) -> str:
        """将子图关系边格式化为结构化 Markdown 文本展示。"""
        if not edges:
            return f"以实体【{seed_entity}】为中心的知识图谱中未发现关联关系边。"

        lines = [f"### 🕸️ 知识图谱关联网络 (起点: {seed_entity}, 关联边数: {len(edges)})\n"]
        for idx, edge in enumerate(edges, 1):
            sub = edge["subject"]
            pred = edge["predicate"]
            obj = edge["object"]
            src = edge.get("source_uri", "未知")
            tier = edge.get("authority_tier", 2)
            tier_label = "正式发布(Tier 1)" if tier == 1 else ("工作区当前(Tier 2)" if tier == 2 else "草稿/归档(Tier 3)")
            hop = edge.get("min_hop", 1)
            lines.append(f"{idx}. `[{sub}]` ---(**{pred}**)---> `[{obj}]`  (第 {hop} 跳 | 来源: `{src}` | {tier_label})")

        return "\n".join(lines)


# 封装对外的 Tool 接口函数
def query_knowledge_graph(
    workspace_root: str,
    entity: str,
    max_hops: int = 2,
    pod_id: str = "global",
) -> str:
    """供智能体调用的轻量图 RAG 查询工具函数。

    Args:
        workspace_root: 工作区物理路径。
        entity: 目标查询实体（机构名、项目名、协议条款等）。
        max_hops: 最大图扩展跳数 (1~3)。
        pod_id: 隔离域。

    Returns:
        str: 结构化关联边与路径文本。
    """
    engine = LightGraphRAGEngine(workspace_root)
    edges = engine.query_subgraph(entity, max_hops=max_hops, pod_id=pod_id)
    return engine.format_subgraph_markdown(entity, edges)

