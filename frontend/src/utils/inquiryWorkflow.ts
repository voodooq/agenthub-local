import {
  ConfirmedFactItem,
  FillTemplateInfo,
  InquiryOption,
  InquiryQuestion,
  InteractiveInquiryPayload,
  Message,
  PlanStepItem,
} from '@/types';

const AGENTHUB_FILE_LINK_STRIP_RE = /\[([^\[\]\r\n]+)\]\(#agenthub-file=[^)\s]+\)/g;

/**
 * 剥离偶发混入文本/JSON 字符串中的 [path](#agenthub-file=...) 芯片语法，还原干净文本路径。
 */
export function stripAgenthubFileLinks(text: string): string {
  if (!text || !text.includes('#agenthub-file=')) return text || '';
  return text.replace(AGENTHUB_FILE_LINK_STRIP_RE, '$1');
}

function unwrapCodeFenceIfNeeded(raw: string): string {
  let trimmed = (raw || '').trim();
  if (trimmed.startsWith('```')) {
    trimmed = trimmed.replace(/^```[^\r\n]*\r?\n?/, '');
    trimmed = trimmed.replace(/\r?\n?```$/, '').trim();
  }
  return trimmed;
}

function extractBalancedJsonObjects(text: string): string[] {
  const results: string[] = [];
  let depth = 0;
  let start = -1;
  let inString = false;
  let escapeNext = false;

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inString) {
      if (escapeNext) {
        escapeNext = false;
      } else if (ch === '\\') {
        escapeNext = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
    } else if (ch === '{') {
      if (depth === 0) start = i;
      depth += 1;
    } else if (ch === '}') {
      if (depth > 0) {
        depth -= 1;
        if (depth === 0 && start !== -1) {
          results.push(text.slice(start, i + 1));
          start = -1;
        }
      }
    }
  }
  return results;
}

function tryParseJsonObject(raw: string): Record<string, any> | null {
  const cleaned = stripAgenthubFileLinks(unwrapCodeFenceIfNeeded(raw));
  if (!cleaned) return null;
  try {
    const direct = JSON.parse(cleaned);
    if (direct && typeof direct === 'object' && !Array.isArray(direct)) {
      return direct as Record<string, any>;
    }
  } catch {
    // 尝试提取内嵌平衡 JSON 对象
  }
  const candidates = extractBalancedJsonObjects(cleaned);
  for (const cand of candidates) {
    try {
      const obj = JSON.parse(cand);
      if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
        return obj as Record<string, any>;
      }
    } catch {
      // 忽略无效片段
    }
  }
  return null;
}

function isStrictInquiryObject(obj: Record<string, any> | null): boolean {
  if (!obj) return false;
  const hasShape =
    'questions' in obj || 'plan_steps' in obj || 'fill_template' in obj || 'phase' in obj;
  return Boolean(hasShape && 'inquiry_id' in obj);
}

/**
 * 将普通 Markdown 文本（非围栏代码块外段）中的 XML/方括号 interactive_inquiry 标签
 * 安全替换为标准 ```interactive_inquiry 代码块，同时严格避开行内反引号代码区间。
 */
function replaceInquiryTagsInProse(prose: string): string {
  if (!prose || !/(?:interactive_inquiry)/i.test(prose)) {
    return prose;
  }

  const out: string[] = [];
  let i = 0;
  const n = prose.length;

  while (i < n) {
    // 1. 跳过行内反引号 `...` / ``...``
    if (prose[i] === '`') {
      let ticks = 0;
      while (i + ticks < n && prose[i + ticks] === '`') ticks += 1;
      const closer = '`'.repeat(ticks);
      const closePos = prose.indexOf(closer, i + ticks);
      if (closePos !== -1) {
        out.push(prose.slice(i, closePos + ticks));
        i = closePos + ticks;
        continue;
      }
    }

    // 2. 尝试匹配 XML <interactive_inquiry ...> 或方括号 [interactive_inquiry ...]
    if (prose[i] === '<' || prose[i] === '[') {
      const rest = prose.slice(i);
      const closedXml = /^<interactive_inquiry(?:\s+[^>]*)?>([\s\S]*?)<\/interactive_inquiry>/i.exec(rest);
      const closedBracket =
        !closedXml
          ? /^\[interactive_inquiry(?:\s+[^\]]*)?\]([\s\S]*?)\[\/interactive_inquiry\]/i.exec(rest)
          : null;
      const closedMatch = closedXml || closedBracket;

      if (closedMatch) {
        const innerClean = stripAgenthubFileLinks(unwrapCodeFenceIfNeeded(closedMatch[1] || ''));
        out.push(`\n\n\`\`\`interactive_inquiry\n${innerClean}\n\`\`\`\n\n`);
        i += closedMatch[0].length;
        continue;
      }

      // 流式未闭合半包标签：仅当标签内已开始吐出 JSON 对象 '{' 时切换为未闭合围栏以显示骨架
      const streamingXml = /^<interactive_inquiry(?:\s+[^>]*)?>\s*(\{[\s\S]*)$/i.exec(rest);
      const streamingBracket =
        !streamingXml
          ? /^\[interactive_inquiry(?:\s+[^\]]*)?\]\s*(\{[\s\S]*)$/i.exec(rest)
          : null;
      const streamingMatch = streamingXml || streamingBracket;

      if (streamingMatch) {
        const innerClean = stripAgenthubFileLinks(streamingMatch[1] || '');
        out.push(`\n\n\`\`\`interactive_inquiry\n${innerClean}`);
        i = n;
        break;
      }
    }

    out.push(prose[i]);
    i += 1;
  }

  return out.join('');
}

interface MarkdownSegment {
  isFence: boolean;
  lang: string;
  content: string;
}

function splitMarkdownByFences(markdown: string): MarkdownSegment[] {
  const segments: MarkdownSegment[] = [];
  const n = markdown.length;
  let i = 0;
  let proseStart = 0;

  while (i < n) {
    const atLineStart = i === 0 || markdown[i - 1] === '\n';
    if (atLineStart) {
      let ws = i;
      while (ws < n && (markdown[ws] === ' ' || markdown[ws] === '\t') && ws - i <= 3) {
        ws += 1;
      }
      const fenceChar = markdown[ws];
      if ((fenceChar === '`' || fenceChar === '~') && markdown.startsWith(fenceChar.repeat(3), ws)) {
        let fenceLen = 0;
        while (ws + fenceLen < n && markdown[ws + fenceLen] === fenceChar) fenceLen += 1;
        const lineEnd = markdown.indexOf('\n', ws + fenceLen);
        const headerRest =
          lineEnd === -1 ? markdown.slice(ws + fenceLen) : markdown.slice(ws + fenceLen, lineEnd);

        // 反引号围栏的信息字符串不得再包含反引号（行内代码情况）
        if (fenceChar === '~' || !headerRest.includes('`')) {
          if (i > proseStart) {
            segments.push({ isFence: false, lang: '', content: markdown.slice(proseStart, i) });
          }
          const lang = headerRest.trim();
          const closeMarker = fenceChar.repeat(fenceLen);
          let bodyStart = lineEnd === -1 ? n : lineEnd + 1;
          let closePos = -1;
          let afterClosePos = n;

          let scan = bodyStart;
          while (scan < n) {
            const nextNl = markdown.indexOf('\n', scan);
            const curLine = nextNl === -1 ? markdown.slice(scan) : markdown.slice(scan, nextNl);
            const trimmedLine = curLine.replace(/\r$/, '').trim();
            if (trimmedLine.startsWith(closeMarker) && new RegExp(`^\\${fenceChar}{${fenceLen},}$`).test(trimmedLine)) {
              closePos = scan;
              afterClosePos = nextNl === -1 ? n : nextNl + 1;
              break;
            }
            if (nextNl === -1) break;
            scan = nextNl + 1;
          }

          if (closePos !== -1) {
            const body = markdown.slice(bodyStart, closePos).replace(/\r?\n$/, '');
            segments.push({ isFence: true, lang, content: body });
            i = afterClosePos;
            proseStart = i;
            continue;
          } else {
            // 未闭合围栏（常见于流式输出结尾）
            const body = lineEnd === -1 ? '' : markdown.slice(bodyStart);
            segments.push({ isFence: true, lang: `${lang}__UNCLOSED__`, content: body });
            i = n;
            proseStart = n;
            break;
          }
        }
      }
    }
    i += 1;
  }

  if (proseStart < n) {
    segments.push({ isFence: false, lang: '', content: markdown.slice(proseStart) });
  }
  return segments;
}

/**
 * 将模型输出及历史消息中的 <interactive_inquiry>...</interactive_inquiry>、
 * [interactive_inquiry] 及符合交互卡特征的 ```json 代码块统一归一化为标准 ```interactive_inquiry 围栏块。
 */
export function normalizeInteractiveInquiryMarkdown(rawMarkdown: string): string {
  if (!rawMarkdown) return '';

  const segments = splitMarkdownByFences(rawMarkdown);
  const rendered: string[] = [];

  for (const seg of segments) {
    if (!seg.isFence) {
      rendered.push(replaceInquiryTagsInProse(seg.content));
      continue;
    }

    const isUnclosed = seg.lang.endsWith('__UNCLOSED__');
    const rawLang = isUnclosed ? seg.lang.slice(0, -'__UNCLOSED__'.length) : seg.lang;
    const langLower = rawLang.trim().toLowerCase();

    if (langLower === 'interactive_inquiry') {
      const cleanedBody = stripAgenthubFileLinks(seg.content);
      if (isUnclosed) {
        rendered.push(`\`\`\`interactive_inquiry\n${cleanedBody}`);
      } else {
        rendered.push(`\`\`\`interactive_inquiry\n${cleanedBody}\n\`\`\`\n`);
      }
      continue;
    }

    if (!isUnclosed && (langLower === '' || langLower === 'json')) {
      const parsedObj = tryParseJsonObject(seg.content);
      if (isStrictInquiryObject(parsedObj)) {
        const cleanedJson = stripAgenthubFileLinks(seg.content.trim());
        rendered.push(`\`\`\`interactive_inquiry\n${cleanedJson}\n\`\`\`\n`);
        continue;
      }
    }

    // 普通代码块原样保留，绝不改写其中的任何演示片段
    if (isUnclosed) {
      rendered.push(`\`\`\`${rawLang}\n${seg.content}`);
    } else {
      rendered.push(`\`\`\`${rawLang}\n${seg.content}\n\`\`\`\n`);
    }
  }

  return rendered.join('').replace(/\n{3,}/g, '\n\n').trim();
}

/**
 * 精准检测消息文本中是否包含真实的交互确认卡区块（兼容 XML/方括号/标准围栏，排除普通说明文字）。
 */
export function hasInteractiveInquiryBlock(rawMarkdown: string): boolean {
  if (!rawMarkdown) return false;
  const normalized = normalizeInteractiveInquiryMarkdown(rawMarkdown);
  const segments = splitMarkdownByFences(normalized);
  return segments.some((seg) => {
    if (!seg.isFence) return false;
    const rawLang = seg.lang.endsWith('__UNCLOSED__')
      ? seg.lang.slice(0, -'__UNCLOSED__'.length)
      : seg.lang;
    return rawLang.trim().toLowerCase() === 'interactive_inquiry';
  });
}

function normalizeInquiryOptions(rawOptions: unknown): InquiryOption[] {
  if (!Array.isArray(rawOptions)) return [];
  const options: InquiryOption[] = [];
  const seenValues = new Set<string>();

  rawOptions.forEach((rawOpt, optIdx) => {
    let val = '';
    let label = '';
    let desc: string | undefined;

    if (typeof rawOpt === 'string') {
      const cleanedStr = stripAgenthubFileLinks(rawOpt).trim();
      if (!cleanedStr) return;
      const prefixMatch = /^([A-Za-z]|\d+)[.、:：)\]]\s*(.+)$/.exec(cleanedStr);
      val = prefixMatch ? prefixMatch[1].trim() : `opt_${optIdx + 1}`;
      label = cleanedStr;
    } else if (rawOpt && typeof rawOpt === 'object') {
      const o = rawOpt as Record<string, any>;
      label = stripAgenthubFileLinks(
        String(o.label ?? o.title ?? o.text ?? o.name ?? o.value ?? `选项 ${optIdx + 1}`)
      ).trim();
      val = String(o.value ?? o.id ?? o.key ?? (label || `opt_${optIdx + 1}`)).trim();
      const rawDesc = o.desc ?? o.description ?? o.hint;
      if (rawDesc !== undefined && rawDesc !== null && String(rawDesc).trim()) {
        desc = stripAgenthubFileLinks(String(rawDesc)).trim();
      }
    } else {
      return;
    }

    if (!val) val = `opt_${optIdx + 1}`;
    let uniqueVal = val;
    if (seenValues.has(uniqueVal)) {
      uniqueVal = `${val}_${optIdx + 1}`;
    }
    seenValues.add(uniqueVal);

    options.push({
      value: uniqueVal,
      label: label || uniqueVal,
      ...(desc ? { desc } : {}),
    });
  });

  return options;
}

/**
 * 将模型生成的各类交互卡 Schema 变体（如 question_id/question/缺失type/纯字符串 options）
 * 深度归一化为前端组件严格期望的 InteractiveInquiryPayload 结构。
 */
export function normalizeInquiryPayload(rawContent: string): InteractiveInquiryPayload | null {
  const rawObj = tryParseJsonObject(rawContent);
  if (!rawObj) return null;

  const normalized: InteractiveInquiryPayload = {
    inquiry_id: rawObj.inquiry_id ? String(rawObj.inquiry_id) : undefined,
    phase:
      rawObj.phase === 'clarification' || rawObj.phase === 'plan_approval'
        ? rawObj.phase
        : undefined,
    title: rawObj.title ? stripAgenthubFileLinks(String(rawObj.title)).trim() : undefined,
    summary: rawObj.summary ? stripAgenthubFileLinks(String(rawObj.summary)).trim() : undefined,
    type: rawObj.type === 'choices' || rawObj.type === 'template' ? rawObj.type : undefined,
    question: rawObj.question ? stripAgenthubFileLinks(String(rawObj.question)).trim() : undefined,
    template: rawObj.template ? stripAgenthubFileLinks(String(rawObj.template)) : undefined,
  };

  if (Array.isArray(rawObj.questions)) {
    const normQuestions: InquiryQuestion[] = [];
    const seenQuestionIds = new Set<string>();
    rawObj.questions.forEach((rawQ: unknown, qIdx: number) => {
      if (!rawQ || typeof rawQ !== 'object') return;
      const q = rawQ as Record<string, any>;
      const baseQId = String(q.id ?? q.question_id ?? q.key ?? q.name ?? `q_${qIdx + 1}`).trim() || `q_${qIdx + 1}`;
      const qId = seenQuestionIds.has(baseQId) ? `${baseQId}_${qIdx + 1}` : baseQId;
      seenQuestionIds.add(qId);

      const qLabel =
        stripAgenthubFileLinks(
          String(q.label ?? q.question ?? q.title ?? q.prompt ?? q.text ?? `确认项 ${qIdx + 1}`)
        ).trim() || `确认项 ${qIdx + 1}`;
      const rawType = String(q.type ?? '').trim().toLowerCase();
      const isMulti =
        rawType === 'multi_choice' ||
        rawType === 'multiple' ||
        rawType === 'multi_select' ||
        rawType === 'checkbox' ||
        q.multi_select === true ||
        q.multiple === true;

      const normOpts = normalizeInquiryOptions(q.options);
      const validOptValues = new Set(normOpts.map((o) => o.value));
      const labelToOptValue = new Map(normOpts.map((o) => [o.label, o.value]));

      const mapRawChoiceToOptValue = (rawItem: unknown): string | null => {
        if (typeof rawItem !== 'string' && typeof rawItem !== 'number') return null;
        const s = String(rawItem).trim();
        if (!s) return null;
        if (validOptValues.has(s)) return s;
        const byLabel = labelToOptValue.get(s);
        return byLabel ?? null;
      };

      // 严格按题型归一化 default，防止模型输出 null / 字符串 / 对象导致多选点击时崩溃
      let normalizedDefault: string | string[] | undefined;
      if (q.default !== undefined && q.default !== null) {
        if (isMulti) {
          if (Array.isArray(q.default)) {
            const matched = q.default
              .map(mapRawChoiceToOptValue)
              .filter((v): v is string => Boolean(v));
            normalizedDefault = matched;
          } else if (typeof q.default === 'string' && q.default.trim()) {
            const parts = q.default
              .split(/[,，、|]+/)
              .map((part) => mapRawChoiceToOptValue(part))
              .filter((v): v is string => Boolean(v));
            if (parts.length > 0) {
              normalizedDefault = parts;
            }
          }
        } else {
          const singleCandidate = Array.isArray(q.default) ? q.default[0] : q.default;
          const matchedSingle = mapRawChoiceToOptValue(singleCandidate);
          if (matchedSingle) {
            normalizedDefault = matchedSingle;
          }
        }
      }

      normQuestions.push({
        id: qId,
        type: isMulti ? 'multi_choice' : 'single_choice',
        label: qLabel,
        options: normOpts,
        ...(normalizedDefault !== undefined ? { default: normalizedDefault } : {}),
        ...(q.fact_key ? { fact_key: String(q.fact_key) } : {}),
        ...(q.fact_category ? { fact_category: q.fact_category } : {}),
        ...(q.fact_scope ? { fact_scope: q.fact_scope } : {}),
      });
    });
    normalized.questions = normQuestions;
  }

  if (rawObj.options !== undefined) {
    normalized.options = normalizeInquiryOptions(rawObj.options);
  }

  if (rawObj.fill_template && typeof rawObj.fill_template === 'object') {
    const ft = rawObj.fill_template as Record<string, any>;
    const tmplText = stripAgenthubFileLinks(String(ft.template_text ?? ft.text ?? '')).trim();
    if (tmplText) {
      normalized.fill_template = {
        label: stripAgenthubFileLinks(String(ft.label ?? '请补充以下参数：')).trim(),
        template_text: tmplText,
      };
    }
  }

  if (Array.isArray(rawObj.plan_steps)) {
    const normSteps: PlanStepItem[] = [];
    rawObj.plan_steps.forEach((rawStep: unknown, idx: number) => {
      if (typeof rawStep === 'string') {
        const sTitle = stripAgenthubFileLinks(rawStep).trim();
        if (sTitle) {
          normSteps.push({ step: idx + 1, title: sTitle, selected: true });
        }
        return;
      }
      if (rawStep && typeof rawStep === 'object') {
        const s = rawStep as Record<string, any>;
        const stepNum = Number(s.step ?? idx + 1);
        const sTitle = stripAgenthubFileLinks(
          String(s.title ?? s.name ?? s.label ?? s.description ?? `步骤 ${idx + 1}`)
        ).trim();
        normSteps.push({
          step: Number.isFinite(stepNum) && stepNum > 0 ? stepNum : idx + 1,
          title: sTitle,
          ...(s.tool_level ? { tool_level: String(s.tool_level) } : {}),
          ...(s.description
            ? { description: stripAgenthubFileLinks(String(s.description)).trim() }
            : {}),
          selected: s.selected !== false,
        });
      }
    });
    normalized.plan_steps = normSteps;
  }

  return normalized;
}

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

