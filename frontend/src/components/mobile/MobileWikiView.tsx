'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowLeft,
  BookOpen,
  Check,
  ChevronRight,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { apiFetch } from '@/utils/api';

interface WikiSpace {
  wiki_space_id: string;
  name: string;
  published_count: number;
  inbox_count: number;
}

interface WikiPageItem {
  page_id: string;
  wiki_space_id: string;
  slug: string;
  title: string;
  category_path: string;
  authority_level: string;
  status: string;
  summary_text?: string;
  updated_at?: number;
}

interface WikiPageDetail extends WikiPageItem {
  markdown_content: string;
  pending_diff_markdown?: string | null;
}

interface WikiPreviewData {
  page_id: string;
  slug: string;
  title: string;
  authority_level: string;
  summary_text: string;
}

interface MobileWikiViewProps {
  onInboxCountChange?: (totalInbox: number) => void;
}

function getAuthorityLabel(level: string): { text: string; cls: string } {
  if (level === 'official') {
    return {
      text: '官方基准',
      cls: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
    };
  }
  if (level === 'decision') {
    return {
      text: '收藏笔记',
      cls: 'bg-sky-500/15 text-sky-600 dark:text-sky-400',
    };
  }
  return {
    text: '数据快照',
    cls: 'bg-surface-2 text-muted-foreground',
  };
}

/**
 * 移动端口袋 Wiki 视图
 * 采用圆角胶囊搜索、iOS 风格分段切换器、宽松卡片与沉浸式正文阅读体验
 */
export function MobileWikiView({ onInboxCountChange }: MobileWikiViewProps) {
  const [spaces, setSpaces] = useState<WikiSpace[]>([]);
  const [activeSpaceId, setActiveSpaceId] = useState<string>('');
  const [activeTab, setActiveTab] = useState<'published' | 'inbox'>('published');
  const [searchQuery, setSearchQuery] = useState('');
  const [pages, setPages] = useState<WikiPageItem[]>([]);
  const [loading, setLoading] = useState(false);

  const [readingPage, setReadingPage] = useState<WikiPageDetail | null>(null);
  const [previewCard, setPreviewCard] = useState<WikiPreviewData | null>(null);
  const [actionBusyId, setActionBusyId] = useState<string | null>(null);

  const loadSpaces = useCallback(async () => {
    try {
      const res = await apiFetch('/api/v1/wiki/spaces');
      if (!res.ok) return;
      const payload = await res.json();
      const list: WikiSpace[] = payload?.data || [];
      setSpaces(list);
      const totalInbox = list.reduce((acc, s) => acc + (s.inbox_count || 0), 0);
      onInboxCountChange?.(totalInbox);
      if (list.length > 0) {
        setActiveSpaceId((prev) => prev || list[0].wiki_space_id);
      }
    } catch {
      // 忽略网络瞬断
    }
  }, [onInboxCountChange]);

  const loadPages = useCallback(async () => {
    if (!activeSpaceId) return;
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set('status', activeTab);
      if (searchQuery.trim()) params.set('q', searchQuery.trim());
      const res = await apiFetch(
        `/api/v1/wiki/spaces/${encodeURIComponent(activeSpaceId)}/pages?${params.toString()}`
      );
      if (!res.ok) return;
      const payload = await res.json();
      setPages(payload?.data || []);
    } finally {
      setLoading(false);
    }
  }, [activeSpaceId, activeTab, searchQuery]);

  useEffect(() => {
    void loadSpaces();
  }, [loadSpaces]);

  useEffect(() => {
    void loadPages();
  }, [loadPages]);

  const openPageDetail = async (pageId: string) => {
    const res = await apiFetch(`/api/v1/wiki/pages/${encodeURIComponent(pageId)}`);
    if (!res.ok) return;
    const payload = await res.json();
    if (payload?.data) {
      setReadingPage(payload.data);
    }
  };

  const handleInboxAction = async (pageId: string, action: 'approve' | 'discard') => {
    setActionBusyId(pageId);
    try {
      const res = await apiFetch(`/api/v1/wiki/inbox/${encodeURIComponent(pageId)}/${action}`, {
        method: 'POST',
      });
      if (res.ok) {
        if (readingPage?.page_id === pageId) setReadingPage(null);
        await loadSpaces();
        await loadPages();
      }
    } finally {
      setActionBusyId(null);
    }
  };

  const handleOpenWikiLink = async (slug: string) => {
    if (!activeSpaceId || !slug) return;
    const res = await apiFetch(
      `/api/v1/wiki/spaces/${encodeURIComponent(activeSpaceId)}/preview?slug=${encodeURIComponent(slug)}`
    );
    if (!res.ok) return;
    const payload = await res.json();
    if (payload?.data) {
      setPreviewCard(payload.data);
    }
  };

  const renderMarkdownWithLinks = (content: string) => {
    const lines = content.split('\n');
    return lines.map((line, lineIdx) => {
      const parts: React.ReactNode[] = [];
      const regex = /\[\[([^\]]+)\]\]|\[([^\]]+)\]\(wikilink:\/\/([^)]+)\)/g;
      let lastIndex = 0;
      let match: RegExpExecArray | null;

      while ((match = regex.exec(line)) !== null) {
        if (match.index > lastIndex) {
          parts.push(line.slice(lastIndex, match.index));
        }
        const label = match[1] || match[2] || '';
        const slug = (match[3] || match[1] || '').trim();
        parts.push(
          <button
            key={`${lineIdx}-${match.index}`}
            type="button"
            onClick={() => void handleOpenWikiLink(slug)}
            className="mx-0.5 inline font-medium text-emerald-600 underline decoration-emerald-500/40 underline-offset-2 dark:text-emerald-400"
          >
            {label}
          </button>
        );
        lastIndex = regex.lastIndex;
      }

      if (lastIndex < line.length) {
        parts.push(line.slice(lastIndex));
      }

      if (line.startsWith('# ')) {
        return (
          <h2 key={lineIdx} className="mt-4 mb-2 text-lg font-semibold text-foreground">
            {line.slice(2)}
          </h2>
        );
      }
      if (line.startsWith('## ')) {
        return (
          <h3 key={lineIdx} className="mt-3.5 mb-1.5 text-base font-semibold text-foreground">
            {line.slice(3)}
          </h3>
        );
      }
      if (!line.trim()) {
        return <div key={lineIdx} className="h-2.5" />;
      }
      return (
        <p key={lineIdx} className="text-[15px] leading-[1.7] text-foreground/95">
          {parts.length > 0 ? parts : line}
        </p>
      );
    });
  };

  const activeSpace = spaces.find((s) => s.wiki_space_id === activeSpaceId);

  if (readingPage) {
    const badge = getAuthorityLabel(readingPage.authority_level);
    return (
      <div className="flex h-full flex-col bg-background">
        <div className="flex h-13 shrink-0 items-center justify-between border-b border-border/60 px-3 py-2">
          <button
            type="button"
            onClick={() => setReadingPage(null)}
            className="flex h-10 items-center gap-1.5 rounded-full px-3 text-sm font-medium text-foreground active:bg-surface-1"
          >
            <ArrowLeft className="h-4 w-4" strokeWidth={1.75} />
            <span>返回列表</span>
          </button>
          <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium ${badge.cls}`}>
            {badge.text}
          </span>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          <h1 className="text-xl font-bold leading-snug text-foreground">{readingPage.title}</h1>
          {readingPage.category_path && (
            <p className="mt-1.5 text-xs text-muted-foreground">{readingPage.category_path}</p>
          )}

          {readingPage.status === 'inbox' && readingPage.pending_diff_markdown && (
            <div className="mt-4 rounded-2xl border border-amber-500/40 bg-amber-500/10 p-4">
              <p className="mb-1.5 text-xs font-semibold text-amber-600 dark:text-amber-400">
                待合并增量内容
              </p>
              <div className="space-y-1.5 text-sm text-foreground">
                {renderMarkdownWithLinks(readingPage.pending_diff_markdown)}
              </div>
            </div>
          )}

          <div className="mt-5 space-y-1.5">
            {renderMarkdownWithLinks(readingPage.markdown_content || '')}
          </div>
        </div>

        {readingPage.status === 'inbox' && (
          <div className="flex shrink-0 gap-3 border-t border-border/60 bg-background px-4 py-3">
            <button
              type="button"
              disabled={actionBusyId === readingPage.page_id}
              onClick={() => void handleInboxAction(readingPage.page_id, 'discard')}
              className="flex h-11 flex-1 items-center justify-center gap-1.5 rounded-xl border border-border bg-surface-1 text-sm font-medium text-foreground active:scale-[0.98]"
            >
              <Trash2 className="h-4 w-4" strokeWidth={1.75} />
              <span>丢弃</span>
            </button>
            <button
              type="button"
              disabled={actionBusyId === readingPage.page_id}
              onClick={() => void handleInboxAction(readingPage.page_id, 'approve')}
              className="flex h-11 flex-1 items-center justify-center gap-1.5 rounded-xl bg-emerald-600 text-sm font-medium text-white shadow-sm active:scale-[0.98]"
            >
              <Check className="h-4 w-4" strokeWidth={1.75} />
              <span>批准归档</span>
            </button>
          </div>
        )}

        {previewCard && (
          <div
            className="fixed inset-0 z-50 flex flex-col justify-end bg-black/50 backdrop-blur-[1px]"
            onClick={() => setPreviewCard(null)}
          >
            <div
              className="rounded-t-3xl border-t border-border bg-background px-5 pb-6 pt-3 shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-border" />
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h4 className="text-base font-semibold text-foreground">{previewCard.title}</h4>
                  <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                    {previewCard.summary_text || '暂无摘要内容'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setPreviewCard(null)}
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-muted-foreground active:bg-surface-1"
                >
                  <X className="h-5 w-5" strokeWidth={1.75} />
                </button>
              </div>
              <button
                type="button"
                onClick={() => {
                  const id = previewCard.page_id;
                  setPreviewCard(null);
                  void openPageDetail(id);
                }}
                className="mt-4 h-11 w-full rounded-xl bg-surface-1 text-sm font-medium text-foreground active:scale-[0.98]"
              >
                打开完整词条
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-background">
      {/* 空间选择 + 圆角胶囊搜索 + 分段器 */}
      <div className="shrink-0 space-y-3 border-b border-border/60 px-4 py-3">
        {spaces.length > 1 && (
          <div className="flex gap-2 overflow-x-auto pb-0.5">
            {spaces.map((sp) => (
              <button
                key={sp.wiki_space_id}
                type="button"
                onClick={() => setActiveSpaceId(sp.wiki_space_id)}
                className={`shrink-0 rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors ${
                  activeSpaceId === sp.wiki_space_id
                    ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                    : 'bg-surface-1 text-muted-foreground'
                }`}
              >
                {sp.name}
              </button>
            ))}
          </div>
        )}

        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            strokeWidth={1.75}
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索收藏词条或关键词..."
            className="h-10 w-full rounded-full border border-border/70 bg-surface-1 pl-10 pr-4 text-sm text-foreground placeholder:text-muted-foreground focus:border-emerald-500 focus:outline-none"
          />
        </div>

        <div className="grid grid-cols-2 gap-1 rounded-xl bg-surface-1 p-1">
          <button
            type="button"
            onClick={() => setActiveTab('published')}
            className={`h-9 rounded-lg text-xs font-semibold transition-all ${
              activeTab === 'published'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground'
            }`}
          >
            全部词条 ({activeSpace?.published_count ?? 0})
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('inbox')}
            className={`h-9 rounded-lg text-xs font-semibold tabular-nums transition-all ${
              activeTab === 'inbox'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground'
            }`}
          >
            待审阅 ({activeSpace?.inbox_count ?? 0})
          </button>
        </div>
      </div>

      {/* 词条卡片列表 */}
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {loading ? (
          <p className="py-12 text-center text-sm text-muted-foreground">正在加载词条...</p>
        ) : pages.length === 0 ? (
          <div className="py-16 text-center">
            <BookOpen className="mx-auto mb-3 h-8 w-8 text-muted-foreground/70" strokeWidth={1.5} />
            <p className="text-sm text-muted-foreground">
              {activeTab === 'inbox' ? '暂无待审阅的新词条' : '还没有收藏的词条'}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {pages.map((page) => {
              const badge = getAuthorityLabel(page.authority_level);
              return (
                <div
                  key={page.page_id}
                  className="rounded-2xl border border-border/70 bg-surface-1 p-4 shadow-sm"
                >
                  <button
                    type="button"
                    onClick={() => void openPageDetail(page.page_id)}
                    className="flex w-full items-start justify-between gap-3 text-left active:opacity-80"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span
                          className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${badge.cls}`}
                        >
                          {badge.text}
                        </span>
                        <h3 className="truncate text-[15px] font-semibold text-foreground">
                          {page.title}
                        </h3>
                      </div>
                      {page.summary_text && (
                        <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                          {page.summary_text}
                        </p>
                      )}
                    </div>
                    <ChevronRight
                      className="mt-1 h-4 w-4 shrink-0 text-muted-foreground"
                      strokeWidth={1.75}
                    />
                  </button>

                  {activeTab === 'inbox' && (
                    <div className="mt-3 flex gap-2.5 border-t border-border/60 pt-3">
                      <button
                        type="button"
                        disabled={actionBusyId === page.page_id}
                        onClick={() => void handleInboxAction(page.page_id, 'discard')}
                        className="flex h-10 flex-1 items-center justify-center gap-1.5 rounded-xl bg-background text-xs font-medium text-muted-foreground active:scale-[0.98]"
                      >
                        <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
                        <span>丢弃</span>
                      </button>
                      <button
                        type="button"
                        disabled={actionBusyId === page.page_id}
                        onClick={() => void handleInboxAction(page.page_id, 'approve')}
                        className="flex h-10 flex-1 items-center justify-center gap-1.5 rounded-xl bg-emerald-600 text-xs font-semibold text-white active:scale-[0.98]"
                      >
                        <Check className="h-3.5 w-3.5" strokeWidth={1.75} />
                        <span>批准归档</span>
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
