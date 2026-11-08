'use client';

import React, { useEffect, useState } from 'react';
import {
  FileText,
  Folder,
  ExternalLink,
  Search,
  Eye,
  FileCode,
  FileSpreadsheet,
  Image as ImageIcon,
  Sparkles,
  RefreshCw,
} from 'lucide-react';
import { Workspace, ArtifactItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import { useLanguage } from '@/context/LanguageContext';

interface LibraryViewProps {
  workspace: Workspace | undefined;
  onOpenCanvas?: (title: string, content: string, type: 'html' | 'mermaid' | 'code' | 'image') => void;
}

export function LibraryView({ workspace, onOpenCanvas }: LibraryViewProps) {
  const { t } = useLanguage();
  const [filterType, setFilterType] = useState<'all' | 'docs' | 'media'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const fetchArtifacts = async (silent = false) => {
    if (!workspace) return;
    if (!silent) setLoading(true);
    try {
      const res = await apiFetch(`/api/v1/artifacts?workspace_id=${workspace.id}`);
      if (res.ok) {
        const data = await res.json();
        setArtifacts(data.artifacts || []);
      }
    } catch (e) {
      console.error('拉取资产库失败:', e);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  useEffect(() => {
    fetchArtifacts();
  }, [workspace?.id]);

  const handleSyncArtifacts = async () => {
    if (!workspace) return;
    setSyncing(true);
    try {
      const res = await apiFetch(`/api/v1/artifacts/sync?workspace_id=${workspace.id}`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setArtifacts(data.artifacts || []);
      }
    } catch (e) {
      console.error('同步资产库失败:', e);
    } finally {
      setSyncing(false);
    }
  };

  const handleOpenWorkspaceFolder = async () => {
    if (!workspace) return;
    try {
      await apiFetch(`/api/v1/workspaces/${workspace.id}/open`, { method: 'POST' });
    } catch (e) {
      console.error('打开工作区目录失败:', e);
    }
  };

  const handlePreviewArtifact = async (art: ArtifactItem) => {
    if (!onOpenCanvas) return;
    try {
      const res = await apiFetch(`/api/v1/artifacts/${art.id}/content`);
      if (res.ok) {
        const data = await res.json();
        const canvasType = art.file_type === 'code' ? 'code' : 'html';
        onOpenCanvas(art.file_name, data.content, canvasType);
      }
    } catch (e) {
      console.error('预览资产失败:', e);
    }
  };

  const handleOpenExplorer = async (artId: string) => {
    try {
      await apiFetch(`/api/v1/artifacts/${artId}/open`, { method: 'POST' });
    } catch (e) {
      console.error('打开资源管理器失败:', e);
    }
  };

  const filteredArtifacts = artifacts.filter((item) => {
    const matchesSearch =
      item.file_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (item.summary && item.summary.toLowerCase().includes(searchQuery.toLowerCase()));
    if (!matchesSearch) return false;
    if (filterType === 'docs') return item.file_type === 'document' || item.file_type === 'code';
    if (filterType === 'media') return item.file_type === 'media' || item.file_type === 'report';
    return true;
  });

  return (
    <div className="flex-1 h-full overflow-y-auto bg-[#131314] text-zinc-100 p-6 md:p-10 select-text">
      {/* 顶部标题栏与工作区信息 */}
      <div className="max-w-6xl mx-auto space-y-8">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-zinc-800/80 pb-6">
          <div>
            <div className="flex items-center space-x-3">
              <span className="text-2xl">📚</span>
              <h1 className="text-2xl font-bold tracking-tight text-white">
                {t('库 (Library)')}
              </h1>
            </div>
            <p className="text-sm text-zinc-400 mt-1">
              {t('集中管理当前工作区内的核心交付物、代码文件与生成资产')}
              {workspace && (
                <span className="ml-2 px-2 py-0.5 rounded bg-zinc-800 text-zinc-300 font-mono text-xs">
                  {workspace.name}
                </span>
              )}
            </p>
          </div>

          {/* 搜索与过滤栏 */}
          <div className="flex items-center space-x-3">
            <div className="relative">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
              <input
                type="text"
                placeholder={t('搜索资产文件名或摘要...')}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9 pr-4 py-1.5 text-xs bg-zinc-900 border border-zinc-800 rounded-lg text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-zinc-600 w-52 sm:w-64 transition"
              />
            </div>

            <div className="flex bg-zinc-900 p-0.5 rounded-lg border border-zinc-800 text-xs">
              <button
                onClick={() => setFilterType('all')}
                className={`px-3 py-1 rounded-md transition ${
                  filterType === 'all' ? 'bg-zinc-800 text-white font-medium shadow-sm' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                {t('全部')}
              </button>
              <button
                onClick={() => setFilterType('docs')}
                className={`px-3 py-1 rounded-md transition ${
                  filterType === 'docs' ? 'bg-zinc-800 text-white font-medium shadow-sm' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                {t('文档与代码')}
              </button>
              <button
                onClick={() => setFilterType('media')}
                className={`px-3 py-1 rounded-md transition ${
                  filterType === 'media' ? 'bg-zinc-800 text-white font-medium shadow-sm' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                {t('媒体与图表')}
              </button>
            </div>
          </div>
        </div>

        {/* 物理目录挂载提示与快捷操作 */}
        {workspace && (
          <div className="p-4 rounded-xl bg-gradient-to-r from-emerald-950/20 via-zinc-900/60 to-zinc-900/40 border border-emerald-800/30 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center space-x-3 min-w-0">
              <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400 shrink-0">
                <Folder className="w-5 h-5" />
              </div>
              <div className="min-w-0">
                <div className="flex items-center space-x-2">
                  <h4 className="text-xs font-semibold text-zinc-200">
                    {t('物理工作区路径')}
                  </h4>
                  <span className="text-[11px] text-zinc-400">
                    ({t('已登记资产')}: <strong className="text-emerald-400">{artifacts.length}</strong>)
                  </span>
                </div>
                <p className="text-xs font-mono text-zinc-400 mt-0.5 truncate max-w-xl">
                  {workspace.root_path}
                </p>
              </div>
            </div>
            <div className="flex items-center space-x-2 shrink-0">
              <button
                onClick={handleSyncArtifacts}
                disabled={syncing}
                className="px-2.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition text-xs flex items-center space-x-1.5 border border-zinc-700"
                title={t('立即扫描物理工作区目录增量同步资产')}
              >
                <RefreshCw className={`w-3.5 h-3.5 text-emerald-400 ${syncing ? 'animate-spin' : ''}`} />
                <span>{syncing ? t('同步中...') : t('扫描同步')}</span>
              </button>
              <button
                onClick={handleOpenWorkspaceFolder}
                className="px-2.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition text-xs flex items-center space-x-1.5 border border-zinc-700"
                title={t('在系统文件资源管理器中打开此文件夹')}
              >
                <ExternalLink className="w-3.5 h-3.5 text-blue-400" />
                <span>{t('打开文件夹')}</span>
              </button>
            </div>
          </div>
        )}

        {/* 资产列表展示 */}
        {loading ? (
          <div className="text-center py-16 text-zinc-500 text-sm">
            {t('正在加载资产库...')}
          </div>
        ) : filteredArtifacts.length === 0 ? (
          <div className="text-center py-16 border border-dashed border-zinc-800 rounded-2xl p-8">
            <div className="w-12 h-12 mx-auto rounded-full bg-zinc-900 flex items-center justify-center text-zinc-500 mb-3 text-xl">
              📂
            </div>
            <p className="text-sm text-zinc-300 font-medium">
              {t('当前工作区资产库暂无登记记录')}
            </p>
            <p className="text-xs text-zinc-500 mt-1 max-w-md mx-auto leading-relaxed">
              {t('在对话中使用 [📥 落盘到工作区] 或运行自主任务生成的脚本、报告，均会自动登记在此处。')}
              {workspace && workspace.name === '默认工作区' && (
                <span className="text-emerald-400/90 mt-1 block">
                  💡 {t('提示：若在其他沙盒工作区（如“算法推演”）中生成了代码，可在顶部切换到对应工作区查看。')}
                </span>
              )}
            </p>
            <div className="mt-4 flex justify-center space-x-2">
              <button
                onClick={handleSyncArtifacts}
                disabled={syncing}
                className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium transition flex items-center space-x-1"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
                <span>{t('重新扫描物理目录')}</span>
              </button>
              <button
                onClick={handleOpenWorkspaceFolder}
                className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs transition flex items-center space-x-1 border border-zinc-700"
              >
                <Folder className="w-3.5 h-3.5" />
                <span>{t('打开物理文件夹')}</span>
              </button>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredArtifacts.map((art) => (
              <div
                key={art.id}
                className="group p-4 rounded-xl border border-zinc-800/80 bg-zinc-900/70 hover:bg-zinc-800/80 hover:border-zinc-700 transition flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-start justify-between">
                    <div className="flex items-center space-x-2.5">
                      <div className="p-2 rounded-lg bg-zinc-800 text-zinc-300">
                        {art.file_type === 'code' ? (
                          <FileCode className="w-4 h-4 text-blue-400" />
                        ) : art.file_type === 'media' ? (
                          <ImageIcon className="w-4 h-4 text-purple-400" />
                        ) : (
                          <FileText className="w-4 h-4 text-emerald-400" />
                        )}
                      </div>
                      <div className="min-w-0">
                        <h3 className="text-sm font-semibold text-zinc-100 truncate group-hover:text-emerald-300 transition">
                          {art.file_name}
                        </h3>
                        <p className="text-[11px] font-mono text-zinc-400 truncate max-w-xs mt-0.5">
                          {art.file_path}
                        </p>
                      </div>
                    </div>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 uppercase font-mono">
                      {art.file_type}
                    </span>
                  </div>

                  {art.summary && (
                    <p className="text-xs text-zinc-300 mt-2.5 line-clamp-2 leading-relaxed">
                      {art.summary}
                    </p>
                  )}
                </div>

                <div className="mt-4 pt-3 border-t border-zinc-800/60 flex items-center justify-between text-xs text-zinc-500">
                  <span>{new Date(art.created_at).toLocaleString()}</span>
                  <div className="flex items-center space-x-2">
                    {onOpenCanvas && (art.file_type === 'code' || art.file_type === 'document') && (
                      <button
                        onClick={() => handlePreviewArtifact(art)}
                        className="px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition text-[11px] flex items-center space-x-1"
                        title={t('在画布中预览文件内容')}
                      >
                        <Eye className="w-3 h-3 text-emerald-400" />
                        <span>{t('预览')}</span>
                      </button>
                    )}
                    <button
                      onClick={() => handleOpenExplorer(art.id)}
                      className="px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition text-[11px] flex items-center space-x-1"
                      title={t('在系统文件资源管理器中定位')}
                    >
                      <ExternalLink className="w-3 h-3 text-blue-400" />
                      <span>{t('定位文件')}</span>
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
