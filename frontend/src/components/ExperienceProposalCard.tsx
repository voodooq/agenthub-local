'use client';

import React, { useEffect, useMemo, useState } from 'react';
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
  RefreshCw,
  ArrowUpRight,
  Loader2,
} from 'lucide-react';
import {
  ExperienceProposalItem,
  ExperienceProposalTargetSkillOption,
} from '@/types';
import { apiFetch } from '@/utils/apiClient';
import {
  invalidateExtensionSkills,
  invalidateWorkspaceSkills,
  prefetchExtensionSkills,
  prefetchWorkspaceSkills,
} from '@/utils/skillCache';

interface ExperienceProposalCardProps {
  proposal: ExperienceProposalItem;
  /** 可选传入当前工作区及全局已有 Skill 候选清单（支持迭代更新版本） */
  availableSkills?: ExperienceProposalTargetSkillOption[];
  /** 是否嵌入顶部 Loop 引擎下方原地展开区域 */
  embeddedInLoop?: boolean;
  /** 仅收起本地预览面板（不忽略或删除提案） */
  onClose?: () => void;
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
  availableSkills,
  embeddedInLoop = false,
  onClose,
  onResolved,
}: ExperienceProposalCardProps) {
  const initialMode: 'update_existing' | 'create_new' =
    proposal.proposal_type === 'skill_patch' ? 'update_existing' : 'create_new';

  const [fetchedSkills, setFetchedSkills] = useState<ExperienceProposalTargetSkillOption[]>([]);
  const skillOptions = useMemo(() => {
    const raw =
      availableSkills && availableSkills.length > 0
        ? availableSkills
        : proposal.available_skills && proposal.available_skills.length > 0
        ? proposal.available_skills
        : fetchedSkills;
    const candidateIds = new Set(
      [
        ...(proposal.payload?.candidate_skill_ids || []),
        proposal.target_skill_id || '',
        proposal.payload?.target_skill_id || '',
      ]
        .map((s) => String(s || '').trim())
        .filter(Boolean)
    );
    return [...raw].sort((a, b) => {
      const aHit = candidateIds.has(a.id) ? 0 : 1;
      const bHit = candidateIds.has(b.id) ? 0 : 1;
      if (aHit !== bHit) return aHit - bHit;
      if (a.scope !== b.scope) return a.scope === 'workspace' ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [availableSkills, proposal.available_skills, fetchedSkills, proposal.payload, proposal.target_skill_id]);

  // 若外层未携带可用 Skill 列表，则后台按需拉取一次作为兜底
  useEffect(() => {
    if (
      (availableSkills && availableSkills.length > 0) ||
      (proposal.available_skills && proposal.available_skills.length > 0)
    ) {
      return;
    }
    let cancelled = false;
    apiFetch(
      `/api/v1/experiences/proposals?workspace_id=${encodeURIComponent(proposal.workspace_id)}&limit=1`
    )
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled && Array.isArray(data?.available_skills)) {
          setFetchedSkills(data.available_skills);
        }
      })
      .catch(() => {
        // 忽略后台补齐失败
      });
    return () => {
      cancelled = true;
    };
  }, [availableSkills, proposal.available_skills, proposal.workspace_id]);

  const initialTargetSkillId =
    proposal.target_skill_id ||
    proposal.payload?.target_skill_id ||
    skillOptions[0]?.id ||
    '';

  const initialEditable =
    initialMode === 'update_existing'
      ? proposal.payload?.skill_md || proposal.payload?.markdown_entry || ''
      : proposal.payload?.new_skill_md || proposal.payload?.skill_md || '';

  const [applyMode, setApplyMode] = useState<'update_existing' | 'create_new'>(initialMode);
  const [selectedTargetSkillId, setSelectedTargetSkillId] = useState<string>(initialTargetSkillId);
  const [targetScope, setTargetScope] = useState<'workspace' | 'global'>(
    proposal.target_scope || 'workspace'
  );
  const [skillNameInput, setSkillNameInput] = useState<string>(
    proposal.payload?.new_skill_id ||
      proposal.proposed_skill_name ||
      proposal.payload?.skill_id ||
      ''
  );
  const [editedContent, setEditedContent] = useState<string>(initialEditable);
  const [currentDiff, setCurrentDiff] = useState<string>(proposal.diff_preview || '');
  const [currentVersion, setCurrentVersion] = useState<string>(
    proposal.payload?.current_version || (initialMode === 'update_existing' ? '1.0.0' : '0.0.0')
  );
  const [nextVersion, setNextVersion] = useState<string>(
    proposal.payload?.next_version || (initialMode === 'update_existing' ? '1.1.0' : '1.0.0')
  );
  const [targetFileDisplay, setTargetFileDisplay] = useState<string>(
    proposal.payload?.target_file ||
      (initialMode === 'update_existing'
        ? `skills/${initialTargetSkillId || 'skill'}/SKILL.md`
        : `skills/${proposal.payload?.new_skill_id || proposal.proposed_skill_name || 'custom-skill'}/SKILL.md`)
  );
  const [isEditing, setIsEditing] = useState(false);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // 当延迟拉取到已有 Skill 候选且当前处于 update_existing 但未选中目标时自动补齐第一项
  useEffect(() => {
    if (applyMode === 'update_existing' && !selectedTargetSkillId && skillOptions.length > 0) {
      setSelectedTargetSkillId(skillOptions[0].id);
    }
  }, [applyMode, selectedTargetSkillId, skillOptions]);

  const reasonBadge =
    TRIGGER_REASON_LABELS[proposal.trigger_reason] || '✨ 实战经验提炼';

  const requestPreview = async (
    nextMode: 'update_existing' | 'create_new',
    nextTargetId: string,
    nextScope: 'workspace' | 'global'
  ) => {
    setIsPreviewLoading(true);
    setErrorMsg(null);
    try {
      const res = await apiFetch(`/api/v1/experiences/proposals/${proposal.id}/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          apply_mode: nextMode,
          target_skill_id: nextMode === 'update_existing' ? nextTargetId || undefined : undefined,
          target_scope: nextScope,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      const p = data.preview;
      if (p) {
        if (typeof p.draft_content === 'string') setEditedContent(p.draft_content);
        if (typeof p.diff_preview === 'string') setCurrentDiff(p.diff_preview);
        if (typeof p.current_version === 'string') setCurrentVersion(p.current_version);
        if (typeof p.next_version === 'string') setNextVersion(p.next_version);
        if (typeof p.target_file === 'string') setTargetFileDisplay(p.target_file);
        if (nextMode === 'update_existing' && typeof p.target_skill_id === 'string' && p.target_skill_id) {
          setSelectedTargetSkillId(p.target_skill_id);
        } else if (nextMode === 'create_new' && typeof p.target_skill_id === 'string' && p.target_skill_id && !skillNameInput) {
          setSkillNameInput(p.target_skill_id);
        }
      }
    } catch (e: any) {
      setErrorMsg(e?.message || '预览方案变更失败');
    } finally {
      setIsPreviewLoading(false);
    }
  };

  const handleModeChange = (mode: 'update_existing' | 'create_new') => {
    if (mode === applyMode) return;
    const fallbackTarget = selectedTargetSkillId || skillOptions[0]?.id || '';
    if (mode === 'update_existing' && fallbackTarget && !selectedTargetSkillId) {
      setSelectedTargetSkillId(fallbackTarget);
    }
    setApplyMode(mode);
    void requestPreview(mode, fallbackTarget, targetScope);
  };

  const handleTargetSkillChange = (nextSkillId: string) => {
    setSelectedTargetSkillId(nextSkillId);
    void requestPreview('update_existing', nextSkillId, targetScope);
  };

  const handleScopeChange = (nextScope: 'workspace' | 'global') => {
    if (nextScope === targetScope) return;
    setTargetScope(nextScope);
    void requestPreview(applyMode, selectedTargetSkillId, nextScope);
  };

  const handleApply = async () => {
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      const bodyPayload: Record<string, any> = {
        apply_mode: applyMode,
        target_scope: targetScope,
        edited_content: editedContent,
      };
      if (applyMode === 'update_existing') {
        if (selectedTargetSkillId.trim()) {
          bodyPayload.target_skill_id = selectedTargetSkillId.trim();
        }
        if (nextVersion.trim()) {
          bodyPayload.next_version = nextVersion.trim();
        }
      } else if (skillNameInput.trim()) {
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

  const candidateIdSet = new Set(
    (proposal.payload?.candidate_skill_ids || []).map((s) => String(s || '').trim())
  );
  const diffLines = currentDiff.replace(/\r\n/g, '\n').split('\n');

  return (
    <div
      className={
        embeddedInLoop
          ? 'w-full border-t border-border bg-surface-card overflow-hidden animate-fade-in'
          : 'w-full max-w-[92%] md:max-w-[85%] rounded-2xl border border-emerald-500/35 bg-surface-card shadow-md overflow-hidden animate-fade-in'
      }
    >
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
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30 font-medium flex items-center space-x-1">
                {applyMode === 'update_existing' ? (
                  <>
                    <span>迭代升级 → /{selectedTargetSkillId || proposal.target_skill_id || 'skill'}</span>
                    <span className="font-mono font-bold">
                      v{currentVersion} → v{nextVersion}
                    </span>
                  </>
                ) : (
                  <span>新建四元组 Skill · v1.0.0</span>
                )}
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
              onClick={() => handleScopeChange('workspace')}
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
              onClick={() => handleScopeChange('global')}
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

          {onClose && (
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="px-2 py-1 rounded-lg text-[11px] border border-border bg-surface-100 hover:bg-surface-200 text-muted-foreground hover:text-foreground transition-colors flex items-center space-x-1 disabled:opacity-50"
              title="收起 Skill 预览面板"
            >
              <X className="w-3 h-3" />
              <span>收起</span>
            </button>
          )}
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

      {/* 演进模式选择、目标 Skill 选择器与版本跃迁控制栏 */}
      <div className="px-4 pt-3 pb-2 space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 rounded-xl bg-surface-100/70 border border-border/70">
          <div className="flex items-center rounded-lg bg-background border border-border p-0.5 text-[11px]">
            <button
              type="button"
              disabled={isPreviewLoading || isSubmitting}
              onClick={() => handleModeChange('update_existing')}
              className={`px-2.5 py-1 rounded-md flex items-center space-x-1 transition-colors disabled:opacity-50 ${
                applyMode === 'update_existing'
                  ? 'bg-emerald-600 text-white font-semibold shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <RefreshCw className="w-3 h-3" />
              <span>迭代更新已有 Skill</span>
            </button>
            <button
              type="button"
              disabled={isPreviewLoading || isSubmitting}
              onClick={() => handleModeChange('create_new')}
              className={`px-2.5 py-1 rounded-md flex items-center space-x-1 transition-colors disabled:opacity-50 ${
                applyMode === 'create_new'
                  ? 'bg-emerald-600 text-white font-semibold shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <Sparkles className="w-3 h-3" />
              <span>另存新建独立 Skill</span>
            </button>
          </div>

          {applyMode === 'update_existing' ? (
            <div className="flex flex-wrap items-center gap-2 flex-1 min-w-[220px] justify-end">
              <div className="flex items-center space-x-1.5 min-w-0 flex-1 max-w-sm">
                <span className="text-[11px] text-muted-foreground shrink-0">升级目标:</span>
                <select
                  value={selectedTargetSkillId}
                  disabled={isPreviewLoading || isSubmitting || skillOptions.length === 0}
                  onChange={(e) => handleTargetSkillChange(e.target.value)}
                  className="flex-1 min-w-0 px-2.5 py-1 rounded-lg border border-border bg-background text-xs font-mono text-foreground focus:outline-none focus:border-emerald-500"
                >
                  {skillOptions.length === 0 ? (
                    <option value={selectedTargetSkillId || ''}>
                      {selectedTargetSkillId ? `/${selectedTargetSkillId}` : '暂无可选 Skill'}
                    </option>
                  ) : (
                    skillOptions.map((sk) => (
                      <option key={`${sk.scope}:${sk.id}`} value={sk.id}>
                        {candidateIdSet.has(sk.id) ? '★ ' : ''}
                        {sk.command} ({sk.name}) · v{sk.version || '1.0.0'} [
                        {sk.scope === 'workspace' ? '工作区' : '全局'}]
                      </option>
                    ))
                  )}
                </select>
              </div>

              <div className="flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-[11px] font-mono text-emerald-700 dark:text-emerald-300 shrink-0">
                <span>v{currentVersion}</span>
                <ArrowUpRight className="w-3 h-3" />
                <span className="font-bold">v{nextVersion}</span>
              </div>
            </div>
          ) : (
            <div className="flex items-center space-x-2 flex-1 min-w-[220px] justify-end text-xs">
              <GitBranch className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
              <span className="text-muted-foreground shrink-0 text-[11px]">新 Skill ID:</span>
              <input
                type="text"
                value={skillNameInput}
                onChange={(e) => setSkillNameInput(e.target.value)}
                placeholder="例如: auto-excel-reconcile"
                className="flex-1 max-w-xs px-2.5 py-1 rounded-lg border border-border bg-background text-xs font-mono text-foreground focus:outline-none focus:border-emerald-500"
              />
              <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-surface-200 border border-border text-muted-foreground shrink-0">
                v1.0.0
              </span>
            </div>
          )}
        </div>

        {/* Diff 预览区或可编辑正文中枢 */}
        {isPreviewLoading ? (
          <div className="rounded-xl border border-border bg-surface-100/70 p-6 flex items-center justify-center space-x-2 text-xs text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin text-emerald-500" />
            <span>正在生成版本差异与更新草案...</span>
          </div>
        ) : isEditing ? (
          <div className="space-y-1">
            <div className="text-[11px] text-muted-foreground flex items-center space-x-1">
              <Layers className="w-3 h-3" />
              <span>
                正在编辑即将写入{' '}
                <code className="font-mono text-foreground">
                  {applyMode === 'update_existing'
                    ? targetFileDisplay || `skills/${selectedTargetSkillId}/SKILL.md`
                    : `skills/${skillNameInput || proposal.proposed_skill_name || 'custom-skill'}/SKILL.md`}
                </code>{' '}
                的完整内容：
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
              disabled={isSubmitting || isPreviewLoading}
              className="px-3 py-1.5 rounded-xl border border-border bg-surface-100 hover:bg-rose-500/10 hover:text-rose-500 text-xs text-muted-foreground transition-colors flex items-center space-x-1 disabled:opacity-50"
            >
              <X className="w-3.5 h-3.5" />
              <span>忽略</span>
            </button>
            <button
              type="button"
              onClick={handleApply}
              disabled={isSubmitting || isPreviewLoading}
              className="px-4 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm shadow-emerald-600/20 transition-all flex items-center space-x-1.5 disabled:opacity-50"
            >
              <Check className="w-3.5 h-3.5" />
              <span>
                {isSubmitting
                  ? '落盘同步中...'
                  : applyMode === 'update_existing'
                  ? `确认迭代升级至 v${nextVersion}`
                  : '确认创建 Skill v1.0.0'}
              </span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

