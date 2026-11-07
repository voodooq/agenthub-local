'use client';

import React from 'react';
import { FileText, Code2, Palette, Stethoscope } from 'lucide-react';

export type WorkMode = 'office' | 'dev' | 'design' | 'doctor';

interface TopModeSwitcherProps {
  currentMode: WorkMode;
  onModeChange: (mode: WorkMode) => void;
}

export const WORK_MODES: {
  id: WorkMode;
  name: string;
  icon: any;
  desc: string;
  badge: string;
  activeColor: string;
}[] = [
  {
    id: 'office',
    name: '日常办公',
    icon: FileText,
    desc: '方案润色、多语言长文档总结、智能办公沙箱',
    badge: '安全沙箱',
    activeColor: 'bg-emerald-600/15 border-emerald-500/40 text-emerald-600 dark:text-emerald-300 shadow-sm',
  },
  {
    id: 'dev',
    name: '代码开发',
    icon: Code2,
    desc: '全栈架构设计、端口占用释放、清理僵尸编译进程',
    badge: '研发环境治理',
    activeColor: 'bg-emerald-600/15 border-emerald-500/40 text-emerald-600 dark:text-emerald-300 shadow-sm',
  },
  {
    id: 'design',
    name: '设计创意',
    icon: Palette,
    desc: '视觉提示词工程、显存 VRAM 护航、素材批量整理',
    badge: '设计显存护航',
    activeColor: 'bg-purple-600/15 border-purple-500/40 text-purple-600 dark:text-purple-300 shadow-sm',
  },
  {
    id: 'doctor',
    name: '硬件医生',
    icon: Stethoscope,
    desc: '系统卡顿发热诊断、高资源进程治理、全盘体检',
    badge: '系统全量体检',
    activeColor: 'bg-rose-600/15 border-rose-500/40 text-rose-600 dark:text-rose-300 shadow-sm',
  },
];

export function TopModeSwitcher({ currentMode, onModeChange }: TopModeSwitcherProps) {
  return (
    <div className="flex items-center space-x-1 p-1 rounded-full bg-surface-200/60 border border-border select-none">
      {WORK_MODES.map((mode) => {
        const Icon = mode.icon;
        const isActive = currentMode === mode.id;

        return (
          <button
            key={mode.id}
            type="button"
            onClick={() => onModeChange(mode.id)}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all ${
              isActive
                ? `${mode.activeColor} font-bold`
                : 'text-muted-foreground hover:text-foreground hover:bg-surface-100'
            }`}
            title={`${mode.name}：${mode.desc}`}
          >
            <Icon className="w-3.5 h-3.5" />
            <span>{mode.name}</span>
            {isActive && (
              <span className="hidden xl:inline text-[9px] px-1.5 py-0.2 rounded-full bg-foreground/10 font-normal">
                {mode.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
