"""
纯本地 SQLite 四层记忆与治理引擎 (Memory Engine)
- L1 会话与环路元数据: working_memory_json + .agenthub/scratchpad.md
- L2 滑动窗口: 滑动 Turn 窗口 + Observation Masking 折叠
- L3 增量会话摘要: session_summaries 水位线摘要压缩
- L4 双模结构化档案: memory_facts 双时间轴精确视图 + long_term_memories 画像画像引擎
- 治理机制: EventLedgerService (追加账本) + FactManager (双时间轴视图) + MemoryPolicyGuard (准入与脱敏)
"""
from .fact_manager import FactManager, fact_manager
from .ledger import EventLedgerService, event_ledger
from .policy_guard import MemoryAdmissionError, MemoryPolicyGuard, MemoryWriteDecision
from .session_summary import SessionSummaryManager, session_summary_manager

__all__ = [
    "EventLedgerService",
    "FactManager",
    "MemoryAdmissionError",
    "MemoryPolicyGuard",
    "MemoryWriteDecision",
    "SessionSummaryManager",
    "event_ledger",
    "fact_manager",
    "session_summary_manager",
]

