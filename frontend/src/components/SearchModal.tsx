'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Search, X, MessageSquare, ArrowRight, Sparkles } from 'lucide-react';
import { SearchResult } from '@/types';
import { apiFetch } from '@/utils/apiClient';

interface SearchModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectResult: (conversationId: string) => void;
}

export function SearchModal({ isOpen, onClose, onSelectResult }: SearchModalProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    } else {
      setQuery('');
      setResults([]);
    }
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
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-20 px-4 bg-black/70 backdrop-blur-sm animate-fade-in">
      <div
        className="w-full max-w-2xl rounded-2xl glass-modal overflow-hidden shadow-2xl border border-white/10 animate-scale-in"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center px-4 py-3.5 border-b border-white/10 bg-surface-100/50">
          <Search className="w-5 h-5 text-emerald-500 mr-3" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="全文检索历史对话 (支持 IronShield、ALGE-TIMING、OpenSSH 等)..."
            className="flex-1 bg-transparent border-none outline-none text-slate-100 placeholder-slate-500 text-sm"
          />
          {query && (
            <button onClick={() => setQuery('')} className="p-1 text-slate-500 hover:text-slate-300 mr-2">
              <X className="w-4 h-4" />
            </button>
          )}
          <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-white/10 text-slate-400">
            ESC 关闭
          </span>
        </div>

        <div className="max-h-[460px] overflow-y-auto p-2">
          {loading ? (
            <div className="p-8 text-center text-sm text-slate-400">
              <span className="inline-block animate-spin mr-2">⚡</span>
              FTS5 全文索引毫秒检索中...
            </div>
          ) : results.length > 0 ? (
            <div className="space-y-1">
              {results.map((res) => (
                <button
                  key={res.message_id}
                  onClick={() => {
                    onSelectResult(res.conversation_id);
                    onClose();
                  }}
                  className="w-full text-left p-3 rounded-xl hover:bg-white/[0.06] border border-transparent hover:border-white/10 transition-all group flex flex-col space-y-1.5"
                >
                  <div className="flex items-center justify-between text-xs text-slate-400">
                    <div className="flex items-center space-x-1.5 font-medium text-slate-300">
                      <MessageSquare className="w-3.5 h-3.5 text-emerald-500" />
                      <span>{res.conversation_title}</span>
                    </div>
                    <span className="text-[11px] text-slate-500 font-mono">{res.created_at?.slice(0, 16)}</span>
                  </div>

                  <div
                    className="text-xs text-slate-300 line-clamp-2 leading-relaxed"
                    dangerouslySetInnerHTML={{ __html: res.highlighted_snippet }}
                  />

                  <div className="flex items-center justify-between text-[11px] text-slate-500 pt-1">
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
            <div className="p-8 text-center text-sm text-slate-500">
              未找到与 &quot;{query}&quot; 相关的历史记录
            </div>
          ) : (
            <div className="p-6 text-center text-xs text-slate-500 space-y-2">
              <Sparkles className="w-5 h-5 mx-auto text-slate-600" />
              <p>利用 SQLite FTS5 虚拟表 + trigram 分词引擎，支持跨账号任意历史对话毫秒级秒出。</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
