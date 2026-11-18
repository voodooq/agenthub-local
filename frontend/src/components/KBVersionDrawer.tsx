import React, { useState, useEffect } from 'react';
import { History, X, RotateCcw, GitCompare, Shield, CheckCircle2, FileText, ArrowRight } from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface KBSnapshotItem {
  snapshot_id: string;
  kb_id: string;
  kb_uuid: string;
  version_tag: string;
  parent_version_tag: string;
  snapshot_type: 'release' | 'auto_backup' | 'imported' | 'rollback';
  content_md5: string;
  embedding_model: string;
  embedding_dim: number;
  change_summary: string;
  bundle_archive_path: string;
  doc_manifest: any[];
  created_at: string;
}

interface KBVersionDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  kbId: string;
  kbName: string;
  currentVersion: string;
  onRollbackSuccess?: () => void;
}

export function KBVersionDrawer({
  isOpen,
  onClose,
  kbId,
  kbName,
  currentVersion,
  onRollbackSuccess,
}: KBVersionDrawerProps) {
  const [snapshots, setSnapshots] = useState<KBSnapshotItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [rollingBackId, setRollingBackId] = useState<string | null>(null);

  // 比对状态
  const [selectedForDiff, setSelectedForDiff] = useState<string[]>([]);
  const [diffResult, setDiffResult] = useState<any | null>(null);
  const [loadingDiff, setLoadingDiff] = useState(false);

  useEffect(() => {
    if (isOpen && kbId) {
      loadVersions();
      setSelectedForDiff([]);
      setDiffResult(null);
    }
  }, [isOpen, kbId]);

  const loadVersions = async () => {
    setLoading(true);
    try {
      const res = await apiFetch(`/api/v1/kb/${kbId}/versions`);
      if (res.ok) {
        const json = await res.json();
        if (json && json.data) {
          setSnapshots(json.data);
        }
      }
    } catch (err) {
      console.error('加载版本列表失败:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleRollback = async (snap: KBSnapshotItem) => {
    const confirmed = window.confirm(
      `确定要将知识库【${kbName}】回滚至版本【${snap.version_tag}】吗？\n系统将在回滚前自动为当前工作态创建 auto_backup 备份。`
    );
    if (!confirmed) return;

    setRollingBackId(snap.snapshot_id);
    try {
      const res = await apiFetch(`/api/v1/kb/${kbId}/versions/${snap.snapshot_id}/rollback`, {
        method: 'POST',
      });
      if (res.ok) {
        const json = await res.json();
        if (json && json.success) {
          alert(`已成功回滚至 ${snap.version_tag}`);
          await loadVersions();
          onRollbackSuccess?.();
        }
      }
    } catch (err: any) {
      alert(`回滚失败: ${err.message || err}`);
    } finally {
      setRollingBackId(null);
    }
  };

  const toggleSelectDiff = (snapshotId: string) => {
    if (selectedForDiff.includes(snapshotId)) {
      setSelectedForDiff(selectedForDiff.filter((id) => id !== snapshotId));
      setDiffResult(null);
    } else {
      if (selectedForDiff.length >= 2) {
        setSelectedForDiff([selectedForDiff[1], snapshotId]);
      } else {
        setSelectedForDiff([...selectedForDiff, snapshotId]);
      }
      setDiffResult(null);
    }
  };

  const runDiff = async () => {
    if (selectedForDiff.length !== 2) return;
    setLoadingDiff(true);
    try {
      const res = await apiFetch(
        `/api/v1/kb/${kbId}/versions/diff?base_snapshot=${selectedForDiff[0]}&target_snapshot=${selectedForDiff[1]}`
      );
      if (res.ok) {
        const json = await res.json();
        if (json && json.data) {
          setDiffResult(json.data);
        }
      }
    } catch (err: any) {
      alert(`版本比对失败: ${err.message || err}`);
    } finally {
      setLoadingDiff(false);
    }
  };

  if (!isOpen) return null;

  const renderBadge = (type: string) => {
    switch (type) {
      case 'release':
        return <span className="px-1.5 py-0.5 text-[10px] rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium">正式发布版</span>;
      case 'auto_backup':
        return <span className="px-1.5 py-0.5 text-[10px] rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 font-medium">覆盖前备份</span>;
      case 'imported':
        return <span className="px-1.5 py-0.5 text-[10px] rounded bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium">外部导入版</span>;
      case 'rollback':
        return <span className="px-1.5 py-0.5 text-[10px] rounded bg-purple-500/10 text-purple-600 dark:text-purple-400 font-medium">回滚历史版</span>;
      default:
        return null;
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-sm">
      <div className="w-full max-w-xl h-full bg-white dark:bg-zinc-900 border-l border-zinc-200 dark:border-zinc-800 shadow-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-200 dark:border-zinc-800">
          <div className="flex items-center gap-2.5">
            <div className="p-2 bg-blue-500/10 text-blue-600 dark:text-blue-400 rounded-lg">
              <History className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-semibold text-zinc-900 dark:text-zinc-100 text-base">
                知识库版本管理与回滚
              </h3>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 truncate max-w-xs">
                【{kbName}】当前生效: <span className="font-semibold text-blue-600 dark:text-blue-400">{currentVersion}</span>
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Diff 工具栏 */}
        <div className="px-6 py-3 border-b border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-800/40 flex items-center justify-between text-xs">
          <div className="flex items-center gap-2 text-zinc-600 dark:text-zinc-400">
            <GitCompare className="w-4 h-4 text-blue-500" />
            <span>
              已选择 <strong className="text-zinc-900 dark:text-zinc-100">{selectedForDiff.length}</strong> / 2 个快照
            </span>
          </div>
          <button
            disabled={selectedForDiff.length !== 2 || loadingDiff}
            onClick={runDiff}
            className={`px-3 py-1.5 rounded-lg font-medium transition flex items-center gap-1.5 ${
              selectedForDiff.length === 2
                ? 'bg-blue-600 text-white hover:bg-blue-500 shadow-sm'
                : 'bg-zinc-200 dark:bg-zinc-800 text-zinc-400 cursor-not-allowed'
            }`}
          >
            <GitCompare className="w-3.5 h-3.5" />
            {loadingDiff ? '比对中...' : '比对选定版本'}
          </button>
        </div>

        {/* Diff 结果面板 */}
        {diffResult && (
          <div className="m-4 p-4 rounded-xl border border-blue-200 dark:border-blue-900/60 bg-blue-50/40 dark:bg-blue-950/20 text-xs space-y-2">
            <div className="flex items-center justify-between font-semibold text-blue-700 dark:text-blue-300">
              <span>{diffResult.base_version} ➔ {diffResult.target_version} 差异明细:</span>
              <button onClick={() => setDiffResult(null)} className="text-zinc-400 hover:text-zinc-600">
                <X className="w-4 h-4" />
              </button>
            </div>
            {diffResult.added_documents.length > 0 && (
              <div className="text-emerald-600 dark:text-emerald-400">
                + 新增文件: {diffResult.added_documents.join(', ')}
              </div>
            )}
            {diffResult.removed_documents.length > 0 && (
              <div className="text-rose-600 dark:text-rose-400">
                - 减少文件: {diffResult.removed_documents.join(', ')}
              </div>
            )}
            {diffResult.modified_documents.length > 0 && (
              <div className="text-amber-600 dark:text-amber-400">
                ~ 变更文件: {diffResult.modified_documents.join(', ')}
              </div>
            )}
            {diffResult.added_documents.length === 0 &&
              diffResult.removed_documents.length === 0 &&
              diffResult.modified_documents.length === 0 && (
                <div className="text-zinc-500">两版本文档清单与内容哈希完全一致。</div>
              )}
          </div>
        )}

        {/* Snapshot Timeline List */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {loading ? (
            <div className="text-center py-12 text-zinc-400 text-xs">加载版本历史中...</div>
          ) : snapshots.length === 0 ? (
            <div className="text-center py-12 text-zinc-400 text-xs">
              暂无历史快照，执行增量发布或导入覆盖时将自动生成版本记录。
            </div>
          ) : (
            snapshots.map((snap) => {
              const isSelected = selectedForDiff.includes(snap.snapshot_id);
              const isCurrent = snap.version_tag === currentVersion;

              return (
                <div
                  key={snap.snapshot_id}
                  className={`p-4 rounded-xl border transition-all ${
                    isSelected
                      ? 'border-blue-500 bg-blue-50/30 dark:bg-blue-950/20 shadow-sm'
                      : isCurrent
                      ? 'border-emerald-300 dark:border-emerald-800 bg-emerald-50/20 dark:bg-emerald-950/10'
                      : 'border-zinc-200 dark:border-zinc-800 hover:border-zinc-300 dark:hover:border-zinc-700'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1.5 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-zinc-900 dark:text-zinc-100 text-sm">
                          {snap.version_tag}
                        </span>
                        {isCurrent && (
                          <span className="px-1.5 py-0.5 text-[10px] rounded bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 font-semibold flex items-center gap-1">
                            <CheckCircle2 className="w-3 h-3" /> 当前使用
                          </span>
                        )}
                        {renderBadge(snap.snapshot_type)}
                      </div>

                      <p className="text-xs text-zinc-600 dark:text-zinc-400">
                        {snap.change_summary || '常规版本快照'}
                      </p>

                      <div className="text-[11px] text-zinc-400 font-mono flex items-center gap-3">
                        <span>MD5: {snap.content_md5?.slice(0, 10)}...</span>
                        <span>{snap.embedding_model} ({snap.embedding_dim}d)</span>
                        <span>{snap.created_at?.slice(0, 19).replace('T', ' ')}</span>
                      </div>
                    </div>

                    <div className="flex flex-col items-end gap-2 shrink-0">
                      <label className="flex items-center gap-1 text-[11px] text-zinc-500 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelectDiff(snap.snapshot_id)}
                          className="rounded border-zinc-300 text-blue-600 focus:ring-0"
                        />
                        <span>比对</span>
                      </label>

                      {!isCurrent && (
                        <button
                          onClick={() => handleRollback(snap)}
                          disabled={rollingBackId === snap.snapshot_id}
                          className="px-2.5 py-1 text-xs font-medium text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-950/40 rounded-lg border border-blue-200 dark:border-blue-900/60 transition flex items-center gap-1"
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                          {rollingBackId === snap.snapshot_id ? '回滚中...' : '回滚到此版'}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
