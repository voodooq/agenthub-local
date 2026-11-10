"""
双模 Token、KV Cache 与推理耗时采集器 (TokenMetricsCollector)
核心能力：
1. 统一归一化采集 local llama-server (timings/usage)、OpenAI/DeepSeek/Ollama、Anthropic、Gemini (usageMetadata) 与 CLI 指标；
2. 实测首字延迟 (TTFT) 与生成速率 (TPS)；
3. 贯彻流式早退/用户中断保底记账规范：配合 try...finally 在异常或用户取消时以已发送估算 + 已接收片段写入 is_estimated=True 的保底记录，确保水表永不缺席。
"""
from dataclasses import dataclass, asdict
import time
from typing import Any

from ..agent.context_compressor import estimate_tokens, infer_context_limit
from ..repository.token_usage_repository import TokenUsageRepository


@dataclass
class StreamMetricsResult:
    """单次推理/清洗/摘要步骤的标准化指标快照"""
    turn_id: int
    step_index: int
    agent_role: str
    provider_id: str
    provider_name: str
    provider_type: str
    model_name: str
    prompt_tokens: int
    cached_tokens: int
    eval_tokens: int
    completion_tokens: int
    saved_tokens: int
    context_limit: int
    ttft_ms: float
    prefill_ms: float
    tps: float
    is_estimated: bool
    aborted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TokenMetricsCollector:
    """流式推理指标生命周期采集器"""

    def __init__(
        self,
        *,
        turn_id: int = 1,
        step_index: int = 0,
        agent_role: str = "primary",
        provider_info: dict[str, Any] | None = None,
        model_name: str = "",
        estimated_prompt_tokens: int = 0,
        saved_tokens: int = 0,
    ) -> None:
        self.turn_id = int(turn_id or 1)
        self.step_index = int(step_index or 0)
        self.agent_role = agent_role or "primary"
        self.provider_info = provider_info or {}
        self.provider_id = str(self.provider_info.get("id") or "")
        self.provider_name = str(self.provider_info.get("name") or self.provider_info.get("provider_type") or "")
        self.provider_type = str(self.provider_info.get("provider_type") or "")
        self.model_name = str(model_name or self.provider_info.get("model_name") or "")
        self.estimated_prompt_tokens = max(0, int(estimated_prompt_tokens or 0))
        self.saved_tokens = max(0, int(saved_tokens or 0))
        self.context_limit = infer_context_limit(self.provider_info, model_name=self.model_name)

        self._start_ts: float = time.perf_counter()
        self._first_byte_ts: float | None = None
        self._end_ts: float | None = None
        self._accumulated_chunks: list[str] = []

        self._raw_usage: dict[str, Any] = {}
        self._raw_timings: dict[str, Any] = {}
        self._finalized: StreamMetricsResult | None = None

    def mark_first_byte(self) -> None:
        """记录网络首字节/首事件到达时间戳（用于扣除上游缓冲前的真实首字延迟）"""
        if self._first_byte_ts is None:
            self._first_byte_ts = time.perf_counter()

    def mark_chunk(self, text: str) -> None:
        """记录流式文本块并自动锚定首个有效内容到达时间"""
        if text:
            if self._first_byte_ts is None:
                self._first_byte_ts = time.perf_counter()
            self._accumulated_chunks.append(text)

    def record_raw_usage(
        self,
        usage: dict[str, Any] | None = None,
        timings: dict[str, Any] | None = None,
    ) -> None:
        """增量合并上游返回的 usage 与 llama.cpp timings 字典"""
        if isinstance(usage, dict):
            for k, v in usage.items():
                if v is not None:
                    self._raw_usage[k] = v
        if isinstance(timings, dict):
            for k, v in timings.items():
                if v is not None:
                    self._raw_timings[k] = v

    def reset_stream_buffer(self) -> None:
        """流式中断重试（stream_reset）时重置已接收片段并重新计时"""
        self._start_ts = time.perf_counter()
        self._first_byte_ts = None
        self._end_ts = None
        self._accumulated_chunks.clear()
        self._raw_usage.clear()
        self._raw_timings.clear()

    @property
    def accumulated_text(self) -> str:
        return "".join(self._accumulated_chunks)

    def finalize(self, aborted: bool = False) -> StreamMetricsResult:
        """收尾计算结构化指标（幂等：多次调用返回同一快照）"""
        if self._finalized is not None:
            return self._finalized

        now = time.perf_counter()
        self._end_ts = now
        end_ts = self._end_ts

        # 计算实测 TTFT (ms)
        if self._first_byte_ts is not None:
            ttft_ms = max(0.0, (self._first_byte_ts - self._start_ts) * 1000.0)
        else:
            ttft_ms = max(0.0, (end_ts - self._start_ts) * 1000.0)

        u = self._raw_usage
        t = self._raw_timings

        prompt_tokens = 0
        cached_tokens = 0
        eval_tokens = 0
        completion_tokens = 0
        prefill_ms = 0.0
        tps = 0.0
        has_authentic_usage = False

        # 1. 优先解析 llama.cpp (llama-server) 原生 timings + usage
        if t and any(k in t for k in ("prompt_n", "cache_n", "predicted_n")):
            has_authentic_usage = True
            cache_n = int(t.get("cache_n") or 0)
            prompt_n = int(t.get("prompt_n") or 0)
            predicted_n = int(t.get("predicted_n") or 0)
            cached_tokens = max(0, cache_n)
            eval_tokens = max(0, prompt_n)
            prompt_tokens = int(u.get("prompt_tokens") or (prompt_n + cache_n))
            completion_tokens = int(u.get("completion_tokens") or predicted_n)
            prefill_ms = float(t.get("prompt_ms") or 0.0)
            tps = float(t.get("predicted_per_second") or 0.0)
            if t.get("prompt_ms") is not None and self._first_byte_ts is None:
                ttft_ms = max(ttft_ms, prefill_ms)

        # 2. Anthropic Messages API usage (input_tokens + cache_read_input_tokens + cache_creation_input_tokens)
        elif any(k in u for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
            has_authentic_usage = True
            inp = int(u.get("input_tokens") or 0)
            cache_read = int(u.get("cache_read_input_tokens") or 0)
            cache_create = int(u.get("cache_creation_input_tokens") or 0)
            out_tok = int(u.get("output_tokens") or 0)
            prompt_tokens = inp + cache_read + cache_create
            cached_tokens = cache_read
            eval_tokens = inp + cache_create
            completion_tokens = out_tok

        # 3. Google Gemini usageMetadata (promptTokenCount / candidatesTokenCount / cachedContentTokenCount)
        elif any(k in u for k in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")) and (
            "promptTokenCount" in u or "candidatesTokenCount" in u
        ):
            has_authentic_usage = True
            prompt_tokens = int(u.get("promptTokenCount") or 0)
            cached_tokens = int(u.get("cachedContentTokenCount") or 0)
            cand_tokens = int(u.get("candidatesTokenCount") or 0)
            thought_tokens = int(u.get("thoughtsTokenCount") or 0)
            completion_tokens = cand_tokens + thought_tokens
            eval_tokens = max(0, prompt_tokens - cached_tokens)

        # 4. 标准 OpenAI / DeepSeek / Ollama / SiliconFlow / OpenRouter usage
        elif any(k in u for k in ("prompt_tokens", "completion_tokens")):
            has_authentic_usage = True
            prompt_tokens = int(u.get("prompt_tokens") or 0)
            completion_tokens = int(u.get("completion_tokens") or 0)
            details = u.get("prompt_tokens_details") or {}
            if isinstance(details, dict) and details.get("cached_tokens") is not None:
                cached_tokens = int(details.get("cached_tokens") or 0)
            elif u.get("prompt_cache_hit_tokens") is not None:
                cached_tokens = int(u.get("prompt_cache_hit_tokens") or 0)
            else:
                cached_tokens = 0

            if u.get("prompt_cache_miss_tokens") is not None:
                eval_tokens = int(u.get("prompt_cache_miss_tokens") or 0)
            else:
                eval_tokens = max(0, prompt_tokens - cached_tokens)

        # 5. 流式中断/用户 Abort/无 usage 中转站保底双语精准估算
        is_estimated = not has_authentic_usage or aborted
        if not has_authentic_usage:
            prompt_tokens = self.estimated_prompt_tokens
            cached_tokens = 0
            eval_tokens = prompt_tokens
            completion_tokens = estimate_tokens(self.accumulated_text)
        elif aborted and completion_tokens == 0 and self.accumulated_text:
            completion_tokens = estimate_tokens(self.accumulated_text)

        if prompt_tokens == 0 and self.estimated_prompt_tokens > 0:
            prompt_tokens = self.estimated_prompt_tokens
            if eval_tokens == 0 and cached_tokens == 0:
                eval_tokens = prompt_tokens

        # 统一计算生成吞吐速率 TPS（若引擎未自带 predicted_per_second；对极短工具调用防 HTTP 突刺分母过小）
        if tps <= 0.0 and completion_tokens > 0:
            gen_anchor = self._first_byte_ts if self._first_byte_ts is not None else self._start_ts
            gen_duration = max(0.25 if completion_tokens >= 12 else 0.5, end_ts - gen_anchor)
            tps = completion_tokens / gen_duration

        self._finalized = StreamMetricsResult(
            turn_id=self.turn_id,
            step_index=self.step_index,
            agent_role=self.agent_role,
            provider_id=self.provider_id,
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            model_name=self.model_name,
            prompt_tokens=max(0, prompt_tokens),
            cached_tokens=max(0, cached_tokens),
            eval_tokens=max(0, eval_tokens),
            completion_tokens=max(0, completion_tokens),
            saved_tokens=self.saved_tokens,
            context_limit=self.context_limit,
            ttft_ms=round(ttft_ms, 1),
            prefill_ms=round(prefill_ms, 1),
            tps=round(tps, 1),
            is_estimated=is_estimated,
            aborted=aborted,
        )
        return self._finalized

    async def persist(
        self,
        *,
        conversation_id: str,
        incognito: bool = False,
        aborted: bool = False,
    ) -> StreamMetricsResult:
        """收尾指标并在非无痕模式下持久化写入 `token_usage_logs`（入库失败不阻断主生成结果）"""
        res = self.finalize(aborted=aborted)
        if not incognito and conversation_id:
            try:
                await TokenUsageRepository.record_usage(
                    conversation_id=conversation_id,
                    turn_id=res.turn_id,
                    step_index=res.step_index,
                    agent_role=res.agent_role,
                    provider_id=res.provider_id,
                    provider_name=res.provider_name,
                    provider_type=res.provider_type,
                    model_name=res.model_name,
                    prompt_tokens=res.prompt_tokens,
                    cached_tokens=res.cached_tokens,
                    eval_tokens=res.eval_tokens,
                    completion_tokens=res.completion_tokens,
                    saved_tokens=res.saved_tokens,
                    context_limit=res.context_limit,
                    ttft_ms=res.ttft_ms,
                    prefill_ms=res.prefill_ms,
                    tps=res.tps,
                    is_estimated=res.is_estimated,
                )
            except Exception as e:
                from .logging_config import logger

                logger.debug(f"[TokenMetrics] 忽略非致命指标持久化异常: {e}")
        return res

