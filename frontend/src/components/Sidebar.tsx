'use client';

import React, { useState } from 'react';
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
} from 'lucide-react';
import { Conversation } from '@/types';

interface SidebarProps {
  conversations: Conversation[];
  activeConvId: string | null;
  onSelectConv: (id: string) => void;
  onNewChat: () => void;
  onDeleteConv: (id: string) => void;
  onTogglePin: (id: string, isPinned: boolean) => void;
  onRenameConv: (id: string, newTitle: string) => void;
  onOpenSearch: () => void;
  onOpenAccounts: () => void;
  onOpenSync: () => void;
  onOpenSettings: () => void;
  onCleanupDuplicates?: () => void;
}

export function Sidebar({
  conversations,
  activeConvId,
  onSelectConv,
  onNewChat,
  onDeleteConv,
  onTogglePin,
  onRenameConv,
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

  const pinnedList = conversations.filter((c) => c.is_pinned);
  const regularList = conversations.filter((c) => !c.is_pinned);

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
        isCollapsed ? 'w-16' : 'w-72'
      }`}
    >
      {/* 顶部 Brand 与收起控制 */}
      <div className="p-3 border-b border-border space-y-2.5">
        <div className="flex items-center justify-between">
          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="p-2 rounded-xl hover:bg-surface-100 text-muted-foreground hover:text-foreground transition-colors"
            title={isCollapsed ? '展开侧边栏' : '收起侧边栏'}
          >
            <Menu className="w-5 h-5" />
          </button>
          {!isCollapsed && (
            <span className="text-xs font-bold tracking-wider text-muted-foreground font-mono flex items-center space-x-1">
              <Sparkles className="w-3.5 h-3.5 text-emerald-500" />
              <span>GEMINI-HUB</span>
            </span>
          )}
        </div>

        {/* 发起新对话按钮 (Strata 极简薄荷胶囊) */}
        <button
          onClick={onNewChat}
          className={`flex items-center rounded-2xl bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-600 dark:text-emerald-400 text-xs font-semibold transition-all border border-emerald-500/30 shadow-sm ${
            isCollapsed ? 'justify-center p-2.5 w-10 h-10 mx-auto' : 'space-x-2.5 w-full px-4 py-3'
          }`}
          title="发起新对话"
        >
          <Plus className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
          {!isCollapsed && <span>新对话</span>}
        </button>

        {/* 全文检索 Ctrl + K */}
        {!isCollapsed && (
          <button
            onClick={onOpenSearch}
            className="w-full flex items-center justify-between px-3.5 py-2 rounded-xl bg-surface-200 hover:bg-surface-50 border border-border/60 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <div className="flex items-center space-x-2">
              <Search className="w-3.5 h-3.5 text-emerald-500" />
              <span>全文检索...</span>
            </div>
            <kbd className="px-1.5 py-0.5 rounded bg-surface-100 border border-border text-[10px] font-mono text-muted-foreground">
              Ctrl K
            </kbd>
          </button>
        )}
      </div>

      {/* 会话列表 */}
      <div className="flex-1 overflow-y-auto p-2 space-y-4">
        {/* 固定会话 */}
        {pinnedList.length > 0 && (
          <div className="space-y-1">
            {!isCollapsed && (
              <div className="px-3 py-1 text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center space-x-1">
                <Pin className="w-3 h-3 rotate-45 text-amber-500" />
                <span>已固定会话</span>
              </div>
            )}
            {pinnedList.map((conv) => (
              <ConversationRow
                key={conv.id}
                conv={conv}
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
                menuOpenId={menuOpenId}
                setMenuOpenId={setMenuOpenId}
              />
            ))}
          </div>
        )}

        {/* 最近会话 */}
        <div className="space-y-0.5">
          {!isCollapsed && (
            <div className="px-3 py-1.5 text-[11px] font-bold text-muted-foreground uppercase tracking-wider flex items-center justify-between">
              <span>最近记录 ({regularList.length})</span>
              {onCleanupDuplicates && regularList.length > 3 && (
                <button
                  type="button"
                  onClick={onCleanupDuplicates}
                  className="text-[10px] text-muted-foreground hover:text-emerald-500 font-normal normal-case hover:bg-surface-100 px-1.5 py-0.5 rounded transition-colors"
                  title="智能去重并清理空白会话"
                >
                  去重清理
                </button>
              )}
            </div>
          )}
          {regularList.map((conv) => (
            <ConversationRow
              key={conv.id}
              conv={conv}
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
              menuOpenId={menuOpenId}
              setMenuOpenId={setMenuOpenId}
            />
          ))}
        </div>
      </div>

      {/* 底部工具箱 */}
      <div className="p-2 border-t border-border space-y-0.5">
        <button
          onClick={onOpenAccounts}
          className={`w-full flex items-center rounded-lg hover:bg-surface-100 text-foreground text-xs font-normal transition-colors group ${
            isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-2'
          }`}
          title="统一管理 Google OAuth、本地 CLI、多源大模型 API 与额度监控"
        >
          <Key className="w-4 h-4 text-muted-foreground group-hover:text-foreground shrink-0 transition-colors" />
          {!isCollapsed && <span>账号管理与额度</span>}
        </button>

        <button
          onClick={onOpenSettings}
          className={`w-full flex items-center rounded-lg hover:bg-surface-100 text-foreground text-xs font-normal transition-colors group ${
            isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-2'
          }`}
          title="模型测试与系统设置"
        >
          <Sliders className="w-4 h-4 text-muted-foreground group-hover:text-foreground shrink-0 transition-colors" />
          {!isCollapsed && <span>设置与模型测试</span>}
        </button>

        <button
          onClick={onOpenSync}
          className={`w-full flex items-center rounded-lg hover:bg-surface-100 text-foreground text-xs font-normal transition-colors group ${
            isCollapsed ? 'justify-center p-2' : 'space-x-2.5 px-3 py-2'
          }`}
          title="ChatGPT / Claude / GEMINI.md 四步数据迁移与导入"
        >
          <UploadCloud className="w-4 h-4 text-muted-foreground group-hover:text-foreground shrink-0 transition-colors" />
          {!isCollapsed && <span>数据迁移与导入</span>}
        </button>
      </div>
    </aside>
  );
}

function ConversationRow({
  conv,
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
  menuOpenId,
  setMenuOpenId,
}: {
  conv: Conversation;
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
  menuOpenId: string | null;
  setMenuOpenId: (id: string | null) => void;
}) {
  const isMenuOpen = menuOpenId === conv.id;

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
      className={`group relative flex items-center justify-between px-3 py-2 rounded-xl cursor-pointer text-xs transition-all ${
        isActive
          ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 font-semibold border border-emerald-500/30'
          : 'hover:bg-surface-100 text-foreground'
      }`}
    >
      <div className="flex items-center space-x-2 truncate flex-1 pr-2">
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
            }}
            className={`p-1 rounded-full hover:bg-surface-200 transition-opacity ${
              isMenuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
            }`}
          >
            <MoreVertical className="w-3.5 h-3.5 text-muted-foreground" />
          </button>

          {isMenuOpen && (
            <div
              className="absolute right-0 top-full mt-1 w-36 rounded-xl glass-modal p-1 shadow-2xl border border-border z-40 animate-scale-in text-xs font-normal"
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
                <span>{conv.is_pinned ? '取消固定' : '固定会话'}</span>
              </button>
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
