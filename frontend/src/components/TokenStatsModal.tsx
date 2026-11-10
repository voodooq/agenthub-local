'use client';

import React, { useEffect, useState, useMemo } from 'react';
import {
  X,
  BarChart3,
  Zap,
  Scissors,
  Timer,
  Cpu,
  AlertTriangle,
  RefreshCw,
  Layers,
  CheckCircle2,
} from 'lucide-react';
import { ConversationTokenStats, TurnTokenMetrics } from '@/types';
import { apiFetch } from '@/utils/apiClient';

interface TokenStatsModalProps {
  /** 弹窗开启状态 */
  isOpen: boolean;
  /** 关闭回调 */
  onClose: () => void;
  /** 当前活跃会话 ID（空串或 null 时自动降级使用 liveTurnMetrics） */
  conversationId: string | null;
  /** 前端内存已收集的各轮实时水表（用于无痕模式或新会话即时预览） */
  liveTurnMetrics?: TurnTokenMetrics[];
}

const ROLE_LABELS: Record<string, { label: string; colorClass: string; badgeClass: string }> = {
  primary: {
    label: '主力推理 (Primary)',
    colorClass: 'bg-emerald-500',
    badgeClass: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  },
  scrubber: {
    label: '旁路脱水清洗 (Scrubber)',
    colorClass: 'bg-teal-500',
    badgeClass: 'bg-teal-500/15 text-teal-300 border-teal-500/30',
  },
  summarizer: {
    label: '分块坍缩摘要 (Summarizer)',
    colorClass: 'bg-indigo-500',
    badgeClass: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
  },
};

/**
 * 当无痕模式未落库时，根据内存中的每轮水表构建同构统计报表
 * @param convId - 会话 ID
 * @param turns - 前端内存收到的逐轮水表数组
 */
function buildFallbackStatsFromLiveTurns(
  convId: string,
  turns: TurnTokenMetrics[]
): ConversationTokenStats {
  const sorted = [...turns].sort((a, b) => a.turn_id - b.turn_id);
  const totalPrompt = sorted.reduce((acc, t) => acc + (t.context_tokens || 0), 0);
  const totalCached = sorted.reduce((acc, t) => acc + (t.cached_tokens || 0), 0);
  const totalEval = sorted.reduce((acc, t) => acc + (t.eval_tokens || 0), 0);
  const totalCompletion = sorted.reduce((acc, t) => acc + (t.completion_tokens || 0), 0);
  const totalSaved = sorted.reduce((acc, t) => acc + (t.turn_saved_tokens ?? t.saved_tokens ?? 0), 0);
  const validTtft = sorted.map((t) => t.ttft_ms).filter((v) => v > 0);
  const validTps = sorted.map((t) => t.tps).filter((v) => v > 0);

  return {
    conversation_id: convId || 'incognito',
    summary: {
      total_turns: sorted.length,
      total_calls: sorted.reduce((acc, t) => acc + (t.react_steps || 1), 0),
      total_prompt_tokens: totalPrompt,
      total_cached_tokens: totalCached,
      total_eval_tokens: totalEval,
      total_completion_tokens: totalCompletion,
      avg_cache_hit_pct: totalPrompt > 0 ? Number(((totalCached / totalPrompt) * 100).toFixed(1)) : 0,
      total_saved_tokens: totalSaved,
      scrub_saved_tokens: totalSaved,
      collapsed_saved_tokens: 0,
      collapse_anchor_turn_id: 0,
      avg_ttft_ms: validTtft.length > 0 ? Number((validTtft.reduce((a, b) => a + b, 0) / validTtft.length).toFixed(1)) : 0,
      avg_tps: validTps.length > 0 ? Number((validTps.reduce((a, b) => a + b, 0) / validTps.length).toFixed(1)) : 0,
      hints: ['当前为无痕会话或刚启动首轮对话，展示前端内存实时采样指标。'],
    },
    by_role: sorted.length > 0
      ? [
          {
            agent_role: 'primary',
            call_count: sorted.reduce((acc, t) => acc + (t.react_steps || 1), 0),
            prompt_tokens: totalPrompt,
            cached_tokens: totalCached,
            eval_tokens: totalEval,
            completion_tokens: totalCompletion,
            saved_tokens: totalSaved,
          },
        ]
      : [],
    by_provider: [],
    turns: sorted,
  };
}

/**
 * 会话级 Token 消耗、KV Cache 复用率与本地算力吞吐分析弹窗（支持点击底部水表或输入 /stats 呼出）
 */
export function TokenStatsModal({
  isOpen,
  onClose,
  conversationId,
  liveTurnMetrics = [],
}: TokenStatsModalProps) {
  const [stats, setStats] = useState<ConversationTokenStats | null>(null);
  const [loading, setLoading] = useState(false);

  const loadStats = async () => {
    if (!conversationId) {
      setStats(buildFallbackStatsFromLiveTurns('', liveTurnMetrics));
      return;
    }
    setLoading(true);
    try {
      const res = await apiFetch(`/api/v1/conversations/${conversationId}/token-stats`);
      if (res.ok) {
        const data: ConversationTokenStats = await res.json();
        if (data.turns.length === 0 && liveTurnMetrics.length > 0) {
          setStats(buildFallbackStatsFromLiveTurns(conversationId, liveTurnMetrics));
        } else {
          setStats(data);
        }
      } else {
        setStats(buildFallbackStatsFromLiveTurns(conversationId, liveTurnMetrics));
      }
    } catch {
      setStats(buildFallbackStatsFromLiveTurns(conversationId, liveTurnMetrics));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    void loadStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, conversationId]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const totalRoleTokens = useMemo(() => {
    if (!stats?.by_role?.length) return 0;
    return stats.by_role.reduce((acc, r) => acc + (r.prompt_tokens || 0) + (r.completion_tokens || 0), 0);
  }, [stats]);

  if (!isOpen) return null;

  const summary = stats?.summary;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 animate-fadeIn">
      <div className="w-full max-w-4xl max-h-[88vh] rounded-2xl bg-surface-100 border border-border/80 shadow-2xl flex flex-col overflow-hidden">
        {/* 顶栏 */}
        <div className="px-6 py-4 border-b border-border/70 flex items-center justify-between bg-surface-200/40">
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center">
              <BarChart3 className="w-5 h-5 text-emerald-400" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-foreground flex items-center space-x-2">
                <span>会话上下文压缩与 KV Cache 性能看板</span>
                <span className="px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-500 dark:text-emerald-300 text-[10px] font-mono">
                  /stats · /tokens
                </span>
              </h2>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                全链路追踪实算 Prompt / KV Cache 命中率、本地旁路工具脱水与分块坍缩净收益
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2">
            <button
              type="button"
              onClick={() => void loadStats()}
              disabled={loading}
              className="px-2.5 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-xs text-muted-foreground hover:text-foreground border border-border/70 flex items-center space-x-1 transition-colors"
              title="重新聚合最新 SQLite 指标"
            >
              <RefreshCw className={`w-3.5 h-3.5 text-emerald-500 ${loading ? 'animate-spin' : ''}`} />
              <span>刷新</span>
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
              title="关闭 (ESC)"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 内容滚动区 */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {loading && !stats ? (
            <div className="py-16 text-center text-xs text-muted-foreground flex items-center justify-center space-x-2">
              <RefreshCw className="w-4 h-4 animate-spin text-emerald-500" />
              <span>正在聚合全链路 Token 与缓存命中明细...</span>
            </div>
          ) : (
            <>
              {/* 顶部 4 格核心 KPI */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
                <div className="p-4 rounded-xl bg-surface-200/40 border border-border/70 space-y-1">
                  <div className="text-[11px] text-muted-foreground flex items-center space-x-1.5">
                    <Cpu className="w-3.5 h-3.5 text-emerald-500" />
                    <span>累计新算 Eval / 输出</span>
                  </div>
                  <div className="text-lg font-bold font-mono text-foreground">
                    {(summary?.total_eval_tokens || 0).toLocaleString()}{' '}
                    <span className="text-xs font-normal text-muted-foreground">
                      / {(summary?.total_completion_tokens || 0).toLocaleString()}
                    </span>
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    总上下文请求量: {(summary?.total_prompt_tokens || 0).toLocaleString()} tokens
                  </div>
                </div>

                <div className="p-4 rounded-xl bg-surface-200/40 border border-border/70 space-y-1">
                  <div className="text-[11px] text-muted-foreground flex items-center space-x-1.5">
                    <Zap className="w-3.5 h-3.5 text-amber-500" />
                    <span>全局 KV Cache 命中率</span>
                  </div>
                  <div className="text-lg font-bold font-mono text-amber-500 dark:text-amber-300">
                    {(summary?.avg_cache_hit_pct ?? 0).toFixed(1)}%
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    累计直接复用缓存: {(summary?.total_cached_tokens || 0).toLocaleString()} tokens
                  </div>
                </div>

                <div className="p-4 rounded-xl bg-surface-200/40 border border-border/70 space-y-1">
                  <div className="text-[11px] text-muted-foreground flex items-center space-x-1.5">
                    <Scissors className="w-3.5 h-3.5 text-teal-500" />
                    <span>累计脱水 + 坍缩节省</span>
                  </div>
                  <div className="text-lg font-bold font-mono text-teal-500 dark:text-teal-300">
                    {(summary?.total_saved_tokens || 0).toLocaleString()} <span className="text-xs font-normal">tokens</span>
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    工具脱水 {(summary?.scrub_saved_tokens || 0).toLocaleString()} · 分块坍缩 {(summary?.collapsed_saved_tokens || 0).toLocaleString()}
                  </div>
                </div>

                <div className="p-4 rounded-xl bg-surface-200/40 border border-border/70 space-y-1">
                  <div className="text-[11px] text-muted-foreground flex items-center space-x-1.5">
                    <Timer className="w-3.5 h-3.5 text-indigo-500" />
                    <span>平均首字延迟 & 吞吐</span>
                  </div>
                  <div className="text-lg font-bold font-mono text-indigo-500 dark:text-indigo-300">
                    {summary?.avg_ttft_ms ? `${Math.round(summary.avg_ttft_ms)} ms` : '--'}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    平均生成速率: {summary?.avg_tps ? `${summary.avg_tps.toFixed(1)} tokens/s` : '--'}
                  </div>
                </div>
              </div>

              {/* 智能调优建议与分块坍缩状态横幅 */}
              {summary?.hints && summary.hints.length > 0 && (
                <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/30 space-y-1.5">
                  <div className="text-xs font-semibold text-amber-600 dark:text-amber-300 flex items-center space-x-1.5">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                    <span>上下文压缩与本地缓存诊断报告</span>
                  </div>
                  <ul className="space-y-1 text-[11px] text-foreground/85 pl-5 list-disc">
                    {summary.hints.map((hint, idx) => (
                      <li key={idx}>{hint}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* 按角色分账与通道分布 */}
              {stats && (stats.by_role.length > 0 || stats.by_provider.length > 0) && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  {/* 角色算力分账 */}
                  <div className="p-4 rounded-xl bg-surface-200/30 border border-border/70 space-y-3">
                    <div className="text-xs font-semibold text-foreground flex items-center space-x-1.5">
                      <Layers className="w-3.5 h-3.5 text-emerald-500" />
                      <span>按智能体角色算力分账</span>
                    </div>
                    <div className="space-y-2.5">
                      {stats.by_role.map((bucket) => {
                        const meta = ROLE_LABELS[bucket.agent_role] || {
                          label: bucket.agent_role,
                          colorClass: 'bg-slate-500',
                          badgeClass: 'bg-slate-500/15 text-slate-300 border-slate-500/30',
                        };
                        const roleTotal = (bucket.prompt_tokens || 0) + (bucket.completion_tokens || 0);
                        const ratio = totalRoleTokens > 0 ? Math.min(100, Math.round((roleTotal / totalRoleTokens) * 100)) : 0;
                        return (
                          <div key={bucket.agent_role} className="space-y-1">
                            <div className="flex items-center justify-between text-[11px]">
                              <span className={`px-2 py-0.5 rounded border text-[10px] font-medium ${meta.badgeClass}`}>
                                {meta.label}
                              </span>
                              <span className="font-mono text-muted-foreground">
                                {bucket.call_count} 次调用 · {roleTotal.toLocaleString()} tokens ({ratio}%)
                              </span>
                            </div>
                            <div className="h-1.5 rounded-full bg-surface-300/60 overflow-hidden">
                              <div
                                className={`h-full rounded-full ${meta.colorClass}`}
                                style={{ width: `${Math.max(4, ratio)}%` }}
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* 算力通道与模型明细 */}
                  <div className="p-4 rounded-xl bg-surface-200/30 border border-border/70 space-y-3">
                    <div className="text-xs font-semibold text-foreground flex items-center space-x-1.5">
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                      <span>实际路由通道与模型分布</span>
                    </div>
                    {stats.by_provider.length === 0 ? (
                      <div className="text-[11px] text-muted-foreground py-4 text-center">暂无后端通道明细流水</div>
                    ) : (
                      <div className="space-y-2">
                        {stats.by_provider.map((pv, i) => (
                          <div
                            key={`${pv.provider_type}-${pv.model_name}-${i}`}
                            className="p-2.5 rounded-lg bg-surface-100/80 border border-border/60 flex items-center justify-between text-[11px]"
                          >
                            <div className="min-w-0">
                              <div className="font-semibold text-foreground truncate">
                                {pv.provider_name || pv.provider_type} · <span className="font-mono text-emerald-500">{pv.model_name}</span>
                              </div>
                              <div className="text-[10px] text-muted-foreground">
                                调用 {pv.call_count} 次 · 缓存命中 {pv.cached_tokens.toLocaleString()} / {pv.prompt_tokens.toLocaleString()}
                              </div>
                            </div>
                            <span className="px-2 py-0.5 rounded bg-surface-200 font-mono text-[10px] text-muted-foreground shrink-0">
                              {pv.provider_type}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* 逐轮 Turn 趋势明细表 */}
              <div className="rounded-xl border border-border/70 overflow-hidden">
                <div className="px-4 py-2.5 bg-surface-200/50 border-b border-border/70 flex items-center justify-between">
                  <span className="text-xs font-semibold text-foreground">逐轮上下文水位与缓存复用明细 ({stats?.turns.length || 0} 轮)</span>
                  {summary && summary.collapse_anchor_turn_id > 0 && (
                    <span className="text-[11px] text-indigo-400 font-mono">
                      已固化坍缩锚点: Turn ≤ {summary.collapse_anchor_turn_id}
                    </span>
                  )}
                </div>

                {!stats?.turns || stats.turns.length === 0 ? (
                  <div className="p-8 text-center text-xs text-muted-foreground">
                    当前会话暂无记录到的推理轮次，发送任意消息后即可在此查看实时上下文与缓存命中曲线。
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left border-collapse text-[11px]">
                      <thead>
                        <tr className="bg-surface-200/30 text-muted-foreground border-b border-border/60">
                          <th className="py-2 px-3 font-medium">Turn</th>
                          <th className="py-2 px-3 font-medium">模型 / 通道</th>
                          <th className="py-2 px-3 font-medium">上下文水位</th>
                          <th className="py-2 px-3 font-medium">KV Cache 命中</th>
                          <th className="py-2 px-3 font-medium">本轮脱水节省</th>
                          <th className="py-2 px-3 font-medium">TTFT / 吞吐</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/50">
                        {stats.turns.map((t) => {
                          const isLocal = t.provider_type === 'llama_server' || t.provider_type === 'ollama';
                          const warnNoCache = isLocal && t.turn_id >= 2 && t.cached_tokens === 0;
                          return (
                            <tr key={t.turn_id} className="hover:bg-surface-200/30 transition-colors">
                              <td className="py-2 px-3 font-mono font-semibold text-foreground">
                                #{t.turn_id}
                                {t.is_estimated && (
                                  <span className="ml-1 text-[10px] font-normal text-muted-foreground">(估)</span>
                                )}
                              </td>
                              <td className="py-2 px-3 font-mono text-foreground/85">
                                {t.model_name || '--'}
                                <span className="ml-1.5 text-[10px] text-muted-foreground">({t.provider_type})</span>
                              </td>
                              <td className="py-2 px-3 font-mono">
                                {t.context_limit > 0
                                  ? `${t.context_tokens.toLocaleString()} / ${t.context_limit.toLocaleString()} (${t.context_usage_pct.toFixed(1)}%)`
                                  : `${t.context_tokens.toLocaleString()} tokens`}
                              </td>
                              <td className="py-2 px-3 font-mono">
                                <span className={t.cached_tokens > 0 ? 'text-amber-500 dark:text-amber-300 font-medium' : 'text-muted-foreground'}>
                                  {t.cached_tokens.toLocaleString()} ({t.cache_hit_pct.toFixed(1)}%)
                                </span>
                                {warnNoCache && (
                                  <span className="ml-1.5 px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-500 text-[10px]">
                                    未复用
                                  </span>
                                )}
                              </td>
                              <td className="py-2 px-3 font-mono text-teal-600 dark:text-teal-300">
                                +{(t.turn_saved_tokens ?? t.saved_tokens ?? 0).toLocaleString()}
                              </td>
                              <td className="py-2 px-3 font-mono text-muted-foreground">
                                {t.ttft_ms > 0 ? `${Math.round(t.ttft_ms)}ms` : '--'}
                                {t.tps > 0 ? ` · ${t.tps.toFixed(1)} t/s` : ''}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

