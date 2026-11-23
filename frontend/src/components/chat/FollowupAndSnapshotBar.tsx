'use client';

import React, { useState } from 'react';
import { CornerDownRight, BookmarkPlus, CheckCircle2, ShieldAlert, RefreshCw } from 'lucide-react';
import { FollowupSuggestionItem, StatsSnapshotOffer } from '@/types';
import { apiFetch } from '@/utils/apiClient';

interface FollowupAndSnapshotBarProps {
  messageContent: string;
  suggestions?: FollowupSuggestionItem[];
  snapshotOffer?: StatsSnapshotOffer;
  onSelectFollowup: (item: FollowupSuggestionItem) => void;
  disabled?: boolean;
}

/**
 * 回答底部的「统计快照一键沉淀」与「下一步追问引导卡片」（2~3 张横向圆角卡片，带 ↳ 图标）
 */
export function FollowupAndSnapshotBar({
  messageContent,
  suggestions,
  snapshotOffer,
  onSelectFollowup,
  disabled = false,
}: FollowupAndSnapshotBarProps) {
  const [savingSnapshot, setSavingSnapshot] = useState(false);
  const [savedSpaceName, setSavedSpaceName] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const hasSuggestions = Array.isArray(suggestions) && suggestions.length > 0;
  const hasSnapshotOffer = Boolean(snapshotOffer && snapshotOffer.agent_id);

  if (!hasSuggestions && !hasSnapshotOffer) {
    return null;
  }

  const handleSaveSnapshot = async () => {
    if (!snapshotOffer || snapshotOffer.has_audit_alert || savingSnapshot || savedSpaceName) {
      return;
    }
    setSavingSnapshot(true);
    setSaveError(null);
    try {
      const res = await apiFetch('/api/v1/wiki/stats-snapshot', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: snapshotOffer.agent_id,
          title: snapshotOffer.title || '业务统计快照',
          markdown_content: messageContent,
          query_scope: snapshotOffer.query_scope || snapshotOffer.title || '',
          verified_sqls: snapshotOffer.verified_sqls || [],
          profile_id: snapshotOffer.profile_id || '',
        }),
      });
      const json = await res.json();
      if (!res.ok) {
        setSaveError(json.detail || '沉淀百科统计快照失败');
        return;
      }
      const spaceName = json?.data?.wiki_space_name || '专家专属百科空间';
      setSavedSpaceName(spaceName);
    } catch (err: any) {
      setSaveError(err?.message || '网络异常，沉淀失败');
    } finally {
      setSavingSnapshot(false);
    }
  };

  return (
    <div className="mt-3 space-y-3 max-w-[92%] md:max-w-[85%] select-none">
      {/* 统计结果确认沉淀条（受 R000-R007 红线审计门控） */}
      {hasSnapshotOffer && snapshotOffer && (
        <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2.5 rounded-xl bg-surface-100/80 border border-border/70 text-xs">
          {snapshotOffer.has_audit_alert ? (
            <div className="flex items-center gap-2 text-amber-500">
              <ShieldAlert className="w-4 h-4 shrink-0" />
              <span>本轮查数存在数据结构红线审计告警，已自动拦截沉淀为百科统计快照</span>
            </div>
          ) : savedSpaceName ? (
            <div className="flex items-center gap-2 text-emerald-500 font-medium">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>已沉淀至【{savedSpaceName}】，下次同类统计将优先秒回并提醒统计时间</span>
            </div>
          ) : (
            <>
              <div className="text-muted-foreground">
                本次统计已通过红线审计，可沉淀为百科快照（若专家未绑定空间将自动创建并绑定）
              </div>
              <button
                type="button"
                onClick={handleSaveSnapshot}
                disabled={savingSnapshot}
                className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs font-medium flex items-center gap-1.5 transition-colors shrink-0"
              >
                {savingSnapshot ? (
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <BookmarkPlus className="w-3.5 h-3.5" />
                )}
                <span>{savingSnapshot ? '沉淀中...' : '沉淀为百科统计快照'}</span>
              </button>
            </>
          )}
          {saveError && (
            <div className="w-full text-[11px] text-red-400 mt-0.5">{saveError}</div>
          )}
        </div>
      )}

      {/* 底部下一步追问引导卡片（1:1 对齐深色圆角卡片 + 左下角 ↳ 样式） */}
      {hasSuggestions && suggestions && (
        <div className="space-y-2 pt-1">
          <div className="text-xs font-medium text-muted-foreground">
            接下来我们可以继续看：
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {suggestions.slice(0, 3).map((item, idx) => (
              <button
                key={`${item.label}-${idx}`}
                type="button"
                disabled={disabled}
                onClick={() => onSelectFollowup(item)}
                className={`group text-left p-3.5 rounded-2xl border transition-all flex flex-col justify-between min-h-[76px] disabled:opacity-50 ${
                  item.force_live
                    ? 'bg-emerald-500/5 border-emerald-500/35 hover:bg-emerald-500/10 hover:border-emerald-500/60'
                    : 'bg-surface-card/90 border-border/80 hover:bg-surface-200/80 hover:border-foreground/25'
                }`}
              >
                <span className="text-xs sm:text-[13px] text-foreground/95 leading-snug line-clamp-2 font-normal">
                  {item.label}
                </span>
                <div className="mt-2.5 flex items-center justify-between text-muted-foreground group-hover:text-foreground transition-colors">
                  <CornerDownRight className="w-3.5 h-3.5" />
                  {item.force_live && (
                    <span className="text-[10px] text-emerald-500 font-medium">
                      实时查库
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

