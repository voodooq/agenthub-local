'use client';

import React, { useState, useEffect } from 'react';
import { Cpu, HardDrive, Activity, ChevronUp, ChevronDown, RefreshCw, X, AlertTriangle } from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface HardwareHudProps {
  isOpen: boolean;
  onToggle: () => void;
  onProposeProcessKill?: (proc: { pid: number; name: string; memory_mb: number }) => void;
}

export function HardwareHud({ isOpen, onToggle, onProposeProcessKill }: HardwareHudProps) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  // 展开时按需拉取瞬时快照并支持手动刷新
  const fetchSnapshot = async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/api/v1/system/snapshot');
      if (res.ok) {
        const json = await res.json();
        setData(json);
      }
    } catch (e) {
      console.warn('获取硬件快照失败:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchSnapshot();
      // 开启轮询，每 3 秒刷新一次
      const timer = setInterval(fetchSnapshot, 3000);
      return () => clearInterval(timer);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const memPercent = data?.memory?.percent || 0;
  const memColor = memPercent > 85 ? 'text-rose-400' : memPercent > 65 ? 'text-amber-400' : 'text-emerald-400';
  const memBarColor = memPercent > 85 ? 'bg-rose-500' : memPercent > 65 ? 'bg-amber-500' : 'bg-emerald-500';

  const cpuPercent = data?.cpu?.usage_percent || 0;
  const cpuColor = cpuPercent > 80 ? 'text-rose-400' : cpuPercent > 50 ? 'text-amber-400' : 'text-emerald-400';

  return (
    <div className="w-full bg-surface-100/95 backdrop-blur-md border-t border-white/10 p-4 transition-all animate-slide-up shadow-2xl z-20">
      <div className="max-w-6xl mx-auto space-y-4">
        {/* 顶部标题与控制器 */}
        <div className="flex items-center justify-between border-b border-white/10 pb-2.5">
          <div className="flex items-center space-x-2.5">
            <Activity className="w-4 h-4 text-accent animate-pulse" />
            <h4 className="text-sm font-semibold text-slate-100">宿主机实时硬件仪表盘</h4>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-white/5 text-slate-400 border border-white/10">
              平台: {data?.os || '探测中...'}
            </span>
          </div>

          <div className="flex items-center space-x-2">
            <button
              onClick={fetchSnapshot}
              disabled={loading}
              className="p-1.5 rounded-lg hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
              title="刷新指标"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={onToggle}
              className="p-1.5 rounded-lg hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
              title="收起仪表盘"
            >
              <ChevronDown className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 核心指标 4 宫格 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          {/* CPU 卡片 */}
          <div className="p-3 rounded-xl bg-surface-50/50 border border-white/5 space-y-1.5">
            <div className="flex items-center justify-between text-slate-400">
              <span className="flex items-center space-x-1.5">
                <Cpu className="w-3.5 h-3.5 text-emerald-400" />
                <span>CPU 算力</span>
              </span>
              <span className="text-[10px]">{data?.cpu?.cores_logical || 0} 线程</span>
            </div>
            <div className="flex items-baseline space-x-2">
              <span className={`text-xl font-bold font-mono ${cpuColor}`}>{cpuPercent}%</span>
              <span className="text-[11px] text-slate-500">{data?.cpu?.frequency_mhz || 0} MHz</span>
            </div>
            <div className="w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
              <div className="h-full bg-emerald-500 transition-all duration-500" style={{ width: `${Math.min(100, cpuPercent)}%` }} />
            </div>
          </div>

          {/* 内存卡片 */}
          <div className="p-3 rounded-xl bg-surface-50/50 border border-white/5 space-y-1.5">
            <div className="flex items-center justify-between text-slate-400">
              <span className="flex items-center space-x-1.5">
                <Activity className="w-3.5 h-3.5 text-purple-400" />
                <span>物理内存 (RAM)</span>
              </span>
              <span className="text-[10px]">{data?.memory?.used_gb || 0} / {data?.memory?.total_gb || 0} GB</span>
            </div>
            <div className="flex items-baseline space-x-2">
              <span className={`text-xl font-bold font-mono ${memColor}`}>{memPercent}%</span>
              <span className="text-[11px] text-slate-500">空闲: {data?.memory?.available_gb || 0} GB</span>
            </div>
            <div className="w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
              <div className={`h-full ${memBarColor} transition-all duration-500`} style={{ width: `${Math.min(100, memPercent)}%` }} />
            </div>
          </div>

          {/* GPU 显存卡片 */}
          <div className="p-3 rounded-xl bg-surface-50/50 border border-white/5 space-y-1.5">
            <div className="flex items-center justify-between text-slate-400">
              <span className="flex items-center space-x-1.5">
                <Activity className="w-3.5 h-3.5 text-emerald-400" />
                <span>GPU 显存 (VRAM)</span>
              </span>
              <span className="text-[10px] truncate max-w-[90px]">{data?.gpu?.vendor || '通用'}</span>
            </div>
            <div className="flex items-baseline space-x-2">
              <span className="text-xl font-bold font-mono text-emerald-400">
                {data?.gpu?.vram_total_mb > 0
                  ? `${Math.round((data.gpu.vram_used_mb / data.gpu.vram_total_mb) * 100)}%`
                  : '核显'}
              </span>
              <span className="text-[11px] text-slate-500">
                {data?.gpu?.vram_total_mb > 0 ? `${data.gpu.vram_used_mb} MB` : '系统共享'}
              </span>
            </div>
            <div className="w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
              <div
                className="h-full bg-emerald-500 transition-all duration-500"
                style={{
                  width: `${
                    data?.gpu?.vram_total_mb > 0
                      ? Math.min(100, Math.round((data.gpu.vram_used_mb / data.gpu.vram_total_mb) * 100))
                      : 0
                  }%`,
                }}
              />
            </div>
          </div>

          {/* 磁盘卡片 */}
          <div className="p-3 rounded-xl bg-surface-50/50 border border-white/5 space-y-1.5">
            <div className="flex items-center justify-between text-slate-400">
              <span className="flex items-center space-x-1.5">
                <HardDrive className="w-3.5 h-3.5 text-amber-400" />
                <span>主系统磁盘</span>
              </span>
              <span className="text-[10px]">{data?.disk?.[0]?.mount || 'C:\\'}</span>
            </div>
            <div className="flex items-baseline space-x-2">
              <span className="text-xl font-bold font-mono text-amber-400">{data?.disk?.[0]?.percent || 0}%</span>
              <span className="text-[11px] text-slate-500">剩余: {data?.disk?.[0]?.free_gb || 0} GB</span>
            </div>
            <div className="w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
              <div
                className="h-full bg-amber-500 transition-all duration-500"
                style={{ width: `${Math.min(100, data?.disk?.[0]?.percent || 0)}%` }}
              />
            </div>
          </div>
        </div>

        {/* Top 高占用进程与一键治理区 */}
        {data?.top_processes && data.top_processes.length > 0 && (
          <div className="p-3 rounded-xl bg-surface-50/30 border border-white/5 space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-300">
              <span className="font-medium">Top 5 内存高占用进程（点击可生成治理待审批单）</span>
              <span className="text-[11px] text-slate-500">包含开发与设计工具链智能归类</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2">
              {data.top_processes.slice(0, 5).map((proc: any) => (
                <div
                  key={proc.pid}
                  onClick={() => onProposeProcessKill && onProposeProcessKill(proc)}
                  className="p-2 rounded-lg bg-surface-200/50 hover:bg-white/10 border border-white/5 cursor-pointer transition-all flex flex-col justify-between group"
                  title="点击生成治理提案"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-[11px] font-semibold text-slate-200 truncate max-w-[90px]">
                      {proc.name}
                    </span>
                    <span
                      className={`text-[9px] px-1.5 py-0.5 rounded ${
                        proc.category === 'dev'
                          ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                          : proc.category === 'design'
                          ? 'bg-purple-500/20 text-purple-300 border border-purple-500/30'
                          : 'bg-white/10 text-slate-400'
                      }`}
                    >
                      {proc.category}
                    </span>
                  </div>
                  <div className="flex items-baseline justify-between mt-1 text-[11px]">
                    <span className="text-slate-400">PID: {proc.pid}</span>
                    <span className="font-mono text-amber-300 font-medium">{proc.memory_mb} MB</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

