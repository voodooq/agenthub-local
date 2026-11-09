import {
  ConfirmedFactItem,
  FillTemplateInfo,
  InquiryQuestion,
  InteractiveInquiryPayload,
  Message,
  PlanStepItem,
} from '@/types';

export const PLACEHOLDER_VALUE_RE =
  /^(默认值|请输入|请填写|待填写|待补充|请补充|填写.*|placeholder|example|todo|tbd|xxx|\.\.\.)$/i;

export interface TemplateFieldItem {
  key: string;
  normalizedKey: string;
  defaultHint: string;
  initialValue: string;
}

export type InquiryWizardStep =
  | {
      kind: 'question';
      id: string;
      shortTitle: string;
      question: InquiryQuestion;
      qIndex: number;
    }
  | {
      kind: 'template';
      id: string;
      shortTitle: string;
    }
  | {
      kind: 'plan';
      id: string;
      shortTitle: string;
    };

/**
 * 解析模板文本中的 {{KEY:HINT}} 或 {{KEY}} 占位符清单，按参数名去重保留顺序。
 */
export function parseTemplateFields(templateText?: string): TemplateFieldItem[] {
  if (!templateText) return [];
  const fields: TemplateFieldItem[] = [];
  const seen = new Set<string>();
  const regex = /\{\{([^{}:]+)(?::([^{}]*))?\}\}/g;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(templateText)) !== null) {
    const rawKey = match[1].trim();
    const rawHint = (match[2] ?? '').trim();
    if (!rawKey) continue;
    const normalizedKey = rawKey.toLowerCase().replace(/[\s-]+/g, '_');
    if (seen.has(normalizedKey)) continue;
    seen.add(normalizedKey);
    const isPlaceholderHint = !rawHint || PLACEHOLDER_VALUE_RE.test(rawHint);
    fields.push({
      key: rawKey,
      normalizedKey,
      defaultHint: rawHint || '请输入',
      initialValue: isPlaceholderHint ? '' : rawHint,
    });
  }
  return fields;
}

/**
 * 构建用于向后提取事实与填充展示的规范化模板字符串。
 * 对保留非空值的占位符输出为最终值或 {{KEY:VAL}} 形式。
 */
export function renderFilledTemplateText(
  templateText: string,
  fieldValues: Record<string, string>,
  keepTokenSyntax = false
): string {
  if (!templateText) return '';
  return templateText.replace(/\{\{([^{}:]+)(?::([^{}]*))?\}\}/g, (_full, rawKey: string, rawHint?: string) => {
    const cleanKey = rawKey.trim();
    const normalizedKey = cleanKey.toLowerCase().replace(/[\s-]+/g, '_');
    const edited = (fieldValues[normalizedKey] ?? '').trim();
    const fallbackHint = (rawHint ?? '').trim();
    if (keepTokenSyntax) {
      const effectiveVal = edited || fallbackHint || '待补充';
      return `{{${cleanKey}:${effectiveVal}}}`;
    }
    if (edited) return edited;
    if (fallbackHint && !PLACEHOLDER_VALUE_RE.test(fallbackHint)) return fallbackHint;
    return `{{${cleanKey}:${fallbackHint || '待补充'}}}`;
  });
}

/**
 * 从占位符语法或用户填写后的字段映射中抽取可持久化确定性事实。
 */
export function extractConfirmedFactsFromTemplate(
  templateText?: string,
  fieldValues?: Record<string, string>
): ConfirmedFactItem[] {
  if (!templateText) return [];
  const fields = parseTemplateFields(templateText);
  const results: ConfirmedFactItem[] = [];
  for (const field of fields) {
    const val = (
      fieldValues !== undefined
        ? fieldValues[field.normalizedKey] ?? ''
        : field.initialValue
    ).trim();
    if (!val || PLACEHOLDER_VALUE_RE.test(val)) continue;
    results.push({
      fact_key: field.key,
      fact_value: val,
      category: 'biz_rule',
      workspace_scope: 'workspace',
    });
  }
  return results;
}

/**
 * 根据卡片载荷组装严格有序的交互确认步骤清单。
 */
export function buildInquiryWizardSteps(
  questions: InquiryQuestion[],
  fillTemplate: FillTemplateInfo | undefined,
  planSteps: PlanStepItem[]
): InquiryWizardStep[] {
  const steps: InquiryWizardStep[] = [];
  questions.forEach((q, idx) => {
    const titleText = q.label?.trim() || `确认项 ${idx + 1}`;
    steps.push({
      kind: 'question',
      id: `q_${q.id || idx}_${idx}`,
      shortTitle: titleText.length > 18 ? `${titleText.slice(0, 18)}…` : titleText,
      question: q,
      qIndex: idx,
    });
  });

  if (fillTemplate && fillTemplate.template_text) {
    const tmplLabel = fillTemplate.label?.trim() || '参数补充';
    steps.push({
      kind: 'template',
      id: 'template_fields',
      shortTitle: tmplLabel.length > 18 ? `${tmplLabel.slice(0, 18)}…` : tmplLabel,
    });
  }

  if (planSteps.length > 0) {
    steps.push({
      kind: 'plan',
      id: 'plan_steps_review',
      shortTitle: `执行步骤 (${planSteps.length})`,
    });
  }

  return steps;
}

/**
 * 判断单个向导步骤当前是否满足可确认提交的最小条件。
 */
export function isWizardStepReady(
  step: InquiryWizardStep | undefined,
  selectedAnswers: Record<string, string | string[]>,
  planSteps: PlanStepItem[],
  stepSelections: Record<number, boolean>
): boolean {
  if (!step) return true;
  if (step.kind === 'question') {
    const q = step.question;
    if (!q.options || q.options.length === 0) return true;
    const val = selectedAnswers[q.id];
    if (q.type === 'multi_choice') {
      return Array.isArray(val) && val.length > 0;
    }
    return Boolean(typeof val === 'string' && val.trim());
  }
  if (step.kind === 'plan') {
    if (planSteps.length === 0) return true;
    return planSteps.some((s) => stepSelections[s.step] !== false);
  }
  return true;
}

/**
 * 为消息流内含 interactive_inquiry 的 AI 消息计算稳定跨轮次唯一标识，防止模型复用通用 inquiry_id 误锁卡片。
 */
export function resolveMessageInquiryKey(
  msgContent: string,
  msgId: number | undefined,
  msgIndex: number
): string {
  const idMatch = /"inquiry_id"\s*:\s*"([^"]+)"/.exec(msgContent || '');
  const rawInqId = idMatch?.[1]?.trim();
  const basePos = msgId !== undefined ? `id_${msgId}` : `idx_${msgIndex}`;
  return rawInqId ? `${basePos}__${rawInqId}` : basePos;
}

/**
 * 从交互卡片原始 JSON 解析并生成供顶部 LoopHUD 使用的简洁目标提示。
 */
export function resolveLoopGoalHint(parsed: InteractiveInquiryPayload | null): string {
  if (!parsed) return '';
  const title = (parsed.title || '').trim();
  if (title) return title.slice(0, 80);
  const summary = (parsed.summary || '').trim();
  if (summary) return summary.slice(0, 80);
  return '';
}

/**
 * 结合消息时序单调性（后续是否存在任意 user 回复）与本地已提交缓存，永久锁定已完成的历史卡片。
 */
export function computeInquiryCardLocked(
  messages: Message[],
  msgIndex: number,
  inquiryKey: string,
  submittedKeys: Set<string>
): boolean {
  if (submittedKeys.has(inquiryKey)) {
    return true;
  }
  for (let i = msgIndex + 1; i < messages.length; i++) {
    if (messages[i]?.role === 'user') {
      return true;
    }
  }
  return false;
}

