'use client';

import React, { useState } from 'react';
import { Copy, Check, ThumbsUp, ThumbsDown, RotateCcw, MoreHorizontal, SlidersHorizontal, Download, GitBranch, BookPlus } from 'lucide-react';

interface MessageActionsProps {
  content: string;
  executedByAccount?: string | null;
  turnMetricsSummary?: string;
  onRegenerate?: () => void;
  onModifyTone?: (tone: 'shorter' | 'longer' | 'simpler' | 'professional') => void;
  onBranchFromHere?: () => void;
  onSaveToWiki?: () => void;
}

export function MessageActions({
  content,
  executedByAccount,
  turnMetricsSummary,
  onRegenerate,
  onModifyTone,
  onBranchFromHere,
  onSaveToWiki,
}: MessageActionsProps) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null);
  const [showModifyMenu, setShowModifyMenu] = useState(false);
  const [showMoreMenu, setShowMoreMenu] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownloadMarkdown = () => {
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = "gemini_response_" + Date.now() + ".md";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="relative flex items-center space-x-1 pt-1.5 text-muted-foreground select-none">
      {/* 复制 */}
      <button
        onClick={handleCopy}
        className="p-1.5 rounded-full hover:bg-surface-200 hover:text-foreground transition-colors"
        title="复制回复"
      >
        {copied ? <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
      </button>

      {/* 好评 */}
      <button
        onClick={() => setFeedback(feedback === 'up' ? null : 'up')}
        className={"p-1.5 rounded-full hover:bg-surface-200 transition-colors " + (feedback === 'up' ? 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10' : 'hover:text-foreground')}
        title="好的回答"
      >
        <ThumbsUp className="w-3.5 h-3.5" />
      </button>

      {/* 差评 */}
      <button
        onClick={() => setFeedback(feedback === 'down' ? null : 'down')}
        className={"p-1.5 rounded-full hover:bg-surface-200 transition-colors " + (feedback === 'down' ? 'text-rose-600 dark:text-rose-400 bg-rose-500/10' : 'hover:text-foreground')}
        title="差的回答"
      >
        <ThumbsDown className="w-3.5 h-3.5" />
      </button>

      {/* 重新生成 */}
      {onRegenerate && (
        <button
          onClick={onRegenerate}
          className="p-1.5 rounded-full hover:bg-surface-200 hover:text-foreground transition-colors"
          title="重新生成"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
      )}

      {/* 沉淀到 LLM Wiki */}
      {onSaveToWiki && (
        <button
          onClick={onSaveToWiki}
          className="px-2 py-1 rounded-full hover:bg-surface-200 hover:text-foreground transition-colors flex items-center gap-1 text-[11px]"
          title="存到 Wiki 待审阅"
        >
          <BookPlus className="w-3.5 h-3.5" strokeWidth={1.75} />
          <span>存到Wiki</span>
        </button>
      )}

      {/* 更多菜单（收敛语气调整、下载 Markdown、创建分支与节点信息） */}
      <div className="relative">
        <button
          onClick={() => setShowMoreMenu(!showMoreMenu)}
          className="p-1.5 rounded-full hover:bg-surface-200 hover:text-foreground transition-colors"
          title="更多操作"
        >
          <MoreHorizontal className="w-3.5 h-3.5" />
        </button>

        {showMoreMenu && (
          <div className="absolute left-0 bottom-full mb-1 w-48 rounded-xl glass-modal p-1.5 shadow-2xl border border-border z-30 animate-scale-in text-xs space-y-0.5">
            {onModifyTone && (
              <div className="relative">
                <button
                  onClick={() => setShowModifyMenu(!showModifyMenu)}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg hover:bg-surface-200 text-foreground transition-colors text-left"
                >
                  <SlidersHorizontal className="w-3.5 h-3.5 text-muted-foreground" />
                  <span>调整回答语气</span>
                </button>
                {showModifyMenu && (
                  <div className="pl-4 py-1 space-y-0.5 border-l border-border/60 ml-2">
                    <button onClick={() => { onModifyTone('shorter'); setShowModifyMenu(false); setShowMoreMenu(false); }} className="w-full text-left px-2 py-1 rounded hover:bg-surface-200 text-foreground">更简短</button>
                    <button onClick={() => { onModifyTone('longer'); setShowModifyMenu(false); setShowMoreMenu(false); }} className="w-full text-left px-2 py-1 rounded hover:bg-surface-200 text-foreground">更详尽</button>
                    <button onClick={() => { onModifyTone('simpler'); setShowModifyMenu(false); setShowMoreMenu(false); }} className="w-full text-left px-2 py-1 rounded hover:bg-surface-200 text-foreground">更通俗</button>
                    <button onClick={() => { onModifyTone('professional'); setShowModifyMenu(false); setShowMoreMenu(false); }} className="w-full text-left px-2 py-1 rounded hover:bg-surface-200 text-foreground">更专业</button>
                  </div>
                )}
              </div>
            )}

            <button
              onClick={() => { handleDownloadMarkdown(); setShowMoreMenu(false); }}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg hover:bg-surface-200 text-foreground transition-colors text-left"
            >
              <Download className="w-3.5 h-3.5 text-muted-foreground" />
              <span>下载 Markdown</span>
            </button>

            {onBranchFromHere && (
              <button
                onClick={() => { onBranchFromHere(); setShowMoreMenu(false); }}
                className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg hover:bg-emerald-500/10 hover:text-emerald-600 dark:hover:text-emerald-400 text-foreground transition-colors text-left"
              >
                <GitBranch className="w-3.5 h-3.5" />
                <span>从此处创建分支</span>
              </button>
            )}

            <div className="my-1 border-t border-border/60 pt-1.5 px-2.5 pb-1 font-mono text-[10px] text-muted-foreground space-y-0.5">
              <div className="truncate">算力: {executedByAccount || '本地调度池'}</div>
              <div>字数: {content.length} 字符</div>
              {turnMetricsSummary && (
                <div className="text-[10px] text-muted-foreground/85 break-anywhere pt-0.5 border-t border-border/40">
                  {turnMetricsSummary}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
