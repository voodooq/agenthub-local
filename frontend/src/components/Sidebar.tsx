'use client';

import React, { useState, useEffect } from 'react';
import {
  Menu,
  Plus,
  MessageSquare,
  Pin,
  Search,
  Key,
  UploadCloud,
  Trash2,
  MoreVertical,
  Edit2,
  Sliders,
  Sparkles,
  Library,
  Clock,
  Puzzle,
  FolderInput,
} from 'lucide-react';
import { Conversation, Workspace } from '@/types';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';

export type MainNavHub = 'chat' | 'library' | 'scheduled' | 'skills';

interface SidebarProps {
  conversations: Conversation[];
  activeConvId: string | null;
  workspaces: Workspace[];
  activeWorkspaceId: string;
  activeNavHub?: MainNavHub;
  onSelectNavHub?: (hub: MainNavHub) => void;
  onSelectWorkspace: (id: string) => void;
  onRefreshWorkspaces: () => void;
  onSelectConv: (id: string) => void;
  onNewChat: () => void;
  onDeleteConv: (id: string) => void;
  onTogglePin: (id: string, isPinned: boolean) => void;
  onRenameConv: (id: string, newTitle: string) => void;
  onMoveConvWorkspace?: (convId: string, targetWorkspaceId: string) => void;
  onOpenSearch: () => void;
  onOpenAccounts: () => void;
  onOpenSync: () => void;
  onOpenSettings: () => void;
  onCleanupDuplicates?: () => void;
}

export function Sidebar({
  conversations,
  activeConvId,
  workspaces,
  activeWorkspaceId,
  activeNavHub = 'chat',
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
  onOpenAccounts,
  onOpenSync,
  onOpenSettings,
  onCleanupDuplicates,
}: SidebarProps) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [movingConvId, setMovingConvId] = useState<string | null>(null);

  // 监听按键 '[' 快捷折叠/展开侧边栏
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // 避免在文本输入框中按 [ 时触发折叠
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

  // 当前工作区下的会话（排除不属于当前工作区的会话）
  const currentWsConvs = conversations.filter((c) => {
    if (!c.workspace_id) return activeWorkspaceId === 'default';
    return c.workspace_id === activeWorkspaceId;
  });

  const pinnedList = currentWsConvs.filter((c) => c.is_pinned);
  const regularList = currentWsConvs.filter((c) => !c.is_pinned);

  const activeWorkspace = workspaces.find((w) => w.id === activeWorkspaceId) || workspaces[0];

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

  return (
    <aside
      className={`bg-surface-sidebar border-r border-border flex flex-col h-full select-none transition-all duration-300 z-30 ${
        isCollapsed ? 'w-16' : 'w-[260px]'
      }`}
    >
      {/* 顶部 Brand 与工作区切换器 */}
      <div className="p-2.5 border-b border-border space-y-2">
        <div className="flex items-center justify-between">
          {!isCollapsed && (
            <span className="text-xs font-bold tracking-wider text-muted-foreground font-mono flex items-center space-x-1.5 pl-1">
              <Sparkles className="w-3.5 h-3.5 text-emerald-500" />
              <span>AGENT-HUB</span>
              <span className="text-[10px] text-zinc-500 font-normal">v1.1.0</span>
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

        {/* 通用用户自定义工作区切换器 */}
        <WorkspaceSwitcher
          workspaces={workspaces}
          activeWorkspaceId={activeWorkspaceId}
          isCollapsed={isCollapsed}
          onSelectWorkspace={onSelectWorkspace}
          onRefreshWorkspaces={onRefreshWorkspaces}
        />

        {/* 发起新对话按钮 (薄荷绿胶囊) */}
        <button
          onClick={onNewChat}
          className={`flex items-center rounded-xl bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-600 dark:text-emerald-400 text-xs font-semibold transition-all border border-emerald-500/30 shadow-sm ${
            isCollapsed ? 'justify-center p-2.5 w-10 h-10 mx-auto' : 'space-x-2 w-full px-3 py-2'
          }`}
          title="在当前工作区发起新对话 / 新任务 (Ctrl+N)"
        >
          <Plus className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
          {!isCollapsed && <span>新对话 / 任务</span>}
        </button>
      </div>

      {/* 核心功能枢纽导航 (Core Hubs) */}
      <div className="p-2 border-b border-border space-y-0.5">
        <button
          onClick={() => onSelectNavHub?.('chat')}
          className={`w-full flex items-center rounded-lg text-xs transition-colors ${
            activeNavHub === 'chat'
              ? 'bg-emerald-500/10 text-emerald-400 font-semibold border-l-2 border-emerald-500 rounded-l-none'
              : 'hover:bg-surface-100 text-muted-foreground hover:text-foreground'
          } ${isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-2'}`}
          title="对话与任务主工作台"
        >
          <MessageSquare className="w-4 h-4 shrink-0" />
          {!isCollapsed && <span>对话与任务</span>}
        </button>

        <button
          onClick={() => onSelectNavHub?.('library')}
          className={`w-full flex items-center justify-between rounded-lg text-xs transition-colors ${
            activeNavHub === 'library'
              ? 'bg-emerald-500/10 text-emerald-400 font-semibold border-l-2 border-emerald-500 rounded-l-none'
              : 'hover:bg-surface-100 text-muted-foreground hover:text-foreground'
          } ${isCollapsed ? 'justify-center p-2' : 'px-3 py-2'}`}
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

        <button
          onClick={() => onSelectNavHub?.('scheduled')}
          className={`w-full flex items-center justify-between rounded-lg text-xs transition-colors ${
            activeNavHub === 'scheduled'
              ? 'bg-emerald-500/10 text-emerald-400 font-semibold border-l-2 border-emerald-500 rounded-l-none'
              : 'hover:bg-surface-100 text-muted-foreground hover:text-foreground'
          } ${isCollapsed ? 'justify-center p-2' : 'px-3 py-2'}`}
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
              ? 'bg-emerald-500/10 text-emerald-400 font-semibold border-l-2 border-emerald-500 rounded-l-none'
              : 'hover:bg-surface-100 text-muted-foreground hover:text-foreground'
          } ${isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-2'}`}
          title="技能与应用市场 (Skills & MCP)"
        >
          <Puzzle className="w-4 h-4 shrink-0 text-indigo-400" />
          {!isCollapsed && <span>技能与应用</span>}
        </button>
      </div>

      {/* 工作区专属会话历史流 */}
      <div className="flex-1 overflow-y-auto p-2 space-y-3">
        {/* 全文检索 Ctrl + K */}
        {!isCollapsed && (
          <button
            onClick={onOpenSearch}
            className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-xl bg-surface-200/50 hover:bg-surface-200 border border-border/50 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <div className="flex items-center space-x-2">
              <Search className="w-3.5 h-3.5 text-emerald-500" />
              <span className="truncate">全文检索历史...</span>
            </div>
            <kbd className="px-1.5 py-0.5 rounded bg-surface-100 border border-border text-[10px] font-mono text-muted-foreground shrink-0">
              Ctrl K
            </kbd>
          </button>
        )}

        {/* 固定会话 */}
        {pinnedList.length > 0 && (
          <div className="space-y-1">
            {!isCollapsed && (
              <div className="px-2 py-0.5 text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center space-x-1">
                <Pin className="w-3 h-3 rotate-45 text-amber-500" />
                <span>置顶会话</span>
              </div>
            )}
            {pinnedList.map((conv) => (
              <ConversationRow
                key={conv.id}
                conv={conv}
                workspaces={workspaces}
                isActive={activeConvId === conv.id}
                isCollapsed={isCollapsed}
                isEditing={editingId === conv.id}
                editingTitle={editingTitle}
                setEditingTitle={setEditingTitle}
                onSelect={() => onSelectConv(conv.id)}
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

        {/* 工作区最近记录 */}
        <div className="space-y-0.5">
          {!isCollapsed && (
            <div className="px-2 py-1 text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center justify-between">
              <span className="truncate">历史记录 ({regularList.length})</span>
              {onCleanupDuplicates && regularList.length > 3 && (
                <button
                  type="button"
                  onClick={onCleanupDuplicates}
                  className="text-[10px] text-muted-foreground hover:text-emerald-500 font-normal normal-case hover:bg-surface-100 px-1 py-0.5 rounded transition-colors"
                  title="智能去重并清理空白会话"
                >
                  去重
                </button>
              )}
            </div>
          )}
          {regularList.map((conv) => (
            <ConversationRow
              key={conv.id}
              conv={conv}
              workspaces={workspaces}
              isActive={activeConvId === conv.id}
              isCollapsed={isCollapsed}
              isEditing={editingId === conv.id}
              editingTitle={editingTitle}
              setEditingTitle={setEditingTitle}
              onSelect={() => onSelectConv(conv.id)}
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

          {currentWsConvs.length === 0 && !isCollapsed && (
            <div className="px-3 py-6 text-center text-xs text-muted-foreground border border-dashed border-border/60 rounded-xl space-y-1">
              <p>当前工作区暂无会话</p>
              <button
                onClick={onNewChat}
                className="text-emerald-500 hover:underline text-xs"
              >
                点击发起新对话
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 底部工具箱 */}
      <div className="p-2 border-t border-border space-y-0.5">
        <button
          onClick={onOpenAccounts}
          className={`w-full flex items-center rounded-lg hover:bg-surface-100 text-foreground text-xs font-normal transition-colors group ${
            isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-2'
          }`}
          title="统一管理算力账号、OAuth 与额度"
        >
          <Key className="w-4 h-4 text-muted-foreground group-hover:text-foreground shrink-0 transition-colors" />
          {!isCollapsed && <span>账号与算力池</span>}
        </button>

        <button
          onClick={onOpenSettings}
          className={`w-full flex items-center rounded-lg hover:bg-surface-100 text-foreground text-xs font-normal transition-colors group ${
            isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-2'
          }`}
          title="系统偏好与模型路由设置"
        >
          <Sliders className="w-4 h-4 text-muted-foreground group-hover:text-foreground shrink-0 transition-colors" />
          {!isCollapsed && <span>设置与首选项</span>}
        </button>

        <button
          onClick={onOpenSync}
          className={`w-full flex items-center rounded-lg hover:bg-surface-100 text-foreground text-xs font-normal transition-colors group ${
            isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-2'
          }`}
          title="数据备份与跨平台迁移"
        >
          <UploadCloud className="w-4 h-4 text-muted-foreground group-hover:text-foreground shrink-0 transition-colors" />
          {!isCollapsed && <span>数据导入导出</span>}
        </button>
      </div>
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
        <MessageSquare className="w-4 h-4" />
      </button>
    );
  }

  return (
    <div
      onClick={onSelect}
      className={`group relative flex items-center justify-between px-2.5 py-1.5 rounded-xl cursor-pointer text-xs transition-all ${
        isActive
          ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 font-semibold border border-emerald-500/30'
          : 'hover:bg-surface-100 text-foreground'
      }`}
    >
      <div className="flex items-center space-x-2 truncate flex-1 pr-1">
        <MessageSquare className="w-3.5 h-3.5 shrink-0 opacity-70" />
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
          <span className="truncate">{conv.title}</span>
        )}
      </div>

      {/* 悬浮菜单 */}
      {!isEditing && (
        <div className="relative">
          <button
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpenId(isMenuOpen ? null : conv.id);
              setMovingConvId(null);
            }}
            className={`p-1 rounded-full hover:bg-surface-200 transition-opacity ${
              isMenuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
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
                    <span>移至工作区...</span>
                  </div>
                  <span className="text-[10px] text-muted-foreground">▶</span>
                </button>

                {isMoving && (
                  <div className="mt-1 p-1 bg-surface-200/90 rounded-lg border border-border space-y-0.5">
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
