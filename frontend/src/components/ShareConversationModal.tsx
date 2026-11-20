'use client';

import React, { useEffect, useState } from 'react';
import { Check, Copy, FileDown, Link2, Loader2, Share2, ShieldOff, X } from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';
import { sanitizeAssistantContent } from '@/utils/toolTagSanitizer';

interface ShareMessageRow {
  id: number | string;
  role: string;
  content: string;
  created_at?: string;
}

interface ShareConversationModalProps {
  conversationId: string | null;
  conversationTitle: string;
  onClose: () => void;
}

/**
 * 简单 HTML 字符转义，确保导出或预览只读 HTML 文件无 XSS 注入风险
 */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * 导出自包含单文件只读 HTML 内容（适配微信/浏览器直接打开阅读）
 */
function buildStandaloneHtml(title: string, rows: ShareMessageRow[]): string {
  const safeTitle = escapeHtml(title || 'AgentHub 对话分享');
  const blocks = rows
    .filter((m) => m.role === 'user' || m.role === 'model' || m.role === 'assistant')
    .map((m) => {
      const isUser = m.role === 'user';
      const raw = isUser
        ? String(m.content || '')
        : sanitizeAssistantContent(String(m.content || ''), { isStreaming: false }).cleanContent;
      if (!raw.trim()) return '';
      const escapedBody = escapeHtml(raw).replace(/\n/g, '<br/>');
      return `<section class="msg ${isUser ? 'msg-user' : 'msg-ai'}">
  <div class="role">${isUser ? '提问' : 'AgentHub'}</div>
  <div class="body">${escapedBody}</div>
</section>`;
    })
    .filter(Boolean)
    .join('\n');

  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>${safeTitle}</title>
  <style>
    :root { color-scheme: light dark; }
    body { margin: 0; padding: 32px 16px; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; background: #131314; color: #e6ece9; line-height: 1.7; }
    @media (prefers-color-scheme: light) { body { background: #f8fafc; color: #1f2421; } .msg-user { background: #e8f5f0 !important; color: #064e3b !important; } .meta { color: #64748b !important; } }
    .wrap { max-width: 780px; margin: 0 auto; }
    h1 { font-size: 20px; margin: 0 0 6px; font-weight: 600; }
    .meta { font-size: 12px; color: #8c9b95; margin-bottom: 28px; }
    .msg { margin-bottom: 20px; }
    .msg-user { background: #282a2c; padding: 12px 16px; border-radius: 16px; margin-left: auto; max-width: 85%; }
    .msg-ai { padding: 4px 0; }
    .role { font-size: 11px; opacity: 0.65; margin-bottom: 4px; font-weight: 600; }
    .body { word-break: break-word; font-size: 15px; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>${safeTitle}</h1>
    <div class="meta">由 AgentHub Local 导出 · 只读快照</div>
    ${blocks || '<p>暂无可展示的对话内容。</p>'}
  </div>
</body>
</html>`;
}

/**
 * 会话只读分享弹窗：支持复制完整 Markdown、导出独立 HTML 以及生成/撤销只读分享链接
 */
export function ShareConversationModal({
  conversationId,
  conversationTitle,
  onClose,
}: ShareConversationModalProps) {
  const [messages, setMessages] = useState<ShareMessageRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [copiedMd, setCopiedMd] = useState(false);
  const [copiedLink, setCopiedLink] = useState(false);
  const [shareToken, setShareToken] = useState<string | null>(null);
  const [shareBusy, setShareBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  useEffect(() => {
    if (!conversationId) return;
    let active = true;
    setLoading(true);
    setErrorMsg('');

    Promise.all([
      apiFetch(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`)
        .then((r) => (r.ok ? r.json() : []))
        .catch(() => []),
      apiFetch(`/api/v1/conversations/${encodeURIComponent(conversationId)}/share`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null),
    ])
      .then(([msgList, shareData]) => {
        if (!active) return;
        setMessages(Array.isArray(msgList) ? msgList : []);
        setShareToken(shareData?.token || null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [conversationId]);

  if (!conversationId) return null;

  const buildMarkdownText = (): string => {
    const lines: string[] = [`# ${conversationTitle || 'AgentHub 对话记录'}`, ''];
    for (const m of messages) {
      if (m.role !== 'user' && m.role !== 'model' && m.role !== 'assistant') continue;
      const body =
        m.role === 'user'
          ? String(m.content || '').trim()
          : sanitizeAssistantContent(String(m.content || ''), { isStreaming: false }).cleanContent.trim();
      if (!body) continue;
      lines.push(m.role === 'user' ? '### 提问' : '### 回复', '', body, '');
    }
    return lines.join('\n');
  };

  const handleCopyMarkdown = () => {
    const md = buildMarkdownText();
    navigator.clipboard.writeText(md).then(() => {
      setCopiedMd(true);
      window.setTimeout(() => setCopiedMd(false), 1800);
    });
  };

  const handleExportHtml = () => {
    const html = buildStandaloneHtml(conversationTitle, messages);
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const safeName = (conversationTitle || 'chat-export').replace(/[\\/:*?"<>|]/g, '_').slice(0, 48);
    a.href = url;
    a.download = `${safeName}.html`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleCreateShareLink = async () => {
    if (!conversationId || shareBusy) return;
    setShareBusy(true);
    setErrorMsg('');
    try {
      const res = await apiFetch(`/api/v1/conversations/${encodeURIComponent(conversationId)}/share`, {
        method: 'POST',
      });
      if (!res.ok) {
        throw new Error('生成分享链接失败');
      }
      const data = await res.json();
      setShareToken(data?.token || null);
    } catch (e: any) {
      setErrorMsg(e?.message || '生成分享链接失败');
    } finally {
      setShareBusy(false);
    }
  };

  const handleRevokeShareLink = async () => {
    if (!conversationId || shareBusy) return;
    setShareBusy(true);
    setErrorMsg('');
    try {
      const res = await apiFetch(`/api/v1/conversations/${encodeURIComponent(conversationId)}/share`, {
        method: 'DELETE',
      });
      if (res.ok) {
        setShareToken(null);
      }
    } finally {
      setShareBusy(false);
    }
  };

  const fullShareUrl =
    shareToken && typeof window !== 'undefined'
      ? `${window.location.origin}/api/v1/conversations/share/${shareToken}`
      : '';

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-[2px] animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl bg-white dark:bg-[#282a2c] text-foreground shadow-2xl p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <Share2 className="w-4 h-4 text-emerald-500 shrink-0" />
            <h3 className="text-sm font-semibold truncate">分享对话内容</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-full hover:bg-black/5 dark:hover:bg-white/10 text-muted-foreground transition-colors"
            aria-label="关闭分享弹窗"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-xs text-muted-foreground truncate">
          当前会话：<span className="text-foreground font-medium">{conversationTitle || '未命名会话'}</span>
        </p>

        {loading ? (
          <div className="flex items-center justify-center py-6 text-xs text-muted-foreground gap-2">
            <Loader2 className="w-4 h-4 animate-spin text-emerald-500" />
            <span>正在加载对话快照...</span>
          </div>
        ) : (
          <div className="space-y-2.5">
            <button
              type="button"
              onClick={handleCopyMarkdown}
              className="w-full flex items-center justify-between px-3.5 py-2.5 rounded-xl bg-black/[0.04] dark:bg-white/[0.06] hover:bg-black/[0.07] dark:hover:bg-white/[0.1] text-xs font-medium transition-colors"
            >
              <span className="flex items-center gap-2.5">
                {copiedMd ? (
                  <Check className="w-4 h-4 text-emerald-500" />
                ) : (
                  <Copy className="w-4 h-4 text-muted-foreground" />
                )}
                <span>{copiedMd ? '已复制完整 Markdown 对话' : '一键复制完整对话（Markdown）'}</span>
              </span>
            </button>

            <button
              type="button"
              onClick={handleExportHtml}
              className="w-full flex items-center justify-between px-3.5 py-2.5 rounded-xl bg-black/[0.04] dark:bg-white/[0.06] hover:bg-black/[0.07] dark:hover:bg-white/[0.1] text-xs font-medium transition-colors"
            >
              <span className="flex items-center gap-2.5">
                <FileDown className="w-4 h-4 text-muted-foreground" />
                <span>导出独立只读 HTML 文件（可直接发微信）</span>
              </span>
            </button>

            <div className="pt-1 border-t border-black/[0.06] dark:border-white/[0.06] space-y-2">
              {!shareToken ? (
                <button
                  type="button"
                  disabled={shareBusy}
                  onClick={() => void handleCreateShareLink()}
                  className="w-full flex items-center justify-center gap-2 px-3.5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium transition-colors disabled:opacity-50"
                >
                  {shareBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Link2 className="w-3.5 h-3.5" />}
                  <span>生成本机/局域网只读分享链接</span>
                </button>
              ) : (
                <div className="space-y-2">
                  <div className="flex items-center gap-1.5 px-2.5 py-2 rounded-xl bg-black/[0.04] dark:bg-black/30 text-[11px] font-mono">
                    <span className="truncate flex-1 select-all">{fullShareUrl}</span>
                    <button
                      type="button"
                      onClick={() => {
                        navigator.clipboard.writeText(fullShareUrl).then(() => {
                          setCopiedLink(true);
                          window.setTimeout(() => setCopiedLink(false), 1800);
                        });
                      }}
                      className="px-2 py-1 rounded-lg bg-emerald-600 text-white font-sans font-medium shrink-0"
                    >
                      {copiedLink ? '已复制' : '复制'}
                    </button>
                  </div>
                  <button
                    type="button"
                    disabled={shareBusy}
                    onClick={() => void handleRevokeShareLink()}
                    className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-xl text-[11px] text-rose-500 hover:bg-rose-500/10 transition-colors"
                  >
                    <ShieldOff className="w-3.5 h-3.5" />
                    <span>撤销只读分享链接</span>
                  </button>
                </div>
              )}
              {errorMsg && <p className="text-[11px] text-rose-500">{errorMsg}</p>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

