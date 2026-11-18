'use client';

import React, { useState } from 'react';
import { ShieldCheck, ShieldAlert, ChevronDown, ChevronUp, Link as LinkIcon } from 'lucide-react';

interface ConfidenceBadgeBarProps {
  score: number;
  tierLevel?: 'high' | 'medium' | 'low';
  reasons?: string[];
  evidenceSources?: string[];
}

export function ConfidenceBadgeBar({
  score,
  tierLevel = 'high',
  reasons = [],
  evidenceSources = [],
}: ConfidenceBadgeBarProps) {
  const [expanded, setExpanded] = useState(false);

  // 纯单色微徽标样式
  const pct = Math.round(score * 100);

  return (
    <div className="mt-3 border-t border-border/40 pt-2 text-xs text-muted-foreground">
      <div className="flex items-center justify-between">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1.5 rounded px-1.5 py-0.5 hover:bg-muted transition-colors text-foreground"
        >
          {score >= 0.6 ? (
            <ShieldCheck className="h-3.5 w-3.5 text-foreground" />
          ) : (
            <ShieldAlert className="h-3.5 w-3.5 text-amber-500" />
          )}
          <span className="font-mono font-medium">置信度: {pct}%</span>
          <span className="text-[10px] text-muted-foreground">({tierLevel.toUpperCase()})</span>
          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        </button>

        {evidenceSources.length > 0 && (
          <span className="text-[11px] text-muted-foreground flex items-center gap-1">
            <LinkIcon className="h-3 w-3" /> 引用 {evidenceSources.length} 处依据
          </span>
        )}
      </div>

      {expanded && (
        <div className="mt-2 rounded-lg border border-border bg-background p-2.5 space-y-1.5">
          {reasons.length > 0 && (
            <div>
              <div className="font-semibold text-foreground text-[11px]">置信度归因与自审项:</div>
              <ul className="list-disc list-inside space-y-0.5 mt-1 text-[11px]">
                {reasons.map((r, idx) => (
                  <li key={idx}>{r}</li>
                ))}
              </ul>
            </div>
          )}

          {evidenceSources.length > 0 && (
            <div className="pt-1 border-t border-border/40">
              <div className="font-semibold text-foreground text-[11px]">参考来源列表:</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {evidenceSources.map((src, idx) => (
                  <span
                    key={idx}
                    className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-foreground"
                  >
                    {src}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
