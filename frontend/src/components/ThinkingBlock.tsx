'use client';

import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Sparkles } from 'lucide-react';

interface ThinkingBlockProps {
  content: string;
  isStreaming?: boolean;
  thinkingLevel?: string;
}

export function ThinkingBlock({ content, isStreaming, thinkingLevel = 'high' }: ThinkingBlockProps) {
  const [isExpanded, setIsExpanded] = useState(true);

  if (!content) return null;

  const levelName = thinkingLevel === 'high' ? '深度推理' : thinkingLevel === 'medium' ? '适中思考' : '极速分析';

  return (
    <div className="my-2 rounded-2xl border border-emerald-500/20 dark:border-emerald-500/20 bg-emerald-500/[0.03] dark:bg-emerald-950/20 overflow-hidden transition-all shadow-sm max-w-full">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between px-3.5 py-2 hover:bg-emerald-500/5 text-xs transition-colors select-none text-foreground"
      >
        <div className="flex items-center space-x-2">
          <div className="relative flex items-center justify-center">
            <Sparkles className={"w-3.5 h-3.5 text-emerald-500 " + (isStreaming ? 'animate-spin' : '')} />
          </div>
          <span className="font-medium text-foreground/90">思考过程 ({levelName})</span>
          {isStreaming && (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-mono animate-pulse">
              深度推演中...
            </span>
          )}
        </div>

        <div className="flex items-center space-x-1 text-muted-foreground/80">
          <span className="text-[11px] font-mono">{isExpanded ? '收起' : '展开'}</span>
          {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </div>
      </button>

      {isExpanded && (
        <div className="px-4 py-3 text-xs text-muted-foreground/90 border-t border-emerald-500/15 font-mono leading-relaxed bg-black/[0.02] dark:bg-black/20 max-h-72 overflow-y-auto whitespace-pre-wrap break-anywhere selection:bg-emerald-500/20">
          {content}
        </div>
      )}
    </div>
  );
}
