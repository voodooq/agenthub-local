'use client';

import React, { useState } from 'react';
import {
  FileText,
  FileSpreadsheet,
  FileCode2,
  Image as ImageIcon,
  CheckCircle2,
  FolderOpen,
  ExternalLink,
  Download,
  Loader2,
  History,
  RotateCcw,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';
import type { VerifiedFileEntry } from '@/hooks/useVerifiedWorkspacePaths';

interface DeliverablesCardProps {
  filePaths: string[];
  workspaceId: string;
  verifiedMap?: Record<string, VerifiedFileEntry>;
  onSavedToast?: (message: string) => void;
}

function getFileMeta(filePath: string): { label: string; kind: 'doc' | 'sheet' | 'code' | 'img' | 'other' } {
  const lower = filePath.toLowerCase();
  if (lower.endsWith('.docx') || lower.endsWith('.doc')) return { label: 'Word', kind: 'doc' };
  if (lower.endsWith('.xlsx') || lower.endsWith('.xls') || lower.endsWith('.csv')) return { label: '表格', kind: 'sheet' };
  if (lower.endsWith('.pdf')) return { label: 'PDF', kind: 'doc' };
  if (lower.endsWith('.pptx') || lower.endsWith('.ppt')) return { label: 'PPT', kind: 'doc' };
  if (lower.endsWith('.md')) return { label: 'Markdown', kind: 'doc' };
  if (lower.endsWith('.html') || lower.endsWith('.htm')) return { label: 'HTML', kind: 'code' };
  if (/\.(png|jpg|jpeg|webp|gif|svg)$/i.test(lower)) return { label: '图片', kind: 'img' };
  if (/\.(py|ts|tsx|js|jsx|json)$/i.test(lower)) return { label: '脚本/代码', kind: 'code' };
  return { label: '文件', kind: 'other' };
}

/**
 * 判断路径是否为面向用户的最终交付物（过滤掉中间转换脚本、临时文件与内部引擎工具）
 */
function isIntermediateArtifact(filePath: string): boolean {
  const fileName = filePath.split(/[\\/]/).pop()?.toLowerCase() || '';
  if (/\.(tmp|log|pyc|pyo|bak)$/i.test(fileName)) return true;
  if (
    /(?:engine|generator|builder|parser|worker|helper|driver|pipeline|test_helper|check_|scratch_|generate_|build_|convert_|temp_|_tmp|debug_)/.test(fileName) &&
    /\.(py|sh|bat|ps1)$/i.test(fileName)
  ) {
    return true;
  }
  return false;
}

export function DeliverablesCard({
  filePaths,
  workspaceId,
  verifiedMap = {},
  onSavedToast,
}: DeliverablesCardProps) {
  const [busyMap, setBusyMap] = useState<Record<string, 'open' | 'desktop' | 'reveal' | null>>({});
  const [savedNoticeMap, setSavedNoticeMap] = useState<Record<string, string>>({});
  const [openSnapshotFp, setOpenSnapshotFp] = useState<string | null>(null);
  const [snapshotListMap, setSnapshotListMap] = useState<Record<string, any[]>>({});
  const [loadingSnapMap, setLoadingSnapMap] = useState<Record<string, boolean>>({});
  const [restoringMap, setRestoringMap] = useState<Record<string, boolean>>({});

  if (!filePaths || filePaths.length === 0) return null;

  // 1. 优先过滤内部工具脚本与临时生成物
  const cleanList = filePaths.filter((p) => !isIntermediateArtifact(p));

  // 2. 如果存在表格、文档、图片等面向用户的业务交付物，强制屏蔽所有底层代码脚本，确保成果绝对聚焦
  const hasUserFacingDocs = cleanList.some((p) =>
    /\.(xlsx|xls|csv|docx|doc|pdf|pptx|ppt|png|jpg|jpeg|webp|gif|svg|md|html)$/i.test(p)
  );

  const displayList = hasUserFacingDocs
    ? cleanList.filter((p) => !/\.(py|sh|bat|ps1)$/i.test(p))
    : cleanList;

  if (displayList.length === 0) return null;

  const handleFetchSnapshots = async (filePath: string) => {
    if (openSnapshotFp === filePath) {
      setOpenSnapshotFp(null);
      return;
    }
    setOpenSnapshotFp(filePath);
    setLoadingSnapMap((prev) => ({ ...prev, [filePath]: true }));
    try {
      const res = await apiFetch(
        `/api/v1/artifacts/snapshots?workspace_id=${encodeURIComponent(workspaceId || 'default')}&file_path=${encodeURIComponent(filePath)}`
      );
      if (res.ok) {
        const data = await res.json();
        setSnapshotListMap((prev) => ({ ...prev, [filePath]: data?.snapshots || [] }));
      }
    } catch {
      // 忽略查询失败容错
    } finally {
      setLoadingSnapMap((prev) => ({ ...prev, [filePath]: false }));
    }
  };

  const handleRestoreSnapshot = async (mainFilePath: string, snapshotPath: string) => {
    setRestoringMap((prev) => ({ ...prev, [snapshotPath]: true }));
    try {
      const res = await apiFetch('/api/v1/artifacts/restore-snapshot', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspace_id: workspaceId || 'default',
          file_path: mainFilePath,
          snapshot_path: snapshotPath,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        onSavedToast?.(data?.message || '已成功还原为主文件');
        // 刷新快照列表以同步最新的自动备份
        const refreshRes = await apiFetch(
          `/api/v1/artifacts/snapshots?workspace_id=${encodeURIComponent(workspaceId || 'default')}&file_path=${encodeURIComponent(mainFilePath)}`
        );
        if (refreshRes.ok) {
          const freshData = await refreshRes.json();
          setSnapshotListMap((prev) => ({ ...prev, [mainFilePath]: freshData?.snapshots || [] }));
        }
      } else {
        onSavedToast?.(data?.detail || '还原快照失败');
      }
    } catch (err: any) {
      onSavedToast?.(err?.message || '还原快照异常');
    } finally {
      setRestoringMap((prev) => ({ ...prev, [snapshotPath]: false }));
    }
  };

  const handleOpenByMode = async (filePath: string, mode: 'launch' | 'reveal') => {
    const opKey = mode === 'launch' ? 'open' : 'reveal';
    setBusyMap((prev) => ({ ...prev, [filePath]: opKey }));
    try {
      const res = await apiFetch('/api/v1/artifacts/open-by-path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspace_id: workspaceId || 'default',
          file_path: filePath,
          mode,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        onSavedToast?.(err?.detail || '打开文件失败');
      }
    } catch (err: any) {
      onSavedToast?.(err?.message || '打开文件请求失败');
    } finally {
      setBusyMap((prev) => ({ ...prev, [filePath]: null }));
    }
  };

  const handleSaveToDesktop = async (filePath: string) => {
    setBusyMap((prev) => ({ ...prev, [filePath]: 'desktop' }));
    try {
      const res = await apiFetch('/api/v1/artifacts/save-to-desktop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspace_id: workspaceId || 'default',
          file_path: filePath,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        onSavedToast?.(data?.detail || '保存到桌面失败');
        return;
      }
      const notice = `已存至桌面: ${data?.file_name || ''}`;
      setSavedNoticeMap((prev) => ({ ...prev, [filePath]: notice }));
      onSavedToast?.(data?.message || notice);
      setTimeout(() => {
        setSavedNoticeMap((prev) => {
          const next = { ...prev };
          delete next[filePath];
          return next;
        });
      }, 4000);
    } catch (err: any) {
      onSavedToast?.(err?.message || '保存到桌面异常');
    } finally {
      setBusyMap((prev) => ({ ...prev, [filePath]: null }));
    }
  };

  return (
    <div className="my-3 rounded-xl border border-emerald-500/40 bg-emerald-500/5 overflow-hidden shadow-sm">
      {/* 标题栏 */}
      <div className="flex items-center justify-between px-3.5 py-2 border-b border-emerald-500/20 bg-emerald-500/10">
        <div className="flex items-center space-x-2 text-xs font-semibold text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
          <span>本次成果 · {displayList.length} 个文件</span>
        </div>
      </div>

      {/* 成果条目 */}
      <div className="divide-y divide-emerald-500/15">
        {displayList.map((fp) => {
          const verified = verifiedMap[fp];
          const fileName = verified?.fileName || fp.split(/[\\/]/).pop() || fp;
          const meta = getFileMeta(fp);
          const busy = busyMap[fp];
          const savedTip = savedNoticeMap[fp];

          return (
            <div
              key={fp}
              className="flex flex-col bg-surface-100/50 hover:bg-surface-100/80 transition-colors"
            >
              <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2.5">
                <div className="flex items-center space-x-2.5 min-w-0 flex-1">
                  {meta.kind === 'sheet' ? (
                    <FileSpreadsheet className="w-4 h-4 text-emerald-500 shrink-0" />
                  ) : meta.kind === 'code' ? (
                    <FileCode2 className="w-4 h-4 text-sky-500 shrink-0" />
                  ) : meta.kind === 'img' ? (
                    <ImageIcon className="w-4 h-4 text-amber-500 shrink-0" />
                  ) : (
                    <FileText className="w-4 h-4 text-emerald-500 shrink-0" />
                  )}
                  <span className="text-xs font-semibold text-foreground truncate" title={fp}>
                    {fileName}
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 font-medium shrink-0">
                    {meta.label}
                  </span>
                  {savedTip && (
                    <span className="text-[11px] text-emerald-600 dark:text-emerald-400 font-medium truncate">
                      ✓ {savedTip}
                    </span>
                  )}
                </div>

                <div className="flex items-center space-x-2 shrink-0">
                  {/* 历史快照版本入口 */}
                  {(verified?.snapshotCount || 0) > 0 && (
                    <button
                      type="button"
                      onClick={() => handleFetchSnapshots(fp)}
                      className={`inline-flex items-center space-x-1 px-2 py-1 rounded-lg border text-xs font-medium transition-colors ${
                        openSnapshotFp === fp
                          ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
                          : 'border-border/60 bg-surface-100 hover:bg-surface-200 text-muted-foreground hover:text-foreground'
                      }`}
                      title="查看因同名防覆写而自动保存的历史快照版本"
                    >
                      <History className="w-3 h-3 text-emerald-500" />
                      <span>快照 ({verified?.snapshotCount})</span>
                      {openSnapshotFp === fp ? (
                        <ChevronUp className="w-2.5 h-2.5" />
                      ) : (
                        <ChevronDown className="w-2.5 h-2.5" />
                      )}
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={() => handleOpenByMode(fp, 'launch')}
                    disabled={Boolean(busy)}
                    className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg border border-border bg-surface-100 hover:bg-surface-200 text-xs font-medium text-foreground transition-colors disabled:opacity-50"
                  >
                    {busy === 'open' ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <ExternalLink className="w-3 h-3" />
                    )}
                    <span>打开</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => handleSaveToDesktop(fp)}
                    disabled={Boolean(busy)}
                    className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition-colors shadow-sm disabled:opacity-50"
                  >
                    {busy === 'desktop' ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Download className="w-3 h-3" />
                    )}
                    <span>保存到桌面</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => handleOpenByMode(fp, 'reveal')}
                    disabled={Boolean(busy)}
                    className="inline-flex items-center space-x-1 px-2 py-1 rounded-lg text-xs text-muted-foreground hover:text-foreground hover:bg-surface-200/70 transition-colors disabled:opacity-50"
                    title="在系统资源管理器中定位该文件"
                  >
                    <FolderOpen className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">在“资源管理器”中打开</span>
                  </button>
                </div>
              </div>

              {/* 展开的历史快照抽屉 */}
              {openSnapshotFp === fp && (
                <div className="border-t border-emerald-500/15 bg-background/50 px-3.5 py-2 space-y-1.5 animate-fade-in text-xs">
                  <div className="flex items-center justify-between text-[11px] text-muted-foreground font-medium">
                    <span>⏱ 自动快照历史（覆写前安全留痕，不丢数据）</span>
                    {loadingSnapMap[fp] && <Loader2 className="w-3 h-3 animate-spin text-emerald-500" />}
                  </div>
                  {loadingSnapMap[fp] ? (
                    <div className="py-2 text-center text-muted-foreground/60 text-xs">正在读取快照版本...</div>
                  ) : (snapshotListMap[fp] || []).length === 0 ? (
                    <div className="py-1 text-muted-foreground/60 text-xs">暂无历史快照版本</div>
                  ) : (
                    <div className="divide-y divide-border/20 rounded-lg border border-border/30 bg-surface-100/60 overflow-hidden">
                      {(snapshotListMap[fp] || []).map((snap: any, sIdx: number) => {
                        const isRestoring = restoringMap[snap.snapshot_path];
                        return (
                          <div
                            key={snap.snapshot_path || sIdx}
                            className="flex items-center justify-between px-2.5 py-1.5 text-xs hover:bg-surface-200/50 transition-colors"
                          >
                            <div className="min-w-0 flex-1 pr-2">
                              <div className="font-mono text-[11.5px] text-foreground truncate">
                                {snap.timestamp || snap.snapshot_name}
                              </div>
                              <div className="text-[10px] text-muted-foreground font-mono">
                                {snap.file_size ? `${(snap.file_size / 1024).toFixed(1)} KB` : ''} · {snap.snapshot_name}
                              </div>
                            </div>
                            <div className="flex items-center space-x-1.5 shrink-0">
                              <button
                                type="button"
                                onClick={() => handleOpenByMode(snap.snapshot_path, 'launch')}
                                className="px-2 py-0.5 rounded border border-border bg-surface-100 hover:bg-surface-200 text-[11px] text-foreground transition-colors"
                                title="打开此历史快照"
                              >
                                查看
                              </button>
                              <button
                                type="button"
                                onClick={() => handleSaveToDesktop(snap.snapshot_path)}
                                className="px-2 py-0.5 rounded bg-surface-200 hover:bg-surface-300 text-[11px] text-foreground transition-colors"
                                title="导出此历史快照到桌面"
                              >
                                存到桌面
                              </button>
                              <button
                                type="button"
                                onClick={() => handleRestoreSnapshot(fp, snap.snapshot_path)}
                                disabled={Boolean(isRestoring)}
                                className="inline-flex items-center space-x-1 px-2 py-0.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-medium transition-colors disabled:opacity-50"
                                title="将主文件一键还原为该快照版本（当前版本会自动另存为最新快照）"
                              >
                                {isRestoring ? (
                                  <Loader2 className="w-2.5 h-2.5 animate-spin" />
                                ) : (
                                  <RotateCcw className="w-2.5 h-2.5" />
                                )}
                                <span>还原</span>
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
