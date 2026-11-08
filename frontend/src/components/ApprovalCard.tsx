'use strict';

import React, { useState } from 'react';
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
    <div className="w-full my-4 rounded-xl border border-rose-500/40 bg-zinc-900/95 shadow-2xl p-4 sm:p-5 backdrop-blur-md">
      <div className="flex items-start justify-between">
        <div className="flex items-center space-x-2.5">
          <span className="flex h-3 w-3 relative">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-3 w-3 bg-rose-500"></span>
          </span>
          <h4 className="text-sm font-semibold text-rose-300">
            {t('🛡️ 敏感操作拦截 · 需要人工审批')}
          </h4>
        </div>
        <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-rose-500/20 text-rose-300 border border-rose-500/30">
          {approvalCall.policy_level} {t('拦截')}
        </span>
      </div>

      <div className="mt-3 p-2.5 rounded-lg bg-rose-950/20 border border-rose-800/30 text-xs text-rose-200 leading-relaxed">
        <strong>{t('高危原因')}:</strong> {approvalCall.risk_reason}
      </div>

      {approvalCall.snapshot_path && (
        <div className="mt-2 text-[11px] text-zinc-400">
          <span>{t('安全快照已备份至')}: </span>
          <code className="text-zinc-300 bg-zinc-800 px-1 py-0.5 rounded font-mono">
            {approvalCall.snapshot_path}
          </code>
        </div>
      )}

      <div className="mt-3">
        <div className="text-[11px] text-zinc-400 font-medium mb-1">
          {t('拟调用工具')}: <span className="text-emerald-400 font-mono">{approvalCall.tool_name}</span>
        </div>
        {!isModifying ? (
          <pre className="text-xs font-mono p-2.5 rounded-lg bg-black/50 border border-zinc-800 text-zinc-300 overflow-x-auto max-h-40">
            {JSON.stringify(approvalCall.arguments, null, 2)}
          </pre>
        ) : (
          <div className="space-y-1.5">
            <textarea
              value={modifiedArgsJson}
              onChange={(e) => setModifiedArgsJson(e.target.value)}
              className="w-full text-xs font-mono p-2.5 rounded-lg bg-black/70 border border-zinc-700 text-zinc-200 focus:outline-none focus:border-amber-500 min-h-[120px]"
            />
            {jsonError && <p className="text-xs text-red-400">{jsonError}</p>}
          </div>
        )}
      </div>

      {isRejecting && (
        <div className="mt-3">
          <label className="block text-[11px] text-zinc-400 mb-1">
            {t('改道引导建议 (告诉 AI 为什么拒绝以及换什么安全方案)')}:
          </label>
          <input
            type="text"
            value={userGuidance}
            onChange={(e) => setUserGuidance(e.target.value)}
            placeholder={t('例如：请改用只读扫描，或将文件保存到当前目录下的 output/ 中...')}
            className="w-full text-xs px-3 py-2 rounded-lg bg-black/60 border border-zinc-700 text-zinc-200 focus:outline-none focus:border-rose-500"
          />
        </div>
      )}

      {/* 四按键操作区 */}
      <div className="mt-4 pt-3 border-t border-zinc-800/80 flex flex-wrap items-center gap-2">
        <button
          onClick={() => handleAction('approve_once')}
          disabled={submitting}
          className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm transition disabled:opacity-50"
        >
          {t('✅ 批准本次')}
        </button>

        <button
          onClick={() => handleAction('approve_all')}
          disabled={submitting}
          className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white shadow-sm transition disabled:opacity-50"
          title={t('在本次任务后续执行中，不再重复拦截该工具')}
        >
          {t('🛡️ 本任务白名单')}
        </button>

        {!isModifying ? (
          <button
            onClick={() => setIsModifying(true)}
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-amber-300 border border-zinc-700 transition"
          >
            {t('✏️ 改参另存为')}
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
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-rose-300 border border-zinc-700 transition ml-auto"
          >
            {t('❌ 拒绝改道')}
          </button>
        ) : (
          <button
            onClick={() => handleAction('reject')}
            disabled={submitting}
            className="text-xs font-medium px-3.5 py-1.5 rounded-lg bg-red-600 hover:bg-red-500 text-white shadow-sm transition disabled:opacity-50 ml-auto"
          >
            {t('确认驳回并改道')}
          </button>
        )}
      </div>
    </div>
  );
}

