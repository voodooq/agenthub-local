'use client';

import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  Menu,
  Plus,
  MessageSquare,
  Pin,
  Search,
  Trash2,
  MoreVertical,
  Edit2,
  Sliders,
  Library,
  Clock,
  Puzzle,
  Bot,
  FolderInput,
  Brain,
  Folder,
  FolderGit2,
  FolderPlus,
  Box,
  Cloud,
  ChevronRight,
  ChevronDown,
  GitBranch,
  BarChart3,
  Activity,
  UploadCloud,
  Sparkles,
  FileCode2,
  X,
  AlertCircle,
} from 'lucide-react';
import { Conversation, StatusPillData, Workspace } from '@/types';
import { useAppVersion } from '@/config/version';
import { apiFetch } from '@/utils/apiClient';
import { ServerFolderBrowser } from './ServerFolderBrowser';

export type MainNavHub = 'chat' | 'agents' | 'library' | 'scheduled' | 'skills' | 'memory' | 'llmwiki';

interface SidebarProps {
  conversations: Conversation[];
  activeConvId: string | null;
  workspaces: Workspace[];
  activeWorkspaceId: string;
  activeNavHub?: MainNavHub;
  statusPill?: StatusPillData | null;
  pendingProposalCount?: number;
  onSelectNavHub?: (hub: MainNavHub) => void;
  onSelectWorkspace: (id: string) => void;
  onRefreshWorkspaces: () => void;
  onSelectConv: (id: string) => void;
  onNewChat: (targetWorkspaceId?: string) => void;
  onDeleteConv: (id: string) => void;
  onTogglePin: (id: string, isPinned: boolean) => void;
  onRenameConv: (id: string, newTitle: string) => void;
  onMoveConvWorkspace?: (convId: string, targetWorkspaceId: string) => void;
  onOpenSearch: () => void;
  onOpenMemoryHub?: () => void;
  onOpenAccounts: () => void;
  onOpenCloudWiki?: () => void;
  onOpenSync: () => void;
  onOpenSettings: () => void;
  onOpenTokenStats?: () => void;
  onOpenSystemMonitor?: () => void;
  onOpenWorkspaceContract?: (workspace: Workspace) => void;
  onCleanupDuplicates?: () => void;
  hasUpdate?: boolean;
  latestVersion?: string;
  onOpenUpdateModal?: () => void;
}

/**
 * 格式化列表右侧精简相对时间（如 刚刚 / 8分钟 / 5小时 / 1天）
 */
function formatRelativeAge(dateStr?: string): string {
  if (!dateStr) return '';
  try {
    const parsed = new Date(dateStr.includes('T') ? dateStr : `${dateStr.replace(' ', 'T')}Z`);
    const ts = parsed.getTime();
    if (Number.isNaN(ts)) return '';
    const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (diffSec < 60) return '刚刚';
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}分钟`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour}小时`;
    const diffDay = Math.floor(diffHour / 24);
    if (diffDay < 30) return `${diffDay}天`;
    const diffMonth = Math.floor(diffDay / 30);
    return `${diffMonth}月`;
  } catch {
    return '';
  }
}

function isBranchedConversation(conv: Conversation): boolean {
  return Boolean(conv.parent_conversation_id) || (conv.title || '').startsWith('分支 · ');
}

export function Sidebar({
  conversations,
  activeConvId,
  workspaces,
  activeWorkspaceId,
  activeNavHub = 'chat',
  statusPill,
  pendingProposalCount = 0,
  onSelectNavHub,
  onSelectWorkspace,
  onRefreshWorkspaces,
  onSelectConv,
  onNewChat,
  onDeleteConv,
  onTogglePin,
  onRenameConv,
  onMoveConvWorkspace,
  onOpenSearch,
  onOpenMemoryHub,
  onOpenAccounts,
  onOpenCloudWiki,
  onOpenSync,
  onOpenSettings,
  onOpenTokenStats,
  onOpenSystemMonitor,
  onOpenWorkspaceContract,
  onCleanupDuplicates,
  hasUpdate = false,
  latestVersion,
  onOpenUpdateModal,
}: SidebarProps) {
  const { tag: appVersionTag } = useAppVersion();
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [movingConvId, setMovingConvId] = useState<string | null>(null);
  const [expandedWorkspaceIds, setExpandedWorkspaceIds] = useState<Record<string, boolean>>({});

  // 内联挂载本地目录 / 新建沙盒工作区状态（替代原本 WorkspaceSwitcher 的下拉隐藏操作）
  const [isMountingModalOpen, setIsMountingModalOpen] = useState(false);
  const [mountPath, setMountPath] = useState('');
  const [mountName, setMountName] = useState('');
  const [mountContext, setMountContext] = useState('');
  const [isSelectingNative, setIsSelectingNative] = useState(false);
  const [isRemoteFolderMode, setIsRemoteFolderMode] = useState(false);
  const [isCreatingVirtual, setIsCreatingVirtual] = useState(false);
  const [virtualName, setVirtualName] = useState('');
  const [wsErrorMsg, setWsErrorMsg] = useState('');
  // 沙盒与项目快捷过滤状态
  const [isFilterOpen, setIsFilterOpen] = useState(false);
  const [filterQuery, setFilterQuery] = useState('');
  const filterInputRef = useRef<HTMLInputElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);

  // 当前激活工作区默认自动展开
  useEffect(() => {
    if (activeWorkspaceId && activeWorkspaceId !== 'default') {
      setExpandedWorkspaceIds((prev) => ({ ...prev, [activeWorkspaceId]: true }));
    }
  }, [activeWorkspaceId]);

  // 监听按键 '[' 快捷折叠/展开侧边栏
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
        return;
      }
      if (e.key === '[') {
        e.preventDefault();
        setIsCollapsed((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // 点击侧栏外部关闭会话操作菜单
  useEffect(() => {
    const handleOutside = (e: MouseEvent) => {
      if (menuOpenId && sidebarRef.current && !sidebarRef.current.contains(e.target as Node)) {
        setMenuOpenId(null);
        setMovingConvId(null);
      }
    };
    document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, [menuOpenId]);

  // 划分项目工作区（非 default）与独立对话（default），并支持快捷过滤与置顶优先排序
  const normalizedFilter = filterQuery.trim().toLowerCase();

  const { projectWorkspaces, filteredProjectWorkspaces, pinnedStandalone, regularStandalone, standaloneConvs } =
    useMemo(() => {
      const allProjects = workspaces.filter((w) => w.id !== 'default');

      // 优先置顶当前激活工作区、以及包含置顶对话的工作区
      const pinnedWsIds = new Set(
        conversations.filter((c) => c.is_pinned && c.workspace_id && c.workspace_id !== 'default').map((c) => c.workspace_id)
      );

      const sortedProjects = [...allProjects].sort((a, b) => {
        const aScore = (a.id === activeWorkspaceId ? 2 : 0) + (pinnedWsIds.has(a.id) ? 1 : 0);
        const bScore = (b.id === activeWorkspaceId ? 2 : 0) + (pinnedWsIds.has(b.id) ? 1 : 0);
        return bScore - aScore;
      });

      const filteredProjects = normalizedFilter
        ? sortedProjects.filter((w) => (w.name || '').toLowerCase().includes(normalizedFilter))
        : sortedProjects;

      const allStandalone = conversations.filter((c) => !c.workspace_id || c.workspace_id === 'default');
      const filteredStandalone = normalizedFilter
        ? allStandalone.filter((c) => (c.title || '').toLowerCase().includes(normalizedFilter))
        : allStandalone;

      return {
        projectWorkspaces: allProjects,
        filteredProjectWorkspaces: filteredProjects,
        standaloneConvs: filteredStandalone,
        pinnedStandalone: filteredStandalone.filter((c) => c.is_pinned),
        regularStandalone: filteredStandalone.filter((c) => !c.is_pinned),
      };
    }, [workspaces, conversations, activeWorkspaceId, normalizedFilter]);

  const handleStartRename = (conv: Conversation) => {
    setEditingId(conv.id);
    setEditingTitle(conv.title);
    setMenuOpenId(null);
  };

  const handleFinishRename = (id: string) => {
    if (editingTitle.trim()) {
      onRenameConv(id, editingTitle.trim());
    }
    setEditingId(null);
  };

  const toggleWorkspaceExpand = (wsId: string) => {
    setExpandedWorkspaceIds((prev) => ({
      ...prev,
      [wsId]: !prev[wsId],
    }));
  };

  // 唤起 Windows 原生文件夹选择对话框
  const handlePickNativeFolder = async () => {
    try {
      setIsSelectingNative(true);
      setWsErrorMsg('');
      const res = await apiFetch('/api/v1/workspaces/select-folder', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        if (data?.headless_remote) {
          const initialPath = data.suggested_root || '';
          setIsRemoteFolderMode(true);
          setMountPath(initialPath);
          const parts = String(initialPath).split(/[\\/]/).filter(Boolean);
          setMountName(parts[parts.length - 1] || '工作区目录');
          setIsMountingModalOpen(true);
        } else if (data?.selected_path) {
          setIsRemoteFolderMode(false);
          setMountPath(data.selected_path);
          const parts = String(data.selected_path).split(/[\\/]/).filter(Boolean);
          setMountName(parts[parts.length - 1] || '本地工程工作区');
          setIsMountingModalOpen(true);
        }
      }
    } catch (e: any) {
      setWsErrorMsg(e?.message || '调用文件夹选择器失败');
    } finally {
      setIsSelectingNative(false);
    }
  };

  const handleConfirmMount = async () => {
    if (!mountPath.trim() || !mountName.trim()) {
      setWsErrorMsg('请完整填写工作区名称与物理目录路径');
      return;
    }
    try {
      setWsErrorMsg('');
      const res = await apiFetch('/api/v1/workspaces', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: mountName.trim(),
          type: 'physical',
          root_path: mountPath.trim(),
          context_prompt: mountContext.trim() || undefined,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data?.id) {
          setIsMountingModalOpen(false);
          setMountPath('');
          setMountName('');
          setMountContext('');
          onRefreshWorkspaces();
          onSelectWorkspace(data.id);
        }
      } else {
        const err = await res.json().catch(() => ({}));
        setWsErrorMsg(err.detail || '挂载本地工作区失败');
      }
    } catch (e: any) {
      setWsErrorMsg(e?.message || '挂载本地工作区失败');
    }
  };

  const handleConfirmVirtual = async () => {
    if (!virtualName.trim()) {
      setWsErrorMsg('请输入工作区名称');
      return;
    }
    try {
      setWsErrorMsg('');
      const res = await apiFetch('/api/v1/workspaces', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: virtualName.trim(),
          type: 'virtual',
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data?.id) {
          setIsCreatingVirtual(false);
          setVirtualName('');
          onRefreshWorkspaces();
          onSelectWorkspace(data.id);
        }
      } else {
        const err = await res.json().catch(() => ({}));
        setWsErrorMsg(err.detail || '创建工作区失败');
      }
    } catch (e: any) {
      setWsErrorMsg(e?.message || '创建工作区失败');
    }
  };

  const handleDeleteWorkspace = async (ws: Workspace, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`确定解除挂载项目【${ws.name}】吗？\n\n安全提示：本地物理文件将完整保留，仅在 AgentHub 中解绑。`)) {
      return;
    }
    try {
      await apiFetch(`/api/v1/workspaces/${ws.id}`, { method: 'DELETE' });
      onRefreshWorkspaces();
      if (activeWorkspaceId === ws.id) {
        onSelectWorkspace('default');
      }
    } catch (err: any) {
      alert(err?.message || '解除挂载失败');
    }
  };

  // 底部账号状态点样式
  const accountDotClass = !statusPill
    ? 'bg-muted-foreground/40'
    : statusPill.status === 'exhausted' || statusPill.status === 'cooldown'
      ? 'bg-rose-500'
      : statusPill.is_warning
        ? 'bg-amber-500'
        : 'bg-emerald-500';

  // 快捷导航网格配置（统一克制语义色调）
  const secondaryHubs: {
    id: MainNavHub;
    label: string;
    title: string;
    icon: React.ReactNode;
    hasIndicator?: boolean;
    spanFull?: boolean;
    onClick?: () => void;
  }[] = [
    {
      id: 'agents',
      label: '智能体专家',
      title: '智能体 (专家装配与沙盒启动)',
      icon: <Bot className="w-3.5 h-3.5 shrink-0" />,
    },
    {
      id: 'skills',
      label: '技能与应用',
      title: '技能与应用 (Skills & MCP)',
      icon: <Puzzle className="w-3.5 h-3.5 shrink-0" />,
    },
    {
      id: 'memory',
      label: '记忆中枢',
      title: '记忆中枢 (长期记忆 · 事实双时间轴 · 行为账本 · 人设)',
      icon: <Brain className="w-3.5 h-3.5 shrink-0" />,
    },
    {
      id: 'library',
      label: '资产与库',
      title: '资产与库 (Library · 沉淀文档与媒体资产)',
      icon: <Library className="w-3.5 h-3.5 shrink-0" />,
    },
    {
      id: 'llmwiki',
      label: 'LLMwiki',
      title: 'LLMwiki (知识库挂载与训练中枢 · 企业微信微盘 / WebDAV / OSS)',
      icon: <Cloud className="w-3.5 h-3.5 shrink-0 text-sky-500" />,
    },
    {
      id: 'scheduled',
      label: '已安排调度',
      title: '已安排调度 (Triggers · 计划任务)',
      icon: <Clock className="w-3.5 h-3.5 shrink-0" />,
      hasIndicator: true,
    },
  ];

  return (
    <aside
      ref={sidebarRef}
      className={`bg-[var(--sidebar-bg)] border-r border-[var(--sidebar-border)] text-[var(--sidebar-fg)] flex flex-col h-full select-none transition-all duration-300 z-30 ${
        isCollapsed ? 'w-16' : 'w-[264px]'
      }`}
    >
      {/* ① 顶部 Brand 与紧凑单行操作条 */}
      <div className="px-3 pt-2.5 pb-2 border-b border-[var(--sidebar-border)] space-y-2">
        <div className="flex items-center justify-between">
          {!isCollapsed && (
            <div className="flex items-center space-x-1.5 pl-0.5 min-w-0">
              <img
                src="/icon.png"
                alt="AgentHub Logo"
                className="w-5 h-5 rounded-md object-contain shrink-0"
              />
              <span className="text-xs font-bold tracking-wider text-[var(--sidebar-section-fg)] font-mono">
                AGENT-HUB
              </span>
              {onOpenUpdateModal ? (
                <button
                  type="button"
                  onClick={onOpenUpdateModal}
                  className="text-[10px] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-item-active-fg)] font-mono transition-colors"
                  title="点击检查客户端版本更新"
                >
                  {appVersionTag}
                </button>
              ) : (
                <span className="text-[10px] text-[var(--sidebar-section-fg)] font-normal font-mono">
                  {appVersionTag}
                </span>
              )}
              {hasUpdate && onOpenUpdateModal && (
                <button
                  type="button"
                  onClick={onOpenUpdateModal}
                  className="px-1.5 py-0.5 rounded-full bg-[var(--sidebar-item-active)] border border-[var(--sidebar-item-active-border)] text-[9px] font-semibold text-[var(--sidebar-item-active-fg)] flex items-center space-x-1 shrink-0 transition-colors"
                  title={latestVersion ? `发现新版本 v${latestVersion}，点击查看更新日志并升级` : '发现新版本'}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-[var(--sidebar-indicator)] animate-pulse" />
                  <span>新版本</span>
                </button>
              )}
            </div>
          )}
          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="p-1.5 rounded-lg hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors ml-auto"
            title={isCollapsed ? '展开侧边栏 ([)' : '收起侧边栏 ([)'}
          >
            <Menu className="w-4 h-4" />
          </button>
        </div>

        {/* 单行双列紧凑操作条：+ 新对话 | 全局检索 Ctrl K */}
        {!isCollapsed ? (
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => onNewChat()}
              className="flex-1 flex items-center justify-center gap-1.5 h-8 px-2.5 rounded-xl bg-[var(--sidebar-item-active)] hover:brightness-110 text-[var(--sidebar-item-active-fg)] border border-[var(--sidebar-item-active-border)] text-xs font-semibold transition-all"
              title="在当前工作区发起新对话 / 新任务 (Ctrl+N)"
            >
              <Plus className="w-3.5 h-3.5 shrink-0" />
              <span>新对话</span>
            </button>
            <button
              onClick={onOpenSearch}
              className="flex items-center gap-1.5 h-8 px-2.5 rounded-xl bg-[var(--sidebar-pill-bg)] hover:bg-[var(--sidebar-item-hover)] border border-[var(--sidebar-pill-border)] text-xs text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors shrink-0"
              title="全局检索历史对话与记忆 (Ctrl+K)"
            >
              <Search className="w-3.5 h-3.5 shrink-0" />
              <kbd className="text-[10px] font-mono opacity-80">Ctrl K</kbd>
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-1.5">
            <button
              onClick={() => onNewChat()}
              className="w-9 h-9 rounded-xl flex items-center justify-center bg-[var(--sidebar-item-active)] text-[var(--sidebar-item-active-fg)] border border-[var(--sidebar-item-active-border)] transition-all"
              title="在当前工作区发起新对话 / 新任务 (Ctrl+N)"
            >
              <Plus className="w-4 h-4" />
            </button>
            <button
              onClick={onOpenSearch}
              className="w-9 h-9 rounded-xl flex items-center justify-center hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors"
              title="全文检索历史 (Ctrl+K)"
            >
              <Search className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>

      {/* ② 核心导航枢纽区：主入口置顶 + 2列紧凑快捷网格 */}
      <div className="px-2.5 py-2 border-b border-[var(--sidebar-border)] space-y-1.5">
        {/* 主入口：对话与任务 */}
        <button
          onClick={() => onSelectNavHub?.('chat')}
          className={`w-full flex items-center rounded-lg text-xs transition-colors ${
            activeNavHub === 'chat'
              ? 'bg-[var(--sidebar-item-active)] text-[var(--sidebar-item-active-fg)] font-semibold border-l-2 border-[var(--sidebar-indicator)] rounded-l-none'
              : 'hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)]'
          } ${isCollapsed ? 'justify-center p-2' : 'gap-2 px-2.5 py-1.5'}`}
          title="对话与任务"
        >
          <MessageSquare className="w-4 h-4 shrink-0" />
          {!isCollapsed && <span>对话与任务</span>}
        </button>

        {/* 其余高频枢纽：2 列紧凑网格（折叠态为垂直图标栏） */}
        {isCollapsed ? (
          <div className="flex flex-col items-center gap-1 pt-1 border-t border-[var(--sidebar-border)]">
            {secondaryHubs.map((hub) => {
              const isActive = activeNavHub === hub.id;
              return (
                <button
                  key={hub.id}
                  onClick={() => {
                    if (hub.onClick) {
                      hub.onClick();
                    } else {
                      onSelectNavHub?.(hub.id as MainNavHub);
                    }
                  }}
                  className={`relative w-9 h-9 rounded-xl flex items-center justify-center transition-colors ${
                    isActive
                      ? 'bg-[var(--sidebar-item-active)] text-[var(--sidebar-item-active-fg)]'
                      : 'hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)]'
                  }`}
                  title={hub.title}
                >
                  {hub.icon}
                  {hub.hasIndicator && (
                    <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-[var(--sidebar-indicator)]" />
                  )}
                </button>
              );
            })}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-1">
            {secondaryHubs.map((hub) => {
              const isActive = activeNavHub === hub.id;
              return (
                <button
                  key={hub.id}
                  onClick={() => {
                    if (hub.onClick) {
                      hub.onClick();
                    } else {
                      onSelectNavHub?.(hub.id as MainNavHub);
                    }
                  }}
                  className={`flex items-center justify-between px-2 py-1.5 rounded-lg text-[11px] transition-colors ${
                    hub.spanFull ? 'col-span-2' : ''
                  } ${
                    isActive
                      ? 'bg-[var(--sidebar-item-active)] text-[var(--sidebar-item-active-fg)] font-semibold'
                      : 'hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)]'
                  }`}
                  title={hub.title}
                >
                  <span className="flex items-center gap-1.5 min-w-0">
                    {hub.icon}
                    <span className="truncate">{hub.label}</span>
                  </span>
                  {hub.hasIndicator && (
                    <span
                      className="w-1.5 h-1.5 rounded-full bg-[var(--sidebar-indicator)] shrink-0"
                      title="后台调度守护就绪"
                    />
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* ③ 中部滚动区：「沙盒与项目」折叠树 + 「对话」独立列表 */}
      <div className="flex-1 overflow-y-auto px-2.5 py-2 space-y-3">
        {/* 分组一：沙盒与项目 (对应非 default 工作区) */}
        <div className="space-y-1">
          {!isCollapsed && (
            <div className="px-1 py-0.5 flex items-center justify-between text-[11px] font-semibold text-[var(--sidebar-section-fg)]">
              <span className="flex items-center gap-1.5">
                <span>沙盒与项目 ({projectWorkspaces.length})</span>
              </span>
              <div className="flex items-center gap-0.5">
                <button
                  type="button"
                  onClick={() => {
                    setIsFilterOpen((prev) => {
                      const next = !prev;
                      if (!next) setFilterQuery('');
                      else setTimeout(() => filterInputRef.current?.focus(), 0);
                      return next;
                    });
                  }}
                  className={`p-1 rounded transition-colors ${
                    isFilterOpen || filterQuery
                      ? 'bg-[var(--sidebar-item-active)] text-[var(--sidebar-item-active-fg)]'
                      : 'hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)]'
                  }`}
                  title="快捷过滤沙盒、项目与对话"
                >
                  <Search className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={handlePickNativeFolder}
                  disabled={isSelectingNative}
                  className="p-1 rounded hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-item-active-fg)] transition-colors"
                  title="挂载本地已有工程目录"
                >
                  <FolderPlus className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setIsCreatingVirtual(true)}
                  className="p-1 rounded hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors"
                  title="新建托管隔离沙盒"
                >
                  <Plus className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}

          {/* 一键展开快捷过滤搜索框 */}
          {!isCollapsed && isFilterOpen && (
            <div className="relative px-0.5 pb-1">
              <Search className="w-3 h-3 text-[var(--sidebar-section-fg)] absolute left-2.5 top-2 pointer-events-none" />
              <input
                ref={filterInputRef}
                type="text"
                value={filterQuery}
                onChange={(e) => setFilterQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') {
                    setFilterQuery('');
                    setIsFilterOpen(false);
                  }
                }}
                placeholder="按名称过滤沙盒、项目或对话..."
                className="w-full h-7 pl-7 pr-6 rounded-lg bg-[var(--sidebar-pill-bg)] border border-[var(--sidebar-pill-border)] focus:border-[var(--sidebar-item-active-border)] text-[11px] text-[var(--sidebar-fg)] placeholder:text-[var(--sidebar-section-fg)] outline-none transition-colors"
              />
              {filterQuery && (
                <button
                  type="button"
                  onClick={() => setFilterQuery('')}
                  className="absolute right-2 top-1.5 p-0.5 rounded hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)]"
                  title="清空过滤"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
          )}

          {filteredProjectWorkspaces.length > 0 ? (
            <div className="space-y-0.5">
              {filteredProjectWorkspaces.map((ws) => {
                const wsConvs = conversations.filter((c) => c.workspace_id === ws.id);
                const isWsActive = activeWorkspaceId === ws.id;
                const isExpanded = Boolean(expandedWorkspaceIds[ws.id]);
                const isSandbox = ws.type !== 'physical' || (ws.name || '').startsWith('[沙盒]');
                const displayName = (ws.name || '').replace(/^\[沙盒\]\s*/, '');
                const hasPinnedConv = wsConvs.some((c) => c.is_pinned);

                if (isCollapsed) {
                  return (
                    <button
                      key={ws.id}
                      onClick={() => {
                        onSelectWorkspace(ws.id);
                        onSelectNavHub?.('chat');
                      }}
                      className={`w-9 h-9 mx-auto rounded-xl flex items-center justify-center transition-colors relative ${
                        isWsActive
                          ? 'bg-[var(--sidebar-item-active)] text-[var(--sidebar-item-active-fg)] border border-[var(--sidebar-item-active-border)]'
                          : 'hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)]'
                      }`}
                      title={`项目: ${ws.name} (${wsConvs.length} 个会话)`}
                    >
                      {ws.type === 'physical' ? (
                        <FolderGit2 className="w-4 h-4" />
                      ) : (
                        <Box className="w-4 h-4" />
                      )}
                    </button>
                  );
                }

                return (
                  <div key={ws.id} className="space-y-0.5">
                    <div
                      onClick={() => {
                        onSelectWorkspace(ws.id);
                        toggleWorkspaceExpand(ws.id);
                      }}
                      className={`group/ws flex items-center justify-between px-2 py-1.5 rounded-lg cursor-pointer text-xs transition-colors ${
                        isWsActive
                          ? 'bg-[var(--sidebar-item-active)] text-[var(--sidebar-item-active-fg)] font-semibold'
                          : 'hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-fg)]/90'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 min-w-0 flex-1 pr-1">
                        <span className="text-[var(--sidebar-section-fg)] shrink-0">
                          {isExpanded ? (
                            <ChevronDown className="w-3.5 h-3.5" />
                          ) : (
                            <ChevronRight className="w-3.5 h-3.5" />
                          )}
                        </span>
                        <div className="relative shrink-0">
                          {ws.type === 'physical' ? (
                            <Folder
                              className={`w-3.5 h-3.5 ${
                                isWsActive ? 'text-[var(--sidebar-item-active-fg)]' : 'text-[var(--sidebar-section-fg)]'
                              }`}
                            />
                          ) : (
                            <Box
                              className={`w-3.5 h-3.5 ${
                                isWsActive ? 'text-[var(--sidebar-item-active-fg)]' : 'text-[var(--sidebar-section-fg)]'
                              }`}
                            />
                          )}
                          {/* 仅在路径不可访问时展示异常警告点，移除泛滥的绿色圆点 */}
                          {ws.is_path_accessible === false && (
                            <span
                              className="absolute -bottom-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-rose-500"
                              title="工作区路径不可访问"
                            />
                          )}
                        </div>
                        <span className="truncate">{displayName}</span>
                        {isSandbox && (
                          <span className="px-1 py-0.2 rounded text-[9px] bg-[var(--sidebar-tag-bg)] text-[var(--sidebar-tag-fg)] shrink-0 font-normal">
                            沙盒
                          </span>
                        )}
                        {hasPinnedConv && !isWsActive && (
                          <Pin className="w-2.5 h-2.5 text-amber-500/80 shrink-0 rotate-45" />
                        )}
                      </div>

                      <div className="flex items-center gap-0.5 opacity-0 group-hover/ws:opacity-100 transition-opacity shrink-0">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpandedWorkspaceIds((prev) => ({ ...prev, [ws.id]: true }));
                            onNewChat(ws.id);
                          }}
                          className="p-1 rounded hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-item-active-fg)]"
                          title="在此项目下新建对话"
                        >
                          <Plus className="w-3 h-3" />
                        </button>
                        {onOpenWorkspaceContract && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenWorkspaceContract(ws);
                            }}
                            className="p-1 rounded hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)]"
                            title="编辑项目背景契约 WORKSPACE.md"
                          >
                            <FileCode2 className="w-3 h-3" />
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={(e) => handleDeleteWorkspace(ws, e)}
                          className="p-1 rounded hover:bg-rose-500/15 text-[var(--sidebar-section-fg)] hover:text-rose-400"
                          title="解除挂载（保留本地文件）"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    </div>

                    {isExpanded && (
                      <div className="pl-3.5 border-l border-[var(--sidebar-border)] ml-3 space-y-0.5">
                        {wsConvs.length > 0 ? (
                          wsConvs.map((conv) => (
                            <ConversationRow
                              key={conv.id}
                              conv={conv}
                              workspaces={workspaces}
                              isActive={activeConvId === conv.id}
                              isCollapsed={false}
                              isEditing={editingId === conv.id}
                              editingTitle={editingTitle}
                              setEditingTitle={setEditingTitle}
                              onSelect={() => {
                                if (conv.workspace_id && conv.workspace_id !== activeWorkspaceId) {
                                  onSelectWorkspace(conv.workspace_id);
                                }
                                onSelectConv(conv.id);
                              }}
                              onFinishRename={() => handleFinishRename(conv.id)}
                              onStartRename={() => handleStartRename(conv)}
                              onTogglePin={() => onTogglePin(conv.id, !conv.is_pinned)}
                              onDelete={() => onDeleteConv(conv.id)}
                              onMoveWorkspace={(targetWsId) => onMoveConvWorkspace?.(conv.id, targetWsId)}
                              menuOpenId={menuOpenId}
                              setMenuOpenId={setMenuOpenId}
                              movingConvId={movingConvId}
                              setMovingConvId={setMovingConvId}
                            />
                          ))
                        ) : (
                          <div className="px-2 py-1 text-[11px] text-[var(--sidebar-section-fg)] opacity-70">
                            暂无对话
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            !isCollapsed && (
              <div className="px-2.5 py-2 rounded-lg border border-dashed border-[var(--sidebar-border)] text-[11px] text-[var(--sidebar-section-fg)] flex items-center justify-between">
                <span>{normalizedFilter ? '无匹配沙盒或项目' : '尚未挂载项目'}</span>
                {normalizedFilter ? (
                  <button
                    type="button"
                    onClick={() => setFilterQuery('')}
                    className="text-[var(--sidebar-item-active-fg)] hover:underline"
                  >
                    清空
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={handlePickNativeFolder}
                    className="text-[var(--sidebar-item-active-fg)] hover:underline"
                  >
                    挂载目录
                  </button>
                )}
              </div>
            )
          )}
        </div>

        {/* 分组二：独立对话（default 默认工作区） */}
        <div className="space-y-1">
          {!isCollapsed && (
            <div className="px-1 py-0.5 flex items-center justify-between text-[11px] font-semibold text-[var(--sidebar-section-fg)]">
              <span>对话 ({standaloneConvs.length})</span>
              <div className="flex items-center gap-0.5">
                {onCleanupDuplicates && standaloneConvs.length > 3 && (
                  <button
                    type="button"
                    onClick={onCleanupDuplicates}
                    className="text-[10px] font-normal px-1.5 py-0.5 rounded hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-item-active-fg)] transition-colors"
                    title="智能去重并清理空白会话"
                  >
                    去重
                  </button>
                )}
                <button
                  type="button"
                  onClick={onOpenSync}
                  className="p-1 rounded hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors"
                  title="数据导入与导出"
                >
                  <UploadCloud className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (activeWorkspaceId !== 'default') {
                      onSelectWorkspace('default');
                    }
                    onNewChat('default');
                  }}
                  className="p-1 rounded hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-item-active-fg)] transition-colors"
                  title="新建独立对话"
                >
                  <Plus className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}

          {pinnedStandalone.length > 0 && (
            <div className="space-y-0.5">
              {!isCollapsed && (
                <div className="px-2 py-0.5 text-[10px] font-semibold text-amber-500/90 flex items-center gap-1">
                  <Pin className="w-2.5 h-2.5 rotate-45" />
                  <span>置顶</span>
                </div>
              )}
              {pinnedStandalone.map((conv) => (
                <ConversationRow
                  key={conv.id}
                  conv={conv}
                  workspaces={workspaces}
                  isActive={activeConvId === conv.id}
                  isCollapsed={isCollapsed}
                  isEditing={editingId === conv.id}
                  editingTitle={editingTitle}
                  setEditingTitle={setEditingTitle}
                  onSelect={() => {
                    if (activeWorkspaceId !== 'default') {
                      onSelectWorkspace('default');
                    }
                    onSelectConv(conv.id);
                  }}
                  onFinishRename={() => handleFinishRename(conv.id)}
                  onStartRename={() => handleStartRename(conv)}
                  onTogglePin={() => onTogglePin(conv.id, !conv.is_pinned)}
                  onDelete={() => onDeleteConv(conv.id)}
                  onMoveWorkspace={(targetWsId) => onMoveConvWorkspace?.(conv.id, targetWsId)}
                  menuOpenId={menuOpenId}
                  setMenuOpenId={setMenuOpenId}
                  movingConvId={movingConvId}
                  setMovingConvId={setMovingConvId}
                />
              ))}
            </div>
          )}

          <div className="space-y-0.5">
            {regularStandalone.map((conv) => (
              <ConversationRow
                key={conv.id}
                conv={conv}
                workspaces={workspaces}
                isActive={activeConvId === conv.id}
                isCollapsed={isCollapsed}
                isEditing={editingId === conv.id}
                editingTitle={editingTitle}
                setEditingTitle={setEditingTitle}
                onSelect={() => {
                  if (activeWorkspaceId !== 'default') {
                    onSelectWorkspace('default');
                  }
                  onSelectConv(conv.id);
                }}
                onFinishRename={() => handleFinishRename(conv.id)}
                onStartRename={() => handleStartRename(conv)}
                onTogglePin={() => onTogglePin(conv.id, !conv.is_pinned)}
                onDelete={() => onDeleteConv(conv.id)}
                onMoveWorkspace={(targetWsId) => onMoveConvWorkspace?.(conv.id, targetWsId)}
                menuOpenId={menuOpenId}
                setMenuOpenId={setMenuOpenId}
                movingConvId={movingConvId}
                setMovingConvId={setMovingConvId}
              />
            ))}

            {standaloneConvs.length === 0 && !isCollapsed && (
              <div className="px-3 py-3 text-center text-xs text-[var(--sidebar-section-fg)] border border-dashed border-[var(--sidebar-border)] rounded-xl space-y-1">
                <p>{normalizedFilter ? '无匹配对话' : '暂无独立对话'}</p>
                {!normalizedFilter && (
                  <button
                    onClick={() => onNewChat('default')}
                    className="text-[var(--sidebar-item-active-fg)] hover:underline text-xs"
                  >
                    点击新建对话
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ④ 底部轻量 surface 账号卡片 + 均匀三联工具条 */}
      <div className="px-3 pt-2 pb-3 border-t border-[var(--sidebar-border)] space-y-2">
        {/* 账号算力状态卡片（轻量 surface 风格，无生硬粗边框） */}
        <button
          type="button"
          onClick={onOpenAccounts}
          className={`w-full flex items-center justify-between rounded-xl bg-[var(--sidebar-card-bg)] hover:bg-[var(--sidebar-card-hover)] transition-colors ${
            isCollapsed ? 'p-2 justify-center' : 'px-2.5 py-2'
          }`}
          title="统一管理算力账号、OAuth 与配额池"
        >
          <div className="flex items-center gap-2 min-w-0">
            <span className={`w-2 h-2 rounded-full shrink-0 ${accountDotClass}`} />
            {!isCollapsed && (
              <div className="text-left min-w-0">
                <div className="text-xs font-semibold text-[var(--sidebar-fg)] truncate flex items-center gap-1.5">
                  <span className="truncate">{statusPill?.active_account_name || '算力账号池'}</span>
                  {statusPill?.auth_type === 'oauth_google_one' && (
                    <span className="text-[9px] px-1 py-0.2 rounded bg-[var(--sidebar-item-active)] text-[var(--sidebar-item-active-fg)] font-mono">
                      PRO
                    </span>
                  )}
                </div>
                <div className="text-[10px] text-[var(--sidebar-section-fg)] truncate">
                  {statusPill
                    ? `今日 ${statusPill.today_used} 次${statusPill.is_relayed ? ' · 热接力中' : ''}`
                    : '点击配置账号 / 本地引擎'}
                </div>
              </div>
            )}
          </div>
          {!isCollapsed && <ChevronRight className="w-3.5 h-3.5 text-[var(--sidebar-section-fg)] shrink-0" />}
        </button>

        {/* 三联轻工具条：用量分析 · 系统监控 · 设置 */}
        {isCollapsed ? (
          <div className="flex flex-col items-center gap-1 pt-1">
            {onOpenTokenStats && (
              <button
                type="button"
                onClick={onOpenTokenStats}
                className="p-2 rounded-xl hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors"
                title="用量分析 (Token 消耗统计)"
              >
                <BarChart3 className="w-4 h-4" />
              </button>
            )}
            {onOpenSystemMonitor && (
              <button
                type="button"
                onClick={onOpenSystemMonitor}
                className="relative p-2 rounded-xl hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors"
                title="系统监控与性能"
              >
                <Activity className="w-4 h-4" />
                {pendingProposalCount > 0 && (
                  <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-amber-500" />
                )}
              </button>
            )}
            <button
              type="button"
              onClick={onOpenSettings}
              className="relative p-2 rounded-xl hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors"
              title="偏好设置"
            >
              <Sliders className="w-4 h-4" />
              {hasUpdate && (
                <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-[var(--sidebar-indicator)] animate-pulse" />
              )}
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-1">
            <button
              type="button"
              onClick={() => onOpenTokenStats?.()}
              className="flex items-center justify-center gap-1 px-1.5 py-1.5 rounded-lg hover:bg-[var(--sidebar-item-hover)] text-[11px] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors"
              title="查看 Token 消耗与脱水节省统计"
            >
              <BarChart3 className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate font-medium">用量分析</span>
            </button>

            <button
              type="button"
              onClick={() => onOpenSystemMonitor?.()}
              className="relative flex items-center justify-center gap-1 px-1.5 py-1.5 rounded-lg hover:bg-[var(--sidebar-item-hover)] text-[11px] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors"
              title="系统监控与优化提案"
            >
              <Activity className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate font-medium">系统监控</span>
              {pendingProposalCount > 0 && (
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
              )}
            </button>

            <button
              type="button"
              onClick={onOpenSettings}
              className="relative flex items-center justify-center gap-1 px-1.5 py-1.5 rounded-lg hover:bg-[var(--sidebar-item-hover)] text-[11px] text-[var(--sidebar-section-fg)] hover:text-[var(--sidebar-fg)] transition-colors"
              title="系统偏好、外观与模型路由设置"
            >
              <Sliders className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate font-medium">设置</span>
              {hasUpdate && (
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--sidebar-indicator)] animate-pulse shrink-0" />
              )}
            </button>
          </div>
        )}
      </div>

      {/* 挂载本地工程目录弹窗 */}
      {isMountingModalOpen && (
        <div 
          onClick={() => setIsMountingModalOpen(false)}
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 animate-fade-in"
        >
          <div 
            onClick={(e) => e.stopPropagation()}
            className="bg-surface-card border border-border w-full max-w-md rounded-2xl p-5 shadow-2xl space-y-4 animate-scale-in"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <FolderPlus className="w-5 h-5 text-foreground" />
                <h3 className="text-sm font-bold text-foreground">挂载本地已有工程项目</h3>
              </div>
              <button
                onClick={() => setIsMountingModalOpen(false)}
                className="p-1 rounded-lg hover:bg-surface-100 text-muted-foreground hover:text-foreground"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-xs font-semibold text-foreground block mb-1">物理绝对路径</label>
                <input
                  type="text"
                  value={mountPath}
                  onChange={(e) => setMountPath(e.target.value)}
                  className="w-full bg-surface-200 border border-border rounded-xl px-3 py-2 text-xs font-mono text-foreground outline-none focus:border-foreground/40"
                />
              </div>

              {isRemoteFolderMode && (
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground block mb-1">
                    服务器 / 容器目录浏览（点击进入并自动选中路径）
                  </label>
                  <ServerFolderBrowser
                    currentPath={mountPath}
                    onSelectPath={(selected) => {
                      setMountPath(selected);
                      const parts = selected.split(/[\\/]/).filter(Boolean);
                      setMountName(parts[parts.length - 1] || '工作区目录');
                    }}
                  />
                </div>
              )}

              <div>
                <label className="text-xs font-semibold text-foreground block mb-1">项目名称</label>
                <input
                  type="text"
                  value={mountName}
                  onChange={(e) => setMountName(e.target.value)}
                  placeholder="例如: 病例研究 / 全栈开发"
                  className="w-full bg-surface-200 border border-border rounded-xl px-3 py-2 text-xs text-foreground outline-none focus:border-foreground/40"
                />
              </div>

              <div>
                <label className="text-xs font-semibold text-foreground block mb-1">
                  专属背景契约 (可选 · WORKSPACE.md)
                </label>
                <textarea
                  value={mountContext}
                  onChange={(e) => setMountContext(e.target.value)}
                  placeholder="可在此输入该项目的专属规范或指令。"
                  rows={3}
                  className="w-full bg-surface-200 border border-border rounded-xl p-2.5 text-xs text-foreground outline-none focus:border-foreground/40 resize-none"
                />
              </div>

              {wsErrorMsg && (
                <div className="p-2.5 rounded-xl bg-surface-100 border border-border text-rose-400 text-xs flex items-center space-x-2">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>{wsErrorMsg}</span>
                </div>
              )}
            </div>

            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                onClick={() => setIsMountingModalOpen(false)}
                className="px-4 py-2 rounded-xl hover:bg-surface-100 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleConfirmMount}
                className="px-4 py-2 rounded-xl bg-foreground text-background hover:opacity-90 text-xs font-semibold transition-opacity shadow-sm"
              >
                立即挂载
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 新建托管沙盒工作区弹窗 */}
      {isCreatingVirtual && (
        <div 
          onClick={() => setIsCreatingVirtual(false)}
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 animate-fade-in"
        >
          <div 
            onClick={(e) => e.stopPropagation()}
            className="bg-surface-card border border-border w-full max-w-sm rounded-2xl p-5 shadow-2xl space-y-4 animate-scale-in"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Box className="w-5 h-5 text-foreground" />
                <h3 className="text-sm font-bold text-foreground">新建沙盒托管项目</h3>
              </div>
              <button
                onClick={() => setIsCreatingVirtual(false)}
                className="p-1 rounded-lg hover:bg-surface-100 text-muted-foreground hover:text-foreground"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-xs font-semibold text-foreground block mb-1">项目名称</label>
                <input
                  type="text"
                  value={virtualName}
                  onChange={(e) => setVirtualName(e.target.value)}
                  placeholder="例如: 文献整理 / 临时沙盒"
                  className="w-full bg-surface-200 border border-border rounded-xl px-3 py-2 text-xs text-foreground outline-none focus:border-foreground/40"
                  autoFocus
                />
              </div>

              {wsErrorMsg && (
                <div className="p-2.5 rounded-xl bg-surface-100 border border-border text-rose-400 text-xs flex items-center space-x-2">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>{wsErrorMsg}</span>
                </div>
              )}
            </div>

            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                onClick={() => setIsCreatingVirtual(false)}
                className="px-4 py-2 rounded-xl hover:bg-surface-100 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleConfirmVirtual}
                className="px-4 py-2 rounded-xl bg-foreground text-background hover:opacity-90 text-xs font-semibold transition-opacity shadow-sm"
              >
                确认创建
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

function ConversationRow({
  conv,
  workspaces,
  isActive,
  isCollapsed,
  isEditing,
  editingTitle,
  setEditingTitle,
  onSelect,
  onFinishRename,
  onStartRename,
  onTogglePin,
  onDelete,
  onMoveWorkspace,
  menuOpenId,
  setMenuOpenId,
  movingConvId,
  setMovingConvId,
}: {
  conv: Conversation;
  workspaces: Workspace[];
  isActive: boolean;
  isCollapsed: boolean;
  isEditing: boolean;
  editingTitle: string;
  setEditingTitle: (v: string) => void;
  onSelect: () => void;
  onFinishRename: () => void;
  onStartRename: () => void;
  onTogglePin: () => void;
  onDelete: () => void;
  onMoveWorkspace?: (targetWsId: string) => void;
  menuOpenId: string | null;
  setMenuOpenId: (id: string | null) => void;
  movingConvId: string | null;
  setMovingConvId: (id: string | null) => void;
}) {
  const isMenuOpen = menuOpenId === conv.id;
  const isMoving = movingConvId === conv.id;
  const isBranch = isBranchedConversation(conv);
  const relAge = formatRelativeAge(conv.updated_at || conv.created_at);

  if (isCollapsed) {
    return (
      <button
        onClick={onSelect}
        className={`w-9 h-9 mx-auto rounded-xl flex items-center justify-center transition-colors ${
          isActive
            ? 'bg-[var(--sidebar-item-active)] text-[var(--sidebar-item-active-fg)] border border-[var(--sidebar-item-active-border)]'
            : 'hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-section-fg)]'
        }`}
        title={conv.title}
      >
        {isBranch ? <GitBranch className="w-4 h-4" /> : <MessageSquare className="w-4 h-4" />}
      </button>
    );
  }

  return (
    <div
      onClick={onSelect}
      className={`group relative flex items-center justify-between px-2.5 py-1.5 rounded-xl cursor-pointer text-xs transition-all ${
        isActive
          ? 'bg-[var(--sidebar-item-active)] text-[var(--sidebar-item-active-fg)] font-semibold border border-[var(--sidebar-item-active-border)]'
          : 'hover:bg-[var(--sidebar-item-hover)] text-[var(--sidebar-fg)] border border-transparent'
      }`}
    >
      <div className="flex items-center space-x-1.5 truncate flex-1 pr-1 min-w-0">
        {Boolean(conv.is_pinned) && <Pin className="w-3 h-3 text-amber-500 shrink-0 rotate-45" />}
        {isBranch && (
          <GitBranch
            className="w-3 h-3 text-emerald-500 shrink-0"
            aria-label="多版本分支会话"
          />
        )}
        {isEditing ? (
          <input
            type="text"
            value={editingTitle}
            onChange={(e) => setEditingTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onFinishRename();
              if (e.key === 'Escape') setEditingTitle(conv.title);
            }}
            onBlur={onFinishRename}
            autoFocus
            className="w-full bg-surface-200 border border-emerald-500 rounded px-1.5 py-0.5 text-xs text-foreground outline-none"
          />
        ) : (
          <span className="truncate" title={conv.title}>
            {conv.title}
          </span>
        )}
      </div>

      {/* 右侧相对时间 & 悬浮菜单 */}
      {!isEditing && (
        <div className="relative flex items-center shrink-0">
          {relAge && !isMenuOpen && (
            <span className="text-[10px] text-muted-foreground/75 font-mono group-hover:hidden pl-1">
              {relAge}
            </span>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpenId(isMenuOpen ? null : conv.id);
              setMovingConvId(null);
            }}
            className={`p-0.5 rounded-full hover:bg-surface-200 transition-opacity ${
              isMenuOpen ? 'opacity-100 block' : 'hidden group-hover:block'
            }`}
          >
            <MoreVertical className="w-3.5 h-3.5 text-muted-foreground" />
          </button>

          {isMenuOpen && (
            <div
              className="absolute right-0 top-full mt-1 w-44 rounded-xl glass-modal p-1 shadow-2xl border border-border z-40 animate-scale-in text-xs font-normal"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                onClick={() => onStartRename()}
                className="w-full flex items-center space-x-2 px-2.5 py-1.5 rounded-lg hover:bg-foreground/5 text-foreground transition-colors"
              >
                <Edit2 className="w-3.5 h-3.5" />
                <span>重命名</span>
              </button>
              <button
                onClick={() => {
                  onTogglePin();
                  setMenuOpenId(null);
                }}
                className="w-full flex items-center space-x-2 px-2.5 py-1.5 rounded-lg hover:bg-foreground/5 text-foreground transition-colors"
              >
                <Pin className="w-3.5 h-3.5" />
                <span>{conv.is_pinned ? '取消置顶' : '置顶会话'}</span>
              </button>

              {/* 移动到其他工作区 */}
              <div className="relative">
                <button
                  onClick={() => setMovingConvId(isMoving ? null : conv.id)}
                  className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg hover:bg-foreground/5 text-foreground transition-colors"
                >
                  <div className="flex items-center space-x-2">
                    <FolderInput className="w-3.5 h-3.5 text-sky-400" />
                    <span>移至项目...</span>
                  </div>
                  <span className="text-[10px] text-muted-foreground">▶</span>
                </button>

                {isMoving && (
                  <div className="mt-1 p-1 bg-surface-200/90 rounded-lg border border-border space-y-0.5 max-h-40 overflow-y-auto">
                    {workspaces.map((w) => (
                      <button
                        key={w.id}
                        onClick={() => {
                          onMoveWorkspace?.(w.id);
                          setMenuOpenId(null);
                          setMovingConvId(null);
                        }}
                        className={`w-full text-left px-2 py-1 rounded text-[11px] truncate ${
                          conv.workspace_id === w.id
                            ? 'text-emerald-400 font-bold'
                            : 'text-foreground hover:bg-surface-100'
                        }`}
                      >
                        {w.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="my-1 border-t border-border/50" />

              <button
                onClick={() => {
                  onDelete();
                  setMenuOpenId(null);
                }}
                className="w-full flex items-center space-x-2 px-2.5 py-1.5 rounded-lg hover:bg-rose-500/20 text-rose-500 transition-colors"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>删除</span>
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
