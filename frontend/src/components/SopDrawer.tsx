'use client';

import React, { useEffect, useState } from 'react';
import { BookOpen, Copy, Check, Trash2, X, Loader2 } from 'lucide-react';
import { SkillItem } from '@/types';
import { useLanguage } from '@/context/LanguageContext';
import { apiFetch } from '@/utils/apiClient';
import { fetchWorkspaceSkillDetail, invalidateWorkspaceSkills } from '@/utils/skillCache';

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
  const [loadedSop, setLoadedSop] = useState<string>('');
  const [loadingSop, setLoadingSop] = useState(false);

  // 列表只拉取轻量摘要；打开 SOP 抽屉时再按需懒加载完整执行规程
  useEffect(() => {
    if (!skill) {
      setLoadedSop('');
      setLoadingSop(false);
      return;
    }

    if (skill.sop_content !== undefined) {
      setLoadedSop(skill.sop_content);
      setLoadingSop(false);
      return;
    }

    let cancelled = false;
    setLoadedSop('');
    setLoadingSop(true);

    fetchWorkspaceSkillDetail(skill.name, workspaceId)
      .then((detail) => {
        if (!cancelled) {
          setLoadedSop(detail?.sop_content || '');
        }
      })
      .catch((e) => {
        console.error('加载 SOP 详情失败:', e);
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingSop(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [skill, workspaceId]);

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
        invalidateWorkspaceSkills(workspaceId);
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
    <div className="fixed inset-y-0 right-0 z-50 w-full sm:w-[480px] bg-surface-card border-l border-border shadow-2xl flex flex-col backdrop-blur-md animate-in slide-in-from-right duration-200 select-text">
      <div className="flex items-center justify-between px-5 py-4 border-b border-border bg-surface-50">
        <div className="flex items-center space-x-2.5 min-w-0">
          <div className="p-1 rounded-lg bg-emerald-500/10 text-emerald-500 shrink-0">
            <BookOpen className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground flex items-center space-x-2 truncate">
              <span className="truncate">{skill.name}</span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-200 text-muted-foreground border border-border/70 flex-shrink-0">
                v{skill.version || '1.0.0'}
              </span>
              {skill.scope === 'workspace' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 flex-shrink-0">
                  {skill.is_override ? t('工作区重写') : t('工作区专属')}
                </span>
              )}
            </h3>
            <div className="flex items-center space-x-2 mt-0.5">
              <span className="text-xs font-mono text-emerald-600 dark:text-emerald-400">{skill.command}</span>
              <button
                onClick={handleCopyCommand}
                className="text-[10px] text-muted-foreground hover:text-foreground flex items-center space-x-0.5 transition"
              >
                {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                <span>{copied ? t('已复制') : t('复制指令')}</span>
              </button>
            </div>
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded-lg hover:bg-surface-200 text-muted-foreground hover:text-foreground transition"
          title={t('关闭')}
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="px-5 py-3 border-b border-border bg-surface-100">
        <div className="text-xs text-muted-foreground leading-relaxed">
          {skill.description || t('暂无详细描述')}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-5 text-xs font-mono text-foreground whitespace-pre-wrap leading-relaxed select-text bg-surface-card">
        {loadingSop ? (
          <div className="flex items-center space-x-2 text-muted-foreground py-6 font-sans">
            <Loader2 className="w-4 h-4 animate-spin text-emerald-500" />
            <span>{t('正在按需读取 SOP 规程...')}</span>
          </div>
        ) : (
          loadedSop || t('该技能未定义详细 SOP 执行规程。')
        )}
      </div>

      <div className="p-4 border-t border-border bg-surface-50 flex items-center justify-between">
        <div>
          {skill.scope === 'workspace' && workspaceId && (
            <button
              onClick={handleDeleteSkill}
              disabled={deleting}
              className="px-3 py-1.5 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-xs transition disabled:opacity-50 flex items-center space-x-1"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>{deleting ? t('正在删除...') : t('删除工作区技能')}</span>
            </button>
          )}
        </div>
        <button
          onClick={onClose}
          className="px-4 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-xs font-medium text-foreground transition"
        >
          {t('关闭')}
        </button>
      </div>
    </div>
  );
}
