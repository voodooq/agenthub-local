'use client';

import React, { useState } from 'react';
import { X, Copy, Sparkles } from 'lucide-react';

interface CanvasDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  content: string;
  type: 'html' | 'mermaid' | 'code' | 'image';
}

export function CanvasDrawer({ isOpen, onClose, title = 'Artifacts 架构画布', content, type }: CanvasDrawerProps) {
  const [copied, setCopied] = useState(false);

  if (!isOpen) return null;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <aside className="fixed inset-y-0 right-0 w-full md:w-[480px] lg:w-[580px] bg-[#0c0e16] border-l border-white/10 shadow-2xl z-40 flex flex-col animate-fade-in">
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 bg-surface-100">
        <div className="flex items-center space-x-2">
          <Sparkles className="w-4 h-4 text-blue-400" />
          <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-slate-400 uppercase font-mono">
            {type}
          </span>
        </div>

        <div className="flex items-center space-x-2">
          <button
            onClick={handleCopy}
            className="p-1.5 rounded hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
            title="复制源码"
          >
            <Copy className="w-4 h-4" />
          </button>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
            title="关闭画布"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-4 bg-[#08090e]">
        {type === 'image' ? (
          <div className="w-full h-full flex flex-col items-center justify-center p-2">
            <img
              src={content}
              alt={title}
              className="max-w-full max-h-[80vh] rounded-xl object-contain shadow-2xl border border-white/10"
            />
            <a
              href={content}
              target="_blank"
              rel="noreferrer"
              className="mt-4 text-xs text-blue-400 hover:text-blue-300 underline"
            >
              在外部浏览器中查看原始大图 ↗
            </a>
          </div>
        ) : type === 'html' ? (
          <iframe
            srcDoc={content}
            sandbox="allow-scripts"
            className="w-full h-full rounded-lg bg-white shadow-inner"
            title="HTML Preview"
          />
        ) : (
          <div className="rounded-xl border border-white/10 bg-[#10121a] p-4 text-xs font-mono text-slate-300 whitespace-pre-wrap overflow-x-auto">
            {content}
          </div>
        )}
      </div>
    </aside>
  );
}
