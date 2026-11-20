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
    <div className="my-1.5 max-w-[94%] md:max-w-[88%]">
      {/* Gemini 风格轻量单行触发条 */}
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="group inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-surface-200/60 hover:text-foreground select-none"
      >
        <Sparkles
          className={`h-3.5 w-3.5 shrink-0 text-emerald-500 ${
            isStreaming ? 'gemini-sparkle-pulse' : ''
          }`}
          strokeWidth={1.85}
        />
        <span className="font-medium tracking-tight">
          {isStreaming ? `正在深入思考 · ${levelName}` : `思考过程 · ${levelName}`}
        </span>
        <span className="text-[11px] text-muted-foreground/60 font-mono tabular-nums">
          {charCount} 字
        </span>
        {isExpanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground/70 transition-transform" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/70 transition-transform group-hover:translate-x-0.5" />
        )}
      </button>

      {/* 平滑手风琴展开区 */}
      <div className="gemini-accordion-content" data-open={isExpanded ? 'true' : 'false'}>
        <div className="gemini-accordion-inner">
          <div className="mt-1.5 rounded-xl border border-border/50 bg-surface-200/35 px-4 py-3 dark:bg-black/25">
            <div className="pl-3 pr-1 border-l-2 border-emerald-500/45 text-[12px] leading-relaxed font-sans text-muted-foreground dark:text-zinc-300 max-h-64 overflow-y-auto whitespace-pre-wrap break-anywhere selection:bg-emerald-500/20">
              {content}
            </div>
            <div className="mt-2 pt-1.5 border-t border-border/30 flex justify-end">
              <button
                type="button"
                onClick={() => setIsExpanded(false)}
                className="text-[11px] text-muted-foreground hover:text-foreground inline-flex items-center space-x-1 transition-colors"
              >
                <ChevronUp className="w-3 h-3" />
                <span>收起思路</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
