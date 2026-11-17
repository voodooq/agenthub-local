'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  History,
  Loader2,
  RotateCcw,
  RotateCw,
  Trash2,
  X,
} from 'lucide-react';
import { SkillItem, SkillVersionDetail, SkillVersionItem } from '@/types';
import { useLanguage } from '@/context/LanguageContext';
import { apiFetch } from '@/utils/apiClient';
import {
  fetchSkillVersionDetail,
  fetchSkillVersions,
  fetchWorkspaceSkillDetail,
  invalidateWorkspaceSkills,
  rollbackSkillVersion,
  subscribeSkillsUpdated,
} from '@/utils/skillCache';

interface SopDrawerProps {
  skill: SkillItem | null;
  workspaceId?: string;
  onClose: () => void;
  onSkillDeleted?: (skillName: string) => void;
  onSkillUpdated?: (updatedSkill: SkillItem) => void;
}

const SOURCE_LABELS: Record<string, string> = {
  sandbox: '沙盒修改',
  write_local_file: '沙盒写入',
  replace_in_file: '沙盒局部编辑',
  rollback: '版本回滚前备份',
  manual: '手动编辑',
  distill: '经验进化',
  import: '导入覆盖',
  save: '保存更新',
};

function formatVersionTime(raw: string): string {
  if (!raw) return '未知时间';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatSizeBytes(bytes?: number): string {
  if (!bytes || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

export function SopDrawer({
  skill,
  workspaceId,
  onClose,
  onSkillDeleted,
  onSkillUpdated,
}: SopDrawerProps) {
  const { t } = useLanguage();
  const [liveSkill, setLiveSkill] = useState<SkillItem | null>(skill);
  const [copied, setCopied] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [loadedSop, setLoadedSop] = useState<string>('');
  const [loadingSop, setLoadingSop] = useState(false);

  // 视图切换：SOP 正文 | 版本历史
  const [viewMode, setViewMode] = useState<'sop' | 'history'>('sop');
  const [versions, setVersions] = useState<SkillVersionItem[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const [expandedVersionId, setExpandedVersionId] = useState<string | null>(null);
  const [versionDetails, setVersionDetails] = useState<Record<string, SkillVersionDetail>>({});
  const [loadingDetailId, setLoadingDetailId] = useState<string | null>(null);
  const [rollingBackId, setRollingBackId] = useState<string | null>(null);
  const [actionBanner, setActionBanner] = useState<{ type: 'success' | 'error'; text: string } | null>(
    null
  );

  // 当传入的 skill 切换时重置抽屉状态
  useEffect(() => {
    setLiveSkill(skill);
    setViewMode('sop');
    setVersions([]);
    setVersionsError(null);
    setExpandedVersionId(null);
    setVersionDetails({});
    setActionBanner(null);
  }, [skill?.name, skill?.scope, workspaceId]);

  // 当父级传入同一技能的新摘要（例如版本或描述刚热更新）时同步更新
  useEffect(() => {
    if (skill) {
      setLiveSkill((prev) => {
        if (!prev || prev.name !== skill.name || prev.scope !== skill.scope) return skill;
        return { ...prev, ...skill };
      });
    } else {
      setLiveSkill(null);
    }
  }, [skill]);

  const loadLatestDetail = useCallback(
    async (targetName: string, force = false, expectedVersion?: string) => {
      setLoadingSop(true);
      try {
        const detail = await fetchWorkspaceSkillDetail(targetName, workspaceId, {
          force,
          expectedVersion,
        });
        if (detail) {
          setLiveSkill((prev) => (prev ? { ...prev, ...detail } : detail));
          setLoadedSop(detail.sop_content || '');
        }
      } catch (e) {
        console.error('加载 SOP 详情失败:', e);
      } finally {
        setLoadingSop(false);
      }
    },
    [workspaceId]
  );

  // 列表只拉取轻量摘要；打开 SOP 抽屉时按需懒加载完整执行规程
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

    fetchWorkspaceSkillDetail(skill.name, workspaceId, {
      expectedVersion: skill.version,
    })
      .then((detail) => {
        if (!cancelled && detail) {
          setLiveSkill((prev) => (prev ? { ...prev, ...detail } : detail));
          setLoadedSop(detail.sop_content || '');
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
  }, [skill?.name, skill?.version, skill?.sop_content, workspaceId]);

  const loadVersions = useCallback(async (targetName: string) => {
    setLoadingVersions(true);
    setVersionsError(null);
    try {
      const data = await fetchSkillVersions('workspace', targetName, workspaceId);
      setVersions(Array.isArray(data.versions) ? data.versions : []);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '获取版本历史失败';
      setVersionsError(msg);
    } finally {
      setLoadingVersions(false);
    }
  }, [workspaceId]);

  // 订阅技能热更新/回滚事件：抽屉打开时自动同步最新版本号、描述与 SOP 正文
  useEffect(() => {
    if (!skill) return;
    return subscribeSkillsUpdated(() => {
      void loadLatestDetail(skill.name, true);
      if (viewMode === 'history') {
        void loadVersions(skill.name);
      }
    });
  }, [skill?.name, viewMode, loadLatestDetail, loadVersions]);

  if (!liveSkill) return null;

  const handleToggleHistoryView = () => {
    if (viewMode === 'history') {
      setViewMode('sop');
      return;
    }
    setViewMode('history');
    setActionBanner(null);
    void loadVersions(liveSkill.name);
  };

  const handleToggleVersionPreview = async (versionId: string) => {
    if (expandedVersionId === versionId) {
      setExpandedVersionId(null);
      return;
    }
    setExpandedVersionId(versionId);
    if (versionDetails[versionId]) return;

    setLoadingDetailId(versionId);
    try {
      const detail = await fetchSkillVersionDetail(
        'workspace',
        liveSkill.name,
        versionId,
        workspaceId
      );
      setVersionDetails((prev) => ({ ...prev, [versionId]: detail }));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '加载版本内容失败';
      setActionBanner({ type: 'error', text: msg });
    } finally {
      setLoadingDetailId((curr) => (curr === versionId ? null : curr));
    }
  };

  const handleRollback = async (item: SkillVersionItem) => {
    const confirmed = window.confirm(
      t(
        `确定将技能「${liveSkill.name}」回滚到历史版本 v${item.version}（${formatVersionTime(item.created_at)}）吗？系统会在回滚前自动备份当前版本。`
      )
    );
    if (!confirmed) return;

    setRollingBackId(item.version_id);
    setActionBanner(null);
    try {
      const res = await rollbackSkillVersion(
        'workspace',
        liveSkill.name,
        item.version_id,
        workspaceId
      );
      const updatedSkill: SkillItem = {
        ...liveSkill,
        ...res.skill,
      };
      setLiveSkill(updatedSkill);
      setLoadedSop(res.skill.sop_content ?? res.skill.body ?? '');
      onSkillUpdated?.(updatedSkill);
      setActionBanner({
        type: 'success',
        text: t(`已成功回滚至 v${res.skill.version || item.version}，当前版本已自动备份。`),
      });
      await loadVersions(liveSkill.name);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '回滚失败';
      setActionBanner({ type: 'error', text: msg });
    } finally {
      setRollingBackId(null);
    }
  };

  const handleCopyCommand = async () => {
    if (!liveSkill.command) return;
    try {
      await navigator.clipboard.writeText(liveSkill.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 容错剪贴板写入
    }
  };

  const handleDeleteSkill = async () => {
    if (!workspaceId || liveSkill.scope !== 'workspace') return;
    if (
      !window.confirm(
        t(`确定要在当前工作区永久删除技能「${liveSkill.name}」吗？相关物理文件将被移除。`)
      )
    ) {
      return;
    }

    setDeleting(true);
    try {
      const res = await apiFetch(
        `/api/v1/skills/${encodeURIComponent(liveSkill.name)}?workspace_id=${workspaceId}`,
        {
          method: 'DELETE',
        }
      );
      if (res.ok) {
        invalidateWorkspaceSkills(workspaceId);
        if (onSkillDeleted) {
          onSkillDeleted(liveSkill.name);
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
    <div className="fixed inset-y-0 right-0 z-50 w-full sm:w-[500px] bg-surface-card border-l border-border shadow-2xl flex flex-col animate-in slide-in-from-right duration-200 select-text">
      {/* 顶部标题栏 */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-border bg-surface-50">
        <div className="flex items-center space-x-2.5 min-w-0">
          <div className="p-1 rounded-lg bg-emerald-500/10 text-emerald-500 shrink-0">
            <BookOpen className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground flex items-center space-x-2 truncate">
              <span className="truncate">{liveSkill.name}</span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-200 text-muted-foreground border border-border/70 flex-shrink-0">
                v{liveSkill.version || '1.0.0'}
              </span>
              {liveSkill.scope === 'workspace' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 flex-shrink-0">
                  {liveSkill.is_override ? t('工作区重写') : t('工作区专属')}
                </span>
              )}
            </h3>
            <div className="flex items-center space-x-2 mt-0.5">
              <span className="text-xs font-mono text-emerald-600 dark:text-emerald-400">
                {liveSkill.command}
              </span>
              <button
                onClick={handleCopyCommand}
                className="text-[10px] text-muted-foreground hover:text-foreground flex items-center space-x-0.5 transition"
              >
                {copied ? (
                  <Check className="w-3 h-3 text-emerald-500" />
                ) : (
                  <Copy className="w-3 h-3" />
                )}
                <span>{copied ? t('已复制') : t('复制指令')}</span>
              </button>
            </div>
          </div>
        </div>

        <div className="flex items-center space-x-1.5 shrink-0">
          <button
            onClick={handleToggleHistoryView}
            title={viewMode === 'history' ? t('返回 SOP 规程') : t('查看版本历史与回滚')}
            className={`px-2.5 py-1.5 rounded-lg text-xs font-medium flex items-center space-x-1 border transition ${
              viewMode === 'history'
                ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/40'
                : 'bg-surface-100 hover:bg-surface-200 text-muted-foreground hover:text-foreground border-border'
            }`}
          >
            <History className="w-3.5 h-3.5" />
            <span>{viewMode === 'history' ? t('SOP 正文') : t('版本历史')}</span>
          </button>
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-surface-200 text-muted-foreground hover:text-foreground transition"
            title={t('关闭')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* 技能描述区（随热更新与回滚实时同步） */}
      <div className="px-5 py-3 border-b border-border bg-surface-100">
        <div className="text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap">
          {liveSkill.description || t('暂无详细描述')}
        </div>
      </div>

      {/* 操作提示横幅 */}
      {actionBanner && (
        <div
          className={`px-5 py-2.5 text-xs border-b flex items-center justify-between ${
            actionBanner.type === 'success'
              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20'
              : 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20'
          }`}
        >
          <span>{actionBanner.text}</span>
          <button
            onClick={() => setActionBanner(null)}
            className="ml-2 opacity-70 hover:opacity-100"
            title={t('关闭提示')}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* 主内容区：SOP 正文 或 版本历史列表 */}
      {viewMode === 'sop' ? (
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
      ) : (
        <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-surface-card">
          <div className="flex items-center justify-between px-1">
            <div className="text-xs text-muted-foreground">
              {t('当前生效版本：')}
              <span className="font-mono font-semibold text-foreground ml-1">
                v{liveSkill.version || '1.0.0'}
              </span>
            </div>
            <button
              onClick={() => void loadVersions(liveSkill.name)}
              disabled={loadingVersions}
              className="flex items-center space-x-1 px-2 py-1 rounded-md border border-border bg-surface-100 hover:bg-surface-200 text-[11px] text-muted-foreground hover:text-foreground transition disabled:opacity-50"
            >
              <RotateCw className={`w-3 h-3 ${loadingVersions ? 'animate-spin' : ''}`} />
              <span>{t('刷新列表')}</span>
            </button>
          </div>

          {loadingVersions ? (
            <div className="flex items-center space-x-2 text-xs text-muted-foreground py-8 justify-center">
              <Loader2 className="w-4 h-4 animate-spin text-emerald-500" />
              <span>{t('正在加载历史版本快照...')}</span>
            </div>
          ) : versionsError ? (
            <div className="p-4 rounded-xl border border-rose-500/30 bg-rose-500/10 text-xs text-rose-600 dark:text-rose-400">
              {versionsError}
            </div>
          ) : versions.length === 0 ? (
            <div className="p-6 rounded-xl border border-dashed border-border text-center text-xs text-muted-foreground leading-relaxed">
              {t('暂无历史版本记录。每次通过沙盒或编辑器更新 SKILL.md 时，系统都会自动留存历史快照。')}
            </div>
          ) : (
            <div className="space-y-2.5">
              {versions.map((item) => {
                const isExpanded = expandedVersionId === item.version_id;
                const detail = versionDetails[item.version_id];
                const isRollingBack = rollingBackId === item.version_id;
                const sourceText =
                  (item.source && SOURCE_LABELS[item.source]) || item.source || '自动备份';
                const sizeText = formatSizeBytes(item.size_bytes ?? item.size);

                return (
                  <div
                    key={item.version_id}
                    className={`rounded-xl border p-3 transition ${
                      item.is_current
                        ? 'border-emerald-500/40 bg-emerald-500/5'
                        : 'border-border bg-surface-50 hover:border-border-hover'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center flex-wrap gap-1.5">
                          <span className="text-xs font-mono font-semibold text-foreground">
                            v{item.version || '1.0.0'}
                          </span>
                          {item.is_current && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 font-medium">
                              {t('当前版本')}
                            </span>
                          )}
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-200 text-muted-foreground border border-border/60">
                            {sourceText}
                          </span>
                          {sizeText && (
                            <span className="text-[10px] text-muted-foreground font-mono">
                              {sizeText}
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-muted-foreground mt-1">
                          {formatVersionTime(item.created_at)}
                        </div>
                        {item.description && (
                          <p className="text-xs text-foreground/85 mt-1.5 line-clamp-2 leading-relaxed">
                            {item.description}
                          </p>
                        )}
                      </div>

                      <div className="flex items-center space-x-1.5 shrink-0">
                        <button
                          onClick={() => void handleToggleVersionPreview(item.version_id)}
                          className="px-2 py-1 rounded-md border border-border bg-surface-100 hover:bg-surface-200 text-[11px] text-foreground flex items-center space-x-1 transition"
                        >
                          {isExpanded ? (
                            <ChevronDown className="w-3 h-3" />
                          ) : (
                            <ChevronRight className="w-3 h-3" />
                          )}
                          <span>{isExpanded ? t('收起') : t('预览')}</span>
                        </button>

                        {!item.is_current && (
                          <button
                            onClick={() => void handleRollback(item)}
                            disabled={Boolean(rollingBackId)}
                            className="px-2.5 py-1 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-medium flex items-center space-x-1 transition disabled:opacity-50"
                          >
                            {isRollingBack ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <RotateCcw className="w-3 h-3" />
                            )}
                            <span>{isRollingBack ? t('回滚中...') : t('回滚')}</span>
                          </button>
                        )}
                      </div>
                    </div>

                    {isExpanded && (
                      <div className="mt-3 pt-3 border-t border-border/70">
                        {loadingDetailId === item.version_id ? (
                          <div className="flex items-center space-x-2 text-xs text-muted-foreground py-2">
                            <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-500" />
                            <span>{t('正在加载快照内容...')}</span>
                          </div>
                        ) : detail ? (
                          <pre className="text-[11px] font-mono text-foreground/90 bg-surface-card border border-border rounded-lg p-3 overflow-x-auto max-h-64 whitespace-pre-wrap leading-relaxed">
                            {detail.content || detail.body || t('（该快照无正文内容）')}
                          </pre>
                        ) : (
                          <div className="text-xs text-muted-foreground">
                            {t('未获取到快照内容')}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* 底部操作栏 */}
      <div className="p-4 border-t border-border bg-surface-50 flex items-center justify-between">
        <div>
          {liveSkill.scope === 'workspace' && workspaceId && (
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
