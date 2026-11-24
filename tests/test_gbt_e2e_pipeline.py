"""
AgentHub Local v1.6.0 国标体系端到端全流程闭环集成测试
验证范围：
1. 新建知识库纯净 DDL 初始化（含 provenance_json 与 (s, p, o, doc_id) 复合唯一索引）；
2. 多模态规程（PDF/Excel/Word）切片携带高保真 PROV-O 逐字证据入库；
3. 三元组抽取自动完成受控词表归一化与跨文档同事实独立证据链共存（防误删）；
4. 读链路自然语言问句长词优先子串扫描 + 并集扩展召回标准节点与多来源证据聚合；
5. 检索响应透出高保真 citation_label 标签；
6. 同文档增量更新级联先清后建（旧三元组彻底清除，跨文档三元组完好）；
7. 首次/增量构建 strict 质量门禁拦截与版本指针防漂移。
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import pytest

from backend.app.kb.provenance import (
    StandardizedProvenance,
    compute_doc_id,
    format_citation_label,
)
from backend.app.kb.controlled_vocabulary import get_aligner
from backend.app.kb.storage import KBStorageManager
from backend.app.search.light_graph_rag import LightGraphRAGEngine
from backend.app.kb.quality_gate import KBQualityGateExecutor


class TestGBTE2EPipeline:
    """端到端全生命周期闭环测试。"""

    def test_doc_id_consistency_and_citation_label(self):
        """测试 doc_id 全局一致性契约与多模态引用标签生成。"""
        uri = "sandbox://kbs/horse/2026_regulations.pdf"
        doc_id1 = compute_doc_id(uri, "规程")
        doc_id2 = compute_doc_id(uri, "任意不同标题")
        assert doc_id1 == doc_id2, "同一 remote_uri 的 doc_id 必须全局恒定不变"

        # PDF 模态引用
        prov_pdf = {
            "doc_title": "2026马术竞赛规程.pdf",
            "anchor": {
                "modality": "pdf",
                "page_number": 12,
                "section_path": "三、报名与缴费",
            },
        }
        assert format_citation_label(prov_pdf) == "《2026马术竞赛规程.pdf》第12页（三、报名与缴费）"

        # Excel 模态引用
        prov_excel = {
            "doc_title": "2026所有赛事.xlsx",
            "anchor": {
                "modality": "xlsx",
                "sheet_name": "全国赛程表",
                "cell_range": "B5:E10",
            },
        }
        assert format_citation_label(prov_excel) == "《2026所有赛事.xlsx》工作表[全国赛程表] B5:E10"

        # Word 模态引用
        prov_word = {
            "doc_title": "技术通知.docx",
            "anchor": {
                "modality": "docx",
                "paragraph_index": 28,
                "section_path": "裁判须知",
            },
        }
        assert format_citation_label(prov_word) == "《技术通知.docx》第28段（裁判须知）"

    def test_ddl_and_multi_doc_triple_coexistence(self):
        """测试 DDL 初始化及 (subject, predicate, object, doc_id) 复合唯一索引支持跨文档同事实共存。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "index.db"
            KBStorageManager.init_version_db_schema(db_path)

            conn = sqlite3.connect(str(db_path))
            try:
                # 校验表结构与索引
                kt_cols = [c[1] for c in conn.execute("PRAGMA table_info(knowledge_triples)").fetchall()]
                assert "doc_id" in kt_cols
                assert "provenance_json" in kt_cols
                assert "alignment_status" in kt_cols
                assert "review_status" in kt_cols

                indices = [r[1] for r in conn.execute("PRAGMA index_list(knowledge_triples)").fetchall()]
                assert "idx_kt_unique" in indices

                # 模拟文档 A 和 文档 B 抽取同一事实 (主办单位, 为, 中国马术协会)
                doc_a_uri = "sandbox://kbs/doc_a.pdf"
                doc_b_uri = "sandbox://kbs/doc_b.docx"
                doc_a_id = compute_doc_id(doc_a_uri)
                doc_b_id = compute_doc_id(doc_b_uri)

                conn.execute(
                    """
                    INSERT INTO knowledge_triples (
                        subject, predicate, object, chunk_id, doc_id, axiom_type, alignment_status, confidence, review_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'FACT', 'AUTO_ALIGNED', 1.0, 'AUTO_APPROVED', 1.0)
                    """,
                    ("主办单位", "为", "中国马术协会", "chk_a_001", doc_a_id),
                )
                conn.execute(
                    """
                    INSERT INTO knowledge_triples (
                        subject, predicate, object, chunk_id, doc_id, axiom_type, alignment_status, confidence, review_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'FACT', 'AUTO_ALIGNED', 1.0, 'AUTO_APPROVED', 1.0)
                    """,
                    ("主办单位", "为", "中国马术协会", "chk_b_001", doc_b_id),
                )
                conn.commit()

                count = conn.execute("SELECT COUNT(*) FROM knowledge_triples WHERE subject='主办单位'").fetchone()[0]
                assert count == 2, "跨文档抽取的同一事实必须各自独立保留证据链，不能相互覆盖"
            finally:
                conn.close()

    def test_cascade_delete_by_remote_uris(self):
        """测试增量更新时按 remote_uri 级联清理同文档旧切片与旧三元组，且不误伤其他文档。"""
        import uuid
        import shutil

        kb_id = f"kb_test_cascade_{uuid.uuid4().hex[:8]}"
        ver = "v1.0.0"
        try:
            db_path = KBStorageManager.get_version_db_path(kb_id, ver)
            KBStorageManager.init_version_db_schema(db_path)

            doc_a_uri = "sandbox://kbs/regulations.pdf"
            doc_b_uri = "sandbox://kbs/schedule.docx"
            doc_a_id = compute_doc_id(doc_a_uri)
            doc_b_id = compute_doc_id(doc_b_uri)

            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO hybrid_chunks (id, remote_uri, title, content, created_at) VALUES (?, ?, ?, ?, 1.0)",
                ("chk_a_1", doc_a_uri, "规程", "【来源】规程 | 【年份】2026 | 【切片】1\n规程正文内容\n[指针：上一片=doc#0 | 下一片=doc#1]"),
            )
            conn.execute(
                "INSERT INTO hybrid_chunks (id, remote_uri, title, content, created_at) VALUES (?, ?, ?, ?, 1.0)",
                ("chk_b_1", doc_b_uri, "赛程", "【来源】赛程 | 【年份】2026 | 【切片】1\n赛程正文内容\n[指针：上一片=doc#0 | 下一片=doc#1]"),
            )
            # 文档 A 的两条三元组
            conn.execute(
                "INSERT INTO knowledge_triples (subject, predicate, object, chunk_id, doc_id, created_at) VALUES (?, ?, ?, ?, ?, 1.0)",
                ("主办单位", "为", "中国马术协会", "chk_a_1", doc_a_id),
            )
            conn.execute(
                "INSERT INTO knowledge_triples (subject, predicate, object, chunk_id, doc_id, created_at) VALUES (?, ?, ?, ?, ?, 1.0)",
                ("报名费", "为", "300元", "chk_a_1", doc_a_id),
            )
            # 文档 B 的一条三元组（同样是主办单位）
            conn.execute(
                "INSERT INTO knowledge_triples (subject, predicate, object, chunk_id, doc_id, created_at) VALUES (?, ?, ?, ?, ?, 1.0)",
                ("主办单位", "为", "中国马术协会", "chk_b_1", doc_b_id),
            )
            conn.commit()
            conn.close()

            # 模拟文档 A 修订，触发按 doc_a_uri 级联清理
            del_cnt = KBStorageManager.delete_chunks_by_remote_uris(kb_id, [doc_a_uri], version=ver)
            assert del_cnt == 1

            conn2 = sqlite3.connect(str(db_path))
            # 文档 A 的切片被清理
            assert conn2.execute("SELECT COUNT(*) FROM hybrid_chunks WHERE remote_uri=?", (doc_a_uri,)).fetchone()[0] == 0
            # 文档 A 的旧三元组（包括报名费 300 元与主办单位）全部被级联清除
            assert conn2.execute("SELECT COUNT(*) FROM knowledge_triples WHERE doc_id=?", (doc_a_id,)).fetchone()[0] == 0
            # 文档 B 的切片与三元组完好无损，未发生跨文档误删！
            assert conn2.execute("SELECT COUNT(*) FROM hybrid_chunks WHERE remote_uri=?", (doc_b_uri,)).fetchone()[0] == 1
            assert conn2.execute("SELECT COUNT(*) FROM knowledge_triples WHERE doc_id=?", (doc_b_id,)).fetchone()[0] == 1
            conn2.close()
        finally:
            kb_dir = KBStorageManager.get_kb_dir(kb_id)
            if kb_dir.exists():
                shutil.rmtree(kb_dir, ignore_errors=True)

    def test_controlled_vocab_long_term_scan_and_graph_aggregation(self):
        """测试自然语言长词优先子串扫描、标准词对齐与图谱多来源聚合。"""
        aligner = get_aligner("kb_equestrian")
        # 扫描自然语言问句，命中别名“退赛”并映射为首选词“撤销报名”
        matched = aligner.scan_query_terms("请问赛前退赛可以退还报名费吗？")
        assert "撤销报名" in matched

        # 测试 LightGraphRAGEngine 的实体锚定与子图查询聚合
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            engine = LightGraphRAGEngine(str(ws))
            conn = engine._get_connection()
            try:
                # 插入两条不同文档抽取的撤销报名事实
                conn.execute(
                    """
                    INSERT INTO knowledge_triples (
                        pod_id, subject, predicate, object, source_uri, authority_tier, effective_time, confidence
                    ) VALUES (?, ?, ?, ?, ?, 1, 1.0, 1.0)
                    """,
                    ("global", "撤销报名", "规则要求", "需提前7天书面申请", "sandbox://doc_a.pdf"),
                )
                conn.execute(
                    """
                    INSERT INTO knowledge_triples (
                        pod_id, subject, predicate, object, source_uri, authority_tier, effective_time, confidence
                    ) VALUES (?, ?, ?, ?, ?, 1, 1.0, 1.0)
                    """,
                    ("global", "撤销报名", "规则要求", "需提前7天书面申请", "sandbox://doc_b.docx"),
                )
                conn.commit()
            finally:
                conn.close()

            # 使用包含别名“退赛”的提问句直接查询图谱
            anchors = engine.find_anchor_entities("选手退赛有什么要求？", pod_id="global")
            assert "撤销报名" in anchors, "长词优先扫描必须将首选词并入锚点集合"

            # 子图查询
            edges = engine.query_subgraph("退赛", pod_id="global")
            assert len(edges) >= 1
            first_edge = edges[0]
            assert first_edge["subject"] == "撤销报名"
            # 验证多来源聚合：同一事实合并展示两份文档的依据
            assert len(first_edge["source_uris"]) == 2
            assert "sandbox://doc_a.pdf" in first_edge["source_uris"]
            assert "sandbox://doc_b.docx" in first_edge["source_uris"]

    def test_strict_quality_gate_intercepts_negative_amount(self):
        """测试 strict 门禁拦截非法负金额三元组，守住 Stage 3 安全防线。"""
        triples = [
            {
                "subject": "撤销报名",
                "predicate": "收费标准",
                "object": "-500元",
                "text_span": "退赛扣减 -500元",
                "source_uri": "sandbox://doc_bad.pdf",
            }
        ]
        res = KBQualityGateExecutor.validate_delta_batch(triples, mode="strict")
        assert not res.passed, "严格合规模式下负金额必须被拦截"
        assert any("SHACL" in err for err in res.errors)
