'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  X,
  Download,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Sparkles,
  RotateCcw,
  ExternalLink,
  ArrowRight,
  Package,
  Clock,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';
import { UpdateStatusData } from '@/types';

interface UpdateModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialUpdateState?: UpdateStatusData | null;
  onUpdateStateChange?: (state: UpdateStatusData) => void;
}

function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

function formatDate(isoStr?: string): string {
  if (!isoStr) return '';
  try {
    const dt = new Date(isoStr);
    if (Number.isNaN(dt.getTime())) return isoStr;
    return dt.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return isoStr;
  }
}

export function UpdateModal({
  isOpen,
  onClose,
  initialUpdateState = null,
  onUpdateStateChange,
}: UpdateModalProps) {
  const [updateState, setUpdateState] = useState<UpdateStatusData | null>(initialUpdateState);
  const [isChecking, setIsChecking] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);
  const [restartMessage, setRestartMessage] = useState<string>('');
  const [localError, setLocalError] = useState<string>('');
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const healthTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (initialUpdateState) {
      setUpdateState(initialUpdateState);
    }
  }, [initialUpdateState]);

  const syncState = useCallback(
    (next: UpdateStatusData) => {
      setUpdateState(next);
      onUpdateStateChange?.(next);
    },
    [onUpdateStateChange]
  );

  const stopDownloadPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const stopHealthPolling = useCallback(() => {
    if (healthTimerRef.current) {
      clearInterval(healthTimerRef.current);
      healthTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      stopDownloadPolling();
      stopHealthPolling();
    };
  }, [stopDownloadPolling, stopHealthPolling]);

  const handleCheckUpdate = useCallback(
    async (force: boolean = true) => {
      try {
        setIsChecking(true);
        setLocalError('');
        const res = await apiFetch(`/api/v1/system/update/check?force=${force ? 'true' : 'false'}`);
        const data: UpdateStatusData = await res.json();
        if (res.ok) {
          syncState(data);
        } else {
          setLocalError((data as any)?.detail || '检查版本更新失败');
        }
      } catch (err: any) {
        setLocalError(err?.message || '连接本地更新服务失败');
      } finally {
        setIsChecking(false);
      }
    },
    [syncState]
  );

  // 打开弹窗时若尚无检查结果或处于 idle，自动触发一次检查；否则同步一次最新状态
  useEffect(() => {
    if (!isOpen) {
      stopDownloadPolling();
      return;
    }
    if (!updateState || updateState.status === 'idle') {
      void handleCheckUpdate(true);
    } else {
      void apiFetch('/api/v1/system/update/status')
        .then((res) => (res.ok ? res.json() : null))
        .then((data: UpdateStatusData | null) => {
          if (data && data.status && data.status !== 'idle') {
            syncState(data);
          }
        })
        .catch(() => {});
    }
  }, [isOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  // 下载过程中持续轮询 /api/v1/system/update/status 更新百分比进度条
  useEffect(() => {
    if (!isOpen || updateState?.status !== 'downloading') {
      stopDownloadPolling();
      return;
    }

    const fetchProgress = async () => {
      try {
        const res = await apiFetch('/api/v1/system/update/status');
        if (res.ok) {
          const data: UpdateStatusData = await res.json();
          syncState(data);
          if (data.status !== 'downloading') {
            stopDownloadPolling();
          }
        }
      } catch {
        // 忽略偶发瞬时轮询波动
      }
    };

    pollTimerRef.current = setInterval(() => {
      void fetchProgress();
    }, 350);

    return () => {
      stopDownloadPolling();
    };
  }, [isOpen, updateState?.status, syncState, stopDownloadPolling]);

  const handleStartDownload = async (force: boolean = false) => {
    try {
      setLocalError('');
      const res = await apiFetch('/api/v1/system/update/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force }),
      });
      const data = await res.json();
      if (res.ok) {
        syncState(data);
      } else {
        setLocalError(data?.detail || '触发客户端下载失败');
      }
    } catch (err: any) {
      setLocalError(err?.message || '触发客户端下载异常');
    }
  };

  const startHealthReconnectPoll = useCallback(() => {
    stopHealthPolling();
    let seenDown = false;
    let attempts = 0;

    healthTimerRef.current = setInterval(async () => {
      attempts += 1;
      if (attempts > 90) {
        stopHealthPolling();
        setRestartMessage('等待服务自动重连超时，如新窗口未弹出请手动重新打开客户端。');
        return;
      }
      try {
        const res = await fetch('/api/v1/health', { cache: 'no-store' });
        if (res.ok) {
          const data = await res.json().catch(() => null);
          const newVer = String(data?.version || '').replace(/^v/i, '').trim();
          const targetVer = String(updateState?.latest_version || '').replace(/^v/i, '').trim();
          if (seenDown || (newVer && targetVer && newVer === targetVer) || attempts >= 4) {
            stopHealthPolling();
            window.location.reload();
          }
        } else {
          seenDown = true;
        }
      } catch {
        seenDown = true;
      }
    }, 1000);
  }, [stopHealthPolling, updateState?.latest_version]);

  const handleApplyAndRestart = async () => {
    try {
      setLocalError('');
      setIsRestarting(true);
      setRestartMessage('正在写入更新并准备重启客户端，请勿关闭电源...');
      const res = await apiFetch('/api/v1/system/update/apply-and-restart', {
        method: 'POST',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setIsRestarting(false);
        setRestartMessage('');
        setLocalError(data?.detail || '应用更新并重启失败');
        return;
      }
      if (data?.status === 'manual_action_required') {
        setIsRestarting(false);
        setRestartMessage(data?.message || '已打开安装镜像，请完成拖拽替换后手动重启。');
        return;
      }
      setRestartMessage(data?.message || '正在重启客户端并加载新版本，页面将在就绪后自动刷新...');
      startHealthReconnectPoll();
    } catch (err: any) {
      // 若服务在响应最后瞬间退出，进入重连轮询
      setRestartMessage('正在等待新版客户端重启就绪...');
      startHealthReconnectPoll();
    }
  };

  const handleOpenReleasePage = async () => {
    const url = updateState?.html_url || 'https://github.com/voodooq/agenthub-local/releases';
    try {
      const res = await apiFetch('/api/v1/system/open-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      if (!res.ok) {
        window.open(url, '_blank', 'noopener,noreferrer');
      }
    } catch {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isRestarting) {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isOpen, isRestarting, onClose]);

  if (!isOpen) return null;

  const status = updateState?.status || 'idle';
  const progressPct = Math.max(0, Math.min(100, Number(updateState?.progress_percent || 0)));
  const totalBytes = Number(updateState?.total_bytes || 0);
  const downloadedBytes = Number(updateState?.downloaded_bytes || 0);
  const speedBps = Number(updateState?.speed_bps || 0);
  const errorMsg = localError || updateState?.error_message || '';

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in"
      onClick={() => {
        if (!isRestarting) onClose();
      }}
    >
      <div
        className="w-full max-w-2xl rounded-3xl bg-surface-card border border-border shadow-2xl overflow-hidden flex flex-col max-h-[85vh] text-foreground animate-scale-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 顶部标题栏 */}
        <div className="px-6 py-4 border-b border-border/70 bg-surface-100/80 flex items-center justify-between shrink-0">
          <div className="flex items-center space-x-2.5">
            <div className="w-8 h-8 rounded-xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center">
              <Sparkles className="w-4 h-4 text-emerald-500" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-foreground">版本检测与客户端升级</h2>
              <p className="text-[11px] text-muted-foreground">
                GitHub Releases 远程版本托管 (voodooq/agenthub-local)
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2">
            <button
              type="button"
              disabled={isChecking || status === 'downloading' || isRestarting}
              onClick={() => void handleCheckUpdate(true)}
              className="px-2.5 py-1.5 rounded-xl bg-surface-200 hover:bg-surface-100 border border-border text-xs font-medium text-foreground transition-colors flex items-center space-x-1.5 disabled:opacity-50"
              title="强制从 GitHub 重新获取最新版本"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isChecking ? 'animate-spin text-emerald-500' : ''}`} />
              <span>{isChecking ? '检查中...' : '检查更新'}</span>
            </button>
            {!isRestarting && (
              <button
                type="button"
                onClick={onClose}
                className="p-1.5 rounded-full text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
                title="关闭"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        {/* 主体内容区 */}
        <div className="p-6 space-y-5 overflow-y-auto flex-1">
          {/* 版本对比概要卡片 */}
          <div className="p-4 rounded-2xl bg-surface-100 border border-border flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="space-y-1.5">
              <div className="flex items-center space-x-2 text-xs">
                <span className="px-2 py-0.5 rounded-lg bg-surface-200 border border-border font-mono text-muted-foreground">
                  当前 v{updateState?.current_version || '1.4.0'}
                </span>
                {updateState?.has_update && (
                  <>
                    <ArrowRight className="w-3.5 h-3.5 text-emerald-500" />
                    <span className="px-2 py-0.5 rounded-lg bg-emerald-500/15 border border-emerald-500/35 font-mono font-bold text-emerald-600 dark:text-emerald-300">
                      最新 v{updateState.latest_version}
                    </span>
                  </>
                )}
                {!updateState?.has_update && status === 'up_to_date' && (
                  <span className="inline-flex items-center space-x-1 text-emerald-600 dark:text-emerald-400 font-semibold">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>已是最新版本</span>
                  </span>
                )}
              </div>

              {updateState?.published_at && (
                <div className="flex items-center space-x-3 text-[11px] text-muted-foreground">
                  <span className="inline-flex items-center space-x-1">
                    <Clock className="w-3 h-3" />
                    <span>发布时间: {formatDate(updateState.published_at)}</span>
                  </span>
                  {updateState.asset_name && (
                    <span className="inline-flex items-center space-x-1 font-mono truncate max-w-[260px]">
                      <Package className="w-3 h-3 shrink-0" />
                      <span className="truncate">{updateState.asset_name}</span>
                    </span>
                  )}
                </div>
              )}
            </div>

            {updateState?.html_url && (
              <button
                type="button"
                onClick={() => void handleOpenReleasePage()}
                className="px-3 py-1.5 rounded-xl bg-surface-200/80 hover:bg-surface-200 border border-border text-[11px] text-foreground transition-colors inline-flex items-center space-x-1.5 shrink-0 self-start sm:self-center"
              >
                <ExternalLink className="w-3.5 h-3.5 text-emerald-500" />
                <span>GitHub Release</span>
              </button>
            )}
          </div>

          {/* 升级日志 (Release Notes) */}
          {(updateState?.has_update || updateState?.release_notes) && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-bold text-foreground">
                  {updateState.release_name || `v${updateState.latest_version}`} 升级日志
                </h3>
                {totalBytes > 0 && (
                  <span className="text-[11px] font-mono text-muted-foreground">
                    安装包大小: {formatBytes(totalBytes)}
                  </span>
                )}
              </div>
              <div className="p-4 rounded-2xl bg-surface-100/70 border border-border max-h-60 overflow-y-auto prose prose-sm dark:prose-invert max-w-none text-xs leading-relaxed">
                {updateState.release_notes ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {updateState.release_notes}
                  </ReactMarkdown>
                ) : (
                  <p className="text-muted-foreground">该版本未附带详细升级日志。</p>
                )}
              </div>
            </div>
          )}

          {/* 下载百分比进度条区域 */}
          {(status === 'downloading' || status === 'downloaded' || status === 'applying' || isRestarting) && (
            <div className="p-4 rounded-2xl bg-surface-100 border border-border space-y-2.5">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-foreground flex items-center space-x-1.5">
                  {status === 'downloading' ? (
                    <>
                      <Download className="w-4 h-4 text-emerald-500 animate-bounce" />
                      <span>正在下载新版本客户端...</span>
                    </>
                  ) : isRestarting || status === 'applying' ? (
                    <>
                      <RefreshCw className="w-4 h-4 text-emerald-500 animate-spin" />
                      <span>正在重启并加载新版本...</span>
                    </>
                  ) : (
                    <>
                      <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                      <span>新版本客户端下载完毕，请重启客户端以加载新版本</span>
                    </>
                  )}
                </span>
                <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">
                  {progressPct.toFixed(1)}%
                </span>
              </div>

              {/* 进度条轨道 */}
              <div className="w-full h-2.5 rounded-full bg-surface-200 overflow-hidden border border-border/60">
                <div
                  className={`h-full rounded-full transition-all duration-300 ${
                    status === 'downloaded' || isRestarting || status === 'applying'
                      ? 'bg-emerald-500'
                      : 'bg-gradient-to-r from-emerald-500 to-teal-400'
                  }`}
                  style={{ width: `${progressPct}%` }}
                />
              </div>

              <div className="flex items-center justify-between text-[11px] font-mono text-muted-foreground">
                <span>
                  {formatBytes(downloadedBytes)} / {totalBytes > 0 ? formatBytes(totalBytes) : '未知大小'}
                </span>
                <span>
                  {status === 'downloading' && speedBps > 0 ? `${formatBytes(speedBps)}/s` : status === 'downloaded' ? '校验通过' : ''}
                </span>
              </div>
            </div>
          )}

          {/* 重启引导提示 */}
          {restartMessage && (
            <div className="p-3.5 rounded-2xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-700 dark:text-emerald-300 text-xs flex items-center space-x-2">
              <RefreshCw className={`w-4 h-4 shrink-0 ${isRestarting ? 'animate-spin' : ''}`} />
              <span>{restartMessage}</span>
            </div>
          )}

          {/* 错误提示 */}
          {errorMsg && (
            <div className="p-3.5 rounded-2xl bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-300 text-xs flex items-start space-x-2">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <span className="break-anywhere">{errorMsg}</span>
            </div>
          )}
        </div>

        {/* 底部操作按钮区 */}
        <div className="px-6 py-4 border-t border-border/70 bg-surface-100/80 flex items-center justify-between shrink-0">
          <div className="text-[11px] text-muted-foreground">
            {updateState?.install_mode ? `运行形态: ${updateState.install_mode}` : '自动匹配适配当前平台的客户端产物'}
          </div>

          <div className="flex items-center space-x-2.5">
            {!isRestarting && (
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 rounded-xl bg-surface-200 hover:bg-surface-100 border border-border text-xs font-medium text-foreground transition-colors"
              >
                {status === 'downloaded' ? '稍后重启' : '关闭'}
              </button>
            )}

            {updateState?.has_update && (status === 'available' || status === 'error') && Boolean(updateState?.download_url) && (
              <button
                type="button"
                onClick={() => void handleStartDownload(status === 'error')}
                className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition-all shadow-md shadow-emerald-600/20 flex items-center space-x-1.5"
              >
                <Download className="w-3.5 h-3.5" />
                <span>{status === 'error' ? '重新下载升级包' : '立即升级'}</span>
              </button>
            )}

            {status === 'downloaded' && !isRestarting && (
              <button
                type="button"
                onClick={() => void handleApplyAndRestart()}
                className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition-all shadow-md shadow-emerald-600/25 flex items-center space-x-1.5 animate-pulse"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>立即重启并加载新版本</span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
