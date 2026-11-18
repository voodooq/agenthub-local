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

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 60000;")
        return conn

    @staticmethod
    def extract_triples_from_text(
        text: str, source_uri: str, authority_tier: int = 2, effective_time: float = 0.0
    ) -> list[KnowledgeTriple]:
        """从文本段落中启发式抽取核心关系三元组。"""
        triples: list[KnowledgeTriple] = []
        eff = effective_time or time.time()

        # 1. 结构化键值抽取 (例如：报名费: 300元, 负责人：张三)
        kv_pattern = re.compile(r"^[\s\-\*]*([A-Za-z0-9_\u4e00-\u9fa5]{2,20})[\s]*[:：][\s]*([^\r\n]{2,50})$", re.M)
        for match in kv_pattern.finditer(text):
            k, v = match.group(1).strip(), match.group(2).strip()
            # 过滤明显不是三元组的内容
            if not k.startswith("http") and not v.startswith("http"):
                triples.append(
                    KnowledgeTriple(
                        subject=source_uri.split("/")[-1].split(".")[0],
                        predicate=k,
                        object=v,
                        source_uri=source_uri,
                        authority_tier=authority_tier,
                        effective_time=eff,
                    )
                )

        # 2. 中文常见谓词抽取：X的Y是Z / X负责Y / X包含Y / X隶属于Y / X兼项限制Y
        patterns = [
            (re.compile(r"([A-Za-z0-9_\u4e00-\u9fa5]{2,15})的([A-Za-z0-9_\u4e00-\u9fa5]{2,10})为?是?([A-Za-z0-9_\u4e00-\u9fa5]{2,30})"), 1, 2, 3),
            (re.compile(r"([A-Za-z0-9_\u4e00-\u9fa5]{2,15})(负责|主管|对接|承办|协办|归属|隶属|兼项限制|规定)([A-Za-z0-9_\u4e00-\u9fa5]{2,30})"), 1, 2, 3),
            (re.compile(r"([A-Za-z0-9_\u4e00-\u9fa5]{2,15})(费用|标准|金额|限额)为([A-Za-z0-9_\u4e00-\u9fa5]{2,20})"), 1, 2, 3),
        ]

        for pat, s_idx, p_idx, o_idx in patterns:
            for match in pat.finditer(text):
                sub = match.group(s_idx).strip()
                pred = match.group(p_idx).strip()
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
                for t in triples:
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

    def query_subgraph(
        self,
        seed_entity: str,
        max_hops: int = 2,
        pod_id: str = "global",
        max_edges: int = 30,
    ) -> list[dict[str, Any]]:
        """基于 SQLite 原生 WITH RECURSIVE 执行双向 2~3 跳子图查询。

        Args:
            seed_entity: 起点实体名称。
            max_hops: 最大递归跳数（限制 <= 3）。
            pod_id: 隔离域。
            max_edges: 返回边数量上限。

        Returns:
            list[dict[str, Any]]: 关系边列表，包含起点、终点、谓词与跳数。
        """
        clean_seed = seed_entity.strip()
        if not clean_seed:
            return []

        hops_limit = max(1, min(max_hops, 3))

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
            WHERE pod_id = ? AND (subject = ? OR subject LIKE '%' || ? || '%')
            
            UNION ALL
            
            SELECT 
                subject AS cur_node,
                subject, predicate, object, source_uri, authority_tier,
                1 AS hop,
                '|' || object || '|' || subject || '|' AS visited
            FROM knowledge_triples
            WHERE pod_id = ? AND (object = ? OR object LIKE '%' || ? || '%')
            
            UNION ALL
            
            -- 递归扩展 (hop <= max_hops)
            SELECT 
                t.object AS cur_node,
                t.subject, t.predicate, t.object, t.source_uri, t.authority_tier,
                g.hop + 1,
                g.visited || t.object || '|'
            FROM knowledge_triples t
            JOIN graph_cte g ON t.subject = g.cur_node
            WHERE t.pod_id = ? 
              AND g.hop < {hops_limit}
              AND instr(g.visited, '|' || t.object || '|') = 0
        )
        SELECT DISTINCT subject, predicate, object, source_uri, authority_tier, MIN(hop) as min_hop
        FROM graph_cte
        GROUP BY subject, predicate, object, source_uri
        ORDER BY min_hop ASC, authority_tier ASC
        LIMIT ?;
        """

        params = [
            pod_id, clean_seed, clean_seed,
            pod_id, clean_seed, clean_seed,
            pod_id,
            max_edges,
        ]

        with closing(self._get_connection()) as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
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
