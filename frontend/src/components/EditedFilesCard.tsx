'use client';

import React, { useState } from 'react';
import {
  FileText,
  FileCode2,
  Eye,
  RotateCcw,
  CheckCircle2,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { EditedFileItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';

interface EditedFilesCardProps {
  files: EditedFileItem[];
  workspaceId: string;
  conversationId?: string | null;
  turnId?: number;
  onPreviewFile?: (filePath: string) => void;
  onReverted?: (message: string) => void;
}

function formatByteSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function EditedFilesCard({
  files,
  workspaceId,
  conversationId,
  turnId,
  onPreviewFile,
  onReverted,
}: EditedFilesCardProps) {
  const [confirming, setConfirming] = useState(false);
  const [reverting, setReverting] = useState(false);
  const [revertStatus, setRevertStatus] = useState<{
    type: 'success' | 'error';
    text: string;
  } | null>(null);

  if (!files || files.length === 0) return null;

  const handleRevert = async () => {
    if (!confirming) {
      setConfirming(true);
      setTimeout(() => setConfirming(false), 4500);
      return;
    }

    setReverting(true);
    setConfirming(false);
    setRevertStatus(null);

    try {
      const snapshotHash = files.find((f) => f.snapshotTreeHash)?.snapshotTreeHash || undefined;
      const createdFiles = files.filter((f) => f.action === 'created').map((f) => f.filePath);
      const res = await apiFetch(`/api/v1/workspaces/${encodeURIComponent(workspaceId || 'default')}/rollback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          snapshot_tree_hash: snapshotHash,
          file_paths: files.map((f) => f.filePath),
          created_files: createdFiles,
          conversation_id: conversationId || undefined,
          turn_id: turnId,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setRevertStatus({
          type: 'error',
          text: data?.detail || '撤销修改失败',
        });
        return;
      }
      const msg = data?.message || `已撤销 ${files.length} 个文件的修改`;
      setRevertStatus({ type: 'success', text: msg });
      onReverted?.(msg);
    } catch (err: any) {
      setRevertStatus({
        type: 'error',
        text: err?.message || '撤销修改请求异常',
      });
    } finally {
      setReverting(false);
    }
  };

  return (
    <div className="my-3 rounded-xl border border-border bg-surface-100/70 overflow-hidden shadow-sm">
      {/* 卡片头部 */}
      <div className="flex items-center justify-between px-3.5 py-2 border-b border-border/70 bg-surface-200/40">
        <div className="flex items-center space-x-2 text-xs font-medium text-foreground">
          <span className="w-2 h-2 rounded-full bg-amber-500 shrink-0" />
          <span>已编辑 {files.length} 个文件</span>
          {revertStatus && (
            <span
              className={`inline-flex items-center space-x-1 text-[11px] px-2 py-0.5 rounded-full ${
                revertStatus.type === 'success'
                  ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                  : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
              }`}
            >
              {revertStatus.type === 'success' ? (
                <CheckCircle2 className="w-3 h-3" />
              ) : (
                <AlertCircle className="w-3 h-3" />
              )}
              <span>{revertStatus.text}</span>
            </span>
          )}
        </div>

        <button
          type="button"
          onClick={handleRevert}
          disabled={reverting}
          className={`inline-flex items-center space-x-1 px-2 py-1 rounded-lg text-[11px] font-medium transition-colors disabled:opacity-50 ${
            confirming
              ? 'bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30'
              : 'text-muted-foreground hover:text-foreground hover:bg-surface-200'
          }`}
          title="将本轮编辑的文件恢复到修改前状态"
        >
          {reverting ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <RotateCcw className="w-3 h-3" />
          )}
          <span>{reverting ? '撤销中...' : confirming ? '再次点击确认撤销' : '撤销修改'}</span>
        </button>
      </div>

      {/* 紧凑文件台账表 */}
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-xs">
          <thead>
            <tr className="border-b border-border/50 text-[11px] text-muted-foreground bg-surface-100/40">
              <th className="py-1.5 pl-3.5 pr-2 font-medium w-16">状态</th>
              <th className="py-1.5 px-2 font-medium">文件</th>
              <th className="py-1.5 px-2 font-medium w-20 text-right">大小</th>
              <th className="py-1.5 px-2 font-medium w-20 text-right">时间</th>
              <th className="py-1.5 pl-2 pr-3.5 font-medium w-12 text-center">预览</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {files.map((item) => {
              const isCode = /\.(py|ts|tsx|js|jsx|json|sh|ps1|css|html|yaml|yml)$/i.test(item.fileName);
              return (
                <tr
                  key={item.filePath}
                  className="hover:bg-surface-200/40 transition-colors"
                >
                  <td className="py-2 pl-3.5 pr-2 whitespace-nowrap">
                    <span
                      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                        item.action === 'created'
                          ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25'
                          : 'bg-sky-500/15 text-sky-600 dark:text-sky-400 border border-sky-500/25'
                      }`}
                    >
                      {item.action === 'created' ? '新增' : '修改'}
                    </span>
                  </td>
                  <td className="py-2 px-2 max-w-[340px]">
                    <div className="flex items-center space-x-2 min-w-0">
                      {isCode ? (
                        <FileCode2 className="w-3.5 h-3.5 text-sky-500 shrink-0" />
                      ) : (
                        <FileText className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                      )}
                      <span className="font-medium text-foreground truncate" title={item.fileName}>
                        {item.fileName}
                      </span>
                      <span
                        className="text-[11px] text-muted-foreground font-mono truncate hidden sm:inline"
                        title={item.filePath}
                      >
                        {item.filePath}
                      </span>
                    </div>
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-[11px] text-muted-foreground whitespace-nowrap">
                    {formatByteSize(item.fileSize)}
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-[11px] text-muted-foreground whitespace-nowrap">
                    {item.editTime || '-'}
                  </td>
                  <td className="py-2 pl-2 pr-3.5 text-center">
                    <button
                      type="button"
                      onClick={() => onPreviewFile?.(item.filePath)}
                      className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
                      title={`预览 ${item.fileName}`}
                    >
                      <Eye className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

