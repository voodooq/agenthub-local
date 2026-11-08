'use client';

import React, { useState } from 'react';
import { Check, Copy, Download, ChevronDown, ChevronRight, Code, FolderDown } from 'lucide-react';

interface CodeBlockProps {
  language: string;
  code: string;
  onSendToCanvas?: (code: string, language: string) => void;
  onSaveToWorkspace?: (code: string, language: string) => void;
}

export function CodeBlock({ language, code, onSendToCanvas, onSaveToWorkspace }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = () => {
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

  const isMermaid = language.toLowerCase() === 'mermaid';

  return (
    <div className="my-3 rounded-xl border border-white/10 bg-[#0d0f17] overflow-hidden shadow-lg group">
      <div className="flex items-center justify-between px-3.5 py-2 bg-surface-100/80 border-b border-white/5 text-xs text-slate-400">
        <div className="flex items-center space-x-2">
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="hover:text-slate-200 transition-colors p-0.5"
            title={collapsed ? '展开代码' : '折叠代码'}
          >
            {collapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
          <span className="font-mono uppercase tracking-wider font-semibold text-slate-300">
            {language || 'text'}
          </span>
          <span className="text-[11px] text-slate-500 font-mono">
            ({code.split('\n').length} 行)
          </span>
        </div>

        <div className="flex items-center space-x-1.5 opacity-80 group-hover:opacity-100 transition-opacity">
          {isMermaid && onSendToCanvas && (
            <button
              onClick={() => onSendToCanvas(code, 'mermaid')}
              className="flex items-center space-x-1 px-2 py-1 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 transition-colors"
            >
              <Code className="w-3 h-3" />
              <span>Canvas 预览</span>
            </button>
          )}

          {onSaveToWorkspace && (
            <button
              onClick={() => onSaveToWorkspace(code, language)}
              className="flex items-center space-x-1 px-2 py-1 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 transition-colors"
              title="一键落盘到当前工作区物理目录"
            >
              <FolderDown className="w-3.5 h-3.5 text-emerald-400" />
              <span>落盘到工作区</span>
            </button>
          )}

          <button
            onClick={handleCopy}
            className="flex items-center space-x-1 px-2 py-1 rounded hover:bg-white/10 text-slate-300 hover:text-white transition-colors"
            title="复制代码"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copied ? '已复制' : '复制'}</span>
          </button>

          <button
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
              {code.split('\n').map((line, idx) => (
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
