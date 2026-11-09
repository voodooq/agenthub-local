'use client';

import React, { useState, useMemo, useEffect, useRef } from 'react';
import {
  Compass,
  CheckCircle2,
  Send,
  Edit3,
  Rocket,
  CheckSquare,
  Square,
  Sparkles,
  HelpCircle,
  FileText,
  ShieldAlert,
} from 'lucide-react';
import {
  InteractiveInquiryPayload,
  InquiryQuestion,
  PlanStepItem,
} from '@/types';
import { useLanguage } from '@/context/LanguageContext';

interface InteractiveInquiryCardProps {
  rawContent: string;
  onSendFeedback?: (feedbackText: string) => void;
  onFillTemplate?: (templateText: string) => void;
  onApprovePlan?: (steps: PlanStepItem[], feedbackText: string) => void;
  isStreaming?: boolean;
}

export function InteractiveInquiryCard({
  rawContent,
  onSendFeedback,
  onFillTemplate,
  onApprovePlan,
  isStreaming = false,
}: InteractiveInquiryCardProps) {
  const { t } = useLanguage();

  // 1. 尝试解析 JSON，处理流式半包防抖
  const { parsed, parseError } = useMemo(() => {
    try {
      const clean = rawContent.trim();
      const obj = JSON.parse(clean) as InteractiveInquiryPayload;
      return { parsed: obj, parseError: null };
    } catch (e: any) {
      return { parsed: null, parseError: e.message };
    }
  }, [rawContent]);

  const strategyFallbackLabel = t('请选择策略：');
  const questions: InquiryQuestion[] = useMemo(() => {
    if (!parsed) return [];
    if (parsed.questions) return parsed.questions;
    if (parsed.type === 'choices' && parsed.options) {
      return [
        {
          id: 'q_default',
          type: 'single_choice',
          label: parsed.question || strategyFallbackLabel,
          options: parsed.options,
          default: parsed.options[0]?.value,
        },
      ];
    }
    return [];
  }, [parsed, strategyFallbackLabel]);

  const buildInitialAnswers = (qs: InquiryQuestion[]) => {
    const initial: Record<string, string | string[]> = {};
    for (const q of qs) {
      if (q.default !== undefined) {
        initial[q.id] = q.default;
      } else if (q.type === 'single_choice' && q.options.length > 0) {
        initial[q.id] = q.options[0].value;
      } else if (q.type === 'multi_choice') {
        initial[q.id] = q.options.map((o) => o.value);
      }
    }
    return initial;
  };

  const buildInitialSteps = (steps?: PlanStepItem[]) => {
    const initial: Record<number, boolean> = {};
    if (steps) {
      for (const step of steps) {
        initial[step.step] = step.selected !== false;
      }
    }
    return initial;
  };

  // Hooks 必须在条件早退前无条件调用，保证流式半包转完整 JSON 时状态与 Hook 顺序稳定
  const hasInitializedRef = useRef(Boolean(parsed));
  const [selectedAnswers, setSelectedAnswers] = useState<Record<string, string | string[]>>(() =>
    buildInitialAnswers(questions)
  );
  const [stepSelections, setStepSelections] = useState<Record<number, boolean>>(() =>
    buildInitialSteps(parsed?.plan_steps)
  );
  const [hasSubmitted, setHasSubmitted] = useState(false);

  useEffect(() => {
    if (!parsed || hasInitializedRef.current) return;
    hasInitializedRef.current = true;
    setSelectedAnswers(buildInitialAnswers(questions));
    setStepSelections(buildInitialSteps(parsed.plan_steps));
  }, [parsed, questions]);

  // 如果解析失败（通常处于流式吐字半包阶段），渲染呼吸骨架屏
  if (!parsed) {
    return (
      <div className="my-4 p-4 rounded-xl border border-emerald-500/30 bg-surface-card shadow-sm animate-pulse">
        <div className="flex items-center space-x-2.5 mb-3">
          <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-500">
            <Compass className="w-4 h-4 animate-spin" />
          </div>
          <span className="text-xs font-semibold text-foreground">
            {t('正在构建交互对齐方案与参数表单...')}
          </span>
        </div>
        <div className="space-y-2">
          <div className="h-3 bg-surface-200 rounded w-3/4"></div>
          <div className="h-3 bg-surface-200 rounded w-1/2"></div>
        </div>
      </div>
    );
  }

  // 兼容老版本或简化协议
  const title = parsed.title || (parsed.phase === 'plan_approval' ? t('实施方案与执行步骤审批') : t('需求与参数交互确认'));
  const summary = parsed.summary || '';
  const fillTemplate = parsed.fill_template || (
    parsed.type === 'template' && parsed.template ? {
      label: parsed.title || t('请补充以下填空内容：'),
      template_text: parsed.template,
    } : undefined
  );

  // 单选切换
  const handleSingleSelect = (questionId: string, val: string) => {
    if (hasSubmitted) return;
    setSelectedAnswers((prev) => ({ ...prev, [questionId]: val }));
  };

  // 多选切换
  const handleMultiToggle = (questionId: string, val: string) => {
    if (hasSubmitted) return;
    setSelectedAnswers((prev) => {
      const current = (prev[questionId] as string[]) || [];
      const next = current.includes(val)
        ? current.filter((x) => x !== val)
        : [...current, val];
      return { ...prev, [questionId]: next };
    });
  };

  // 步骤勾选切换
  const handleStepToggle = (stepNo: number) => {
    if (hasSubmitted) return;
    setStepSelections((prev) => ({ ...prev, [stepNo]: !prev[stepNo] }));
  };

  // 单题一键直发
  const handleDirectSendOption = (q: InquiryQuestion, opt: { value: string; label: string }) => {
    if (hasSubmitted || !onSendFeedback) return;
    const text = `已确认决策: 【${q.label}】选用方案为「${opt.label}」(${opt.value})。请继续。`;
    setHasSubmitted(true);
    onSendFeedback(text);
  };

  // 组合表单统一提交
  const handleSubmitForm = () => {
    if (hasSubmitted || !onSendFeedback) return;
    const lines: string[] = ['已确认以下配置与决策：'];
    for (const q of questions) {
      const ans = selectedAnswers[q.id];
      if (Array.isArray(ans)) {
        const labels = q.options.filter((o) => ans.includes(o.value)).map((o) => o.label);
        lines.push(`- ${q.label}: ${labels.join('、')}`);
      } else if (ans) {
        const opt = q.options.find((o) => o.value === ans);
        lines.push(`- ${q.label}: ${opt ? opt.label : ans}`);
      }
    }
    lines.push('请根据以上确认结果继续执行。');
    setHasSubmitted(true);
    onSendFeedback(lines.join('\n'));
  };

  // 方案批准并执行
  const handleApprovePlanClick = () => {
    if (hasSubmitted) return;
    const activeSteps = (parsed.plan_steps || []).filter((s) => stepSelections[s.step] !== false);
    const feedbackLines = [
      '✅ 实施方案已通过用户审批，批准执行以下步骤清单：',
      ...activeSteps.map((s) => `${s.step}. ${s.title} [${s.tool_level || 'L1'}]`),
      '请解锁执行权限并立即进入 TaskLoop 自主推演循环！',
    ];
    setHasSubmitted(true);
    if (onApprovePlan) {
      onApprovePlan(activeSteps, feedbackLines.join('\n'));
    } else if (onSendFeedback) {
      onSendFeedback(feedbackLines.join('\n'));
    }
  };

  const isPlanApproval = parsed.phase === 'plan_approval' || (parsed.plan_steps && parsed.plan_steps.length > 0);

  return (
    <div className="my-4 rounded-xl border border-emerald-500/40 bg-surface-card shadow-sm overflow-hidden select-text transition">
      {/* 顶部标题栏 */}
      <div className="px-4 py-3 bg-surface-50 border-b border-border flex items-center justify-between gap-3">
        <div className="flex items-center space-x-2 min-w-0">
          <div className="p-1 rounded-lg bg-emerald-500/10 text-emerald-500 shrink-0">
            {isPlanApproval ? <Rocket className="w-4 h-4" /> : <Compass className="w-4 h-4" />}
          </div>
          <h4 className="text-xs font-semibold text-foreground truncate">
            {title}
          </h4>
        </div>
        <span className="text-[11px] px-2 py-0.5 rounded-full font-medium shrink-0 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
          {isPlanApproval ? t('方案审批') : t('需求澄清')}
        </span>
      </div>

      <div className="p-4 space-y-4 text-xs">
        {/* 摘要说明 */}
        {summary && (
          <p className="text-muted-foreground leading-relaxed">{summary}</p>
        )}

        {/* 问询问题列表 (Questions) */}
        {questions.length > 0 && (
          <div className="space-y-4">
            {questions.map((q) => {
              const currentVal = selectedAnswers[q.id];
              return (
                <div key={q.id} className="space-y-2 p-3 rounded-lg bg-surface-100 border border-border">
                  <div className="font-medium text-foreground flex items-center justify-between">
                    <span>{q.label}</span>
                    <span className="text-[10px] text-muted-foreground">
                      {q.type === 'multi_choice' ? t('多选') : t('单选')}
                    </span>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                    {q.options.map((opt) => {
                      const isSelected =
                        q.type === 'single_choice'
                          ? currentVal === opt.value
                          : Array.isArray(currentVal) && currentVal.includes(opt.value);

                      return (
                        <div
                          key={opt.value}
                          onClick={() => {
                            if (q.type === 'single_choice') {
                              handleSingleSelect(q.id, opt.value);
                            } else {
                              handleMultiToggle(q.id, opt.value);
                            }
                          }}
                          className={`p-2.5 rounded-lg border text-left cursor-pointer transition flex flex-col justify-between ${
                            isSelected
                              ? 'border-emerald-500 bg-emerald-500/10 text-foreground'
                              : 'border-border bg-surface-card hover:bg-surface-50 text-muted-foreground hover:text-foreground'
                          } ${hasSubmitted ? 'opacity-60 cursor-not-allowed' : ''}`}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <span className="font-semibold text-foreground text-xs leading-snug">
                              {opt.label}
                            </span>
                            {isSelected ? (
                              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0 mt-0.5" />
                            ) : (
                              <div className="w-3.5 h-3.5 rounded-full border border-border shrink-0 mt-0.5" />
                            )}
                          </div>
                          {opt.desc && (
                            <p className="text-[11px] text-muted-foreground mt-1 leading-normal">
                              {opt.desc}
                            </p>
                          )}
                          {/* 单选且未提交时，支持卡片内单题直发快捷键 */}
                          {q.type === 'single_choice' && !hasSubmitted && (
                            <div className="mt-2 pt-1 border-t border-border/40 flex justify-end">
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDirectSendOption(q, opt);
                                }}
                                className="text-[10px] px-1.5 py-0.5 rounded bg-surface-200 hover:bg-emerald-500 hover:text-white text-muted-foreground transition flex items-center space-x-1"
                              >
                                <span>{t('⚡ 选此项直发')}</span>
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* 留空填空模板 (Fill-in Template) */}
        {fillTemplate && (
          <div className="p-3 rounded-lg bg-surface-100 border border-border space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-medium text-foreground">{fillTemplate.label}</span>
              <span className="text-[10px] text-muted-foreground font-mono">
                {t('支持 Tab 键跳转填空')}
              </span>
            </div>
            <div className="p-2.5 rounded-lg bg-surface-card border border-border font-mono text-xs text-foreground whitespace-pre-wrap select-all">
              {fillTemplate.template_text}
            </div>
            {onFillTemplate && !hasSubmitted && (
              <div className="flex justify-end pt-1">
                <button
                  type="button"
                  onClick={() => onFillTemplate(fillTemplate.template_text)}
                  className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs shadow-sm transition"
                >
                  <Edit3 className="w-3.5 h-3.5" />
                  <span>{t('📝 填入输入框 (Tab 快速跳选填空)')}</span>
                </button>
              </div>
            )}
          </div>
        )}

        {/* 分步步骤清单 (Step-by-Step Task List) */}
        {parsed.plan_steps && parsed.plan_steps.length > 0 && (
          <div className="space-y-2 p-3 rounded-lg bg-surface-100 border border-border">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-foreground">{t('执行步骤清单 (可按需勾选微调)')}</span>
              <span className="text-[10px] text-muted-foreground font-mono">
                {Object.values(stepSelections).filter(Boolean).length}/{parsed.plan_steps.length} {t('已勾选')}
              </span>
            </div>

            <div className="space-y-1.5 mt-2">
              {parsed.plan_steps.map((st) => {
                const isChecked = stepSelections[st.step] !== false;
                const toolLvl = (st.tool_level || 'L1').toUpperCase();
                const lvlColor =
                  toolLvl === 'L0'
                    ? 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20'
                    : toolLvl === 'L2'
                    ? 'bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20'
                    : 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20';

                return (
                  <div
                    key={st.step}
                    onClick={() => handleStepToggle(st.step)}
                    className={`flex items-start space-x-2.5 p-2 rounded-lg border text-left cursor-pointer transition ${
                      isChecked
                        ? 'border-border bg-surface-card text-foreground'
                        : 'border-border/60 bg-surface-200/50 text-muted-foreground opacity-60'
                    }`}
                  >
                    <div className="mt-0.5">
                      {isChecked ? (
                        <CheckSquare className="w-4 h-4 text-emerald-500" />
                      ) : (
                        <Square className="w-4 h-4 text-muted-foreground" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center space-x-2">
                        <span className="font-mono text-xs font-semibold text-foreground">
                          {t('步骤')} {st.step}:
                        </span>
                        <span className="text-xs text-foreground font-medium truncate">
                          {st.title}
                        </span>
                        <span className={`text-[10px] font-mono px-1.5 py-0.2 rounded border ${lvlColor}`}>
                          {toolLvl}
                        </span>
                      </div>
                      {st.description && (
                        <p className="text-[11px] text-muted-foreground mt-0.5">{st.description}</p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* 底部操作区域 */}
        <div className="pt-2 flex flex-col sm:flex-row items-center justify-between gap-3 border-t border-border">
          <span className="text-[11px] text-muted-foreground">
            {hasSubmitted
              ? t('✓ 已提交确认结果，等待 Agent 响应中...')
              : isPlanApproval
              ? t('请确认实施步骤无误后批准启动执行')
              : t('可点击单个选项直发，或在调整后统一提交')}
          </span>

          <div className="flex items-center space-x-2 w-full sm:w-auto justify-end">
            {/* 多题组合提交按钮 */}
            {questions.length > 0 && !isPlanApproval && (
              <button
                type="button"
                disabled={hasSubmitted}
                onClick={handleSubmitForm}
                className="flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs shadow-sm transition disabled:opacity-50"
              >
                <Send className="w-3.5 h-3.5" />
                <span>{t('提交所选决策')}</span>
              </button>
            )}

            {/* 方案批准启动自主执行按钮 */}
            {isPlanApproval && (
              <button
                type="button"
                disabled={hasSubmitted}
                onClick={handleApprovePlanClick}
                className="flex items-center space-x-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs shadow-md transition disabled:opacity-50 group"
              >
                <Rocket className="w-4 h-4 group-hover:scale-110 transition-transform" />
                <span>{t('🚀 批准方案并开始执行')}</span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

