'use client';

import React, { useEffect, useState, useMemo, useRef } from 'react';
import {
  FileText,
  FileSpreadsheet,
  Folder,
  ExternalLink,
  Search,
  Eye,
  FileCode,
  ImageIcon,
  Sparkles,
  RefreshCw,
  FolderOpen,
  ArrowUp,
  ChevronRight,
  LayoutGrid,
  List,
  Layers,
  HardDrive,
  Clock,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import { Workspace, ArtifactItem } from '@/types';
import { apiFetch, getAccessToken } from '@/utils/apiClient';
import { useLanguage } from '@/context/LanguageContext';

interface LibraryViewProps {
  workspace: Workspace | undefined;
  onOpenCanvas?: (title: string, content: string, type: 'html' | 'mermaid' | 'code' | 'image') => void;
}

interface FolderItem {
  name: string;
  relativePath: string;
  itemCount: number;
}

export function LibraryView({ workspace, onOpenCanvas }: LibraryViewProps) {
  const { t } = useLanguage();
  const [filterType, setFilterType] = useState<'all' | 'docs' | 'media'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [actionFeedback, setActionFeedback] = useState<{
    message: string;
    type: 'success' | 'warning' | 'error';
  } | null>(null);
  const feedbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showFeedback = (message: string, type: 'success' | 'warning' | 'error' = 'success') => {
    if (feedbackTimerRef.current) {
      clearTimeout(feedbackTimerRef.current);
    }
    setActionFeedback({ message, type });
    feedbackTimerRef.current = setTimeout(() => {
      setActionFeedback(null);
      feedbackTimerRef.current = null;
    }, 4200);
  };

  // 资源管理器核心状态：当前所处相对路径（"" 表示根目录）
  const [currentPath, setCurrentPath] = useState<string>('');
  // 视图模式：'grid' 卡片大图标模式 | 'table' 详细信息列表模式 | 'flat' 平铺总览模式
  const [viewMode, setViewMode] = useState<'grid' | 'table' | 'flat'>('grid');

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
    setCurrentPath(''); // 切换工作区时重置为根目录
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

  const handleOpenWorkspaceFolder = async (subpath?: string) => {
    if (!workspace) return;
    try {
      const qs = subpath ? `?subpath=${encodeURIComponent(subpath)}` : '';
      await apiFetch(`/api/v1/workspaces/${workspace.id}/open${qs}`, { method: 'POST' });
    } catch (e) {
      console.error('打开工作区目录失败:', e);
    }
  };

  const handlePreviewArtifact = async (art: ArtifactItem) => {
    if (!onOpenCanvas) return;
    try {
      const res = await apiFetch(`/api/v1/artifacts/${art.id}/content`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showFeedback(data?.detail || `预览失败: ${art.file_name}`, 'error');
        return;
      }
      if (data.canvas_type === 'image' && data.raw_url) {
        const token = await getAccessToken();
        const finalUrl = token
          ? `${data.raw_url}${data.raw_url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
          : data.raw_url;
        onOpenCanvas(art.file_name, finalUrl, 'image');
        return;
      }
      const fallbackType = /\.(html?|htm)$/i.test(art.file_name) ? 'html' : 'code';
      onOpenCanvas(art.file_name, data.content || '', data.canvas_type || fallbackType);
    } catch (e: any) {
      console.error('预览资产失败:', e);
      showFeedback(e?.message || `预览资产失败: ${art.file_name}`, 'error');
    }
  };

  const handleLaunchArtifact = async (art: ArtifactItem) => {
    try {
      const res = await apiFetch(`/api/v1/artifacts/${art.id}/open?mode=launch`, {
        method: 'POST',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showFeedback(data?.detail || `无法打开文件: ${art.file_name}`, 'error');
        return;
      }
      if (data.downgraded) {
        showFeedback(data.message || data.downgraded_reason || '出于安全防护已自动打开所在文件夹定位', 'warning');
      } else {
        showFeedback(data.message || `已在默认应用中打开 ${art.file_name}`, 'success');
      }
    } catch (e: any) {
      console.error('直接打开资产失败:', e);
      showFeedback(e?.message || `无法打开文件: ${art.file_name}`, 'error');
    }
  };

  const handleOpenExplorer = async (artId: string, fileName?: string) => {
    try {
      const res = await apiFetch(`/api/v1/artifacts/${artId}/open?mode=reveal`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showFeedback(data?.detail || `定位文件失败${fileName ? `: ${fileName}` : ''}`, 'error');
        return;
      }
      showFeedback(data.message || `已在系统文件管理器中定位${fileName ? ` ${fileName}` : ''}`, 'success');
    } catch (e: any) {
      console.error('打开资源管理器失败:', e);
      showFeedback(e?.message || '打开资源管理器失败', 'error');
    }
  };

  // 标准化计算相对路径（统一使用 '/' 分隔符）
  const getRelativePath = (fullPath: string): string => {
    if (!workspace?.root_path) return fullPath.replace(/\\/g, '/');
    const normFull = fullPath.replace(/\\/g, '/');
    const normRoot = workspace.root_path.replace(/\\/g, '/').replace(/\/+$/, '');
    if (normFull.toLowerCase().startsWith(normRoot.toLowerCase())) {
      return normFull.slice(normRoot.length).replace(/^\/+/, '');
    }
    return fullPath.replace(/\\/g, '/');
  };

  // 格式化文件大小
  const formatFileSize = (bytes: number): string => {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
  };

  // 全量过滤符合搜索条件与类型条件的资产
  const allFilteredArtifacts = useMemo(() => {
    return artifacts.filter((item) => {
      const matchesSearch =
        item.file_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (item.summary && item.summary.toLowerCase().includes(searchQuery.toLowerCase())) ||
        item.file_path.toLowerCase().includes(searchQuery.toLowerCase());
      if (!matchesSearch) return false;
      if (filterType === 'docs') return item.file_type === 'document' || item.file_type === 'code';
      if (filterType === 'media') return item.file_type === 'media' || item.file_type === 'report';
      return true;
    });
  }, [artifacts, searchQuery, filterType]);

  // 根据当前路径 currentPath 解析出当前目录的【子文件夹】与【直接文件】
  const { currentFolders, currentFiles } = useMemo(() => {
    const foldersMap = new Map<string, { relativePath: string; count: number }>();
    const files: ArtifactItem[] = [];

    const normCurrent = currentPath.trim().replace(/^\/+|\/+$/g, '');

    for (const art of allFilteredArtifacts) {
      const relPath = getRelativePath(art.file_path);
      const parts = relPath.split('/').filter(Boolean);

      // 如果没有任何层级分割，说明是根目录直接文件
      if (parts.length <= 1) {
        if (!normCurrent) {
          files.push(art);
        }
        continue;
      }

      // 获取文件所在目录相对路径
      const fileDir = parts.slice(0, -1).join('/');

      if (!normCurrent) {
        // 当前在根目录，提取顶级子文件夹 (parts[0])
        const topFolder = parts[0];
        const existing = foldersMap.get(topFolder);
        if (existing) {
          existing.count += 1;
        } else {
          foldersMap.set(topFolder, { relativePath: topFolder, count: 1 });
        }
      } else {
        // 当前在某个子目录下，比对是否正好属于当前目录或当前目录的子文件夹
        if (fileDir === normCurrent) {
          files.push(art);
        } else if (fileDir.startsWith(`${normCurrent}/`)) {
          // 是更深层级的文件，提取直接下一级子文件夹名
          const remaining = fileDir.slice(normCurrent.length + 1);
          const nextFolder = remaining.split('/')[0];
          const fullSubPath = `${normCurrent}/${nextFolder}`;
          const existing = foldersMap.get(nextFolder);
          if (existing) {
            existing.count += 1;
          } else {
            foldersMap.set(nextFolder, { relativePath: fullSubPath, count: 1 });
          }
        }
      }
    }

    const folderList: FolderItem[] = Array.from(foldersMap.entries()).map(([name, info]) => ({
      name,
      relativePath: info.relativePath,
      itemCount: info.count,
    })).sort((a, b) => a.name.localeCompare(b.name));

    files.sort((a, b) => a.file_name.localeCompare(b.file_name));

    return { currentFolders: folderList, currentFiles: files };
  }, [allFilteredArtifacts, currentPath, workspace?.root_path]);

  // 面包屑分段
  const breadcrumbParts = useMemo(() => {
    if (!currentPath) return [];
    return currentPath.split('/').filter(Boolean);
  }, [currentPath]);

  // 返回上一级目录
  const handleGoUp = () => {
    if (!currentPath) return;
    const parts = currentPath.split('/').filter(Boolean);
    parts.pop();
    setCurrentPath(parts.join('/'));
  };

  // 点击面包屑某一层
  const handleJumpToBreadcrumb = (index: number) => {
    const parts = breadcrumbParts.slice(0, index + 1);
    setCurrentPath(parts.join('/'));
  };

  return (
    <div className="flex-1 h-full overflow-y-auto bg-background text-foreground p-6 md:p-10 select-text">
      <div className="max-w-6xl mx-auto space-y-6">
        {/* 顶部标题栏与工作区信息 */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-border pb-5">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-foreground">
              {t('库 (Library)')}
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              {t('集中管理当前工作区内的核心交付物、代码文件与生成资产')}
              {workspace && (
                <span className="ml-2 px-2 py-0.5 rounded bg-surface-200 text-foreground border border-border font-mono text-xs">
                  {workspace.name}
                </span>
              )}
            </p>
          </div>

          {/* 搜索与过滤栏 */}
          <div className="flex items-center space-x-2.5">
            <div className="relative">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                placeholder={t('搜索文件名、路径或摘要...')}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9 pr-4 py-1.5 text-xs bg-surface-card border border-border rounded-lg text-foreground placeholder-muted-foreground focus:outline-none focus:border-accent w-48 sm:w-60 transition"
              />
            </div>

            <div className="flex bg-surface-card p-0.5 rounded-lg border border-border text-xs">
              <button
                onClick={() => setFilterType('all')}
                className={`px-2.5 py-1 rounded-md transition ${
                  filterType === 'all' ? 'bg-surface-200 text-foreground font-semibold shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {t('全部')}
              </button>
              <button
                onClick={() => setFilterType('docs')}
                className={`px-2.5 py-1 rounded-md transition ${
                  filterType === 'docs' ? 'bg-surface-200 text-foreground font-semibold shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {t('文档与代码')}
              </button>
              <button
                onClick={() => setFilterType('media')}
                className={`px-2.5 py-1 rounded-md transition ${
                  filterType === 'media' ? 'bg-surface-200 text-foreground font-semibold shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {t('媒体与图表')}
              </button>
            </div>
          </div>
        </div>

        {/* 物理工作区根目录与快捷同步 */}
        {workspace && (
          <div className="p-3.5 rounded-xl bg-surface-card border border-border flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-sm">
            <div className="flex items-center space-x-3 min-w-0">
              <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-500 shrink-0">
                <HardDrive className="w-4 h-4" />
              </div>
              <div className="min-w-0">
                <div className="flex items-center space-x-2">
                  <h4 className="text-xs font-semibold text-foreground">
                    {t('工作区物理根路径')}
                  </h4>
                  <span className="text-[11px] text-muted-foreground">
                    ({t('已登记资产')}: <strong className="text-emerald-500 font-semibold">{artifacts.length}</strong>)
                  </span>
                </div>
                <p className="text-xs font-mono text-muted-foreground mt-0.5 truncate max-w-xl">
                  {workspace.root_path}
                </p>
              </div>
            </div>
            <div className="flex items-center space-x-2 shrink-0">
              <button
                onClick={handleSyncArtifacts}
                disabled={syncing}
                className="px-2.5 py-1.5 rounded-lg bg-surface-100 hover:bg-surface-200 text-foreground transition text-xs flex items-center space-x-1.5 border border-border"
                title={t('立即扫描物理工作区目录增量同步资产')}
              >
                <RefreshCw className={`w-3.5 h-3.5 text-emerald-500 ${syncing ? 'animate-spin' : ''}`} />
                <span>{syncing ? t('同步中...') : t('扫描同步')}</span>
              </button>
              <button
                onClick={() => handleOpenWorkspaceFolder(currentPath)}
                className="px-2.5 py-1.5 rounded-lg bg-surface-100 hover:bg-surface-200 text-foreground transition text-xs flex items-center space-x-1.5 border border-border"
                title={t('在系统文件资源管理器中打开当前所在目录')}
              >
                <ExternalLink className="w-3.5 h-3.5 text-blue-500" />
                <span>{t('在本地打开')}</span>
              </button>
            </div>
          </div>
        )}

        {/* 操作结果内联反馈条（直接打开 / 安全降级 / 定位提示） */}
        {actionFeedback && (
          <div
            className={`px-3.5 py-2.5 rounded-xl border text-xs flex items-center space-x-2 animate-fade-in shadow-xs ${
              actionFeedback.type === 'error'
                ? 'bg-rose-500/10 border-rose-500/30 text-rose-600 dark:text-rose-300'
                : actionFeedback.type === 'warning'
                ? 'bg-amber-500/10 border-amber-500/30 text-amber-700 dark:text-amber-300'
                : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700 dark:text-emerald-300'
            }`}
          >
            {actionFeedback.type === 'success' ? (
              <CheckCircle2 className="w-4 h-4 shrink-0" />
            ) : (
              <AlertTriangle className="w-4 h-4 shrink-0" />
            )}
            <span className="leading-snug">{actionFeedback.message}</span>
          </div>
        )}

        {/* Windows 资源管理器风格：面包屑导航栏与视图切换器 */}
        <div className="p-2.5 rounded-xl bg-surface-card border border-border flex items-center justify-between gap-3 shadow-sm select-none">
          {/* 地址栏 / 面包屑 */}
          <div className="flex items-center space-x-1 text-xs overflow-x-auto py-0.5 flex-1 min-w-0 font-mono">
            {/* 返回上一级按钮 */}
            <button
              onClick={handleGoUp}
              disabled={!currentPath}
              className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-surface-100 disabled:opacity-30 disabled:hover:bg-transparent transition shrink-0 mr-1"
              title={t('返回上一级目录')}
            >
              <ArrowUp className="w-4 h-4" />
            </button>

            {/* 根目录入口 */}
            <button
              onClick={() => setCurrentPath('')}
              className={`flex items-center space-x-1 px-2.5 py-1 rounded-md transition shrink-0 ${
                !currentPath
                  ? 'bg-surface-200 text-emerald-600 dark:text-emerald-400 font-semibold'
                  : 'text-muted-foreground hover:text-foreground hover:bg-surface-100'
              }`}
            >
              <span>{t('根目录')}</span>
            </button>

            {/* 子路径面包屑节点 */}
            {breadcrumbParts.map((folder, idx) => (
              <React.Fragment key={idx}>
                <ChevronRight className="w-3.5 h-3.5 text-muted-foreground/60 shrink-0" />
                <button
                  onClick={() => handleJumpToBreadcrumb(idx)}
                  className={`flex items-center space-x-1 px-2 py-1 rounded-md transition truncate shrink-0 ${
                    idx === breadcrumbParts.length - 1
                      ? 'bg-surface-200 text-emerald-600 dark:text-emerald-400 font-semibold'
                      : 'text-muted-foreground hover:text-foreground hover:bg-surface-100'
                  }`}
                >
                  <Folder className="w-3.5 h-3.5 text-amber-500/90" />
                  <span>{folder}</span>
                </button>
              </React.Fragment>
            ))}

            {searchQuery && (
              <span className="text-[11px] text-muted-foreground font-sans pl-2 truncate">
                ({t('正在搜索')}: &quot;{searchQuery}&quot;)
              </span>
            )}
          </div>

          {/* 视图模式切换胶囊 */}
          <div className="flex items-center bg-surface-100 rounded-lg p-0.5 border border-border text-xs shrink-0">
            <button
              onClick={() => setViewMode('grid')}
              className={`p-1.5 rounded-md transition ${
                viewMode === 'grid'
                  ? 'bg-emerald-600 text-white font-medium shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
              title={t('文件夹大图标卡片视图')}
            >
              <LayoutGrid className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setViewMode('table')}
              className={`p-1.5 rounded-md transition ${
                viewMode === 'table'
                  ? 'bg-emerald-600 text-white font-medium shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
              title={t('详细信息列表视图 (Windows 列表)')}
            >
              <List className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setViewMode('flat')}
              className={`p-1.5 rounded-md transition ${
                viewMode === 'flat'
                  ? 'bg-emerald-600 text-white font-medium shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
              title={t('平铺总览所有文件')}
            >
              <Layers className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* 主内容区域 */}
        {loading ? (
          <div className="text-center py-16 text-muted-foreground text-sm">
            {t('正在加载资产库...')}
          </div>
        ) : allFilteredArtifacts.length === 0 ? (
          <div className="text-center py-16 border border-dashed border-border rounded-2xl p-8 bg-surface-card/50">
            <div className="w-10 h-10 mx-auto rounded-full bg-surface-200 flex items-center justify-center text-muted-foreground mb-3">
              <FolderOpen className="w-5 h-5 text-muted-foreground" />
            </div>
            <p className="text-sm text-foreground font-medium">
              {t('当前工作区资产库暂无登记记录')}
            </p>
            <p className="text-xs text-muted-foreground mt-1 max-w-md mx-auto leading-relaxed">
              {t('在对话中使用 [📥 落盘到工作区] 或运行自主任务生成的脚本、报告，均会自动登记在此处。')}
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
                onClick={() => handleOpenWorkspaceFolder()}
                className="px-3 py-1.5 rounded-lg bg-surface-100 hover:bg-surface-200 text-foreground text-xs transition flex items-center space-x-1 border border-border"
              >
                <Folder className="w-3.5 h-3.5" />
                <span>{t('打开物理文件夹')}</span>
              </button>
            </div>
          </div>
        ) : viewMode === 'flat' ? (
          /* ──────────────────── 模式 1: 全部平铺视图 ──────────────────── */
          <div className="space-y-3">
            <div className="text-xs text-muted-foreground px-1 flex items-center justify-between">
              <span>{t('平铺总览模式 · 共展示')} {allFilteredArtifacts.length} {t('个资产文件')}</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {allFilteredArtifacts.map((art) => (
                <ArtifactCard
                  key={art.id}
                  art={art}
                  relativePath={getRelativePath(art.file_path)}
                  t={t}
                  onLaunch={() => handleLaunchArtifact(art)}
                  onPreview={() => handlePreviewArtifact(art)}
                  onOpenExplorer={() => handleOpenExplorer(art.id, art.file_name)}
                  onOpenCanvas={Boolean(onOpenCanvas)}
                />
              ))}
            </div>
          </div>
        ) : viewMode === 'table' ? (
          /* ──────────────────── 模式 2: Windows 详细信息列表视图 ──────────────────── */
          <div className="rounded-xl border border-border bg-surface-card overflow-hidden shadow-sm">
            <table className="w-full text-xs text-left">
              <thead className="bg-surface-100 text-muted-foreground font-semibold border-b border-border">
                <tr>
                  <th className="py-2.5 px-4">{t('名称')}</th>
                  <th className="py-2.5 px-3 w-28">{t('类型')}</th>
                  <th className="py-2.5 px-3 w-24">{t('大小')}</th>
                  <th className="py-2.5 px-3 w-40">{t('修改时间')}</th>
                  <th className="py-2.5 px-4 text-right w-56">{t('操作')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {/* 1. 文件夹优先排在最前 */}
                {currentFolders.map((f) => (
                  <tr
                    key={f.relativePath}
                    onClick={() => setCurrentPath(f.relativePath)}
                    className="hover:bg-surface-50 transition cursor-pointer group"
                  >
                    <td className="py-2.5 px-4 flex items-center space-x-2.5">
                      <Folder className="w-4 h-4 text-amber-500 shrink-0 fill-amber-500/20" />
                      <span className="font-semibold text-foreground group-hover:text-emerald-600 dark:group-hover:text-emerald-400 transition">
                        {f.name}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-muted-foreground font-mono">
                      {t('文件夹')} ({f.itemCount})
                    </td>
                    <td className="py-2.5 px-3 text-muted-foreground/60 font-mono">--</td>
                    <td className="py-2.5 px-3 text-muted-foreground/60 font-mono">--</td>
                    <td className="py-2.5 px-4 text-right" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => handleOpenWorkspaceFolder(f.relativePath)}
                        className="px-2 py-1 rounded bg-surface-100 hover:bg-surface-200 text-foreground text-[11px] transition inline-flex items-center space-x-1 border border-border"
                        title={t('在系统文件资源管理器中打开')}
                      >
                        <ExternalLink className="w-3 h-3 text-blue-500" />
                        <span>{t('打开')}</span>
                      </button>
                    </td>
                  </tr>
                ))}

                {/* 2. 直接文件列表 */}
                {currentFiles.map((art) => (
                  <tr
                    key={art.id}
                    onDoubleClick={() => handleLaunchArtifact(art)}
                    title={t('双击可直接用默认程序打开')}
                    className="hover:bg-surface-50 transition cursor-pointer"
                  >
                    <td className="py-2.5 px-4">
                      <div className="flex items-center space-x-2.5">
                        <FileIcon type={art.file_type} fileName={art.file_name} />
                        <span data-no-i18n="true" className="font-medium text-foreground truncate max-w-xs md:max-w-md">
                          {art.file_name}
                        </span>
                      </div>
                    </td>
                    <td className="py-2.5 px-3 text-muted-foreground uppercase font-mono text-[10px]">
                      {art.file_type}
                    </td>
                    <td className="py-2.5 px-3 text-muted-foreground font-mono text-[11px]">
                      {formatFileSize(art.file_size)}
                    </td>
                    <td className="py-2.5 px-3 text-muted-foreground text-[11px] font-mono">
                      {new Date(art.created_at).toLocaleString()}
                    </td>
                    <td className="py-2.5 px-4 text-right" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-end space-x-1.5">
                        <button
                          onClick={() => handleLaunchArtifact(art)}
                          className="px-2 py-1 rounded bg-emerald-600/12 hover:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 text-[11px] font-medium transition inline-flex items-center space-x-1 border border-emerald-500/30"
                          title={t('在默认应用中打开')}
                        >
                          <ExternalLink className="w-3 h-3" />
                          <span>{t('打开')}</span>
                        </button>
                        {onOpenCanvas && (
                          <button
                            onClick={() => handlePreviewArtifact(art)}
                            className="px-2 py-1 rounded bg-surface-100 hover:bg-surface-200 text-foreground text-[11px] transition inline-flex items-center space-x-1 border border-border"
                            title={t('在画布中预览')}
                          >
                            <Eye className="w-3 h-3 text-emerald-500" />
                            <span>{t('预览')}</span>
                          </button>
                        )}
                        <button
                          onClick={() => handleOpenExplorer(art.id, art.file_name)}
                          className="px-2 py-1 rounded bg-surface-100 hover:bg-surface-200 text-foreground text-[11px] transition inline-flex items-center space-x-1 border border-border"
                          title={t('定位文件')}
                        >
                          <FolderOpen className="w-3 h-3 text-blue-500" />
                          <span>{t('定位')}</span>
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}

                {currentFolders.length === 0 && currentFiles.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-12 text-center text-muted-foreground text-xs">
                      {t('此文件夹为空')}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        ) : (
          /* ──────────────────── 模式 3: 文件夹网格大图标卡片视图 (默认) ──────────────────── */
          <div className="space-y-6">
            {/* 子文件夹分区 */}
            {currentFolders.length > 0 && (
              <div className="space-y-2.5">
                <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider px-1 flex items-center space-x-1.5">
                  <Folder className="w-3.5 h-3.5 text-amber-500" />
                  <span>{t('文件夹')} ({currentFolders.length})</span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                  {currentFolders.map((f) => (
                    <div
                      key={f.relativePath}
                      onClick={() => setCurrentPath(f.relativePath)}
                      className="group p-3.5 rounded-xl border border-border bg-surface-card hover:bg-surface-50 hover:border-amber-500/40 transition cursor-pointer flex items-center justify-between shadow-sm"
                    >
                      <div className="flex items-center space-x-3 min-w-0 pr-2">
                        <div className="p-2.5 rounded-lg bg-amber-500/10 text-amber-500 group-hover:scale-105 transition shrink-0">
                          <Folder className="w-5 h-5 fill-amber-500/20" />
                        </div>
                        <div className="min-w-0">
                          <h4 className="text-xs font-semibold text-foreground group-hover:text-amber-500 truncate transition">
                            {f.name}
                          </h4>
                          <p className="text-[11px] text-muted-foreground mt-0.5 font-mono">
                            {f.itemCount} {t('个资产')}
                          </p>
                        </div>
                      </div>

                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleOpenWorkspaceFolder(f.relativePath);
                        }}
                        className="opacity-0 group-hover:opacity-100 p-1.5 rounded hover:bg-surface-200 text-muted-foreground hover:text-foreground transition"
                        title={t('在系统资源管理器中打开此文件夹')}
                      >
                        <ExternalLink className="w-3.5 h-3.5 text-blue-500" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 直接文件分区 */}
            <div className="space-y-2.5">
              <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider px-1 flex items-center space-x-1.5">
                <FileText className="w-3.5 h-3.5 text-emerald-500" />
                <span>{t('文件')} ({currentFiles.length})</span>
              </div>

              {currentFiles.length === 0 && currentFolders.length === 0 ? (
                <div className="text-center py-12 border border-dashed border-border rounded-xl text-muted-foreground text-xs bg-surface-card/40">
                  {t('此目录下暂无资产文件')}
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {currentFiles.map((art) => (
                    <ArtifactCard
                      key={art.id}
                      art={art}
                      relativePath={getRelativePath(art.file_path)}
                      t={t}
                      onLaunch={() => handleLaunchArtifact(art)}
                      onPreview={() => handlePreviewArtifact(art)}
                      onOpenExplorer={() => handleOpenExplorer(art.id, art.file_name)}
                      onOpenCanvas={Boolean(onOpenCanvas)}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// 辅助组件：文件卡片
function ArtifactCard({
  art,
  relativePath,
  t,
  onLaunch,
  onPreview,
  onOpenExplorer,
  onOpenCanvas,
}: {
  art: ArtifactItem;
  relativePath: string;
  t: (k: string) => string;
  onLaunch: () => void;
  onPreview: () => void;
  onOpenExplorer: () => void;
  onOpenCanvas: boolean;
}) {
  return (
    <div
      onDoubleClick={onLaunch}
      title={t('双击可直接用默认程序打开')}
      className="group p-4 rounded-xl border border-border bg-surface-card hover:bg-surface-50 hover:border-border-hover transition flex flex-col justify-between shadow-sm cursor-pointer"
    >
      <div>
        <div className="flex items-start justify-between">
          <div className="flex items-center space-x-2.5 min-w-0 pr-2">
            <div className="p-2 rounded-lg bg-surface-200 text-foreground shrink-0">
              <FileIcon type={art.file_type} fileName={art.file_name} />
            </div>
            <div className="min-w-0">
              <h3 data-no-i18n="true" className="text-sm font-semibold text-foreground truncate group-hover:text-emerald-600 dark:group-hover:text-emerald-400 transition">
                {art.file_name}
              </h3>
              <p data-no-i18n="true" className="text-[11px] font-mono text-muted-foreground truncate max-w-xs mt-0.5">
                {relativePath}
              </p>
            </div>
          </div>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-200 text-muted-foreground uppercase font-mono shrink-0">
            {art.file_type}
          </span>
        </div>

        {art.summary && (
          <p className="text-xs text-muted-foreground mt-2.5 line-clamp-2 leading-relaxed">
            {art.summary}
          </p>
        )}
      </div>

      <div
        className="mt-4 pt-3 border-t border-border flex items-center justify-between text-xs text-muted-foreground"
        onDoubleClick={(e) => e.stopPropagation()}
      >
        <span className="font-mono text-[11px]">{new Date(art.created_at).toLocaleString()}</span>
        <div className="flex items-center space-x-1.5">
          <button
            onClick={onLaunch}
            className="px-2.5 py-1 rounded bg-emerald-600/12 hover:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 transition text-[11px] font-medium flex items-center space-x-1 border border-emerald-500/30"
            title={t('在默认应用中打开')}
          >
            <ExternalLink className="w-3 h-3" />
            <span>{t('打开')}</span>
          </button>
          {onOpenCanvas && (
            <button
              onClick={onPreview}
              className="px-2.5 py-1 rounded bg-surface-100 hover:bg-surface-200 text-foreground transition text-[11px] flex items-center space-x-1 border border-border"
              title={t('在画布中预览文件内容')}
            >
              <Eye className="w-3 h-3 text-emerald-500" />
              <span>{t('预览')}</span>
            </button>
          )}
          <button
            onClick={onOpenExplorer}
            className="px-2.5 py-1 rounded bg-surface-100 hover:bg-surface-200 text-foreground transition text-[11px] flex items-center space-x-1 border border-border"
            title={t('在系统文件资源管理器中定位')}
          >
            <FolderOpen className="w-3 h-3 text-blue-500" />
            <span>{t('定位')}</span>
          </button>
        </div>
      </div>
    </div>
  );
}

// 辅助组件：根据文件类型与扩展名渲染图标
function FileIcon({ type, fileName }: { type: string; fileName?: string }) {
  if (fileName && /\.(xlsx?|xlsm|csv|tsv)$/i.test(fileName)) {
    return <FileSpreadsheet className="w-4 h-4 text-emerald-500" />;
  }
  if (type === 'code') return <FileCode className="w-4 h-4 text-blue-500" />;
  if (type === 'media') return <ImageIcon className="w-4 h-4 text-purple-500" />;
  return <FileText className="w-4 h-4 text-emerald-500" />;
}
