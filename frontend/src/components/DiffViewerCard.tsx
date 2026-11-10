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
    <div className="my-2 rounded-lg border border-zinc-700/60 bg-zinc-950 overflow-hidden text-xs font-mono shadow-inner">
      {/* 顶栏控制条 */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-zinc-900/90 border-b border-zinc-800">
        <div className="flex items-center space-x-2 truncate min-w-0 mr-2">
          <span className="text-zinc-400 font-semibold select-none">Diff 补丁:</span>
          <span className="text-zinc-200 font-medium truncate" title={filePath}>
            {filePath}
          </span>
          {snapshotTreeHash && (
            <span
              className="text-[10px] text-zinc-500 font-mono hidden sm:inline"
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
              <span className="text-[10px] text-rose-400 flex items-center space-x-1" title={revertError}>
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
                  ? 'bg-zinc-800 text-zinc-400 border border-zinc-700 cursor-not-allowed'
                  : 'bg-zinc-800 hover:bg-rose-950/80 text-zinc-300 hover:text-rose-200 border border-zinc-700 hover:border-rose-600/50 shadow-sm active:scale-95'
              }`}
              title={reverted ? '该文件修改已撤销' : '一键将该文件还原到修改前状态'}
            >
              {isReverting ? (
                <>
                  <Loader2 className="w-3 h-3 animate-spin text-zinc-400" />
                  <span>正在还原...</span>
                </>
              ) : reverted ? (
                <>
                  <Check className="w-3 h-3 text-emerald-400" />
                  <span>已撤销修改</span>
                </>
              ) : (
                <>
                  <Undo2 className="w-3 h-3 text-zinc-400 group-hover:text-rose-300" />
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
                  ? 'bg-emerald-950/60 text-emerald-300 font-medium'
                  : isDel
                  ? 'bg-rose-950/60 text-rose-300 font-medium line-through decoration-rose-500/40'
                  : isHunk
                  ? 'text-sky-400 bg-sky-950/30 font-bold my-1 border-y border-sky-800/30'
                  : 'text-zinc-400'
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

