"""
额度水位拦截监控器 (Quota Monitor)
实时拦截统计每个账号在滑动时间窗口内的 RPM、TPM 与响应延迟
"""
import time
from collections import deque
from ..core.logging_config import logger


class AccountQuotaTracker:
    """单个账号的内存滑动窗口监控器"""
    def __init__(self, rpm_limit: int = 15, tpm_limit: int = 1000000):
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self.request_timestamps: deque[float] = deque()
        self.token_timestamps: deque[tuple[float, int]] = deque()
        self.last_latency_ms: float = 0.0

    def record_request_start(self) -> None:
        """记录一次请求发起"""
        now = time.time()
        self.request_timestamps.append(now)
        self._prune(now)

    def record_request_end(self, latency_ms: float, tokens: int = 0) -> None:
        """记录请求完成指标"""
        now = time.time()
        self.last_latency_ms = latency_ms
        if tokens > 0:
            self.token_timestamps.append((now, tokens))
        self._prune(now)

    def _prune(self, now: float) -> None:
        """清理 60 秒以外的过期窗口数据"""
        cutoff = now - 60.0
        while self.request_timestamps and self.request_timestamps[0] < cutoff:
            self.request_timestamps.popleft()
        while self.token_timestamps and self.token_timestamps[0][0] < cutoff:
            self.token_timestamps.popleft()

    @property
    def current_rpm(self) -> int:
        self._prune(time.time())
        return len(self.request_timestamps)

    @property
    def is_warning(self) -> bool:
        """达到 90% 软阈值发出告警"""
        return self.current_rpm >= int(self.rpm_limit * 0.9)

    @property
    def is_exhausted(self) -> bool:
        """达到 100% 阈值需预防性切换"""
        return self.current_rpm >= self.rpm_limit


class QuotaMonitorService:
    """全局多账号配额监控管理器"""
    def __init__(self):
        self._trackers: dict[str, AccountQuotaTracker] = {}

    def get_tracker(self, account_name: str, rpm_limit: int = 15) -> AccountQuotaTracker:
        if account_name not in self._trackers:
            self._trackers[account_name] = AccountQuotaTracker(rpm_limit=rpm_limit)
        return self._trackers[account_name]

    def remove_tracker(self, account_name: str) -> None:
        """从内存中注销并销毁指定账号的水位追踪器"""
        self._trackers.pop(account_name, None)

    def clear_all(self) -> None:
        """清空所有配额监控缓存"""
        self._trackers.clear()


quota_monitor = QuotaMonitorService()
