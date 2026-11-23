'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Edit3,
  Eye,
  FilePlus,
  FileText,
  Folder,
  GitFork,
  Inbox,
  Link2,
  Plus,
  Search,
  Trash2,
  Upload,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface WikiSpace {
  wiki_space_id: string;
  name: string;
  description: string;
  default_kb_id: string;
  published_count: number;
  inbox_count: number;
}

interface WikiPageSummary {
  page_id: string;
  wiki_space_id: string;
  kb_id: string;
  slug: string;
  title: string;
  category_path: string;
  authority_level: 'official' | 'decision' | 'data_snapshot';
  review_status: 'inbox' | 'published' | 'discarded';
  effective_date: string;
  summary_text: string;
  pending_diff_markdown?: string;
  updated_at: string;
}

interface WikiPageDetail extends WikiPageSummary {
  markdown_content: string;
  outgoing_links: Array<{ target_slug: string; context_snippet: string; target_page_id?: string }>;
  backlinks: Array<{ source_page_id: string; source_slug: string; source_title: string; context_snippet: string }>;
  source_refs: Array<{ type: string; ref_id: string; title: string; timestamp: string }>;
  local_graph: {
    nodes: Array<{ id: string; slug: string; title: string; authority_level: string; is_center: boolean }>;
    edges: Array<{ source: string; target: string }>;
  };
}

interface HoverPreviewState {
  slug: string;
  x: number;
  y: number;
  loading: boolean;
  data?: {
    page_id?: string;
    title: string;
    authority_level: string;
    updated_at: string;
    summary_text: string;
  };
}

const AUTHORITY_LABEL: Record<string, string> = {
  official: '[官方基准]',
  decision: '[会议决策]',
  data_snapshot: '[数据快照]',
};

/**
 * 预处理正文中的 [[词条名]]，转换为内部伪协议链接供 ReactMarkdown 自定义渲染。
 */
function preprocessWikiLinks(md: string): string {
  return (md || '').replace(/\[\[([^\[\]\n]+)\]\]/g, (_m, slug) => {
    const clean = String(slug).trim();
    return `[${clean}](wikilink://${encodeURIComponent(clean)})`;
  });
}

/**
 * 桌面端三栏式 LLM Wiki 工作台
 * 左栏：Inbox 审阅入口 + 分类目录树；中栏：阅读/编辑双模 + [[WikiLink]] 悬停预览；右栏：局部图谱与溯源时间轴。
 */
export function WikiWorkspaceView() {
  const [spaces, setSpaces] = useState<WikiSpace[]>([]);
  const [activeSpaceId, setActiveSpaceId] = useState<string>('');
  const [pages, setPages] = useState<WikiPageSummary[]>([]);
  const [leftTab, setLeftTab] = useState<'tree' | 'inbox'>('tree');
  const [searchKeyword, setSearchKeyword] = useState('');
  const [collapsedDirs, setCollapsedDirs] = useState<Record<string, boolean>>({});

  const [selectedPage, setSelectedPage] = useState<WikiPageDetail | null>(null);
  const [editorMode, setEditorMode] = useState<'read' | 'edit'>('read');
  const [draftMarkdown, setDraftMarkdown] = useState('');
  const [draftTitle, setDraftTitle] = useState('');
  const [savingPage, setSavingPage] = useState(false);

  const [rightTab, setRightTab] = useState<'graph' | 'timeline'>('graph');
  const [hoverPreview, setHoverPreview] = useState<HoverPreviewState | null>(null);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 新建空间 / 导入会议纪要弹窗状态
  const [showNewSpace, setShowNewSpace] = useState(false);
  const [newSpaceName, setNewSpaceName] = useState('');
  const [showIngestModal, setShowIngestModal] = useState(false);
  const [ingestType, setIngestType] = useState<'meeting_note' | 'kb_doc'>('meeting_note');
  const [ingestTitle, setIngestTitle] = useState('');
  const [ingestContent, setIngestContent] = useState('');
  const [ingesting, setIngesting] = useState(false);
  const [showSplitImportModal, setShowSplitImportModal] = useState(false);
  const [splitFilename, setSplitFilename] = useState('ontology_guide.md');
  const [splitContent, setSplitContent] = useState('');
  const [splittingImport, setSplittingImport] = useState(false);
  const [deletingPage, setDeletingPage] = useState(false);
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToastMsg(msg);
    setTimeout(() => setToastMsg(null), 2600);
  };

  const loadSpaces = useCallback(async () => {
    try {
      const res = await apiFetch('/api/v1/wiki/spaces');
      if (res.ok) {
        const json = await res.json();
        const list: WikiSpace[] = json.data || [];
        setSpaces(list);
        if (list.length > 0 && !activeSpaceId) {
          setActiveSpaceId(list[0].wiki_space_id);
        }
      }
    } catch {}
  }, [activeSpaceId]);

  const loadPages = useCallback(async (spaceId: string, q = '') => {
    if (!spaceId) return;
    try {
      const qs = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : '';
      const res = await apiFetch(`/api/v1/wiki/spaces/${spaceId}/pages${qs}`);
      if (res.ok) {
        const json = await res.json();
        setPages(json.data || []);
      }
    } catch {}
  }, []);

  const loadPageDetail = useCallback(async (pageId: string) => {
    try {
      const res = await apiFetch(`/api/v1/wiki/pages/${pageId}`);
      if (res.ok) {
        const json = await res.json();
        const detail: WikiPageDetail = json.data;
        setSelectedPage(detail);
        setDraftMarkdown(detail.markdown_content || '');
        setDraftTitle(detail.title || '');
        setEditorMode('read');
      }
    } catch {}
  }, []);

  useEffect(() => {
    loadSpaces();
  }, [loadSpaces]);

  useEffect(() => {
    if (activeSpaceId) {
      loadPages(activeSpaceId, searchKeyword);
    }
  }, [activeSpaceId, searchKeyword, loadPages]);

  const publishedPages = useMemo(
    () => pages.filter((p) => p.review_status === 'published'),
    [pages]
  );
  const inboxPages = useMemo(
    () => pages.filter((p) => p.review_status === 'inbox'),
    [pages]
  );

  // 按 category_path 构建单层折叠树
  const groupedTree = useMemo(() => {
    const map: Record<string, WikiPageSummary[]> = {};
    for (const p of publishedPages) {
      const cat = p.category_path || '未分类';
      if (!map[cat]) map[cat] = [];
      map[cat].push(p);
    }
    return Object.entries(map).sort(([a], [b]) => a.localeCompare(b));
  }, [publishedPages]);

  const handleCreateSpace = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newSpaceName.trim()) return;
    const res = await apiFetch('/api/v1/wiki/spaces', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newSpaceName.trim() }),
    });
    if (res.ok) {
      const json = await res.json();
      setNewSpaceName('');
      setShowNewSpace(false);
      await loadSpaces();
      if (json.data?.wiki_space_id) {
        setActiveSpaceId(json.data.wiki_space_id);
      }
    }
  };

  const handleSaveEdit = useCallback(async () => {
    if (!selectedPage || savingPage) return;
    setSavingPage(true);
    try {
      const res = await apiFetch(`/api/v1/wiki/pages/${selectedPage.page_id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: draftTitle.trim() || selectedPage.title,
          markdown_content: draftMarkdown,
        }),
      });
      if (res.ok) {
        const json = await res.json();
        setSelectedPage(json.data);
        setEditorMode('read');
        await loadPages(activeSpaceId, searchKeyword);
        await loadSpaces();
        showToast('词条已保存并刷新双向链接');
      }
    } finally {
      setSavingPage(false);
    }
  }, [selectedPage, savingPage, draftTitle, draftMarkdown, activeSpaceId, searchKeyword, loadPages, loadSpaces]);

  // 编辑态支持 Ctrl+S 快捷保存
  useEffect(() => {
    if (editorMode !== 'edit') return;
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        handleSaveEdit();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [editorMode, handleSaveEdit]);

  const handleApproveInbox = async (pageId: string) => {
    const res = await apiFetch(`/api/v1/wiki/inbox/${pageId}/approve`, { method: 'POST' });
    if (res.ok) {
      const json = await res.json();
      await loadPages(activeSpaceId, searchKeyword);
      await loadSpaces();
      if (json.data) {
        setSelectedPage(json.data);
        setDraftMarkdown(json.data.markdown_content || '');
      }
      showToast('已批准归档并同步反哺知识库');
    }
  };

  const handleDiscardInbox = async (pageId: string) => {
    const res = await apiFetch(`/api/v1/wiki/inbox/${pageId}/discard`, { method: 'POST' });
    if (res.ok) {
      await loadPages(activeSpaceId, searchKeyword);
      await loadSpaces();
      if (selectedPage?.page_id === pageId) {
        setSelectedPage(null);
      }
      showToast('已丢弃待审阅增量');
    }
  };

  const handleIngestSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeSpaceId || !ingestContent.trim()) return;
    setIngesting(true);
    try {
      const res = await apiFetch('/api/v1/wiki/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wiki_space_id: activeSpaceId,
          source_type: ingestType,
          content: ingestContent.trim(),
          source_meta: { title: ingestTitle.trim() || undefined },
        }),
      });
      if (res.ok) {
        const json = await res.json();
        setShowIngestModal(false);
        setIngestTitle('');
        setIngestContent('');
        await loadPages(activeSpaceId, searchKeyword);
        await loadSpaces();
        if (json.data?.page_id) {
          await loadPageDetail(json.data.page_id);
        }
        showToast(ingestType === 'meeting_note' ? '会议纪要已整理并送入待审阅 Inbox' : '官方词条已初始化发布');
      }
    } finally {
      setIngesting(false);
    }
  };

  const handleSplitImportFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setSplitFilename(file.name);
    const text = await file.text();
    setSplitContent(text);
  };

  const handleSplitImportSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeSpaceId || !splitContent.trim()) return;
    setSplittingImport(true);
    try {
      const res = await apiFetch('/api/v1/wiki/import/markdown-split', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wiki_space_id: activeSpaceId,
          filename: splitFilename.trim() || 'ontology_guide.md',
          content: splitContent,
          auto_split: true,
          auto_publish: true,
        }),
      });
      if (res.ok) {
        const json = await res.json();
        const pages = json.data?.pages || [];
        setShowSplitImportModal(false);
        setSplitContent('');
        await loadPages(activeSpaceId, searchKeyword);
        await loadSpaces();
        if (pages[0]?.page_id) {
          await loadPageDetail(pages[0].page_id);
        }
        showToast(`已自动拆解导入 ${json.data?.imported_count ?? pages.length} 篇词条`);
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || '导入拆解失败，请检查文档格式');
      }
    } finally {
      setSplittingImport(false);
    }
  };

  const handleDeleteSelectedPage = async () => {
    if (!selectedPage || deletingPage) return;
    const confirmed = window.confirm(`确定要删除词条「${selectedPage.title}」吗？删除后将移入回收站。`);
    if (!confirmed) return;
    setDeletingPage(true);
    try {
      const res = await apiFetch(`/api/v1/wiki/pages/${selectedPage.page_id}?hard_delete=false`, {
        method: 'DELETE',
      });
      if (res.ok) {
        setSelectedPage(null);
        setEditorMode('read');
        await loadPages(activeSpaceId, searchKeyword);
        await loadSpaces();
        showToast('词条已移入回收站');
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || '删除词条失败');
      }
    } finally {
      setDeletingPage(false);
    }
  };

  const handleDeleteActiveSpace = async () => {
    if (!activeSpaceId) return;
    const activeSpace = spaces.find((s) => s.wiki_space_id === activeSpaceId);
    const spaceName = activeSpace?.name || activeSpaceId;
    const confirmed = window.confirm(
      `确定要清空并删除整个 Wiki 空间「${spaceName}」吗？\n删除后将移除该空间下所有词条与索引，且不可恢复。`
    );
    if (!confirmed) return;
    const res = await apiFetch(`/api/v1/wiki/spaces/${encodeURIComponent(activeSpaceId)}`, {
      method: 'DELETE',
    });
    if (res.ok) {
      setSelectedPage(null);
      setPages([]);
      setActiveSpaceId('');
      await loadSpaces();
      showToast(`已删除空间「${spaceName}」`);
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || '删除空间失败');
    }
  };

  const handleLinkMouseEnter = (slug: string, e: React.MouseEvent) => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    const rect = (e.target as HTMLElement).getBoundingClientRect();
    hoverTimerRef.current = setTimeout(async () => {
      setHoverPreview({ slug, x: rect.left, y: rect.bottom + 6, loading: true });
      try {
        const res = await apiFetch(
          `/api/v1/wiki/spaces/${activeSpaceId}/preview?slug=${encodeURIComponent(slug)}`
        );
        if (res.ok) {
          const json = await res.json();
          setHoverPreview((prev) => (prev && prev.slug === slug ? { ...prev, loading: false, data: json.data } : prev));
        } else {
          setHoverPreview((prev) => (prev && prev.slug === slug ? { ...prev, loading: false } : prev));
        }
      } catch {
        setHoverPreview(null);
      }
    }, 150);
  };

  const handleLinkMouseLeave = () => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    setHoverPreview(null);
  };

  const handleNavigateBySlug = async (slug: string) => {
    setHoverPreview(null);
    const match = pages.find((p) => p.slug === slug);
    if (match) {
      await loadPageDetail(match.page_id);
      return;
    }
    const res = await apiFetch(
      `/api/v1/wiki/spaces/${activeSpaceId}/preview?slug=${encodeURIComponent(slug)}`
    );
    if (res.ok) {
      const json = await res.json();
      if (json.data?.page_id) {
        await loadPageDetail(json.data.page_id);
      }
    }
  };

  return (
    <div className="flex flex-col h-full bg-background text-foreground">
      {/* 顶部空间切换与快捷工具栏 */}
      <div className="px-4 py-2.5 border-b border-border bg-surface-card flex items-center justify-between gap-3 shrink-0">
        <div className="flex items-center gap-2.5">
          <BookOpen className="w-4 h-4 text-blue-600 dark:text-blue-400" strokeWidth={1.75} />
          <select
            value={activeSpaceId}
            onChange={(e) => {
              setActiveSpaceId(e.target.value);
              setSelectedPage(null);
            }}
            className="px-2.5 py-1 rounded-md border border-border bg-surface-200 text-xs font-medium focus:outline-none"
          >
            {spaces.length === 0 && <option value="">暂无 Wiki 空间</option>}
            {spaces.map((s) => (
              <option key={s.wiki_space_id} value={s.wiki_space_id}>
                {s.name} ({s.published_count} 词条 / {s.inbox_count} 待审)
              </option>
            ))}
          </select>

          <button
            type="button"
            onClick={() => setShowNewSpace(true)}
            className="px-2.5 py-1 rounded-md border border-border hover:bg-surface-200 text-xs flex items-center gap-1 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" strokeWidth={1.75} />
            <span>新建空间</span>
          </button>

          {activeSpaceId && (
            <button
              type="button"
              onClick={handleDeleteActiveSpace}
              className="px-2.5 py-1 rounded-md border border-rose-500/30 text-rose-600 dark:text-rose-400 hover:bg-rose-500/10 text-xs flex items-center gap-1 transition-colors"
              title="删除整个 Wiki 空间及其中所有词条"
            >
              <Trash2 className="w-3.5 h-3.5" strokeWidth={1.75} />
              <span>删除空间</span>
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          {toastMsg && (
            <span className="px-2.5 py-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-xs">
              {toastMsg}
            </span>
          )}
          <button
            type="button"
            disabled={!activeSpaceId}
            onClick={() => setShowSplitImportModal(true)}
            className="px-2.5 py-1 rounded-md border border-border hover:bg-surface-200 text-xs flex items-center gap-1 disabled:opacity-40"
            title="上传或粘贴 Markdown 总纲，按二级标题自动拆解归类"
          >
            <Upload className="w-3.5 h-3.5" strokeWidth={1.75} />
            <span>导入拆解 .md</span>
          </button>
          <button
            type="button"
            disabled={!activeSpaceId}
            onClick={() => {
              setIngestType('meeting_note');
              setShowIngestModal(true);
            }}
            className="px-2.5 py-1 rounded-md border border-border hover:bg-surface-200 text-xs flex items-center gap-1 disabled:opacity-40"
          >
            <FilePlus className="w-3.5 h-3.5" strokeWidth={1.75} />
            <span>导入会议纪要</span>
          </button>
          <button
            type="button"
            disabled={!activeSpaceId}
            onClick={() => {
              setIngestType('kb_doc');
              setShowIngestModal(true);
            }}
            className="px-2.5 py-1 rounded-md bg-slate-900 text-white dark:bg-zinc-100 dark:text-zinc-900 text-xs font-medium flex items-center gap-1 disabled:opacity-40"
          >
            <Plus className="w-3.5 h-3.5" strokeWidth={1.75} />
            <span>新建词条</span>
          </button>
        </div>
      </div>

      {/* 三栏主工作区 */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* 左栏（260px）：Inbox 入口 + 目录树 */}
        <aside className="w-[260px] shrink-0 border-r border-border bg-surface-card flex flex-col">
          <div className="p-2.5 border-b border-border space-y-2">
            <div className="grid grid-cols-2 gap-1 p-0.5 rounded-md bg-surface-200 border border-border text-xs">
              <button
                type="button"
                onClick={() => setLeftTab('tree')}
                className={`py-1 rounded-md font-medium transition-colors ${
                  leftTab === 'tree' ? 'bg-surface-card text-foreground shadow-sm' : 'text-muted-foreground'
                }`}
              >
                词条目录
              </button>
              <button
                type="button"
                onClick={() => setLeftTab('inbox')}
                className={`py-1 rounded-md font-medium flex items-center justify-center gap-1.5 transition-colors ${
                  leftTab === 'inbox' ? 'bg-surface-card text-foreground shadow-sm' : 'text-muted-foreground'
                }`}
              >
                <Inbox className="w-3.5 h-3.5" strokeWidth={1.75} />
                <span>待审阅</span>
                <span className="px-1.5 py-0.2 rounded-md bg-blue-600 text-white text-[10px] tabular-nums">
                  {inboxPages.length}
                </span>
              </button>
            </div>

            <div className="relative">
              <Search className="w-3.5 h-3.5 text-muted-foreground absolute left-2.5 top-2.5" strokeWidth={1.75} />
              <input
                type="text"
                value={searchKeyword}
                onChange={(e) => setSearchKeyword(e.target.value)}
                placeholder="筛选词条名称或摘要..."
                className="w-full pl-8 pr-2.5 py-1.5 rounded-md border border-border bg-surface-200 text-xs focus:outline-none"
              />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-2 space-y-1.5 text-xs">
            {leftTab === 'inbox' ? (
              inboxPages.length === 0 ? (
                <div className="py-8 text-center text-muted-foreground">当前没有待审阅增量记录</div>
              ) : (
                inboxPages.map((p) => (
                  <div
                    key={p.page_id}
                    onClick={() => loadPageDetail(p.page_id)}
                    className={`p-2.5 rounded-md border cursor-pointer space-y-1.5 transition-colors ${
                      selectedPage?.page_id === p.page_id
                        ? 'border-blue-600 bg-blue-500/5'
                        : 'border-border hover:bg-surface-200/60'
                    }`}
                  >
                    <div className="font-medium text-foreground truncate">{p.title}</div>
                    <div className="text-[11px] text-muted-foreground line-clamp-2">
                      {p.pending_diff_markdown || p.summary_text}
                    </div>
                    <div className="flex items-center gap-1.5 pt-1">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleApproveInbox(p.page_id);
                        }}
                        className="flex-1 py-1 rounded-md bg-slate-900 text-white dark:bg-zinc-100 dark:text-zinc-900 text-[11px] font-medium flex items-center justify-center gap-1"
                      >
                        <Check className="w-3 h-3" strokeWidth={1.75} />
                        <span>批准归档</span>
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDiscardInbox(p.page_id);
                        }}
                        className="px-2 py-1 rounded-md border border-border text-muted-foreground hover:text-rose-600 text-[11px]"
                      >
                        <Trash2 className="w-3 h-3" strokeWidth={1.75} />
                      </button>
                    </div>
                  </div>
                ))
              )
            ) : groupedTree.length === 0 ? (
              <div className="py-8 text-center text-muted-foreground">暂无已发布词条</div>
            ) : (
              groupedTree.map(([dirName, items]) => {
                const isCollapsed = Boolean(collapsedDirs[dirName]);
                return (
                  <div key={dirName} className="space-y-0.5">
                    <button
                      type="button"
                      onClick={() =>
                        setCollapsedDirs((prev) => ({ ...prev, [dirName]: !prev[dirName] }))
                      }
                      className="w-full flex items-center gap-1.5 px-1.5 py-1 rounded-md hover:bg-surface-200 text-muted-foreground font-medium"
                    >
                      {isCollapsed ? (
                        <ChevronRight className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} />
                      ) : (
                        <ChevronDown className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} />
                      )}
                      <Folder className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} />
                      <span className="truncate">{dirName}</span>
                      <span className="ml-auto text-[10px] tabular-nums">{items.length}</span>
                    </button>

                    {!isCollapsed &&
                      items.map((item) => (
                        <button
                          key={item.page_id}
                          type="button"
                          onClick={() => loadPageDetail(item.page_id)}
                          className={`w-full text-left pl-6 pr-2 py-1.5 rounded-md flex items-center gap-1.5 transition-colors ${
                            selectedPage?.page_id === item.page_id
                              ? 'bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium'
                              : 'text-foreground hover:bg-surface-200/60'
                          }`}
                        >
                          <FileText className="w-3.5 h-3.5 shrink-0 opacity-70" strokeWidth={1.75} />
                          <span className="truncate">{item.title}</span>
                        </button>
                      ))}
                  </div>
                );
              })
            )}
          </div>
        </aside>

        {/* 中栏（自适应）：阅读 / 编辑主区 */}
        <main className="flex-1 min-w-0 flex flex-col bg-background overflow-hidden">
          {!selectedPage ? (
            <div className="flex-1 flex flex-col items-center justify-center text-xs text-muted-foreground space-y-2 p-8">
              <BookOpen className="w-8 h-8 opacity-30" strokeWidth={1.5} />
              <p>从左侧目录选择一个词条开始阅读或编辑，或在待审阅中处理新增内容</p>
            </div>
          ) : (
            <>
              <div className="px-6 py-3 border-b border-border bg-surface-card flex items-center justify-between gap-4 shrink-0">
                <div className="min-w-0 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="px-1.5 py-0.5 rounded-md border border-border text-[11px] font-medium text-muted-foreground">
                      {AUTHORITY_LABEL[selectedPage.authority_level] || '[业务词条]'}
                    </span>
                    {editorMode === 'edit' ? (
                      <input
                        type="text"
                        value={draftTitle}
                        onChange={(e) => setDraftTitle(e.target.value)}
                        className="px-2 py-0.5 rounded-md border border-border bg-surface-200 text-sm font-semibold focus:outline-none"
                      />
                    ) : (
                      <h2 className="text-base font-semibold truncate">{selectedPage.title}</h2>
                    )}
                  </div>
                  <div className="text-[11px] text-muted-foreground flex items-center gap-3 tabular-nums">
                    <span>目录：{selectedPage.category_path}</span>
                    {selectedPage.effective_date && <span>生效日期：{selectedPage.effective_date}</span>}
                    <span>更新：{String(selectedPage.updated_at).slice(0, 16)}</span>
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {editorMode === 'read' ? (
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setEditorMode('edit')}
                        className="px-3 py-1.5 rounded-md border border-border hover:bg-surface-200 text-xs font-medium flex items-center gap-1.5"
                      >
                        <Edit3 className="w-3.5 h-3.5" strokeWidth={1.75} />
                        <span>编辑</span>
                      </button>
                      <button
                        type="button"
                        disabled={deletingPage}
                        onClick={handleDeleteSelectedPage}
                        className="px-3 py-1.5 rounded-md border border-rose-500/30 text-rose-600 dark:text-rose-400 hover:bg-rose-500/10 text-xs font-medium flex items-center gap-1.5 disabled:opacity-40"
                        title="删除当前词条（软删除移入回收站）"
                      >
                        <Trash2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                        <span>{deletingPage ? '删除中...' : '删除'}</span>
                      </button>
                    </div>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => {
                          setDraftMarkdown(selectedPage.markdown_content || '');
                          setEditorMode('read');
                        }}
                        className="px-3 py-1.5 rounded-md border border-border hover:bg-surface-200 text-xs flex items-center gap-1"
                      >
                        <Eye className="w-3.5 h-3.5" strokeWidth={1.75} />
                        <span>取消</span>
                      </button>
                      <button
                        type="button"
                        disabled={savingPage}
                        onClick={handleSaveEdit}
                        className="px-3.5 py-1.5 rounded-md bg-slate-900 text-white dark:bg-zinc-100 dark:text-zinc-900 text-xs font-medium disabled:opacity-50"
                      >
                        {savingPage ? '保存中...' : '保存 (Ctrl+S)'}
                      </button>
                    </>
                  )}
                </div>
              </div>

              {/* 若存在待审阅增量 Diff 块，展示顶部扁平审批条 */}
              {selectedPage.pending_diff_markdown && (
                <div className="mx-6 mt-4 p-3.5 rounded-md border border-blue-600/30 bg-blue-500/5 space-y-2 shrink-0">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-blue-600 dark:text-blue-400">
                      待审阅增量补充（原正文未受影响，批准后追加至修订记录）
                    </span>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => handleDiscardInbox(selectedPage.page_id)}
                        className="px-2.5 py-1 rounded-md border border-border text-xs hover:bg-surface-200"
                      >
                        丢弃
                      </button>
                      <button
                        type="button"
                        onClick={() => handleApproveInbox(selectedPage.page_id)}
                        className="px-3 py-1 rounded-md bg-slate-900 text-white dark:bg-zinc-100 dark:text-zinc-900 text-xs font-medium"
                      >
                        批准归档
                      </button>
                    </div>
                  </div>
                  <div className="pl-3 border-l-2 border-blue-600 text-xs whitespace-pre-wrap leading-relaxed">
                    {selectedPage.pending_diff_markdown}
                  </div>
                </div>
              )}

              <div className="flex-1 overflow-y-auto p-6">
                {editorMode === 'edit' ? (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 h-full min-h-[420px]">
                    <textarea
                      value={draftMarkdown}
                      onChange={(e) => setDraftMarkdown(e.target.value)}
                      className="w-full h-full min-h-[400px] p-4 rounded-md border border-border bg-surface-card font-mono text-xs leading-relaxed resize-none focus:outline-none focus:border-blue-600"
                      placeholder="输入 Markdown 正文，支持 [[词条名]] 双向链接..."
                    />
                    <div className="p-4 rounded-md border border-border bg-surface-card overflow-y-auto prose prose-sm dark:prose-invert max-w-none text-xs leading-relaxed">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {preprocessWikiLinks(draftMarkdown)}
                      </ReactMarkdown>
                    </div>
                  </div>
                ) : (
                  <div className="max-w-3xl prose prose-sm dark:prose-invert prose-slate text-sm leading-relaxed">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        a: ({ href, children }) => {
                          if (href && href.startsWith('wikilink://')) {
                            const targetSlug = decodeURIComponent(href.replace('wikilink://', ''));
                            return (
                              <button
                                type="button"
                                onMouseEnter={(e) => handleLinkMouseEnter(targetSlug, e)}
                                onMouseLeave={handleLinkMouseLeave}
                                onClick={() => handleNavigateBySlug(targetSlug)}
                                className="inline-flex items-center px-1.5 py-0.2 mx-0.5 rounded-md border border-blue-600/30 bg-blue-500/10 text-blue-600 dark:text-blue-400 text-xs font-medium hover:bg-blue-500/20"
                              >
                                {children}
                              </button>
                            );
                          }
                          return (
                            <a href={href} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                              {children}
                            </a>
                          );
                        },
                      }}
                    >
                      {preprocessWikiLinks(selectedPage.markdown_content || '*暂无正式正文*')}
                    </ReactMarkdown>
                  </div>
                )}
              </div>
            </>
          )}
        </main>

        {/* 右栏（280px）：局部图谱、双链上下文与溯源时间轴 */}
        <aside className="w-[280px] shrink-0 border-l border-border bg-surface-card flex flex-col">
          <div className="p-2 border-b border-border grid grid-cols-2 gap-1 text-xs">
            <button
              type="button"
              onClick={() => setRightTab('graph')}
              className={`py-1.5 rounded-md font-medium flex items-center justify-center gap-1 ${
                rightTab === 'graph' ? 'bg-surface-200 text-foreground' : 'text-muted-foreground'
              }`}
            >
              <GitFork className="w-3.5 h-3.5" strokeWidth={1.75} />
              <span>关联与图谱</span>
            </button>
            <button
              type="button"
              onClick={() => setRightTab('timeline')}
              className={`py-1.5 rounded-md font-medium flex items-center justify-center gap-1 ${
                rightTab === 'timeline' ? 'bg-surface-200 text-foreground' : 'text-muted-foreground'
              }`}
            >
              <Clock className="w-3.5 h-3.5" strokeWidth={1.75} />
              <span>溯源时间轴</span>
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-4 text-xs">
            {!selectedPage ? (
              <div className="py-8 text-center text-muted-foreground">选择词条后查看关系图谱与溯源记录</div>
            ) : rightTab === 'graph' ? (
              <>
                {/* 轻量 1-hop SVG 图谱 */}
                <div className="p-2.5 rounded-md border border-border bg-surface-200/50">
                  <div className="text-[11px] font-medium text-muted-foreground mb-2">1-Hop 局部关系图谱</div>
                  <svg viewBox="0 0 240 160" className="w-full h-40">
                    {(() => {
                      const nodes = selectedPage.local_graph?.nodes || [];
                      const center = nodes.find((n) => n.is_center);
                      const peers = nodes.filter((n) => !n.is_center);
                      const cx = 120;
                      const cy = 80;
                      return (
                        <>
                          {peers.map((peer, idx) => {
                            const angle = (2 * Math.PI * idx) / Math.max(1, peers.length);
                            const px = cx + Math.cos(angle) * 76;
                            const py = cy + Math.sin(angle) * 54;
                            return (
                              <g
                                key={peer.slug}
                                className="cursor-pointer"
                                onClick={() => handleNavigateBySlug(peer.slug)}
                              >
                                <line x1={cx} y1={cy} x2={px} y2={py} stroke="currentColor" strokeOpacity={0.25} />
                                <circle cx={px} cy={py} r={14} className="fill-surface-card stroke-blue-600" strokeWidth={1.5} />
                                <text
                                  x={px}
                                  y={py + 24}
                                  textAnchor="middle"
                                  className="fill-foreground text-[9px]"
                                >
                                  {peer.slug.slice(0, 8)}
                                </text>
                              </g>
                            );
                          })}
                          {center && (
                            <g>
                              <circle cx={cx} cy={cy} r={18} className="fill-slate-900 dark:fill-zinc-100" />
                              <text
                                x={cx}
                                y={cy + 28}
                                textAnchor="middle"
                                className="fill-foreground text-[10px] font-semibold"
                              >
                                {center.slug.slice(0, 10)}
                              </text>
                            </g>
                          )}
                        </>
                      );
                    })()}
                  </svg>
                </div>

                {/* 本文引用 */}
                <div className="space-y-1.5">
                  <div className="font-medium text-muted-foreground flex items-center gap-1">
                    <Link2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                    <span>本文引用 ({selectedPage.outgoing_links.length})</span>
                  </div>
                  {selectedPage.outgoing_links.length === 0 ? (
                    <div className="text-[11px] text-muted-foreground">暂无出链</div>
                  ) : (
                    selectedPage.outgoing_links.map((o) => (
                      <button
                        key={o.target_slug}
                        type="button"
                        onClick={() => handleNavigateBySlug(o.target_slug)}
                        className="w-full text-left p-2 rounded-md border border-border hover:bg-surface-200/70 space-y-0.5"
                      >
                        <div className="font-medium text-blue-600 dark:text-blue-400">[[{o.target_slug}]]</div>
                        <div className="text-[11px] text-muted-foreground line-clamp-2">{o.context_snippet}</div>
                      </button>
                    ))
                  )}
                </div>

                {/* 反向引用 (Backlinks) */}
                <div className="space-y-1.5">
                  <div className="font-medium text-muted-foreground flex items-center gap-1">
                    <Link2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                    <span>反向引用 ({selectedPage.backlinks.length})</span>
                  </div>
                  {selectedPage.backlinks.length === 0 ? (
                    <div className="text-[11px] text-muted-foreground">暂无其他词条引用本页</div>
                  ) : (
                    selectedPage.backlinks.map((b) => (
                      <button
                        key={b.source_page_id}
                        type="button"
                        onClick={() => loadPageDetail(b.source_page_id)}
                        className="w-full text-left p-2 rounded-md border border-border hover:bg-surface-200/70 space-y-0.5"
                      >
                        <div className="font-medium text-foreground">{b.source_title}</div>
                        <div className="text-[11px] text-muted-foreground line-clamp-2">{b.context_snippet}</div>
                      </button>
                    ))
                  )}
                </div>
              </>
            ) : (
              <div className="space-y-2.5">
                {(selectedPage.source_refs || []).length === 0 ? (
                  <div className="text-muted-foreground text-center py-6">暂无外部来源记录</div>
                ) : (
                  [...(selectedPage.source_refs || [])].reverse().map((ref, idx) => (
                    <div key={`${ref.ref_id}_${idx}`} className="p-2.5 rounded-md border border-border space-y-1">
                      <div className="flex items-center justify-between text-[11px] text-muted-foreground tabular-nums">
                        <span>{ref.type}</span>
                        <span>{ref.timestamp}</span>
                      </div>
                      <div className="font-medium text-foreground">{ref.title}</div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </aside>
      </div>

      {/* 悬停 150ms 双链预览浮卡 */}
      {hoverPreview && (
        <div
          style={{ left: Math.min(hoverPreview.x, window.innerWidth - 300), top: hoverPreview.y }}
          className="fixed z-50 w-72 p-3 rounded-md border border-border bg-surface-card shadow-lg text-xs space-y-1 pointer-events-none"
        >
          <div className="font-semibold text-foreground">{hoverPreview.data?.title || hoverPreview.slug}</div>
          {hoverPreview.loading ? (
            <div className="text-muted-foreground">正在加载摘要...</div>
          ) : hoverPreview.data ? (
            <>
              <div className="text-[11px] text-muted-foreground tabular-nums">
                {AUTHORITY_LABEL[hoverPreview.data.authority_level] || ''} · 更新 {String(hoverPreview.data.updated_at).slice(0, 10)}
              </div>
              <p className="text-muted-foreground leading-relaxed line-clamp-3">{hoverPreview.data.summary_text}</p>
            </>
          ) : (
            <div className="text-muted-foreground">该词条尚未创建正式内容</div>
          )}
        </div>
      )}

      {/* 新建空间弹窗 */}
      {showNewSpace && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <form
            onSubmit={handleCreateSpace}
            className="w-full max-w-sm p-4 rounded-md border border-border bg-surface-card space-y-3 text-xs"
          >
            <div className="font-semibold text-sm">新建 Wiki 知识空间</div>
            <input
              type="text"
              value={newSpaceName}
              onChange={(e) => setNewSpaceName(e.target.value)}
              placeholder="例如：2026击剑俱乐部联赛运营百科"
              className="w-full px-3 py-2 rounded-md border border-border bg-surface-200 focus:outline-none"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowNewSpace(false)}
                className="px-3 py-1.5 rounded-md border border-border"
              >
                取消
              </button>
              <button
                type="submit"
                className="px-3 py-1.5 rounded-md bg-slate-900 text-white dark:bg-zinc-100 dark:text-zinc-900 font-medium"
              >
                创建
              </button>
            </div>
          </form>
        </div>
      )}

      {/* 新建词条 / 导入会议纪要弹窗 */}
      {showIngestModal && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <form
            onSubmit={handleIngestSubmit}
            className="w-full max-w-lg p-4 rounded-md border border-border bg-surface-card space-y-3 text-xs"
          >
            <div className="font-semibold text-sm">
              {ingestType === 'meeting_note' ? '导入会议纪要（自动进入待审阅 Inbox）' : '新建官方基准词条'}
            </div>
            <input
              type="text"
              value={ingestTitle}
              onChange={(e) => setIngestTitle(e.target.value)}
              placeholder="词条标题（选填，留空则由轻量模型自动提炼）"
              className="w-full px-3 py-2 rounded-md border border-border bg-surface-200 focus:outline-none"
            />
            <textarea
              value={ingestContent}
              onChange={(e) => setIngestContent(e.target.value)}
              rows={7}
              placeholder="粘贴会议纪要或规程正文..."
              className="w-full px-3 py-2 rounded-md border border-border bg-surface-200 focus:outline-none"
              required
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowIngestModal(false)}
                className="px-3 py-1.5 rounded-md border border-border"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={ingesting}
                className="px-4 py-1.5 rounded-md bg-slate-900 text-white dark:bg-zinc-100 dark:text-zinc-900 font-medium disabled:opacity-50"
              >
                {ingesting ? '处理中...' : '确认提交'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* 导入拆解 .md 弹窗 */}
      {showSplitImportModal && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <form
            onSubmit={handleSplitImportSubmit}
            className="w-full max-w-2xl rounded-xl border border-border bg-surface-50 p-5 shadow-2xl space-y-3"
          >
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">导入并自动拆解 Markdown 总纲</h3>
              <span className="text-[11px] text-text-tertiary">
                按二级标题 (##) 自动拆解归类并发布到当前 Wiki 空间
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] text-text-secondary mb-1">选择本地 .md 文件</label>
                <input
                  type="file"
                  accept=".md,.markdown,.txt"
                  onChange={handleSplitImportFileChange}
                  className="w-full text-xs text-text-secondary file:mr-2 file:py-1 file:px-2.5 file:rounded-md file:border file:border-border file:bg-surface-200 file:text-xs file:text-text-primary hover:file:bg-surface-300"
                />
              </div>
              <div>
                <label className="block text-[11px] text-text-secondary mb-1">源文件名</label>
                <input
                  type="text"
                  value={splitFilename}
                  onChange={(e) => setSplitFilename(e.target.value)}
                  placeholder="例如：fencing_business_ontology_and_query_guide.md"
                  className="w-full px-3 py-1.5 text-xs rounded-md border border-border bg-surface-100"
                />
              </div>
            </div>
            <textarea
              autoFocus
              rows={12}
              value={splitContent}
              onChange={(e) => setSplitContent(e.target.value)}
              placeholder="上传本地 .md 文件或直接粘贴包含多个 ## 二级标题的 Markdown 文档..."
              className="w-full px-3 py-2 text-xs font-mono rounded-md border border-border bg-surface-100 focus:outline-none focus:border-blue-500/60"
            />
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setShowSplitImportModal(false)}
                className="px-3 py-1.5 rounded-md border border-border text-xs"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={splittingImport || !splitContent.trim()}
                className="px-3 py-1.5 rounded-md bg-slate-900 text-white dark:bg-zinc-100 dark:text-zinc-900 text-xs font-medium disabled:opacity-40"
              >
                {splittingImport ? '正在拆解入库...' : '开始拆解并发布'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

