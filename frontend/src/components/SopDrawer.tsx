'use strict';

import React, { useState } from 'react';
import { SkillItem } from '@/types';
import { useLanguage } from '@/context/LanguageContext';
import { apiFetch } from '@/utils/apiClient';

interface SopDrawerProps {
  skill: SkillItem | null;
  workspaceId?: string;
  onClose: () => void;
  onSkillDeleted?: (skillName: string) => void;
}

export function SopDrawer({ skill, workspaceId, onClose, onSkillDeleted }: SopDrawerProps) {
  const { t } = useLanguage();
  const [copied, setCopied] = useState(false);
  const [deleting, setDeleting] = useState(false);

  if (!skill) return null;

  const handleCopyCommand = async () => {
    if (!skill.command) return;
    try {
      await navigator.clipboard.writeText(skill.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 容错剪贴板写入
    }
  };

  const handleDeleteSkill = async () => {
    if (!workspaceId || skill.scope !== 'workspace') return;
    if (!window.confirm(t(`确定要在当前工作区永久删除技能「${skill.name}」吗？相关物理文件将被移除。`))) {
      return;
    }

    setDeleting(true);
    try {
      const res = await apiFetch(`/api/v1/skills/${encodeURIComponent(skill.name)}?workspace_id=${workspaceId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        if (onSkillDeleted) {
          onSkillDeleted(skill.name);
        }
        onClose();
      } else {
        const data = await res.json().catch(() => ({}));
        alert(data.detail || t('删除技能失败'));
      }
    } catch (e) {
      console.error('删除技能异常:', e);
      alert(t('删除技能请求失败，请检查后端运行状态'));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="fixed inset-y-0 right-0 z-50 w-full sm:w-[480px] bg-zinc-950/95 border-l border-zinc-800 shadow-2xl flex flex-col backdrop-blur-md animate-in slide-in-from-right duration-200">
      <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
        <div className="flex items-center space-x-2.5 min-w-0">
          <span className="text-lg flex-shrink-0">📖</span>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-zinc-100 flex items-center space-x-2 truncate">
              <span className="truncate">{skill.name}</span>
              {skill.scope === 'workspace' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex-shrink-0">
                  {skill.is_override ? t('工作区重写') : t('工作区专属')}
                </span>
              )}
            </h3>
            <div className="flex items-center space-x-2 mt-0.5">
              <span className="text-xs font-mono text-emerald-400">{skill.command}</span>
              <button
                onClick={handleCopyCommand}
                className="text-[10px] text-zinc-400 hover:text-zinc-200 underline transition"
              >
                {copied ? t('✓ 已复制') : t('复制指令')}
              </button>
            </div>
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

      <div className="p-4 border-t border-zinc-800 flex items-center justify-between">
        <div>
          {skill.scope === 'workspace' && workspaceId && (
            <button
              onClick={handleDeleteSkill}
              disabled={deleting}
              className="px-3 py-1.5 rounded-lg bg-red-950/40 hover:bg-red-900/60 border border-red-800/50 text-red-300 hover:text-red-200 text-xs transition disabled:opacity-50 flex items-center space-x-1"
            >
              <span>🗑️</span>
              <span>{deleting ? t('正在删除...') : t('删除工作区技能')}</span>
            </button>
          )}
        </div>
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
