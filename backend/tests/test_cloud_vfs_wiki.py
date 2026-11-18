"""
v1.5.5 测试套件 3: CloudVFS 云端虚拟知识库与流式解析
"""
import io
import pytest
import shutil
import tempfile
import zipfile
from app.vfs.base import CloudFileMeta, CloudVFSSyncManager
from app.vfs.document_extractor import DocumentStreamExtractor, CloudWikiLRUCache
from app.search.hybrid_engine import HybridSearchEngine


@pytest.fixture
def temp_workspace():
    tmp_dir = tempfile.mkdtemp(prefix="test_agenthub_vfs_")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_cloud_vfs_sync_watermark(temp_workspace):
    # 初始化表
    _ = HybridSearchEngine(temp_workspace)
    sync_mgr = CloudVFSSyncManager(temp_workspace)

    f1 = CloudFileMeta(
        remote_uri="wecom://space/file1",
        title="2026规程.docx",
        size=1024,
        etag="etag_v1",
        mtime=100.0,
        authority_tier=1,
    )

    # 首次判定为修改
    modified = sync_mgr.filter_modified_files("mnt_wecom", [f1])
    assert len(modified) == 1

    # 记录同步水位线
    sync_mgr.update_sync_state("mnt_wecom", f1)

    # 再次检查未修改
    modified_again = sync_mgr.filter_modified_files("mnt_wecom", [f1])
    assert len(modified_again) == 0

    # 当 ETag 变化时再次判定修改
    f1_updated = CloudFileMeta(
        remote_uri="wecom://space/file1",
        title="2026规程.docx",
        size=2048,
        etag="etag_v2",
        mtime=120.0,
        authority_tier=1,
    )
    modified_3 = sync_mgr.filter_modified_files("mnt_wecom", [f1_updated])
    assert len(modified_3) == 1


def test_docx_stream_extractor():
    # 构造一个内存 DOCX 文件 (ZIP 格式，包含 word/document.xml)
    buf = io.BytesIO()
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>第一章 总则</w:t></w:r></w:p>
            <w:p><w:r><w:t>本章规定全国击剑锦标赛基本组织原则。</w:t></w:r></w:p>
            <w:p><w:r><w:t>第二章 参赛经费标准为每队 2000 元。</w:t></w:r></w:p>
        </w:body>
    </w:document>""".encode("utf-8")

    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc_xml)

    raw_bytes = buf.getvalue()
    chunks = DocumentStreamExtractor.extract_chunks(raw_bytes, "规程.docx", "wecom://space/doc1", authority_tier=1)
    assert len(chunks) >= 1
    assert "第一章 总则" in chunks[0]["content_text"]
    assert "2000 元" in chunks[0]["content_text"]


def test_cloud_wiki_lru_cache():
    remote_uri = "wecom://space/doc_test"
    sec_path = "Part_1"
    content = "这是从云端精读的规程第一部分详细原文内容"

    CloudWikiLRUCache.set_section_cache(remote_uri, sec_path, content)
    cached = CloudWikiLRUCache.get_section_cache(remote_uri, sec_path)
    assert cached == content

    # 查不存在的返回 None
    assert CloudWikiLRUCache.get_section_cache(remote_uri, "Part_99") is None
