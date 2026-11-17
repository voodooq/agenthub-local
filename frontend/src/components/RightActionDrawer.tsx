'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  CheckCircle2,
  Cpu,
  HardDrive,
  RefreshCw,
  Search,
  ShieldAlert,
  Wifi,
  X,
  Zap,
  Terminal,
  Square,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface RightActionDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  pendingCountChange?: (count: number) => void;
}

interface ManagedProcessItem {
  proc_id: string;
  workspace_id: string;
  command: string;
  pid: number;
  status: string;
  exit_code?: number | null;
  running_seconds: number;
  port_hints: number[];
  log_lines_count?: number;
  has_errors: boolean;
}

interface HardwareProcessItem {
  pid: number;
  name: string;
  category: 'dev' | 'design' | 'general' | string;
  cpu_percent: number;
  memory_mb: number;
  status: string;
}

interface HardwareDiskItem {
  mount: string;
  device: string;
  fstype: string;
  total_gb: number;
  used_gb: number;
  free_gb: number;
  percent: number;
}

interface HardwareSnapshot {
  os: string;
  cpu: {
    usage_percent: number;
    cores_physical: number;
    cores_logical: number;
    frequency_mhz: number;
  };
  memory: {
    total_gb: number;
    used_gb: number;
    available_gb: number;
    percent: number;
    swap_percent: number;
  };
  disk: HardwareDiskItem[];
  gpu: {
    detected: boolean;
    vendor: string;
    name: string;
    load_percent: number | null;
    vram_used_mb: number;
    vram_total_mb: number;
    temperature_c: number | null;
  };
  network?: {
    upload_kbps: number;
    download_kbps: number;
    total_sent_gb: number;
    total_recv_gb: number;
    packets_sent: number;
    packets_recv: number;
  };
  top_processes: HardwareProcessItem[];
}

function formatThroughput(kbps: number): string {
  const safeVal = Number.isFinite(kbps) && kbps > 0 ? kbps : 0;
  if (safeVal >= 1024) {
    return `${(safeVal / 1024).toFixed(2)} MB/s`;
  }
  return `${safeVal.toFixed(1)} KB/s`;
}

function getUsageColor(percent: number): { text: string; bar: string } {
  if (percent >= 85) {
    return { text: 'text-rose-500', bar: 'bg-rose-500' };
  }
  if (percent >= 65) {
    return { text: 'text-amber-500', bar: 'bg-amber-500' };
  }
  return { text: 'text-emerald-500', bar: 'bg-emerald-500' };
}

/**
 * 右侧系统实时监控与智能治理中心抽屉
 * 实时展示宿主机 CPU / RAM / GPU / 网络 / 磁盘及 Top 进程，并集成人工审批锁提案流。
 */
export function RightActionDrawer({ isOpen, onClose, pendingCountChange }: RightActionDrawerProps) {
  const [snapshot, setSnapshot] = useState<HardwareSnapshot | null>(null);
  const [loadingSnapshot, setLoadingSnapshot] = useState(false);
  const [proposals, setProposals] = useState<any[]>([]);
  const [loadingProposals, setLoadingProposals] = useState(false);
  const [executingId, setExecutingId] = useState<string | null>(null);
  const [proposingPid, setProposingPid] = useState<number | null>(null);
  const [proposingTop, setProposingTop] = useState(false);
  const [portInput, setPortInput] = useState('');
  const [inspectingPort, setInspectingPort] = useState(false);
  const [actionFeedback, setActionFeedback] = useState<{ ok: boolean; msg: string } | null>(null);
  const [managedProcesses, setManagedProcesses] = useState<ManagedProcessItem[]>([]);
  const [loadingProcesses, setLoadingProcesses] = useState(false);
  const [stoppingProcId, setStoppingProcId] = useState<string | null>(null);

  const fetchManagedProcesses = useCallback(async () => {
    try {
      const res = await apiFetch('/api/v1/processes');
      if (res.ok) {
        const data = await res.json();
        setManagedProcesses(data.processes || []);
      }
    } catch (e) {
      console.warn('获取托管进程失败:', e);
    }
  }, []);

  const handleStopManagedProcess = async (procId: string) => {
    setStoppingProcId(procId);
    try {
      const res = await apiFetch(`/api/v1/processes/${procId}/stop`, { method: 'POST' });
      if (res.ok) {
        setActionFeedback({ ok: true, msg: `已成功终止后台进程树 [${procId}]。` });
        await fetchManagedProcesses();
      } else {
        const err = await res.json().catch(() => ({}));
        setActionFeedback({ ok: false, msg: err.detail || '终止后台进程失败' });
      }
    } catch (e: any) {
      setActionFeedback({ ok: false, msg: e?.message || '终止进程异常' });
    } finally {
      setStoppingProcId(null);
    }
  };

  const fetchProposals = useCallback(async () => {
    setLoadingProposals(true);
    try {
      const res = await apiFetch('/api/v1/system/proposals');
      if (res.ok) {
        const list = await res.json();
        setProposals(list);
        const pending = list.filter((p: any) => p.status === 'pending').length;
        if (pendingCountChange) pendingCountChange(pending);
      }
    } catch (e) {
      console.warn('获取提案失败:', e);
    } finally {
      setLoadingProposals(false);
    }
  }, [pendingCountChange]);

  const fetchSnapshot = useCallback(async (silent: boolean = false) => {
    if (!silent) setLoadingSnapshot(true);
    try {
      const res = await apiFetch('/api/v1/system/snapshot');
      if (res.ok) {
        const json: HardwareSnapshot = await res.json();
        setSnapshot(json);
      }
    } catch (e) {
      console.warn('获取硬件快照失败:', e);
    } finally {
      if (!silent) setLoadingSnapshot(false);
    }
  }, []);

  useEffect(() => {
    fetchProposals();
  }, [fetchProposals]);

  useEffect(() => {
    if (!isOpen) return;
    setActionFeedback(null);
    fetchSnapshot(false);
    fetchProposals();
    fetchManagedProcesses();

    // 抽屉展开期间每 3 秒自动刷新实时硬件与网络指标及活跃后台托管进程
    const timer = setInterval(() => {
      fetchSnapshot(true);
      fetchManagedProcesses();
    }, 3000);
    return () => clearInterval(timer);
  }, [isOpen, fetchSnapshot, fetchProposals, fetchManagedProcesses]);

  const handleRefreshAll = () => {
    fetchSnapshot(false);
    fetchProposals();
  };

  const handleProposeProcess = async (proc: HardwareProcessItem) => {
    setProposingPid(proc.pid);
    setActionFeedback(null);
    try {
      const res = await apiFetch('/api/v1/system/governance/propose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scenario: proc.category === 'design' ? 'design' : 'dev',
          targets: [proc],
          reason: `用户在系统实时监控中提请治理高占用进程 [${proc.name}] (PID: ${proc.pid}，内存 ${proc.memory_mb} MB)`,
        }),
      });
      if (res.ok) {
        setActionFeedback({
          ok: true,
          msg: `已针对进程 [${proc.name}] (PID: ${proc.pid}) 生成待审批治理提案，请在下方核准执行。`,
        });
        await fetchProposals();
      } else {
        const err = await res.json().catch(() => ({}));
        setActionFeedback({ ok: false, msg: err.detail || '生成治理提案失败' });
      }
    } catch (e: any) {
      setActionFeedback({ ok: false, msg: e?.message || '网络请求异常' });
    } finally {
      setProposingPid(null);
    }
  };

  const handleQuickOptimizeTop = async () => {
    const topTargets = (snapshot?.top_processes || []).slice(0, 3);
    if (topTargets.length === 0) return;

    setProposingTop(true);
    setActionFeedback(null);
    try {
      const res = await apiFetch('/api/v1/system/governance/propose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scenario: 'system',
          targets: topTargets,
          reason: `系统实时监控体检：汇总 Top ${topTargets.length} 高内存占用进程生成待确认清理方案`,
        }),
      });
      if (res.ok) {
        setActionFeedback({
          ok: true,
          msg: `已汇总 Top ${topTargets.length} 高占用进程生成待审批提案（系统核心受保护进程执行时将自动跳过）。`,
        });
        await fetchProposals();
      } else {
        const err = await res.json().catch(() => ({}));
        setActionFeedback({ ok: false, msg: err.detail || '一键生成提案失败' });
      }
    } catch (e: any) {
      setActionFeedback({ ok: false, msg: e?.message || '生成提案异常' });
    } finally {
      setProposingTop(false);
    }
  };

  const handleInspectPort = async (e: React.FormEvent) => {
    e.preventDefault();
    const portNum = Number.parseInt(portInput.trim(), 10);
    if (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535) {
      setActionFeedback({ ok: false, msg: '请输入有效端口号（1 ~ 65535 整数）' });
      return;
    }

    setInspectingPort(true);
    setActionFeedback(null);
    try {
      const res = await apiFetch('/api/v1/system/port/inspect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ port: portNum }),
      });
      const json = await res.json().catch(() => ({}));
      if (res.ok) {
        setActionFeedback({
          ok: true,
          msg: json.message || (json.occupied ? `端口 ${portNum} 已被占用，已生成释放提案` : `端口 ${portNum} 当前空闲`),
        });
        if (json.occupied) {
          await fetchProposals();
        }
      } else {
        setActionFeedback({ ok: false, msg: json.detail || '端口排查失败' });
      }
    } catch (err: any) {
      setActionFeedback({ ok: false, msg: err?.message || '端口排查请求异常' });
    } finally {
      setInspectingPort(false);
    }
  };

  const handleApprove = async (id: string) => {
    setExecutingId(id);
    setActionFeedback(null);
    try {
      const res = await apiFetch(`/api/v1/system/proposals/${id}/approve`, { method: 'POST' });
      if (res.ok) {
        setActionFeedback({ ok: true, msg: `提案 [${id}] 已确认执行完成，已记录前后内存对比。` });
        await fetchProposals();
        await fetchSnapshot(true);
      } else {
        const err = await res.json().catch(() => ({}));
        setActionFeedback({ ok: false, msg: err.detail || '执行提案失败' });
      }
    } catch (e: any) {
      setActionFeedback({ ok: false, msg: `执行异常: ${e.message}` });
    } finally {
      setExecutingId(null);
    }
  };

  const handleReject = async (id: string) => {
    try {
      const res = await apiFetch(`/api/v1/system/proposals/${id}/reject`, { method: 'POST' });
      if (res.ok) {
        await fetchProposals();
      }
    } catch (e) {
      console.warn('拒绝提案失败:', e);
    }
  };

  if (!isOpen) return null;

  const pendingProposals = proposals.filter((p) => p.status === 'pending');
  const finishedProposals = proposals.filter((p) => p.status !== 'pending');

  const cpuPercent = snapshot?.cpu?.usage_percent ?? 0;
  const cpuColors = getUsageColor(cpuPercent);

  const memPercent = snapshot?.memory?.percent ?? 0;
  const memColors = getUsageColor(memPercent);

  const gpu = snapshot?.gpu;
  const gpuVramPercent =
    gpu && gpu.vram_total_mb > 0 ? Math.min(100, Math.round((gpu.vram_used_mb / gpu.vram_total_mb) * 100)) : 0;
  const gpuColors = getUsageColor(gpu?.load_percent ?? gpuVramPercent);

  const net = snapshot?.network;

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/50 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl h-full bg-surface-card border-l border-border shadow-2xl flex flex-col animate-slide-left text-foreground select-text"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 顶部标题栏 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border bg-surface-100 shrink-0">
          <div className="flex items-center space-x-2.5 min-w-0">
            <Activity className="w-5 h-5 text-emerald-500 animate-pulse shrink-0" />
            <div className="min-w-0">
              <div className="flex items-center space-x-2">
                <h3 className="text-sm font-bold text-foreground truncate">系统实时监控与智能治理</h3>
                {pendingProposals.length > 0 && (
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-600 dark:text-amber-300 border border-amber-500/30 font-bold shrink-0">
                    {pendingProposals.length} 待审核
                  </span>
                )}
              </div>
              <p className="text-[11px] text-muted-foreground mt-0.5 truncate">
                宿主机：{snapshot?.os || '探测中...'} · 每 3 秒自动刷新硬件遥测
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-1 shrink-0">
            <button
              type="button"
              onClick={handleRefreshAll}
              disabled={loadingSnapshot || loadingProposals}
              className="p-1.5 rounded-lg hover:bg-surface-200 text-muted-foreground hover:text-foreground transition-colors"
              title="立即刷新监控指标与提案"
            >
              <RefreshCw className={`w-4 h-4 ${loadingSnapshot || loadingProposals ? 'animate-spin' : ''}`} />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-surface-200 text-muted-foreground hover:text-foreground transition-colors"
              title="关闭系统监控"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 滚动内容区域 */}
        <div className="flex-1 overflow-y-auto p-4 space-y-5 text-xs">
          {/* ==================== 1. 实时硬件监控面板 ==================== */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider">
                宿主机实时硬件与网络状态
              </h4>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 font-mono">
                LIVE · 3s
              </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {/* CPU 算力卡片 */}
              <div className="p-3.5 rounded-2xl bg-surface-100 border border-border space-y-2 shadow-xs">
                <div className="flex items-center justify-between text-muted-foreground">
                  <span className="flex items-center space-x-1.5 font-medium text-foreground">
                    <Cpu className="w-4 h-4 text-emerald-500" />
                    <span>CPU 算力</span>
                  </span>
                  <span className="text-[10px] font-mono">
                    {snapshot?.cpu?.cores_physical || 0}核 / {snapshot?.cpu?.cores_logical || 0}线程
                  </span>
                </div>
                <div className="flex items-baseline justify-between">
                  <span className={`text-2xl font-bold font-mono ${cpuColors.text}`}>
                    {cpuPercent.toFixed(1)}%
                  </span>
                  <span className="text-[11px] font-mono text-muted-foreground">
                    {snapshot?.cpu?.frequency_mhz || 0} MHz
                  </span>
                </div>
                <div className="w-full h-1.5 rounded-full bg-surface-200 overflow-hidden">
                  <div
                    className={`h-full ${cpuColors.bar} transition-all duration-500`}
                    style={{ width: `${Math.min(100, cpuPercent)}%` }}
                  />
                </div>
              </div>

              {/* 物理内存 (RAM) 卡片 */}
              <div className="p-3.5 rounded-2xl bg-surface-100 border border-border space-y-2 shadow-xs">
                <div className="flex items-center justify-between text-muted-foreground">
                  <span className="flex items-center space-x-1.5 font-medium text-foreground">
                    <Activity className="w-4 h-4 text-purple-500" />
                    <span>物理内存 (RAM)</span>
                  </span>
                  <span className="text-[10px] font-mono">
                    {snapshot?.memory?.used_gb ?? 0} / {snapshot?.memory?.total_gb ?? 0} GB
                  </span>
                </div>
                <div className="flex items-baseline justify-between">
                  <span className={`text-2xl font-bold font-mono ${memColors.text}`}>
                    {memPercent.toFixed(1)}%
                  </span>
                  <span className="text-[11px] font-mono text-muted-foreground">
                    可用 {snapshot?.memory?.available_gb ?? 0} GB · Swap {snapshot?.memory?.swap_percent ?? 0}%
                  </span>
                </div>
                <div className="w-full h-1.5 rounded-full bg-surface-200 overflow-hidden">
                  <div
                    className={`h-full ${memColors.bar} transition-all duration-500`}
                    style={{ width: `${Math.min(100, memPercent)}%` }}
                  />
                </div>
              </div>

              {/* GPU 状态卡片 */}
              <div className="p-3.5 rounded-2xl bg-surface-100 border border-border space-y-2 shadow-xs">
                <div className="flex items-center justify-between text-muted-foreground">
                  <span className="flex items-center space-x-1.5 font-medium text-foreground">
                    <Zap className="w-4 h-4 text-amber-500" />
                    <span>GPU 显卡</span>
                  </span>
                  <span className="text-[10px] truncate max-w-[140px] font-mono" title={gpu?.name}>
                    {gpu?.name || '探测中...'}
                  </span>
                </div>
                <div className="flex items-baseline justify-between">
                  <span className={`text-2xl font-bold font-mono ${gpuColors.text}`}>
                    {gpu?.load_percent !== null && gpu?.load_percent !== undefined
                      ? `${gpu.load_percent}%`
                      : gpu && gpu.vram_total_mb > 0
                        ? `${gpuVramPercent}%`
                        : '核显'}
                  </span>
                  <span className="text-[11px] font-mono text-muted-foreground">
                    {gpu && gpu.vram_total_mb > 0
                      ? `显存 ${Math.round(gpu.vram_used_mb)} / ${Math.round(gpu.vram_total_mb)} MB${
                          gpu.temperature_c ? ` · ${gpu.temperature_c}°C` : ''
                        }`
                      : '共享系统内存'}
                  </span>
                </div>
                <div className="w-full h-1.5 rounded-full bg-surface-200 overflow-hidden">
                  <div
                    className={`h-full ${gpuColors.bar} transition-all duration-500`}
                    style={{ width: `${gpu && gpu.vram_total_mb > 0 ? gpuVramPercent : gpu?.load_percent ?? 0}%` }}
                  />
                </div>
              </div>

              {/* 实时网络状态卡片 */}
              <div className="p-3.5 rounded-2xl bg-surface-100 border border-border space-y-2 shadow-xs">
                <div className="flex items-center justify-between text-muted-foreground">
                  <span className="flex items-center space-x-1.5 font-medium text-foreground">
                    <Wifi className="w-4 h-4 text-sky-500" />
                    <span>网络吞吐</span>
                  </span>
                  <span className="text-[10px] font-mono">
                    累计 ↓{net?.total_recv_gb ?? 0}G / ↑{net?.total_sent_gb ?? 0}G
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 pt-0.5">
                  <div className="p-2 rounded-xl bg-surface-200/60 border border-border/60">
                    <div className="text-[10px] text-muted-foreground">下行速率 ↓</div>
                    <div className="text-sm font-bold font-mono text-emerald-500 mt-0.5">
                      {formatThroughput(net?.download_kbps ?? 0)}
                    </div>
                  </div>
                  <div className="p-2 rounded-xl bg-surface-200/60 border border-border/60">
                    <div className="text-[10px] text-muted-foreground">上行速率 ↑</div>
                    <div className="text-sm font-bold font-mono text-sky-500 mt-0.5">
                      {formatThroughput(net?.upload_kbps ?? 0)}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* 硬盘分区状态列表 */}
            <div className="p-3.5 rounded-2xl bg-surface-100 border border-border space-y-2.5 shadow-xs">
              <div className="flex items-center justify-between">
                <span className="flex items-center space-x-1.5 font-medium text-foreground">
                  <HardDrive className="w-4 h-4 text-amber-500" />
                  <span>硬盘分区状态</span>
                </span>
                <span className="text-[10px] text-muted-foreground font-mono">
                  共 {snapshot?.disk?.length ?? 0} 个挂载分区
                </span>
              </div>

              {(!snapshot?.disk || snapshot.disk.length === 0) ? (
                <div className="text-[11px] text-muted-foreground">正在采集磁盘信息...</div>
              ) : (
                <div className="space-y-2">
                  {snapshot.disk.map((d) => {
                    const dc = getUsageColor(d.percent);
                    return (
                      <div key={d.mount} className="space-y-1">
                        <div className="flex items-center justify-between text-[11px]">
                          <span className="font-mono font-semibold text-foreground">
                            {d.mount} <span className="text-muted-foreground font-normal">({d.fstype})</span>
                          </span>
                          <span className="font-mono text-muted-foreground">
                            已用 {d.used_gb} / {d.total_gb} GB · 剩余 <strong className="text-foreground">{d.free_gb} GB</strong> (
                            <span className={dc.text}>{d.percent}%</span>)
                          </span>
                        </div>
                        <div className="w-full h-1.5 rounded-full bg-surface-200 overflow-hidden">
                          <div
                            className={`h-full ${dc.bar} transition-all duration-500`}
                            style={{ width: `${Math.min(100, d.percent)}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </section>

          {/* ==================== 1.5. 活跃长驻托管进程 ==================== */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-1.5">
                <Terminal className="w-4 h-4 text-foreground" />
                <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider">
                  活跃长驻托管进程 ({managedProcesses.filter((p) => p.status === 'running' || p.status === 'starting').length})
                </h4>
              </div>
              <span className="text-[10px] text-muted-foreground font-mono">
                并发上限 5 个
              </span>
            </div>

            <div className="p-3.5 rounded-2xl bg-surface-100 border border-border space-y-2.5">
              {managedProcesses.length === 0 ? (
                <div className="text-center py-4 text-muted-foreground text-[11px]">
                  当前工作区暂无运行中的长驻开发服务或后台进程
                </div>
              ) : (
                <div className="space-y-2">
                  {managedProcesses.map((proc) => {
                    const isAlive = proc.status === 'running' || proc.status === 'starting';
                    return (
                      <div
                        key={proc.proc_id}
                        className="p-2.5 rounded-xl bg-surface-200 border border-border flex items-center justify-between gap-3 text-xs"
                      >
                        <div className="min-w-0 flex-1 space-y-1">
                          <div className="flex items-center space-x-2">
                            <span className="font-mono font-bold text-foreground truncate max-w-[140px]">
                              {proc.proc_id}
                            </span>
                            <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-surface-100 border border-border text-muted-foreground">
                              PID: {proc.pid}
                            </span>
                            <span
                              className={`text-[10px] px-1.5 py-0.2 rounded border font-mono ${
                                isAlive
                                  ? 'bg-surface-100 text-foreground border-foreground/30 font-semibold'
                                  : 'bg-surface-100 text-muted-foreground border-border'
                              }`}
                            >
                              {proc.status}
                            </span>
                            {proc.port_hints && proc.port_hints.length > 0 && (
                              <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-surface-100 border border-border text-foreground font-bold">
                                {proc.port_hints.map((p) => `:${p}`).join(' ')}
                              </span>
                            )}
                          </div>
                          <div className="font-mono text-[11px] text-muted-foreground truncate" title={proc.command}>
                            {proc.command}
                          </div>
                        </div>

                        {isAlive && (
                          <button
                            type="button"
                            onClick={() => handleStopManagedProcess(proc.proc_id)}
                            disabled={stoppingProcId === proc.proc_id}
                            className="px-2.5 py-1 rounded-lg bg-surface-100 hover:bg-surface-50 border border-border text-foreground text-[11px] font-medium transition-colors shrink-0 flex items-center space-x-1"
                            title="终止该进程及其派生的整个子进程树"
                          >
                            <Square className="w-3 h-3 text-foreground" />
                            <span>{stoppingProcId === proc.proc_id ? '终止中' : '终止'}</span>
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </section>

          {/* 操作反馈提示条 */}
          {actionFeedback && (
            <div
              className={`p-3 rounded-xl text-xs flex items-start space-x-2 border ${
                actionFeedback.ok
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300 border-emerald-500/25'
                  : 'bg-rose-500/10 text-rose-600 dark:text-rose-300 border-rose-500/25'
              }`}
            >
              {actionFeedback.ok ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
              ) : (
                <AlertTriangle className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
              )}
              <span className="leading-relaxed break-anywhere">{actionFeedback.msg}</span>
            </div>
          )}

          {/* ==================== 2. Top 高占用进程与快速提案生成 ==================== */}
          <section className="space-y-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider">
                Top 高占用进程（点击生成待审核治理提案）
              </h4>
              <button
                type="button"
                onClick={handleQuickOptimizeTop}
                disabled={proposingTop || !snapshot?.top_processes?.length}
                className="px-2.5 py-1 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/30 text-amber-700 dark:text-amber-300 font-semibold text-[11px] transition-colors disabled:opacity-50"
              >
                {proposingTop ? '生成中...' : '一键体检高占用进程'}
              </button>
            </div>

            {/* 端口占用快速排查栏 */}
            <form onSubmit={handleInspectPort} className="flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="w-3.5 h-3.5 text-muted-foreground absolute left-2.5 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={portInput}
                  onChange={(e) => setPortInput(e.target.value)}
                  placeholder="输入端口号排查占用并生成释放提案（如 3100、8000）"
                  className="w-full pl-8 pr-3 py-1.5 rounded-xl bg-surface-100 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-emerald-500"
                />
              </div>
              <button
                type="submit"
                disabled={inspectingPort || !portInput.trim()}
                className="px-3 py-1.5 rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-xs font-semibold text-foreground transition-colors disabled:opacity-50 shrink-0"
              >
                {inspectingPort ? '排查中...' : '排查端口'}
              </button>
            </form>

            {/* 进程列表 */}
            <div className="rounded-2xl bg-surface-100 border border-border divide-y divide-border overflow-hidden">
              {(!snapshot?.top_processes || snapshot.top_processes.length === 0) ? (
                <div className="p-3 text-center text-muted-foreground text-[11px]">正在扫描宿主机进程...</div>
              ) : (
                snapshot.top_processes.slice(0, 6).map((proc) => (
                  <div
                    key={proc.pid}
                    className="px-3 py-2 flex items-center justify-between gap-2 hover:bg-surface-200/50 transition-colors"
                  >
                    <div className="min-w-0 flex-1 flex items-center space-x-2">
                      <span
                        className={`text-[9px] px-1.5 py-0.5 rounded font-mono shrink-0 ${
                          proc.category === 'dev'
                            ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/25'
                            : proc.category === 'design'
                              ? 'bg-purple-500/15 text-purple-600 dark:text-purple-300 border border-purple-500/25'
                              : 'bg-surface-200 text-muted-foreground'
                        }`}
                      >
                        {proc.category}
                      </span>
                      <span className="font-mono font-semibold text-foreground truncate" title={proc.name}>
                        {proc.name}
                      </span>
                      <span className="text-[10px] text-muted-foreground font-mono shrink-0">
                        PID {proc.pid}
                      </span>
                    </div>

                    <div className="flex items-center space-x-2.5 shrink-0">
                      <span className="font-mono text-amber-600 dark:text-amber-300 font-semibold">
                        {proc.memory_mb} MB
                      </span>
                      <button
                        type="button"
                        onClick={() => handleProposeProcess(proc)}
                        disabled={proposingPid === proc.pid}
                        className="px-2 py-1 rounded-lg bg-surface-200 hover:bg-amber-500/20 hover:text-amber-600 dark:hover:text-amber-300 border border-border text-[10px] font-medium transition-colors disabled:opacity-50"
                      >
                        {proposingPid === proc.pid ? '创建中...' : '提请治理'}
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>

          {/* ==================== 3. 待审批优化清单（遵循人类确认锁） ==================== */}
          <section className="space-y-3">
            <h4 className="text-[11px] font-bold text-muted-foreground tracking-wider uppercase flex items-center justify-between">
              <span className="flex items-center space-x-1.5">
                <ShieldAlert className="w-3.5 h-3.5 text-amber-500" />
                <span>待审批优化清单 ({pendingProposals.length})</span>
              </span>
              <span className="text-[10px] font-normal text-muted-foreground">遵循人类确认锁 · 确认后才会物理执行</span>
            </h4>

            {pendingProposals.length === 0 ? (
              <div className="p-4 rounded-2xl bg-surface-100 border border-border text-center text-muted-foreground text-xs space-y-1">
                <div>暂无待审批动作，系统运行良好 ✨</div>
                <div className="text-[11px] opacity-80">
                  可通过上方点击进程「提请治理」、排查占用端口，或在对话框发送 <code className="px-1 rounded bg-surface-200">/optimize-dev</code> 生成待审动作
                </div>
              </div>
            ) : (
              pendingProposals.map((prop) => (
                <div
                  key={prop.id}
                  className="p-4 rounded-2xl bg-surface-100 border border-amber-500/40 shadow-sm space-y-3 relative overflow-hidden"
                >
                  <div className="absolute top-0 left-0 w-1 h-full bg-amber-500" />
                  <div className="flex items-start justify-between">
                    <div className="space-y-1">
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-300 border border-amber-500/25">
                        {prop.id}
                      </span>
                      <h5 className="font-semibold text-foreground text-xs mt-1">{prop.title}</h5>
                    </div>
                    <span className="text-[10px] text-muted-foreground font-mono">{prop.created_at?.slice(11)}</span>
                  </div>

                  <p className="text-muted-foreground leading-relaxed">{prop.description}</p>

                  {prop.targets && prop.targets.length > 0 && (
                    <div className="p-2.5 rounded-xl bg-surface-200/60 border border-border/70 space-y-1 font-mono text-[11px]">
                      {prop.targets.slice(0, 3).map((t: any, idx: number) => (
                        <div key={idx} className="flex items-center justify-between text-muted-foreground">
                          <span className="truncate max-w-[220px] text-foreground">{t.name || t.path}</span>
                          <div className="flex items-center space-x-2 shrink-0">
                            {t.pid && <span>PID: {t.pid}</span>}
                            {t.memory_mb && <span className="text-amber-600 dark:text-amber-300">{t.memory_mb} MB</span>}
                          </div>
                        </div>
                      ))}
                      {prop.targets.length > 3 && (
                        <div className="text-[10px] text-muted-foreground text-right">
                          等 {prop.targets.length} 项目标
                        </div>
                      )}
                    </div>
                  )}

                  <div className="flex items-center justify-between pt-1">
                    <span className="text-[11px] text-emerald-600 dark:text-emerald-400 font-medium">
                      收益: {prop.estimated_gain}
                    </span>
                    <div className="flex items-center space-x-2">
                      <button
                        type="button"
                        onClick={() => handleReject(prop.id)}
                        disabled={executingId === prop.id}
                        className="px-2.5 py-1.5 rounded-lg hover:bg-surface-200 text-muted-foreground hover:text-foreground transition-colors"
                      >
                        驳回
                      </button>
                      <button
                        type="button"
                        onClick={() => handleApprove(prop.id)}
                        disabled={executingId === prop.id}
                        className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium shadow-sm flex items-center space-x-1.5 disabled:opacity-50"
                      >
                        {executingId === prop.id ? (
                          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <CheckCircle2 className="w-3.5 h-3.5" />
                        )}
                        <span>{executingId === prop.id ? '执行中...' : '一键确认执行'}</span>
                      </button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </section>

          {/* ==================== 4. 已完成优化与复盘区 ==================== */}
          {finishedProposals.length > 0 && (
            <section className="space-y-2.5 pt-1">
              <h4 className="text-[11px] font-bold text-muted-foreground tracking-wider uppercase">
                已完成优化与复盘 ({finishedProposals.length})
              </h4>

              {finishedProposals.map((prop) => (
                <div
                  key={prop.id}
                  className="p-3.5 rounded-2xl bg-surface-100 border border-border space-y-2"
                >
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-foreground">{prop.title}</span>
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded font-semibold ${
                        prop.status === 'approved'
                          ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300'
                          : 'bg-rose-500/15 text-rose-600 dark:text-rose-300'
                      }`}
                    >
                      {prop.status === 'approved' ? '执行成功' : '已驳回'}
                    </span>
                  </div>

                  {prop.result_diff && prop.status === 'approved' && (
                    <div className="p-2.5 rounded-xl bg-surface-200/60 border border-border/70 space-y-1.5 text-[11px]">
                      <div className="flex items-center justify-between text-muted-foreground font-mono">
                        <span>内存变化:</span>
                        <span className="flex items-center text-emerald-600 dark:text-emerald-400 font-semibold">
                          {prop.result_diff.memory_before_percent}% <ArrowDownRight className="w-3.5 h-3.5" />{' '}
                          {prop.result_diff.memory_after_percent}% (-{prop.result_diff.memory_freed_gb} GB)
                        </span>
                      </div>
                      {prop.result_diff.execution_logs && (
                        <div className="text-[10px] text-muted-foreground space-y-0.5 font-mono">
                          {prop.result_diff.execution_logs.slice(0, 3).map((log: string, idx: number) => (
                            <div key={idx} className="truncate">
                              ✓ {log}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
