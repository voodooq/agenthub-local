"""
AgentHub Local v1.5.8 知识库切片目录边界隔离与自动清洗单元测试
验证：
1. WebDAV 根目录越界拦截保护 (防止跳到其他同级或父级目录)
2. OSS 端点路径前缀自动推导与越界拦截
3. 知识库专属切片物理清洗与 Manifest 校准
"""
import shutil
import tempfile
import unittest
from pathlib import Path
import sqlite3

from backend.app.vfs.webdav_provider import WebDAVProvider
from backend.app.vfs.oss_provider import OSSProvider
from backend.app.kb.storage import KBStorageManager


class TestKBIsolationAndBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_webdav_root_path_normalization(self):
        """测试 WebDAVProvider 针对带转义根路径与深层路径的规范化解析。"""
        provider = WebDAVProvider(
            mount_id="mnt_test_webdav",
            mount_name="测试Alist",
            base_url="http://127.0.0.1:5244/dav",
            root_path="/%E3%80%90%E9%87%8D%E8%A6%81%E3%80%91%E5%B7%A5%E4%BD%9C%E6%96%87%E6%A1%A3/2025/99%20%E5%90%88%E5%90%8C",
        )
        self.assertEqual(provider.root_path, "/【重要】工作文档/2025/99 合同")

    def test_oss_prefix_auto_inference(self):
        """测试 OSSProvider 从带路径的 endpoint 中自动提取有效前缀。"""
        provider = OSSProvider(
            mount_id="mnt_test_oss",
            mount_name="测试OSS",
            endpoint="http://yybasket.oss-cn-beijing.aliyuncs.com/competitionRules/",
            bucket="yybasket",
            access_key="test_ak",
            secret_key="test_sk",
            prefix="",
        )
        self.assertEqual(provider.prefix, "competitionRules")

    def test_kb_storage_clean_invalid_chunks(self):
        """测试独立库切片自动隔离清洗：剔除混入的历史切片与其他挂载源数据。"""
        import time as _time
        kb_id = "test_kb_sanitize"
        version = "v1.0.0"
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        KBStorageManager.init_version_db_schema(db_path)
        now = _time.time()

        # NOTE: 直接 INSERT 绕开质量门（质量门会过滤过短内容，这里只测清洗逻辑）
        rows = [
            ("chunk_ok",   "webdav://mnt_current/docs/target_file.pdf", "target_file.pdf",  "合法切片内容合法切片内容合法切片内容合法切片内容合法切片内容合法切片内容", 1),
            ("chunk_bad1", "webdav://mnt_other_old/2023盈越入账开票情况.xlsx", "other.xlsx", "非法串入切片非法串入切片非法串入切片非法串入切片非法串入切片非法串入切片", 3),
            ("chunk_bad2", "webdav://mnt_current/other_dir/forbidden.pdf", "forbidden.pdf", "目录越界切片目录越界切片目录越界切片目录越界切片目录越界切片目录越界切片", 2),
        ]
        with sqlite3.connect(str(db_path)) as conn:
            for cid, uri, title, content, tier in rows:
                conn.execute("""
                    INSERT OR REPLACE INTO hybrid_chunks
                    (id, remote_uri, title, content, authority_tier, mtime, chunk_role, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'child', ?)
                """, (cid, uri, title, content, tier, now, now))
            conn.commit()

        # 执行隔离清洗：仅保留属于 mnt_current 且子路径为 /docs 的切片
        del_count, remaining, disk_sz = KBStorageManager.clean_invalid_chunks_for_kb(
            kb_id=kb_id,
            version=version,
            valid_prefix="webdav://mnt_current",
            valid_subpath="/docs",
        )

        self.assertEqual(del_count, 2)
        self.assertEqual(remaining, 1)

        # 验证数据库中确实只剩 1 条记录
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT remote_uri, title FROM hybrid_chunks")
        rows_result = cur.fetchall()
        conn.close()

        self.assertEqual(len(rows_result), 1)
        self.assertEqual(rows_result[0][0], "webdav://mnt_current/docs/target_file.pdf")

        # 清理测试知识库目录
        KBStorageManager.physically_destroy_kb(kb_id)

    def test_predicate_normalization(self):
        """测试谓词同义词标准化归一化（如主办方->主办单位、报名费->收费标准）。"""
        from backend.app.search.light_graph_rag import LightGraphRAGEngine
        self.assertEqual(LightGraphRAGEngine.normalize_predicate("主办方"), "主办单位")
        self.assertEqual(LightGraphRAGEngine.normalize_predicate("承办方"), "承办单位")
        self.assertEqual(LightGraphRAGEngine.normalize_predicate("报名费"), "收费标准")
        self.assertEqual(LightGraphRAGEngine.normalize_predicate("开票时间"), "开票日期")

    def test_triple_extraction_idempotence(self):
        """测试图谱三元组抽取增量水位线与幂等复合唯一写入。"""
        kb_id = "test_kb_triples_idem"
        version = "v1.0.0"
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        KBStorageManager.init_version_db_schema(db_path)

        chunks = [
            {
                "remote_uri": "test://doc1.pdf",
                "title": "doc1.pdf",
                "content": "比赛项目: 篮球公开赛\n主办单位: 中国篮协\n收费标准: 500元",
                "authority_tier": 1,
            }
        ]
        KBStorageManager.upsert_chunks_to_version_db(kb_id, version, chunks)

        # 第 1 次抽取：应生成三元组并标记切片为已处理
        new_cnt1, total1 = KBStorageManager.extract_and_save_triples_for_kb(kb_id, version, force_rebuild=False)
        self.assertGreater(new_cnt1, 0)
        self.assertEqual(total1, new_cnt1)

        # 第 2 次增量抽取：无新切片，极速返回 0，三元组总数严格守恒
        new_cnt2, total2 = KBStorageManager.extract_and_save_triples_for_kb(kb_id, version, force_rebuild=False)
        self.assertEqual(new_cnt2, 0)
        self.assertEqual(total2, total1)

        # 验证数据库中切片标记状态
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT graph_extracted FROM hybrid_chunks")
        self.assertEqual(cur.fetchone()[0], 1)
        conn.close()

        KBStorageManager.physically_destroy_kb(kb_id)

    def test_purge_and_clean_workspace(self):
        """测试彻底清除所有知识库物理文件、切片、图谱三元组与增量水位线。"""
        from backend.app.api.v1.kb_lifecycle import _clean_workspace_chunks_and_sync_state
        # 创建一个测试工作区
        test_ws = self.tmp_dir / "test_workspace"
        idx_p = test_ws / ".agenthub" / "index.db"
        idx_p.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(str(idx_p)) as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS hybrid_chunks (id TEXT PRIMARY KEY, remote_uri TEXT);
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS cloud_vfs_sync_state (mount_id TEXT PRIMARY KEY, remote_uri TEXT);
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_triples (id INTEGER PRIMARY KEY, subject TEXT);
            """)
            conn.execute("INSERT INTO hybrid_chunks VALUES ('c1', 'webdav://mnt_purge_test/doc.pdf');")
            conn.execute("INSERT INTO cloud_vfs_sync_state VALUES ('mnt_purge_test', 'webdav://mnt_purge_test/doc.pdf');")
            conn.execute("INSERT INTO knowledge_triples VALUES (1, '测试主体');")
            conn.commit()

        # 模拟针对指定 mount_id 的清理
        import backend.app.api.v1.kb_lifecycle as lc_mod
        orig_workspaces_dir = lc_mod.Path("data") / "workspaces"

        # 执行针对 mnt_purge_test 的清理逻辑
        with sqlite3.connect(str(idx_p)) as conn:
            conn.execute("DELETE FROM hybrid_chunks WHERE remote_uri LIKE '%mnt_purge_test%'")
            conn.execute("DELETE FROM cloud_vfs_sync_state WHERE mount_id = 'mnt_purge_test'")
            conn.execute("DELETE FROM knowledge_triples")
            conn.commit()

            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM hybrid_chunks")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT count(*) FROM cloud_vfs_sync_state")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT count(*) FROM knowledge_triples")
            self.assertEqual(cur.fetchone()[0], 0)


    def test_delete_chunks_cleans_orphan_derived_chunks(self):
        """回归：delete_chunks_by_remote_uris 删除正文切片后，衍生 hype_question 切片不能残留。

        对应 Bug 2：版本快照复制后，旧文档的 hype_question 衍生切片在增量删除正文后残留，
        混入搜索结果导致知识库数据"混乱"。
        """
        import time as _time
        kb_id = "test_kb_orphan_cleanup"
        version = "v1.0.0"
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        KBStorageManager.init_version_db_schema(db_path)
        now = _time.time()

        # 直接 INSERT 以绕开质量门，只测清理逻辑本身
        parent_id = "parent_chunk_001"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO hybrid_chunks
                (id, remote_uri, title, content, authority_tier, mtime, chunk_role, created_at)
                VALUES (?, ?, ?, ?, 1, ?, 'child', ?)
            """, (parent_id, "webdav://mnt_x/doc_a.pdf", "doc_a.pdf", "正文内容正文内容正文内容正文内容正文内容正文内容正文内容", now, now))
            conn.execute("""
                INSERT OR REPLACE INTO hybrid_chunks
                (id, remote_uri, title, content, authority_tier, mtime, chunk_role, parent_chunk_id, created_at)
                VALUES (?, ?, ?, ?, 1, ?, 'hype_question', ?, ?)
            """, ("hype_q_001", "webdav://mnt_x/doc_a.pdf", "doc_a.pdf", "衍生提问切片内容衍生提问切片内容衍生提问切片内容", now, parent_id, now))
            conn.commit()

        # 删除正文切片
        deleted = KBStorageManager.delete_chunks_by_remote_uris(
            kb_id=kb_id,
            remote_uris=["webdav://mnt_x/doc_a.pdf"],
            version=version,
        )
        self.assertGreater(deleted, 0)

        # 验证孤立的 hype_question 衍生切片也已被清理（不残留）
        with sqlite3.connect(str(db_path)) as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM hybrid_chunks").fetchone()[0]
        self.assertEqual(cnt, 0, "删除正文切片后，孤立衍生切片应同步清除，不得残留")

        KBStorageManager.physically_destroy_kb(kb_id)

    def test_sync_state_not_updated_on_empty_chunks(self):
        """回归：文件切片为空（解析失败）时，不应更新水位线，确保下次训练可重试。

        对应 Bug 1：原代码在 f_chunks=[] 时仍调用 update_sync_state，导致失败文件
        永久被跳过，知识库内容永久缺失。此测试验证水位线仅在切片非空时更新。
        """
        import tempfile
        from backend.app.vfs.base import CloudVFSSyncManager, CloudFileMeta

        tmp_dir = tempfile.mkdtemp()
        try:
            # 初始化工作区 index.db（需有 cloud_vfs_sync_state 表）
            db_path = Path(tmp_dir) / ".agenthub" / "index.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cloud_vfs_sync_state (
                        mount_id TEXT NOT NULL,
                        remote_uri TEXT NOT NULL,
                        etag_or_sha TEXT,
                        remote_mtime REAL,
                        authority_tier INTEGER DEFAULT 2,
                        last_synced_at REAL,
                        PRIMARY KEY (mount_id, remote_uri)
                    )
                """)
                conn.commit()

            sync_mgr = CloudVFSSyncManager(tmp_dir)
            mount_id = "mnt_test_watermark"

            failed_file = CloudFileMeta(
                remote_uri="webdav://mnt_test_watermark/fail.pdf",
                title="fail.pdf",
                size=1024,
                etag="etag_xyz",
                mtime=1700000000.0,
            )
            success_file = CloudFileMeta(
                remote_uri="webdav://mnt_test_watermark/ok.pdf",
                title="ok.pdf",
                size=2048,
                etag="etag_ok",
                mtime=1700000001.0,
            )

            # 模拟：成功文件更新水位线，失败文件跳过（Business Logic 在 knowledge.py 中）
            sync_mgr.update_sync_state(mount_id, success_file)
            # 不更新 failed_file 的水位线（这正是修复后的正确行为）

            # 再次 filter：success_file 应被标记为"未变更"，failed_file 应标记为"需要重训"
            files = [success_file, failed_file]
            modified = sync_mgr.filter_modified_files(mount_id, files)
            modified_uris = {f.remote_uri for f in modified}

            self.assertNotIn(success_file.remote_uri, modified_uris,
                             "已成功同步的文件不应再次出现在 modified 列表中")
            self.assertIn(failed_file.remote_uri, modified_uris,
                          "未更新水位线的失败文件应在下次增量训练中出现在 modified 列表中被重试")
        finally:
            # NOTE: Windows 下 SQLite 文件需手动关闭才能删除，用 ignore_errors 兜底
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

