'use client';

import React, { useState } from 'react';
import { Copy, Check, ThumbsUp, ThumbsDown, RotateCcw, MoreHorizontal, SlidersHorizontal, Download, GitBranch } from 'lucide-react';

interface MessageActionsProps {
  content: string;
  executedByAccount?: string | null;
  onRegenerate?: () => void;
  onModifyTone?: (tone: 'shorter' | 'longer' | 'simpler' | 'professional') => void;
  onBranchFromHere?: () => void;
}

export function MessageActions({
  content,
  executedByAccount,
  onRegenerate,
  onModifyTone,
  onBranchFromHere,
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
    <div className="relative flex items-center space-x-1 pt-1.5 text-slate-400 select-none">
      {/* 复制 */}
      <button
        onClick={handleCopy}
        className="p-1.5 rounded-full hover:bg-white/10 hover:text-slate-200 transition-colors"
        title="复制回复"
      >
        {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
      </button>

      {/* 好评 */}
      <button
        onClick={() => setFeedback(feedback === 'up' ? null : 'up')}
        className={"p-1.5 rounded-full hover:bg-surface-200 transition-colors " + (feedback === 'up' ? 'text-emerald-500 bg-emerald-500/10' : 'hover:text-foreground')}
        title="好的回答"
      >
        <ThumbsUp className="w-3.5 h-3.5" />
      </button>

      {/* 差评 */}
      <button
        onClick={() => setFeedback(feedback === 'down' ? null : 'down')}
        className={"p-1.5 rounded-full hover:bg-white/10 transition-colors " + (feedback === 'down' ? 'text-rose-400 bg-rose-500/10' : 'hover:text-slate-200')}
        title="差的回答"
      >
        <ThumbsDown className="w-3.5 h-3.5" />
      </button>

      {/* 修改回复风格 */}
      {onModifyTone && (
        <div className="relative">
          <button
            onClick={() => setShowModifyMenu(!showModifyMenu)}
            className="p-1.5 rounded-full hover:bg-white/10 hover:text-slate-200 transition-colors"
            title="修改回复"
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
          </button>

          {showModifyMenu && (
            <div className="absolute left-0 bottom-full mb-1 w-32 rounded-xl glass-modal p-1 shadow-2xl border border-white/10 z-30 animate-scale-in text-xs">
              <button
                onClick={() => { onModifyTone('shorter'); setShowModifyMenu(false); }}
                className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-white/10 text-slate-300 hover:text-white transition-colors"
              >
                更简短
              </button>
              <button
                onClick={() => { onModifyTone('longer'); setShowModifyMenu(false); }}
                className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-white/10 text-slate-300 hover:text-white transition-colors"
              >
                更详尽
              </button>
              <button
                onClick={() => { onModifyTone('simpler'); setShowModifyMenu(false); }}
                className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-white/10 text-slate-300 hover:text-white transition-colors"
              >
                更通俗
              </button>
              <button
                onClick={() => { onModifyTone('professional'); setShowModifyMenu(false); }}
                className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-white/10 text-slate-300 hover:text-white transition-colors"
              >
                更专业
              </button>
            </div>
          )}
        </div>
      )}

      {/* 重新生成 */}
      {onRegenerate && (
        <button
          onClick={onRegenerate}
          className="p-1.5 rounded-full hover:bg-white/10 hover:text-slate-200 transition-colors"
          title="重新生成"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
      )}

      {/* 导出 Markdown */}
      <button
        onClick={handleDownloadMarkdown}
        className="p-1.5 rounded-full hover:bg-white/10 hover:text-slate-200 transition-colors"
        title="下载为 Markdown 文件"
      >
        <Download className="w-3.5 h-3.5" />
      </button>

      {/* 从此处创建多版本分支 */}
      {onBranchFromHere && (
        <button
          onClick={onBranchFromHere}
          className="p-1.5 rounded-full hover:bg-emerald-500/15 hover:text-emerald-500 transition-colors"
          title="从此处创建分支会话（保留截至当前轮次的上下文）"
        >
          <GitBranch className="w-3.5 h-3.5" />
        </button>
      )}

      {/* 更多信息 */}
      <div className="relative">
        <button
          onClick={() => setShowMoreMenu(!showMoreMenu)}
          className="p-1.5 rounded-full hover:bg-white/10 hover:text-slate-200 transition-colors"
          title="查看更多"
        >
          <MoreHorizontal className="w-3.5 h-3.5" />
        </button>

        {showMoreMenu && (
          <div className="absolute left-0 bottom-full mb-1 w-48 rounded-xl glass-modal p-2 shadow-2xl border border-white/10 z-30 animate-scale-in text-xs font-mono">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">执行节点信息</div>
            <div className="text-slate-300 truncate">算力: {executedByAccount || '本地调度池'}</div>
            <div className="text-slate-500 text-[10px] mt-1">字数: {content.length} 字符</div>
          </div>
        )}
      </div>
    </div>
  );
}
