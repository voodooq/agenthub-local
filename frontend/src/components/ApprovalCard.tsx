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
  const [timeLeft, setTimeLeft] = useState(15);
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

  // 15 秒倒计时：未操作时自动触发封锁与驳回
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
          ? t('【15秒超时自动封锁】审批卡未在时限内做出决定，安全网关已自动封锁该高危操作。')
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

  return (
    <div className="w-full my-4 rounded-xl border border-zinc-700 bg-zinc-900/90 text-zinc-100 shadow-xl p-4 sm:p-5 select-text transition relative overflow-hidden">
      {/* 顶部 15 秒倒计时扁平微进度条 */}
      <div className="absolute top-0 left-0 right-0 h-0.5 bg-zinc-800">
        <div
          className="h-full bg-zinc-400 transition-all duration-1000 ease-linear"
          style={{ width: `${Math.max(0, (timeLeft / 15) * 100)}%` }}
        />
      </div>

      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center space-x-2.5 min-w-0">
          <div className="p-1.5 rounded-lg bg-zinc-800 border border-zinc-700 text-zinc-200 shrink-0">
            <ShieldAlert className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <h4 className="text-sm font-semibold text-zinc-100 tracking-tight flex items-center gap-2">
              <span>{t('高危操作拦截 · 需要人工审批')}</span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-zinc-300">
                {approvalCall.policy_level || 'L2'}
              </span>
            </h4>
          </div>
        </div>

        {/* 倒计时单色状态指示 */}
        <div className="flex items-center space-x-1.5 text-xs font-mono px-2 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-zinc-300 shrink-0">
          <Clock className="w-3.5 h-3.5" />
          {timeLeft > 0 ? (
            <span>{t('倒计时')} {timeLeft}s {t('自动封锁')}</span>
          ) : (
            <span className="text-zinc-400">{t('已自动封锁')}</span>
          )}
        </div>
      </div>

      {/* 风险原因 */}
      <div className="mt-3 p-3 rounded-lg bg-zinc-950/70 border border-zinc-800 text-xs text-zinc-300 leading-relaxed font-sans">
        <span className="text-zinc-400 font-medium mr-1.5">{t('拦截原因')}:</span>
        {approvalCall.risk_reason}
      </div>

      {approvalCall.snapshot_path && (
        <div className="mt-2 text-[11px] text-zinc-400 flex items-center gap-1.5">
          <span>{t('安全快照已自动备份至')}:</span>
          <code className="text-zinc-200 bg-zinc-800/80 px-1.5 py-0.5 rounded font-mono border border-zinc-700 text-[10px]">
            {approvalCall.snapshot_path}
          </code>
        </div>
      )}

      {/* 拟调用工具与参数预览 / 改参编辑区 */}
      <div className="mt-3.5">
        <div className="text-[11px] text-zinc-400 font-medium mb-1.5 flex items-center justify-between">
          <span>
            {t('拟调用工具')}: <span className="text-zinc-100 font-mono font-semibold">{approvalCall.tool_name}</span>
          </span>
          {isModifying && (
            <span className="text-[10px] text-zinc-400 font-mono">{t('编辑 JSON 参数后放行')}</span>
          )}
        </div>

        {!isModifying ? (
          <pre className="text-xs font-mono p-3 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-300 overflow-x-auto max-h-44">
            {JSON.stringify(approvalCall.arguments, null, 2)}
          </pre>
        ) : (
          <div className="space-y-1.5">
            <textarea
              value={modifiedArgsJson}
              onChange={(e) => setModifiedArgsJson(e.target.value)}
              className="w-full text-xs font-mono p-3 rounded-lg bg-zinc-950 border border-zinc-700 text-zinc-200 focus:outline-none focus:border-zinc-400 min-h-[130px]"
            />
            {jsonError && <p className="text-xs text-zinc-400 font-mono">{jsonError}</p>}
          </div>
        )}
      </div>

      {isRejecting && (
        <div className="mt-3">
          <label className="block text-[11px] text-zinc-400 mb-1">
            {t('改道引导建议 (指引 AI 采用何种替代安全方案)')}:
          </label>
          <input
            type="text"
            value={userGuidance}
            onChange={(e) => setUserGuidance(e.target.value)}
            placeholder={t('例如：请将输出文件写入工作区 output/ 目录，避免跨目录修改...')}
            className="w-full text-xs px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-700 text-zinc-200 focus:outline-none focus:border-zinc-400"
          />
        </div>
      )}

      {/* 四按键操作区 (单色扁平化设计) */}
      <div className="mt-4 pt-3 border-t border-zinc-800 flex flex-wrap items-center gap-2">
        <button
          onClick={() => handleAction('approve_once')}
          disabled={submitting || timeLeft <= 0}
          className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-zinc-100 hover:bg-white text-zinc-900 border border-zinc-200 shadow-sm transition disabled:opacity-40 flex items-center space-x-1.5"
        >
          <Check className="w-3.5 h-3.5" />
          <span>{t('批准本次')}</span>
        </button>

        <button
          onClick={() => handleAction('approve_all')}
          disabled={submitting || timeLeft <= 0}
          className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 transition disabled:opacity-40 flex items-center space-x-1.5"
          title={t('在本次任务后续执行中，不再重复拦截该工具')}
        >
          <BookmarkPlus className="w-3.5 h-3.5" />
          <span>{t('本任务白名单')}</span>
        </button>

        {!isModifying ? (
          <button
            onClick={() => setIsModifying(true)}
            disabled={submitting || timeLeft <= 0}
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 transition disabled:opacity-40 flex items-center space-x-1.5"
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            <span>{t('改参另存为')}</span>
          </button>
        ) : (
          <button
            onClick={() => handleAction('modify_args')}
            disabled={submitting || timeLeft <= 0}
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-zinc-200 hover:bg-white text-zinc-900 border border-zinc-300 transition disabled:opacity-40"
          >
            {t('确认以新参数放行')}
          </button>
        )}

        {!isRejecting ? (
          <button
            onClick={() => setIsRejecting(true)}
            disabled={submitting || timeLeft <= 0}
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700 transition disabled:opacity-40 ml-auto flex items-center space-x-1.5"
          >
            <Ban className="w-3.5 h-3.5" />
            <span>{t('拒绝并改道')}</span>
          </button>
        ) : (
          <button
            onClick={() => handleAction('reject')}
            disabled={submitting || timeLeft <= 0}
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-100 border border-zinc-600 transition disabled:opacity-40 ml-auto"
          >
            {t('确认驳回并改道')}
          </button>
        )}
      </div>
    </div>
  );
}
