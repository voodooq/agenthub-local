"""
v1.5.5 测试套件 5: 无头浏览器与多模态解析管道
"""
import pytest
import shutil
import tempfile
from pathlib import Path
from app.tools.browser_agent import browser_session_manager
from app.multimodal.vision_extractor import VisionExtractor
from app.multimodal.audio_transcriber import AudioTranscriber
from app.search.hybrid_engine import HybridSearchEngine


@pytest.fixture
def temp_workspace():
    tmp_dir = tempfile.mkdtemp(prefix="test_agenthub_mm_")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_browser_ax_node_compression():
    raw_node = {
        "role": "button",
        "name": "立即提交报名",
        "value": "",
        "children": [
            {"role": "none", "name": "", "children": []},
            {"role": "statictext", "name": "确认", "children": []},
        ],
    }
    comp = browser_session_manager._compress_ax_node(raw_node)
    assert comp is not None
    assert comp["role"] == "button"
    assert comp["name"] == "立即提交报名"
    # 空的 none 节点被过滤
    assert len(comp["children"]) == 1
    assert comp["children"][0]["name"] == "确认"


@pytest.mark.asyncio
async def test_vision_extractor_metadata(temp_workspace):
    # 模拟一张图片文件
    img_path = Path(temp_workspace) / "table.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    res = await VisionExtractor.extract_structured_data(
        image_path=str(img_path),
        workspace_root=temp_workspace,
    )
    assert res["success"] is True
    assert "table.png" in res["filename"]

    # 验证是否写入了 hybrid_chunks 索引
    engine = HybridSearchEngine(temp_workspace)
    with engine._get_connection() as conn:
        row = conn.execute("SELECT * FROM hybrid_chunks WHERE source_type = 'multimodal'").fetchone()
        assert row is not None
        assert "table.png" in row["title"]


def test_audio_transcriber_fallback():
    # 测试音频文件不存在时安全返回
    res = AudioTranscriber.transcribe("non_existent_audio.mp3")
    assert res["success"] is False
    assert "不存在" in res["error"]
