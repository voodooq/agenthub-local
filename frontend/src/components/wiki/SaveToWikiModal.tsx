'use client';

import React, { useEffect, useState } from 'react';
import { BookPlus, Check, Loader2, X } from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface WikiSpaceOption {
  wiki_space_id: string;
  name: string;
  published_count?: number;
  inbox_count?: number;
}

interface SaveToWikiModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialContent: string;
  defaultSourceType?: 'chat_decision' | 'sql_snapshot' | 'meeting_note';
  defaultTitle?: string;
}

/**
 * 「存到Wiki」轻量选择弹窗
 * 支持将对话结论或 SQL 统计卡片投递至指定 Wiki 空间的 Inbox 待审阅。
 */
export function SaveToWikiModal({
  isOpen,
  onClose,
  initialContent,
  defaultSourceType = 'chat_decision',
  defaultTitle = '',
}: SaveToWikiModalProps) {
  const [spaces, setSpaces] = useState<WikiSpaceOption[]>([]);
  const [selectedSpaceId, setSelectedSpaceId] = useState<string>('');
  const [targetSlug, setTargetSlug] = useState<string>('');
  const [titleHint, setTitleHint] = useState<string>(defaultTitle);
  const [sourceType, setSourceType] = useState<'chat_decision' | 'sql_snapshot' | 'meeting_note'>(defaultSourceType);
  const [newSpaceName, setNewSpaceName] = useState<string>('');
  const [creatingSpace, setCreatingSpace] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [doneMsg, setDoneMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setDoneMsg(null);
    setErrorMsg(null);
    setSourceType(defaultSourceType);
    setTitleHint(defaultTitle);
    setTargetSlug('');
    loadSpaces();
  }, [isOpen, defaultSourceType, defaultTitle]);

  const loadSpaces = async () => {
    try {
      const res = await apiFetch('/api/v1/wiki/spaces');
      if (res.ok) {
        const json = await res.json();
        const list: WikiSpaceOption[] = json.data || [];
        setSpaces(list);
        if (list.length > 0 && !selectedSpaceId) {
          setSelectedSpaceId(list[0].wiki_space_id);
        }
      }
    } catch {
      setErrorMsg('加载 Wiki 空间列表失败');
    }
  };

  const handleQuickCreateSpace = async () => {
    if (!newSpaceName.trim()) return;
    setCreatingSpace(true);
    setErrorMsg(null);
    try {
      const res = await apiFetch('/api/v1/wiki/spaces', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newSpaceName.trim() }),
      });
      if (res.ok) {
        const json = await res.json();
        const created = json.data;
        setNewSpaceName('');
        await loadSpaces();
        if (created?.wiki_space_id) {
          setSelectedSpaceId(created.wiki_space_id);
        }
      }
    } catch {
      setErrorMsg('创建空间失败');
    } finally {
      setCreatingSpace(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedSpaceId) {
      setErrorMsg('请先选择或创建一个目标 Wiki 空间');
      return;
    }
    setSubmitting(true);
    setErrorMsg(null);
    try {
      const res = await apiFetch('/api/v1/wiki/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wiki_space_id: selectedSpaceId,
          source_type: sourceType,
          content: initialContent,
          target_slug: targetSlug.trim() || undefined,
          auto_publish: true,
          source_meta: {
            title: titleHint.trim() || undefined,
            source_title: sourceType === 'sql_snapshot' ? '数据统计快照' : '对话业务结论',
          },
        }),
      });
      if (res.ok) {
        const json = await res.json();
        const isPendingMerge = json.data?.merge_mode === 'pending_diff_on_existing';
        setDoneMsg(
          isPendingMerge
            ? `命中既有词条，增量已送入待审阅：${json.data?.title || '词条更新'}`
            : `已直接收藏到口袋 Wiki：${json.data?.title || '新词条'}`
        );
        setTimeout(() => {
          onClose();
        }, 900);
      } else {
        const err = await res.json().catch(() => ({}));
        setErrorMsg(err.detail || '沉淀到 Wiki 失败');
      }
    } catch (err: any) {
      setErrorMsg(err?.message || '网络请求失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
      <div className="w-full max-w-md rounded-2xl border border-border bg-surface-card text-foreground shadow-xl">
        <div className="px-4 py-3.5 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <BookPlus className="w-4 h-4 text-emerald-600 dark:text-emerald-400" strokeWidth={1.75} />
            <span>收藏到口袋 Wiki</span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
          >
            <X className="w-4 h-4" strokeWidth={1.75} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-4 space-y-3 text-xs">
          <div>
            <label className="block font-medium mb-1">目标 Wiki 空间</label>
            {spaces.length > 0 ? (
              <select
                value={selectedSpaceId}
                onChange={(e) => setSelectedSpaceId(e.target.value)}
                className="w-full px-3 py-2 rounded-md border border-border bg-surface-200 text-foreground focus:outline-none focus:border-blue-600"
              >
                {spaces.map((s) => (
                  <option key={s.wiki_space_id} value={s.wiki_space_id}>
                    {s.name} ({s.published_count || 0} 词条)
                  </option>
                ))}
              </select>
            ) : (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={newSpaceName}
                  onChange={(e) => setNewSpaceName(e.target.value)}
                  placeholder="暂无空间，输入名称快速创建"
                  className="flex-1 px-3 py-2 rounded-md border border-border bg-surface-200 text-foreground focus:outline-none"
                />
                <button
                  type="button"
                  disabled={creatingSpace || !newSpaceName.trim()}
                  onClick={handleQuickCreateSpace}
                  className="px-3 py-2 rounded-md bg-slate-900 text-white dark:bg-zinc-100 dark:text-zinc-900 font-medium disabled:opacity-50"
                >
                  {creatingSpace ? '创建中' : '创建'}
                </button>
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <label className="block font-medium mb-1">来源类型</label>
              <select
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value as any)}
                className="w-full px-2.5 py-2 rounded-md border border-border bg-surface-200 text-foreground focus:outline-none"
              >
                <option value="chat_decision">对话业务结论</option>
                <option value="sql_snapshot">数据统计快照</option>
                <option value="meeting_note">会议决策纪要</option>
              </select>
            </div>
            <div>
              <label className="block font-medium mb-1">合并词条（选填）</label>
              <input
                type="text"
                value={targetSlug}
                onChange={(e) => setTargetSlug(e.target.value)}
                placeholder="留空则自动语义归类"
                className="w-full px-2.5 py-2 rounded-md border border-border bg-surface-200 text-foreground focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block font-medium mb-1">标题提示（选填）</label>
            <input
              type="text"
              value={titleHint}
              onChange={(e) => setTitleHint(e.target.value)}
              placeholder="例如：2026总决赛报名费补充说明"
              className="w-full px-3 py-2 rounded-md border border-border bg-surface-200 text-foreground focus:outline-none"
            />
          </div>

          {errorMsg && (
            <div className="px-3 py-2 rounded-md border border-rose-500/40 bg-rose-500/10 text-rose-600 dark:text-rose-400">
              {errorMsg}
            </div>
          )}

          {doneMsg && (
            <div className="px-3 py-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
              <Check className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} />
              <span>{doneMsg}</span>
            </div>
          )}

          <div className="pt-2 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl border border-border hover:bg-surface-200 transition-colors"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={submitting || !selectedSpaceId}
              className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-medium flex items-center gap-1.5 disabled:opacity-50"
            >
              {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              <span>{submitting ? '正在收藏...' : '确认收藏'}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

