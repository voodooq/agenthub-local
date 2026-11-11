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
        获取数据库连接上下文管理器，自动配置 busy_timeout 与外键，并保证用毕安全关闭
        """
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # 设置连接超时为 60.0 秒，并在连接级别开启 busy_timeout=60000ms，彻底消除 database locked
        conn = await aiosqlite.connect(self.db_path, timeout=60.0)
        conn.row_factory = aiosqlite.Row

        await conn.execute("PRAGMA busy_timeout = 60000;")
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
            "last_extracted_turn_id": "INTEGER DEFAULT 0",
            "tenant_id": "TEXT DEFAULT 'default'",
            "project_id": "TEXT DEFAULT 'default'",
            "workspace_id": "TEXT DEFAULT 'default'",
            "collapse_anchor_turn_id": "INTEGER DEFAULT 0",
            "anchored_summary": "TEXT DEFAULT ''",
            "collapsed_tokens_saved": "INTEGER DEFAULT 0",
        }
        for col, col_type in new_columns.items():
            if col not in columns:
                await conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} {col_type};")
                logger.info(f"成功为 conversations 表迁移并扩展字段: {col} ({col_type})")

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_workspace ON conversations(workspace_id);"
        )

    async def _migrate_workspaces_table(self, conn: aiosqlite.Connection) -> None:
        """
        动态平滑补全 workspaces 表结构
        """
        async with conn.execute("PRAGMA table_info(workspaces);") as cursor:
            columns = [row["name"] for row in await cursor.fetchall()]

        new_columns = {
            "context_prompt": "TEXT",
            "model_preference": "TEXT",
            "security_level": "TEXT DEFAULT 'standard'",
            "fts_last_indexed_at": "DATETIME",
        }
        for col, col_type in new_columns.items():
            if col not in columns:
                await conn.execute(f"ALTER TABLE workspaces ADD COLUMN {col} {col_type};")
                logger.info(f"成功为 workspaces 表迁移并扩展字段: {col} ({col_type})")

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
            "hit_count": "INTEGER DEFAULT 0",
            "last_accessed_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
            "tenant_id": "TEXT DEFAULT 'default'",
            "project_id": "TEXT DEFAULT 'default'",
        }
        for col, col_type in new_columns.items():
            if col not in columns:
                await conn.execute(f"ALTER TABLE long_term_memories ADD COLUMN {col} {col_type};")
                logger.info(f"成功为 long_term_memories 表迁移并扩展字段: {col} ({col_type})")

    async def _migrate_task_loop_runs_table(self, conn: aiosqlite.Connection) -> None:
        """
        动态平滑补全 task_loop_runs 表字段
        """
        async with conn.execute("PRAGMA table_info(task_loop_runs);") as cursor:
            columns = [row["name"] for row in await cursor.fetchall()]

        new_columns = {
            "pending_approval_call": "TEXT",
            "error_fingerprints": "TEXT DEFAULT '[]'",
            "scratchpad_md": "TEXT",
        }
        for col, col_type in new_columns.items():
            if col not in columns:
                await conn.execute(f"ALTER TABLE task_loop_runs ADD COLUMN {col} {col_type};")
                logger.info(f"成功为 task_loop_runs 表迁移并扩展字段: {col} ({col_type})")

    async def _migrate_llm_providers_table(self, conn: aiosqlite.Connection) -> None:
        """
        动态平滑补全 llm_providers 表字段（支持 Google OAuth refresh_token_encrypted 与 429 cooldown_until）
        """
        async with conn.execute("PRAGMA table_info(llm_providers);") as cursor:
            columns = [row["name"] for row in await cursor.fetchall()]

        new_columns = {
            "refresh_token_encrypted": "TEXT",
            "cooldown_until": "DATETIME",
            "max_context_tokens": "INTEGER DEFAULT 0",
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
            # 全局持久化开启 WAL 日志模式
            await conn.execute("PRAGMA journal_mode = WAL;")
            # 0. 工作区表 (Workspaces)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('virtual', 'physical')),
                root_path TEXT NOT NULL UNIQUE,
                context_prompt TEXT,
                model_preference TEXT,
                security_level TEXT DEFAULT 'standard',
                fts_last_indexed_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await self._migrate_workspaces_table(conn)

            # 插入默认虚拟沙盒工作区（如果不存在）
            default_ws_path = str((Path(self.db_path).parent / "workspaces" / "default").resolve())
            Path(default_ws_path).mkdir(parents=True, exist_ok=True)
            await conn.execute("""
            INSERT OR IGNORE INTO workspaces (id, name, type, root_path, context_prompt, security_level)
            VALUES ('default', '默认工作区', 'virtual', ?, '默认沙盒工作区，所有未指定工作区的会话与任务均沉淀于此。', 'standard');
            """, (default_ws_path,))

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
                workspace_id TEXT DEFAULT 'default',
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

            # 清理历史废弃的系统占位池记录（彻底删除任何写死的占位项，所有通道均由用户自主控制）
            await conn.execute("DELETE FROM llm_providers WHERE id = 'provider_google_pool';")

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

            # 11. Skill 领域知识库分片表与 FTS5 索引表 (Skill-as-a-Folder 四元组 knowledge/ 引擎)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_knowledge_chunks (
                id TEXT PRIMARY KEY,
                skill_id TEXT NOT NULL,
                tenant_id TEXT DEFAULT 'default',
                project_id TEXT DEFAULT 'default',
                file_name TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                chunk_index INTEGER DEFAULT 0,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_skc_skill_hash ON skill_knowledge_chunks(skill_id, file_name, file_hash);"
            )

            await conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS skill_knowledge_fts USING fts5(
                content,
                skill_id UNINDEXED,
                file_name UNINDEXED,
                chunk_id UNINDEXED,
                tokenize='trigram'
            );
            """)

            await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_skc_ai AFTER INSERT ON skill_knowledge_chunks
            BEGIN
                INSERT INTO skill_knowledge_fts(rowid, content, skill_id, file_name, chunk_id)
                VALUES (new.rowid, new.content, new.skill_id, new.file_name, new.id);
            END;
            """)

            await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_skc_ad AFTER DELETE ON skill_knowledge_chunks
            BEGIN
                DELETE FROM skill_knowledge_fts WHERE rowid = old.rowid;
            END;
            """)

            await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_skc_au AFTER UPDATE ON skill_knowledge_chunks
            BEGIN
                UPDATE skill_knowledge_fts SET content = new.content WHERE rowid = old.rowid;
            END;
            """)

            # 12. 任务自主循环状态表 (task_loop_runs - 支持 Planner->Executor->Verifier->Reflector 状态机与中断审批)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS task_loop_runs (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                goal_prompt TEXT NOT NULL,
                scratchpad_md TEXT,
                loop_status TEXT NOT NULL CHECK(loop_status IN ('idle', 'planning', 'executing', 'verifying', 'reflecting', 'waiting_approval', 'completed', 'failed', 'paused')),
                current_iteration INTEGER DEFAULT 0,
                max_iterations INTEGER DEFAULT 10,
                pending_approval_call TEXT,
                error_fingerprints TEXT DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_task_loop_ws ON task_loop_runs(workspace_id);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_task_loop_conv ON task_loop_runs(conversation_id);")
            await self._migrate_task_loop_runs_table(conn)

            # 13. 工作区资产登记表 (workspace_artifacts - 代码落盘、报告生成与资产溯源)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS workspace_artifacts (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                conversation_id TEXT,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER DEFAULT 0,
                source TEXT DEFAULT 'ai_generated',
                summary TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_ws ON workspace_artifacts(workspace_id);")

            # 14. 无人值守已安排计划任务表 (scheduled_tasks - 支持 Cron/间隔调度与桌面通知)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                cron_expr TEXT NOT NULL,
                prompt_template TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'running')),
                last_run_at DATETIME,
                next_run_at DATETIME,
                last_status TEXT,
                last_output TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_ws ON scheduled_tasks(workspace_id);")

            # 15. 追加式原始事件账本表 (event_ledger - Append-Only 黑匣子事件轨迹)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS event_ledger (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL DEFAULT 'default',
                conversation_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL DEFAULT 1,
                loop_id TEXT,
                event_type TEXT NOT NULL CHECK(event_type IN (
                    'user_input',
                    'inquiry_confirmed',
                    'tool_executed',
                    'tool_error_recovered',
                    'user_intervention',
                    'artifact_produced'
                )),
                actor TEXT NOT NULL DEFAULT 'user',
                skill_id TEXT,
                payload_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ledger_conv_turn ON event_ledger(conversation_id, turn_id);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ledger_ws_type ON event_ledger(workspace_id, event_type, created_at);"
            )

            # 16. 双时间轴结构化事实表 (memory_facts - 工作区沙箱隔离 + Bi-temporal 版本演化)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_facts (
                fact_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL DEFAULT 'GLOBAL',
                category TEXT NOT NULL CHECK(category IN ('user_pref', 'biz_rule', 'env_config')),
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                source_type TEXT NOT NULL DEFAULT 'inquiry',
                source_event_id TEXT,
                valid_from DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                valid_to DATETIME DEFAULT NULL,
                recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_active_lookup ON memory_facts(workspace_id, fact_key, valid_to);"
            )
            # NOTE: 局部唯一索引从引擎层物理防重，同一工作区下同一 fact_key 绝不允许存在多条 valid_to IS NULL 的生效记录
            await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_facts_active_key
                ON memory_facts(workspace_id, fact_key)
                WHERE valid_to IS NULL;
            """)

            # 17. 增量会话摘要表 (session_summaries - 水位线推进压缩长程历史)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS session_summaries (
                conversation_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL DEFAULT 'default',
                watermark_turn_id INTEGER NOT NULL DEFAULT 0,
                core_goal TEXT DEFAULT '',
                confirmed_params_json TEXT DEFAULT '{}',
                produced_artifacts_json TEXT DEFAULT '[]',
                open_issues_json TEXT DEFAULT '[]',
                summary_markdown TEXT DEFAULT '',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            """)

            # 18. Skill 经验提炼与升级提议表 (experience_proposals - HITL Diff 确认防污染)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS experience_proposals (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL DEFAULT 'default',
                conversation_id TEXT NOT NULL,
                loop_id TEXT,
                proposal_type TEXT NOT NULL CHECK(proposal_type IN ('skill_patch', 'new_skill')),
                target_skill_id TEXT,
                proposed_skill_name TEXT,
                target_scope TEXT NOT NULL DEFAULT 'workspace' CHECK(target_scope IN ('global', 'workspace')),
                trigger_reason TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                experience_triple_json TEXT NOT NULL,
                diff_preview TEXT DEFAULT '',
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'applied', 'dismissed')),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at DATETIME
            );
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_exp_prop_status ON experience_proposals(status, workspace_id, created_at);"
            )

            # 19. 工具超长原始输出按需引用表 (tool_output_refs - 支持旁路脱水原文溯源与按行切片回查)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_output_refs (
                ref_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL DEFAULT 1,
                tool_name TEXT NOT NULL,
                tool_args_summary TEXT DEFAULT '',
                storage_mode TEXT NOT NULL DEFAULT 'inline_db',
                raw_content TEXT,
                disk_path TEXT,
                raw_chars INTEGER NOT NULL DEFAULT 0,
                raw_tokens_est INTEGER NOT NULL DEFAULT 0,
                scrubbed_summary TEXT NOT NULL DEFAULT '',
                scrubbed_tokens_est INTEGER NOT NULL DEFAULT 0,
                saved_tokens_est INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_refs_conv_turn ON tool_output_refs(conversation_id, turn_id);"
            )

            # 20. 推理与辅助清洗 Token/KV Cache/耗时明细表 (token_usage_logs)
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS token_usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL DEFAULT 1,
                step_index INTEGER NOT NULL DEFAULT 0,
                agent_role TEXT NOT NULL DEFAULT 'primary',
                provider_id TEXT DEFAULT '',
                provider_name TEXT DEFAULT '',
                provider_type TEXT DEFAULT '',
                model_name TEXT NOT NULL DEFAULT '',
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                cached_tokens INTEGER NOT NULL DEFAULT 0,
                eval_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                saved_tokens INTEGER NOT NULL DEFAULT 0,
                context_limit INTEGER NOT NULL DEFAULT 0,
                ttft_ms REAL DEFAULT 0.0,
                prefill_ms REAL DEFAULT 0.0,
                tps REAL DEFAULT 0.0,
                is_estimated INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_token_logs_conv_turn ON token_usage_logs(conversation_id, turn_id, step_index);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_token_logs_created ON token_usage_logs(created_at);"
            )

            # 21. 个人实战微经验库 (L5 Episodic Experience Cases) + FTS5 trigram 自同步索引
            await conn.executescript("""
            CREATE TABLE IF NOT EXISTS experience_cases (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL DEFAULT 'GLOBAL',
                task_category TEXT NOT NULL DEFAULT 'general',
                entity_tags TEXT NOT NULL DEFAULT '',
                error_signature TEXT NOT NULL DEFAULT '',
                symptom_keywords TEXT NOT NULL DEFAULT '',
                scene_summary TEXT NOT NULL DEFAULT '',
                root_cause TEXT NOT NULL DEFAULT '',
                verified_solution TEXT NOT NULL DEFAULT '',
                effective_commands TEXT NOT NULL DEFAULT '[]',
                origin_type TEXT NOT NULL DEFAULT 'auto_recover',
                confidence REAL NOT NULL DEFAULT 0.85,
                hit_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                scope TEXT NOT NULL DEFAULT 'private',
                embedding BLOB,
                source_conversation_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_exp_cases_ws ON experience_cases(workspace_id, confidence DESC);
            CREATE INDEX IF NOT EXISTS idx_exp_cases_err ON experience_cases(error_signature);

            CREATE VIRTUAL TABLE IF NOT EXISTS experience_cases_fts USING fts5(
                case_id UNINDEXED,
                entity_tags,
                error_signature,
                symptom_keywords,
                scene_summary,
                root_cause,
                verified_solution,
                tokenize='trigram'
            );

            CREATE TRIGGER IF NOT EXISTS trg_exp_cases_ai AFTER INSERT ON experience_cases BEGIN
                INSERT INTO experience_cases_fts (
                    case_id, entity_tags, error_signature, symptom_keywords,
                    scene_summary, root_cause, verified_solution
                ) VALUES (
                    new.id, new.entity_tags, new.error_signature, new.symptom_keywords,
                    new.scene_summary, new.root_cause, new.verified_solution
                );
            END;

            CREATE TRIGGER IF NOT EXISTS trg_exp_cases_ad AFTER DELETE ON experience_cases BEGIN
                DELETE FROM experience_cases_fts WHERE case_id = old.id;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_exp_cases_au AFTER UPDATE OF
                id, entity_tags, error_signature, symptom_keywords,
                scene_summary, root_cause, verified_solution
            ON experience_cases BEGIN
                DELETE FROM experience_cases_fts WHERE case_id = old.id;
                INSERT INTO experience_cases_fts (
                    case_id, entity_tags, error_signature, symptom_keywords,
                    scene_summary, root_cause, verified_solution
                ) VALUES (
                    new.id, new.entity_tags, new.error_signature, new.symptom_keywords,
                    new.scene_summary, new.root_cause, new.verified_solution
                );
            END;

            -- 22. 双回滚时光机检查点表 (Session Checkpoints)
            CREATE TABLE IF NOT EXISTS session_checkpoints (
                ckpt_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                git_hash TEXT NOT NULL,
                turn_id INTEGER NOT NULL DEFAULT 0,
                scratchpad_snapshot TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_checkpoints_conv
                ON session_checkpoints(conversation_id, turn_id DESC, created_at DESC);
            """)

            await conn.commit()
            logger.info(
                "SQLite 全景架构表 (conversations/messages/long_term_memories/memory_facts/event_ledger/session_summaries/experience_proposals/task_loop_runs/workspace_artifacts/scheduled_tasks/experience_cases/session_checkpoints) 初始化完成"
            )


db_manager = DatabaseManager(settings.db_path)
