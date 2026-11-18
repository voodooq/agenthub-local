"""
智能体 RAG 多跳追问自检闭环 (Agentic Retrieval Loop)
核心功能：
1. 封装最多 3 跳 (max_hops=3) 自主检索扩展闭环；
2. 证据链覆盖度自检 (Coverage Evaluator)：检测召回结果中出现的指针型引用（如“详见附件”、“参见规程”、“以通知为准”）；
3. 提取缺失实体并改写生成下一跳子查询 (Sub-Query)，定向补全证据链；
4. 聚合多跳切片池，按内容哈希与 RRF 分值合并去重输出。
"""
from dataclasses import dataclass
import re
from typing import Any

from ..core.logging_config import logger
from .hybrid_engine import HybridSearchEngine


@dataclass
class HopResult:
    hop_index: int
    query: str
    chunks: list[dict[str, Any]]
    unresolved_pointers: list[str]


class AgenticRetrievalLoop:
    """智能体多跳自主检索闭环控制器。"""

    # 常见未展开的间接指针模式
    POINTER_PATTERNS = [
        re.compile(r"(详见|参见|参考|见附录|见附件|查阅)([\u4e00-\u9fa5A-Za-z0-9_]{2,20}(?:附件|规程|通知|办法|标准|规则|规定|文件|目录|清单|表)[一二三四五六七八九十\d]*)"),
        re.compile(r"([\u4e00-\u9fa5A-Za-z0-9_]{2,20}(?:附件|附表|规程|细则|说明)[一二三四五六七八九十\d]+)"),
        re.compile(r"(以[《\“][^\”》]{2,20}[》\”]为准)"),
    ]

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.engine = HybridSearchEngine(workspace_root)

    def extract_unresolved_pointers(self, chunks: list[dict[str, Any]]) -> list[str]:
        """扫描当前召回切片中是否存在未展开的外部/附件引用指针。"""
        pointers: set[str] = set()
        for c in chunks:
            text = c.get("content_text", "")
            for pat in self.POINTER_PATTERNS:
                for match in pat.finditer(text):
                    # 取关键捕获组
                    g = match.group(1) if match.lastindex == 1 else match.group(2)
                    ptr = g.strip("《》“”\"' ")
                    if len(ptr) >= 3:
                        pointers.add(ptr)
        return list(pointers)[:5]

    def generate_sub_query(self, base_query: str, pointer: str) -> str:
        """根据未展开的指针生成下一跳补全查询。"""
        # 如果指针本身已具备完整语义，直接使用；否则与原查询关键名词结合
        clean_ptr = re.sub(r"^(详见|参见|参考|见)", "", pointer).strip()
        return f"{clean_ptr} {base_query}"

    async def execute_loop(
        self,
        initial_query: str,
        max_hops: int = 3,
        top_k: int = 6,
        pod_id: str = "global",
        source_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """执行最多 3 跳的自主多跳检索闭环。

        Args:
            initial_query: 初始用户提问。
            max_hops: 最大允许跳数 (1~3)。
            top_k: 每一跳检索数量。
            pod_id: 隔离域。
            source_type: 过滤数据源。

        Returns:
            list[dict[str, Any]]: 聚合多跳并去重后的最终证据切片集合。
        """
        hops_limit = max(1, min(max_hops, 3))
        visited_queries = {initial_query.strip()}
        aggregated_chunks: dict[str, dict[str, Any]] = {}  # content_hash -> chunk
        cur_query = initial_query.strip()

        for hop in range(1, hops_limit + 1):
            logger.info(f"AgenticRetrievalLoop 第 {hop} 跳检索: {cur_query}")
            step_chunks = await self.engine.search(
                cur_query,
                top_k=top_k,
                pod_id=pod_id,
                source_type=source_type,
            )

            # 收集并去重切片
            for c in step_chunks:
                chash = c.get("content_hash") or c.get("remote_uri", "") + str(c.get("start_line", 0))
                if chash not in aggregated_chunks:
                    c_copy = dict(c)
                    c_copy["retrieved_hop"] = hop
                    aggregated_chunks[chash] = c_copy

            if hop >= hops_limit:
                break

            # 覆盖度自检：扫描指针
            pointers = self.extract_unresolved_pointers(step_chunks)
            if not pointers:
                # 证据链已自洽完整，无需继续下一跳
                break

            # 寻找未检索过的下一个最有价值指针
            next_query = None
            for p in pointers:
                candidate_q = self.generate_sub_query(initial_query, p)
                if candidate_q not in visited_queries:
                    next_query = candidate_q
                    visited_queries.add(candidate_q)
                    break

            if not next_query:
                break
            cur_query = next_query

        # 最终合并切片并按 RRF 分值排序
        final_list = list(aggregated_chunks.values())
        final_list.sort(key=lambda x: x.get("rrf_score", 0.0), reverse=True)
        return final_list[:top_k * 2]
