'use client';

import React, { useState, useEffect } from 'react';
import { CheckCircle2, XCircle, AlertOctagon, ArrowDownRight, RefreshCw, X, ShieldAlert, Cpu } from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface RightActionDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  pendingCountChange?: (count: number) => void;
}

export function RightActionDrawer({ isOpen, onClose, pendingCountChange }: RightActionDrawerProps) {
  const [proposals, setProposals] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [executingId, setExecutingId] = useState<string | null>(null);

  const fetchProposals = async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/api/v1/system/proposals');
      if (res.ok) {
        const list = await res.json();
        setProposals(list);
        const pending = list.filter((p: any) => p.status === 'pending').length;
        if (pendingCountChange) pendingCountChange(pending);
      }
    } catch (e) {
      console.warn('获取提案失败:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProposals();
  }, [isOpen]);

  const handleApprove = async (id: string) => {
    setExecutingId(id);
    try {
      const res = await apiFetch(`/api/v1/system/proposals/${id}/approve`, { method: 'POST' });
      if (res.ok) {
        await fetchProposals();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || '执行提案失败');
      }
    } catch (e: any) {
      alert(`执行异常: ${e.message}`);
    } finally {
      setExecutingId(null);
    }
  };

  const handleReject = async (id: string) => {
    try {
      const res = await apiFetch(`/api/v1/system/proposals/${id}/reject`, { method: 'POST' });
      if (res.ok) {
        await fetchProposals();
      }
    } catch (e) {
      console.warn('拒绝提案失败:', e);
    }
  };

  if (!isOpen) return null;

  const pendingProposals = proposals.filter((p) => p.status === 'pending');
  const finishedProposals = proposals.filter((p) => p.status !== 'pending');

  return (
    <div className="fixed inset-y-0 right-0 z-40 w-full max-w-md bg-surface-100/95 backdrop-blur-xl border-l border-white/10 shadow-2xl flex flex-col animate-slide-left">
      {/* 顶部标题栏 */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 bg-surface-200/50">
        <div className="flex items-center space-x-2">
          <ShieldAlert className="w-5 h-5 text-amber-400" />
          <h3 className="text-sm font-semibold text-slate-100">智能优化与结果验收区</h3>
          {pendingProposals.length > 0 && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30 font-bold">
              {pendingProposals.length} 待审批
            </span>
          )}
        </div>
        <div className="flex items-center space-x-1">
          <button
            onClick={fetchProposals}
            disabled={loading}
            className="p-1.5 rounded-lg hover:bg-white/10 text-slate-400 hover:text-white"
            title="刷新提案"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-white/10 text-slate-400 hover:text-white"
            title="关闭面板"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* 滚动内容区域 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
        {/* 待审批区 */}
        <div className="space-y-3">
          <h4 className="text-[11px] font-semibold text-slate-400 tracking-wider uppercase flex items-center justify-between">
            <span>待审批优化清单 ({pendingProposals.length})</span>
            <span className="text-[10px] font-normal text-slate-500">遵循人类确认锁</span>
          </h4>

          {pendingProposals.length === 0 ? (
            <div className="p-4 rounded-xl bg-surface-50/30 border border-white/5 text-center text-slate-500 text-xs">
              暂无待审批动作，系统运行良好 ✨
            </div>
          ) : (
            pendingProposals.map((prop) => (
              <div
                key={prop.id}
                className="p-4 rounded-xl bg-surface-200/60 border border-amber-500/30 shadow-lg space-y-3 relative overflow-hidden group"
              >
                <div className="absolute top-0 left-0 w-1 h-full bg-amber-500" />
                <div className="flex items-start justify-between">
                  <div className="space-y-1">
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20">
                      {prop.id}
                    </span>
                    <h5 className="font-semibold text-slate-100 text-xs mt-1">{prop.title}</h5>
                  </div>
                  <span className="text-[10px] text-slate-400">{prop.created_at.slice(11)}</span>
                </div>

                <p className="text-slate-300 leading-relaxed">{prop.description}</p>

                {/* 目标列表 */}
                {prop.targets && prop.targets.length > 0 && (
                  <div className="p-2 rounded-lg bg-surface-50/50 border border-white/5 space-y-1 font-mono text-[11px]">
                    {prop.targets.slice(0, 3).map((t: any, idx: number) => (
                      <div key={idx} className="flex items-center justify-between text-slate-400">
                        <span className="truncate max-w-[200px] text-slate-200">{t.name || t.path}</span>
                        {t.pid && <span className="text-slate-500">PID: {t.pid}</span>}
                        {t.memory_mb && <span className="text-amber-300">{t.memory_mb} MB</span>}
                      </div>
                    ))}
                    {prop.targets.length > 3 && (
                      <div className="text-[10px] text-slate-500 text-right">等 {prop.targets.length} 项目标</div>
                    )}
                  </div>
                )}

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-emerald-400 font-medium">收益: {prop.estimated_gain}</span>
                  <div className="flex items-center space-x-2">
                    <button
                      onClick={() => handleReject(prop.id)}
                      disabled={executingId === prop.id}
                      className="px-2.5 py-1.5 rounded-lg hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
                    >
                      驳回
                    </button>
                    <button
                      onClick={() => handleApprove(prop.id)}
                      disabled={executingId === prop.id}
                      className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium shadow-md shadow-emerald-900/30 flex items-center space-x-1.5 disabled:opacity-50"
                    >
                      {executingId === prop.id ? (
                        <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <CheckCircle2 className="w-3.5 h-3.5" />
                      )}
                      <span>{executingId === prop.id ? '执行中...' : '一键确认执行'}</span>
                    </button>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>

        {/* 历史复盘与验收区 */}
        {finishedProposals.length > 0 && (
          <div className="space-y-3 pt-2">
            <h4 className="text-[11px] font-semibold text-slate-400 tracking-wider uppercase">
              已完成优化与复盘 ({finishedProposals.length})
            </h4>

            {finishedProposals.map((prop) => (
              <div
                key={prop.id}
                className="p-3.5 rounded-xl bg-surface-50/40 border border-white/5 space-y-2 opacity-90 hover:opacity-100 transition-opacity"
              >
                <div className="flex items-center justify-between text-xs">
                  <span className="font-medium text-slate-200">{prop.title}</span>
                  <span
                    className={`text-[10px] px-2 py-0.5 rounded ${
                      prop.status === 'approved'
                        ? 'bg-emerald-500/20 text-emerald-300'
                        : 'bg-rose-500/20 text-rose-300'
                    }`}
                  >
                    {prop.status === 'approved' ? '执行成功' : '已驳回'}
                  </span>
                </div>

                {/* 优化前后复盘指标 */}
                {prop.result_diff && prop.status === 'approved' && (
                  <div className="p-2.5 rounded-lg bg-surface-200/50 border border-white/5 space-y-1.5 text-[11px]">
                    <div className="flex items-center justify-between text-slate-300 font-mono">
                      <span>内存变化:</span>
                      <span className="flex items-center text-emerald-400 font-semibold">
                        {prop.result_diff.memory_before_percent}% <ArrowDownRight className="w-3.5 h-3.5" />{' '}
                        {prop.result_diff.memory_after_percent}% (-{prop.result_diff.memory_freed_gb} GB)
                      </span>
                    </div>
                    {prop.result_diff.execution_logs && (
                      <div className="text-[10px] text-slate-500 space-y-0.5 font-mono">
                        {prop.result_diff.execution_logs.slice(0, 2).map((log: string, idx: number) => (
                          <div key={idx} className="truncate">
                            ✓ {log}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

