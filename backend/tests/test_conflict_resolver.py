"""
v1.5.5 测试套件 2: 多源知识冲突与时序权威仲裁器
"""
import pytest
import shutil
import tempfile
from datetime import datetime
from app.search.conflict_resolver import ConflictResolver
from app.search.hybrid_engine import HybridSearchEngine
from app.search.embedding_provider import NullEmbeddingProvider


@pytest.fixture
def temp_workspace():
    tmp_dir = tempfile.mkdtemp(prefix="test_agenthub_conflict_")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_authority_tier_resolution():
    assert ConflictResolver.resolve_authority_tier("wecom://space/reg_official.docx") == 1
    assert ConflictResolver.resolve_authority_tier("docs/2026规程_发布版.md") == 1
    assert ConflictResolver.resolve_authority_tier("src/main.py") == 2
    assert ConflictResolver.resolve_authority_tier("docs/draft_old.md") == 3
    assert ConflictResolver.resolve_authority_tier("archive/backup_v1.txt") == 3


def test_effective_time_extraction():
    text = "本管理制度于 2026年11月10日 经审议通过正式发布生效实施。"
    t = ConflictResolver.extract_effective_time(text, "制度.md")
    dt = datetime.fromtimestamp(t)
    assert dt.year == 2026
    assert dt.month == 11
    assert dt.day == 10


@pytest.mark.asyncio
async def test_superseded_chain_and_arbitration_score(temp_workspace):
    engine = HybridSearchEngine(temp_workspace)

    t_old = datetime(2024, 1, 1).timestamp()
    t_new = datetime(2026, 11, 10).timestamp()

    # 先入库旧版草稿
    await engine.upsert_chunks([
        {
            "pod_id": "global",
            "remote_uri": "draft_old.md",
            "title": "2024年竞赛规程草稿",
            "section_path": "sec1",
            "start_line": 1,
            "end_line": 5,
            "content_text": "报名费每人 100 元",
            "effective_time": t_old,
            "authority_tier": 3,
        }
    ], provider=NullEmbeddingProvider())

    # 再入库正式新版
    await engine.upsert_chunks([
        {
            "pod_id": "global",
            "remote_uri": "wecom://space/2026竞赛规程_最终版.docx",
            "title": "2026年竞赛规程最终版",
            "section_path": "sec1",
            "start_line": 1,
            "end_line": 5,
            "content_text": "报名费调整为每人 300 元",
            "effective_time": t_new,
            "authority_tier": 1,
        }
    ], provider=NullEmbeddingProvider())

    # 查询
    res = await engine.search("报名费", top_k=5, include_superseded=True)
    assert len(res) == 2
    # 最新正式版排在第一位
    top = res[0]
    assert top["authority_tier"] == 1
    assert top["is_superseded"] == 0
    assert "300" in top["content_text"]

    # 旧版被降权
    second = res[1]
    assert second["is_superseded"] == 1
    assert second["arbitrated_score"] < top["arbitrated_score"]

    # 冲突检测
    conflict, instr = ConflictResolver.detect_conflicts_and_generate_disclosure(res)
    assert conflict is True
    assert "冲突审计提示" in instr
