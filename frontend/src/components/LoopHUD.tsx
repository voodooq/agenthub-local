'use strict';

import React, { useState } from 'react';
import { TaskLoopResponse, LoopStatus } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import { useLanguage } from '@/context/LanguageContext';

interface LoopHUDProps {
  loop: TaskLoopResponse | null;
  onRefresh?: () => void;
}

export function LoopHUD({ loop, onRefresh }: LoopHUDProps) {
  const { t } = useLanguage();
  const [isExpanded, setIsExpanded] = useState(false);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  if (!loop || loop.loop_status === 'idle') {
    return null;
  }

  const handleControl = async (action: 'pause' | 'resume' | 'abort' | 'complete') => {
    setLoadingAction(action);
    try {
      await apiFetch(`/api/v1/loop/${loop.id}/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (onRefresh) onRefresh();
    } catch (e) {
      console.error('控制任务循环失败:', e);
    } finally {
      setLoadingAction(null);
    }
  };

  const getStatusBadge = (status: LoopStatus) => {
    switch (status) {
      case 'planning':
        return { text: t('规划子任务中'), color: 'bg-blue-500/10 text-blue-400 border-blue-500/20' };
      case 'executing':
        return { text: t('具身执行中'), color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' };
      case 'verifying':
        return { text: t('客观验收中 (DoD)'), color: 'bg-purple-500/10 text-purple-400 border-purple-500/20' };
      case 'reflecting':
        return { text: t('自愈反思中'), color: 'bg-amber-500/10 text-amber-400 border-amber-500/20' };
      case 'waiting_approval':
        return { text: t('等待人工审批'), color: 'bg-rose-500/10 text-rose-400 border-rose-500/20 animate-pulse' };
      case 'completed':
        return { text: t('任务已交付'), color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' };
      case 'failed':
        return { text: t('熔断或终止'), color: 'bg-red-500/10 text-red-400 border-red-500/20' };
      case 'paused':
        return { text: t('已暂停'), color: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20' };
      default:
        return { text: status, color: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20' };
    }
  };

  const badge = getStatusBadge(loop.loop_status);

  return (
    <div className="w-full mb-3 rounded-xl border border-zinc-700/60 bg-zinc-900/90 backdrop-blur-md shadow-lg overflow-hidden transition-all duration-200">
      <div className="flex items-center justify-between px-4 py-2.5">
        <div className="flex items-center space-x-3 min-w-0">
          <div className="flex items-center space-x-2">
            <span className="relative flex h-2.5 w-2.5">
              {['executing', 'planning', 'verifying', 'reflecting'].includes(loop.loop_status) && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              )}
              <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${
                loop.loop_status === 'waiting_approval' ? 'bg-rose-500' :
                loop.loop_status === 'completed' ? 'bg-emerald-500' :
                loop.loop_status === 'failed' ? 'bg-red-500' : 'bg-emerald-400'
              }`}></span>
            </span>
            <span className="text-xs font-semibold tracking-wider uppercase text-zinc-400">
              {t('Loop 自主领航舱')}
            </span>
          </div>

          <span className={`text-[11px] px-2 py-0.5 rounded-full border font-medium ${badge.color}`}>
            {badge.text}
          </span>

          <span className="text-[11px] text-zinc-400 bg-zinc-800/80 px-2 py-0.5 rounded border border-zinc-700/50">
            {t('轮次')}: {loop.current_iteration} / {loop.max_iterations}
          </span>

          <span className="text-xs text-zinc-200 truncate max-w-[280px] sm:max-w-[420px] font-mono">
            {loop.goal_prompt}
          </span>
        </div>

        <div className="flex items-center space-x-1.5 flex-shrink-0">
          {['executing', 'planning', 'verifying', 'reflecting'].includes(loop.loop_status) && (
            <button
              onClick={() => handleControl('complete')}
              disabled={loadingAction !== null}
              className="text-[11px] px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-medium shadow-sm transition flex items-center space-x-1"
              title={t('确认本次任务已满足预期，标记完成并归档')}
            >
              <span>{loadingAction === 'complete' ? '...' : t('✅ 验收通过')}</span>
            </button>
          )}

          {loop.loop_status === 'executing' && (
            <button
              onClick={() => handleControl('pause')}
              disabled={loadingAction !== null}
              className="text-[11px] px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 transition"
              title={t('暂停循环')}
            >
              {loadingAction === 'pause' ? '...' : t('暂停')}
            </button>
          )}

          {loop.loop_status === 'paused' && (
            <button
              onClick={() => handleControl('resume')}
              disabled={loadingAction !== null}
              className="text-[11px] px-2.5 py-1 rounded bg-emerald-600/30 hover:bg-emerald-600/50 text-emerald-300 border border-emerald-500/40 transition"
              title={t('继续执行')}
            >
              {loadingAction === 'resume' ? '...' : t('继续')}
            </button>
          )}

          {['executing', 'planning', 'verifying', 'reflecting', 'paused', 'waiting_approval'].includes(loop.loop_status) && (
            <button
              onClick={() => handleControl('abort')}
              disabled={loadingAction !== null}
              className="text-[11px] px-2.5 py-1 rounded bg-red-500/10 hover:bg-red-500/20 text-red-300 border border-red-500/30 transition"
              title={t('强行终止')}
            >
              {loadingAction === 'abort' ? '...' : t('终止')}
            </button>
          )}

          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="text-[11px] px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition"
          >
            {isExpanded ? t('收起清单') : t('查看清单')}
          </button>
        </div>
      </div>

      {isExpanded && loop.scratchpad_md && (
        <div className="px-4 py-3 border-t border-zinc-800/80 bg-black/40 text-xs font-mono text-zinc-300 max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed select-text">
          {loop.scratchpad_md}
        </div>
      )}
    </div>
  );
}

