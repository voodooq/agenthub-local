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

  const [imageError, setImageError] = useState(false);

  if (!isOpen) return null;

  const fullUrl =
    content.startsWith('http') || content.startsWith('data:')
      ? content
      : typeof window !== 'undefined'
      ? `${window.location.origin}${content}`
      : content;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(fullUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <aside className="fixed inset-y-0 right-0 w-full md:w-[480px] lg:w-[580px] bg-[#0c0e16] border-l border-white/10 shadow-2xl z-40 flex flex-col animate-fade-in">
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 bg-surface-100">
        <div className="flex items-center space-x-2">
          <Sparkles className="w-4 h-4 text-blue-400" />
          <h3 className="text-sm font-semibold text-slate-100 truncate max-w-[320px]">{title}</h3>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-slate-400 uppercase font-mono shrink-0">
            {type}
          </span>
        </div>

        <div className="flex items-center space-x-2">
          <button
            onClick={handleCopy}
            className="p-1.5 rounded hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
            title={type === 'image' ? '复制图片链接' : '复制源码'}
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
            {!imageError ? (
              <img
                src={content}
                alt={title}
                onError={() => setImageError(true)}
                className="max-w-full max-h-[80vh] rounded-xl object-contain shadow-2xl border border-white/10"
              />
            ) : (
              <div className="p-8 text-center space-y-3 rounded-2xl bg-surface-200/40 border border-border/80">
                <div className="text-sm text-rose-400 font-semibold">图片暂时无法加载</div>
                <div className="text-xs text-slate-400 max-w-sm">
                  请检查本地后端是否正在运行，或尝试在浏览器新标签页中打开。
                </div>
                <button
                  type="button"
                  onClick={() => setImageError(false)}
                  className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs"
                >
                  重试加载
                </button>
              </div>
            )}
            <a
              href={fullUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-4 text-xs text-blue-400 hover:text-blue-300 underline flex items-center space-x-1"
            >
              <span>在外部浏览器中查看原始大图 ↗</span>
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
