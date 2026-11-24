"""
AgentHub Local v1.6.0 国标化 PROV-O 证据链与受控词表对齐单元测试
验证：
1. LocationAnchor 模态自适应：PDF/Word/Excel 坐标与旧库迁移 is_legacy_migrated
2. StandardizedProvenance 逐字证据 text_span 强约束与 JSON 序列化
3. ControlledVocabularyAligner 双路 Top-K 召回与合并重排加权（解决语义召回盲区）
"""
import unittest

from backend.app.kb.provenance import LocationAnchor, StandardizedProvenance
from backend.app.kb.controlled_vocabulary import ControlledVocabularyAligner


class TestGBTProvenanceAndVocab(unittest.TestCase):
    def test_pdf_provenance_anchor(self):
        """测试 PDF 模态的物理页码与字符偏移量约束。"""
        prov = StandardizedProvenance.build_from_chunk(
            doc_title="2026年马术锦标赛规程.pdf",
            content="比赛报名截止时间为赛前7天，逾期不再受理。",
            remote_uri="webdav://mnt_1/docs/2026年马术锦标赛规程.pdf",
            section_path="3.1 报名要求",
            page_number=5,
            char_start=120,
            char_end=150,
            evidence_quote="比赛报名截止时间为赛前7天",
        )
        self.assertEqual(prov.anchor.modality, "pdf")
        self.assertEqual(prov.anchor.page_number, 5)
        self.assertEqual(prov.anchor.char_start, 120)
        self.assertEqual(prov.anchor.char_end, 150)
        self.assertEqual(prov.text_span, "比赛报名截止时间为赛前7天")

        # 验证 JSON 导出再还原无损
        d = prov.to_compact_dict()
        self.assertEqual(d["anchor"]["modality"], "pdf")
        restored = StandardizedProvenance.model_validate(d)
        self.assertEqual(restored.doc_id, prov.doc_id)

    def test_legacy_migrated_provenance_fallback(self):
        """测试旧库切片原地升级：无物理页码时优雅降级为 0，不崩溃中断。"""
        prov = StandardizedProvenance.build_from_chunk(
            doc_title="历史老马术规程.pdf",
            content="这是一条历史老切片，未曾记录物理页码。",
            remote_uri="file:///legacy/doc.pdf",
            page_number=None,
            is_legacy_migrated=True,
        )
        self.assertEqual(prov.anchor.modality, "pdf")
        self.assertEqual(prov.anchor.page_number, 0)
        self.assertTrue(prov.anchor.is_legacy_migrated)

    def test_word_provenance_anchor(self):
        """测试 Word (.docx) 模态自适应：无物理页码，依赖段落序号与章节路径。"""
        prov = StandardizedProvenance.build_from_chunk(
            doc_title="击剑协会运动员认证办法.docx",
            content="运动员注册费用为每人每年100元整。",
            remote_uri="file:///data/docs/击剑协会运动员认证办法.docx",
            section_path="第四章 费用说明",
            paragraph_index=28,
            evidence_quote="运动员注册费用为每人每年100元整。",
        )
        self.assertEqual(prov.anchor.modality, "docx")
        self.assertEqual(prov.anchor.paragraph_index, 28)
        self.assertIsNone(prov.anchor.cell_range)
        self.assertEqual(prov.text_span, "运动员注册费用为每人每年100元整。")

    def test_excel_provenance_anchor(self):
        """测试 Excel (.xlsx) 模态自适应：依赖工作表名与单元格范围。"""
        prov = StandardizedProvenance.build_from_chunk(
            doc_title="2026年赛事日程表.xlsx",
            content="杭州站 2026-05-01 场地障碍赛",
            remote_uri="oss://bucket/2026年赛事日程表.xlsx",
            section_path="场地赛程",
            sheet_name="全国联赛赛程表",
            cell_range="B5:E5",
            evidence_quote="杭州站 2026-05-01 场地障碍赛",
        )
        self.assertEqual(prov.anchor.modality, "xlsx")
        self.assertEqual(prov.anchor.sheet_name, "全国联赛赛程表")
        self.assertEqual(prov.anchor.cell_range, "B5:E5")

    def test_controlled_vocabulary_exact_and_alt_match(self):
        """测试受控词表通道 1：精确首选词与同义别名秒级对齐。"""
        seed_vocab = {
            "主办单位": ["主办方", "发起单位", "指导单位"],
            "承办单位": ["承办方", "执行机构"],
            "场地障碍赛": ["障碍赛", "Show Jumping"],
        }
        aligner = ControlledVocabularyAligner(seed_vocab)

        # 命中 prefLabel
        term1, status1, conf1 = aligner.align_concept("主办单位")
        self.assertEqual(term1, "主办单位")
        self.assertEqual(status1, "AUTO_ALIGNED")
        self.assertEqual(conf1, 1.0)

        # 命中 altLabel
        term2, status2, conf2 = aligner.align_concept("主办方")
        self.assertEqual(term2, "主办单位")
        self.assertEqual(status2, "AUTO_ALIGNED")
        self.assertEqual(conf2, 1.0)

    def test_controlled_vocabulary_dual_top_k_recall_prevents_semantic_blind_spot(self):
        """测试双路 Top-K 召回重排：解决字面不重合但语义高度一致的召回盲区（如‘退赛’vs‘撤销报名’）。"""
        seed_vocab = {
            "退役申请": ["退役手续"],          # 字面和‘退赛’有一点点重合（都带‘退’），但语义无关
            "撤销报名": ["取消参赛资格"],      # 字面和‘退赛’完全不重合，但语义高度吻合
            "场地障碍赛": ["障碍赛"],
        }
        aligner = ControlledVocabularyAligner(seed_vocab, auto_align_threshold=0.80)

        # 模拟语义向量模型打分：虽然‘退赛’与‘撤销报名’字面无重合，但向量打分 0.95
        def mock_vector_scorer(cand: str, target: str) -> float:
            if cand == "退赛" and target == "撤销报名":
                return 0.95
            if cand == "退赛" and target == "退役申请":
                return 0.20
            return 0.10

        # 双路召回机制下，‘撤销报名’即使编辑距离落后，也能被向量路直接召回并重排登顶
        term, status, conf = aligner.align_concept("退赛", embedding_scorer=mock_vector_scorer)
        self.assertEqual(term, "撤销报名", "双路召回必须能克服字面重合盲区，正确召回语义对齐词")
        self.assertEqual(status, "AUTO_ALIGNED")
        self.assertGreaterEqual(conf, 0.80)

    def test_controlled_vocabulary_provisional_for_new_terms(self):
        """测试低置信度陌生专业新词自动标记为 PROVISIONAL（不强制误归类）。"""
        seed_vocab = {
            "马术比赛": ["马术赛事"],
        }
        aligner = ControlledVocabularyAligner(seed_vocab)

        term, status, conf = aligner.align_concept("量子计算抗力协议")
        self.assertEqual(term, "量子计算抗力协议")
        self.assertEqual(status, "PROVISIONAL")
        self.assertLess(conf, 0.70)


if __name__ == "__main__":
    unittest.main()
