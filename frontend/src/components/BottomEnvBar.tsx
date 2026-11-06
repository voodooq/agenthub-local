'use client';

import React from 'react';
import { FolderGit2, ShieldCheck, Activity, ChevronUp, ChevronDown } from 'lucide-react';
import { WorkMode } from './TopModeSwitcher';

interface BottomEnvBarProps {
  currentMode: WorkMode;
  workspacePath: string;
  isHudOpen: boolean;
  onToggleHud: () => void;
  cpuPercent?: number;
  memPercent?: number;
}

export function BottomEnvBar({
  currentMode,
  workspacePath,
  isHudOpen,
  onToggleHud,
  cpuPercent = 18,
  memPercent = 42,
}: BottomEnvBarProps) {
  // 根据当前模式确定权限标签
  const permissionInfo =
    currentMode === 'office'
      ? { label: '只读沙箱 (无硬件写权限)', color: 'text-blue-400 border-blue-500/30 bg-blue-500/10' }
      : { label: '需人类确认锁 (审批后物理执行)', color: 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10' };

  return (
    <div className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-surface-200/60 border border-white/5 text-[11px] text-slate-400 select-none">
      {/* 1. 工作区绑定 */}
      <div className="flex items-center space-x-1.5 truncate max-w-[260px] md:max-w-[400px]">
        <FolderGit2 className="w-3.5 h-3.5 text-slate-400 shrink-0" />
        <span className="text-slate-500">工作区:</span>
        <span className="font-mono text-slate-300 truncate" title={workspacePath}>
          {workspacePath}
        </span>
      </div>

      {/* 2. 权限状态胶囊 */}
      <div className="hidden sm:flex items-center space-x-1 px-2 py-0.5 rounded-full border text-[10px] font-medium transition-colors">
        <ShieldCheck className="w-3 h-3 text-emerald-400" />
        <span className={permissionInfo.color.split(' ')[0]}>{permissionInfo.label}</span>
      </div>

      {/* 3. 硬件 HUD 折叠开关 */}
      <button
        onClick={onToggleHud}
        className={`flex items-center space-x-2 px-2 py-1 rounded-md transition-all ${
          isHudOpen
            ? 'bg-accent/20 text-accent-light border border-accent/30'
            : 'hover:bg-white/10 text-slate-300'
        }`}
        title={isHudOpen ? '收起硬件仪表盘' : '展开实时硬件仪表盘'}
      >
        <Activity className="w-3.5 h-3.5 text-accent animate-pulse" />
        <span className="font-mono font-medium text-[10px]">
          CPU <span className={cpuPercent > 70 ? 'text-rose-400' : 'text-blue-400'}>{cpuPercent}%</span> | RAM{' '}
          <span className={memPercent > 80 ? 'text-rose-400' : 'text-purple-400'}>{memPercent}%</span>
        </span>
        {isHudOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronUp className="w-3.5 h-3.5" />}
      </button>
    </div>
  );
}

