'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  BookOpen,
  Camera,
  Check,
  ChevronDown,
  Clock,
  Database,
  Menu,
  MessageSquare,
  Monitor,
  Moon,
  Plus,
  Search,
  Sparkles,
  SquarePen,
  Sun,
  X,
} from 'lucide-react';
import { apiFetch } from '@/utils/api';
import { useTheme } from '@/context/ThemeContext';
import { MobileChatView, MobileExpertItem } from '@/components/mobile/MobileChatView';
import { MobileWikiView } from '@/components/mobile/MobileWikiView';
import { MobileSnapSheet } from '@/components/mobile/MobileSnapSheet';

type MobileTab = 'wiki' | 'chat';

interface ConversationSummary {
  id: string;
  title: string;
  updated_at?: number;
}

interface KBItemSummary {
  kb_id: string;
  name: string;
  active_version?: string;
}

interface MobileShellProps {
  onSwitchToDesktop: () => void;
}

/**
 * 移动端 Gemini 风格主外壳
 * - 极简 56px 顶栏：左汉堡菜单 + 居中专家切换胶囊 + 右新建对话
 * - 侧滑抽屉（对齐图 2）：搜索/关闭 -> 发起新对话 -> 专家/Wiki/随手拍入口 -> 知识库快捷列表 -> 全部历史对话（含筛选）-> 底部设置
 */
export function MobileShell({ onSwitchToDesktop }: MobileShellProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const [activeTab, setActiveTab] = useState<MobileTab>('chat');
  const [experts, setExperts] = useState<MobileExpertItem[]>([]);
  const [activeExpert, setActiveExpert] = useState<MobileExpertItem | null>(null);
  const [expertPickerOpen, setExpertPickerOpen] = useState(false);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerSearchOpen, setDrawerSearchOpen] = useState(false);
  const [drawerKeyword, setDrawerKeyword] = useState('');
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [kbList, setKbList] = useState<KBItemSummary[]>([]);

  const [snapOpen, setSnapOpen] = useState(false);
  const [inboxCount, setInboxCount] = useState(0);
  const [sceneMode, setSceneMode] = useState<'agent' | 'chat'>('agent');

  const loadExpertsAndSpaces = useCallback(async () => {
    try {
      const [agRes, spRes, kbRes] = await Promise.all([
        apiFetch('/api/v1/agents'),
        apiFetch('/api/v1/wiki/spaces'),
        apiFetch('/api/v1/kb/bases?published_only=true'),
      ]);
      if (agRes.ok) {
        const agData = await agRes.json();
        const items: MobileExpertItem[] = (agData?.agents || []).map((a: MobileExpertItem) => ({
          id: a.id,
          name: a.name,
          description: a.description,
        }));
        setExperts(items);
        if (items.length > 0) {
          setActiveExpert((prev) => prev || items[0]);
        }
      }
      if (spRes.ok) {
        const spData = await spRes.json();
        const total = (spData?.data || []).reduce(
          (acc: number, s: { inbox_count?: number }) => acc + (s.inbox_count || 0),
          0
        );
        setInboxCount(total);
      }
      if (kbRes.ok) {
        const kbData = await kbRes.json();
        const rows = Array.isArray(kbData?.data) ? kbData.data : Array.isArray(kbData) ? kbData : [];
        setKbList(
          rows.slice(0, 6).map((k: KBItemSummary) => ({
            kb_id: k.kb_id,
            name: k.name,
            active_version: k.active_version,
          }))
        );
      }
    } catch {
      // 忽略瞬时网络错误
    }
  }, []);

  const loadConversations = useCallback(async () => {
    try {
      const res = await apiFetch('/api/v1/conversations');
      if (!res.ok) return;
      const list = await res.json();
      if (Array.isArray(list)) {
        setConversations(list.slice(0, 40));
      }
    } catch {
      // 忽略
    }
  }, []);

  useEffect(() => {
    void loadExpertsAndSpaces();
    void loadConversations();
  }, [loadExpertsAndSpaces, loadConversations]);

  const filteredConversations = useMemo(() => {
    const q = drawerKeyword.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter((c) => (c.title || '未命名会话').toLowerCase().includes(q));
  }, [conversations, drawerKeyword]);

  const toggleTheme = () => {
    setTheme(resolvedTheme === 'dark' ? 'light' : 'dark');
  };

  const handleNewChat = () => {
    setActiveConvId(null);
    setActiveTab('chat');
    setDrawerOpen(false);
  };

  return (
    <div
      className="flex h-[100dvh] w-full flex-col overflow-hidden bg-background text-foreground touch-manipulation"
      style={{
        paddingTop: 'env(safe-area-inset-top)',
        paddingBottom: 'env(safe-area-inset-bottom)',
      }}
    >
      {/* 极简顶栏（对齐图 1）：左汉堡菜单 + 居中专家胶囊 + 右新建对话 */}
      <header className="z-20 flex h-14 shrink-0 items-center justify-between px-3 bg-background">
        <button
          type="button"
          onClick={() => {
            void loadConversations();
            setDrawerOpen(true);
          }}
          aria-label="打开导航抽屉"
          className="flex h-11 w-11 items-center justify-center rounded-full text-foreground transition-transform active:scale-95 active:bg-surface-1"
        >
          <Menu className="h-5 w-5" strokeWidth={1.85} />
        </button>

        <button
          type="button"
          onClick={() => setExpertPickerOpen(true)}
          className="flex max-w-[62%] items-center gap-1.5 rounded-full border border-border/60 bg-surface-1 px-3.5 py-1.5 text-xs font-semibold text-foreground transition-transform active:scale-[0.98]"
        >
          <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-500" />
          <span className="truncate">{activeExpert?.name || '通用助手'}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} />
        </button>

        <button
          type="button"
          onClick={handleNewChat}
          aria-label="新建对话"
          className="flex h-11 w-11 items-center justify-center rounded-full text-foreground transition-transform active:scale-95 active:bg-surface-1"
        >
          <SquarePen className="h-5 w-5" strokeWidth={1.75} />
        </button>
      </header>

      {/* 主内容区域（全屏留白给对话区或口袋 Wiki） */}
      <main className="min-h-0 flex-1 overflow-hidden">
        {activeTab === 'chat' ? (
          <MobileChatView
            conversationId={activeConvId}
            experts={experts}
            activeExpert={activeExpert}
            sceneMode={sceneMode}
            onChangeSceneMode={setSceneMode}
            onSelectExpert={(ex) => setActiveExpert(ex)}
            onConversationCreated={(convId) => {
              setActiveConvId(convId);
              void loadConversations();
            }}
            onOpenSnap={() => setSnapOpen(true)}
          />
        ) : (
          <MobileWikiView onInboxCountChange={setInboxCount} />
        )}
      </main>

      {/* 左侧滑抽屉（对齐图 2 结构） */}
      {drawerOpen && (
        <div
          className="fixed inset-0 z-50 flex bg-black/50 backdrop-blur-[1px]"
          onClick={() => setDrawerOpen(false)}
        >
          <aside
            className="flex h-full w-[82%] max-w-xs flex-col rounded-r-2xl border-r border-border bg-background shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 1. 顶部品牌 + 搜索开关 + 关闭 */}
            <div className="flex h-14 shrink-0 items-center justify-between px-4">
              <div className="flex items-center gap-2 font-semibold text-foreground">
                <Sparkles className="h-4 w-4 text-emerald-500" strokeWidth={2} />
                <span>AgentHub</span>
              </div>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setDrawerSearchOpen((v) => !v)}
                  aria-label="搜索历史对话"
                  className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground active:bg-surface-1"
                >
                  <Search className="h-4.5 w-4.5" strokeWidth={1.75} />
                </button>
                <button
                  type="button"
                  onClick={() => setDrawerOpen(false)}
                  aria-label="关闭抽屉"
                  className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground active:bg-surface-1"
                >
                  <X className="h-5 w-5" strokeWidth={1.75} />
                </button>
              </div>
            </div>

            {/* 可展开的搜索框 */}
            {drawerSearchOpen && (
              <div className="px-3 pb-2">
                <input
                  type="text"
                  value={drawerKeyword}
                  onChange={(e) => setDrawerKeyword(e.target.value)}
                  placeholder="筛选历史对话..."
                  className="h-9 w-full rounded-full border border-border bg-surface-1 px-3.5 text-xs text-foreground placeholder:text-muted-foreground focus:border-emerald-500 focus:outline-none"
                  autoFocus
                />
              </div>
            )}

            {/* 2. 发起新对话全宽胶囊 */}
            <div className="px-3 pb-2">
              <button
                type="button"
                onClick={handleNewChat}
                className="flex h-11 w-full items-center gap-2.5 rounded-full border border-emerald-500/35 bg-surface-1 px-4 text-sm font-medium text-foreground transition-transform active:scale-[0.98]"
              >
                <Plus className="h-4 w-4 text-emerald-500" strokeWidth={2} />
                <span>发起新对话</span>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-3 pb-4 space-y-4">
              {/* 3. 快捷功能入口 */}
              <div className="space-y-1">
                <button
                  type="button"
                  onClick={() => {
                    setActiveTab('chat');
                    setDrawerOpen(false);
                  }}
                  className={`flex min-h-[44px] w-full items-center gap-3 rounded-xl px-3 text-sm ${
                    activeTab === 'chat'
                      ? 'bg-emerald-500/10 font-medium text-emerald-600 dark:text-emerald-400'
                      : 'text-foreground/85 active:bg-surface-1'
                  }`}
                >
                  <MessageSquare className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                  <span>专家问答</span>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setActiveTab('wiki');
                    setDrawerOpen(false);
                  }}
                  className={`flex min-h-[44px] w-full items-center justify-between rounded-xl px-3 text-sm ${
                    activeTab === 'wiki'
                      ? 'bg-emerald-500/10 font-medium text-emerald-600 dark:text-emerald-400'
                      : 'text-foreground/85 active:bg-surface-1'
                  }`}
                >
                  <span className="flex items-center gap-3">
                    <BookOpen className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                    <span>口袋 Wiki</span>
                  </span>
                  {inboxCount > 0 && (
                    <span className="rounded-full bg-rose-500 px-1.5 py-0.5 text-[10px] font-semibold text-white tabular-nums">
                      {inboxCount > 99 ? '99+' : inboxCount}
                    </span>
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setDrawerOpen(false);
                    setSnapOpen(true);
                  }}
                  className="flex min-h-[44px] w-full items-center gap-3 rounded-xl px-3 text-sm text-foreground/85 active:bg-surface-1"
                >
                  <Camera className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                  <span>随手拍归档</span>
                </button>
              </div>

              {/* 4. 知识库快捷列表 */}
              {kbList.length > 0 && (
                <div>
                  <div className="px-3 pb-1.5 text-[11px] font-semibold tracking-wider text-muted-foreground">
                    已发布知识库
                  </div>
                  <div className="space-y-1">
                    {kbList.map((kb) => (
                      <button
                        key={kb.kb_id}
                        type="button"
                        onClick={() => {
                          setSceneMode('agent');
                          setActiveTab('chat');
                          setDrawerOpen(false);
                        }}
                        className="flex min-h-[40px] w-full items-center justify-between gap-2 rounded-xl px-3 py-1.5 text-left text-xs text-foreground/85 active:bg-surface-1"
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <Database className="h-3.5 w-3.5 shrink-0 text-emerald-500" strokeWidth={1.75} />
                          <span className="truncate">{kb.name}</span>
                        </span>
                        {kb.active_version && (
                          <span className="shrink-0 rounded bg-surface-1 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                            {kb.active_version}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* 5. 全部历史对话 */}
              <div>
                <div className="px-3 pb-1.5 text-[11px] font-semibold tracking-wider text-muted-foreground">
                  最近对话
                </div>
                {filteredConversations.length === 0 ? (
                  <p className="px-3 py-4 text-xs text-muted-foreground">暂无匹配的历史会话</p>
                ) : (
                  <div className="space-y-1">
                    {filteredConversations.map((conv) => (
                      <button
                        key={conv.id}
                        type="button"
                        onClick={() => {
                          setActiveConvId(conv.id);
                          setActiveTab('chat');
                          setDrawerOpen(false);
                        }}
                        className={`flex min-h-[42px] w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-xs transition-colors ${
                          activeConvId === conv.id
                            ? 'bg-emerald-500/10 font-medium text-emerald-600 dark:text-emerald-400'
                            : 'text-foreground/85 active:bg-surface-1'
                        }`}
                      >
                        <Clock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} />
                        <span className="truncate">{conv.title || '未命名会话'}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* 6. 底部固定设置区 */}
            <div className="shrink-0 border-t border-border/60 p-3 flex items-center gap-2">
              <button
                type="button"
                onClick={toggleTheme}
                aria-label="切换主题"
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-surface-1 text-foreground active:bg-surface-2"
              >
                {resolvedTheme === 'dark' ? (
                  <Sun className="h-4 w-4" strokeWidth={1.75} />
                ) : (
                  <Moon className="h-4 w-4" strokeWidth={1.75} />
                )}
              </button>
              <button
                type="button"
                onClick={() => {
                  setDrawerOpen(false);
                  onSwitchToDesktop();
                }}
                className="flex h-11 flex-1 items-center justify-center gap-2 rounded-xl border border-border bg-surface-1 text-xs font-medium text-foreground active:bg-surface-2"
              >
                <Monitor className="h-4 w-4" strokeWidth={1.75} />
                <span>切换桌面完整版</span>
              </button>
            </div>
          </aside>
        </div>
      )}

      {/* 专家与对话模式 Bottom Sheet */}
      {expertPickerOpen && (
        <div
          className="fixed inset-0 z-50 flex flex-col justify-end bg-black/50 backdrop-blur-[1px]"
          onClick={() => setExpertPickerOpen(false)}
        >
          <div
            className="max-h-[78dvh] overflow-y-auto rounded-t-3xl border-t border-border bg-background px-4 pb-6 pt-2.5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-border" />

            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-base font-semibold text-foreground">选择专家与对话模式</h3>
              <button
                type="button"
                onClick={() => setExpertPickerOpen(false)}
                aria-label="关闭"
                className="flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground active:bg-surface-1"
              >
                <X className="h-5 w-5" strokeWidth={1.75} />
              </button>
            </div>

            <div className="mb-4 grid grid-cols-2 gap-1.5 rounded-2xl bg-surface-1 p-1.5">
              <button
                type="button"
                onClick={() => setSceneMode('agent')}
                className={`h-10 rounded-xl text-xs font-medium transition-all ${
                  sceneMode === 'agent'
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground'
                }`}
              >
                Task（知识库 / 自主任务）
              </button>
              <button
                type="button"
                onClick={() => setSceneMode('chat')}
                className={`h-10 rounded-xl text-xs font-medium transition-all ${
                  sceneMode === 'chat'
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground'
                }`}
              >
                Chat（纯文本快聊）
              </button>
            </div>

            <p className="mb-2 text-xs font-medium text-muted-foreground">全部专家</p>
            <div className="space-y-2">
              {experts.map((ex) => {
                const selected = activeExpert?.id === ex.id;
                return (
                  <button
                    key={ex.id}
                    type="button"
                    onClick={() => {
                      setActiveExpert(ex);
                      setExpertPickerOpen(false);
                    }}
                    className={`flex w-full items-center justify-between gap-3 rounded-2xl border p-3.5 text-left transition-transform active:scale-[0.99] ${
                      selected
                        ? 'border-emerald-500/70 bg-emerald-500/10'
                        : 'border-border/70 bg-surface-1'
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-semibold text-foreground">{ex.name}</div>
                      {ex.description && (
                        <div className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                          {ex.description}
                        </div>
                      )}
                    </div>
                    {selected && (
                      <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white">
                        <Check className="h-3.5 w-3.5" strokeWidth={2.25} />
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* 随手拍抽屉 */}
      <MobileSnapSheet
        open={snapOpen}
        onClose={() => setSnapOpen(false)}
        experts={experts}
        defaultExpertId={activeExpert?.id}
        onNavigateToWiki={() => setActiveTab('wiki')}
      />
    </div>
  );
}
