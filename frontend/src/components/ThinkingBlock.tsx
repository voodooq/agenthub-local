'use client';

import React, { useState } from 'react';
import { ChevronDown, ChevronRight, ChevronUp, Sparkles, BrainCircuit } from 'lucide-react';

interface ThinkingBlockProps {
  content: string;
  isStreaming?: boolean;
  thinkingLevel?: string;
}

export function ThinkingBlock({ content, isStreaming, thinkingLevel = 'high' }: ThinkingBlockProps) {
  // 默认收起，避免大量推演草稿挤占主要对话视线；支持用户一键展开查看
  const [isExpanded, setIsExpanded] = useState(false);

  if (!content) return null;

  const levelName =
    thinkingLevel === 'high' ? '深度推演' : thinkingLevel === 'medium' ? '适中思考' : '极速分析';
  const charCount = content.trim().length;

  return (
    <div className="my-1.5 rounded-xl border border-border/60 bg-surface-200/30 dark:bg-surface-200/15 overflow-hidden transition-all max-w-[94%] md:max-w-[88%] shadow-xs">
      {/* 顶部标题栏 */}
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between px-3.5 py-2 hover:bg-surface-200/50 text-xs transition-colors select-none text-muted-foreground/80 hover:text-foreground"
      >
        <div className="flex items-center space-x-2 min-w-0">
          <div className="p-1 rounded-md bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20 shrink-0">
            <BrainCircuit
              className={`w-3.5 h-3.5 ${
                isStreaming ? 'animate-pulse text-purple-500' : 'text-purple-600 dark:text-purple-400'
              }`}
            />
          </div>
          <span className="font-semibold text-foreground tracking-tight text-xs">
            思考过程 · {levelName}
          </span>
          <span className="text-[11px] text-muted-foreground/60 font-mono">
            ({charCount} 字)
          </span>
          {isStreaming && (
            <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-purple-500/15 text-purple-700 dark:text-purple-300 border border-purple-500/30 animate-pulse">
              <span className="w-1.5 h-1.5 rounded-full bg-purple-500 animate-ping mr-1"></span>
              推演中…
            </span>
          )}
        </div>

        <div className="flex items-center space-x-1 text-muted-foreground shrink-0 ml-2">
          <span className="text-[11px] font-medium">{isExpanded ? '收起' : '展开'}</span>
          {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </div>
      </button>

      {/* 展开内容区 */}
      {isExpanded && (
        <div className="border-t border-border/50 bg-black/5 dark:bg-black/35 px-4 py-3">
          <div className="pl-3.5 pr-1 border-l-2 border-purple-500/50 dark:border-purple-400/40 text-[12px] leading-relaxed font-sans text-muted-foreground dark:text-zinc-300 max-h-64 overflow-y-auto whitespace-pre-wrap break-anywhere selection:bg-purple-500/25">
            {content}
          </div>
          <div className="mt-2.5 pt-1.5 border-t border-border/30 flex justify-end">
            <button
              type="button"
              onClick={() => setIsExpanded(false)}
              className="text-[11px] text-muted-foreground hover:text-foreground inline-flex items-center space-x-1 transition-colors"
            >
              <ChevronUp className="w-3 h-3" />
              <span>收起思考过程</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
