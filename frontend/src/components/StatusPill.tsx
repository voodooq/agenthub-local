'use client';

import React from 'react';
import { StatusPillData } from '@/types';

interface StatusPillProps {
  data: StatusPillData | null;
  onOpenAccounts: () => void;
}

export function StatusPill({ data, onOpenAccounts }: StatusPillProps) {
  if (!data) {
    return (
      <div className="flex items-center space-x-2 px-2.5 py-1 rounded-full bg-surface-100 border border-border text-xs text-muted-foreground">
        <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40"></span>
        <span className="text-[11px]">连接中...</span>
      </div>
    );
  }

  const isWarning = data.is_warning;
  const isExhausted = data.status === 'exhausted' || data.status === 'cooldown';
  const isGoogleOne = data.auth_type === 'oauth_google_one';

  let dotColor = 'bg-emerald-500';
  if (isExhausted) {
    dotColor = 'bg-rose-500';
  } else if (isWarning) {
    dotColor = 'bg-amber-500';
  }

  const tooltipText = `当前账号: ${data.active_account_name || '默认'} | 今日调用: ${data.today_used} 次 | 状态: ${
    isExhausted ? '配额耗尽/冷却' : isWarning ? '告警' : '正常'
  } (点击管理账号池)`;

  return (
    <button
      onClick={onOpenAccounts}
      className="group flex items-center space-x-2 px-3 py-1 rounded-full border border-border/80 bg-surface-100/70 hover:bg-surface-200/80 text-foreground transition-all cursor-pointer select-none text-xs"
      title={tooltipText}
    >
      {/* 稳定的小圆点，摒弃晃眼的呼吸灯动画 */}
      <span className={`w-2 h-2 rounded-full shrink-0 ${dotColor}`}></span>

      {/* 极简账号名与身份标签 */}
      <div className="flex items-center space-x-1.5">
        <span className="font-medium max-w-[120px] truncate text-foreground text-xs">
          {data.active_account_name || '默认账号'}
        </span>
        {isGoogleOne && (
          <span className="text-[10px] px-1 py-0.2 rounded bg-blue-500/15 text-blue-600 dark:text-blue-400 font-semibold font-mono">
            PRO
          </span>
        )}
      </div>

      {data.is_relayed && (
        <span className="text-[10px] text-amber-500 font-medium">接力中</span>
      )}
    </button>
  );
}
