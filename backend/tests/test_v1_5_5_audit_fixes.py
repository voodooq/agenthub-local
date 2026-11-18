"""
v1.5.5 代码审计与架构优化整改回归测试集
涵盖：
1. 多模态与浏览器工具协议注册及 include_archived 参数契约；
2. 知识库领域统计接口 (get_knowledge_stats) 与分层规范；
3. 云盘 VFS 提供商工厂 (create_cloud_provider) 统一实例化；
4. 图谱起点实体智能模糊锚定 (find_anchor_entities)；
5. 治理自审真实证据评估与综合置信度闭环。
"""
import pytest
from backend.app.agent.tool_protocol import ToolProtocolAdapter
from backend.app.search.hybrid_engine import HybridSearchEngine
from backend.app.search.light_graph_rag import LightGraphRAGEngine
from backend.app.api.v1.knowledge import create_cloud_provider
from backend.app.governance.self_critique import SelfCritiqueCritic
from backend.app.governance.confidence_evaluator import ConfidenceEvaluator


def test_tool_protocol_multimodal_tools_registered():
    """验证多模态与浏览器工具已完整注册至原生工具协议池。"""
    tools = ToolProtocolAdapter.get_builtin_tools()
    tool_map = {t["name"]: t for t in tools}

    assert "workspace_hybrid_search" in tool_map
    hybrid_props = tool_map["workspace_hybrid_search"]["parameters"]["properties"]
    assert "include_archived" in hybrid_props, "workspace_hybrid_search 必须支持 include_archived"

    assert "transcribe_audio_file" in tool_map, "缺少 transcribe_audio_file 工具协议"
    assert "browser_inspect_page" in tool_map, "缺少 browser_inspect_page 工具协议"
    assert "browser_interact_action" in tool_map, "缺少 browser_interact_action 工具协议"

    # 验证 browser_interact_action 枚举值
    act_enum = tool_map["browser_interact_action"]["parameters"]["properties"]["action"]["enum"]
    assert set(act_enum) == {"click", "type", "scroll", "navigate"}


def test_hybrid_search_stats_and_api_contract(tmp_path):
    """验证 HybridSearchEngine.get_knowledge_stats 领域方法正常返回。"""
    engine = HybridSearchEngine(str(tmp_path))
    stats = engine.get_knowledge_stats()

    assert isinstance(stats, dict)
    assert "total_chunks" in stats
    assert "total_triples" in stats
    assert "synced_cloud_mounts" in stats
    assert stats["total_chunks"] >= 0


def test_cloud_vfs_factory_method():
    """验证 create_cloud_provider 工厂方法能正确实例化或优雅拒绝。"""
    wecom = create_cloud_provider(
        mount_id="m1",
        mount_name="企业微盘",
        provider_type="wecom",
        config={"corpid": "corp1", "corpsecret": "sec1", "space_id": "sp1"},
    )
    assert wecom is not None
    assert wecom.mount_name == "企业微盘"

    webdav = create_cloud_provider(
        mount_id="m2",
        mount_name="私有网盘",
        provider_type="webdav",
        config={"base_url": "https://dav.example.com", "username": "admin", "password": "pwd"},
    )
    assert webdav is not None

    oss = create_cloud_provider(
        mount_id="m3",
        mount_name="阿里云OSS",
        provider_type="oss",
        config={"endpoint": "oss.aliyun.com", "bucket": "b1", "access_key": "k", "secret_key": "s"},
    )
    assert oss is not None

    invalid = create_cloud_provider(
        mount_id="m4",
        mount_name="未知类型",
        provider_type="unknown_proto",
        config={},
    )
    assert invalid is None


def test_graph_anchor_entities_fuzzy_resolution(tmp_path):
    """验证图谱实体模糊锚定支持长句及词素命中。"""
    engine = LightGraphRAGEngine(str(tmp_path))
    triples = [
        ("张经理", "负责", "跨境电商项目", "doc1", 1),
        ("跨境电商项目", "预算上限", "500万元", "doc1", 1),
        ("李总监", "审批", "跨境电商项目", "doc2", 1),
    ]
    engine.upsert_triples(triples, pod_id="global")

    # 精确匹配
    anchors = engine.find_anchor_entities("张经理", pod_id="global")
    assert "张经理" in anchors

    # 自然语言长句拆解模糊命中
    anchors_sentence = engine.find_anchor_entities("关于跨境电商项目的审批人是谁", pod_id="global")
    assert "跨境电商项目" in anchors_sentence

    # 基于长句查询子图
    edges = engine.query_subgraph("请问跨境电商项目的相关关系", max_hops=2, pod_id="global")
    assert len(edges) >= 2
    subjects = {e["subject"] for e in edges} | {e["object"] for e in edges}
    assert "跨境电商项目" in subjects


def test_governance_with_real_retrieved_evidence():
    """验证向治理机制传入真实检索证据与工具记录时的准确评分与通过率。"""
    evidence = [
        {
            "source_uri": "wecom://rule_2026.docx",
            "content_text": "差旅住宿标准为每日不超过 800 元，需提前在系统发起报备。",
            "authority_tier": 1,
            "score": 0.95,
        }
    ]
    tools_rec = [
        {"name": "search_cloud_wiki", "status": "success", "is_empty": False}
    ]

    # 正确引用依据的回答
    draft_ok = "根据最新差旅管理规程，每日住宿标准上限为 800 元，出行前需在系统发起提前报备。"
    critique_ok = SelfCritiqueCritic.evaluate_draft(
        user_query="差旅住宿标准是多少？",
        retrieved_evidence=evidence,
        draft_response=draft_ok,
    )
    assert critique_ok.is_passed

    rep_ok = ConfidenceEvaluator.evaluate(
        retrieved_chunks=evidence,
        critique_res=critique_ok,
        tools_executed=tools_rec,
        strict_mode=False,
    )
    assert rep_ok.score >= 0.85
    assert not rep_ok.requires_confirmation

    # 包含冲突数字且未引用的臆造回答
    draft_bad = "住宿标准随便报销，没有任何限制，上限是 99999 元。"
    critique_bad = SelfCritiqueCritic.evaluate_draft(
        user_query="差旅住宿标准是多少？",
        retrieved_evidence=evidence,
        draft_response=draft_bad,
    )
    assert not critique_bad.is_passed
    assert len(critique_bad.critique_points) > 0
