import React from 'react';
import { AlertTriangle, ArrowRight, ShieldCheck, Copy, X } from 'lucide-react';

export interface ConflictDiffData {
  added_documents: string[];
  removed_documents: string[];
  shared_documents: string[];
  local_chunks: number;
  incoming_chunks: number;
}

interface KBImportConflictModalProps {
  isOpen: boolean;
  onClose: () => void;
  kbName: string;
  localVersion: string;
  localMd5: string;
  incomingVersion: string;
  incomingMd5: string;
  diff: ConflictDiffData | null;
  onConfirmOverwrite: () => void;
  onImportAsNew: () => void;
  isProcessing: boolean;
}

export function KBImportConflictModal({
  isOpen,
  onClose,
  kbName,
  localVersion,
  localMd5,
  incomingVersion,
  incomingMd5,
  diff,
  onConfirmOverwrite,
  onImportAsNew,
  isProcessing,
}: KBImportConflictModalProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-white dark:bg-zinc-900 border border-amber-300 dark:border-amber-700/60 rounded-2xl w-full max-w-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-200 dark:border-zinc-800 bg-amber-50 dark:bg-amber-950/20">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-amber-500/20 text-amber-600 dark:text-amber-400 rounded-lg">
              <AlertTriangle className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-semibold text-zinc-900 dark:text-zinc-100 text-base">
                知识库版本冲突与导入仲裁
              </h3>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                检测到同名知识库【{kbName}】，但核心内容 MD5 与版本存在差异
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isProcessing}
            className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 p-1 rounded-lg"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto space-y-5 flex-1">
          {/* 版本对比左右双栏 */}
          <div className="grid grid-cols-2 gap-4">
            {/* 本地当前版本 */}
            <div className="p-4 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-800/40">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">本地原版本</span>
                <span className="px-2 py-0.5 text-xs font-semibold rounded bg-zinc-200 dark:bg-zinc-700 text-zinc-700 dark:text-zinc-300">
                  {localVersion}
                </span>
              </div>
              <div className="text-xs text-zinc-500 dark:text-zinc-400 font-mono truncate" title={localMd5}>
                MD5: {localMd5 ? `${localMd5.slice(0, 12)}...` : '未计算'}
              </div>
              <div className="mt-2 text-xs text-zinc-600 dark:text-zinc-300">
                切片总数: <span className="font-semibold">{diff?.local_chunks ?? '--'}</span>
              </div>
            </div>

            {/* 待导入新版本 */}
            <div className="p-4 rounded-xl border border-blue-200 dark:border-blue-900/60 bg-blue-50/50 dark:bg-blue-950/20">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-blue-600 dark:text-blue-400">待导入新包</span>
                <span className="px-2 py-0.5 text-xs font-semibold rounded bg-blue-500/20 text-blue-700 dark:text-blue-300">
                  {incomingVersion}
                </span>
              </div>
              <div className="text-xs text-blue-600/80 dark:text-blue-400/80 font-mono truncate" title={incomingMd5}>
                MD5: {incomingMd5 ? `${incomingMd5.slice(0, 12)}...` : '--'}
              </div>
              <div className="mt-2 text-xs text-blue-700 dark:text-blue-300">
                切片总数: <span className="font-semibold">{diff?.incoming_chunks ?? '--'}</span>
              </div>
            </div>
          </div>

          {/* 变更对比 Diff 详情 */}
          {diff && (
            <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 p-4 space-y-3">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                文档清单变更比对
              </h4>
              <div className="space-y-2 text-xs">
                {diff.added_documents.length > 0 && (
                  <div className="flex items-start gap-2 text-emerald-600 dark:text-emerald-400">
                    <span className="font-semibold shrink-0">+ 新增文件 ({diff.added_documents.length}):</span>
                    <span className="truncate">{diff.added_documents.join(', ')}</span>
                  </div>
                )}
                {diff.removed_documents.length > 0 && (
                  <div className="flex items-start gap-2 text-rose-600 dark:text-rose-400">
                    <span className="font-semibold shrink-0">- 减少文件 ({diff.removed_documents.length}):</span>
                    <span className="truncate">{diff.removed_documents.join(', ')}</span>
                  </div>
                )}
                <div className="flex items-start gap-2 text-zinc-500">
                  <span className="shrink-0">= 保持文件 ({diff.shared_documents.length}):</span>
                  <span className="truncate">{diff.shared_documents.join(', ') || '无'}</span>
                </div>
              </div>
            </div>
          )}

          <div className="p-3 bg-zinc-100 dark:bg-zinc-800/60 rounded-xl flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
            <ShieldCheck className="w-4 h-4 text-emerald-500 shrink-0" />
            <span>
              <strong>安全机制保证</strong>：点击覆盖前，系统会自动为原版本创建一份 <code>auto_backup</code> 快照，随时可在版本管理中一键无损回滚。
            </span>
          </div>
        </div>

        {/* Footer 操作按钮 */}
        <div className="px-6 py-4 border-t border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900/50 flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            disabled={isProcessing}
            className="px-4 py-2 text-xs font-medium text-zinc-600 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-800 rounded-lg transition"
          >
            取消导入
          </button>
          <button
            onClick={onImportAsNew}
            disabled={isProcessing}
            className="px-4 py-2 text-xs font-medium text-zinc-700 dark:text-zinc-200 border border-zinc-300 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded-lg transition"
          >
            另存为独立新库
          </button>
          <button
            onClick={onConfirmOverwrite}
            disabled={isProcessing}
            className="px-4 py-2 text-xs font-semibold text-white bg-blue-600 hover:bg-blue-500 active:bg-blue-700 rounded-lg shadow-sm transition flex items-center gap-1.5"
          >
            <ShieldCheck className="w-4 h-4" />
            {isProcessing ? '备份并覆盖中...' : '备份原版并覆盖 (推荐)'}
          </button>
        </div>
      </div>
    </div>
  );
}
