'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Search, X, MessageSquare, ArrowRight, Sparkles } from 'lucide-react';
import { SearchResult } from '@/types';
import { apiFetch } from '@/utils/apiClient';

interface SearchModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectResult: (result: SearchResult) => void;
}

export function SearchModal({ isOpen, onClose, onSelectResult }: SearchModalProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  // 独立监听 ESC 关闭事件，避免父组件高频轮询引起 onClose 引用变化时重新聚焦顶部输入框
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCloseRef.current();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen]);

  // 仅在弹窗打开/关闭切换时自动聚焦或清空检索状态
  useEffect(() => {
    if (isOpen) {
      const focusTimer = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(focusTimer);
    }
    setQuery('');
    setResults([]);
  }, [isOpen]);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }

    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const resp = await apiFetch("/api/v1/search?q=" + encodeURIComponent(query));
        if (resp.ok) {
          const data = await resp.json();
          setResults(data);
        }
      } catch (e) {
        console.error('搜索失败:', e);
      } finally {
        setLoading(false);
      }
    }, 250);

    return () => clearTimeout(timer);
  }, [query]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-20 px-4 bg-black/70 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-2xl glass-modal overflow-hidden shadow-2xl border border-border animate-scale-in text-foreground"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center px-4 py-3.5 border-b border-border bg-surface-100/80">
          <Search className="w-5 h-5 text-emerald-500 mr-3" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault();
                onClose();
              }
            }}
            placeholder="全文检索历史对话 (支持 IronShield、ALGE-TIMING、OpenSSH 等)..."
            className="flex-1 bg-transparent border-none outline-none text-foreground placeholder-muted-foreground text-sm"
          />
          {query && (
            <button onClick={() => setQuery('')} className="p-1 text-muted-foreground hover:text-foreground mr-2">
              <X className="w-4 h-4" />
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="flex items-center space-x-1 text-[11px] font-mono px-2 py-0.5 rounded bg-surface-200 hover:bg-surface-300 text-muted-foreground hover:text-foreground transition-colors cursor-pointer select-none"
            title="点击或按 ESC 键关闭"
          >
            <span>ESC 关闭</span>
            <X className="w-3 h-3 ml-0.5 opacity-70" />
          </button>
        </div>

        <div className="max-h-[460px] overflow-y-auto p-2">
          {loading ? (
            <div className="p-8 text-center text-sm text-muted-foreground">
              <span className="inline-block animate-spin mr-2">⚡</span>
              FTS5 全文索引毫秒检索中...
            </div>
          ) : results.length > 0 ? (
            <div className="space-y-1">
              {results.map((res) => (
                <button
                  key={res.message_id}
                  onClick={() => {
                    onSelectResult(res);
                    onClose();
                  }}
                  className="w-full text-left p-3 rounded-xl hover:bg-foreground/[0.05] border border-transparent hover:border-border transition-all group flex flex-col space-y-1.5"
                >
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <div className="flex items-center space-x-1.5 font-semibold text-foreground">
                      <MessageSquare className="w-3.5 h-3.5 text-emerald-500" />
                      <span>{res.conversation_title}</span>
                    </div>
                    <span className="text-[11px] text-muted-foreground font-mono">{res.created_at?.slice(0, 16)}</span>
                  </div>

                  <div
                    className="text-xs text-foreground/90 line-clamp-2 leading-relaxed"
                    dangerouslySetInnerHTML={{ __html: res.highlighted_snippet }}
                  />

                  <div className="flex items-center justify-between text-[11px] text-muted-foreground pt-1">
                    <span>来源: {res.executed_by_account || '本地记录'}</span>
                    <span className="text-emerald-500 group-hover:translate-x-1 transition-transform flex items-center space-x-0.5">
                      <span>跳转会话</span>
                      <ArrowRight className="w-3 h-3" />
                    </span>
                  </div>
                </button>
              ))}
            </div>
          ) : query.trim() ? (
            <div className="p-8 text-center text-sm text-muted-foreground">
              未找到与 &quot;{query}&quot; 相关的历史记录
            </div>
          ) : (
            <div className="p-6 text-center text-xs text-muted-foreground space-y-2">
              <Sparkles className="w-5 h-5 mx-auto text-muted-foreground" />
              <p>利用 SQLite FTS5 虚拟表 + trigram 分词引擎，支持跨账号任意历史对话毫秒级秒出。</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
