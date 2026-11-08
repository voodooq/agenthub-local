'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
  Folder,
  FolderPlus,
  FolderGit2,
  Box,
  ChevronsUpDown,
  Check,
  Search,
  Plus,
  X,
  ExternalLink,
  Trash2,
  AlertCircle,
} from 'lucide-react';
import { Workspace } from '@/types';
import { apiFetch } from '@/utils/apiClient';

interface WorkspaceSwitcherProps {
  workspaces: Workspace[];
  activeWorkspaceId: string;
  isCollapsed: boolean;
  onSelectWorkspace: (id: string) => void;
  onRefreshWorkspaces: () => void;
}

export function WorkspaceSwitcher({
  workspaces,
  activeWorkspaceId,
  isCollapsed,
  onSelectWorkspace,
  onRefreshWorkspaces,
}: WorkspaceSwitcherProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [isMountingModalOpen, setIsMountingModalOpen] = useState(false);
  const [mountPath, setMountPath] = useState('');
  const [mountName, setMountName] = useState('');
  const [mountContext, setMountContext] = useState('');
  const [isSelectingNative, setIsSelectingNative] = useState(false);
  const [isCreatingVirtual, setIsCreatingVirtual] = useState(false);
  const [virtualName, setVirtualName] = useState('');
  const [errorMessage, setErrorMessage] = useState('');
  const popoverRef = useRef<HTMLDivElement>(null);

  const activeWorkspace = workspaces.find((w) => w.id === activeWorkspaceId) || workspaces[0];

  // 监听点击外部关闭下拉面板
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  // 过滤工作区
  const filteredWorkspaces = workspaces.filter((w) => {
    const q = searchQuery.toLowerCase().trim();
    if (!q) return true;
    return w.name.toLowerCase().includes(q) || w.root_path.toLowerCase().includes(q);
  });

  // 唤起 Windows 原生文件夹选择对话框
  const handlePickNativeFolder = async () => {
    try {
      setIsSelectingNative(true);
      setErrorMessage('');
      const res = await apiFetch('/api/v1/workspaces/select-folder', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        if (data && data.selected_path) {
          setMountPath(data.selected_path);
          // 默认用文件夹名称作为工作区名字
          const parts = data.selected_path.split(/[\\/]/).filter(Boolean);
          const defaultName = parts[parts.length - 1] || '本地工程工作区';
          setMountName(defaultName);
          setIsMountingModalOpen(true);
          setIsOpen(false);
        }
      }
    } catch (e: any) {
      setErrorMessage(e?.message || '调用 Windows 文件夹选择器失败');
    } finally {
      setIsSelectingNative(false);
    }
  };

  // 确认挂载本地物理工作区
  const handleConfirmMount = async () => {
    if (!mountPath.trim() || !mountName.trim()) {
      setErrorMessage('请完整填写工作区名称与物理目录路径');
      return;
    }
    try {
      setErrorMessage('');
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
        if (data && data.id) {
          setIsMountingModalOpen(false);
          setMountPath('');
          setMountName('');
          setMountContext('');
          onRefreshWorkspaces();
          onSelectWorkspace(data.id);
        }
      } else {
        const err = await res.json().catch(() => ({}));
        setErrorMessage(err.detail || '挂载本地工作区失败');
      }
    } catch (e: any) {
      setErrorMessage(e?.message || '挂载本地工作区失败');
    }
  };

  // 确认创建托管虚拟沙盒工作区
  const handleConfirmVirtual = async () => {
    if (!virtualName.trim()) {
      setErrorMessage('请输入工作区名称');
      return;
    }
    try {
      setErrorMessage('');
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
        if (data && data.id) {
          setIsCreatingVirtual(false);
          setVirtualName('');
          onRefreshWorkspaces();
          onSelectWorkspace(data.id);
        }
      } else {
        const err = await res.json().catch(() => ({}));
        setErrorMessage(err.detail || '创建工作区失败');
      }
    } catch (e: any) {
      setErrorMessage(e?.message || '创建工作区失败');
    }
  };

  // 解除工作区挂载
  const handleDeleteWorkspace = async (ws: Workspace, e: React.MouseEvent) => {
    e.stopPropagation();
    if (ws.id === 'default') {
      alert('默认工作区受系统保护，不可删除');
      return;
    }
    if (!confirm(`确定解除挂载工作区【${ws.name}】吗？\n\n安全提示：本地物理文件将 100% 完整保留，仅在 AgentHub 中解绑。`)) {
      return;
    }
    try {
      await apiFetch(`/api/v1/workspaces/${ws.id}`, { method: 'DELETE' });
      onRefreshWorkspaces();
      if (activeWorkspaceId === ws.id) {
        onSelectWorkspace('default');
      }
    } catch (e: any) {
      alert(e?.message || '解除挂载失败');
    }
  };

  if (isCollapsed) {
    return (
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-10 h-10 mx-auto rounded-xl flex items-center justify-center transition-all bg-surface-100 hover:bg-surface-200 border border-white/5 text-foreground relative group"
        title={`当前工作区: ${activeWorkspace?.name || '默认工作区'}`}
      >
        {activeWorkspace?.type === 'physical' ? (
          <FolderGit2 className="w-5 h-5 text-emerald-500" />
        ) : (
          <Box className="w-5 h-5 text-sky-400" />
        )}
        <span className="absolute bottom-1 right-1 w-2 h-2 rounded-full bg-emerald-500 ring-2 ring-black" />
      </button>
    );
  }

  return (
    <div className="relative" ref={popoverRef}>
      {/* 触发卡片 */}
      <div
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center justify-between p-2 rounded-xl bg-surface-100 hover:bg-surface-200/80 border border-white/5 cursor-pointer transition-all group"
      >
        <div className="flex items-center space-x-2.5 truncate flex-1 min-w-0 pr-1">
          <div className="relative shrink-0">
            {activeWorkspace?.type === 'physical' ? (
              <div className="w-7 h-7 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center">
                <FolderGit2 className="w-4 h-4 text-emerald-400" />
              </div>
            ) : (
              <div className="w-7 h-7 rounded-lg bg-sky-500/10 border border-sky-500/20 flex items-center justify-center">
                <Box className="w-4 h-4 text-sky-400" />
              </div>
            )}
            <span
              className={`absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full ring-2 ring-surface-sidebar ${
                activeWorkspace?.is_path_accessible ? 'bg-emerald-500' : 'bg-rose-500'
              }`}
            />
          </div>

          <div className="truncate flex-1 min-w-0 text-left">
            <div className="text-xs font-bold text-foreground truncate flex items-center space-x-1.5">
              <span className="truncate">{activeWorkspace?.name || '默认工作区'}</span>
              <span className="text-[10px] px-1 py-0.2 rounded bg-surface-200 text-muted-foreground shrink-0 font-normal">
                {activeWorkspace?.type === 'physical' ? '物理' : '沙盒'}
              </span>
            </div>
            <div
              className="text-[10px] text-muted-foreground font-mono truncate"
              title={activeWorkspace?.root_path}
            >
              {activeWorkspace?.root_path || 'data/workspaces/default'}
            </div>
          </div>
        </div>

        <ChevronsUpDown className="w-4 h-4 text-muted-foreground group-hover:text-foreground shrink-0 transition-colors ml-1" />
      </div>

      {/* 下拉浮层面板 */}
      {isOpen && (
        <div className="absolute left-0 top-full mt-2 w-72 rounded-2xl glass-modal p-2 shadow-2xl border border-border z-50 animate-scale-in text-xs font-normal">
          {/* 即时搜索框 */}
          <div className="relative mb-2">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              placeholder="搜索工作区或路径..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-surface-200/70 border border-white/5 rounded-xl pl-8 pr-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground outline-none focus:border-emerald-500/50"
            />
          </div>

          {/* 工作区列表 */}
          <div className="max-h-56 overflow-y-auto space-y-1 pr-0.5">
            {filteredWorkspaces.map((ws) => {
              const isActive = ws.id === activeWorkspaceId;
              return (
                <div
                  key={ws.id}
                  onClick={() => {
                    onSelectWorkspace(ws.id);
                    setIsOpen(false);
                  }}
                  className={`group/item flex items-center justify-between p-2 rounded-xl cursor-pointer transition-all ${
                    isActive
                      ? 'bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 font-semibold'
                      : 'hover:bg-surface-200/60 text-foreground'
                  }`}
                >
                  <div className="flex items-center space-x-2 truncate flex-1 min-w-0 pr-1">
                    {ws.type === 'physical' ? (
                      <Folder className="w-4 h-4 text-emerald-400 shrink-0" />
                    ) : (
                      <Box className="w-4 h-4 text-sky-400 shrink-0" />
                    )}
                    <div className="truncate flex-1 min-w-0 text-left">
                      <div className="text-xs truncate flex items-center space-x-1">
                        <span className="truncate">{ws.name}</span>
                        {ws.conversation_count !== undefined && ws.conversation_count > 0 && (
                          <span className="text-[10px] text-muted-foreground font-mono font-normal">
                            ({ws.conversation_count})
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground font-mono truncate">
                        {ws.root_path}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center space-x-1 shrink-0">
                    {isActive && <Check className="w-4 h-4 text-emerald-400" />}
                    {ws.id !== 'default' && (
                      <button
                        onClick={(e) => handleDeleteWorkspace(ws, e)}
                        className="opacity-0 group-hover/item:opacity-100 p-1 rounded hover:bg-rose-500/20 text-muted-foreground hover:text-rose-400 transition-all"
                        title="解除挂载（保留本地文件）"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}

            {filteredWorkspaces.length === 0 && (
              <div className="py-4 text-center text-xs text-muted-foreground">
                未检索到匹配的工作区
              </div>
            )}
          </div>

          {/* 分割线与底部快捷动作 */}
          <div className="mt-2 pt-2 border-t border-white/5 space-y-1">
            <button
              onClick={handlePickNativeFolder}
              disabled={isSelectingNative}
              className="w-full flex items-center space-x-2 px-2.5 py-2 rounded-xl bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-xs font-semibold transition-colors disabled:opacity-50"
            >
              <FolderPlus className="w-4 h-4 shrink-0" />
              <span>{isSelectingNative ? '正在打开 Windows 选择器...' : '挂载本地已有工程目录...'}</span>
            </button>

            <button
              onClick={() => {
                setIsCreatingVirtual(true);
                setIsOpen(false);
              }}
              className="w-full flex items-center space-x-2 px-2.5 py-1.5 rounded-xl hover:bg-surface-200 text-muted-foreground hover:text-foreground text-xs transition-colors"
            >
              <Plus className="w-4 h-4 shrink-0" />
              <span>新建沙盒托管工作区</span>
            </button>
          </div>
        </div>
      )}

      {/* 挂载本地工程目录确认弹窗 */}
      {isMountingModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
          <div className="bg-surface-modal border border-border w-full max-w-md rounded-2xl p-5 shadow-2xl space-y-4 animate-scale-in">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <FolderPlus className="w-5 h-5 text-emerald-400" />
                <h3 className="text-sm font-bold text-foreground">挂载本地已有工程工作区</h3>
              </div>
              <button
                onClick={() => setIsMountingModalOpen(false)}
                className="p-1 rounded-lg hover:bg-surface-100 text-muted-foreground hover:text-foreground"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <p className="text-xs text-muted-foreground leading-relaxed">
              将电脑上已有的代码仓库或资料目录挂载至 AgentHub。AI 将锁定在此物理目录下读写，绝不越界。
            </p>

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
                <label className="text-xs font-semibold text-foreground block mb-1">自定义工作区名称</label>
                <input
                  type="text"
                  value={mountName}
                  onChange={(e) => setMountName(e.target.value)}
                  placeholder="例如: Web全栈开发 / 财务对账系统 / 论文阅读"
                  className="w-full bg-surface-200/80 border border-border rounded-xl px-3 py-2 text-xs text-foreground outline-none focus:border-emerald-500"
                />
              </div>

              <div>
                <label className="text-xs font-semibold text-foreground block mb-1">
                  专属背景契约 (可选 · 对应 WORKSPACE.md)
                </label>
                <textarea
                  value={mountContext}
                  onChange={(e) => setMountContext(e.target.value)}
                  placeholder="可在此输入该项目的专属技术栈规范、核心业务术语或指令，AI 将天然贴合此语境回答。"
                  rows={3}
                  className="w-full bg-surface-200/80 border border-border rounded-xl p-2.5 text-xs text-foreground outline-none focus:border-emerald-500 resize-none"
                />
              </div>

              {errorMessage && (
                <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs flex items-center space-x-2">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>{errorMessage}</span>
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
                <h3 className="text-sm font-bold text-foreground">新建沙盒托管工作区</h3>
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
                <label className="text-xs font-semibold text-foreground block mb-1">工作区名称</label>
                <input
                  type="text"
                  value={virtualName}
                  onChange={(e) => setVirtualName(e.target.value)}
                  placeholder="例如: 临时脚本测试 / 算法推演"
                  className="w-full bg-surface-200/80 border border-border rounded-xl px-3 py-2 text-xs text-foreground outline-none focus:border-sky-500"
                  autoFocus
                />
              </div>

              {errorMessage && (
                <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs flex items-center space-x-2">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>{errorMessage}</span>
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
    </div>
  );
}

