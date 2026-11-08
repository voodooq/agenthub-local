'use strict';

import React from 'react';
import { SkillItem } from '@/types';
import { useLanguage } from '@/context/LanguageContext';

interface SopDrawerProps {
  skill: SkillItem | null;
  onClose: () => void;
}

export function SopDrawer({ skill, onClose }: SopDrawerProps) {
  const { t } = useLanguage();

  if (!skill) return null;

  return (
    <div className="fixed inset-y-0 right-0 z-50 w-full sm:w-[480px] bg-zinc-950/95 border-l border-zinc-800 shadow-2xl flex flex-col backdrop-blur-md animate-in slide-in-from-right duration-200">
      <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
        <div className="flex items-center space-x-2.5">
          <span className="text-lg">📖</span>
          <div>
            <h3 className="text-sm font-semibold text-zinc-100 flex items-center space-x-2">
              <span>{skill.name}</span>
              {skill.scope === 'workspace' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                  {skill.is_override ? t('工作区重写') : t('工作区专属')}
                </span>
              )}
            </h3>
            <p className="text-xs text-zinc-400 mt-0.5">{skill.command}</p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded-lg hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 transition"
          title={t('关闭')}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="px-5 py-3 border-b border-zinc-800/60 bg-zinc-900/50">
        <div className="text-xs text-zinc-300 leading-relaxed">
          {skill.description || t('暂无详细描述')}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-5 text-xs font-mono text-zinc-300 whitespace-pre-wrap leading-relaxed select-text">
        {skill.sop_content || t('该技能未定义详细 SOP 执行规程。')}
      </div>

      <div className="p-4 border-t border-zinc-800 flex justify-end">
        <button
          onClick={onClose}
          className="px-4 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-xs font-medium text-zinc-200 transition"
        >
          {t('关闭')}
        </button>
      </div>
    </div>
  );
}

