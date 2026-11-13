'use client';

import React, { useState, useEffect, useRef } from 'react';
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
  FolderInput,
  Brain,
  Folder,
  FolderGit2,
  FolderPlus,
  Box,
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

export type MainNavHub = 'chat' | 'library' | 'scheduled' | 'skills';

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
  onOpenSync: () => void;
  onOpenSettings: () => void;
  onOpenTokenStats?: () => void;
  onOpenSystemMonitor?: () => void;
  onOpenWorkspaceContract?: (workspace: Workspace) => void;
  onCleanupDuplicates?: () => void;
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
  onOpenSync,
  onOpenSettings,
  onOpenTokenStats,
  onOpenSystemMonitor,
  onOpenWorkspaceContract,
  onCleanupDuplicates,
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
  const [isCreatingVirtual, setIsCreatingVirtual] = useState(false);
  const [virtualName, setVirtualName] = useState('');
  const [wsErrorMsg, setWsErrorMsg] = useState('');
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

  // 划分项目工作区（非 default）与独立对话（default）
  const projectWorkspaces = workspaces.filter((w) => w.id !== 'default');
  const standaloneConvs = conversations.filter((c) => !c.workspace_id || c.workspace_id === 'default');
  const pinnedStandalone = standaloneConvs.filter((c) => c.is_pinned);
  const regularStandalone = standaloneConvs.filter((c) => !c.is_pinned);

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
        if (data?.selected_path) {
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

  return (
    <aside
      ref={sidebarRef}
      className={`bg-surface-sidebar border-r border-border flex flex-col h-full select-none transition-all duration-300 z-30 ${
        isCollapsed ? 'w-16' : 'w-[264px]'
      }`}
    >
      {/* ① 顶部 Brand 与动作区 */}
      <div className="p-2.5 border-b border-border space-y-2">
        <div className="flex items-center justify-between">
          {!isCollapsed && (
            <span className="text-xs font-bold tracking-wider text-muted-foreground font-mono flex items-center space-x-1.5 pl-1">
              <img
                src="/icon.png"
                alt="AgentHub Logo"
                className="w-5 h-5 rounded-md object-contain shrink-0"
              />
              <span>AGENT-HUB</span>
              <span className="text-[10px] text-muted-foreground/70 font-normal">{appVersionTag}</span>
            </span>
          )}
          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="p-1.5 rounded-lg hover:bg-surface-100 text-muted-foreground hover:text-foreground transition-colors ml-auto"
            title={isCollapsed ? '展开侧边栏 ([)' : '收起侧边栏 ([)'}
          >
            <Menu className="w-4 h-4" />
          </button>
        </div>

        {/* 发起新对话 / 任务 */}
        <button
          onClick={() => onNewChat()}
          className={`flex items-center rounded-xl bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-600 dark:text-emerald-400 text-xs font-semibold transition-all border border-emerald-500/30 shadow-sm ${
            isCollapsed ? 'justify-center p-2.5 w-10 h-10 mx-auto' : 'space-x-2 w-full px-3 py-2'
          }`}
          title="在当前工作区发起新对话 / 新任务 (Ctrl+N)"
        >
          <Plus className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
          {!isCollapsed && <span>新对话 / 任务</span>}
        </button>

        {/* 全文检索 Ctrl + K */}
        {!isCollapsed ? (
          <button
            onClick={onOpenSearch}
            className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-xl bg-surface-200/50 hover:bg-surface-200 border border-border/50 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <div className="flex items-center space-x-2 min-w-0">
              <Search className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
              <span className="truncate">全文检索历史...</span>
            </div>
            <kbd className="px-1.5 py-0.5 rounded bg-surface-100 border border-border text-[10px] font-mono text-muted-foreground shrink-0">
              Ctrl K
            </kbd>
          </button>
        ) : (
          <button
            onClick={onOpenSearch}
            className="w-10 h-10 mx-auto rounded-xl flex items-center justify-center hover:bg-surface-100 text-muted-foreground hover:text-foreground transition-colors"
            title="全文检索历史 (Ctrl+K)"
          >
            <Search className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* ② 核心功能枢纽区 (保留通用工作台命名) */}
      <div className="p-2 border-b border-border space-y-0.5">
        <button
          onClick={() => onSelectNavHub?.('chat')}
          className={`w-full flex items-center rounded-lg text-xs transition-colors ${
            activeNavHub === 'chat'
              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-semibold border-l-2 border-emerald-500 rounded-l-none'
              : 'hover:bg-surface-100 text-muted-foreground hover:text-foreground'
          } ${isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-1.5'}`}
          title="对话与任务"
        >
          <MessageSquare className="w-4 h-4 shrink-0" />
          {!isCollapsed && <span>对话与任务</span>}
        </button>

        <button
          onClick={() => onSelectNavHub?.('library')}
          className={`w-full flex items-center justify-between rounded-lg text-xs transition-colors ${
            activeNavHub === 'library'
              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-semibold border-l-2 border-emerald-500 rounded-l-none'
              : 'hover:bg-surface-100 text-muted-foreground hover:text-foreground'
          } ${isCollapsed ? 'justify-center p-2' : 'px-3 py-1.5'}`}
          title="库 (Library · 沉淀文档与媒体资产)"
        >
          <div className="flex items-center space-x-2.5">
            <Library className="w-4 h-4 shrink-0 text-sky-400" />
            {!isCollapsed && <span>库 (Library)</span>}
          </div>
          {!isCollapsed && (
            <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-surface-200 text-muted-foreground font-mono">
              资产
            </span>
          )}
        </button>

        {onOpenMemoryHub && (
          <button
            onClick={onOpenMemoryHub}
            className={`w-full flex items-center rounded-lg text-xs transition-colors hover:bg-surface-100 text-muted-foreground hover:text-foreground ${
              isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-1.5'
            }`}
            title="记忆中枢 (长期记忆 · 事实双时间轴 · 行为账本 · 人设)"
          >
            <Brain className="w-4 h-4 shrink-0 text-emerald-500" />
            {!isCollapsed && <span>记忆中枢</span>}
          </button>
        )}

        <button
          onClick={() => onSelectNavHub?.('scheduled')}
          className={`w-full flex items-center justify-between rounded-lg text-xs transition-colors ${
            activeNavHub === 'scheduled'
              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-semibold border-l-2 border-emerald-500 rounded-l-none'
              : 'hover:bg-surface-100 text-muted-foreground hover:text-foreground'
          } ${isCollapsed ? 'justify-center p-2' : 'px-3 py-1.5'}`}
          title="已安排 (Scheduled · 自动化定时任务)"
        >
          <div className="flex items-center space-x-2.5">
            <Clock className="w-4 h-4 shrink-0 text-amber-400" />
            {!isCollapsed && <span>已安排</span>}
          </div>
          {!isCollapsed && (
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" title="后台调度守护就绪" />
          )}
        </button>

        <button
          onClick={() => onSelectNavHub?.('skills')}
          className={`w-full flex items-center rounded-lg text-xs transition-colors ${
            activeNavHub === 'skills'
              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-semibold border-l-2 border-emerald-500 rounded-l-none'
              : 'hover:bg-surface-100 text-muted-foreground hover:text-foreground'
          } ${isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-1.5'}`}
          title="技能与应用 (Skills & MCP)"
        >
          <Puzzle className="w-4 h-4 shrink-0 text-indigo-400" />
          {!isCollapsed && <span>技能与应用</span>}
        </button>
      </div>

      {/* ③ 中部滚动区：「项目」折叠树 + 「对话」独立列表 */}
      <div className="flex-1 overflow-y-auto p-2 space-y-3">
        {/* 分组一：项目 (对应非 default 工作区) */}
        <div className="space-y-1">
          {!isCollapsed && (
            <div className="px-1.5 py-0.5 flex items-center justify-between text-[11px] font-bold text-muted-foreground">
              <span>项目 ({projectWorkspaces.length})</span>
              <div className="flex items-center space-x-1">
                <button
                  type="button"
                  onClick={handlePickNativeFolder}
                  disabled={isSelectingNative}
                  className="p-1 rounded hover:bg-surface-200 text-muted-foreground hover:text-emerald-500 transition-colors"
                  title="挂载本地已有工程目录"
                >
                  <FolderPlus className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setIsCreatingVirtual(true)}
                  className="p-1 rounded hover:bg-surface-200 text-muted-foreground hover:text-sky-400 transition-colors"
                  title="新建沙盒托管项目"
                >
                  <Plus className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}

          {projectWorkspaces.length > 0 ? (
            <div className="space-y-0.5">
              {projectWorkspaces.map((ws) => {
                const wsConvs = conversations.filter((c) => c.workspace_id === ws.id);
                const isWsActive = activeWorkspaceId === ws.id;
                const isExpanded = Boolean(expandedWorkspaceIds[ws.id]);

                if (isCollapsed) {
                  return (
                    <button
                      key={ws.id}
                      onClick={() => {
                        onSelectWorkspace(ws.id);
                        onSelectNavHub?.('chat');
                      }}
                      className={`w-10 h-10 mx-auto rounded-xl flex items-center justify-center transition-colors relative ${
                        isWsActive
                          ? 'bg-emerald-500/20 text-emerald-500 border border-emerald-500/30'
                          : 'hover:bg-surface-100 text-muted-foreground'
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
                          ? 'bg-surface-200/80 text-foreground font-semibold'
                          : 'hover:bg-surface-100 text-foreground/90'
                      }`}
                    >
                      <div className="flex items-center space-x-1.5 min-w-0 flex-1 pr-1">
                        <span className="text-muted-foreground shrink-0">
                          {isExpanded ? (
                            <ChevronDown className="w-3.5 h-3.5" />
                          ) : (
                            <ChevronRight className="w-3.5 h-3.5" />
                          )}
                        </span>
                        <div className="relative shrink-0">
                          {ws.type === 'physical' ? (
                            <Folder className="w-3.5 h-3.5 text-emerald-500" />
                          ) : (
                            <Box className="w-3.5 h-3.5 text-sky-400" />
                          )}
                          <span
                            className={`absolute -bottom-0.5 -right-0.5 w-1.5 h-1.5 rounded-full ${
                              ws.is_path_accessible === false ? 'bg-rose-500' : 'bg-emerald-500'
                            }`}
                          />
                        </div>
                        <span className="truncate">{ws.name}</span>
                      </div>

                      <div className="flex items-center space-x-0.5 opacity-0 group-hover/ws:opacity-100 transition-opacity shrink-0">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpandedWorkspaceIds((prev) => ({ ...prev, [ws.id]: true }));
                            onNewChat(ws.id);
                          }}
                          className="p-1 rounded hover:bg-surface-200 text-muted-foreground hover:text-emerald-500"
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
                            className="p-1 rounded hover:bg-surface-200 text-muted-foreground hover:text-sky-400"
                            title="编辑项目背景契约 WORKSPACE.md"
                          >
                            <FileCode2 className="w-3 h-3" />
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={(e) => handleDeleteWorkspace(ws, e)}
                          className="p-1 rounded hover:bg-rose-500/20 text-muted-foreground hover:text-rose-400"
                          title="解除挂载（保留本地文件）"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    </div>

                    {isExpanded && (
                      <div className="pl-4 border-l border-border/60 ml-3.5 space-y-0.5">
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
                          <div className="px-2 py-1 text-[11px] text-muted-foreground/70">
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
              <div className="px-2 py-2 rounded-lg border border-dashed border-border/60 text-[11px] text-muted-foreground flex items-center justify-between">
                <span>尚未挂载项目</span>
                <button
                  type="button"
                  onClick={handlePickNativeFolder}
                  className="text-emerald-500 hover:underline"
                >
                  挂载目录
                </button>
              </div>
            )
          )}
        </div>

        {/* 分组二：独立对话（default 默认工作区） */}
        <div className="space-y-1">
          {!isCollapsed && (
            <div className="px-1.5 py-0.5 flex items-center justify-between text-[11px] font-bold text-muted-foreground">
              <span>对话 ({standaloneConvs.length})</span>
              <div className="flex items-center space-x-1">
                {onCleanupDuplicates && standaloneConvs.length > 3 && (
                  <button
                    type="button"
                    onClick={onCleanupDuplicates}
                    className="text-[10px] font-normal px-1.5 py-0.5 rounded hover:bg-surface-200 text-muted-foreground hover:text-emerald-500 transition-colors"
                    title="智能去重并清理空白会话"
                  >
                    去重
                  </button>
                )}
                <button
                  type="button"
                  onClick={onOpenSync}
                  className="p-1 rounded hover:bg-surface-200 text-muted-foreground hover:text-foreground transition-colors"
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
                  className="p-1 rounded hover:bg-surface-200 text-muted-foreground hover:text-emerald-500 transition-colors"
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
                <div className="px-2 py-0.5 text-[10px] font-semibold text-amber-500/90 flex items-center space-x-1">
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
              <div className="px-3 py-4 text-center text-xs text-muted-foreground border border-dashed border-border/60 rounded-xl space-y-1">
                <p>暂无独立对话</p>
                <button
                  onClick={() => onNewChat('default')}
                  className="text-emerald-500 hover:underline text-xs"
                >
                  点击新建对话
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ④ 底部账号与算力卡片 + 运维三联栏 */}
      <div className="p-2 border-t border-border space-y-1.5">
        {/* 账号算力状态卡片 */}
        <button
          type="button"
          onClick={onOpenAccounts}
          className={`w-full flex items-center justify-between rounded-xl border border-border/80 bg-surface-100/70 hover:bg-surface-200/80 transition-colors ${
            isCollapsed ? 'p-2 justify-center' : 'px-2.5 py-2'
          }`}
          title="统一管理算力账号、OAuth 与配额池"
        >
          <div className="flex items-center space-x-2 min-w-0">
            <span className={`w-2 h-2 rounded-full shrink-0 ${accountDotClass}`} />
            {!isCollapsed && (
              <div className="text-left min-w-0">
                <div className="text-xs font-semibold text-foreground truncate flex items-center space-x-1.5">
                  <span className="truncate">{statusPill?.active_account_name || '算力账号池'}</span>
                  {statusPill?.auth_type === 'oauth_google_one' && (
                    <span className="text-[9px] px-1 py-0.2 rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 font-mono">
                      PRO
                    </span>
                  )}
                </div>
                <div className="text-[10px] text-muted-foreground truncate">
                  {statusPill
                    ? `今日 ${statusPill.today_used} 次${statusPill.is_relayed ? ' · 热接力中' : ''}`
                    : '点击配置账号 / 本地引擎'}
                </div>
              </div>
            )}
          </div>
          {!isCollapsed && <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
        </button>

        {/* 三联轻工具条：用量分析 · 系统监控 · 设置 */}
        {isCollapsed ? (
          <div className="flex flex-col items-center space-y-1">
            {onOpenTokenStats && (
              <button
                onClick={onOpenTokenStats}
                className="p-2 rounded-lg hover:bg-surface-100 text-muted-foreground hover:text-foreground transition-colors"
                title="用量分析"
              >
                <BarChart3 className="w-4 h-4" />
              </button>
            )}
            <button
              onClick={onOpenSettings}
              className="p-2 rounded-lg hover:bg-surface-100 text-muted-foreground hover:text-foreground transition-colors"
              title="设置"
            >
              <Sliders className="w-4 h-4" />
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-1 pt-0.5">
            <button
              type="button"
              onClick={() => onOpenTokenStats?.()}
              className="flex items-center justify-center space-x-1 px-1.5 py-1.5 rounded-lg hover:bg-surface-100 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
              title="查看 Token 消耗与脱水节省统计"
            >
              <BarChart3 className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate">用量分析</span>
            </button>

            <button
              type="button"
              onClick={() => onOpenSystemMonitor?.()}
              className="relative flex items-center justify-center space-x-1 px-1.5 py-1.5 rounded-lg hover:bg-surface-100 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
              title="系统监控与优化提案"
            >
              <Activity className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate">系统监控</span>
              {pendingProposalCount > 0 && (
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
              )}
            </button>

            <button
              type="button"
              onClick={onOpenSettings}
              className="flex items-center justify-center space-x-1 px-1.5 py-1.5 rounded-lg hover:bg-surface-100 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
              title="系统偏好、外观与模型路由设置"
            >
              <Sliders className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate">设置</span>
            </button>
          </div>
        )}
      </div>

      {/* 挂载本地工程目录弹窗 */}
      {isMountingModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
          <div className="bg-surface-modal border border-border w-full max-w-md rounded-2xl p-5 shadow-2xl space-y-4 animate-scale-in">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <FolderPlus className="w-5 h-5 text-emerald-400" />
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
                  className="w-full bg-surface-200/80 border border-border rounded-xl px-3 py-2 text-xs font-mono text-foreground outline-none"
                />
              </div>

              <div>
                <label className="text-xs font-semibold text-foreground block mb-1">项目名称</label>
                <input
                  type="text"
                  value={mountName}
                  onChange={(e) => setMountName(e.target.value)}
                  placeholder="例如: 病例研究 / 全栈开发"
                  className="w-full bg-surface-200/80 border border-border rounded-xl px-3 py-2 text-xs text-foreground outline-none focus:border-emerald-500"
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
                  className="w-full bg-surface-200/80 border border-border rounded-xl p-2.5 text-xs text-foreground outline-none focus:border-emerald-500 resize-none"
                />
              </div>

              {wsErrorMsg && (
                <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs flex items-center space-x-2">
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
                className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition-all shadow-md"
              >
                立即挂载
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 新建托管沙盒工作区弹窗 */}
      {isCreatingVirtual && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
          <div className="bg-surface-modal border border-border w-full max-w-sm rounded-2xl p-5 shadow-2xl space-y-4 animate-scale-in">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Box className="w-5 h-5 text-sky-400" />
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
                  className="w-full bg-surface-200/80 border border-border rounded-xl px-3 py-2 text-xs text-foreground outline-none focus:border-sky-500"
                  autoFocus
                />
              </div>

              {wsErrorMsg && (
                <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs flex items-center space-x-2">
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
                className="px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold transition-all shadow-md"
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
        className={`w-10 h-10 mx-auto rounded-xl flex items-center justify-center transition-colors ${
          isActive
            ? 'bg-emerald-600 text-white'
            : 'hover:bg-surface-100 text-muted-foreground'
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
          ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 font-semibold border border-emerald-500/35 shadow-xs'
          : 'hover:bg-surface-100 text-foreground border border-transparent'
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
