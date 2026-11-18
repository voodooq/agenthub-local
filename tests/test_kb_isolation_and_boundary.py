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
        kb_id = "test_kb_sanitize"
        version = "v1.0.0"
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        KBStorageManager.init_version_db_schema(db_path)

        # 写入一条合法切片和两条外来串入切片
        chunks = [
            {
                "remote_uri": "webdav://mnt_current/docs/target_file.pdf",
                "title": "target_file.pdf",
                "content": "合法切片内容",
                "authority_tier": 1,
            },
            {
                "remote_uri": "webdav://mnt_other_old/2023盈越入账开票情况.xlsx",
                "title": "2023盈越入账开票情况.xlsx",
                "content": "非法串入切片1",
                "authority_tier": 3,
            },
            {
                "remote_uri": "webdav://mnt_current/other_dir/forbidden.pdf",
                "title": "forbidden.pdf",
                "content": "目录越界切片2",
                "authority_tier": 2,
            },
        ]
        KBStorageManager.upsert_chunks_to_version_db(kb_id, version, chunks)

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
        rows = cur.fetchall()
        conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "webdav://mnt_current/docs/target_file.pdf")

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


if __name__ == "__main__":
    unittest.main()

