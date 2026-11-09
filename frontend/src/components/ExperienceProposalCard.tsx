'use client';

import React, { useState } from 'react';
import {
  Sparkles,
  Check,
  X,
  Edit3,
  Eye,
  GitBranch,
  Layers,
  Globe,
  FolderGit2,
} from 'lucide-react';
import { ExperienceProposalItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import {
  invalidateExtensionSkills,
  invalidateWorkspaceSkills,
  prefetchExtensionSkills,
  prefetchWorkspaceSkills,
} from '@/utils/skillCache';

interface ExperienceProposalCardProps {
  proposal: ExperienceProposalItem;
  onResolved: (
    proposalId: string,
    action: 'applied' | 'dismissed',
    updated?: ExperienceProposalItem
  ) => void;
}

const TRIGGER_REASON_LABELS: Record<string, string> = {
  self_healed: '⚡ 故障自愈避坑',
  user_intervention: '🧭 人工纠偏固化',
  complex_unskilled_workflow: '🧩 复杂链路沉淀',
  manual_distill: '✋ 人工归档沉淀',
};

export function ExperienceProposalCard({
  proposal,
  onResolved,
}: ExperienceProposalCardProps) {
  const isPatch = proposal.proposal_type === 'skill_patch';
  const initialEditable = isPatch
    ? proposal.payload?.markdown_entry || ''
    : proposal.payload?.skill_md || '';

  const [targetScope, setTargetScope] = useState<'workspace' | 'global'>(
    proposal.target_scope || 'workspace'
  );
  const [skillNameInput, setSkillNameInput] = useState<string>(
    proposal.proposed_skill_name || proposal.payload?.skill_id || ''
  );
  const [editedContent, setEditedContent] = useState<string>(initialEditable);
  const [isEditing, setIsEditing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const reasonBadge =
    TRIGGER_REASON_LABELS[proposal.trigger_reason] || '✨ 实战经验提炼';

  const handleApply = async () => {
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      const bodyPayload: Record<string, any> = {
        target_scope: targetScope,
        edited_content: editedContent,
      };
      if (!isPatch && skillNameInput.trim()) {
        bodyPayload.target_skill_name = skillNameInput.trim();
      }
      const res = await apiFetch(`/api/v1/experiences/proposals/${proposal.id}/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(bodyPayload),
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      // 立即失效前端工作区与全局扩展缓存并后台静默预热，保障 / 面板 0ms 见效
      invalidateWorkspaceSkills(proposal.workspace_id);
      invalidateExtensionSkills();
      prefetchWorkspaceSkills(proposal.workspace_id);
      prefetchExtensionSkills();
      onResolved(proposal.id, 'applied', data.proposal);
    } catch (e: any) {
      setErrorMsg(e?.message || '应用经验提议失败');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDismiss = async () => {
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      const res = await apiFetch(`/api/v1/experiences/proposals/${proposal.id}/dismiss`, {
        method: 'POST',
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      onResolved(proposal.id, 'dismissed', data.proposal);
    } catch (e: any) {
      setErrorMsg(e?.message || '忽略提议失败');
    } finally {
      setIsSubmitting(false);
    }
  };

  const diffLines = (proposal.diff_preview || '').replace(/\r\n/g, '\n').split('\n');

  return (
    <div className="w-full max-w-[92%] md:max-w-[85%] rounded-2xl border border-emerald-500/35 bg-surface-card shadow-md overflow-hidden animate-fade-in">
      {/* 头部标题与触发原因 */}
      <div className="px-4 py-3 border-b border-border/70 bg-emerald-500/5 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center space-x-2.5 min-w-0">
          <div className="w-7 h-7 rounded-lg bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center shrink-0">
            <Sparkles className="w-4 h-4 text-emerald-500" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center flex-wrap gap-1.5">
              <span className="text-xs font-bold text-foreground truncate">
                {proposal.title}
              </span>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30 font-medium">
                {isPatch ? `补丁 → ${proposal.target_skill_id}` : '新建四元组 Skill'}
              </span>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-700 dark:text-amber-300 border border-amber-500/25">
                {reasonBadge}
              </span>
            </div>
            <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">
              {proposal.summary}
            </p>
          </div>
        </div>

        {/* 作用域与视图切换控件 */}
        <div className="flex items-center space-x-1.5 shrink-0">
          <div className="flex items-center rounded-lg bg-surface-100 border border-border p-0.5 text-[11px]">
            <button
              type="button"
              onClick={() => setTargetScope('workspace')}
              className={`px-2 py-0.5 rounded-md flex items-center space-x-1 transition-colors ${
                targetScope === 'workspace'
                  ? 'bg-emerald-600 text-white font-medium'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
              title="沉淀至当前工作区私有 skills/ 目录（推荐，防止跨项目污染）"
            >
              <FolderGit2 className="w-3 h-3" />
              <span>工作区私有</span>
            </button>
            <button
              type="button"
              onClick={() => setTargetScope('global')}
              className={`px-2 py-0.5 rounded-md flex items-center space-x-1 transition-colors ${
                targetScope === 'global'
                  ? 'bg-emerald-600 text-white font-medium'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
              title="写入全局底座 skills/ 目录，供所有工作区通用"
            >
              <Globe className="w-3 h-3" />
              <span>全局底座</span>
            </button>
          </div>

          <button
            type="button"
            onClick={() => setIsEditing((prev) => !prev)}
            className={`px-2.5 py-1 rounded-lg text-[11px] border flex items-center space-x-1 transition-colors ${
              isEditing
                ? 'bg-indigo-500/15 border-indigo-500/35 text-indigo-600 dark:text-indigo-300 font-medium'
                : 'bg-surface-100 border-border text-muted-foreground hover:text-foreground'
            }`}
          >
            {isEditing ? <Eye className="w-3 h-3" /> : <Edit3 className="w-3 h-3" />}
            <span>{isEditing ? '预览 Diff' : '编辑正文'}</span>
          </button>
        </div>
      </div>

      {/* 结构化三元组摘要 */}
      <div className="px-4 py-2.5 bg-surface-100/60 border-b border-border/60 grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
        <div className="rounded-lg bg-background/70 border border-border/70 px-2.5 py-1.5">
          <div className="font-semibold text-rose-600 dark:text-rose-400 mb-0.5">
            📌 触发问题 / 难点
          </div>
          <div className="text-foreground/90 leading-snug break-words">
            {proposal.experience_triple?.problem}
          </div>
        </div>
        <div className="rounded-lg bg-background/70 border border-border/70 px-2.5 py-1.5">
          <div className="font-semibold text-amber-600 dark:text-amber-400 mb-0.5">
            🔍 根因分析
          </div>
          <div className="text-foreground/90 leading-snug break-words">
            {proposal.experience_triple?.root_cause}
          </div>
        </div>
        <div className="rounded-lg bg-background/70 border border-border/70 px-2.5 py-1.5">
          <div className="font-semibold text-emerald-600 dark:text-emerald-400 mb-0.5">
            🛡️ 固化解决方案
          </div>
          <div className="text-foreground/90 leading-snug break-words">
            {proposal.experience_triple?.solution}
          </div>
        </div>
      </div>

      {/* Diff 预览区或可编辑正文中枢 */}
      <div className="px-4 py-3 space-y-2.5">
        {!isPatch && (
          <div className="flex items-center space-x-2 text-xs">
            <GitBranch className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
            <span className="text-muted-foreground shrink-0">目标 Skill ID (kebab-case):</span>
            <input
              type="text"
              value={skillNameInput}
              onChange={(e) => setSkillNameInput(e.target.value)}
              placeholder="例如: auto-excel-reconcile"
              className="flex-1 max-w-xs px-2.5 py-1 rounded-lg border border-border bg-background text-xs font-mono text-foreground focus:outline-none focus:border-emerald-500"
            />
          </div>
        )}

        {isEditing ? (
          <div className="space-y-1">
            <div className="text-[11px] text-muted-foreground flex items-center space-x-1">
              <Layers className="w-3 h-3" />
              <span>
                正在编辑即将写入{' '}
                <code className="font-mono text-foreground">
                  {isPatch
                    ? `skills/${proposal.target_skill_id}/knowledge/experience_playbook.md`
                    : `skills/${skillNameInput || proposal.proposed_skill_name}/SKILL.md`}
                </code>{' '}
                的内容：
              </span>
            </div>
            <textarea
              value={editedContent}
              onChange={(e) => setEditedContent(e.target.value)}
              rows={9}
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-xs font-mono text-foreground leading-relaxed focus:outline-none focus:border-emerald-500 resize-y"
            />
          </div>
        ) : (
          <div className="rounded-xl border border-border bg-zinc-950/90 dark:bg-black/60 p-3 max-h-60 overflow-y-auto font-mono text-[11px] leading-relaxed select-text">
            {diffLines.map((line, idx) => {
              let lineClass = 'text-zinc-300';
              if (line.startsWith('+++') || line.startsWith('---')) {
                lineClass = 'text-zinc-400 font-semibold';
              } else if (line.startsWith('@@')) {
                lineClass = 'text-indigo-400 bg-indigo-500/10 px-1 rounded';
              } else if (line.startsWith('+')) {
                lineClass = 'text-emerald-300 bg-emerald-500/15 block px-1';
              } else if (line.startsWith('-')) {
                lineClass = 'text-rose-300 bg-rose-500/15 block px-1';
              }
              return (
                <div key={idx} className={`${lineClass} whitespace-pre-wrap break-all`}>
                  {line || ' '}
                </div>
              );
            })}
          </div>
        )}

        {errorMsg && (
          <div className="text-xs text-rose-500 bg-rose-500/10 border border-rose-500/25 rounded-lg px-3 py-1.5">
            {errorMsg}
          </div>
        )}

        {/* 底部人工确认操作栏 */}
        <div className="flex items-center justify-between pt-1">
          <span className="text-[11px] text-muted-foreground">
            为防模型幻觉污染知识库，须经您确认后才写入物理 Skill 并刷新 FTS5 索引
          </span>
          <div className="flex items-center space-x-2">
            <button
              type="button"
              onClick={handleDismiss}
              disabled={isSubmitting}
              className="px-3 py-1.5 rounded-xl border border-border bg-surface-100 hover:bg-rose-500/10 hover:text-rose-500 text-xs text-muted-foreground transition-colors flex items-center space-x-1 disabled:opacity-50"
            >
              <X className="w-3.5 h-3.5" />
              <span>忽略</span>
            </button>
            <button
              type="button"
              onClick={handleApply}
              disabled={isSubmitting}
              className="px-4 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm shadow-emerald-600/20 transition-all flex items-center space-x-1.5 disabled:opacity-50"
            >
              <Check className="w-3.5 h-3.5" />
              <span>
                {isSubmitting
                  ? '落盘同步中...'
                  : isPatch
                  ? '确认合入经验库'
                  : '确认创建 Skill'}
              </span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

