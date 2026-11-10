'use client';

import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Sparkles } from 'lucide-react';

interface ThinkingBlockProps {
  content: string;
  isStreaming?: boolean;
  thinkingLevel?: string;
}

export function ThinkingBlock({ content, isStreaming, thinkingLevel = 'high' }: ThinkingBlockProps) {
  // 默认收起：大幅降低推理草稿或内部代码片段对主阅读视线的视觉干扰
  const [isExpanded, setIsExpanded] = useState(false);

  if (!content) return null;

  const levelName = thinkingLevel === 'high' ? '深度推理' : thinkingLevel === 'medium' ? '适中思考' : '极速分析';
  const charCount = content.trim().length;

  return (
    <div className="my-1 rounded-xl border border-border/45 bg-surface-200/20 dark:bg-surface-200/15 overflow-hidden transition-colors max-w-full">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between px-3 py-1.5 hover:bg-surface-200/40 text-[11px] transition-colors select-none text-muted-foreground/75 hover:text-muted-foreground"
      >
        <div className="flex items-center space-x-1.5 min-w-0">
          <Sparkles
            className={
              'w-3 h-3 shrink-0 ' +
              (isStreaming ? 'text-emerald-500/70 animate-pulse' : 'text-muted-foreground/60')
            }
          />
          <span className="font-normal tracking-tight">思考过程 · {levelName}</span>
          <span className="text-[10px] text-muted-foreground/50 font-mono">
            ({charCount} 字)
          </span>
          {isStreaming && (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] bg-surface-200/70 text-muted-foreground/75 animate-pulse">
              推演中…
            </span>
          )}
        </div>

        <div className="flex items-center space-x-1 text-muted-foreground/60 shrink-0 ml-2">
          <span className="text-[10px]">{isExpanded ? '收起' : '展开'}</span>
          {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        </div>
      </button>

      {isExpanded && (
        <div className="mx-3 mb-2.5 mt-0.5 pl-3 pr-1.5 py-1.5 border-l-2 border-border/55 text-[11px] leading-[1.6] font-sans text-muted-foreground/65 dark:text-muted-foreground/55 max-h-48 overflow-y-auto whitespace-pre-wrap break-anywhere selection:bg-surface-300/40">
          {content}
        </div>
      )}
    </div>
  );
}
