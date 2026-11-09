'use client';

import React, { useState, useEffect, useRef } from 'react';
import { X, Upload, Link2, Bookmark, Check, Copy } from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface SyncModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (newConvId?: string) => void;
}

export function SyncModal({ isOpen, onClose, onSuccess }: SyncModalProps) {
  const [shareUrl, setShareUrl] = useState('');
  const [cloning, setCloning] = useState(false);
  const [copiedBookmarklet, setCopiedBookmarklet] = useState(false);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCloseRef.current();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen]);

  if (!isOpen) return null;

  const handleCloneShare = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!shareUrl.trim()) return;
    setCloning(true);
    try {
      const resp = await apiFetch('/api/v1/sync/share-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: shareUrl.trim() }),
      });
      if (resp.ok) {
        const data = await resp.json();
        setShareUrl('');
        onSuccess(data.conversation_id);
        onClose();
      } else {
        const errData = await resp.json().catch(() => ({}));
        alert(errData.detail || '抓取公开分享链接失败，请确认链接格式或网络连接');
      }
    } catch (e: any) {
      alert(`网络请求异常: ${e.message || '无法连接到本地服务'}`);
    } finally {
      setCloning(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    try {
      const resp = await apiFetch('/api/v1/sync/takeout', {
        method: 'POST',
        body: formData,
      });
      if (resp.ok) {
        const res = await resp.json();
        alert("导入成功！已归档 " + res.conversations_imported + " 个会话，" + res.messages_imported + " 条历史消息。");
        onSuccess();
        onClose();
      } else {
        const errData = await resp.json().catch(() => ({}));
        alert(errData.detail || '解析 Takeout 文件失败');
      }
    } catch (e: any) {
      alert(`上传发生错误: ${e.message || '网络异常'}`);
    }
  };

  const bookmarkletCode = "javascript:(function(){const title=document.title;const msgs=Array.from(document.querySelectorAll('.conversation-container, [data-message-author-role]')).map(el=>({role:el.getAttribute('data-message-author-role')||'user',content:el.innerText}));fetch('http://localhost:8686/api/v1/sync/web',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,messages:msgs})}).then(()=>alert('已一键同步至 AgentHub Local!')).catch(()=>alert('同步失败，请检查服务是否启动'));})();";

  const copyBookmarklet = () => {
    navigator.clipboard.writeText(bookmarkletCode);
    setCopiedBookmarklet(true);
    setTimeout(() => setCopiedBookmarklet(false), 2000);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-2xl glass-modal overflow-hidden shadow-2xl border border-white/10 animate-scale-in"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 bg-surface-100/50">
          <h3 className="text-base font-semibold text-slate-100">历史会话全量同步管道 (3 种途径)</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-white/10 text-slate-400 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-5 text-xs text-slate-300">
          <div className="p-4 rounded-xl bg-surface-50/40 border border-white/10 space-y-2.5">
            <div className="flex items-center space-x-2 font-semibold text-slate-200">
              <Link2 className="w-4 h-4 text-emerald-400" />
              <span>途径 1：公开分享链接一键克隆 (可续聊分支)</span>
            </div>
            <p className="text-slate-400 leading-relaxed">
              输入在网页版 Gemini 生成的公开分享链接（形如 https://share.gemini.google/xxx 或 https://g.co/gemini/share/xxx），1 秒克隆至本地：
            </p>
            <form onSubmit={handleCloneShare} className="flex space-x-2">
              <input
                type="url"
                value={shareUrl}
                onChange={(e) => setShareUrl(e.target.value)}
                placeholder="https://share.gemini.google/..."
                className="flex-1 px-3 py-2 rounded-lg bg-surface-200 border border-white/10 text-slate-100 placeholder-slate-500 outline-none focus:border-emerald-500"
              />
              <button
                type="submit"
                disabled={cloning}
                className="px-4 py-2 rounded-lg bg-accent hover:bg-accent-hover text-white font-medium disabled:opacity-50"
              >
                {cloning ? '克隆中...' : '克隆入库'}
              </button>
            </form>
          </div>

          <div className="p-4 rounded-xl bg-surface-50/40 border border-white/10 space-y-2.5">
            <div className="flex items-center space-x-2 font-semibold text-slate-200">
              <Upload className="w-4 h-4 text-emerald-400" />
              <span>途径 2：Google Takeout 批量搬迁 (MyActivity.json)</span>
            </div>
            <p className="text-slate-400 leading-relaxed">
              首次全量导入过去 1 年的历史对话，自动去除网页噪点并生成 FTS5 倒排索引：
            </p>
            <label className="flex items-center justify-center p-4 border-2 border-dashed border-white/10 hover:border-emerald-500/50 rounded-xl cursor-pointer transition-colors bg-surface-200/40">
              <input type="file" accept=".json" onChange={handleFileUpload} className="hidden" />
              <span className="text-slate-400 hover:text-slate-200">点击选择或拖放 MyActivity.json</span>
            </label>
          </div>

          <div className="p-4 rounded-xl bg-surface-50/40 border border-white/10 space-y-2.5">
            <div className="flex items-center space-x-2 font-semibold text-slate-200">
              <Bookmark className="w-4 h-4 text-amber-400" />
              <span>途径 3：网页版 Gemini 一键推送书签脚本</span>
            </div>
            <p className="text-slate-400 leading-relaxed">
              将下方脚本拖入浏览器书签栏。在 gemini.google.com 浏览时点击一下，即可直推保存回本地：
            </p>
            <button
              onClick={copyBookmarklet}
              className="w-full flex items-center justify-center space-x-2 py-2 rounded-lg bg-surface-200 hover:bg-white/10 border border-white/10 text-slate-200 transition-colors"
            >
              {copiedBookmarklet ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
              <span>{copiedBookmarklet ? '已复制 Bookmarklet 代码' : '一键复制 Bookmarklet 书签脚本'}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
