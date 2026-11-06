'use client';

import React, { useState } from 'react';
import {
  Terminal,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Loader2,
  ShieldAlert,
} from 'lucide-react';
import { ToolExecutionCardItem } from '@/types';

interface InlineToolCardProps {
  events: ToolExecutionCardItem[];
  onApprove: (callId: string, approved: boolean) => void;
}

export function InlineToolCard({ events, onApprove }: InlineToolCardProps) {
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});

  if (!events || events.length === 0) return null;

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  return (
    <div className="w-full max-w-[88%] md:max-w-[82%] space-y-2 my-1">
      {events.map((ev) => {
        const isExpanded = !!expandedIds[ev.call_id];

        // 1. 待用户审批状态（灰名单命令拦截）
        if (ev.status === 'require_approval') {
          return (
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
          );
        }

        // 2. 正在运行状态
        if (ev.status === 'running') {
          return (
            <div
              key={ev.call_id}
              className="rounded-xl border border-blue-500/30 bg-blue-500/5 px-3.5 py-2.5 flex items-center justify-between text-xs"
            >
              <div className="flex items-center space-x-2 min-w-0">
                <Loader2 className="w-3.5 h-3.5 text-blue-500 animate-spin shrink-0" />
                <span className="font-mono text-foreground truncate">
                  正在执行: {ev.command}
                </span>
              </div>
              <span className="text-[11px] text-blue-500 shrink-0 ml-2">运行中...</span>
            </div>
          );
        }

        // 3. 已完成 / 已拒绝 / 黑名单拦截（默认单行折叠，点击可展开完整输出）
        const isDeniedOrRejected = ev.status === 'denied' || ev.status === 'rejected';
        return (
          <div
            key={ev.call_id}
            className="rounded-xl border border-border bg-surface-100/80 overflow-hidden text-xs transition-all"
          >
            <button
              type="button"
              onClick={() => toggleExpand(ev.call_id)}
              className="w-full px-3.5 py-2 flex items-center justify-between hover:bg-surface-200/60 transition-colors text-left"
            >
              <div className="flex items-center space-x-2 min-w-0 flex-1">
                {isDeniedOrRejected ? (
                  <ShieldAlert className="w-3.5 h-3.5 text-rose-500 shrink-0" />
                ) : (
                  <Terminal className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                )}
                <span className="font-mono text-muted-foreground truncate">
                  {ev.compressed || `已执行: ${ev.command}`}
                </span>
              </div>

              <div className="flex items-center space-x-1.5 shrink-0 ml-2 text-muted-foreground">
                <span className="text-[11px]">{isExpanded ? '收起输出' : '展开详情'}</span>
                {isExpanded ? (
                  <ChevronDown className="w-3.5 h-3.5" />
                ) : (
                  <ChevronRight className="w-3.5 h-3.5" />
                )}
              </div>
            </button>

            {isExpanded && ev.output && (
              <div className="px-3.5 py-2.5 border-t border-border bg-black/85 text-zinc-200 font-mono text-[11px] whitespace-pre-wrap break-all max-h-60 overflow-y-auto">
                <div className="text-emerald-400 mb-1">$ {ev.command}</div>
                {ev.output}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

