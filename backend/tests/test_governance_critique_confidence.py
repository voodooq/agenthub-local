"""
v1.5.5 测试套件 4: 输出前自我批评与置信度治理
"""
import pytest
from app.governance.self_critique import SelfCritiqueCritic
from app.governance.confidence_evaluator import ConfidenceEvaluator


def test_self_critique_pass():
    user_q = "2026年竞赛报名费是多少？"
    evidence = ["证据1: 2026年击剑锦标赛每人报名费为 300 元整。"]
    draft = "根据2026年规程，本次锦标赛个人报名费为 300 元。"

    res = SelfCritiqueCritic.evaluate_draft(user_q, evidence, draft)
    assert res.passed is True
    assert len(res.issues) == 0
    assert res.score_deduction == 0.0


def test_self_critique_conflict_and_suspicious_numbers():
    user_q = "报名费是多少？"
    evidence = ["证据: 正式版规定报名费 300 元，历史旧草稿为 100 元。"]
    # 草稿胡编了 9999元，且未披露冲突
    draft = "报名费为 9999 元/人。"

    res = SelfCritiqueCritic.evaluate_draft(user_q, evidence, draft, has_version_conflict=True)
    assert res.passed is False
    assert len(res.issues) >= 1
    assert "9999元" in str(res.issues) or "冲突" in str(res.issues)
    assert "重新修正回复" in res.revision_hint


def test_confidence_evaluator_high_score():
    critique = SelfCritiqueCritic.evaluate_draft("费用", ["收费 300 元"], "收费 300 元")
    chunks = [{"rrf_score": 0.025, "authority_tier": 1, "remote_uri": "doc.md"}]
    tools = [{"name": "workspace_hybrid_search", "status": "completed"}]

    rep = ConfidenceEvaluator.evaluate(chunks, critique, tools, strict_mode=True)
    assert rep.score >= 0.85
    assert rep.tier_level == "high"
    assert rep.requires_confirmation is False


def test_confidence_evaluator_low_score_gate():
    critique = SelfCritiqueCritic.evaluate_draft("费用", [], "大概是 5000 元或者 8000 元吧，我不确定。")
    chunks = []  # 无检索依据
    tools = [{"name": "search_cloud_wiki", "status": "failed"}]

    rep = ConfidenceEvaluator.evaluate(chunks, critique, tools, strict_mode=True)
    assert rep.score < 0.60
    assert rep.tier_level == "low"
    # 严谨模式下低置信度必须触发人工确认
    assert rep.requires_confirmation is True
