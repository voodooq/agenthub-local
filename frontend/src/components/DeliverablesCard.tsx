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
 * 判断路径是否为面向用户的最终交付物（过滤掉中间转换脚本与临时文件）
 */
function isIntermediateArtifact(filePath: string): boolean {
  const fileName = filePath.split(/[\\/]/).pop()?.toLowerCase() || '';
  if (/\.(tmp|log|pyc|pyo|bak)$/i.test(fileName)) return true;
  if (/^(convert_|temp_|_tmp|debug_|test_helper)/.test(fileName) && /\.(py|sh|bat|ps1)$/i.test(fileName)) {
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

  if (!filePaths || filePaths.length === 0) return null;

  // 若存在非临时/非脚本交付物，则优先隐藏纯中间脚本，保持成果聚焦
  const primaryList = filePaths.filter((p) => !isIntermediateArtifact(p));
  const displayList = primaryList.length > 0 ? primaryList : filePaths;

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
              className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2.5 bg-surface-100/50 hover:bg-surface-100/80 transition-colors"
            >
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
          );
        })}
      </div>
    </div>
  );
}
