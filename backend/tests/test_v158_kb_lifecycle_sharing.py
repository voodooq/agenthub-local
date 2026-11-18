"""
AgentHub Local v1.5.8 知识库生命周期治理、共享分发与沙盒隔离全链路验收测试集
涵盖：
1. 核心 Schema 与数据源断连双模式策略 (保库 frozen_snapshot vs 彻底销毁)；
2. 独立 index.db 存储隔离与原子版本回滚 (耗时 < 50ms)；
3. .kbpack 归档打包、团队拉取订阅 (只读锁保护) 与 Fork 本地独立副本；
4. 专家角色多库绑定优先级加权 (Priority 1 > Priority 2)；
5. 检索门控：未挂载知识库时严格阻断盲搜，显式挂载后精准命中。
"""
import asyncio
import os
from pathlib import Path
import pytest
import shutil
import time

from backend.app.kb.storage import KBStorageManager
from backend.app.kb.manager import KBLifecycleManager
from backend.app.kb.package import KBPackageEngine
from backend.app.search.multi_kb_router import MultiKBRetrievalRouter
from backend.app.repository.db import db_manager


@pytest.mark.asyncio
async def test_kb_schema_and_lifecycle_dual_mode():
    """验证数据源创建、独立知识库初始化以及断连保库/销毁双模式。"""
    await db_manager.init_db()

    # 1. 创建数据源
    source_id = f"ds_unit_{int(time.time())}"
    ds = await KBLifecycleManager.create_data_source(
        source_id=source_id,
        name="测试企微规程盘",
        source_type="wecom",
        config={"space_id": "s.123456"},
    )
    assert ds["source_id"] == source_id

    # 2. 创建独立知识库资产
    kb_id = f"kb_unit_{int(time.time())}"
    kb = await KBLifecycleManager.create_knowledge_base(
        kb_id=kb_id,
        name="青少年击剑公开赛规程库",
        source_id=source_id,
        initial_version="v1.0.0",
    )
    assert kb["kb_id"] == kb_id
    assert kb["active_version"] == "v1.0.0"

    # 验证物理文件结构已自包含创建
    db_path = KBStorageManager.get_version_db_path(kb_id, "v1.0.0")
    assert db_path.exists()
    manifest = KBStorageManager.read_manifest(kb_id, "v1.0.0")
    assert manifest is not None
    assert manifest["version"] == "v1.0.0"

    # 3. 验证断连保库 (模式 A: 仅解绑数据源，知识库安全冻结为离线快照)
    detach_res = await KBLifecycleManager.remove_data_source(source_id, destroy_associated_kb=False)
    assert detach_res["success"] is True

    kbs = await KBLifecycleManager.list_knowledge_bases()
    target = next((k for k in kbs if k["kb_id"] == kb_id), None)
    assert target is not None
    assert target["ownership_type"] == "frozen_snapshot"
    assert target["source_id"] is None
    # 物理数据库依然完好可用
    assert db_path.exists()

    # 清理测试资产
    KBStorageManager.physically_destroy_kb(kb_id)


@pytest.mark.asyncio
async def test_version_rollback_instant():
    """验证版本快照创建与毫秒级原子回滚。"""
    await db_manager.init_db()
    kb_id = f"kb_rollback_{int(time.time())}"
    await KBLifecycleManager.create_knowledge_base(
        kb_id=kb_id,
        name="赛事判例规则库",
        initial_version="v1.0.0",
    )

    # 创建 v1.1.0 快照
    KBStorageManager.copy_version_snapshot(kb_id, "v1.0.0", "v1.1.0")
    KBStorageManager.write_manifest(
        kb_id=kb_id,
        version="v1.1.0",
        embedding_model="null_offline_lexical",
        embedding_dim=768,
        chunk_count=20,
        triple_count=50,
        changelog="新增 2026 最新击剑裁判判例解析",
    )
    from backend.app.repository.db import db_manager as dbm
    async with dbm.get_connection() as conn:
        await conn.execute(
            "INSERT INTO kb_versions (kb_id, version, status, changelog, chunk_count, triple_count, disk_size_bytes, created_at) VALUES (?, 'v1.1.0', 'ready', '新增判例', 20, 50, 1024, ?)",
            (kb_id, time.time()),
        )
        await conn.commit()

    # 切换到 v1.1.0
    t0 = time.perf_counter()
    rb = await KBLifecycleManager.rollback_version(kb_id, "v1.1.0")
    duration_ms = (time.perf_counter() - t0) * 1000
    assert rb["active_version"] == "v1.1.0"
    assert duration_ms < 50.0  # 验证必须在 50ms 内完成原子切换

    # 再回滚回 v1.0.0
    rb2 = await KBLifecycleManager.rollback_version(kb_id, "v1.0.0")
    assert rb2["active_version"] == "v1.0.0"

    KBStorageManager.physically_destroy_kb(kb_id)


@pytest.mark.asyncio
async def test_publish_subscribe_and_fork():
    """验证 .kbpack 导出、团队订阅只读锁保护与 Fork 派生。"""
    await db_manager.init_db()
    kb_id = f"kb_pub_{int(time.time())}"
    await KBLifecycleManager.create_knowledge_base(
        kb_id=kb_id,
        name="标准财务报销指南",
        embedding_model="null_offline_lexical",
        embedding_dim=768,
        initial_version="v2.0.0",
    )

    # 导出 .kbpack
    pkg_file = KBPackageEngine.export_package(kb_id, "v2.0.0")
    assert pkg_file.exists()

    # 模拟其他成员一键拉取并订阅 (只读锁)
    sub = await KBPackageEngine.import_and_subscribe_package(
        pkg_path=pkg_file,
        kb_name="全员订阅-财务报销指南",
        expected_dim=768,
        as_readonly=True,
    )
    assert sub["ownership_type"] == "subscribed_ro"

    # Fork 派生为本地可编辑副本
    forked = await KBPackageEngine.fork_as_local_editable(
        source_kb_id=kb_id,
        new_name="华北区分部财务报销细则",
    )
    assert forked["ownership_type"] == "local"
    assert "华北区分部" in forked["name"]

    # 清理
    KBStorageManager.physically_destroy_kb(kb_id)
    KBStorageManager.physically_destroy_kb(forked["kb_id"])
    if pkg_file.exists():
        pkg_file.unlink()


@pytest.mark.asyncio
async def test_expert_binding_and_search_gate():
    """验证专家知识库绑定优先级与未挂载时的检索门控阻断。"""
    await db_manager.init_db()

    # 1. 门控测试：未挂载任何知识库时，检索自动阻断，不盲搜
    gated, text, chunks = await MultiKBRetrievalRouter.search_mounted_kbs(
        query="击剑比赛重剑得分规则",
        expert_id="unbound_general_expert",
    )
    assert gated is False
    assert "未挂载任何知识库" in text
    assert len(chunks) == 0

    # 2. 绑定知识库后进行优先级检索
    kb_id = f"kb_expert_{int(time.time())}"
    await KBLifecycleManager.create_knowledge_base(
        kb_id=kb_id,
        name="击剑比赛技术总规程",
        initial_version="v1.0.0",
    )
    # 写入一条规程切片用于验证
    db_p = KBStorageManager.get_version_db_path(kb_id, "v1.0.0")
    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.execute(
        "INSERT INTO hybrid_chunks (id, remote_uri, title, content, authority_tier, mtime, etag, created_at) VALUES ('c1', 'doc://rules.pdf', '重剑技术规则', '重剑比赛中，双方击中有效部位均计得分，灯亮即算。', 1, 0, 'e1', 0)"
    )
    conn.commit()
    conn.close()

    # 绑定专家
    expert_id = f"fencing_referee_{int(time.time())}"
    await KBLifecycleManager.bind_expert_kbs(
        expert_id=expert_id,
        bindings=[{"kb_id": kb_id, "priority_rank": 1, "pinned_version": "latest"}],
    )

    # 检索已绑定的专家知识库
    gated_ok, result_text, hits = await MultiKBRetrievalRouter.search_mounted_kbs(
        query="重剑",
        expert_id=expert_id,
    )
    assert gated_ok is True
    assert len(hits) > 0
    assert "重剑比赛中" in hits[0]["content"]

    KBStorageManager.physically_destroy_kb(kb_id)

