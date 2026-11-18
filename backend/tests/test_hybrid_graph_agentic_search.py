"""
v1.5.5 测试套件 1: 混合检索、递归图谱、智能体 RAG 与 Pod 隔离
"""
import pytest
import shutil
import tempfile
from pathlib import Path
import numpy as np

from app.search.embedding_provider import NullEmbeddingProvider, normalize_l2
from app.search.ast_chunker import AstChunker
from app.search.hybrid_engine import HybridSearchEngine
from app.search.light_graph_rag import LightGraphRAGEngine, KnowledgeTriple
from app.search.agentic_retrieval import AgenticRetrievalLoop


@pytest.fixture
def temp_workspace():
    tmp_dir = tempfile.mkdtemp(prefix="test_agenthub_ws_")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_normalize_l2():
    vec = np.array([3.0, 4.0], dtype=np.float32)
    normed = normalize_l2(vec)
    assert np.isclose(np.linalg.norm(normed), 1.0)


@pytest.mark.asyncio
async def test_ast_chunker_python(temp_workspace):
    py_code = """
import os

class Calculator:
    def add(self, a, b):
        return a + b

    def subtract(self, a, b):
        return a - b

def helper():
    pass
"""
    f_path = Path(temp_workspace) / "calc.py"
    f_path.write_text(py_code, encoding="utf-8")

    chunks = AstChunker.chunk_file(f_path, "calc.py")
    assert len(chunks) >= 2
    paths = [c.section_path for c in chunks]
    assert any("Calculator" in p for p in paths)


@pytest.mark.asyncio
async def test_hybrid_search_and_pod_isolation(temp_workspace):
    engine = HybridSearchEngine(temp_workspace)

    # 写入两个不同 pod 的切片
    await engine.upsert_chunks([
        {
            "pod_id": "pod_alpha",
            "remote_uri": "doc_a.md",
            "title": "Alpha 制度",
            "section_path": "sec1",
            "start_line": 1,
            "end_line": 10,
            "content_text": "Alpha 机构的参赛费用为 500 元。",
            "authority_tier": 1,
        },
        {
            "pod_id": "pod_beta",
            "remote_uri": "doc_b.md",
            "title": "Beta 制度",
            "section_path": "sec1",
            "start_line": 1,
            "end_line": 10,
            "content_text": "Beta 机构的参赛费用为 800 元。",
            "authority_tier": 1,
        },
    ], provider=NullEmbeddingProvider())

    # 检索 pod_alpha
    res_alpha = await engine.search("参赛费用", pod_id="pod_alpha")
    assert len(res_alpha) == 1
    assert "Alpha" in res_alpha[0]["content_text"]

    # 检索 pod_beta
    res_beta = await engine.search("参赛费用", pod_id="pod_beta")
    assert len(res_beta) == 1
    assert "Beta" in res_beta[0]["content_text"]


@pytest.mark.asyncio
async def test_recursive_subgraph_traversal(temp_workspace):
    engine = LightGraphRAGEngine(temp_workspace)
    # 初始化表
    _ = HybridSearchEngine(temp_workspace)

    t1 = KnowledgeTriple("击剑俱乐部", "隶属于", "击剑协会", "reg.md", authority_tier=1)
    t2 = KnowledgeTriple("击剑协会", "发布规程", "2026花剑赛", "reg.md", authority_tier=1)
    t3 = KnowledgeTriple("2026花剑赛", "限定年龄", "U14组别", "reg.md", authority_tier=1)
    engine.upsert_triples([t1, t2, t3])

    edges = engine.query_subgraph("击剑俱乐部", max_hops=3)
    assert len(edges) == 3
    hops = [e["min_hop"] for e in edges]
    assert hops == [1, 2, 3]


@pytest.mark.asyncio
async def test_agentic_retrieval_loop(temp_workspace):
    engine = HybridSearchEngine(temp_workspace)
    await engine.upsert_chunks([
        {
            "pod_id": "global",
            "remote_uri": "main_rule.md",
            "title": "总则",
            "section_path": "p1",
            "start_line": 1,
            "end_line": 10,
            "content_text": "全国锦标赛报名详情详见附件一规程。",
            "authority_tier": 1,
        },
        {
            "pod_id": "global",
            "remote_uri": "attachment_1.md",
            "title": "附件一规程",
            "section_path": "p1",
            "start_line": 1,
            "end_line": 10,
            "content_text": "附件一规程：U14组个人赛费用 300 元，兼项限制每人至多两项。",
            "authority_tier": 1,
        },
    ], provider=NullEmbeddingProvider())

    loop = AgenticRetrievalLoop(temp_workspace)
    chunks = await loop.execute_loop("全国锦标赛报名费用", max_hops=3)
    assert len(chunks) >= 2
    # 验证是否成功关联检索到了附件一规程
    all_text = " ".join([c["content_text"] for c in chunks])
    assert "附件一规程" in all_text
