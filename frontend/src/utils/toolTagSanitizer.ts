/**
 * 前端助手输出协议清洗与思考片段拆解工具
 * 统一用于流式 SSE 增量渲染与历史数据库消息加载，彻底消除双轨正则分叉与协议源码泄露。
 */

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

  // 5. 清理剥离工具标签后遗留的空围栏块与冗余空行
  text = text
    .replace(/```(?:xml|json|tool_call)?\s*```/gi, '')
    .replace(/^\s*```xml\s*$/gim, '')
    .replace(/^\s*xml\s*$/gim, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  return {
    cleanContent: text,
    thinkingParts,
    strippedToolNames: Array.from(toolNameSet),
    hadToolMarkup,
  };
}
