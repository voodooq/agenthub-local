export interface ToolMetaInput {
  name?: string;
  command?: string;
  compressed?: string;
}

/**
 * 将底层工具名与参数映射为自然语言可读动作描述，避免默认聊天流直接裸露函数名或命令行
 */
export function formatToolNaturalDescription({
  name = '',
  command = '',
  compressed = '',
}: ToolMetaInput): string {
  const fn = name.trim().toLowerCase();
  const cmd = command.trim();
  const comp = compressed.trim();

  if (fn === 'list_kb_docs') {
    const kwMatch = cmd.match(/^list_kb_docs\((.*)\)$/);
    const kw = kwMatch?.[1]?.trim();
    return kw ? `筛选知识库文档清单（${kw}）` : '查看当前知识库全部文档清单';
  }
  if (fn === 'list_kb_doc_toc') {
    const docMatch = cmd.match(/^list_kb_doc_toc\((.*)\)$/);
    const target = docMatch?.[1]?.trim();
    return target ? `查看文档章节目录（${target}）` : '查看文档章节目录';
  }
  if (fn === 'read_kb_slices') {
    const sliceMatch = cmd.match(/^read_kb_slices\(([^,]+),\s*([^)]+)\)$/);
    if (sliceMatch) {
      return `读取文档第 ${sliceMatch[2].trim()} 号切片（${sliceMatch[1].trim()}）`;
    }
    return '精读知识库连续切片内容';
  }
  if (fn === 'search_cloud_wiki') {
    const qMatch = cmd.match(/^search_cloud_wiki\((.*)\)$/);
    const q = qMatch?.[1]?.trim();
    return q ? `检索知识库：${q}` : '检索知识库规程内容';
  }
  if (fn === 'read_cloud_wiki_section') {
    return comp ? comp.replace(/^\[|\]$/g, '') : '精读知识库指定章节';
  }
  if (fn === 'query_knowledge_graph') {
    const entMatch = cmd.match(/^query_knowledge_graph\(([^,]+)/);
    const ent = entMatch?.[1]?.trim();
    return ent ? `查询知识图谱实体关系（${ent}）` : '查询知识图谱关系网络';
  }
  if (fn === 'read_wiki_ontology_guides') {
    const kwMatch = cmd.match(/^read_wiki_ontology_guides\((.*)\)$/);
    const kw = kwMatch?.[1]?.trim();
    return kw ? `读取本地 Wiki 业务口径（${kw}）` : '读取本地 Wiki 业务口径与红线';
  }
  if (fn === 'search_biz_ontology_terms') {
    const kwMatch = cmd.match(/^search_biz_ontology_terms\((.*)\)$/);
    const kw = kwMatch?.[1]?.trim();
    return kw ? `检索业务本体术语（${kw}）` : '检索业务本体术语表';
  }
  if (fn === 'query_biz_data_via_ontology') {
    const qMatch = cmd.match(/^query_biz_data_via_ontology\((.*)\)$/);
    const q = qMatch?.[1]?.trim();
    return q ? `查询实时业务库：${q.slice(0, 36)}` : '查询实时业务数据库';
  }
  if (fn === 'execute_sql' || fn.includes('sql')) {
    return '查询业务数据库记录';
  }
  if (fn === 'read_local_file') {
    return comp ? comp.replace(/^\[|\]$/g, '') : '读取本地文件内容';
  }
  if (fn === 'write_local_file') {
    return comp ? comp.replace(/^\[|\]$/g, '') : '写入本地文件';
  }
  if (fn === 'replace_in_file') {
    return comp ? comp.replace(/^\[|\]$/g, '') : '应用代码局部修改';
  }
  if (fn === 'execute_local_command') {
    return '执行本地终端命令';
  }
  if (comp) {
    return comp.replace(/^\[|\]$/g, '');
  }
  return fn ? `调用工具：${name}` : '执行后台工具';
}

export interface TimelineTurnItem<T> {
  /** 稳定 React Key：绑定首条消息 id 或 turn_id，严禁使用数组索引 */
  stableKey: string;
  role: 'user' | 'assistant';
  /** 用户消息直接对应单条 */
  userMessage?: T;
  /** 聚合后的过程消息集合（思考块、中间工具步骤） */
  processMessages: T[];
  /** 最终交付的正式答复消息 */
  finalMessage?: T;
}

export interface TimelineMessageLike {
  id?: string | number | null;
  role: string;
  content?: string | null;
  turn_id?: number | null;
  thinking?: string | null;
  is_process_step?: boolean | null;
  tool_events?: Array<{ status?: string }> | null;
  tool_calls_json?: string | null;
}

/**
 * PC 端与移动端共用的 Turn 级过程聚合器
 * 将同一轮（两个 user 消息之间）的中间思考、工具步骤与最终交付消息聚合成稳定节点，防止流式切换闪烁重置
 */
export function groupMessagesByTurn<T extends TimelineMessageLike>(
  messages: T[],
  isInteractiveChecker?: (content: string) => boolean
): TimelineTurnItem<T>[] {
  const result: TimelineTurnItem<T>[] = [];
  let pendingProcess: T[] = [];

  const flushPendingAsAssistant = () => {
    if (pendingProcess.length === 0) return;
    const first = pendingProcess[0];
    const stableKey =
      first.id !== undefined && first.id !== null
        ? `turn-${String(first.id)}`
        : `turn-idx-${result.length}`;
    const last = pendingProcess[pendingProcess.length - 1];
    const restProcess = pendingProcess.slice(0, -1);
    result.push({
      stableKey,
      role: 'assistant',
      processMessages: restProcess,
      finalMessage: last,
    });
    pendingProcess = [];
  };

  for (let i = 0; i < messages.length; i += 1) {
    const msg = messages[i];
    if (msg.role === 'user') {
      flushPendingAsAssistant();
      const stableKey =
        msg.id !== undefined && msg.id !== null
          ? `user-${String(msg.id)}`
          : `user-pos-${i}`;
      result.push({
        stableKey,
        role: 'user',
        userMessage: msg,
        processMessages: [],
      });
      continue;
    }

    const isInteractive = isInteractiveChecker ? isInteractiveChecker(msg.content || '') : false;
    const hasTools =
      (Array.isArray(msg.tool_events) && msg.tool_events.length > 0) ||
      Boolean(msg.tool_calls_json && msg.tool_calls_json !== '[]');
    const isProcess = !isInteractive && Boolean(msg.is_process_step || hasTools);

    if (isProcess) {
      pendingProcess.push(msg);
    } else {
      const anchor = pendingProcess[0] || msg;
      const stableKey =
        anchor.id !== undefined && anchor.id !== null
          ? `turn-${String(anchor.id)}`
          : `turn-pos-${i}`;
      result.push({
        stableKey,
        role: 'assistant',
        processMessages: pendingProcess,
        finalMessage: msg,
      });
      pendingProcess = [];
    }
  }

  flushPendingAsAssistant();
  return result;
}

