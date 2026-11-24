"""
AgentHub Local v1.6.0 国标质量门禁 (Quality Gate) 专项测试
验证：
1. 零 JVM DFS 拓扑环检测：自环过滤、强继承环路拦截与宽泛环路告警
2. 编程式 rdflib 构建与中文特殊字符防崩溃（杜绝 BadSyntax）
3. 中文日期与金额类型归一化
4. Delta PySHACL 增量校验与负金额/异常日期识别
5. 敏捷模式放行 vs 合规模式阻断
6. 执行性能（稳定在 100ms~300ms 内）
"""
import time
import unittest

from backend.app.kb.quality_gate import KBQualityGateExecutor


class TestKBQualityGate(unittest.TestCase):
    def test_topological_cycle_classification(self):
        """测试拓扑环检测的自环过滤、强继承阻断与宽泛包含告警。"""
        # 1. 包含 A->A 自环 + 强继承环路 (A 属于 B, B 属于 A)
        bad_triples = [
            {"subject": "全国总决赛", "predicate": "属于", "object": "U系列联赛"},
            {"subject": "U系列联赛", "predicate": "属于", "object": "全国总决赛"},
            {"subject": "中马协", "predicate": "主办", "object": "中马协"}, # 自环
        ]
        errors, warnings = KBQualityGateExecutor.detect_topological_cycles(bad_triples)
        self.assertTrue(any("循环继承" in e for e in errors), "强继承环路必须触发 Error 阻断")
        self.assertTrue(any("自环" in w for w in warnings), "自环关系必须转为 Warning 过滤")

        # 2. 宽泛包含环路 (A 包含 B, B 包含 A) - 不得阻断，仅记 Warning
        broad_triples = [
            {"subject": "报名费", "predicate": "包含", "object": "保险费"},
            {"subject": "保险费", "predicate": "包含", "object": "报名费"},
        ]
        b_errors, b_warnings = KBQualityGateExecutor.detect_topological_cycles(broad_triples)
        self.assertEqual(len(b_errors), 0, "宽泛包含关系成环不得作为阻断级 Error")
        self.assertTrue(any("宽泛语义包含环路" in w for w in b_warnings))

    def test_chinese_date_and_amount_normalization(self):
        """测试中文日期与金额的类型安全提取。"""
        d1 = KBQualityGateExecutor._normalize_date_literal("2026年11月20日18:00前")
        self.assertEqual(d1, "2026-11-20")

        d2 = KBQualityGateExecutor._normalize_date_literal("报名截止：2026/05/01")
        self.assertEqual(d2, "2026-05-01")

        a1 = KBQualityGateExecutor._normalize_amount_literal("个人赛报名费300元/人")
        self.assertEqual(a1, 300.0)

        a2 = KBQualityGateExecutor._normalize_amount_literal("500.50 RMB")
        self.assertEqual(a2, 500.5)

    def test_delta_shacl_validation_negative_amount_violation(self):
        """测试 Delta PySHACL 拦截非法负金额条款。"""
        illegal_triples = [
            {
                "subject": "马术障碍个人赛",
                "predicate": "收费标准",
                "object": "-500元", # 非法负数金额
                "text_span": "个人赛退款扣减 -500元",
            }
        ]
        # 合规 strict 模式下必须拦截
        strict_res = KBQualityGateExecutor.validate_delta_batch(illegal_triples, mode="strict")
        self.assertFalse(strict_res.passed)
        self.assertTrue(any("SHACL" in e for e in strict_res.errors))

    def test_delta_shacl_validation_valid_batch_passes_under_300ms(self):
        """测试合规正常规程条款 100% 验证通过且耗时在 300ms 内。"""
        valid_triples = [
            {
                "subject": "2026年全国击剑锦标赛",
                "predicate": "主办单位",
                "object": "中国击剑协会",
                "text_span": "本届比赛由中国击剑协会主办。",
            },
            {
                "subject": "2026年全国击剑锦标赛",
                "predicate": "开赛日期",
                "object": "2026年10月01日",
                "text_span": "比赛定于2026年10月01日在青岛举行。",
            },
            {
                "subject": "2026年全国击剑锦标赛",
                "predicate": "参赛费",
                "object": "350元/人",
                "text_span": "个人赛参赛费为350元/人。",
            },
        ]
        t0 = time.perf_counter()
        res = KBQualityGateExecutor.validate_delta_batch(valid_triples, mode="strict")
        duration = (time.perf_counter() - t0) * 1000

        self.assertTrue(res.passed, f"正常规程条款应顺利通过门禁，实际 errors: {res.errors}")
        self.assertEqual(len(res.errors), 0)
        self.assertLess(duration, 300.0, f"单次增量门禁必须在 300ms 内完成，实际耗时: {duration:.2f}ms")

    def test_agile_mode_warns_without_blocking(self):
        """测试敏捷模式（agile）：轻微违规（如空证据 text_span）仅记 Warning，依然放行发布。"""
        weak_triples = [
            {
                "subject": "临时测试赛事",
                "predicate": "举办地",
                "object": "杭州市",
                "text_span": "", # 故意缺失证据
            }
        ]
        res = KBQualityGateExecutor.validate_delta_batch(weak_triples, mode="agile")
        self.assertTrue(res.passed, "敏捷模式下缺失证据不应阻断发布")
        self.assertTrue(any("text_span 为空" in w for w in res.warnings))


if __name__ == "__main__":
    unittest.main()

