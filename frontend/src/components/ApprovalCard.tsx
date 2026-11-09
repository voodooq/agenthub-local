'use client';

import React, { useState } from 'react';
import { ShieldAlert, CheckCircle2, Edit3, XCircle, ShieldCheck } from 'lucide-react';
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
  const [isModifying, setIsModifying] = useState(false);
  const [isRejecting, setIsRejecting] = useState(false);
  const [modifiedArgsJson, setModifiedArgsJson] = useState(
    JSON.stringify(approvalCall.arguments, null, 2)
  );
  const [userGuidance, setUserGuidance] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [jsonError, setJsonError] = useState<string | null>(null);

  const handleAction = async (action: 'approve_once' | 'approve_all' | 'modify_args' | 'reject') => {
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
        bodyPayload.user_guidance = userGuidance.trim() || t('用户拒绝了该项高危操作');
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
    <div className="w-full my-4 rounded-xl border border-rose-500/40 bg-surface-card shadow-lg p-4 sm:p-5 select-text transition">
      <div className="flex items-start justify-between">
        <div className="flex items-center space-x-2.5">
          <div className="p-1 rounded-lg bg-rose-500/10 text-rose-500">
            <ShieldAlert className="w-4 h-4 animate-pulse" />
          </div>
          <h4 className="text-sm font-semibold text-rose-600 dark:text-rose-400">
            {t('敏感操作拦截 · 需要人工审批')}
          </h4>
        </div>
        <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20 font-medium">
          {approvalCall.policy_level} {t('拦截')}
        </span>
      </div>

      <div className="mt-3 p-2.5 rounded-lg bg-rose-500/5 border border-rose-500/20 text-xs text-rose-700 dark:text-rose-300 leading-relaxed">
        <strong>{t('高危原因')}:</strong> {approvalCall.risk_reason}
      </div>

      {approvalCall.snapshot_path && (
        <div className="mt-2 text-[11px] text-muted-foreground">
          <span>{t('安全快照已备份至')}: </span>
          <code className="text-foreground bg-surface-200 px-1 py-0.5 rounded font-mono border border-border">
            {approvalCall.snapshot_path}
          </code>
        </div>
      )}

      <div className="mt-3">
        <div className="text-[11px] text-muted-foreground font-medium mb-1">
          {t('拟调用工具')}: <span className="text-emerald-600 dark:text-emerald-400 font-mono font-semibold">{approvalCall.tool_name}</span>
        </div>
        {!isModifying ? (
          <pre className="text-xs font-mono p-2.5 rounded-lg bg-surface-200 border border-border text-foreground overflow-x-auto max-h-40">
            {JSON.stringify(approvalCall.arguments, null, 2)}
          </pre>
        ) : (
          <div className="space-y-1.5">
            <textarea
              value={modifiedArgsJson}
              onChange={(e) => setModifiedArgsJson(e.target.value)}
              className="w-full text-xs font-mono p-2.5 rounded-lg bg-surface-200 border border-border text-foreground focus:outline-none focus:border-amber-500 min-h-[120px]"
            />
            {jsonError && <p className="text-xs text-rose-500">{jsonError}</p>}
          </div>
        )}
      </div>

      {isRejecting && (
        <div className="mt-3">
          <label className="block text-[11px] text-muted-foreground mb-1">
            {t('改道引导建议 (告诉 AI 为什么拒绝以及换什么安全方案)')}:
          </label>
          <input
            type="text"
            value={userGuidance}
            onChange={(e) => setUserGuidance(e.target.value)}
            placeholder={t('例如：请改用只读扫描，或将文件保存到当前目录下的 output/ 中...')}
            className="w-full text-xs px-3 py-2 rounded-lg bg-surface-200 border border-border text-foreground focus:outline-none focus:border-rose-500"
          />
        </div>
      )}

      {/* 四按键操作区 */}
      <div className="mt-4 pt-3 border-t border-border flex flex-wrap items-center gap-2">
        <button
          onClick={() => handleAction('approve_once')}
          disabled={submitting}
          className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm transition disabled:opacity-50 flex items-center space-x-1"
        >
          <CheckCircle2 className="w-3.5 h-3.5" />
          <span>{t('批准本次')}</span>
        </button>

        <button
          onClick={() => handleAction('approve_all')}
          disabled={submitting}
          className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white shadow-sm transition disabled:opacity-50 flex items-center space-x-1"
          title={t('在本次任务后续执行中，不再重复拦截该工具')}
        >
          <ShieldCheck className="w-3.5 h-3.5" />
          <span>{t('本任务白名单')}</span>
        </button>

        {!isModifying ? (
          <button
            onClick={() => setIsModifying(true)}
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-amber-600 dark:text-amber-300 border border-border transition flex items-center space-x-1"
          >
            <Edit3 className="w-3.5 h-3.5" />
            <span>{t('改参另存为')}</span>
          </button>
        ) : (
          <button
            onClick={() => handleAction('modify_args')}
            disabled={submitting}
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white shadow-sm transition disabled:opacity-50"
          >
            {t('确认以新参数放行')}
          </button>
        )}

        {!isRejecting ? (
          <button
            onClick={() => setIsRejecting(true)}
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-rose-600 dark:text-rose-400 border border-border transition ml-auto flex items-center space-x-1"
          >
            <XCircle className="w-3.5 h-3.5" />
            <span>{t('拒绝改道')}</span>
          </button>
        ) : (
          <button
            onClick={() => handleAction('reject')}
            disabled={submitting}
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-white shadow-sm transition disabled:opacity-50 ml-auto"
          >
            {t('确认驳回并改道')}
          </button>
        )}
      </div>
    </div>
  );
}
