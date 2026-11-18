"""
工作区混合检索与云端 Wiki 原生工具 (Workspace Hybrid Search & Cloud Wiki Tools)
核心工具：
1. workspace_hybrid_search: 向量 + FTS5 + RRF + 权威加权的本地工作区代码与文档检索；
2. search_cloud_wiki: 检索企业微信微盘/WebDAV/OSS 挂载的云端规程与知识库切片；
3. read_cloud_wiki_section: 按需精读云端知识库特定章节全文（带 24 小时本地 LRU 缓存）；
4. query_knowledge_graph: 基于纯 SQLite 递归 CTE 的 2~3 跳知识图谱关系推理。
"""
import asyncio
from pathlib import Path
from typing import Any

from ..core.logging_config import logger
from ..search.hybrid_engine import HybridSearchEngine
from ..search.agentic_retrieval import AgenticRetrievalLoop
from ..search.conflict_resolver import ConflictResolver
from ..search.light_graph_rag import LightGraphRAGEngine, query_knowledge_graph
from ..vfs.document_extractor import CloudWikiLRUCache


async def workspace_hybrid_search(
    workspace_root: str,
    query: str,
    top_k: int = 10,
    pod_id: str = "global",
    use_agentic_loop: bool = True,
    include_archived: bool = False,
) -> str:
    """在当前本地工作区执行多维度混合语义检索。

    Args:
        workspace_root: 工作区物理路径。
        query: 用户的自然语言问句或代码符号。
        top_k: 期望返回的高置信证据切片上限。
        pod_id: 客户/群组隔离域。
        use_agentic_loop: 是否启用最多 3 跳的自主追问自检闭环。
        include_archived: 是否包含已废止/历史归档版本规程（默认 False）。

    Returns:
        str: 包含“时空身份证”和冲突审计警示的标准证据块。
    """
    clean_q = query.strip()
    if not clean_q:
        return "检索关键词为空。"

    try:
        if use_agentic_loop and not include_archived:
            loop = AgenticRetrievalLoop(workspace_root)
            chunks = await loop.execute_loop(clean_q, max_hops=3, top_k=top_k, pod_id=pod_id)
        else:
            engine = HybridSearchEngine(workspace_root)
            chunks = await engine.search(
                clean_q,
                top_k=top_k,
                pod_id=pod_id,
                include_superseded=include_archived,
            )
    except Exception as e:
        logger.warning(f"工作区混合检索执行异常: {e}")
        return f"检索异常: {str(e)}"

    if not chunks:
        return f"未在工作区找到与「{clean_q}」高度相关的代码片段或文档记录。"

    # 冲突检测与披露警示
    has_conflict, disclosure_text = ConflictResolver.detect_conflicts_and_generate_disclosure(chunks)

    lines = [f"### 🔍 工作区混合检索结果 (`query=\"{clean_q}\"`, 命中数: {len(chunks)})\n"]
    if has_conflict and disclosure_text:
        lines.append(disclosure_text + "\n")

    for idx, c in enumerate(chunks, 1):
        st_text = c.get("spatial_temporal_text") or ConflictResolver.format_spatial_temporal_id(c, idx)
        lines.append(f"{st_text}\n")

    return "\n".join(lines)


async def search_cloud_wiki(
    workspace_root: str,
    query: str,
    top_k: int = 6,
    pod_id: str = "global",
) -> str:
    """在已挂载的企业微信微盘、WebDAV 或 OSS 云端知识库中检索规程与制度。

    Args:
        workspace_root: 工作区物理路径。
        query: 检索业务提问或制度名称。
        top_k: 返回切片上限。
        pod_id: 隔离域。

    Returns:
        str: 格式化的云端规程切片与时空身份证。
    """
    clean_q = query.strip()
    if not clean_q:
        return "检索关键词为空。"

    engine = HybridSearchEngine(workspace_root)
    # 过滤云端 source_type
    try:
        # 召回非 local 的云端源
        chunks = await engine.search(clean_q, top_k=top_k * 2, pod_id=pod_id)
        cloud_chunks = [c for c in chunks if c.get("source_type") in ("wecom", "webdav", "oss", "cloud")][:top_k]
        if not cloud_chunks:
            cloud_chunks = chunks[:top_k]
    except Exception as e:
        logger.warning(f"云端 Wiki 检索异常: {e}")
        return f"云端检索异常: {str(e)}"

    if not cloud_chunks:
        return f"未在云端知识库中找到与「{clean_q}」匹配的规程文档。"

    has_conflict, disclosure_text = ConflictResolver.detect_conflicts_and_generate_disclosure(cloud_chunks)

    lines = [f"### ☁️ 云端 LLM Wiki 规程检索结果 (`query=\"{clean_q}\"`, 命中数: {len(cloud_chunks)})\n"]
    if has_conflict and disclosure_text:
        lines.append(disclosure_text + "\n")

    for idx, c in enumerate(cloud_chunks, 1):
        st_text = c.get("spatial_temporal_text") or ConflictResolver.format_spatial_temporal_id(c, idx)
        lines.append(f"{st_text}\n")

    return "\n".join(lines)


async def read_cloud_wiki_section(
    remote_uri: str,
    section_path: str,
    workspace_root: str = "",
) -> str:
    """按需精读云端知识库特定章节原文（优先命中 24 小时本地 LRU 缓存）。

    Args:
        remote_uri: 云端规程 URI（如 wecom://space/fileid）。
        section_path: 章节面包屑或段落定位符。
        workspace_root: 可选工作区路径。

    Returns:
        str: 目标章节详细原文。
    """
    # 1. 查询 LRU 缓存
    cached = CloudWikiLRUCache.get_section_cache(remote_uri, section_path)
    if cached:
        return f"### 📖 云端规程章节精读 (已命中本地缓存)\n> 来源: `{remote_uri} §{section_path}`\n\n{cached}"

    # 2. 从本地 SQLite hybrid_chunks 表直接查找已解析切片
    if workspace_root:
        engine = HybridSearchEngine(workspace_root)
        with engine._get_connection() as conn:
            row = conn.execute("""
                SELECT content_text FROM hybrid_chunks
                WHERE remote_uri = ? AND section_path = ?
                LIMIT 1
            """, (remote_uri, section_path)).fetchone()
            if row:
                content = row["content_text"]
                CloudWikiLRUCache.set_section_cache(remote_uri, section_path, content)
                return f"### 📖 云端规程章节精读\n> 来源: `{remote_uri} §{section_path}`\n\n{content}"

    return f"暂无法直接拉取云端规程章节 `{remote_uri} §{section_path}` 的完整原文，建议使用 search_cloud_wiki 进行摘要检索。"


__all__ = [
    "workspace_hybrid_search",
    "search_cloud_wiki",
    "read_cloud_wiki_section",
    "query_knowledge_graph",
]

