"""
SQLite 3 存储引擎持久化连接池
核心特性：开启 WAL 模式 + 外键级联 + FTS5 全文索引虚拟表与自同步触发器 + 动态平滑迁移
"""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator
import aiosqlite

from ..core.config import settings
from ..core.logging_config import logger


class DatabaseManager:
    """
    异步 SQLite 数据库管理器
    # NOTE: 遵循 Single Source of Truth 原则，单文件支持跨容器卷持久化挂载
    """
    def __init__(self, db_path: str):
        self.db_path = db_path

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """
        获取数据库连接上下文管理器，自动开启 WAL 模式与外键，并保证用毕安全关闭
        """
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row

        # NOTE: 开启 WAL 模式可提升多并发读写效率，杜绝 database locked
        await conn.execute("PRAGMA journal_mode = WAL;")
        await conn.execute("PRAGMA synchronous = NORMAL;")
        await conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        finally:
            await conn.close()

    async def _migrate_accounts_table(self, conn: aiosqlite.Connection) -> None:
        """
        动态平滑补全 accounts 表结构，兼容旧版本数据库
        """
        async with conn.execute("PRAGMA table_info(accounts);") as cursor:
            columns = [row["name"] for row in await cursor.fetchall()]

        new_columns = {
            "email": "TEXT",
            "avatar_url": "TEXT",
            "extra_meta": "TEXT",
            "cli_home_dir": "TEXT",
            "refresh_token": "TEXT",
            "expires_at": "INTEGER",
        }
        for col, col_type in new_columns.items():
            if col not in columns:
                await conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} {col_type};")
                logger.info(f"成功为 accounts 表迁移并扩展字段: {col} ({col_type})")

    async def _migrate_conversations_table(self, conn: aiosqlite.Connection) -> None:
        """
        动态平滑补全 conversations 表结构，支持三层记忆、微内核 Agent 场景、CWD 与跨平台导入批次
        """
        async with conn.execute("PRAGMA table_info(conversations);") as cursor:
            columns = [row["name"] for row in await cursor.fetchall()]

        new_columns = {
            "scene_mode": "TEXT DEFAULT 'agent'",
            "incognito": "INTEGER DEFAULT 0",
            "working_memory_json": "TEXT DEFAULT '{}'",
            "rolling_summary": "TEXT DEFAULT ''",
            "current_cwd": "TEXT",
            "import_source": "TEXT DEFAULT 'local'",
            "import_batch_id": "TEXT",
        }
        for col, col_type in new_columns.items():
            if col not in columns:
                await conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} {col_type};")
                logger.info(f"成功为 conversations 表迁移并扩展字段: {col} ({col_type})")

    async def _migrate_messages_table(self, conn: aiosqlite.Connection) -> None:
        """
        动态平滑补全 messages 表结构，支持双层循环轮次锚定 (turn_id)、历史命令折叠摘要与工具调用
        """
        async with conn.execute("PRAGMA table_info(messages);") as cursor:
            columns = [row["name"] for row in await cursor.fetchall()]

        new_columns = {
            "turn_id": "INTEGER DEFAULT 1",
            "compressed_content": "TEXT",
            "tool_calls_json": "TEXT",
            "tool_call_id": "TEXT",
        }
        for col, col_type in new_columns.items():
            if col not in columns:
                await conn.execute(f"ALTER TABLE messages ADD COLUMN {col} {col_type};")
                logger.info(f"成功为 messages 表迁移并扩展字段: {col} ({col_type})")

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conv_turn ON messages(conversation_id, turn_id);"
        )

    async def _migrate_long_term_memories_table(self, conn: aiosqlite.Connection) -> None:
        """
        动态平滑补全 long_term_memories 表字段（支持来源追踪、置顶豁免衰减与导入批次关联）
        """
        async with conn.execute("PRAGMA table_info(long_term_memories);") as cursor:
            columns = [row["name"] for row in await cursor.fetchall()]

        new_columns = {
            "source": "TEXT DEFAULT 'auto_extract'",
            "is_pinned": "INTEGER DEFAULT 0",
            "import_batch_id": "TEXT",
        }
        for col, col_type in new_columns.items():
            if col not in columns:
                await conn.execute(f"ALTER TABLE long_term_memories ADD COLUMN {col} {col_type};")
                logger.info(f"成功为 long_term_memories 表迁移并扩展字段: {col} ({col_type})")

    async def _migrate_llm_providers_table(self, conn: aiosqlite.Connection) -> None:
        """
        动态平滑补全 llm_providers 表字段（支持 Google OAuth refresh_token_encrypted 与 429 cooldown_until）
        """
        async with conn.execute("PRAGMA table_info(llm_providers);") as cursor:
            columns = [row["name"] for row in await cursor.fetchall()]

        new_columns = {
            "refresh_token_encrypted": "TEXT",
            "cooldown_until": "DATETIME",
        }
        for col, col_type in new_columns.items():
            if col not in columns:
                await conn.execute(f"ALTER TABLE llm_providers ADD COLUMN {col} {col_type};")
                logger.info(f"成功为 llm_providers 表迁移并扩展字段: {col} ({col_type})")

    async def init_db(self) -> None:
        """
        执行数据库建表与触发器初始化脚本
        """
        logger.info(f"正在初始化 SQLite 数据库: {self.db_path}")
        async with self.get_connection() as conn:
            # 1. 账号表
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                auth_type TEXT DEFAULT 'gemini_cli',
                credentials TEXT NOT NULL,
                priority INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                cooldown_until DATETIME,
                rpm_limit INTEGER DEFAULT 15,
                rpd_limit INTEGER DEFAULT 1500,
                today_used INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                email TEXT,
                avatar_url TEXT,
                extra_meta TEXT,
                cli_home_dir TEXT
            );
            """)
            await self._migrate_accounts_table(conn)

            # 2. 会话表
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_type TEXT DEFAULT 'local',
                source_account TEXT,
                model_name TEXT DEFAULT '4 Argon',
                is_pinned BOOLEAN DEFAULT 0,
                scene_mode TEXT DEFAULT 'agent',
                incognito INTEGER DEFAULT 0,
                working_memory_json TEXT DEFAULT '{}',
                rolling_summary TEXT DEFAULT '',
                current_cwd TEXT,
                import_source TEXT DEFAULT 'local',
                import_batch_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await self._migrate_conversations_table(conn)

            # 3. 消息明细表
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                turn_id INTEGER DEFAULT 1,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                compressed_content TEXT,
                tool_calls_json TEXT,
                tool_call_id TEXT,
                executed_by_account TEXT,
                token_count INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            """)
            await self._migrate_messages_table(conn)

            # 4. FTS5 全文索引虚拟表
            await conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                conversation_id UNINDEXED,
                tokenize='trigram'
            );
            """)

            # 5. 触发器：messages 表增删改自动同步至 messages_fts
            await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_messages_ai AFTER INSERT ON messages
            BEGIN
                INSERT INTO messages_fts(rowid, content, conversation_id)
                VALUES (new.id, new.content, new.conversation_id);
            END;
            """)

            await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_messages_ad AFTER DELETE ON messages
            BEGIN
                DELETE FROM messages_fts WHERE rowid = old.id;
            END;
            """)

            await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_messages_au AFTER UPDATE ON messages
            BEGIN
                UPDATE messages_fts SET content = new.content WHERE rowid = old.id;
            END;
            """)

            # 6. 审计日志表（T7）
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                account_name TEXT,
                detail TEXT,
                approved INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # 7. 长期记忆表 (跨会话用户画像、环境路径与安全偏好)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS long_term_memories (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL DEFAULT 0.85,
                source TEXT DEFAULT 'auto_extract',
                is_pinned INTEGER DEFAULT 0,
                import_batch_id TEXT,
                hit_count INTEGER DEFAULT 0,
                last_accessed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await self._migrate_long_term_memories_table(conn)

            # 8. 长期记忆 FTS5 虚拟表 (纯本地零依赖 BM25 检索)
            await conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS long_term_memories_fts USING fts5(
                content,
                category UNINDEXED,
                memory_id UNINDEXED,
                tokenize='trigram'
            );
            """)

            await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_ltm_ai AFTER INSERT ON long_term_memories
            BEGIN
                INSERT INTO long_term_memories_fts(rowid, content, category, memory_id)
                VALUES (new.rowid, new.content, new.category, new.id);
            END;
            """)

            await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_ltm_ad AFTER DELETE ON long_term_memories
            BEGIN
                DELETE FROM long_term_memories_fts WHERE rowid = old.rowid;
            END;
            """)

            await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_ltm_au AFTER UPDATE ON long_term_memories
            BEGIN
                UPDATE long_term_memories_fts SET content = new.content, category = new.category WHERE rowid = old.rowid;
            END;
            """)

            # 9. 多源大模型供应商表 (llm_providers)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider_type TEXT NOT NULL,
                base_url TEXT,
                api_key_encrypted TEXT,
                refresh_token_encrypted TEXT,
                models_json TEXT DEFAULT '[]',
                role_binding TEXT DEFAULT 'both',
                tool_call_mode TEXT DEFAULT 'auto',
                priority INTEGER DEFAULT 10,
                cooldown_until DATETIME,
                is_enabled INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await self._migrate_llm_providers_table(conn)

            await conn.execute("""
            INSERT OR IGNORE INTO llm_providers
            (id, name, provider_type, base_url, api_key_encrypted, models_json, role_binding, tool_call_mode, priority, is_enabled)
            VALUES (
                'provider_google_pool',
                'Google OAuth / CLI 多账号算力池',
                'google_pool',
                '',
                '',
                '["gemini-2.5-pro", "gemini-2.5-flash", "4 Argon", "claude-sonnet-4-5"]',
                'both',
                'xml_prompt',
                1,
                1
            );
            """)

            # 10. 导入批次记录表 (import_batches - 支持按批次一键回滚)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS import_batches (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                filename TEXT,
                imported_sessions_count INTEGER DEFAULT 0,
                imported_memories_count INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)

            await conn.commit()
            logger.info("SQLite 全景架构表 (conversations/messages/long_term_memories/llm_providers/import_batches) 初始化完成")


db_manager = DatabaseManager(settings.db_path)
