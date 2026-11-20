'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  BookPlus,
  Check,
  ChevronRight,
  Copy,
  Loader2,
  Sparkles,
  Volume2,
  VolumeX,
} from 'lucide-react';
import { apiFetch } from '@/utils/api';
import { sanitizeAssistantContent } from '@/utils/toolTagSanitizer';
import { VoiceFirstInputBar } from '@/components/mobile/VoiceFirstInputBar';
import { SaveToWikiModal } from '@/components/wiki/SaveToWikiModal';

export interface MobileExpertItem {
  id: string;
  name: string;
  description?: string;
}

interface ChatMsg {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

interface MobileChatViewProps {
  conversationId: string | null;
  experts: MobileExpertItem[];
  activeExpert: MobileExpertItem | null;
  sceneMode?: 'agent' | 'chat';
  onSelectExpert: (expert: MobileExpertItem) => void;
  onConversationCreated: (convId: string) => void;
  onOpenSnap: () => void;
}

const STARTER_PROMPTS = [
  '帮我核对当前竞赛规程中的关键时间节点与报名资格要求',
  '总结近期会议决策要点，整理成待归档的规则条款',
  '查询器材检录规范与常见违规判定标准',
];

/**
 * 尝试从 Markdown 文本中提取简明的 2 列 Key-Value 键值对，用于移动端紧凑展示
 */
function extractCompactKeyValues(content: string): Array<{ key: string; value: string }> {
  const rows: Array<{ key: string; value: string }> = [];
  const lines = content.split('\n');
  for (const line of lines) {
    const m = line.match(/^\s*[-*]\s+\*\*([^*：:]{2,20})\*\*[：:]\s*(.{2,60})$/);
    if (m) {
      rows.push({ key: m[1].trim(), value: m[2].trim() });
    }
    if (rows.length >= 6) break;
  }
  return rows.length >= 2 ? rows : [];
}

/**
 * 移动端「问专家」主视图
 * 采用原生手机消息流风格：圆润对话气泡、专家身份徽章、15px 高可读排版与胶囊操作栏
 */
export function MobileChatView({
  conversationId,
  experts,
  activeExpert,
  sceneMode = 'agent',
  onSelectExpert,
  onConversationCreated,
  onOpenSnap,
}: MobileChatViewProps) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [saveModalContent, setSaveModalContent] = useState<string | null>(null);

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
    const plainText = cleanText.replace(/[#*`>\[\]()]/g, ' ').trim();
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
        { id: assistantMsgId, role: 'assistant', content: '' },
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

  return (
    <div className="flex h-full flex-col bg-background">
      {/* 消息流区域 */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="space-y-6 pt-2">
            {/* 专家横向滑动选择条 */}
            <div>
              <div className="mb-2.5 flex items-center justify-between">
                <p className="text-xs font-semibold tracking-wide text-muted-foreground">
                  选择领域专家
                </p>
                <span className="rounded-full bg-surface-1 px-2.5 py-0.5 text-[11px] text-muted-foreground">
                  {sceneMode === 'agent' ? '深度查阅' : '纯文本快聊'}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2.5">
                {experts.slice(0, 6).map((ex) => {
                  const selected = activeExpert?.id === ex.id;
                  return (
                    <button
                      key={ex.id}
                      type="button"
                      onClick={() => onSelectExpert(ex)}
                      className={`flex min-h-[72px] flex-col justify-between rounded-2xl border p-3 text-left transition-all active:scale-[0.98] ${
                        selected
                          ? 'border-emerald-500/70 bg-emerald-500/10 shadow-sm'
                          : 'border-border/70 bg-surface-1'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-sm font-semibold text-foreground">
                          {ex.name}
                        </span>
                        {selected && (
                          <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-500" />
                        )}
                      </div>
                      <div className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                        {ex.description || '领域规程顾问'}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* 高频问题推荐 */}
            <div>
              <p className="mb-2.5 text-xs font-semibold tracking-wide text-muted-foreground">
                你可以这样问
              </p>
              <div className="space-y-2.5">
                {STARTER_PROMPTS.map((prompt, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => void sendMessage(prompt)}
                    className="flex min-h-[48px] w-full items-center justify-between gap-3 rounded-2xl border border-border/70 bg-surface-1 px-4 py-3 text-left text-sm text-foreground transition-transform active:scale-[0.99]"
                  >
                    <div className="flex min-w-0 items-center gap-2.5">
                      <Sparkles
                        className="h-4 w-4 shrink-0 text-emerald-500"
                        strokeWidth={1.75}
                      />
                      <span className="line-clamp-2 leading-snug">{prompt}</span>
                    </div>
                    <ChevronRight
                      className="h-4 w-4 shrink-0 text-muted-foreground"
                      strokeWidth={1.75}
                    />
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((msg, msgIdx) => {
              if (msg.role === 'user') {
                return (
                  <div key={msg.id} className="flex justify-end">
                    <div className="max-w-[82%] rounded-2xl rounded-tr-sm bg-emerald-600 px-4 py-2.5 text-[15px] leading-relaxed text-white shadow-sm">
                      {msg.content}
                    </div>
                  </div>
                );
              }

              const isLatestStreaming = isStreaming && msgIdx === messages.length - 1;
              const { cleanContent } = sanitizeAssistantContent(msg.content, {
                isStreaming: isLatestStreaming,
              });

              // 历史消息中纯工具调用步骤在清洗后为空，直接跳过不渲染空卡片
              if (!cleanContent && !isLatestStreaming) {
                return null;
              }

              const compactPairs = cleanContent ? extractCompactKeyValues(cleanContent) : [];

              return (
                <div
                  key={msg.id}
                  className="rounded-2xl border border-border/70 bg-surface-1 p-4 shadow-sm"
                >
                  {/* 专家小抬头 */}
                  <div className="mb-2.5 flex items-center justify-between text-xs text-muted-foreground">
                    <div className="flex items-center gap-1.5 font-medium text-foreground/85">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                      <span>{activeExpert?.name || '专家顾问'}</span>
                    </div>
                    <span className="text-[11px]">
                      {sceneMode === 'agent' ? '深度查阅' : '快聊'}
                    </span>
                  </div>

                  {compactPairs.length > 0 && (
                    <div className="mb-3 grid grid-cols-2 gap-2 border-b border-border/60 pb-3">
                      {compactPairs.map((pair, idx) => (
                        <div
                          key={idx}
                          className="rounded-xl border border-border/60 bg-background px-3 py-2"
                        >
                          <div className="text-[11px] text-muted-foreground">{pair.key}</div>
                          <div className="mt-0.5 text-xs font-semibold text-foreground tabular-nums">
                            {pair.value}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {cleanContent ? (
                    <div className="prose prose-sm max-w-none text-[15px] leading-[1.65] text-foreground dark:prose-invert [&_p]:my-2 [&_ul]:my-2 [&_ol]:my-2 [&_table]:block [&_table]:w-full [&_table]:overflow-x-auto [&_table]:whitespace-nowrap">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleanContent}</ReactMarkdown>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin text-emerald-500" strokeWidth={1.75} />
                      <span>正在查阅资料与工具...</span>
                    </div>
                  )}

                  {cleanContent && (
                    <div className="mt-3.5 flex items-center gap-2 border-t border-border/60 pt-3">
                      <button
                        type="button"
                        onClick={() => handleToggleSpeak(msg.id, cleanContent)}
                        className="flex h-9 items-center gap-1.5 rounded-full bg-background px-3 text-xs font-medium text-muted-foreground transition-transform active:scale-95"
                      >
                        {speakingId === msg.id ? (
                          <>
                            <VolumeX className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
                            <span className="text-emerald-600 dark:text-emerald-400">停止</span>
                          </>
                        ) : (
                          <>
                            <Volume2 className="h-3.5 w-3.5" strokeWidth={1.75} />
                            <span>朗读</span>
                          </>
                        )}
                      </button>

                      <button
                        type="button"
                        onClick={() => handleCopy(msg.id, cleanContent)}
                        className="flex h-9 items-center gap-1.5 rounded-full bg-background px-3 text-xs font-medium text-muted-foreground transition-transform active:scale-95"
                      >
                        {copiedId === msg.id ? (
                          <>
                            <Check className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
                            <span className="text-emerald-600 dark:text-emerald-400">已复制</span>
                          </>
                        ) : (
                          <>
                            <Copy className="h-3.5 w-3.5" strokeWidth={1.75} />
                            <span>复制</span>
                          </>
                        )}
                      </button>

                      <button
                        type="button"
                        onClick={() => setSaveModalContent(cleanContent)}
                        className="ml-auto flex h-9 items-center gap-1.5 rounded-full bg-emerald-500/15 px-3.5 text-xs font-semibold text-emerald-600 transition-transform active:scale-95 dark:text-emerald-400"
                      >
                        <BookPlus className="h-3.5 w-3.5" strokeWidth={1.75} />
                        <span>收藏到Wiki</span>
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
        onSubmit={(txt) => void sendMessage(txt)}
        onOpenSnap={onOpenSnap}
      />

      <SaveToWikiModal
        isOpen={saveModalContent !== null}
        onClose={() => setSaveModalContent(null)}
        initialContent={saveModalContent || ''}
        defaultSourceType="chat_decision"
      />
    </div>
  );
}
