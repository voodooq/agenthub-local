'use client';

import React, { useEffect, useState } from 'react';
import { Check, Copy, Download, ChevronDown, ChevronRight, Code, FolderDown } from 'lucide-react';

interface CodeBlockProps {
  language: string;
  code: string;
  subtle?: boolean;
  onSendToCanvas?: (code: string, language: string) => void;
  onSaveToWorkspace?: (code: string, language: string) => void;
}

export function CodeBlock({ language, code, subtle = false, onSendToCanvas, onSaveToWorkspace }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const [collapsed, setCollapsed] = useState(subtle);
  const [hasUserInteracted, setHasUserInteracted] = useState(false);

  useEffect(() => {
    // 若过程状态动态切换（如步骤触发工具降级为过程步骤），且用户尚未手动展开过，则自动折叠弱化
    if (!hasUserInteracted) {
      setCollapsed(subtle);
    }
  }, [subtle, hasUserInteracted]);

  const toggleCollapsed = () => {
    setHasUserInteracted(true);
    setCollapsed((prev) => !prev);
  };

  const handleCopy = async (e?: React.MouseEvent) => {
    e?.stopPropagation();
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = (e?: React.MouseEvent) => {
    e?.stopPropagation();
    const extMap: Record<string, string> = {
      python: 'py',
      javascript: 'js',
      typescript: 'ts',
      json: 'json',
      markdown: 'md',
      html: 'html',
      css: 'css',
      sql: 'sql',
      dockerfile: 'dockerfile',
      yaml: 'yaml',
    };
    const ext = extMap[language.toLowerCase()] || 'txt';
    const blob = new Blob([code], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = "snippet_" + Date.now() + "." + ext;
    link.click();
    URL.revokeObjectURL(url);
  };

  const lines = code.split('\n');
  const isMermaid = language.toLowerCase() === 'mermaid';

  if (subtle) {
    return (
      <div className="my-1.5 rounded-lg border border-border/50 bg-surface-200/35 dark:bg-surface-200/20 overflow-hidden group transition-colors">
        <div
          role="button"
          tabIndex={0}
          onClick={toggleCollapsed}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              toggleCollapsed();
            }
          }}
          className="flex items-center justify-between px-2.5 py-1.5 bg-surface-200/40 hover:bg-surface-200/65 text-[11px] text-muted-foreground/75 transition-colors cursor-pointer select-none"
        >
          <div className="flex items-center space-x-1.5 min-w-0">
            {collapsed ? (
              <ChevronRight className="w-3 h-3 shrink-0 text-muted-foreground/60" />
            ) : (
              <ChevronDown className="w-3 h-3 shrink-0 text-muted-foreground/60" />
            )}
            <Code className="w-3 h-3 shrink-0 text-muted-foreground/55" />
            <span className="truncate">
              过程代码片段 · <span className="font-mono uppercase">{language || 'text'}</span>
            </span>
            <span className="text-[10px] text-muted-foreground/50 font-mono shrink-0">
              ({lines.length} 行)
            </span>
          </div>

          <div className="flex items-center space-x-1.5 shrink-0 ml-2">
            <span className="text-[10px] text-muted-foreground/55">
              {collapsed ? '展开查看' : '收起'}
            </span>
            <button
              type="button"
              onClick={handleCopy}
              className="p-0.5 rounded hover:bg-surface-300/60 text-muted-foreground/60 hover:text-muted-foreground opacity-60 group-hover:opacity-100 transition-opacity"
              title="复制代码"
            >
              {copied ? <Check className="w-3 h-3 text-emerald-500/80" /> : <Copy className="w-3 h-3" />}
            </button>
          </div>
        </div>

        {!collapsed && (
          <div className="px-2.5 py-2 text-[11px] font-mono leading-[1.55] overflow-x-auto max-h-56 overflow-y-auto border-t border-border/40 text-muted-foreground/80">
            <table className="w-full border-collapse">
              <tbody>
                {lines.map((line, idx) => (
                  <tr key={idx} className="hover:bg-surface-300/20">
                    <td className="w-7 select-none text-right pr-3 text-muted-foreground/40 text-[10px] font-mono">
                      {idx + 1}
                    </td>
                    <td className="whitespace-pre text-muted-foreground/80">{line || ' '}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="my-3 rounded-xl border border-white/10 bg-[#0d0f17] overflow-hidden shadow-lg group">
      <div className="flex items-center justify-between px-3.5 py-2 bg-surface-100/80 border-b border-white/5 text-xs text-slate-400">
        <div className="flex items-center space-x-2">
          <button
            type="button"
            onClick={toggleCollapsed}
            className="hover:text-slate-200 transition-colors p-0.5"
            title={collapsed ? '展开代码' : '折叠代码'}
          >
            {collapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
          <span className="font-mono uppercase tracking-wider font-semibold text-slate-300">
            {language || 'text'}
          </span>
          <span className="text-[11px] text-slate-500 font-mono">
            ({lines.length} 行)
          </span>
        </div>

        <div className="flex items-center space-x-1.5 opacity-80 group-hover:opacity-100 transition-opacity">
          {isMermaid && onSendToCanvas && (
            <button
              type="button"
              onClick={() => onSendToCanvas(code, 'mermaid')}
              className="flex items-center space-x-1 px-2 py-1 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 transition-colors"
            >
              <Code className="w-3 h-3" />
              <span>Canvas 预览</span>
            </button>
          )}

          {onSaveToWorkspace && (
            <button
              type="button"
              onClick={() => onSaveToWorkspace(code, language)}
              className="flex items-center space-x-1 px-2 py-1 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 transition-colors"
              title="一键落盘到当前工作区物理目录"
            >
              <FolderDown className="w-3.5 h-3.5 text-emerald-400" />
              <span>落盘到工作区</span>
            </button>
          )}

          <button
            type="button"
            onClick={handleCopy}
            className="flex items-center space-x-1 px-2 py-1 rounded hover:bg-white/10 text-slate-300 hover:text-white transition-colors"
            title="复制代码"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copied ? '已复制' : '复制'}</span>
          </button>

          <button
            type="button"
            onClick={handleDownload}
            className="flex items-center space-x-1 px-2 py-1 rounded hover:bg-white/10 text-slate-300 hover:text-white transition-colors"
            title="保存为本地文件"
          >
            <Download className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {!collapsed && (
        <div className="p-3 text-[13px] font-mono leading-relaxed overflow-x-auto selection:bg-emerald-500/40">
          <table className="w-full border-collapse">
            <tbody>
              {lines.map((line, idx) => (
                <tr key={idx} className="hover:bg-white/[0.02]">
                  <td className="w-8 select-none text-right pr-4 text-slate-600 text-xs font-mono">
                    {idx + 1}
                  </td>
                  <td className="text-slate-200 whitespace-pre">{line || ' '}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
