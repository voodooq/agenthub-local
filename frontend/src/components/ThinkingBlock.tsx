'use client';

import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Brain, Sparkles } from 'lucide-react';

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
    <div className="my-2.5 rounded-2xl border border-border/80 bg-surface-100/70 overflow-hidden transition-all shadow-sm max-w-full">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between px-4 py-2.5 bg-surface-200/50 hover:bg-surface-50 text-xs transition-colors select-none text-foreground"
      >
        <div className="flex items-center space-x-2">
          <div className="relative flex items-center justify-center">
            <Sparkles className={"w-3.5 h-3.5 text-blue-500 " + (isStreaming ? 'animate-spin' : '')} />
          </div>
          <span className="font-semibold text-foreground">思考过程 ({levelName})</span>
          {isStreaming && (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] bg-blue-500/15 text-blue-400 font-mono animate-pulse">
              深度推演中...
            </span>
          )}
        </div>

        <div className="flex items-center space-x-1 text-muted-foreground">
          <span className="text-[11px] font-mono">{isExpanded ? '收起' : '展开'}</span>
          {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </div>
      </button>

      {isExpanded && (
        <div className="px-4 py-3 text-xs text-muted-foreground border-t border-border/60 font-mono leading-relaxed bg-surface-200/30 max-h-72 overflow-y-auto whitespace-pre-wrap break-anywhere selection:bg-blue-500/20">
          {content}
        </div>
      )}
    </div>
  );
}
