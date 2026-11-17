'use client';

import React, { useState } from 'react';
import { Undo2, Check, AlertCircle, Loader2 } from 'lucide-react';

interface DiffViewerCardProps {
  filePath: string;
  unifiedDiff: string;
  snapshotTreeHash?: string;
  workspaceId?: string;
  onRollbackSuccess?: () => void;
}

export const DiffViewerCard: React.FC<DiffViewerCardProps> = ({
  filePath,
  unifiedDiff,
  snapshotTreeHash,
  workspaceId = 'default',
  onRollbackSuccess,
}) => {
  const [isReverting, setIsReverting] = useState(false);
  const [reverted, setReverted] = useState(false);
  const [revertError, setRevertError] = useState<string | null>(null);

  if (!unifiedDiff || !unifiedDiff.trim()) {
    return null;
  }

  // 过滤头部元数据行，聚焦于真实的 diff 代码行
  const diffLines = unifiedDiff
    .split('\n')
    .filter((line) => !line.startsWith('---') && !line.startsWith('+++'));

  const handleRollback = async () => {
    if (!snapshotTreeHash || isReverting || reverted) return;
    setIsReverting(true);
    setRevertError(null);

    try {
      const res = await fetch(`/api/v1/workspaces/${workspaceId}/rollback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          snapshot_tree_hash: snapshotTreeHash,
          file_path: filePath,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `回滚失败 (HTTP ${res.status})`);
      }

      setReverted(true);
      if (onRollbackSuccess) {
        onRollbackSuccess();
      }
    } catch (err: any) {
      setRevertError(err.message || '撤销操作失败');
    } finally {
      setIsReverting(false);
    }
  };

  return (
    <div className="my-2 rounded-lg border border-border bg-surface-100 overflow-hidden text-xs font-mono shadow-inner">
      {/* 顶栏控制条 */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-surface-200/80 border-b border-border">
        <div className="flex items-center space-x-2 truncate min-w-0 mr-2">
          <span className="text-muted-foreground font-semibold select-none">Diff 补丁:</span>
          <span className="text-foreground font-medium truncate" title={filePath}>
            {filePath}
          </span>
          {snapshotTreeHash && (
            <span
              className="text-[10px] text-muted-foreground font-mono hidden sm:inline"
              title={`快照 Tree Hash: ${snapshotTreeHash}`}
            >
              ({snapshotTreeHash.slice(0, 7)})
            </span>
          )}
        </div>

        {/* 撤销 / 回滚按钮 */}
        {snapshotTreeHash && (
          <div className="flex items-center space-x-2 shrink-0">
            {revertError && (
              <span className="text-[10px] text-rose-600 dark:text-rose-400 flex items-center space-x-1" title={revertError}>
                <AlertCircle className="w-3 h-3" />
                <span className="hidden md:inline">撤销失败</span>
              </span>
            )}
            <button
              type="button"
              disabled={isReverting || reverted}
              onClick={handleRollback}
              className={`px-2 py-0.5 rounded text-[11px] font-sans font-medium flex items-center space-x-1 transition-all ${
                reverted
                  ? 'bg-surface-200 text-muted-foreground border border-border cursor-not-allowed'
                  : 'bg-surface-100 hover:bg-rose-500/10 text-foreground hover:text-rose-600 dark:hover:text-rose-300 border border-border hover:border-rose-500/40 shadow-sm active:scale-95'
              }`}
              title={reverted ? '该文件修改已撤销' : '一键将该文件还原到修改前状态'}
            >
              {isReverting ? (
                <>
                  <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />
                  <span>正在还原...</span>
                </>
              ) : reverted ? (
                <>
                  <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                  <span>已撤销修改</span>
                </>
              ) : (
                <>
                  <Undo2 className="w-3 h-3 text-muted-foreground group-hover:text-rose-600 dark:group-hover:text-rose-300" />
                  <span>↺ 撤销此修改</span>
                </>
              )}
            </button>
          </div>
        )}
      </div>

      {/* Unified Diff 逐行渲染 */}
      <div className="p-2 overflow-x-auto max-h-72 overflow-y-auto space-y-0.5 selection:bg-emerald-500/25">
        {diffLines.map((line, idx) => {
          const isAdd = line.startsWith('+');
          const isDel = line.startsWith('-');
          const isHunk = line.startsWith('@@');

          return (
            <div
              key={idx}
              className={`px-2 py-0.5 whitespace-pre rounded-sm font-mono text-[11px] leading-snug ${
                isAdd
                  ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 font-medium'
                  : isDel
                  ? 'bg-rose-500/15 text-rose-700 dark:text-rose-300 font-medium line-through decoration-rose-500/40'
                  : isHunk
                  ? 'text-sky-700 dark:text-sky-400 bg-sky-500/10 font-bold my-1 border-y border-sky-500/20'
                  : 'text-muted-foreground'
              }`}
            >
              {line}
            </div>
          );
        })}
      </div>
    </div>
  );
};

