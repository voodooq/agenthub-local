'use client';

import React, { useState, useEffect } from 'react';
import {
  X,
  Brain,
  Trash2,
  Plus,
  Edit3,
  Check,
  Sparkles,
  Terminal,
  Puzzle,
  ShieldCheck,
  Pin,
  Cpu,
} from 'lucide-react';
import { LongTermMemoryItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';

interface MemoryHubModalProps {
  isOpen: boolean;
  onClose: () => void;
  onProvidersChanged?: () => void;
}

interface SkillInfo {
  id: string;
  name: string;
  description: string;
  path: string;
}

interface LLMProviderItem {
  id: string;
  name: string;
  provider_type: 'google_pool' | 'google_oauth' | 'gemini_api' | 'openai_compat' | 'anthropic' | 'ollama';
  base_url: string;
  api_key_masked: string;
  has_api_key: boolean;
  has_refresh_token?: boolean;
  cooldown_until?: string | null;
  is_cooling_down?: boolean;
  models: string[];
  role_binding: 'primary' | 'light' | 'both';
  tool_call_mode: 'auto' | 'native' | 'xml_prompt';
  priority: number;
  is_enabled: number;
}

const CATEGORY_MAP: Record<string, { label: string; colorClass: string }> = {
  rule: {
    label: '工程规范',
    colorClass: 'bg-purple-500/15 text-purple-600 dark:text-purple-400 border-purple-500/30',
  },
  preference: {
    label: '个人偏好',
    colorClass: 'bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30',
  },
  env_info: {
    label: '本机环境',
    colorClass: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30',
  },
};

export function MemoryHubModal({ isOpen, onClose, onProvidersChanged }: MemoryHubModalProps) {
  const [activeTab, setActiveTab] = useState<'memories' | 'providers' | 'extensions'>('memories');
  const [memories, setMemories] = useState<LongTermMemoryItem[]>([]);
  const [loading, setLoading] = useState(false);

  // 新增记忆表单
  const [newCategory, setNewCategory] = useState<'preference' | 'rule' | 'env_info'>('preference');
  const [newContent, setNewContent] = useState('');
  const [newPinned, setNewPinned] = useState(false);
  const [isAdding, setIsAdding] = useState(false);

  // 编辑单条记忆状态
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState('');

  // 多源大模型供应商状态
  const [providers, setProviders] = useState<LLMProviderItem[]>([]);
  const [isAddingProvider, setIsAddingProvider] = useState(false);
  const [provName, setProvName] = useState('');
  const [provType, setProvType] = useState<'openai_compat' | 'gemini_api' | 'anthropic' | 'ollama'>('openai_compat');
  const [provBaseUrl, setProvBaseUrl] = useState('https://api.deepseek.com/v1');
  const [provApiKey, setProvApiKey] = useState('');
  const [provModels, setProvModels] = useState('deepseek-chat, deepseek-reasoner');
  const [provRole, setProvRole] = useState<'both' | 'primary' | 'light'>('both');

  // Google OAuth PKCE 授权状态
  const [oauthState, setOauthState] = useState<string>('');
  const [oauthRemark, setOauthRemark] = useState<string>('');
  const [oauthManualUrl, setOauthManualUrl] = useState<string>('');
  const [oauthMessage, setOauthMessage] = useState<string>('');
  const [oauthSubmitting, setOauthSubmitting] = useState<boolean>(false);

  // Skills & MCP
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [mcpServers, setMcpServers] = useState<Record<string, any>>({});

  const fetchMemories = async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/api/v1/memories');
      if (res.ok) {
        const data = await res.json();
        setMemories(data.items || []);
      }
    } catch (e) {
      console.warn('获取长期记忆失败:', e);
    } finally {
      setLoading(false);
    }
  };

  const fetchProviders = async () => {
    try {
      const res = await apiFetch('/api/v1/providers');
      if (res.ok) {
        const data = await res.json();
        setProviders(data.providers || []);
      }
    } catch (e) {}
  };

  const fetchExtensions = async () => {
    try {
      const [sRes, mRes] = await Promise.all([
        apiFetch('/api/v1/extensions/skills'),
        apiFetch('/api/v1/extensions/mcp'),
      ]);
      if (sRes.ok) {
        const sData = await sRes.json();
        setSkills(sData.skills || []);
      }
      if (mRes.ok) {
        const mData = await mRes.json();
        setMcpServers(mData.mcpServers || {});
      }
    } catch (e) {}
  };

  useEffect(() => {
    if (isOpen) {
      fetchMemories();
      fetchProviders();
      fetchExtensions();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleAddMemory = async () => {
    if (!newContent.trim()) return;
    try {
      const res = await apiFetch('/api/v1/memories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category: newCategory,
          content: newContent.trim(),
          confidence: newPinned ? 0.95 : 0.9,
          is_pinned: newPinned ? 1 : 0,
          source: 'manual',
        }),
      });
      if (res.ok) {
        setNewContent('');
        setNewPinned(false);
        setIsAdding(false);
        fetchMemories();
      }
    } catch (e) {}
  };

  const handleTogglePin = async (item: LongTermMemoryItem) => {
    const nextPin = item.is_pinned ? 0 : 1;
    try {
      const res = await apiFetch(`/api/v1/memories/${item.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_pinned: nextPin }),
      });
      if (res.ok) {
        fetchMemories();
      }
    } catch (e) {}
  };

  const handleSaveEdit = async (memId: string) => {
    if (!editingContent.trim()) return;
    try {
      const res = await apiFetch(`/api/v1/memories/${memId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editingContent.trim() }),
      });
      if (res.ok) {
        setEditingId(null);
        fetchMemories();
      }
    } catch (e) {}
  };

  const handleDeleteMemory = async (memId: string) => {
    try {
      const res = await apiFetch(`/api/v1/memories/${memId}`, { method: 'DELETE' });
      if (res.ok) {
        setMemories((prev) => prev.filter((m) => m.id !== memId));
      }
    } catch (e) {}
  };

  const handleClearAll = async () => {
    if (!window.confirm('确定要清空全部跨会话长期记忆吗？此操作不可撤销。')) return;
    try {
      const res = await apiFetch('/api/v1/memories', { method: 'DELETE' });
      if (res.ok) {
        setMemories([]);
      }
    } catch (e) {}
  };

  const handleInitGoogleOAuth = async () => {
    setOauthMessage('');
    try {
      const res = await apiFetch('/api/v1/providers/oauth/init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          remark: oauthRemark.trim() || undefined,
          role_binding: 'both',
          priority: 3,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setOauthState(data.state || '');
        if (data.auth_url) {
          window.open(data.auth_url, '_blank', 'noopener,noreferrer');
          setOauthMessage(
            data.local_server_listening
              ? '已打开 Google 授权页（本地 1455 端口已开启自动回环监听，也可在下方手动粘贴回调 URL）'
              : '已打开 Google 授权页，授权完成后请复制浏览器地址栏的完整 localhost:1455 回调 URL 粘贴至下方'
          );
        }
      }
    } catch (e) {
      setOauthMessage('发起 Google OAuth 授权失败，请检查后端服务状态');
    }
  };

  const handleCompleteManualOAuth = async () => {
    if (!oauthManualUrl.trim()) return;
    setOauthSubmitting(true);
    setOauthMessage('');
    try {
      const res = await apiFetch('/api/v1/providers/oauth/manual-callback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          callback_url: oauthManualUrl.trim(),
          state: oauthState || undefined,
          remark: oauthRemark.trim() || undefined,
          role_binding: 'both',
          priority: 3,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setOauthManualUrl('');
        setOauthRemark('');
        setOauthMessage(`✅ 授权成功！账号已加密存入算力池：${data.provider?.name || 'Google OAuth'}`);
        fetchProviders();
        onProvidersChanged?.();
      } else {
        setOauthMessage(`❌ 授权失败：${data.detail || '请检查回调 URL 是否完整'}`);
      }
    } catch (e) {
      setOauthMessage('❌ 网络异常，无法完成令牌换取');
    } finally {
      setOauthSubmitting(false);
    }
  };

  const handleSaveProvider = async () => {
    if (!provName.trim()) return;
    const modelList = provModels
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      const res = await apiFetch('/api/v1/providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: provName.trim(),
          provider_type: provType,
          base_url: provBaseUrl.trim(),
          api_key: provApiKey.trim(),
          models: modelList,
          role_binding: provRole,
          priority: 2,
          is_enabled: 1,
        }),
      });
      if (res.ok) {
        setProvName('');
        setProvApiKey('');
        setIsAddingProvider(false);
        fetchProviders();
        onProvidersChanged?.();
      }
    } catch (e) {}
  };

  const handleToggleProvider = async (p: LLMProviderItem) => {
    try {
      await apiFetch('/api/v1/providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: p.id,
          name: p.name,
          provider_type: p.provider_type,
          base_url: p.base_url,
          models: p.models,
          role_binding: p.role_binding,
          tool_call_mode: p.tool_call_mode,
          priority: p.priority,
          is_enabled: p.is_enabled ? 0 : 1,
        }),
      });
      fetchProviders();
      onProvidersChanged?.();
    } catch (e) {}
  };

  const handleDeleteProvider = async (id: string) => {
    try {
      const res = await apiFetch(`/api/v1/providers/${id}`, { method: 'DELETE' });
      if (res.ok) {
        fetchProviders();
        onProvidersChanged?.();
      }
    } catch (e) {}
  };

  const handleToggleMcpServer = async (srvName: string) => {
    const nextServers = {
      ...mcpServers,
      [srvName]: {
        ...mcpServers[srvName],
        enabled: !mcpServers[srvName]?.enabled,
      },
    };
    setMcpServers(nextServers);
    try {
      await apiFetch('/api/v1/extensions/mcp', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mcpServers: nextServers }),
      });
    } catch (e) {}
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-fade-in">
      <div className="w-full max-w-3xl max-h-[86vh] flex flex-col rounded-2xl bg-surface-card border border-border shadow-2xl overflow-hidden">
        {/* 顶部标题栏 */}
        <div className="px-6 py-4 border-b border-border flex items-center justify-between bg-surface-100/60">
          <div className="flex items-center space-x-2.5">
            <div className="p-2 rounded-xl bg-blue-500/15 text-blue-600 dark:text-blue-400">
              <Brain className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-[15px] font-bold text-foreground">
                记忆中枢 · 多源算力池 · Skill/MCP 扩展总线
              </h2>
              <p className="text-xs text-muted-foreground">
                SQLite FTS5 长期记忆画像 · OpenAI/DeepSeek/Ollama 多协议接入 · 按需技能与常驻 MCP
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* 顶部 Tab 切换 */}
        <div className="px-6 pt-3 border-b border-border flex items-center justify-between bg-surface-100/30">
          <div className="flex space-x-5">
            <button
              onClick={() => setActiveTab('memories')}
              className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors flex items-center space-x-1.5 ${
                activeTab === 'memories'
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>长期记忆画像 ({memories.length})</span>
            </button>

            <button
              onClick={() => setActiveTab('providers')}
              className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors flex items-center space-x-1.5 ${
                activeTab === 'providers'
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Cpu className="w-3.5 h-3.5" />
              <span>多源大模型 API ({providers.length})</span>
            </button>

            <button
              onClick={() => setActiveTab('extensions')}
              className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors flex items-center space-x-1.5 ${
                activeTab === 'extensions'
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Puzzle className="w-3.5 h-3.5" />
              <span>Skill 技能包与 MCP ({skills.length})</span>
            </button>
          </div>

          {activeTab === 'memories' && (
            <div className="flex items-center space-x-2 pb-2">
              <button
                onClick={() => setIsAdding(!isAdding)}
                className="flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium transition-colors"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>新增画像</span>
              </button>
              {memories.length > 0 && (
                <button
                  onClick={handleClearAll}
                  className="flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>清空全部</span>
                </button>
              )}
            </div>
          )}

          {activeTab === 'providers' && (
            <div className="pb-2">
              <button
                onClick={() => setIsAddingProvider(!isAddingProvider)}
                className="flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium transition-colors"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>接入自定义 API / Ollama</span>
              </button>
            </div>
          )}
        </div>

        {/* 内容区 */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {activeTab === 'memories' && (
            <>
              {/* 手动新增长期记忆表单 */}
              {isAdding && (
                <div className="p-4 rounded-xl bg-surface-100 border border-border space-y-3">
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div className="flex items-center space-x-2">
                      <span className="text-xs font-medium text-muted-foreground">记忆分类:</span>
                      {(['preference', 'rule', 'env_info'] as const).map((cat) => (
                        <button
                          key={cat}
                          type="button"
                          onClick={() => setNewCategory(cat)}
                          className={`px-2.5 py-1 rounded-lg text-xs border transition-all ${
                            newCategory === cat
                              ? CATEGORY_MAP[cat].colorClass + ' font-semibold'
                              : 'border-border text-muted-foreground hover:text-foreground'
                          }`}
                        >
                          {CATEGORY_MAP[cat].label}
                        </button>
                      ))}
                    </div>

                    <label className="flex items-center space-x-1.5 text-xs text-foreground cursor-pointer">
                      <input
                        type="checkbox"
                        checked={newPinned}
                        onChange={(e) => setNewPinned(e.target.checked)}
                      />
                      <span>📌 永久置顶（豁免 30 天未命中衰减）</span>
                    </label>
                  </div>
                  <div className="flex items-center space-x-2">
                    <input
                      type="text"
                      value={newContent}
                      onChange={(e) => setNewContent(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleAddMemory();
                      }}
                      placeholder="例如：前端状态管理统一使用 Zustand，禁止使用 Redux..."
                      className="flex-1 px-3 py-2 rounded-xl bg-surface-card border border-border text-xs text-foreground outline-none focus:border-blue-500"
                    />
                    <button
                      type="button"
                      onClick={handleAddMemory}
                      className="px-3.5 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium transition-colors"
                    >
                      保存
                    </button>
                  </div>
                </div>
              )}

              {/* 记忆列表 */}
              {loading ? (
                <div className="text-center py-12 text-xs text-muted-foreground">
                  正在加载本地长期记忆库...
                </div>
              ) : memories.length === 0 ? (
                <div className="text-center py-12 space-y-2">
                  <p className="text-sm font-medium text-foreground">暂无跨会话长期记忆画像</p>
                  <p className="text-xs text-muted-foreground max-w-md mx-auto">
                    您可以在上方手动点击「新增画像」，或通过侧边栏「数据迁移与导入」导入 GEMINI.md / ChatGPT 记忆库。
                  </p>
                </div>
              ) : (
                <div className="space-y-2.5">
                  {memories.map((item) => {
                    const catMeta = CATEGORY_MAP[item.category] || CATEGORY_MAP.preference;
                    const confPct = Math.round((item.confidence || 0.8) * 100);
                    const isEditing = editingId === item.id;

                    return (
                      <div
                        key={item.id}
                        className="p-3.5 rounded-xl bg-surface-100/70 hover:bg-surface-100 border border-border/80 flex items-start justify-between gap-3 transition-colors"
                      >
                        <div className="flex-1 min-w-0 space-y-1.5">
                          <div className="flex items-center flex-wrap gap-2">
                            <span
                              className={`px-2 py-0.5 rounded-md text-[11px] border font-medium ${catMeta.colorClass}`}
                            >
                              {catMeta.label}
                            </span>
                            {item.is_pinned ? (
                              <span className="px-2 py-0.5 rounded-md text-[10px] bg-amber-500/15 text-amber-600 dark:text-amber-400 font-semibold">
                                📌 置顶免衰减
                              </span>
                            ) : null}
                            <span className="text-[11px] text-muted-foreground font-mono">
                              置信度 {confPct}% · 已召回 {item.hit_count || 0} 次
                              {item.source ? ` · 来源: ${item.source}` : ''}
                            </span>
                          </div>

                          {isEditing ? (
                            <div className="flex items-center space-x-2 pt-1">
                              <input
                                type="text"
                                value={editingContent}
                                onChange={(e) => setEditingContent(e.target.value)}
                                className="flex-1 px-2.5 py-1.5 rounded-lg bg-surface-card border border-blue-500 text-xs text-foreground outline-none"
                              />
                              <button
                                onClick={() => handleSaveEdit(item.id)}
                                className="p-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500"
                                title="确认修改"
                              >
                                <Check className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          ) : (
                            <p className="text-[13px] text-foreground break-anywhere leading-relaxed">
                              {item.content}
                            </p>
                          )}
                        </div>

                        <div className="flex items-center space-x-1 shrink-0">
                          <button
                            onClick={() => handleTogglePin(item)}
                            className={`p-1.5 rounded-lg transition-colors ${
                              item.is_pinned
                                ? 'text-amber-500 bg-amber-500/10'
                                : 'text-muted-foreground hover:text-foreground hover:bg-surface-200'
                            }`}
                            title={item.is_pinned ? '取消置顶' : '置顶此条记忆（豁免 30 天衰减）'}
                          >
                            <Pin className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => {
                              setEditingId(item.id);
                              setEditingContent(item.content);
                            }}
                            className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
                            title="编辑此条记忆"
                          >
                            <Edit3 className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => handleDeleteMemory(item.id)}
                            className="p-1.5 rounded-lg text-muted-foreground hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
                            title="删除此条记忆"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}

          {/* ────────── TAB 2: 多协议大模型供应商池 ────────── */}
          {activeTab === 'providers' && (
            <div className="space-y-4">
              {/* Google 官方桌面端 PKCE OAuth 授权接入卡片 */}
              <div className="p-4 rounded-xl bg-blue-500/5 border border-blue-500/25 space-y-3 text-xs">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <div>
                    <h4 className="font-bold text-foreground flex items-center space-x-1.5">
                      <span>🔐 Google 官方 OAuth 算力池接入（免 GCP 项目门槛 · 标准 PKCE 安全流）</span>
                    </h4>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      支持本机 1455 端口自动回环捕获 + 远程/指纹浏览器手动粘贴回调 URL 双模兜底，遇到 429 自动 &lt;50ms 冷却接力
                    </p>
                  </div>
                  <div className="flex items-center space-x-2">
                    <input
                      type="text"
                      value={oauthRemark}
                      onChange={(e) => setOauthRemark(e.target.value)}
                      placeholder="账号备注(可选，如主力号A)"
                      className="px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-foreground outline-none w-40"
                    />
                    <button
                      type="button"
                      onClick={handleInitGoogleOAuth}
                      className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold transition-colors shrink-0"
                    >
                      🔗 发起 Google OAuth 授权
                    </button>
                  </div>
                </div>

                <div className="flex items-center space-x-2">
                  <input
                    type="text"
                    value={oauthManualUrl}
                    onChange={(e) => setOauthManualUrl(e.target.value)}
                    placeholder="模式 B 手动兜底：粘贴浏览器跳转后的完整回调 URL (http://localhost:1455/auth/callback?code=...&state=...)"
                    className="flex-1 px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-foreground font-mono outline-none"
                  />
                  <button
                    type="button"
                    disabled={oauthSubmitting || !oauthManualUrl.trim()}
                    onClick={handleCompleteManualOAuth}
                    className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-semibold transition-colors shrink-0"
                  >
                    {oauthSubmitting ? '换取令牌中...' : '确认完成授权'}
                  </button>
                </div>

                {oauthMessage && (
                  <p className="text-[11px] text-blue-600 dark:text-blue-400 font-medium">
                    {oauthMessage}
                  </p>
                )}
              </div>

              {isAddingProvider && (
                <div className="p-4 rounded-xl bg-surface-100 border border-border space-y-3 text-xs">
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div>
                      <label className="block text-muted-foreground mb-1">通道名称</label>
                      <input
                        type="text"
                        value={provName}
                        onChange={(e) => setProvName(e.target.value)}
                        placeholder="如 DeepSeek 官方 / 本地 Ollama"
                        className="w-full px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-foreground outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-muted-foreground mb-1">协议类型</label>
                      <select
                        value={provType}
                        onChange={(e) => {
                          const v = e.target.value as any;
                          setProvType(v);
                          if (v === 'openai_compat') setProvBaseUrl('https://api.deepseek.com/v1');
                          if (v === 'ollama') {
                            setProvBaseUrl('http://127.0.0.1:11434/v1');
                            setProvModels('qwen2.5-coder:14b, deepseek-r1:14b');
                          }
                          if (v === 'anthropic') setProvBaseUrl('https://api.anthropic.com/v1');
                          if (v === 'gemini_api') setProvBaseUrl('');
                        }}
                        className="w-full px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-foreground outline-none"
                      >
                        <option value="openai_compat">OpenAI 兼容 (DeepSeek / 硅基流动 / OpenAI)</option>
                        <option value="ollama">Ollama 本地模型 (http://127.0.0.1:11434/v1)</option>
                        <option value="gemini_api">Google AI Studio API Key</option>
                        <option value="anthropic">Anthropic Claude API</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-muted-foreground mb-1">角色绑定策略</label>
                      <select
                        value={provRole}
                        onChange={(e) => setProvRole(e.target.value as any)}
                        className="w-full px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-foreground outline-none"
                      >
                        <option value="both">🧠+⚡ 主力推理 + 后台记忆通用</option>
                        <option value="primary">🧠 仅作为主力对话与 Agent 推理</option>
                        <option value="light">⚡ 仅作为后台轻量记忆提炼与摘要</option>
                      </select>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-muted-foreground mb-1">
                        Base URL (本地 Ollama 填 http://127.0.0.1:11434/v1)
                      </label>
                      <input
                        type="text"
                        value={provBaseUrl}
                        onChange={(e) => setProvBaseUrl(e.target.value)}
                        className="w-full px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-foreground font-mono outline-none"
                      />
                    </div>
                    <div>
                      <label className="block text-muted-foreground mb-1">
                        API Key (本地加密存储，Ollama 可留空)
                      </label>
                      <input
                        type="password"
                        value={provApiKey}
                        onChange={(e) => setProvApiKey(e.target.value)}
                        placeholder="sk-..."
                        className="w-full px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-foreground font-mono outline-none"
                      />
                    </div>
                  </div>

                  <div className="flex items-center justify-between gap-3">
                    <div className="flex-1">
                      <label className="block text-muted-foreground mb-1">
                        可用模型 ID 列表（逗号分隔，保存后将直接出现在聊天模型选择器中）
                      </label>
                      <input
                        type="text"
                        value={provModels}
                        onChange={(e) => setProvModels(e.target.value)}
                        placeholder="deepseek-chat, qwen2.5-coder:14b, gpt-4o"
                        className="w-full px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-foreground font-mono outline-none"
                      />
                    </div>
                    <button
                      type="button"
                      onClick={handleSaveProvider}
                      className="mt-5 px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-semibold shrink-0"
                    >
                      保存通道
                    </button>
                  </div>
                </div>
              )}

              <div className="space-y-2.5">
                {providers.map((p) => (
                  <div
                    key={p.id}
                    className="p-3.5 rounded-xl bg-surface-100/70 border border-border flex items-center justify-between gap-3"
                  >
                    <div className="space-y-1 min-w-0">
                      <div className="flex items-center flex-wrap gap-2">
                        <span className="text-xs font-bold text-foreground">{p.name}</span>
                        <span className="px-2 py-0.5 rounded bg-blue-500/15 text-blue-600 dark:text-blue-400 text-[10px] font-mono">
                          {p.provider_type}
                        </span>
                        <span className="px-2 py-0.5 rounded bg-purple-500/15 text-purple-600 dark:text-purple-400 text-[10px]">
                          职责: {p.role_binding === 'primary' ? '主力推理' : p.role_binding === 'light' ? '后台轻量提炼' : '主力+后台通用'}
                        </span>
                        {p.is_cooling_down ? (
                          <span className="px-2 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400 text-[10px] font-semibold">
                            ⏳ 429 冷却至 {p.cooldown_until}
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 text-[10px]">
                            ● 就绪 (优先级 {p.priority})
                          </span>
                        )}
                      </div>
                      <div className="text-[11px] text-muted-foreground font-mono truncate">
                        模型: {(p.models || []).join(', ')}
                        {p.base_url ? ` · Endpoint: ${p.base_url}` : ''}
                        {p.api_key_masked ? ` · Key: ${p.api_key_masked}` : ''}
                      </div>
                    </div>

                    <div className="flex items-center space-x-2 shrink-0">
                      <button
                        type="button"
                        onClick={() => handleToggleProvider(p)}
                        className={`px-3 py-1 rounded-lg text-xs font-medium ${
                          p.is_enabled
                            ? 'bg-emerald-600 text-white'
                            : 'bg-surface-200 text-muted-foreground'
                        }`}
                      >
                        {p.is_enabled ? '已启用' : '已停用'}
                      </button>
                      {p.id !== 'provider_google_pool' && (
                        <button
                          type="button"
                          onClick={() => handleDeleteProvider(p.id)}
                          className="p-1.5 rounded-lg text-muted-foreground hover:text-rose-500 hover:bg-rose-500/10"
                          title="删除该自定义通道"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ────────── TAB 3: Skill & MCP 扩展 ────────── */}
          {activeTab === 'extensions' && (
            <div className="space-y-6">
              {/* Skills 区域 */}
              <div className="space-y-2.5">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-bold text-foreground flex items-center space-x-1.5">
                    <Terminal className="w-3.5 h-3.5 text-blue-500" />
                    <span>按需专业技能包 (Workspace Skills)</span>
                  </h3>
                  <span className="text-[11px] text-muted-foreground">
                    目录: ./skills/*/SKILL.md（平时仅加载摘要，按需注入全文）
                  </span>
                </div>
                <div className="grid grid-cols-1 gap-2.5">
                  {skills.map((sk) => (
                    <div
                      key={sk.id}
                      className="p-3.5 rounded-xl bg-surface-100/70 border border-border flex items-start justify-between"
                    >
                      <div className="space-y-1">
                        <div className="flex items-center space-x-2">
                          <span className="text-xs font-bold text-foreground font-mono">
                            {sk.name}
                          </span>
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
                            已就绪
                          </span>
                        </div>
                        <p className="text-xs text-muted-foreground">{sk.description}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* MCP Servers 区域 */}
              <div className="space-y-2.5">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-bold text-foreground flex items-center space-x-1.5">
                    <ShieldCheck className="w-3.5 h-3.5 text-purple-500" />
                    <span>MCP 外部扩展服务池 (data/mcp_servers.json)</span>
                  </h3>
                  <span className="text-[11px] text-muted-foreground">
                    常驻 JSON-RPC 连接池 · 随应用退出自动清理进程树
                  </span>
                </div>
                <div className="space-y-2">
                  {Object.entries(mcpServers).map(([srvName, cfg]: [string, any]) => (
                    <div
                      key={srvName}
                      className="p-3.5 rounded-xl bg-surface-100/70 border border-border flex items-center justify-between"
                    >
                      <div className="space-y-1">
                        <div className="flex items-center space-x-2">
                          <span className="text-xs font-bold text-foreground font-mono">
                            {srvName}
                          </span>
                          <span className="text-[11px] text-muted-foreground font-mono bg-surface-200 px-1.5 py-0.5 rounded">
                            {cfg.command} {(cfg.args || []).join(' ')}
                          </span>
                        </div>
                        {cfg.description && (
                          <p className="text-xs text-muted-foreground">{cfg.description}</p>
                        )}
                      </div>

                      <button
                        type="button"
                        onClick={() => handleToggleMcpServer(srvName)}
                        className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                          cfg.enabled
                            ? 'bg-emerald-600 text-white'
                            : 'bg-surface-200 text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        {cfg.enabled ? '已启用' : '未启用'}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
