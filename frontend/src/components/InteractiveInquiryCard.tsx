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
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  Lock,
  LayoutList,
  Layers,
  AlertCircle,
} from 'lucide-react';
import {
  InteractiveInquiryPayload,
  InquiryQuestion,
  PlanStepItem,
  ConfirmedFactItem,
  InquirySubmitAction,
} from '@/types';
import { useLanguage } from '@/context/LanguageContext';
import {
  InquiryWizardStep,
  buildInquiryWizardSteps,
  extractConfirmedFactsFromTemplate,
  isWizardStepReady,
  normalizeInquiryPayload,
  parseTemplateFields,
  renderFilledTemplateText,
  resolveLoopGoalHint,
} from '@/utils/inquiryWorkflow';

interface InteractiveInquiryCardProps {
  rawContent: string;
  inquiryKey?: string;
  initialSubmitted?: boolean;
  isExecutionContext?: boolean;
  onMarkSubmitted?: (inquiryKey: string) => void;
  onSendFeedback?: (
    feedbackText: string,
    confirmedFacts?: ConfirmedFactItem[],
    inquiryAction?: InquirySubmitAction,
    loopGoalHint?: string
  ) => void;
  onFillTemplate?: (templateText: string) => void;
  onApprovePlan?: (
    steps: PlanStepItem[],
    feedbackText: string,
    confirmedFacts?: ConfirmedFactItem[],
    loopGoalHint?: string
  ) => void;
  isStreaming?: boolean;
}

export function InteractiveInquiryCard({
  rawContent,
  inquiryKey = '',
  initialSubmitted = false,
  isExecutionContext = true,
  onMarkSubmitted,
  onSendFeedback,
  onFillTemplate,
  onApprovePlan,
  isStreaming = false,
}: InteractiveInquiryCardProps) {
  const { t } = useLanguage();

  // 1. 解析并深度归一化 JSON 协议内容（兼容 XML 包装及变体字段，流式半包阶段返回 null 并渲染骨架）
  const { parsed } = useMemo(() => {
    return { parsed: normalizeInquiryPayload(rawContent) };
  }, [rawContent]);

  const strategyFallbackLabel = t('请选择策略：');
  const questions: InquiryQuestion[] = useMemo(() => {
    if (!parsed) return [];
    if (Array.isArray(parsed.questions)) return parsed.questions;
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

  const fillTemplate = useMemo(() => {
    if (!parsed) return undefined;
    if (parsed.fill_template && parsed.fill_template.template_text) {
      return parsed.fill_template;
    }
    if (parsed.type === 'template' && parsed.template) {
      return {
        label: parsed.title || t('请补充以下填空内容：'),
        template_text: parsed.template,
      };
    }
    return undefined;
  }, [parsed, t]);

  const planSteps = useMemo(() => parsed?.plan_steps || [], [parsed]);
  const templateFields = useMemo(
    () => parseTemplateFields(fillTemplate?.template_text),
    [fillTemplate]
  );

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

  const buildInitialTemplateValues = (tplText?: string) => {
    const initial: Record<string, string> = {};
    for (const field of parseTemplateFields(tplText)) {
      initial[field.normalizedKey] = field.initialValue;
    }
    return initial;
  };

  // NOTE: 所有 Hook 必须在任何条件 return 前无条件调用，保证流式 JSON 闭合时不触发 Hook 顺序错乱
  const hasInitializedRef = useRef(Boolean(parsed));
  const [selectedAnswers, setSelectedAnswers] = useState<Record<string, string | string[]>>(() =>
    buildInitialAnswers(questions)
  );
  const [stepSelections, setStepSelections] = useState<Record<number, boolean>>(() =>
    buildInitialSteps(planSteps)
  );
  const [templateFieldValues, setTemplateFieldValues] = useState<Record<string, string>>(() =>
    buildInitialTemplateValues(fillTemplate?.template_text)
  );
  const [hasSubmitted, setHasSubmitted] = useState(false);
  const [rememberFacts, setRememberFacts] = useState(true);
  const [collapsedQuestions, setCollapsedQuestions] = useState<Record<string, boolean>>({});
  const [stepsCollapsed, setStepsCollapsed] = useState(false);
  const [viewMode, setViewMode] = useState<'wizard' | 'all'>('wizard');
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [confirmedStepIds, setConfirmedStepIds] = useState<Record<string, boolean>>({});
  const [showLockedDetails, setShowLockedDetails] = useState(false);

  useEffect(() => {
    if (!parsed || hasInitializedRef.current) return;
    hasInitializedRef.current = true;
    setSelectedAnswers(buildInitialAnswers(questions));
    setStepSelections(buildInitialSteps(planSteps));
    setTemplateFieldValues(buildInitialTemplateValues(fillTemplate?.template_text));
  }, [parsed, questions, planSteps, fillTemplate]);

  const wizardSteps = useMemo(
    () => buildInquiryWizardSteps(questions, fillTemplate, planSteps),
    [questions, fillTemplate, planSteps]
  );

  // 确保索引始终处于合法区间，防止模型增删步骤后越界
  useEffect(() => {
    if (wizardSteps.length > 0 && currentStepIndex >= wizardSteps.length) {
      setCurrentStepIndex(wizardSteps.length - 1);
    }
  }, [wizardSteps.length, currentStepIndex]);

  // 流式吐字半包阶段展示骨架占位
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

  const isLocked = hasSubmitted || Boolean(initialSubmitted);
  const isPlanApproval = parsed.phase === 'plan_approval' || planSteps.length > 0;
  const title =
    parsed.title ||
    (isPlanApproval ? t('实施方案与执行步骤审批') : t('需求与参数交互确认'));
  const summary = parsed.summary || '';
  const activeStepCount = planSteps.filter((s) => stepSelections[s.step] !== false).length;
  const isMultiStepWizard = wizardSteps.length >= 2;
  const safeStepIndex = Math.min(Math.max(0, currentStepIndex), Math.max(0, wizardSteps.length - 1));
  const currentWizardStep = wizardSteps[safeStepIndex];
  const isLastWizardStep = wizardSteps.length === 0 || safeStepIndex === wizardSteps.length - 1;
  const loopGoalHint = resolveLoopGoalHint(parsed);
  const effectiveInquiryKey = inquiryKey || parsed.inquiry_id || '';

  const templateFacts = extractConfirmedFactsFromTemplate(
    fillTemplate?.template_text,
    templateFieldValues
  );
  const hasFactCandidates =
    questions.some((q) => Boolean(q.fact_key && q.fact_key.trim())) || templateFacts.length > 0;

  const formatOptionFactValue = (val: string, opt?: { value: string; label: string }) => {
    if (!opt) return val;
    if (opt.label && opt.label !== opt.value) {
      return `${opt.label} (${opt.value})`;
    }
    return opt.value;
  };

  const buildConfirmedFacts = (): ConfirmedFactItem[] | undefined => {
    if (!rememberFacts) return undefined;
    const factMap = new Map<string, ConfirmedFactItem>();
    for (const tf of templateFacts) {
      factMap.set(tf.fact_key.toLowerCase().replace(/[\s-]+/g, '_'), tf);
    }

    for (const q of questions) {
      const fk = q.fact_key?.trim();
      if (!fk) continue;
      const ans = selectedAnswers[q.id];
      let valStr = '';
      if (Array.isArray(ans)) {
        const parts = q.options
          .filter((o) => ans.includes(o.value))
          .map((o) => formatOptionFactValue(o.value, o));
        valStr = parts.join('、');
      } else if (ans) {
        const opt = q.options.find((o) => o.value === ans);
        valStr = formatOptionFactValue(String(ans), opt);
      }
      if (valStr.trim()) {
        factMap.set(fk.toLowerCase().replace(/[\s-]+/g, '_'), {
          fact_key: fk,
          fact_value: valStr.trim(),
          category: q.fact_category || 'biz_rule',
          workspace_scope: q.fact_scope || 'workspace',
        });
      }
    }

    const list = Array.from(factMap.values());
    return list.length > 0 ? list : undefined;
  };

  const getQuestionSummary = (q: InquiryQuestion): string => {
    const ans = selectedAnswers[q.id];
    if (Array.isArray(ans)) {
      return q.options
        .filter((o) => ans.includes(o.value))
        .map((o) => o.label)
        .join('、');
    }
    if (ans) {
      const opt = q.options.find((o) => o.value === ans);
      return opt ? opt.label : String(ans);
    }
    return '';
  };

  const getTemplateSummary = (): string => {
    if (!fillTemplate) return '';
    if (templateFields.length === 0) {
      return fillTemplate.template_text.slice(0, 60);
    }
    const parts = templateFields
      .map((f) => {
        const v = (templateFieldValues[f.normalizedKey] || '').trim();
        return v ? `${f.key}=${v}` : '';
      })
      .filter(Boolean);
    return parts.length > 0 ? parts.join('；') : t('已保持默认参数模板');
  };

  const getStepSummaryLabel = (step: InquiryWizardStep): string => {
    if (step.kind === 'question') return getQuestionSummary(step.question) || '-';
    if (step.kind === 'template') return getTemplateSummary();
    return `${activeStepCount}/${planSteps.length} ${t('个步骤已勾选')}`;
  };

  const formatSelectedAnswersLines = (): string[] => {
    const lines: string[] = [];
    questions.forEach((q, idx) => {
      const summaryText = getQuestionSummary(q);
      if (!summaryText) return;
      const prefix = questions.length > 1 ? `[${idx + 1}/${questions.length}] ` : '';
      lines.push(`- ${prefix}${q.label}: ${summaryText}`);
    });
    if (fillTemplate && fillTemplate.template_text) {
      const filled = renderFilledTemplateText(fillTemplate.template_text, templateFieldValues, false);
      if (filled.trim()) {
        lines.push(`- [${t('参数配置')}]: ${filled}`);
      }
    }
    return lines;
  };

  // 单选切换（不触发自动跳步或抢跑发送，由用户明确点击下一步/确认）
  const handleSingleSelect = (questionId: string, val: string) => {
    if (isLocked) return;
    setSelectedAnswers((prev) => ({ ...prev, [questionId]: val }));
  };

  // 多选切换
  const handleMultiToggle = (questionId: string, val: string) => {
    if (isLocked) return;
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
    if (isLocked) return;
    setStepSelections((prev) => ({ ...prev, [stepNo]: prev[stepNo] === false }));
  };

  const handleTemplateFieldChange = (normalizedKey: string, val: string) => {
    if (isLocked) return;
    setTemplateFieldValues((prev) => ({ ...prev, [normalizedKey]: val }));
  };

  const handleSelectAllSteps = () => {
    if (isLocked || planSteps.length === 0) return;
    const next: Record<number, boolean> = {};
    for (const s of planSteps) {
      next[s.step] = true;
    }
    setStepSelections(next);
  };

  const handleInvertSteps = () => {
    if (isLocked || planSteps.length === 0) return;
    const next: Record<number, boolean> = {};
    for (const s of planSteps) {
      next[s.step] = stepSelections[s.step] === false;
    }
    setStepSelections(next);
  };

  const toggleQuestionCollapsed = (qId: string) => {
    setCollapsedQuestions((prev) => ({ ...prev, [qId]: !prev[qId] }));
  };

  const toggleAllQuestions = (collapse: boolean) => {
    const next: Record<string, boolean> = {};
    for (const q of questions) {
      next[q.id] = collapse;
    }
    setCollapsedQuestions(next);
  };

  // 分步向导：确认当前步骤并跳转到下一步（或首个尚未确认的较早步骤）
  const handleConfirmCurrentAndNext = () => {
    if (isLocked || !currentWizardStep) return;
    if (!isWizardStepReady(currentWizardStep, selectedAnswers, planSteps, stepSelections)) {
      return;
    }
    const nextConfirmed = { ...confirmedStepIds, [currentWizardStep.id]: true };
    setConfirmedStepIds(nextConfirmed);
    if (safeStepIndex < wizardSteps.length - 1) {
      setCurrentStepIndex(safeStepIndex + 1);
    }
  };

  // 统一执行/提交入口：仅在全部步骤已确认后触发一次后端请求
  const handleUnifiedSubmit = (action: InquirySubmitAction = 'execute_now') => {
    if (isLocked) return;
    if (planSteps.length > 0 && activeStepCount === 0) return;

    // 校验所有前置题与步骤均满足最小有效状态
    for (const step of wizardSteps) {
      if (!isWizardStepReady(step, selectedAnswers, planSteps, stepSelections)) {
        const targetIdx = wizardSteps.findIndex((s) => s.id === step.id);
        if (targetIdx >= 0) {
          setViewMode('wizard');
          setCurrentStepIndex(targetIdx);
        }
        return;
      }
    }

    const answerLines = formatSelectedAnswersLines();
    const activeSteps = planSteps.filter((s) => stepSelections[s.step] !== false);
    const feedbackLines: string[] = [];

    if (answerLines.length > 0) {
      feedbackLines.push('已确认以下前置配置与选型决策：', ...answerLines, '');
    }

    if (activeSteps.length > 0) {
      feedbackLines.push(
        '✅ 实施方案已通过用户审批，批准执行以下步骤清单：',
        ...activeSteps.map((s) => `${s.step}. ${s.title} [${s.tool_level || 'L1'}]`),
        '请解锁全部工具权限并立即一气呵成执行到底，不再停下来反问确认！'
      );
    } else if (action === 'clarify_only') {
      feedbackLines.push('以上为补充的澄清确认信息，请先据此完善并输出最终实施方案。');
    } else if (isExecutionContext) {
      feedbackLines.push('全部确认完毕，请解锁执行权限并立即一气呵成执行完成，不再重复弹出确认卡片。');
    } else {
      feedbackLines.push('以上决策已全部确认，请据此继续完整解答。');
    }

    const facts = buildConfirmedFacts();
    // 先锁定本地和父级持久化记录，防止在流式回复或轮询期间任何闪回重点
    setHasSubmitted(true);
    if (effectiveInquiryKey && onMarkSubmitted) {
      onMarkSubmitted(effectiveInquiryKey);
    }

    if (activeSteps.length > 0 && action === 'execute_now' && onApprovePlan) {
      onApprovePlan(activeSteps, feedbackLines.join('\n'), facts, loopGoalHint);
    } else if (onSendFeedback) {
      onSendFeedback(feedbackLines.join('\n'), facts, action, loopGoalHint);
    }
  };

  // 计算尚有多少前置步骤未经过点击确认
  const unconfirmedPrevIndices = isMultiStepWizard
    ? wizardSteps
        .slice(0, Math.max(0, wizardSteps.length - 1))
        .map((s, idx) => ({ s, idx }))
        .filter(({ s }) => !confirmedStepIds[s.id])
    : [];
  const confirmedCount = wizardSteps.filter((s) => confirmedStepIds[s.id]).length;
  const isCurrentStepReady = isWizardStepReady(
    currentWizardStep,
    selectedAnswers,
    planSteps,
    stepSelections
  );
  const areAllQuestionsCollapsed =
    questions.length > 1 && questions.every((q) => Boolean(collapsedQuestions[q.id]));

  // 渲染单个题目块
  const renderQuestionBlock = (q: InquiryQuestion, qIdx: number, forceExpanded = false) => {
    const currentVal = selectedAnswers[q.id];
    const isCollapsed = !forceExpanded && Boolean(collapsedQuestions[q.id]);
    const selectedSummary = getQuestionSummary(q);
    return (
      <div
        key={q.id || `q_${qIdx}`}
        className="space-y-2 p-3 rounded-lg bg-surface-100 border border-border"
      >
        <div className="font-medium text-foreground flex items-center justify-between gap-2">
          <div className="flex items-center space-x-2 min-w-0">
            {questions.length > 1 && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 shrink-0">
                {qIdx + 1}/{questions.length}
              </span>
            )}
            <span className="break-words">{q.label}</span>
          </div>
          <div className="flex items-center space-x-2 shrink-0">
            <span className="text-[10px] text-muted-foreground">
              {q.type === 'multi_choice' ? t('多选') : t('单选')}
            </span>
            {!forceExpanded && questions.length > 1 && (
              <button
                type="button"
                onClick={() => toggleQuestionCollapsed(q.id)}
                className="p-0.5 rounded hover:bg-surface-200 text-muted-foreground hover:text-foreground transition"
                title={isCollapsed ? t('全部展开') : t('全部收起')}
              >
                {isCollapsed ? (
                  <ChevronDown className="w-3.5 h-3.5" />
                ) : (
                  <ChevronUp className="w-3.5 h-3.5" />
                )}
              </button>
            )}
          </div>
        </div>

        {isCollapsed ? (
          <div
            onClick={() => toggleQuestionCollapsed(q.id)}
            className="text-[11px] text-muted-foreground bg-surface-card border border-border/70 rounded-md px-2.5 py-1.5 cursor-pointer hover:border-emerald-500/40 transition truncate"
          >
            {t('已收起，当前选择:')}{' '}
            <span className="text-foreground font-medium">{selectedSummary || '-'}</span>
          </div>
        ) : (
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
                    if (isLocked) return;
                    if (q.type === 'single_choice') {
                      handleSingleSelect(q.id, opt.value);
                    } else {
                      handleMultiToggle(q.id, opt.value);
                    }
                  }}
                  className={`p-2.5 rounded-lg border text-left transition flex flex-col justify-between ${
                    isLocked ? 'cursor-not-allowed opacity-75' : 'cursor-pointer'
                  } ${
                    isSelected
                      ? 'border-emerald-500 bg-emerald-500/10 text-foreground'
                      : 'border-border bg-surface-card hover:bg-surface-50 text-muted-foreground hover:text-foreground'
                  }`}
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
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  // 渲染行内填空模板块
  const renderTemplateBlock = () => {
    if (!fillTemplate) return null;
    const previewText = renderFilledTemplateText(
      fillTemplate.template_text,
      templateFieldValues,
      false
    );
    const inputPromptText = renderFilledTemplateText(
      fillTemplate.template_text,
      templateFieldValues,
      true
    );

    return (
      <div className="p-3 rounded-lg bg-surface-100 border border-border space-y-3">
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-foreground">{fillTemplate.label}</span>
          <span className="text-[10px] text-muted-foreground font-mono">
            {templateFields.length > 0
              ? t('可直接在此填写参数后下一步')
              : t('支持 Tab 键跳转填空')}
          </span>
        </div>

        {templateFields.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {templateFields.map((field) => (
              <label
                key={field.normalizedKey}
                className="flex flex-col space-y-1 text-[11px] text-muted-foreground"
              >
                <span className="font-mono font-semibold text-foreground">{field.key}</span>
                <input
                  type="text"
                  disabled={isLocked}
                  value={templateFieldValues[field.normalizedKey] ?? ''}
                  onChange={(e) => handleTemplateFieldChange(field.normalizedKey, e.target.value)}
                  placeholder={field.defaultHint}
                  className="px-2.5 py-1.5 rounded-lg bg-surface-card border border-border focus:border-emerald-500 focus:outline-none text-xs text-foreground placeholder-muted-foreground/70 disabled:opacity-60"
                />
              </label>
            ))}
          </div>
        )}

        <div className="p-2.5 rounded-lg bg-surface-card border border-border font-mono text-xs text-foreground whitespace-pre-wrap select-all">
          {previewText}
        </div>

        {onFillTemplate && !isLocked && (
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => onFillTemplate(inputPromptText)}
              className="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg bg-surface-200 hover:bg-emerald-600 text-muted-foreground hover:text-white font-medium text-[11px] transition"
            >
              <Edit3 className="w-3 h-3" />
              <span>{t('📝 同步填入下方输入框 (Tab 跳选)')}</span>
            </button>
          </div>
        )}
      </div>
    );
  };

  // 渲染实施步骤清单块
  const renderPlanStepsBlock = (forceExpanded = false) => {
    if (planSteps.length === 0) return null;
    const isListCollapsed = !forceExpanded && stepsCollapsed;
    return (
      <div className="space-y-2 p-3 rounded-lg bg-surface-100 border border-border">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-semibold text-foreground">
            {t('执行步骤清单 (可按需勾选微调)')}
          </span>
          <div className="flex items-center space-x-2">
            {planSteps.length >= 2 && !isLocked && (
              <div className="flex items-center space-x-1">
                <button
                  type="button"
                  onClick={handleSelectAllSteps}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-surface-card hover:bg-emerald-500/15 text-muted-foreground hover:text-emerald-600 dark:hover:text-emerald-400 border border-border transition"
                >
                  {t('全选')}
                </button>
                <button
                  type="button"
                  onClick={handleInvertSteps}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-surface-card hover:bg-emerald-500/15 text-muted-foreground hover:text-emerald-600 dark:hover:text-emerald-400 border border-border transition"
                >
                  {t('反选')}
                </button>
              </div>
            )}
            <span className="text-[10px] text-muted-foreground font-mono">
              {activeStepCount}/{planSteps.length} {t('已勾选')}
            </span>
            {!forceExpanded && planSteps.length >= 4 && (
              <button
                type="button"
                onClick={() => setStepsCollapsed((prev) => !prev)}
                className="text-[10px] text-emerald-600 dark:text-emerald-400 hover:underline flex items-center space-x-0.5"
              >
                <span>{isListCollapsed ? t('查看清单') : t('收起清单')}</span>
                {isListCollapsed ? (
                  <ChevronDown className="w-3 h-3" />
                ) : (
                  <ChevronUp className="w-3 h-3" />
                )}
              </button>
            )}
          </div>
        </div>

        {!isListCollapsed && (
          <div className="space-y-1.5 mt-2">
            {planSteps.map((st) => {
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
                  className={`flex items-start space-x-2.5 p-2 rounded-lg border text-left transition ${
                    isLocked ? 'cursor-not-allowed' : 'cursor-pointer'
                  } ${
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
                      <span className="font-mono text-xs font-semibold text-foreground shrink-0">
                        {t('步骤')} {st.step}:
                      </span>
                      <span className="text-xs text-foreground font-medium break-words">
                        {st.title}
                      </span>
                      <span
                        className={`text-[10px] font-mono px-1.5 py-0.2 rounded border shrink-0 ${lvlColor}`}
                      >
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
        )}
      </div>
    );
  };

  // ── 已确认锁定态（含刚点击后等待执行与刷新历史只读回显） ──
  if (isLocked) {
    const isActivelyWaiting = hasSubmitted && !initialSubmitted && isStreaming;
    return (
      <div className="my-4 rounded-xl border border-emerald-500/35 bg-surface-card/90 shadow-xs overflow-hidden select-text">
        <div className="px-4 py-2.5 bg-emerald-500/10 border-b border-emerald-500/20 flex items-center justify-between gap-3">
          <div className="flex items-center space-x-2 min-w-0">
            <Lock className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
            <h4 className="text-xs font-semibold text-foreground truncate">{title}</h4>
          </div>
          <div className="flex items-center space-x-2 shrink-0">
            <span className="text-[11px] px-2 py-0.5 rounded-full font-medium bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30">
              {isActivelyWaiting ? t('⏳ 已提交执行中...') : t('✅ 已确认并提交')}
            </span>
            {wizardSteps.length > 0 && (
              <button
                type="button"
                onClick={() => setShowLockedDetails((prev) => !prev)}
                className="text-[11px] text-muted-foreground hover:text-foreground underline flex items-center space-x-0.5"
              >
                <span>{showLockedDetails ? t('收起详情') : t('查看详情')}</span>
                {showLockedDetails ? (
                  <ChevronUp className="w-3 h-3" />
                ) : (
                  <ChevronDown className="w-3 h-3" />
                )}
              </button>
            )}
          </div>
        </div>

        <div className="px-4 py-2.5 text-xs space-y-2">
          {wizardSteps.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {wizardSteps.map((st, idx) => (
                <span
                  key={st.id}
                  className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-surface-100 border border-border text-[11px] text-muted-foreground max-w-full"
                >
                  <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0" />
                  <span className="font-mono text-[10px] shrink-0">{idx + 1}.</span>
                  <span className="truncate">{st.shortTitle}</span>
                </span>
              ))}
            </div>
          ) : (
            <span className="text-[11px] text-muted-foreground">
              {t('✓ 已完成交互确认，当前卡片处于只读归档状态')}
            </span>
          )}

          {showLockedDetails && (
            <div className="pt-2 space-y-3 border-t border-border/60">
              {questions.map((q, qIdx) => renderQuestionBlock(q, qIdx, true))}
              {renderTemplateBlock()}
              {renderPlanStepsBlock(true)}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="my-4 rounded-xl border border-emerald-500/40 bg-surface-card shadow-sm overflow-hidden select-text transition">
      {/* 顶部标题栏 */}
      <div className="px-4 py-3 bg-surface-50 border-b border-border flex items-center justify-between gap-3">
        <div className="flex items-center space-x-2 min-w-0">
          <div className="p-1 rounded-lg bg-emerald-500/10 text-emerald-500 shrink-0">
            {isPlanApproval ? <Rocket className="w-4 h-4" /> : <Compass className="w-4 h-4" />}
          </div>
          <h4 className="text-xs font-semibold text-foreground truncate">{title}</h4>
        </div>
        <div className="flex items-center space-x-1.5 shrink-0">
          {isMultiStepWizard && (
            <button
              type="button"
              onClick={() => setViewMode((prev) => (prev === 'wizard' ? 'all' : 'wizard'))}
              className="text-[10px] px-2 py-0.5 rounded-md bg-surface-200 hover:bg-emerald-500/15 text-muted-foreground hover:text-emerald-600 dark:hover:text-emerald-400 border border-border transition flex items-center space-x-1"
              title={viewMode === 'wizard' ? t('切换为同屏全部总览') : t('切换为逐项分步引导')}
            >
              {viewMode === 'wizard' ? (
                <>
                  <LayoutList className="w-3 h-3" />
                  <span>{t('全部总览')}</span>
                </>
              ) : (
                <>
                  <Layers className="w-3 h-3" />
                  <span>{t('分步引导')}</span>
                </>
              )}
            </button>
          )}
          {questions.length > 1 && (
            <span className="text-[10px] px-2 py-0.5 rounded-full font-mono bg-surface-200 text-muted-foreground border border-border">
              {questions.length} {t('项确认')}
            </span>
          )}
          {planSteps.length > 0 && (
            <span className="text-[10px] px-2 py-0.5 rounded-full font-mono bg-surface-200 text-muted-foreground border border-border">
              {planSteps.length} {t('个步骤')}
            </span>
          )}
          <span className="text-[11px] px-2 py-0.5 rounded-full font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
            {isPlanApproval ? t('方案审批') : t('需求澄清')}
          </span>
        </div>
      </div>

      <div className="p-4 space-y-4 text-xs">
        {summary && <p className="text-muted-foreground leading-relaxed">{summary}</p>}

        {/* 分步向导步骤导览条 */}
        {isMultiStepWizard && viewMode === 'wizard' && (
          <div className="p-2.5 rounded-lg bg-surface-100/80 border border-border space-y-2">
            <div className="flex items-center justify-between text-[11px]">
              <span className="font-semibold text-foreground">
                {t('分步逐项确认')} · {safeStepIndex + 1} / {wizardSteps.length}
              </span>
              <span className="font-mono text-muted-foreground">
                {t('已确认')} {confirmedCount} / {wizardSteps.length}
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {wizardSteps.map((step, idx) => {
                const isActive = idx === safeStepIndex;
                const isStepConfirmed = Boolean(confirmedStepIds[step.id]);
                const isMissingWarning = isLastWizardStep && idx < safeStepIndex && !isStepConfirmed;
                return (
                  <button
                    key={step.id}
                    type="button"
                    onClick={() => setCurrentStepIndex(idx)}
                    className={`inline-flex items-center space-x-1 px-2 py-1 rounded-md text-[11px] border transition ${
                      isActive
                        ? 'border-emerald-500 bg-emerald-500/15 text-foreground font-semibold shadow-xs'
                        : isStepConfirmed
                        ? 'border-emerald-500/35 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300'
                        : isMissingWarning
                        ? 'border-amber-500/60 bg-amber-500/10 text-amber-700 dark:text-amber-300'
                        : 'border-border bg-surface-card text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {isStepConfirmed ? (
                      <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0" />
                    ) : isMissingWarning ? (
                      <AlertCircle className="w-3 h-3 text-amber-500 shrink-0" />
                    ) : (
                      <span className="font-mono text-[10px] w-3.5 text-center shrink-0">
                        {idx + 1}
                      </span>
                    )}
                    <span className="truncate max-w-[140px]">{step.shortTitle}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* 主体内容区：分步模式聚焦单步，单步或总览模式全量渲染 */}
        {isMultiStepWizard && viewMode === 'wizard' ? (
          <div className="space-y-3">
            {currentWizardStep?.kind === 'question' &&
              renderQuestionBlock(currentWizardStep.question, currentWizardStep.qIndex, true)}
            {currentWizardStep?.kind === 'template' && renderTemplateBlock()}
            {currentWizardStep?.kind === 'plan' && renderPlanStepsBlock(true)}

            {/* 最后一步展示前置确认项汇总，方便统一执行前核对与回跳修改 */}
            {isLastWizardStep && wizardSteps.length > 1 && (
              <div className="p-3 rounded-lg bg-surface-50 border border-border/80 space-y-2">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="font-semibold text-foreground">
                    {t('已确认前置项汇总（全部核对完毕后点击右下角统一执行）')}
                  </span>
                </div>
                <div className="space-y-1">
                  {wizardSteps.slice(0, -1).map((prevStep, idx) => {
                    const isPrevDone = Boolean(confirmedStepIds[prevStep.id]);
                    return (
                      <div
                        key={prevStep.id}
                        onClick={() => setCurrentStepIndex(idx)}
                        className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-md bg-surface-card border border-border/60 hover:border-emerald-500/40 cursor-pointer text-[11px] transition"
                      >
                        <div className="flex items-center space-x-2 min-w-0">
                          {isPrevDone ? (
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                          ) : (
                            <AlertCircle className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                          )}
                          <span className="text-muted-foreground shrink-0">
                            {idx + 1}. {prevStep.shortTitle}:
                          </span>
                          <span className="text-foreground font-medium truncate">
                            {getStepSummaryLabel(prevStep)}
                          </span>
                        </div>
                        <span className="text-[10px] text-emerald-600 dark:text-emerald-400 shrink-0">
                          {isPrevDone ? t('修改') : t('待确认 →')}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            {questions.length > 0 && (
              <div className="space-y-3">
                {questions.length > 1 && (
                  <div className="flex items-center justify-between px-1">
                    <span className="text-[11px] font-semibold text-muted-foreground">
                      {t('待确认事项')} ({questions.length})
                    </span>
                    <button
                      type="button"
                      onClick={() => toggleAllQuestions(!areAllQuestionsCollapsed)}
                      className="text-[11px] text-emerald-600 dark:text-emerald-400 hover:underline flex items-center space-x-1"
                    >
                      {areAllQuestionsCollapsed ? (
                        <>
                          <ChevronDown className="w-3 h-3" />
                          <span>{t('全部展开')}</span>
                        </>
                      ) : (
                        <>
                          <ChevronUp className="w-3 h-3" />
                          <span>{t('全部收起')}</span>
                        </>
                      )}
                    </button>
                  </div>
                )}
                {questions.map((q, qIdx) => renderQuestionBlock(q, qIdx, false))}
              </div>
            )}
            {renderTemplateBlock()}
            {renderPlanStepsBlock(false)}
          </div>
        )}

        {/* 底部操作区域 */}
        <div className="pt-2 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 border-t border-border">
          <div className="flex flex-col gap-1.5">
            {isMultiStepWizard && viewMode === 'wizard' ? (
              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  disabled={safeStepIndex === 0}
                  onClick={() => setCurrentStepIndex(Math.max(0, safeStepIndex - 1))}
                  className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-surface-100 hover:bg-surface-200 border border-border text-[11px] text-foreground disabled:opacity-40 disabled:cursor-not-allowed transition"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                  <span>{t('上一步')}</span>
                </button>
                <span className="text-[11px] text-muted-foreground">
                  {!isLastWizardStep
                    ? t('请确认本项后点击右侧按钮继续下一项')
                    : unconfirmedPrevIndices.length > 0
                    ? t('尚有前置项未点确认，请先逐项确认后再执行')
                    : isPlanApproval && activeStepCount === 0
                    ? t('请至少保留勾选 1 个执行步骤')
                    : t('所有步骤已就绪，点击右侧按钮统一开始执行')}
                </span>
              </div>
            ) : (
              <span className="text-[11px] text-muted-foreground">
                {isPlanApproval && activeStepCount === 0 && planSteps.length > 0
                  ? t('请至少保留勾选 1 个执行步骤')
                  : questions.length > 1
                  ? t('请逐项核对以上全部待确认项后统一开始执行')
                  : isPlanApproval
                  ? t('请确认实施步骤无误后批准启动执行')
                  : t('请确认选项与参数后统一提交执行')}
              </span>
            )}

            {hasFactCandidates && (
              <label className="flex items-center space-x-1.5 text-[11px] select-none cursor-pointer text-emerald-700 dark:text-emerald-300">
                <input
                  type="checkbox"
                  checked={rememberFacts}
                  onChange={(e) => setRememberFacts(e.target.checked)}
                  className="rounded border-emerald-500/40 text-emerald-600 focus:ring-emerald-500"
                />
                <span>{t('记住本次配置偏好到当前工作区')}</span>
              </label>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 w-full sm:w-auto justify-end">
            {isMultiStepWizard && viewMode === 'wizard' ? (
              !isLastWizardStep ? (
                <button
                  type="button"
                  disabled={!isCurrentStepReady}
                  onClick={handleConfirmCurrentAndNext}
                  className="flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs shadow-sm transition disabled:opacity-50"
                >
                  <span>
                    {t('确认本项，继续下一项')} ({safeStepIndex + 1}/{wizardSteps.length})
                  </span>
                  <ChevronRight className="w-4 h-4" />
                </button>
              ) : unconfirmedPrevIndices.length > 0 ? (
                <button
                  type="button"
                  onClick={() => setCurrentStepIndex(unconfirmedPrevIndices[0].idx)}
                  className="flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-amber-600 hover:bg-amber-500 text-white font-semibold text-xs shadow-sm transition"
                >
                  <AlertCircle className="w-4 h-4" />
                  <span>
                    {t('前往未确认项')} #{unconfirmedPrevIndices[0].idx + 1} ({unconfirmedPrevIndices.length}{' '}
                    {t('项待确认')})
                  </span>
                </button>
              ) : (
                <>
                  {isExecutionContext && !isPlanApproval && planSteps.length === 0 && (
                    <button
                      type="button"
                      disabled={!isCurrentStepReady}
                      onClick={() => handleUnifiedSubmit('clarify_only')}
                      className="flex items-center space-x-1.5 px-3 py-2 rounded-xl bg-surface-200 hover:bg-surface-300 text-foreground font-medium text-xs border border-border transition disabled:opacity-50"
                    >
                      <Send className="w-3.5 h-3.5" />
                      <span>{t('仅提交继续细化方案')}</span>
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={!isCurrentStepReady || (planSteps.length > 0 && activeStepCount === 0)}
                    onClick={() => handleUnifiedSubmit('execute_now')}
                    className="flex items-center space-x-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs shadow-md transition disabled:opacity-50 group"
                  >
                    <Rocket className="w-4 h-4 group-hover:scale-110 transition-transform" />
                    <span>
                      {isExecutionContext
                        ? t('🚀 全部确认完毕，统一开始执行')
                        : t('✓ 全部确认完毕，统一提交解答')}
                    </span>
                  </button>
                </>
              )
            ) : (
              <>
                {isExecutionContext && !isPlanApproval && planSteps.length === 0 && (
                  <button
                    type="button"
                    disabled={!isCurrentStepReady}
                    onClick={() => handleUnifiedSubmit('clarify_only')}
                    className="flex items-center space-x-1.5 px-3 py-2 rounded-xl bg-surface-200 hover:bg-surface-300 text-foreground font-medium text-xs border border-border transition disabled:opacity-50"
                  >
                    <Send className="w-3.5 h-3.5" />
                    <span>{t('仅提交继续细化方案')}</span>
                  </button>
                )}
                <button
                  type="button"
                  disabled={
                    (planSteps.length > 0 && activeStepCount === 0) ||
                    wizardSteps.some(
                      (s) => !isWizardStepReady(s, selectedAnswers, planSteps, stepSelections)
                    )
                  }
                  onClick={() => handleUnifiedSubmit('execute_now')}
                  className="flex items-center space-x-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs shadow-md transition disabled:opacity-50 group"
                >
                  <Rocket className="w-4 h-4 group-hover:scale-110 transition-transform" />
                  <span>
                    {isExecutionContext
                      ? wizardSteps.length > 1
                        ? t('🚀 全部确认完毕，统一开始执行')
                        : isPlanApproval
                        ? t('🚀 批准方案并开始执行')
                        : t('🚀 确认并立即执行')
                      : t('✓ 提交所选决策')}
                  </span>
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
