'use client';

import React from 'react';
import { AlertTriangle, CheckCircle, RefreshCw, XCircle } from 'lucide-react';

interface LowConfidenceConfirmCardProps {
  score: number;
  reasons: string[];
  onAccept: () => void;
  onRetryRetrieval: () => void;
  onCancel: () => void;
}

export function LowConfidenceConfirmCard({
  score,
  reasons,
  onAccept,
  onRetryRetrieval,
  onCancel,
}: LowConfidenceConfirmCardProps) {
  const pct = Math.round(score * 100);

  return (
    <div className="my-4 rounded-xl border border-border bg-surface-card p-4 shadow-lg text-foreground">
      <div className="flex items-center gap-2 border-b border-border pb-3">
        <AlertTriangle className="h-5 w-5 text-amber-500 flex-shrink-0" />
        <div>
          <h4 className="text-sm font-semibold text-foreground">
            严谨模式：动态置信度过低拦截 ({pct}%)
          </h4>
          <p className="text-xs text-muted-foreground mt-0.5">
            系统检测到当前回答依据薄弱或存在多版本新旧规程冲突，已暂停直接采纳结论。
          </p>
        </div>
      </div>

      {reasons.length > 0 && (
        <div className="my-3 rounded-lg bg-background border border-border p-2.5 text-xs">
          <span className="font-semibold text-foreground">风险归因分析:</span>
          <ul className="mt-1 list-disc list-inside space-y-0.5 text-muted-foreground">
            {reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center justify-end gap-2 pt-2 border-t border-border">
        <button
          onClick={onCancel}
          className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
        >
          <XCircle className="h-3.5 w-3.5" /> 终止并放弃
        </button>
        <button
          onClick={onRetryRetrieval}
          className="flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted text-foreground transition-colors"
        >
          <RefreshCw className="h-3.5 w-3.5" /> 扩大范围补全检索
        </button>
        <button
          onClick={onAccept}
          className="flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          <CheckCircle className="h-3.5 w-3.5" /> 仍要采纳此回答
        </button>
      </div>
    </div>
  );
}
