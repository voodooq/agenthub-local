'use client';

import React from 'react';
import { BarChart3, Zap, Scissors, Timer, AlertTriangle } from 'lucide-react';
import { TurnTokenMetrics } from '@/types';

interface TokenMetricsFooterBarProps {
  /** 本轮聚合后的 Token 与 KV Cache 水表指标 */
  stats?: TurnTokenMetrics | null;
  /** 点击水表时打开会话 Token 详细统计弹窗 */
  onOpenStats?: () => void;
}

/**
 * 格式化整数为千分位简明展示（大于 10,000 时保留一位小数显示 k 单位）
 * @param val - 待格式化的数值
 */
function formatCompactTokens(val: number): string {
  const n = Math.max(0, Math.round(val || 0));
  if (n >= 10000) {
    return `${(n / 1000).toFixed(1)}k`;
  }
  return n.toLocaleString();
}

/**
 * AI 回复消息底部的单行轻量 Token 与 KV Cache 微水表
 * 紧贴 MessageActions 之上渲染，支持水位三段式色阶提示与本地缓存复用告警
 */
export function TokenMetricsFooterBar({ stats, onOpenStats }: TokenMetricsFooterBarProps) {
  if (!stats) return null;

  const usagePct = Number(stats.context_usage_pct || 0);
  const hitPct = Number(stats.cache_hit_pct || 0);
  const isLocalEngine = stats.provider_type === 'llama_server' || stats.provider_type === 'ollama';
  // 本地引擎在多轮对话（Turn >= 2）时若 KV Cache 命中为 0，往往意味着启用了多槽位却未开启 --cache-reuse
  const showLocalCacheMissWarn = isLocalEngine && stats.turn_id >= 2 && (stats.cached_tokens || 0) === 0;

  // 上下文水位三段色阶：<65% 翠绿中性、65%~85% 琥珀警告、>85% 玫瑰红高水位
  const contextBadgeClass =
    usagePct > 85
      ? 'text-rose-500 dark:text-rose-400 font-semibold'
      : usagePct >= 65
        ? 'text-amber-600 dark:text-amber-400 font-medium'
        : 'text-emerald-600 dark:text-emerald-400';

  const contextText =
    stats.context_limit > 0
      ? `${formatCompactTokens(stats.context_tokens)}/${formatCompactTokens(stats.context_limit)} (${usagePct.toFixed(1)}%)`
      : `${formatCompactTokens(stats.context_tokens)} tokens`;

  const cacheText =
    stats.cached_tokens > 0
      ? `${formatCompactTokens(stats.cached_tokens)} (${hitPct.toFixed(1)}%)`
      : `0 (${hitPct.toFixed(1)}%)`;

  const latencyParts: string[] = [];
  if (stats.ttft_ms > 0) {
    latencyParts.push(`${Math.round(stats.ttft_ms)}ms`);
  }
  if (stats.tps > 0) {
    latencyParts.push(`${stats.tps.toFixed(1)} t/s`);
  }
  const latencyText = latencyParts.length > 0 ? latencyParts.join(' · ') : '--';

  return (
    <div className="mt-2 mb-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
      <button
        type="button"
        onClick={onOpenStats}
        title="点击查看本会话完整 Token 消耗、KV Cache 命中趋势与分块坍缩明细（也可输入 /stats 随时呼出）"
        className="group inline-flex flex-wrap items-center gap-x-2 gap-y-1 px-2.5 py-1 rounded-lg bg-surface-100/70 hover:bg-surface-200/80 border border-border/60 hover:border-emerald-500/40 text-[11px] text-muted-foreground hover:text-foreground transition-all text-left"
      >
        {/* 轮次与上下文水位 */}
        <span className="inline-flex items-center space-x-1">
          <BarChart3 className="w-3 h-3 text-emerald-500 shrink-0" />
          <span className="font-mono text-[10px] text-foreground/80 font-semibold">[Turn {stats.turn_id}]</span>
          <span>上下文:</span>
          <span className={`font-mono ${contextBadgeClass}`}>{contextText}</span>
        </span>

        <span className="text-border select-none">｜</span>

        {/* KV Cache 命中率 */}
        <span className="inline-flex items-center space-x-1">
          <Zap className={`w-3 h-3 shrink-0 ${stats.cached_tokens > 0 ? 'text-amber-500' : 'text-muted-foreground/60'}`} />
          <span>缓存命中:</span>
          <span className={`font-mono ${stats.cached_tokens > 0 ? 'text-amber-600 dark:text-amber-300 font-medium' : ''}`}>
            {cacheText}
          </span>
          {showLocalCacheMissWarn && (
            <span
              title="本地引擎多轮对话未复用 KV Cache：请确认 llama-server 已添加 --cache-reuse 256，且多并发时按总上下文除以 -np 设置单槽位上限"
              className="inline-flex items-center space-x-0.5 px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-600 dark:text-amber-300 border border-amber-500/30 text-[10px] font-medium"
            >
              <AlertTriangle className="w-2.5 h-2.5" />
              <span>未复用缓存</span>
            </span>
          )}
        </span>

        <span className="text-border select-none">｜</span>

        {/* 旁路脱水与坍缩节省 */}
        <span className="inline-flex items-center space-x-1">
          <Scissors className="w-3 h-3 text-teal-500 shrink-0" />
          <span>已脱水节省:</span>
          <span className={`font-mono ${stats.saved_tokens > 0 ? 'text-teal-600 dark:text-teal-300 font-medium' : ''}`}>
            {formatCompactTokens(stats.saved_tokens)} tokens
          </span>
        </span>

        <span className="text-border select-none">｜</span>

        {/* 首字延迟与生成吞吐 */}
        <span className="inline-flex items-center space-x-1">
          <Timer className="w-3 h-3 text-indigo-500 shrink-0" />
          <span>TTFT:</span>
          <span className="font-mono text-foreground/85">{latencyText}</span>
          {stats.is_estimated && (
            <span
              title="当前底层推理通道未返回原生精确 usage 字节计数，数值基于本地分词器估算"
              className="px-1 py-0.2 rounded bg-surface-200 text-muted-foreground text-[10px]"
            >
              (估算)
            </span>
          )}
        </span>
      </button>
    </div>
  );
}

