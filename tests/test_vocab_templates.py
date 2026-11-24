"""
AgentHub Local v1.6.0 体育运动协会受控词表分层继承（Base + Domain Pack）与边界保护测试
测试范围：
1. 通用体育赛事底座 (base_sports_event) + 单项扩展包 (sports/fencing, equestrian 等) 继承与多级合并；
2. 知识库本地 terms 覆盖与 exclude 剔除机制；
3. 纯 ASCII 英文缩写词边界保护（避免 code 误撞 DE、hotel 误撞 EL）；
4. 知识库隔离与 create_preset_vocabulary 模板创建。
"""
import shutil
import tempfile
from pathlib import Path
import pytest

from backend.app.kb.controlled_vocabulary import (
    ControlledVocabularyAligner,
    resolve_vocab_hierarchy,
    create_preset_vocabulary,
    get_aligner,
)
from backend.app.kb.storage import KBStorageManager


class TestVocabTemplatesHierarchy:
    """分层继承与 ASCII 边界测试套件。"""

    def test_base_and_fencing_pack_inheritance(self):
        """测试 base_sports_event 与 sports/fencing 继承合并，同名首选词追加别名。"""
        raw_config = {
            "extends": ["base_sports_event", "sports/fencing"],
            "terms": {
                "中国击剑协会": ["中剑协", "CFA"],
                "直接淘汰赛": ["单败淘汰赛"],  # 扩展单项包中已有的 prefLabel
            },
            "exclude": ["反兴奋剂"],  # 测试剔除底座中不适用的词条
        }
        resolved, max_mtime = resolve_vocab_hierarchy(raw_config)
        assert max_mtime > 0.0

        # 1. 验证来自 base_sports_event 的共性词
        assert "撤销报名" in resolved
        assert "退赛" in resolved["撤销报名"]
        assert "主办单位" in resolved

        # 2. 验证来自 sports/fencing 的专业词
        assert "花剑" in resolved
        assert "重剑" in resolved
        assert "决一剑" in resolved
        assert "直接淘汰赛" in resolved
        assert "DE" in resolved["直接淘汰赛"]
        # 验证本地追加的别名被并集合并
        assert "单败淘汰赛" in resolved["直接淘汰赛"]

        # 3. 验证本地独立 terms
        assert "中国击剑协会" in resolved
        assert "中剑协" in resolved["中国击剑协会"]

        # 4. 验证 exclude 成功剔除
        assert "反兴奋剂" not in resolved

    def test_ascii_abbreviation_word_boundary_protection(self):
        """测试纯 ASCII 缩写词单词边界保护，严格防止嵌入英文单词时被误召回。"""
        raw_config = {
            "extends": ["base_sports_event", "sports/fencing", "sports/equestrian", "sports/basketball"],
        }
        resolved, _ = resolve_vocab_hierarchy(raw_config)
        aligner = ControlledVocabularyAligner(seed_vocab=resolved)

        # 1. 负面用例：嵌入英文单词中不应误撞
        # "code" 包含 "de"，但被边界拦截，不应触发“直接淘汰赛”
        # "hotel" 包含 "el"，但被边界拦截，不应触发“淘汰”
        # "route" 包含 "rt"，不应触发“弃权”
        false_positive_query = "Please review the source code in hotel room before the meeting."
        matched_false = aligner.scan_query_terms(false_positive_query)
        assert "直接淘汰赛" not in matched_false, "code 不应误触发 DE (直接淘汰赛)"
        assert "淘汰" not in matched_false, "hotel 不应误触发 EL (淘汰)"
        assert "弃权" not in matched_false, "route 不应误触发 RT (弃权)"

        # 2. 正面用例：独立英文缩写词应精准命中
        true_de_query = "男子重剑个人 DE 淘汰赛何时开赛？"
        matched_de = aligner.scan_query_terms(true_de_query)
        assert "直接淘汰赛" in matched_de, "独立的 DE 必须命中直接淘汰赛"

        true_ot_query = "比赛进入加时赛 OT 阶段"
        matched_ot = aligner.scan_query_terms(true_ot_query)
        assert "加时赛" in matched_ot, "独立的 OT 必须命中加时赛"

        true_dns_query = "由于伤病运动员赛前登记为 DNS"
        matched_dns = aligner.scan_query_terms(true_dns_query)
        assert "未上场" in matched_dns, "独立的 DNS 必须命中未上场"

        # 3. 中文词条无边界阻碍，正常命中
        cn_query = "赛前验马和走路线的时间安排"
        matched_cn = aligner.scan_query_terms(cn_query)
        assert "验马" in matched_cn
        assert "熟悉场地" in matched_cn

    def test_create_preset_and_kb_isolation(self):
        """测试预设模板自动生成与不同知识库之间的词表隔离。"""
        import uuid

        kb_fencing = f"kb_fence_{uuid.uuid4().hex[:6]}"
        kb_horse = f"kb_horse_{uuid.uuid4().hex[:6]}"

        try:
            # 1. 为击剑库生成 fencing 预设
            fencing_file = create_preset_vocabulary(kb_fencing, preset="fencing")
            assert fencing_file.exists()

            # 2. 为马术库生成 equestrian 预设
            horse_file = create_preset_vocabulary(kb_horse, preset="equestrian")
            assert horse_file.exists()

            # 3. 获取击剑库 aligner
            fencing_aligner = get_aligner(kb_fencing)
            # 击剑库应该包含“决一剑”，但绝不包含“验马”或“马房费”
            assert "决一剑" in fencing_aligner.seed_vocab
            assert "验马" not in fencing_aligner.seed_vocab
            assert "马房费" not in fencing_aligner.seed_vocab

            # 4. 获取马术库 aligner
            horse_aligner = get_aligner(kb_horse)
            # 马术库应该包含“验马”与“马房费”，绝不包含“决一剑”或“剑道”
            assert "验马" in horse_aligner.seed_vocab
            assert "马房费" in horse_aligner.seed_vocab
            assert "决一剑" not in horse_aligner.seed_vocab
            assert "剑道" not in horse_aligner.seed_vocab

            # 5. 两者共同拥有通用底座词汇
            assert "撤销报名" in fencing_aligner.seed_vocab
            assert "撤销报名" in horse_aligner.seed_vocab
            assert "主办单位" in fencing_aligner.seed_vocab
            assert "主办单位" in horse_aligner.seed_vocab
        finally:
            shutil.rmtree(KBStorageManager.get_kb_dir(kb_fencing), ignore_errors=True)
            shutil.rmtree(KBStorageManager.get_kb_dir(kb_horse), ignore_errors=True)
