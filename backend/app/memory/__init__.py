"""
纯本地 SQLite 三层记忆引擎 (Memory Engine)
- 短期记忆: 滑动 Turn 窗口 + Observation Masking 折叠
- 工作记忆: sessions.working_memory_json 状态机草稿纸
- 长期记忆: FTS5 BM25 检索 + 提示词级冲突合并 + 30天指数衰减
"""

