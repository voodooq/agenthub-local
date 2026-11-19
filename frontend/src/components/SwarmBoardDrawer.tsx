'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  X,
  Users,
  Send,
  RefreshCw,
  Skull,
  CheckCircle2,
  Clock,
  Activity,
  MessageSquare,
  ShieldAlert,
  Loader2,
  AtSign,
  PowerOff,
} from 'lucide-react';
import { SwarmInfo, SwarmMember, SwarmMessage } from '@/types';
import { apiFetch } from '@/utils/apiClient';

interface SwarmBoardDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  swarmId: string | null;
  sessionId?: string | null;
  initialSwarm?: SwarmInfo | null;
  initialMembers?: SwarmMember[];
  initialMessages?: SwarmMessage[];
  onSwarmUpdated?: (swarm: SwarmInfo | null, members: SwarmMember[]) => void;
  onDisband?: () => void;
}

/**
 * 渲染单条蜂群消息文本，安全高亮 @mention 与 [done]/[verified] 状态标签
 */
function renderHighlightedSwarmText(rawText: string): React.ReactNode {
  if (!rawText) return null;
  const tokenRegex = /(@[a-zA-Z0-9_\-\u4e00-\u9fa5]+|\[done\]|\[verified\])/gi;
  const parts = rawText.split(tokenRegex);

  return parts.map((part, idx) => {
    if (!part) return null;
    if (part.startsWith('@')) {
      return (
        <span
          key={idx}
          className="inline-flex items-center px-1.5 py-0.2 mx-0.5 rounded bg-surface-200 text-foreground border border-border font-mono text-[11px] font-semibold"
        >
          {part}
        </span>
      );
    }
    const lower = part.toLowerCase();
    if (lower === '[done]') {
      return (
        <span
          key={idx}
          className="inline-flex items-center px-1.5 py-0.2 mx-0.5 rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30 font-mono text-[10px] font-bold uppercase"
        >
          [done]
        </span>
      );
    }
    if (lower === '[verified]') {
      return (
        <span
          key={idx}
          className="inline-flex items-center px-1.5 py-0.2 mx-0.5 rounded bg-sky-500/15 text-sky-600 dark:text-sky-300 border border-sky-500/30 font-mono text-[10px] font-bold uppercase"
        >
          [verified]
        </span>
      );
    }
    return <React.Fragment key={idx}>{part}</React.Fragment>;
  });
}

function formatTimeShort(raw?: string): string {
  if (!raw) return '';
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function getMemberStatusBadge(status: string) {
  const s = (status || '').toLowerCase();
  if (s === 'working' || s === 'running') {
    return {
      label: '工作中',
      dotClass: 'bg-emerald-500 animate-pulse',
      pillClass: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border-emerald-500/30',
      Icon: Activity,
    };
  }
  if (s === 'waiting' || s === 'idle') {
    return {
      label: '待命中',
      dotClass: 'bg-amber-500',
      pillClass: 'bg-amber-500/15 text-amber-600 dark:text-amber-300 border-amber-500/30',
      Icon: Clock,
    };
  }
  if (s === 'killed' || s === 'stopped') {
    return {
      label: s === 'killed' ? '已终止' : '已停止',
      dotClass: 'bg-rose-500',
      pillClass: 'bg-rose-500/10 text-rose-500 border-rose-500/25',
      Icon: Skull,
    };
  }
  return {
    label: status || '未知',
    dotClass: 'bg-muted-foreground',
    pillClass: 'bg-surface-200 text-muted-foreground border-border',
    Icon: CheckCircle2,
  };
}

export function SwarmBoardDrawer({
  isOpen,
  onClose,
  swarmId,
  sessionId,
  initialSwarm = null,
  initialMembers = [],
  initialMessages = [],
  onSwarmUpdated,
  onDisband,
}: SwarmBoardDrawerProps) {
  const [swarm, setSwarm] = useState<SwarmInfo | null>(initialSwarm);
  const [members, setMembers] = useState<SwarmMember[]>(initialMembers);
  const [messages, setMessages] = useState<SwarmMessage[]>(initialMessages);
  const [loading, setLoading] = useState(false);
  const [sseConnected, setSseConnected] = useState(false);
  const [inputText, setInputText] = useState('');
  const [sending, setSending] = useState(false);
  const [killingMember, setKillingMember] = useState<string | null>(null);
  const [disbanding, setDisbanding] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const latestSeqRef = useRef<number>(0);

  const effectiveSwarmId = swarm?.id || swarmId || null;

  const handleDisbandSwarm = async () => {
    if (!effectiveSwarmId || disbanding) return;
    const confirmed = window.confirm(
      '确定要解散当前蜂群吗？解散后所有节点任务将终止，会话将恢复为单智能体对话模式。'
    );
    if (!confirmed) return;

    setDisbanding(true);
    setErrorBanner(null);
    try {
      const res = await apiFetch(
        `/api/v1/swarms/${encodeURIComponent(effectiveSwarmId)}/disband`,
        { method: 'POST' }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setErrorBanner(err?.detail || '解散蜂群失败');
        return;
      }
      setSwarm(null);
      setMembers([]);
      setMessages([]);
      onSwarmUpdated?.(null, []);
      onDisband?.();
      onClose();
    } catch (err: any) {
      setErrorBanner(err?.message || '解散蜂群请求异常');
    } finally {
      setDisbanding(false);
    }
  };

  // 同步外部初始状态
  useEffect(() => {
    if (initialSwarm) setSwarm(initialSwarm);
  }, [initialSwarm]);

  useEffect(() => {
    if (initialMembers && initialMembers.length > 0) {
      setMembers(initialMembers);
    }
  }, [initialMembers]);

  useEffect(() => {
    if (initialMessages && initialMessages.length > 0) {
      setMessages((prev) => {
        const map = new Map<number, SwarmMessage>();
        for (const m of prev) map.set(m.seq, m);
        for (const m of initialMessages) map.set(m.seq, m);
        return Array.from(map.values()).sort((a, b) => a.seq - b.seq);
      });
    }
  }, [initialMessages]);

  useEffect(() => {
    const maxSeq = messages.reduce((acc, m) => Math.max(acc, m.seq || 0), 0);
    latestSeqRef.current = maxSeq;
  }, [messages]);

  const mergeIncomingMessages = useCallback((incoming: SwarmMessage[]) => {
    if (!Array.isArray(incoming) || incoming.length === 0) return;
    setMessages((prev) => {
      const map = new Map<number, SwarmMessage>();
      for (const m of prev) map.set(m.seq, m);
      for (const m of incoming) {
        if (typeof m.seq === 'number') {
          map.set(m.seq, m);
        }
      }
      return Array.from(map.values()).sort((a, b) => a.seq - b.seq);
    });
  }, []);

  // 根据 sessionId 或 swarmId 拉取蜂群详情与消息
  const fetchSwarmSnapshot = useCallback(
    async (silent = false) => {
      if (!isOpen) return;
      if (!silent) setLoading(true);
      setErrorBanner(null);

      try {
        if (sessionId && (!swarmId || !swarm)) {
          const res = await apiFetch(
            `/api/v1/swarms/by-session/${encodeURIComponent(sessionId)}`
          );
          if (res.ok) {
            const data = await res.json();
            if (data?.swarm) {
              setSwarm(data.swarm);
              const nextMembers = Array.isArray(data.members) ? data.members : [];
              setMembers(nextMembers);
              if (Array.isArray(data.messages)) {
                mergeIncomingMessages(data.messages);
              }
              onSwarmUpdated?.(data.swarm, nextMembers);
            }
          }
        }

        const targetId = swarm?.id || swarmId;
        if (targetId) {
          const msgRes = await apiFetch(
            `/api/v1/swarms/${encodeURIComponent(targetId)}/messages?since=${latestSeqRef.current}`
          );
          if (msgRes.ok) {
            const msgData = await msgRes.json();
            if (Array.isArray(msgData?.messages)) {
              mergeIncomingMessages(msgData.messages);
            }
          }
        }
      } catch (err: any) {
        if (!silent) {
          setErrorBanner(err?.message || '拉取蜂群看板状态失败');
        }
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [isOpen, sessionId, swarmId, swarm, mergeIncomingMessages, onSwarmUpdated]
  );

  useEffect(() => {
    if (!isOpen) return;
    void fetchSwarmSnapshot(false);
  }, [isOpen, swarmId, sessionId]);

  // 建立 SSE 实时订阅，并配合轻量轮询兜底
  useEffect(() => {
    if (!isOpen || !effectiveSwarmId) {
      setSseConnected(false);
      return;
    }

    const abortController = new AbortController();
    let active = true;

    const connectSse = async () => {
      try {
        const res = await apiFetch(
          `/api/v1/swarms/${encodeURIComponent(effectiveSwarmId)}/stream`,
          { signal: abortController.signal }
        );
        if (!res.ok || !res.body) {
          setSseConnected(false);
          return;
        }
        setSseConnected(true);
        const reader = res.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let currentEvent = '';

        while (active) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const rawLine of lines) {
            const line = rawLine.trim();
            if (!line) {
              currentEvent = '';
              continue;
            }
            if (line.startsWith('event:')) {
              currentEvent = line.slice(6).trim();
              continue;
            }
            if (!line.startsWith('data:')) continue;
            const jsonText = line.slice(5).trim();
            if (!jsonText) continue;

            try {
              const payload = JSON.parse(jsonText);
              const evtType = payload.type || currentEvent;

              if (evtType === 'swarm_update') {
                const nextSwarm = payload.swarm || (payload.status ? { ...(swarm || {}), ...payload } : null);
                if (nextSwarm) {
                  setSwarm(nextSwarm);
                }
                if (Array.isArray(payload.members)) {
                  setMembers(payload.members);
                  onSwarmUpdated?.(nextSwarm || swarm, payload.members);
                }
              } else if (evtType === 'member_update' && payload.member) {
                const updatedMember: SwarmMember = payload.member;
                setMembers((prev) => {
                  const exists = prev.some((m) => m.name === updatedMember.name);
                  const next = exists
                    ? prev.map((m) => (m.name === updatedMember.name ? { ...m, ...updatedMember } : m))
                    : [...prev, updatedMember];
                  onSwarmUpdated?.(swarm, next);
                  return next;
                });
              } else if (
                evtType === 'message' ||
                evtType === 'board_message' ||
                typeof payload.seq === 'number'
              ) {
                const msg: SwarmMessage = payload.message || payload;
                if (typeof msg.seq === 'number') {
                  mergeIncomingMessages([msg]);
                }
              }
            } catch {
              // 忽略非 JSON 心跳行
            }
          }
        }
      } catch {
        // 忽略主动 abort
      } finally {
        if (active) setSseConnected(false);
      }
    };

    void connectSse();

    const pollTimer = setInterval(() => {
      void fetchSwarmSnapshot(true);
    }, 5000);

    return () => {
      active = false;
      abortController.abort();
      clearInterval(pollTimer);
      setSseConnected(false);
    };
  }, [isOpen, effectiveSwarmId]);

  // 自动滚动至消息板底部
  useEffect(() => {
    if (isOpen && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [isOpen, messages.length]);

  const handleSendIntervention = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const text = inputText.trim();
    if (!text || !effectiveSwarmId || sending) return;

    setSending(true);
    setErrorBanner(null);
    try {
      const res = await apiFetch(
        `/api/v1/swarms/${encodeURIComponent(effectiveSwarmId)}/messages`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text }),
        }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setErrorBanner(err?.detail || `发送插话失败 (HTTP ${res.status})`);
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (data?.message && typeof data.message.seq === 'number') {
        mergeIncomingMessages([data.message]);
      } else {
        await fetchSwarmSnapshot(true);
      }
      setInputText('');
    } catch (err: any) {
      setErrorBanner(err?.message || '发送插话异常');
    } finally {
      setSending(false);
    }
  };

  const handleKillMember = async (memberName: string) => {
    if (!effectiveSwarmId || killingMember) return;
    const confirmed = window.confirm(`确定要终止蜂群节点【${memberName}】吗？终止后该节点将停止当前任务。`);
    if (!confirmed) return;

    setKillingMember(memberName);
    setErrorBanner(null);
    try {
      const res = await apiFetch(
        `/api/v1/swarms/${encodeURIComponent(effectiveSwarmId)}/members/${encodeURIComponent(memberName)}/kill`,
        { method: 'POST' }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setErrorBanner(err?.detail || `终止节点 ${memberName} 失败`);
        return;
      }
      setMembers((prev) => {
        const next = prev.map((m) => (m.name === memberName ? { ...m, status: 'killed' } : m));
        onSwarmUpdated?.(swarm, next);
        return next;
      });
    } catch (err: any) {
      setErrorBanner(err?.message || '终止节点请求异常');
    } finally {
      setKillingMember(null);
    }
  };

  const handleMentionMember = (memberName: string) => {
    setInputText((prev) => {
      const token = `@${memberName} `;
      if (prev.includes(`@${memberName}`)) return prev;
      return prev ? `${prev.trimEnd()} ${token}` : token;
    });
  };

  if (!isOpen) return null;

  const workingCount = members.filter((m) => m.status === 'working').length;
  const waitingCount = members.filter((m) => m.status === 'waiting').length;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/80 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl h-full bg-surface-card border-l border-border shadow-2xl flex flex-col text-foreground select-text"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 顶部标题栏 */}
        <div className="px-5 py-4 border-b border-border flex items-center justify-between bg-surface-100 shrink-0">
          <div className="flex items-center space-x-3 min-w-0">
            <div className="p-2 rounded-xl bg-surface-200 text-foreground border border-border shrink-0">
              <Users className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center space-x-2">
                <h2 className="text-sm font-bold truncate">
                  {swarm?.title || '蜂群实时协作看板'}
                </h2>
                {swarm?.status && (
                  <span
                    className={`text-[10px] px-2 py-0.5 rounded-full font-mono font-semibold border ${
                      swarm.status === 'running'
                        ? 'bg-surface-200 text-emerald-600 dark:text-emerald-400 border-border'
                        : swarm.status === 'completed'
                          ? 'bg-surface-200 text-foreground border-border'
                          : 'bg-surface-200 text-muted-foreground border-border'
                    }`}
                  >
                    {swarm.status}
                  </span>
                )}
                {sseConnected && (
                  <span
                    className="inline-flex items-center space-x-1 text-[10px] text-emerald-500 font-mono"
                    title="SSE 实时事件流已连接"
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    <span>LIVE</span>
                  </span>
                )}
              </div>
              <p className="text-[11px] text-muted-foreground font-mono truncate mt-0.5">
                {effectiveSwarmId ? `Swarm: ${effectiveSwarmId}` : '当前会话暂无活跃蜂群'}
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-1.5 shrink-0">
            {effectiveSwarmId && (
              <button
                type="button"
                onClick={() => void handleDisbandSwarm()}
                disabled={disbanding}
                className="px-2.5 py-1 rounded-lg border border-rose-500/30 bg-rose-500/10 hover:bg-rose-500/20 text-rose-500 text-xs font-medium flex items-center space-x-1 transition-colors disabled:opacity-50"
                title="解散当前蜂群，终止所有节点并恢复单智能体常规对话"
              >
                {disbanding ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <PowerOff className="w-3.5 h-3.5" />
                )}
                <span>解散蜂群</span>
              </button>
            )}
            <button
              type="button"
              onClick={() => void fetchSwarmSnapshot(false)}
              disabled={loading}
              className="p-1.5 rounded-lg hover:bg-surface-200 text-muted-foreground hover:text-foreground transition-colors"
              title="刷新蜂群状态"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-surface-200 text-muted-foreground hover:text-foreground transition-colors"
              title="关闭看板"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 成员状态栏 */}
        <div className="px-5 py-3 border-b border-border bg-surface-50 shrink-0 space-y-2">
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span className="font-semibold text-foreground flex items-center space-x-1.5">
              <Activity className="w-3.5 h-3.5 text-foreground" />
              <span>蜂群节点 ({members.length})</span>
            </span>
            <span className="font-mono">
              工作中 {workingCount} · 待命 {waitingCount}
            </span>
          </div>

          {members.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-40 overflow-y-auto pr-0.5">
              {members.map((member) => {
                const badge = getMemberStatusBadge(member.status);
                const isKilled = member.status === 'killed' || member.status === 'stopped';
                return (
                  <div
                    key={member.name}
                    className="p-2.5 rounded-xl bg-surface-card border border-border flex items-center justify-between gap-2 text-xs shadow-xs"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center space-x-1.5">
                        <span className={`w-2 h-2 rounded-full shrink-0 ${badge.dotClass}`} />
                        <button
                          type="button"
                          onClick={() => handleMentionMember(member.name)}
                          className="font-mono font-bold text-foreground hover:underline truncate transition-colors"
                          title={`点击 @${member.name}`}
                        >
                          @{member.name}
                        </button>
                      </div>
                      <div className="flex items-center space-x-1.5 mt-1">
                        <span
                          className={`text-[10px] px-1.5 py-0.2 rounded border font-medium ${badge.pillClass}`}
                        >
                          {badge.label}
                        </span>
                        {member.model && (
                          <span className="text-[10px] font-mono text-muted-foreground truncate">
                            {member.model}
                          </span>
                        )}
                      </div>
                    </div>

                    {!isKilled && effectiveSwarmId && (
                      <button
                        type="button"
                        onClick={() => void handleKillMember(member.name)}
                        disabled={killingMember === member.name}
                        className="px-2 py-1 rounded-lg border border-border bg-surface-100 hover:bg-surface-200 text-rose-500 text-[10px] font-medium flex items-center space-x-1 shrink-0 transition-colors disabled:opacity-50"
                        title={`终止节点 ${member.name}`}
                      >
                        {killingMember === member.name ? (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        ) : (
                          <Skull className="w-3 h-3" />
                        )}
                        <span>终止</span>
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-xs text-muted-foreground py-2 italic">
              {loading ? '正在同步蜂群成员...' : '暂无已加入的专家节点'}
            </div>
          )}
        </div>

        {/* 错误提示横幅 */}
        {errorBanner && (
          <div className="mx-5 mt-3 px-3 py-2 rounded-xl bg-surface-200 border border-border text-xs text-rose-500 flex items-center space-x-2 shrink-0">
            <ShieldAlert className="w-4 h-4 shrink-0" />
            <span className="flex-1">{errorBanner}</span>
          </div>
        )}

        {/* 消息板实时时间线 */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3 bg-surface-card">
          <div className="flex items-center justify-between text-[11px] text-muted-foreground pb-1">
            <span className="flex items-center space-x-1.5 font-semibold text-foreground">
              <MessageSquare className="w-3.5 h-3.5 text-foreground" />
              <span>共享消息板时间线</span>
            </span>
            <span className="font-mono">{messages.length} 条记录</span>
          </div>

          {messages.length === 0 ? (
            <div className="py-12 text-center text-xs text-muted-foreground space-y-2">
              <Users className="w-8 h-8 mx-auto opacity-30 text-foreground" />
              <p>消息板暂无广播记录，可在下方发送人工插话指令调度专家。</p>
            </div>
          ) : (
            messages.map((msg) => {
              const rawBody = msg.content || msg.text || '';
              const isHuman = msg.sender === 'user' || msg.sender === 'human';
              return (
                <div
                  key={`${msg.seq}_${msg.sender}`}
                  className={`p-3 rounded-2xl border text-xs space-y-1.5 transition-all ${
                    isHuman
                      ? 'bg-surface-50 border-emerald-500/40 text-foreground'
                      : 'bg-surface-100 border-border text-foreground'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center space-x-2 min-w-0">
                      <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-surface-200 text-muted-foreground">
                        #{msg.seq}
                      </span>
                      <button
                        type="button"
                        onClick={() => handleMentionMember(msg.sender)}
                        className={`font-mono font-bold truncate ${
                          isHuman ? 'text-emerald-600 dark:text-emerald-400' : 'text-foreground hover:underline'
                        }`}
                      >
                        {isHuman ? '人工督导 (User)' : `@${msg.sender}`}
                      </button>
                    </div>
                    <span className="text-[10px] font-mono text-muted-foreground shrink-0">
                      {formatTimeShort(msg.created_at)}
                    </span>
                  </div>

                  <div className="text-[12.5px] leading-relaxed text-foreground whitespace-pre-wrap break-words">
                    {renderHighlightedSwarmText(rawBody)}
                  </div>
                </div>
              );
            })
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* 底部人工插话输入框 */}
        <form
          onSubmit={handleSendIntervention}
          className="p-4 border-t border-border bg-surface-100 shrink-0 space-y-2"
        >
          {members.length > 0 && (
            <div className="flex items-center space-x-1.5 overflow-x-auto pb-1">
              <span className="text-[10px] text-muted-foreground shrink-0 flex items-center space-x-0.5">
                <AtSign className="w-3 h-3" />
                <span>快速点名:</span>
              </span>
              {members
                .filter((m) => m.status !== 'killed')
                .map((m) => (
                  <button
                    key={m.name}
                    type="button"
                    onClick={() => handleMentionMember(m.name)}
                    className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-surface-200 hover:bg-surface-300 border border-border text-foreground transition-colors shrink-0"
                  >
                    @{m.name}
                  </button>
                ))}
            </div>
          )}

          <div className="flex items-center space-x-2">
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              disabled={!effectiveSwarmId || sending}
              placeholder={
                effectiveSwarmId
                  ? '输入人工插话指令（如 @专家名 请优先检查边界条件）...'
                  : '请先在对话中启动蜂群协作模式'
              }
              className="flex-1 px-3.5 py-2 rounded-xl bg-surface-200 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-border-hover disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!effectiveSwarmId || !inputText.trim() || sending}
              className="px-4 py-2 rounded-xl bg-foreground text-background hover:opacity-90 text-xs font-semibold flex items-center space-x-1.5 shadow-sm transition-all active:scale-95 disabled:opacity-40 disabled:pointer-events-none"
            >
              {sending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
              <span>广播</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
