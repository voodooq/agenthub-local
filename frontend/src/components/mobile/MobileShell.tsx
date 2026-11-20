'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  BookOpen,
  Camera,
  Check,
  ChevronDown,
  History,
  MessageSquare,
  Monitor,
  Moon,
  Plus,
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

interface MobileShellProps {
  onSwitchToDesktop: () => void;
}

/**
 * 移动端伴侣模式主外壳
 * 遵循移动端人机工程学规范：100dvh 安全区、44px+ 触控热区、半透明毛玻璃导航栏与原生 Bottom Sheet 手感
 */
export function MobileShell({ onSwitchToDesktop }: MobileShellProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const [activeTab, setActiveTab] = useState<MobileTab>('chat');
  const [experts, setExperts] = useState<MobileExpertItem[]>([]);
  const [activeExpert, setActiveExpert] = useState<MobileExpertItem | null>(null);
  const [expertPickerOpen, setExpertPickerOpen] = useState(false);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);

  const [snapOpen, setSnapOpen] = useState(false);
  const [inboxCount, setInboxCount] = useState(0);
  const [sceneMode, setSceneMode] = useState<'agent' | 'chat'>('agent');

  const loadExpertsAndSpaces = useCallback(async () => {
    try {
      const [agRes, spRes] = await Promise.all([
        apiFetch('/api/v1/agents'),
        apiFetch('/api/v1/wiki/spaces'),
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
        setConversations(list.slice(0, 30));
      }
    } catch {
      // 忽略
    }
  }, []);

  useEffect(() => {
    void loadExpertsAndSpaces();
    void loadConversations();
  }, [loadExpertsAndSpaces, loadConversations]);

  const toggleTheme = () => {
    setTheme(resolvedTheme === 'dark' ? 'light' : 'dark');
  };

  return (
    <div
      className="flex h-[100dvh] w-full flex-col overflow-hidden bg-background text-foreground touch-manipulation"
      style={{
        paddingTop: 'env(safe-area-inset-top)',
        paddingBottom: 'env(safe-area-inset-bottom)',
      }}
    >
      {/* 顶部原生 App 导航栏 */}
      <header className="z-20 flex h-14 shrink-0 items-center justify-between border-b border-border/60 bg-background/90 px-3 backdrop-blur-md">
        <button
          type="button"
          onClick={() => {
            void loadConversations();
            setDrawerOpen(true);
          }}
          aria-label="历史会话"
          className="flex h-11 w-11 items-center justify-center rounded-full text-foreground transition-transform active:scale-95 active:bg-surface-1"
        >
          <History className="h-5 w-5" strokeWidth={1.75} />
        </button>

        <button
          type="button"
          onClick={() => setExpertPickerOpen(true)}
          className="flex max-w-[64%] items-center gap-1.5 rounded-full border border-border/70 bg-surface-1 px-3.5 py-2 text-xs font-semibold text-foreground shadow-sm transition-transform active:scale-[0.98]"
        >
          <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-500" />
          <span className="truncate">{activeExpert?.name || '通用助手'}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.75} />
        </button>

        <button
          type="button"
          onClick={toggleTheme}
          aria-label={resolvedTheme === 'dark' ? '切换浅色模式' : '切换深色模式'}
          className="flex h-11 w-11 items-center justify-center rounded-full text-foreground transition-transform active:scale-95 active:bg-surface-1"
        >
          {resolvedTheme === 'dark' ? (
            <Sun className="h-5 w-5" strokeWidth={1.75} />
          ) : (
            <Moon className="h-5 w-5" strokeWidth={1.75} />
          )}
        </button>
      </header>

      {/* 主内容区域 */}
      <main className="min-h-0 flex-1 overflow-hidden">
        {activeTab === 'chat' ? (
          <MobileChatView
            conversationId={activeConvId}
            experts={experts}
            activeExpert={activeExpert}
            sceneMode={sceneMode}
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

      {/* 底部原生 App 风格 3-Tab 导航栏 */}
      <nav className="z-20 grid h-16 shrink-0 grid-cols-3 border-t border-border/60 bg-background/90 px-2 backdrop-blur-md">
        <button
          type="button"
          onClick={() => setActiveTab('wiki')}
          className="flex flex-col items-center justify-center gap-1 transition-transform active:scale-95"
        >
          <div
            className={`relative flex h-8 w-14 items-center justify-center rounded-full transition-colors ${
              activeTab === 'wiki'
                ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                : 'text-muted-foreground'
            }`}
          >
            <BookOpen className="h-5 w-5" strokeWidth={1.75} />
            {inboxCount > 0 && (
              <span className="absolute right-1 top-0 rounded-full bg-rose-500 px-1.5 text-[10px] font-semibold leading-tight text-white tabular-nums">
                {inboxCount > 99 ? '99+' : inboxCount}
              </span>
            )}
          </div>
          <span
            className={`text-[11px] font-medium ${
              activeTab === 'wiki' ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'
            }`}
          >
            口袋Wiki
          </span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('chat')}
          className="flex flex-col items-center justify-center gap-1 transition-transform active:scale-95"
        >
          <div
            className={`flex h-8 w-14 items-center justify-center rounded-full transition-colors ${
              activeTab === 'chat'
                ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                : 'text-muted-foreground'
            }`}
          >
            <MessageSquare className="h-5 w-5" strokeWidth={1.75} />
          </div>
          <span
            className={`text-[11px] font-medium ${
              activeTab === 'chat' ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'
            }`}
          >
            问专家
          </span>
        </button>

        <button
          type="button"
          onClick={() => setSnapOpen(true)}
          className="flex flex-col items-center justify-center gap-1 text-muted-foreground transition-transform active:scale-95 active:text-foreground"
        >
          <div className="flex h-8 w-14 items-center justify-center rounded-full">
            <Camera className="h-5 w-5" strokeWidth={1.75} />
          </div>
          <span className="text-[11px] font-medium">随手拍</span>
        </button>
      </nav>

      {/* 左侧历史会话抽屉 */}
      {drawerOpen && (
        <div
          className="fixed inset-0 z-50 flex bg-black/50 backdrop-blur-[1px]"
          onClick={() => setDrawerOpen(false)}
        >
          <aside
            className="flex h-full w-[82%] max-w-xs flex-col rounded-r-2xl border-r border-border bg-background shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex h-14 shrink-0 items-center justify-between border-b border-border/60 px-4">
              <span className="text-base font-semibold text-foreground">历史会话</span>
              <button
                type="button"
                onClick={() => setDrawerOpen(false)}
                aria-label="关闭抽屉"
                className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground active:bg-surface-1"
              >
                <X className="h-5 w-5" strokeWidth={1.75} />
              </button>
            </div>

            <div className="p-3">
              <button
                type="button"
                onClick={() => {
                  setActiveConvId(null);
                  setActiveTab('chat');
                  setDrawerOpen(false);
                }}
                className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 text-sm font-medium text-white shadow-sm transition-transform active:scale-[0.98]"
              >
                <Plus className="h-4 w-4" strokeWidth={1.75} />
                <span>开启新对话</span>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-3 pb-4">
              {conversations.length === 0 ? (
                <p className="py-10 text-center text-xs text-muted-foreground">暂无历史会话</p>
              ) : (
                <div className="space-y-1.5">
                  {conversations.map((conv) => (
                    <button
                      key={conv.id}
                      type="button"
                      onClick={() => {
                        setActiveConvId(conv.id);
                        setActiveTab('chat');
                        setDrawerOpen(false);
                      }}
                      className={`flex min-h-[44px] w-full items-center rounded-xl px-3.5 py-2.5 text-left text-sm transition-colors ${
                        activeConvId === conv.id
                          ? 'bg-emerald-500/10 font-medium text-emerald-600 dark:text-emerald-400'
                          : 'text-foreground/85 active:bg-surface-1'
                      }`}
                    >
                      <span className="truncate">{conv.title || '未命名会话'}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="shrink-0 border-t border-border/60 p-3">
              <button
                type="button"
                onClick={() => {
                  setDrawerOpen(false);
                  onSwitchToDesktop();
                }}
                className="flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-border bg-surface-1 text-xs font-medium text-muted-foreground active:bg-surface-2"
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
            {/* Bottom Sheet 拖拽指示条 */}
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

            {/* 对话模式分段器 */}
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
                深度查阅（调用知识库）
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
                纯文本快聊
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
