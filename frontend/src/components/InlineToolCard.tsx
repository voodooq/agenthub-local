'use client';

import React, { useState } from 'react';
import {
  Terminal,
  CheckCircle2,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Loader2,
  ShieldAlert,
} from 'lucide-react';
import { ToolExecutionCardItem } from '@/types';

interface InlineToolCardProps {
  events: ToolExecutionCardItem[];
  onApprove: (callId: string, approved: boolean) => void;
}

export function InlineToolCard({ events, onApprove }: InlineToolCardProps) {
  // 整组执行步骤默认处于单行收缩态，避免多步骤执行将上方 Thinking 思考内容顶出视线
  const [isGroupExpanded, setIsGroupExpanded] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});

  if (!events || events.length === 0) return null;

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  // 待用户审批的高危命令始终独立高亮展示，确保不因步骤折叠而被遗漏
  const pendingApprovals = events.filter((e) => e.status === 'require_approval');
  const regularEvents = events.filter((e) => e.status !== 'require_approval');

  const runningCount = regularEvents.filter((e) => e.status === 'running').length;
  const deniedCount = regularEvents.filter(
    (e) => e.status === 'denied' || e.status === 'rejected'
  ).length;
  const completedCount = regularEvents.filter((e) => e.status === 'completed').length;
  const latestEvent = regularEvents[regularEvents.length - 1];
  const latestSummary = latestEvent
    ? latestEvent.status === 'running'
      ? `正在执行: ${latestEvent.command}`
      : latestEvent.compressed || `已执行: ${latestEvent.command}`
    : '';

  return (
    <div className="w-full max-w-[88%] md:max-w-[82%] space-y-2 my-1">
      {/* 1. 待人工审批项：独立显眼卡片 */}
      {pendingApprovals.map((ev) => (
        <div
          key={ev.call_id}
          className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3.5 space-y-2.5 shadow-sm animate-fade-in"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2 text-amber-600 dark:text-amber-400 text-xs font-bold">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>安全沙箱拦截：Agent 申请执行本机终端命令</span>
            </div>
            {ev.cwd && (
              <span className="text-[11px] text-muted-foreground font-mono truncate max-w-[220px]">
                目录: {ev.cwd}
              </span>
            )}
          </div>

          {ev.reason && (
            <p className="text-xs text-foreground/90">
              <span className="text-muted-foreground">执行目的：</span>
              {ev.reason}
            </p>
          )}

          <div className="px-3 py-2 rounded-lg bg-black/80 text-emerald-400 font-mono text-xs break-all">
            $ {ev.command}
          </div>

          <div className="flex items-center justify-end space-x-2 pt-1">
            <button
              type="button"
              onClick={() => onApprove(ev.call_id, false)}
              className="px-3 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 text-rose-600 dark:text-rose-400 text-xs font-semibold transition-colors"
            >
              拒绝执行
            </button>
            <button
              type="button"
              onClick={() => onApprove(ev.call_id, true)}
              className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition-colors shadow-sm"
            >
              允许执行
            </button>
          </div>
        </div>
      ))}

      {/* 2. 常规执行步骤汇总区：默认单行收缩，支持按需展开与手动收起 */}
      {regularEvents.length > 0 && (
        <div
          className={`rounded-xl border overflow-hidden text-xs transition-all ${
            runningCount > 0
              ? 'border-emerald-500/35 bg-emerald-500/5'
              : deniedCount > 0
              ? 'border-rose-500/30 bg-surface-100/85'
              : 'border-border bg-surface-100/80'
          }`}
        >
          {/* 顶部单行总览栏 */}
          <button
            type="button"
            onClick={() => setIsGroupExpanded((prev) => !prev)}
            className="w-full px-3.5 py-2 flex items-center justify-between hover:bg-surface-200/60 transition-colors text-left"
          >
            <div className="flex items-center space-x-2 min-w-0 flex-1">
              {runningCount > 0 ? (
                <Loader2 className="w-3.5 h-3.5 text-emerald-500 animate-spin shrink-0" />
              ) : deniedCount > 0 ? (
                <ShieldAlert className="w-3.5 h-3.5 text-rose-500 shrink-0" />
              ) : (
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
              )}

              {!isGroupExpanded ? (
                <span className="font-mono text-muted-foreground truncate">
                  {runningCount > 0
                    ? `执行步骤 · 共 ${regularEvents.length} 步（进行中：${latestEvent?.command || ''}）`
                    : `执行步骤 · 共 ${regularEvents.length} 步（最新：${latestSummary}）`}
                </span>
              ) : (
                <span className="font-medium text-foreground truncate">
                  执行步骤详情 · 共 {regularEvents.length} 步
                  <span className="text-muted-foreground font-mono ml-2">
                    (完成 {completedCount}
                    {runningCount > 0 ? ` · 运行 ${runningCount}` : ''}
                    {deniedCount > 0 ? ` · 拦截/拒绝 ${deniedCount}` : ''})
                  </span>
                </span>
              )}
            </div>

            <div className="flex items-center space-x-1.5 shrink-0 ml-2 text-muted-foreground">
              <span className="text-[11px] font-medium">
                {isGroupExpanded ? '收起步骤' : `展开步骤 (${regularEvents.length})`}
              </span>
              {isGroupExpanded ? (
                <ChevronUp className="w-3.5 h-3.5" />
              ) : (
                <ChevronDown className="w-3.5 h-3.5" />
              )}
            </div>
          </button>

          {/* 展开后的分步明细清单（限制最大高度可内部滚动，底部也可快捷收起） */}
          {isGroupExpanded && (
            <div className="border-t border-border/60 bg-background/40">
              <div className="max-h-[340px] overflow-y-auto divide-y divide-border/50">
                {regularEvents.map((ev, stepIdx) => {
                  const isExpanded = !!expandedIds[ev.call_id];
                  const isRunning = ev.status === 'running';
                  const isDeniedOrRejected = ev.status === 'denied' || ev.status === 'rejected';

                  return (
                    <div key={ev.call_id} className="text-xs">
                      <button
                        type="button"
                        onClick={() => {
                          if (ev.output || ev.command) toggleExpand(ev.call_id);
                        }}
                        className="w-full px-3.5 py-2 flex items-center justify-between hover:bg-surface-200/50 transition-colors text-left"
                      >
                        <div className="flex items-center space-x-2 min-w-0 flex-1">
                          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-200/90 text-muted-foreground shrink-0">
                            #{stepIdx + 1}
                          </span>
                          {isRunning ? (
                            <Loader2 className="w-3.5 h-3.5 text-emerald-500 animate-spin shrink-0" />
                          ) : isDeniedOrRejected ? (
                            <ShieldAlert className="w-3.5 h-3.5 text-rose-500 shrink-0" />
                          ) : (
                            <Terminal className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                          )}
                          <span className="font-mono text-muted-foreground truncate">
                            {isRunning
                              ? `正在执行: ${ev.command}`
                              : ev.compressed || `已执行: ${ev.command}`}
                          </span>
                        </div>

                        <div className="flex items-center space-x-1.5 shrink-0 ml-2 text-muted-foreground">
                          {isRunning ? (
                            <span className="text-[11px] text-emerald-600 dark:text-emerald-400 font-medium">
                              运行中...
                            </span>
                          ) : (
                            <>
                              <span className="text-[11px]">
                                {isExpanded ? '收起输出' : '展开详情'}
                              </span>
                              {isExpanded ? (
                                <ChevronDown className="w-3.5 h-3.5" />
                              ) : (
                                <ChevronRight className="w-3.5 h-3.5" />
                              )}
                            </>
                          )}
                        </div>
                      </button>

                      {isExpanded && (ev.output || ev.command) && (
                        <div className="px-3.5 py-2.5 border-t border-border/50 bg-black/85 text-zinc-200 font-mono text-[11px] whitespace-pre-wrap break-all max-h-60 overflow-y-auto">
                          <div className="text-emerald-400 mb-1">$ {ev.command}</div>
                          {ev.output || '（无标准输出）'}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              <div className="px-3.5 py-1.5 border-t border-border/60 bg-surface-100/70 flex justify-end">
                <button
                  type="button"
                  onClick={() => setIsGroupExpanded(false)}
                  className="text-[11px] text-muted-foreground hover:text-foreground flex items-center space-x-1 transition-colors"
                >
                  <ChevronUp className="w-3.5 h-3.5" />
                  <span>收起执行步骤</span>
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

