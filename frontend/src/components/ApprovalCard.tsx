'use client';

import React, { useState, useEffect, useRef } from 'react';
import { ShieldAlert, Check, BookmarkPlus, SlidersHorizontal, Ban, Clock } from 'lucide-react';
import { PendingApprovalCall } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import { useLanguage } from '@/context/LanguageContext';

interface ApprovalCardProps {
  loopId: string;
  approvalCall: PendingApprovalCall;
  onResolved: () => void;
}

export function ApprovalCard({ loopId, approvalCall, onResolved }: ApprovalCardProps) {
  const { t } = useLanguage();
  const TOTAL_SECONDS = 20;
  const [timeLeft, setTimeLeft] = useState(TOTAL_SECONDS);
  const [isModifying, setIsModifying] = useState(false);
  const [isRejecting, setIsRejecting] = useState(false);
  const [modifiedArgsJson, setModifiedArgsJson] = useState(
    JSON.stringify(approvalCall.arguments, null, 2)
  );
  const [userGuidance, setUserGuidance] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [isAutoBlocked, setIsAutoBlocked] = useState(false);
  const [jsonError, setJsonError] = useState<string | null>(null);

  const submittingRef = useRef(false);
  submittingRef.current = submitting;

  // 20 秒倒计时：未操作时自动触发封锁与驳回
  useEffect(() => {
    if (timeLeft <= 0) {
      if (!submittingRef.current && !isAutoBlocked) {
        setIsAutoBlocked(true);
        void handleAction('reject', true);
      }
      return;
    }

    const timer = setInterval(() => {
      setTimeLeft((prev) => prev - 1);
    }, 1000);

    return () => clearInterval(timer);
  }, [timeLeft, isAutoBlocked]);

  const handleAction = async (
    action: 'approve_once' | 'approve_all' | 'modify_args' | 'reject',
    isAutoTimeout = false
  ) => {
    if (submittingRef.current) return;
    setSubmitting(true);
    setJsonError(null);

    try {
      let bodyPayload: Record<string, any> = { action };

      if (action === 'modify_args') {
        try {
          const parsed = JSON.parse(modifiedArgsJson);
          bodyPayload.modified_arguments = parsed;
        } catch (e: any) {
          setJsonError(t('JSON 格式有误，请检查'));
          setSubmitting(false);
          return;
        }
      } else if (action === 'reject') {
        bodyPayload.user_guidance = isAutoTimeout
          ? t('【20秒超时自动封锁】审批卡未在时限内做出决定，安全网关已自动封锁该高危操作。')
          : userGuidance.trim() || t('用户已拒绝该项高危操作');
      }

      const res = await apiFetch(`/api/v1/loop/${loopId}/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(bodyPayload),
      });

      if (res.ok) {
        onResolved();
      }
    } catch (e) {
      console.error('提交审批反馈异常:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 精简提炼关键操作参数，方便 20 秒内一眼看清
  const compactArgsPreview = React.useMemo(() => {
    const args = approvalCall.arguments || {};
    const cmd = args.command || args.cmd || args.path || args.file_path || args.url || args.question;
    if (typeof cmd === 'string' && cmd.trim()) {
      return cmd.trim();
    }
    const raw = JSON.stringify(args);
    return raw.length > 220 ? `${raw.slice(0, 220)}...` : raw;
  }, [approvalCall.arguments]);

  return (
    <div className="w-full my-3 rounded-xl border border-rose-500/45 bg-rose-500/10 text-foreground shadow-lg p-3.5 sm:p-4 select-text transition relative overflow-hidden">
      {/* 顶部 20 秒红色倒计时进度条 */}
      <div className="absolute top-0 left-0 right-0 h-1 bg-rose-500/15">
        <div
          className="h-full bg-rose-500 transition-all duration-1000 ease-linear"
          style={{ width: `${Math.max(0, (timeLeft / TOTAL_SECONDS) * 100)}%` }}
        />
      </div>

      <div className="flex items-center justify-between gap-2 pt-0.5">
        <div className="flex items-center gap-2 min-w-0">
          <ShieldAlert className="w-4 h-4 text-rose-500 shrink-0 animate-pulse" />
          <span className="text-xs sm:text-sm font-semibold text-rose-500 truncate">
            {t('安全风控拦截 · 请确认是否放行')}
          </span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-rose-500/15 border border-rose-500/30 text-rose-400 shrink-0">
            {approvalCall.tool_name}
          </span>
        </div>

        <div className="flex items-center gap-1 text-xs font-mono px-2 py-0.5 rounded-md bg-rose-500/15 border border-rose-500/35 text-rose-400 shrink-0">
          <Clock className="w-3.5 h-3.5" />
          {timeLeft > 0 ? <span>{timeLeft}s</span> : <span>{t('已封锁')}</span>}
        </div>
      </div>

      {/* 精简核心风险说明 + 拟执行内容 */}
      <div className="mt-2.5 space-y-1.5 text-xs">
        <div className="text-foreground/90 leading-relaxed">
          <span className="font-semibold text-rose-400 mr-1.5">{t('风险操作')}:</span>
          <span>{approvalCall.risk_reason}</span>
        </div>
        <div className="px-2.5 py-1.5 rounded-lg bg-black/25 border border-rose-500/25 font-mono text-[11px] text-foreground/85 break-all line-clamp-3">
          {compactArgsPreview}
        </div>
      </div>

      {isModifying && (
        <div className="mt-2.5 space-y-1.5">
          <textarea
            value={modifiedArgsJson}
            onChange={(e) => setModifiedArgsJson(e.target.value)}
            className="w-full text-xs font-mono p-2.5 rounded-lg bg-black/30 border border-rose-500/35 text-foreground focus:outline-none min-h-[100px]"
          />
          {jsonError && <p className="text-xs text-rose-400 font-mono">{jsonError}</p>}
        </div>
      )}

      {isRejecting && (
        <div className="mt-2.5">
          <input
            type="text"
            value={userGuidance}
            onChange={(e) => setUserGuidance(e.target.value)}
            placeholder={t('可选：输入替代指引建议...')}
            className="w-full text-xs px-2.5 py-1.5 rounded-lg bg-black/30 border border-rose-500/35 text-foreground focus:outline-none"
          />
        </div>
      )}

      {/* 精简三主按钮：放行本次 / 沙盒全放行白名单 / 拒绝 */}
      <div className="mt-3 pt-2.5 border-t border-rose-500/25 flex flex-wrap items-center gap-2">
        <button
          onClick={() => handleAction('approve_once')}
          disabled={submitting || timeLeft <= 0}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm transition disabled:opacity-40 flex items-center gap-1"
        >
          <Check className="w-3.5 h-3.5" />
          <span>{t('放行本次')}</span>
        </button>

        <button
          onClick={() => handleAction('approve_all')}
          disabled={submitting || timeLeft <= 0}
          className="text-xs font-medium px-3 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 text-rose-300 border border-rose-500/35 transition disabled:opacity-40 flex items-center gap-1"
          title={t('加入当前沙盒白名单，后续同类操作自动放行')}
        >
          <BookmarkPlus className="w-3.5 h-3.5" />
          <span>{t('沙盒全放行白名单')}</span>
        </button>

        {!isModifying ? (
          <button
            onClick={() => setIsModifying(true)}
            disabled={submitting || timeLeft <= 0}
            className="text-[11px] px-2.5 py-1.5 rounded-lg border border-border/70 bg-surface-100/40 hover:bg-surface-200 text-muted-foreground hover:text-foreground transition disabled:opacity-40 flex items-center gap-1"
          >
            <SlidersHorizontal className="w-3 h-3" />
            <span>{t('改参')}</span>
          </button>
        ) : (
          <button
            onClick={() => handleAction('modify_args')}
            disabled={submitting || timeLeft <= 0}
            className="text-xs font-medium px-3 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-black transition disabled:opacity-40"
          >
            {t('按新参数放行')}
          </button>
        )}

        {!isRejecting ? (
          <button
            onClick={() => handleAction('reject')}
            disabled={submitting || timeLeft <= 0}
            className="text-xs font-medium px-3 py-1.5 rounded-lg bg-surface-200/70 hover:bg-rose-600 hover:text-white text-foreground border border-rose-500/30 transition disabled:opacity-40 ml-auto flex items-center gap-1"
          >
            <Ban className="w-3.5 h-3.5" />
            <span>{t('拒绝拦截')}</span>
          </button>
        ) : (
          <button
            onClick={() => handleAction('reject')}
            disabled={submitting || timeLeft <= 0}
            className="text-xs font-medium px-3 py-1.5 rounded-lg bg-rose-600 text-white transition disabled:opacity-40 ml-auto"
          >
            {t('确认拒绝')}
          </button>
        )}
      </div>
    </div>
  );
}
