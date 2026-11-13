/**
 * 前端助手输出协议清洗与思考片段拆解工具
 * 统一用于流式 SSE 增量渲染与历史数据库消息加载，彻底消除双轨正则分叉与协议源码泄露。
 */
import type { Message } from '@/types';

export interface SanitizeAssistantOptions {
  isStreaming?: boolean;
}

export interface SanitizedAssistantContent {
  cleanContent: string;
  thinkingParts: string[];
  strippedToolNames: string[];
  hadToolMarkup: boolean;
}

const DIRECT_TOOL_TAG_NAMES = [
  'execute_local_command',
  'write_local_file',
  'read_local_file',
  'read_tool_ref',
  'web_fetch',
  'ui_action',
  'update_working_memory',
  'load_skill_detail',
  'search_memory_history',
  'save_long_term_memory',
  'search_skill_knowledge',
  'run_skill_tool',
];

const DIRECT_TOOL_PATTERN = new RegExp(
  `<(?:${DIRECT_TOOL_TAG_NAMES.join('|')})\\b[^>]*?(?:\\/>|>[\\s\\S]*?<\\/(?:${DIRECT_TOOL_TAG_NAMES.join('|')})>)`,
  'gi'
);

const DIRECT_TOOL_UNCLOSED_TAIL_PATTERN = new RegExp(
  `<(?:${DIRECT_TOOL_TAG_NAMES.join('|')})\\b(?![\\s\\S]*?<\\/(?:${DIRECT_TOOL_TAG_NAMES.join('|')})>)[\\s\\S]*$`,
  'gi'
);

// 按完整围栏代码块拆分正文，避免把用户教学/代码片段里的字面 <think> 示例误当思考过程剥离
const CODE_FENCE_SPLIT_RE = /(```[^\r\n]*\r?\n[\s\S]*?\r?\n```|~~~[^\r\n]*\r?\n[\s\S]*?\r?\n~~~)/gm;

const CLOSED_THINK_RE = /<\s*(?:think|thinking|thought)(?:\s+[^>]*)?>([\s\S]*?)<\/\s*(?:think|thinking|thought)\s*>/gi;
const UNCLOSED_THINK_TAIL_RE = /<\s*(?:think|thinking|thought)(?:\s+[^>]*)?>([\s\S]*)$/i;
const ORPHAN_CLOSE_THINK_RE = /<\/\s*(?:think|thinking|thought)\s*>/gi;

// 兼容单全角竖线、双全角竖线、半角竖线及其间空格的 DSML 标签结构（如 <｜｜DSML｜｜ calls>）
const DSML_INVOKE_NAME_RE = /<\s*[\uFF5C|][\uFF5C|\s]*DSML[\uFF5C|\s]*invoke\b[^>]*?name=["']?([^"'\s>]+)/gi;
const DSML_CALLS_BLOCK_RE = /<\s*[\uFF5C|][\uFF5C|\s]*DSML[\uFF5C|\s]*(?:function_)?calls\b[^>]*>[\s\S]*?<\/\s*[\uFF5C|][\uFF5C|\s]*DSML[\uFF5C|\s]*(?:function_)?calls\s*>/gi;
const DSML_INVOKE_BLOCK_RE = /<\s*[\uFF5C|][\uFF5C|\s]*DSML[\uFF5C|\s]*invoke\b[^>]*>[\s\S]*?<\/\s*[\uFF5C|][\uFF5C|\s]*DSML[\uFF5C|\s]*invoke\s*>/gi;
const DSML_PARAM_BLOCK_RE = /<\s*[\uFF5C|][\uFF5C|\s]*DSML[\uFF5C|\s]*parameter\b[^>]*>[\s\S]*?<\/\s*[\uFF5C|][\uFF5C|\s]*DSML[\uFF5C|\s]*parameter\s*>/gi;
const DSML_UNCLOSED_TAIL_RE = /<\s*[\uFF5C|][\uFF5C|\s]*DSML[\uFF5C|\s]*(?:(?:function_)?calls|invoke|parameter)\b(?![\s\S]*?<\/\s*[\uFF5C|][\uFF5C|\s]*DSML\b)[\s\S]*$/gi;
const DSML_ORPHAN_TAG_RE = /<\/?\s*[\uFF5C|][\uFF5C|\s]*DSML[\uFF5C|\s]*[^>]*>/gi;
const DSML_STREAM_HALF_PREFIX_RE = /<\s*[\uFF5C|]{1,2}\s*(?:D(?:S(?:M(?:L)?)?)?)?$/i;

const TOOL_CALL_NAMED_RE = /[<\[]tool_call\s+name=["']?([^"'\]>]+)["']?\s*[>\]]/gi;
const TOOL_CALL_PAIR_RE = /[<\[]tool_call(?:\s+name=["']?[^"'\]>]+["']?)?\s*[>\]][\s\S]*?[<\[]\/tool_call[>\]]/gi;
const TOOL_CALL_UNCLOSED_TAIL_RE = /[<\[]tool_call\b(?![\s\S]*?[<\[]\/tool_call[>\]])[\s\S]*$/gi;

export function sanitizeAssistantContent(
  raw: string,
  options?: SanitizeAssistantOptions
): SanitizedAssistantContent {
  if (!raw) {
    return {
      cleanContent: '',
      thinkingParts: [],
      strippedToolNames: [],
      hadToolMarkup: false,
    };
  }

  const isStreaming = Boolean(options?.isStreaming);
  const thinkingParts: string[] = [];
  const toolNameSet = new Set<string>();
  let hadToolMarkup = false;

  // 1. 先按围栏代码块切段，提取并剥离非代码示例内的 <think>...</think>
  const rawSegments = raw.split(CODE_FENCE_SPLIT_RE);
  const withoutThinkSegments = rawSegments.map((seg, idx) => {
    if (!seg) return '';
    if (idx % 2 === 1) {
      return seg;
    }

    let nextSeg = seg.replace(CLOSED_THINK_RE, (_full, inner: string) => {
      const trimmedInner = (inner || '').trim();
      if (trimmedInner) thinkingParts.push(trimmedInner);
      return '';
    });

    if (idx === rawSegments.length - 1) {
      nextSeg = nextSeg.replace(UNCLOSED_THINK_TAIL_RE, (_full, inner: string) => {
        const trimmedInner = (inner || '').trim();
        if (trimmedInner) thinkingParts.push(trimmedInner);
        return '';
      });
    }

    return nextSeg.replace(ORPHAN_CLOSE_THINK_RE, '');
  });

  let text = withoutThinkSegments.join('');

  // 2. 收集被剥离的工具名元信息（用于历史纯协议气泡的静默友好提示）
  if (text.includes('DSML') || text.includes('dsml')) {
    let m: RegExpExecArray | null;
    DSML_INVOKE_NAME_RE.lastIndex = 0;
    while ((m = DSML_INVOKE_NAME_RE.exec(text)) !== null) {
      if (m[1]) toolNameSet.add(m[1].trim());
    }
  }
  if (text.toLowerCase().includes('tool_call')) {
    let m: RegExpExecArray | null;
    TOOL_CALL_NAMED_RE.lastIndex = 0;
    while ((m = TOOL_CALL_NAMED_RE.exec(text)) !== null) {
      if (m[1]) toolNameSet.add(m[1].trim());
    }
  }

  const prevBeforeTools = text;

  // 3. 剥离 DeepSeek DSML 全角/半角竖线调用块及流式未闭合半截标签
  text = text
    .replace(DSML_CALLS_BLOCK_RE, '')
    .replace(DSML_INVOKE_BLOCK_RE, '')
    .replace(DSML_PARAM_BLOCK_RE, '')
    .replace(DSML_UNCLOSED_TAIL_RE, '')
    .replace(DSML_ORPHAN_TAG_RE, '');

  if (isStreaming) {
    text = text.replace(DSML_STREAM_HALF_PREFIX_RE, '');
  }

  // 4. 剥离标准 XML / 方括号工具调用标签与直写工具标签
  text = text
    .replace(TOOL_CALL_PAIR_RE, '')
    .replace(TOOL_CALL_UNCLOSED_TAIL_RE, '')
    .replace(DIRECT_TOOL_PATTERN, '')
    .replace(DIRECT_TOOL_UNCLOSED_TAIL_PATTERN, '')
    .replace(/<\/?tool_call\b[^>]*>/gi, '')
    .replace(/\[\/?tool_call\b[^\]]*\]/gi, '')
    .replace(/<\/?tool_result\b[^>]*>/gi, '')
    .replace(/\[\/?tool_result\b[^\]]*\]/gi, '')
    .replace(/<\/?function_results?\b[^>]*>/gi, '')
    .replace(/<\/?slot\b[^>]*>/gi, '')
    .replace(/<\/?tool_code\b[^>]*>/gi, '')
    .replace(/\[调用工具:\s*[^\]]+\]/gi, '');

  if (isStreaming) {
    // 拦截流式尾部尚未敲完的协议起始碎片，防止逐字闪现
    text = text.replace(
      /<\s*(?:\/?\s*(?:th(?:ink(?:ing)?|ough(?:t)?)?|to(?:ol(?:_?(?:call|code))?)?|sl(?:ot)?)|(?:ex(?:e(?:cute_local_command)?)?|wr(?:i(?:te_local_file)?)?|re(?:a(?:d_(?:local_file|tool_ref))?)?|we(?:b_fetch)?|ui(?:_action)?|up(?:date_working_memory)?|lo(?:ad_skill_detail)?|sa(?:ve_long_term_memory)?|se(?:arch_(?:memory_history|skill_knowledge))?|ru(?:n_skill_tool)?))[a-z_]*$/i,
      (tail) => (tail.length <= 28 ? '' : tail)
    );
  }

  if (text !== prevBeforeTools) {
    hadToolMarkup = true;
  }

  // 5. 清理剥离工具标签后遗留的空围栏块、伪造历史串台前缀与冗余空行
  text = text
    .replace(/```(?:xml|json|tool_call)?\s*```/gi, '')
    .replace(/^\s*```xml\s*$/gim, '')
    .replace(/^\s*xml\s*$/gim, '');

  text = stripPseudoTranscriptPrefixes(text);
  text = text.replace(/\n{3,}/g, '\n\n').trim();

  return {
    cleanContent: text,
    thinkingParts,
    strippedToolNames: Array.from(toolNameSet),
    hadToolMarkup,
  };
}

/**
 * 安全剥离非代码块围栏外部误混入的 [终端/工具输出]: / [Gemini]: / <history_turn> 伪串台片段
 */
export function stripPseudoTranscriptPrefixes(raw: string): string {
  if (!raw) return '';
  if (
    !raw.includes('[终端/工具输出]:') &&
    !raw.includes('[Gemini]:') &&
    !raw.includes('<history_turn') &&
    !raw.includes('</history_turn>')
  ) {
    return raw;
  }

  const segments = raw.split(CODE_FENCE_SPLIT_RE);
  const cleanedSegments = segments.map((seg, idx) => {
    if (!seg) return '';
    if (idx % 2 === 1) {
      return seg;
    }

    let nextSeg = seg.replace(/<\/?\s*history_turn(?:\s+[^>]*)?>/gi, '');
    // 先剥离明确以后续角色标记收尾的伪造 [终端/工具输出]: 区块（含自演脱水/归档段落）
    nextSeg = nextSeg.replace(
      /(?:^|\n)[ \t]*\[终端\/工具输出\]:[\s\S]*?(?=\r?\n[ \t]*\[(?:Gemini|用户|终端\/工具输出)\]:)/g,
      ''
    );
    // 剥离残留行首裸 [Gemini]: 标签前缀
    nextSeg = nextSeg.replace(/(?:^|\n)[ \t]*\[Gemini\]:[ \t]*\r?\n?/g, '\n');
    // 剥离无后续 [Gemini]: 的孤立 [终端/工具输出]: 脱水摘要块（仅匹配脱水元数据行，不吞后续正式正文）
    nextSeg = nextSeg.replace(
      /(?:^|\n)[ \t]*\[终端\/工具输出\]:[^\r\n]*(?:\r?\n[ \t]*(?:核心摘要:|物理路径:|\(提示:|### Sheet:|Row \d+:|\.\.\. \(该表共有)[^\r\n]*)*\r?\n?/g,
      '\n'
    );
    return nextSeg;
  });

  return cleanedSegments
    .join('')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * 判断清洗后的文本是否属于面向用户的完整交付报告（而非简短步骤过渡旁白或折叠占位句）
 */
export function isSubstantiveDeliverableText(content: string): boolean {
  const clean = (content || '').trim();
  if (!clean || clean.startsWith('（已自动折叠历史')) {
    return false;
  }
  if (clean.length >= 120) {
    return true;
  }
  if (
    clean.length >= 40 &&
    (/^#{1,4}\s+\S/m.test(clean) || /\|[\s:-]+\|/.test(clean) || clean.split('\n').length >= 3)
  ) {
    return true;
  }
  return false;
}

/**
 * 历史/流式单节点兼容拆分：将同时包含“过程产物”和“大段最终交付正文”的单一 model 消息，
 * 无损拆分为“过程节点（思考 + 工具卡片）”与“外部独立最终交付气泡（含生成文件直达与指标卡）”。
 */
export function splitSingleProcessDeliverable(
  msg: Message,
  isInteractiveCardFn: (text: string) => boolean = () => false
): Message[] {
  const content = (msg.content || '').trim();
  const hasToolEvents = Boolean(msg.tool_events && msg.tool_events.length > 0);
  const hasToolCallsMeta = Boolean(msg.tool_calls_json && msg.tool_calls_json !== '[]');
  const hasProcessArtifact = Boolean(hasToolEvents || hasToolCallsMeta || msg.thinking);

  if (
    !msg.is_process_step ||
    isInteractiveCardFn(content) ||
    !isSubstantiveDeliverableText(content)
  ) {
    return [msg];
  }

  // 仅因历史标签被误置为过程步但实际没有任何工具/思考产物时，直接还原为普通独立交付卡片
  if (!hasProcessArtifact) {
    return [
      {
        ...msg,
        is_process_step: false,
      },
    ];
  }

  return [
    {
      ...msg,
      id: undefined,
      content: '',
      is_process_step: true,
      fact_refs: undefined,
      memory_refs: undefined,
      token_stats: undefined,
    },
    {
      ...msg,
      thinking: undefined,
      is_placeholder_thinking: false,
      tool_calls_json: null,
      tool_events: undefined,
      merged_tool_message_ids: undefined,
      is_process_step: false,
    },
  ];
}

/**
 * 历史兼容视图拆分：当某轮最后一条 model 消息把“工具执行过程”和“大段最终交付正文”混存于同一记录时，
 * 复用单节点拆分逻辑无损拆出独立的最终交付气泡。
 */
export function splitTrailingProcessDeliverables(
  messages: Message[],
  isInteractiveCardFn: (text: string) => boolean = () => false
): Message[] {
  if (!messages || messages.length === 0) return [];

  const result: Message[] = [];
  let turnStart = 0;

  const flushTurnSlice = (slice: Message[]) => {
    let lastModelIdx = -1;
    for (let i = slice.length - 1; i >= 0; i--) {
      if (slice[i].role === 'model') {
        lastModelIdx = i;
        break;
      }
    }

    slice.forEach((msg, idx) => {
      if (idx !== lastModelIdx) {
        result.push(msg);
        return;
      }
      result.push(...splitSingleProcessDeliverable(msg, isInteractiveCardFn));
    });
  };

  for (let i = 0; i < messages.length; i++) {
    if (messages[i].role === 'user') {
      if (i > turnStart) {
        flushTurnSlice(messages.slice(turnStart, i));
      }
      result.push(messages[i]);
      turnStart = i + 1;
    }
  }
  if (turnStart < messages.length) {
    flushTurnSlice(messages.slice(turnStart));
  }

  return result;
}
