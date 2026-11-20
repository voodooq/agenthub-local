'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  BookPlus,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Sparkles,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react';
import { apiFetch } from '@/utils/api';
import { sanitizeAssistantContent } from '@/utils/toolTagSanitizer';
import { formatToolNaturalDescription, groupMessagesByTurn } from '@/utils/turnTimeline';
import { CodeBlock } from '@/components/CodeBlock';
import { VoiceFirstInputBar } from '@/components/mobile/VoiceFirstInputBar';
import { SaveToWikiModal } from '@/components/wiki/SaveToWikiModal';

export interface MobileExpertItem {
  id: string;
  name: string;
  description?: string;
}

interface ToolStepItem {
  id: string;
  name: string;
  status: 'running' | 'completed';
  command?: string;
  outputPreview?: string;
}

interface ChatMsg {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  toolSteps?: ToolStepItem[];
  followups?: string[];
}

interface MobileChatViewProps {
  conversationId: string | null;
  experts: MobileExpertItem[];
  activeExpert: MobileExpertItem | null;
  sceneMode: 'agent' | 'chat';
  onChangeSceneMode: (mode: 'agent' | 'chat') => void;
  onSelectExpert: (expert: MobileExpertItem) => void;
  onConversationCreated: (convId: string) => void;
  onOpenSnap: () => void;
}

/**
 * 移动端 Gemini 风格对话主视图
 * - 极简留白空状态（居中星芒 + 问候语）
 * - 单行可折叠检索/工具状态胶囊（展开可核对工具名与切片来源）
 * - 去框化平铺 Markdown 正文 + 局部横向滚动表格（防页面溢出）
 * - 动态追问胶囊（仅当返回动态建议时渲染）
 */
export function MobileChatView({
  conversationId,
  activeExpert,
  sceneMode,
  onChangeSceneMode,
  onConversationCreated,
  onOpenSnap,
}: MobileChatViewProps) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [saveModalContent, setSaveModalContent] = useState<string | null>(null);
  const [sheetToolSteps, setSheetToolSteps] = useState<ToolStepItem[] | null>(null);

  const bottomRef = useRef<HTMLDivElement | null>(null);
  const skipHistoryLoadConvIdRef = useRef<string | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      return;
    }
    if (skipHistoryLoadConvIdRef.current === conversationId) {
      skipHistoryLoadConvIdRef.current = null;
      return;
    }
    let active = true;
    apiFetch(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`)
      .then((res) => (res.ok ? res.json() : null))
      .then((list) => {
        if (!active || !Array.isArray(list)) return;
        const mapped: ChatMsg[] = list
          .filter((m) => m.role === 'user' || m.role === 'model' || m.role === 'assistant')
          .map((m, idx) => ({
            id: String(m.id || idx),
            role: m.role === 'user' ? 'user' : 'assistant',
            content: String(m.content || ''),
          }));
        setMessages(mapped);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [conversationId]);

  useEffect(() => {
    return () => {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  const handleToggleSpeak = (msgId: string, cleanText: string) => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;
    if (speakingId === msgId) {
      window.speechSynthesis.cancel();
      setSpeakingId(null);
      return;
    }
    window.speechSynthesis.cancel();
    const plainText = cleanText.replace(/[#*`>\[\]()|]/g, ' ').trim();
    if (!plainText) return;
    const utterance = new SpeechSynthesisUtterance(plainText);
    utterance.lang = 'zh-CN';
    utterance.onend = () => setSpeakingId(null);
    utterance.onerror = () => setSpeakingId(null);
    setSpeakingId(msgId);
    window.speechSynthesis.speak(utterance);
  };

  const handleCopy = (msgId: string, cleanText: string) => {
    navigator.clipboard.writeText(cleanText).then(() => {
      setCopiedId(msgId);
      window.setTimeout(() => {
        setCopiedId((prev) => (prev === msgId ? null : prev));
      }, 1800);
    });
  };

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) return;

      const userMsgId = `u-${Date.now()}`;
      const assistantMsgId = `a-${Date.now() + 1}`;
      setMessages((prev) => [
        ...prev,
        { id: userMsgId, role: 'user', content: trimmed },
        { id: assistantMsgId, role: 'assistant', content: '', toolSteps: [] },
      ]);
      setIsStreaming(true);

      try {
        const res = await apiFetch('/api/v1/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            conversation_id: conversationId,
            message: trimmed,
            agent_id: activeExpert?.id || null,
            scene_mode: sceneMode,
          }),
        });

        if (!res.ok || !res.body) {
          throw new Error('对话请求失败');
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const rawLine of lines) {
            const line = rawLine.trim();
            if (!line.startsWith('data:')) continue;
            const jsonStr = line.slice(5).trim();
            if (!jsonStr || jsonStr === '[DONE]') continue;

            try {
              const ev = JSON.parse(jsonStr);
              if (ev.type === 'conv_init' && ev.conversation_id && !conversationId) {
                skipHistoryLoadConvIdRef.current = ev.conversation_id;
                onConversationCreated(ev.conversation_id);
              } else if (ev.type === 'stream_reset') {
                setMessages((prev) =>
                  prev.map((m) => (m.id === assistantMsgId ? { ...m, content: '' } : m))
                );
              } else if (ev.type === 'tool_start') {
                const callId = String(ev.call_id || `${ev.name || 'tool'}-${Date.now()}`);
                const tName = String(ev.name || 'tool');
                setMessages((prev) =>
                  prev.map((m) => {
                    if (m.id !== assistantMsgId) return m;
                    const existing = m.toolSteps || [];
                    return {
                      ...m,
                      toolSteps: [
                        ...existing,
                        {
                          id: callId,
                          name: tName,
                          status: 'running',
                          command: typeof ev.command === 'string' ? ev.command : undefined,
                        },
                      ],
                    };
                  })
                );
              } else if (ev.type === 'tool_end') {
                const callId = String(ev.call_id || '');
                const tName = String(ev.name || 'tool');
                const preview =
                  typeof ev.output === 'string'
                    ? ev.output.slice(0, 420)
                    : typeof ev.compressed === 'string'
                    ? ev.compressed
                    : undefined;
                setMessages((prev) =>
                  prev.map((m) => {
                    if (m.id !== assistantMsgId) return m;
                    const existing = m.toolSteps || [];
                    const idx = existing.findIndex((s) => (callId && s.id === callId) || (s.name === tName && s.status === 'running'));
                    if (idx >= 0) {
                      const nextSteps = [...existing];
                      nextSteps[idx] = {
                        ...nextSteps[idx],
                        status: 'completed',
                        command: typeof ev.command === 'string' ? ev.command : nextSteps[idx].command,
                        outputPreview: preview,
                      };
                      return { ...m, toolSteps: nextSteps };
                    }
                    return {
                      ...m,
                      toolSteps: [
                        ...existing,
                        {
                          id: callId || `${tName}-${existing.length}`,
                          name: tName,
                          status: 'completed',
                          command: typeof ev.command === 'string' ? ev.command : undefined,
                          outputPreview: preview,
                        },
                      ],
                    };
                  })
                );
              } else if (Array.isArray(ev.followups) && ev.followups.length > 0) {
                const nextFollowups = ev.followups
                  .filter((x: unknown): x is string => typeof x === 'string' && x.trim().length > 0)
                  .slice(0, 2);
                if (nextFollowups.length > 0) {
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMsgId ? { ...m, followups: nextFollowups } : m
                    )
                  );
                }
              } else if (
                (ev.type === 'chunk' ||
                  ev.type === 'token' ||
                  ev.type === 'content' ||
                  ev.type === 'delta') &&
                typeof ev.content === 'string'
              ) {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsgId ? { ...m, content: m.content + ev.content } : m
                  )
                );
              } else if ((ev.type === 'content' || ev.type === 'text') && typeof ev.text === 'string') {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsgId ? { ...m, content: m.content + ev.text } : m
                  )
                );
              } else if (ev.type === 'error') {
                const errText = ev.content || ev.message || ev.detail || '服务暂时不可用，请重试';
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsgId ? { ...m, content: `提示：${errText}` } : m
                  )
                );
              }
            } catch {
              // 忽略非 JSON SSE 行
            }
          }
        }
      } catch {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId && !m.content
              ? { ...m, content: '网络连接中断，请检查本地服务状态后重试。' }
              : m
          )
        );
      } finally {
        setIsStreaming(false);
      }
    },
    [activeExpert?.id, conversationId, isStreaming, onConversationCreated, sceneMode]
  );

  const timelineTurns = useMemo(() => groupMessagesByTurn(messages), [messages]);

  const latestAssistantReply = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'assistant' && messages[i].content.trim()) {
        const { cleanContent } = sanitizeAssistantContent(messages[i].content, { isStreaming: false });
        if (cleanContent) return cleanContent;
      }
    }
    return '';
  }, [messages]);

  return (
    <div className="flex h-full flex-col bg-background">
      {/* 消息流区域 */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-4 text-center">
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-500/12 text-emerald-500">
              <Sparkles className="h-6 w-6 gemini-sparkle-pulse" strokeWidth={1.75} />
            </div>
            <h2 className="text-xl font-semibold tracking-tight text-foreground">
              有什么我可以帮你分担的吗？
            </h2>
            {activeExpert?.name && (
              <p className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-surface-1 px-3 py-1 text-xs text-muted-foreground">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                <span>当前专家：{activeExpert.name}</span>
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-5 pb-2">
            {timelineTurns.map((turn, turnIdx) => {
              if (turn.role === 'user' && turn.userMessage) {
                return (
                  <div key={turn.stableKey} className="gemini-msg-enter flex justify-end">
                    <div className="max-w-[82%] rounded-2xl rounded-tr-sm bg-emerald-600 px-4 py-2.5 text-[15px] leading-relaxed text-white shadow-xs break-anywhere">
                      {turn.userMessage.content}
                    </div>
                  </div>
                );
              }

              const targetMsg =
                turn.finalMessage || turn.processMessages[turn.processMessages.length - 1];
              if (!targetMsg) return null;

              const isLatestStreaming = isStreaming && turnIdx === timelineTurns.length - 1;
              const { cleanContent } = sanitizeAssistantContent(targetMsg.content, {
                isStreaming: isLatestStreaming,
              });
              const toolSteps: ToolStepItem[] = [
                ...turn.processMessages.flatMap((m) => m.toolSteps || []),
                ...(turn.finalMessage?.toolSteps || []),
              ];
              const hasTools = toolSteps.length > 0;
              const runningStep = toolSteps.find((s) => s.status === 'running');
              const lastStep = toolSteps[toolSteps.length - 1];

              if (!cleanContent && !isLatestStreaming && !hasTools) {
                return null;
              }

              return (
                <div key={turn.stableKey} className="gemini-msg-enter space-y-2.5">
                  {/* 单行可折叠检索 / 工具状态胶囊（点击弹出半屏 Bottom Sheet 查看过程详情与命中切片） */}
                  {hasTools && (
                    <div>
                      <button
                        type="button"
                        onClick={() => setSheetToolSteps(toolSteps)}
                        className="inline-flex min-h-[34px] max-w-full items-center gap-2 rounded-full border border-border/70 bg-surface-1 px-3 py-1 text-xs font-medium text-foreground/85 transition-colors active:bg-surface-2"
                      >
                        <Sparkles
                          className={`h-3.5 w-3.5 shrink-0 text-emerald-500 ${
                            runningStep ? 'gemini-sparkle-pulse' : ''
                          }`}
                          strokeWidth={1.85}
                        />
                        <span className="truncate">
                          {runningStep
                            ? `正在${formatToolNaturalDescription({ name: runningStep.name, command: runningStep.command })}...`
                            : toolSteps.length === 1 && lastStep
                            ? formatToolNaturalDescription({ name: lastStep.name, command: lastStep.command })
                            : `已完成 ${toolSteps.length} 项检索与核验`}
                        </span>
                        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
                        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      </button>
                    </div>
                  )}

                  {/* AI 回复正文（去框化平铺，Markdown 表格支持局部横向滚动，代码块支持 260px 限高与全屏查看） */}
                  {cleanContent ? (
                    <div className="chat-markdown-body prose prose-sm max-w-none text-foreground dark:prose-invert [&_p]:my-2 [&_ul]:my-2 [&_ol]:my-2 break-anywhere">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          pre: ({ children }) => <>{children}</>,
                          code: ({ className, children, ...props }) => {
                            const match = /language-(\w+)/.exec(className || '');
                            const codeStr = String(children).replace(/\n$/, '');
                            const isBlock = Boolean(match) || codeStr.includes('\n');
                            if (isBlock) {
                              return (
                                <CodeBlock
                                  code={codeStr}
                                  language={match ? match[1] : 'text'}
                                  mobileMode={true}
                                />
                              );
                            }
                            return (
                              <code className={className} {...props}>
                                {children}
                              </code>
                            );
                          },
                          table: ({ children }) => (
                            <div className="my-2.5 w-full overflow-x-auto rounded-xl border border-border/60 bg-surface-1/60">
                              <table className="min-w-full border-collapse text-xs">{children}</table>
                            </div>
                          ),
                          th: ({ children }) => (
                            <th className="border-b border-border/60 bg-surface-2/60 px-3 py-2 text-left font-semibold whitespace-nowrap">
                              {children}
                            </th>
                          ),
                          td: ({ children }) => (
                            <td className="border-b border-border/40 px-3 py-2 whitespace-nowrap">
                              {children}
                            </td>
                          ),
                        }}
                      >
                        {cleanContent}
                      </ReactMarkdown>
                    </div>
                  ) : isLatestStreaming ? (
                    <div className="inline-flex items-center gap-2 py-1 text-sm text-muted-foreground">
                      <Sparkles className="h-4 w-4 text-emerald-500 gemini-sparkle-pulse" />
                      <span>正在思考并查阅知识库...</span>
                    </div>
                  ) : null}

                  {/* 动态追问胶囊（无动态推荐时不渲染） */}
                  {!isLatestStreaming && targetMsg.followups && targetMsg.followups.length > 0 && (
                    <div className="flex flex-wrap gap-2 pt-1">
                      {targetMsg.followups.slice(0, 2).map((q, idx) => (
                        <button
                          key={idx}
                          type="button"
                          onClick={() => void sendMessage(q)}
                          className="min-h-[36px] rounded-full border border-emerald-500/35 bg-emerald-500/8 px-3.5 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-300 transition-transform active:scale-95"
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  )}

                  {/* 极简底部操作条 */}
                  {cleanContent && !isLatestStreaming && (
                    <div className="flex items-center gap-1.5 pt-1 text-muted-foreground">
                      <button
                        type="button"
                        onClick={() => handleCopy(targetMsg.id, cleanContent)}
                        aria-label="复制回复"
                        className="flex h-9 items-center gap-1 rounded-full px-2.5 text-xs transition-colors active:bg-surface-1"
                      >
                        {copiedId === targetMsg.id ? (
                          <Check className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
                        ) : (
                          <Copy className="h-3.5 w-3.5" strokeWidth={1.75} />
                        )}
                      </button>

                      <button
                        type="button"
                        onClick={() => handleToggleSpeak(targetMsg.id, cleanContent)}
                        aria-label={speakingId === targetMsg.id ? '停止朗读' : '朗读回复'}
                        className="flex h-9 items-center gap-1 rounded-full px-2.5 text-xs transition-colors active:bg-surface-1"
                      >
                        {speakingId === targetMsg.id ? (
                          <VolumeX className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
                        ) : (
                          <Volume2 className="h-3.5 w-3.5" strokeWidth={1.75} />
                        )}
                      </button>

                      <button
                        type="button"
                        onClick={() => setSaveModalContent(cleanContent)}
                        className="ml-auto flex h-9 items-center gap-1 rounded-full px-3 text-xs font-medium text-emerald-600 dark:text-emerald-400 transition-colors active:bg-emerald-500/10"
                      >
                        <BookPlus className="h-3.5 w-3.5" strokeWidth={1.75} />
                        <span>存到Wiki</span>
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <VoiceFirstInputBar
        expertId={activeExpert?.id}
        disabled={isStreaming}
        sceneMode={sceneMode}
        onChangeSceneMode={onChangeSceneMode}
        latestAssistantReply={latestAssistantReply}
        onSubmit={(txt) => void sendMessage(txt)}
        onOpenSnap={onOpenSnap}
      />

      <SaveToWikiModal
        isOpen={saveModalContent !== null}
        onClose={() => setSaveModalContent(null)}
        initialContent={saveModalContent || ''}
        defaultSourceType="chat_decision"
      />

      {/* 半屏 Bottom Sheet：展示工具执行步骤与命中切片详情 */}
      {sheetToolSteps && (
        <div
          className="fixed inset-0 z-50 flex items-end bg-black/50 backdrop-blur-[2px]"
          onClick={() => setSheetToolSteps(null)}
        >
          <div
            className="w-full max-h-[70vh] rounded-t-3xl border-t border-border/70 bg-background flex flex-col shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-border/50 shrink-0">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-emerald-500" />
                <span className="text-sm font-semibold text-foreground">
                  检索与执行过程（共 {sheetToolSteps.length} 步）
                </span>
              </div>
              <button
                type="button"
                onClick={() => setSheetToolSteps(null)}
                className="p-1.5 rounded-full hover:bg-surface-1 text-muted-foreground"
                aria-label="关闭过程详情"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-2.5 text-xs">
              {sheetToolSteps.map((step, idx) => (
                <div
                  key={step.id || idx}
                  className="rounded-2xl border border-border/60 bg-surface-1 p-3 space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-foreground truncate">
                      {formatToolNaturalDescription({ name: step.name, command: step.command })}
                    </span>
                    <span
                      className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                        step.status === 'running'
                          ? 'bg-amber-500/15 text-amber-500'
                          : 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                      }`}
                    >
                      {step.status === 'running' ? '执行中' : step.name}
                    </span>
                  </div>
                  {step.command && (
                    <div className="font-mono text-[11px] text-muted-foreground break-all">
                      $ {step.command}
                    </div>
                  )}
                  {step.outputPreview && (
                    <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-anywhere rounded-xl bg-background/80 p-2.5 font-mono text-[11px] leading-relaxed text-foreground/85 border border-border/40">
                      {step.outputPreview}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
