'use client';

import React, { useState, useEffect } from 'react';
import {
  X,
  Cloud,
  HardDrive,
  Database,
  Check,
  AlertCircle,
  RefreshCw,
  Trash2,
  PlugZap,
  Sparkles,
  Layers,
  Search,
  RotateCcw,
  Scissors,
  CheckCircle2,
  Server,
  BookOpen,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface LLMWikiDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  workspaceRoot?: string;
}

export interface CloudMountItem {
  mount_id: string;
  mount_name: string;
  provider_type: 'wecom' | 'webdav' | 'oss';
  authority_tier: number;
  config: Record<string, any>;
}

export interface KnowledgeBaseItem {
  kb_id: string;
  name: string;
  description: string;
  ownership_type: string;
  source_id?: string;
  source_name?: string;
  active_version: string;
  chunk_count: number;
  triple_count: number;
  disk_size_bytes: number;
  updated_at: number;
}

export function LLMWikiDrawer({ isOpen, onClose, workspaceRoot = '.' }: LLMWikiDrawerProps) {
  // 主分段：1 = 知识库挂载, 2 = 知识库训练
  const [activeSection, setActiveSection] = useState<'mount' | 'train'>('mount');

  // ── 挂载部分状态 ──
  const [providerTab, setProviderTab] = useState<'wecom' | 'webdav' | 'oss'>('wecom');
  const [mountName, setMountName] = useState('');
  const [authorityTier, setAuthorityTier] = useState<number>(1);
  const [mountsList, setMountsList] = useState<CloudMountItem[]>([]);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // 表单状态
  const [wecomForm, setWecomForm] = useState({ corpid: '', corpsecret: '', space_id: '' });
  const [webdavForm, setWebdavForm] = useState({ base_url: '', username: '', password: '', root_path: '/' });
  const [ossForm, setOssForm] = useState({ endpoint: '', bucket: '', access_key: '', secret_key: '', prefix: '' });

  // ── 训练部分状态 ──
  const [training, setTraining] = useState(false);
  const [trainStatusMsg, setTrainStatusMsg] = useState<string | null>(null);
  const [kbsList, setKbsList] = useState<KnowledgeBaseItem[]>([]);
  const [playgroundQuery, setPlaygroundQuery] = useState('');
  const [playgroundTesting, setPlaygroundTesting] = useState(false);
  const [playgroundHits, setPlaygroundHits] = useState<any[] | null>(null);
  const [selectedKbId, setSelectedKbId] = useState<string>('');

  useEffect(() => {
    if (isOpen) {
      loadMounts();
      loadKBs();
      setTestResult(null);
      setTrainStatusMsg(null);
    }
  }, [isOpen]);

  const loadMounts = async () => {
    try {
      const resp = await apiFetch('/api/v1/knowledge/mounts');
      if (resp.ok) {
        const json = await resp.json();
        setMountsList(json.data || []);
      }
    } catch (e) {
      console.error('加载挂载源失败:', e);
    }
  };

  const loadKBs = async () => {
    try {
      const resp = await apiFetch('/api/v1/kb/bases');
      if (resp.ok) {
        const json = await resp.json();
        const list: KnowledgeBaseItem[] = json.data || [];
        setKbsList(list);
        if (list.length > 0 && !selectedKbId) {
          setSelectedKbId(list[0].kb_id);
        }
      }
    } catch (e) {
      console.error('加载知识库资产失败:', e);
    }
  };

  if (!isOpen) return null;

  const getCurrentConfig = () => {
    if (providerTab === 'wecom') return wecomForm;
    if (providerTab === 'webdav') return webdavForm;
    return ossForm;
  };

  // 测试连通性
  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const resp = await apiFetch('/api/v1/knowledge/mounts/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mount_id: `test_${Date.now()}`,
          mount_name: mountName || '测试源',
          provider_type: providerTab,
          authority_tier: authorityTier,
          config: getCurrentConfig(),
        }),
      });
      const data = await resp.json();
      setTestResult({ success: data.success, message: data.message });
    } catch (e: any) {
      setTestResult({ success: false, message: e.message || '网络请求失败' });
    } finally {
      setTesting(false);
    }
  };

  // 保存挂载源
  const handleSaveMount = async () => {
    if (!mountName.trim()) {
      alert('请输入挂载展示名称');
      return;
    }
    setSaving(true);
    try {
      const mountId = `mnt_${Date.now()}`;
      const resp = await apiFetch('/api/v1/knowledge/mounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mount_id: mountId,
          mount_name: mountName.trim(),
          provider_type: providerTab,
          authority_tier: authorityTier,
          config: getCurrentConfig(),
        }),
      });
      if (resp.ok) {
        setMountName('');
        setTestResult(null);
        await loadMounts();
        alert('挂载源已保存！可切换至【知识库训练】开始切片与构建索引。');
      }
    } catch (e) {
      console.error('保存挂载源失败:', e);
    } finally {
      setSaving(false);
    }
  };

  // 删除挂载源
  const handleDeleteMount = async (mountId: string) => {
    if (!confirm('确定要移除此云端挂载源吗？')) return;
    try {
      await apiFetch(`/api/v1/knowledge/mounts/${mountId}`, { method: 'DELETE' });
      await loadMounts();
    } catch (e) {
      console.error('删除挂载失败:', e);
    }
  };

  // 触发增量训练/同步
  const handleTriggerTrain = async (targetMountId?: string) => {
    setTraining(true);
    setTrainStatusMsg(null);
    try {
      const resp = await apiFetch('/api/v1/knowledge/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspace_root: workspaceRoot,
          mount_id: targetMountId || null,
        }),
      });
      const data = await resp.json();
      setTrainStatusMsg(data.message || '训练同步完成');
      await loadKBs();
      await loadMounts();
    } catch (e: any) {
      setTrainStatusMsg('训练失败: ' + (e.message || '网络异常'));
    } finally {
      setTraining(false);
    }
  };

  // 检索命中率实时测试台 (Playground)
  const handleTestPlayground = async () => {
    if (!playgroundQuery.trim()) return;
    if (!selectedKbId && kbsList.length === 0) {
      alert('请先挂载并训练至少一个知识库');
      return;
    }
    const kbId = selectedKbId || (kbsList[0]?.kb_id ?? '');
    setPlaygroundTesting(true);
    setPlaygroundHits(null);
    try {
      const resp = await apiFetch(`/api/v1/kb/bases/${kbId}/playground`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: playgroundQuery.trim(),
          top_k: 5,
        }),
      });
      const data = await resp.json();
      setPlaygroundHits(data.chunks || []);
    } catch (e: any) {
      console.error('Playground 测试异常:', e);
    } finally {
      setPlaygroundTesting(false);
    }
  };

  // 磁盘瘦身清理
  const handlePruneKb = async (kbId: string) => {
    if (!confirm('是否清理该知识库的历史旧版本快照（保留最新 3 个版本）并执行 VACUUM 释放磁盘？')) return;
    try {
      const resp = await apiFetch(`/api/v1/kb/bases/${kbId}/prune`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keep_count: 3 }),
      });
      const data = await resp.json();
      alert(`瘦身完成！共释放磁盘空间: ${data.freed_mb || 0} MB`);
      await loadKBs();
    } catch (e: any) {
      alert('清理异常: ' + e.message);
    }
  };

  // 彻底删除知识库
  const handleDeleteKb = async (kbId: string, kbName: string) => {
    if (!confirm(`确定要彻底删除独立知识库【${kbName}】吗？\n该操作将物理销毁其所有历史版本与向量数据库。`)) return;
    try {
      const resp = await apiFetch(`/api/v1/kb/bases/${kbId}`, { method: 'DELETE' });
      if (resp.ok) {
        if (selectedKbId === kbId) {
          const remaining = kbsList.filter((k) => k.kb_id !== kbId);
          setSelectedKbId(remaining[0]?.kb_id || '');
          setPlaygroundHits(null);
        }
        await loadKBs();
      }
    } catch (e: any) {
      alert('删除失败: ' + e.message);
    }
  };

  return (
    <aside className="fixed inset-y-0 right-0 w-full md:w-[500px] lg:w-[560px] bg-surface-card border-l border-border shadow-2xl z-40 flex flex-col animate-fade-in text-foreground">
      {/* 顶部 Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-surface-100">
        <div className="flex items-center space-x-2 min-w-0">
          <BookOpen className="w-4 h-4 text-sky-500 shrink-0" />
          <h2 className="text-sm font-semibold text-foreground truncate">LLMWiki 知识中枢</h2>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-500 border border-sky-500/20 font-mono">
            v1.5.8
          </span>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
          title="关闭右侧面板"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* 核心双分段切换 Tabs: 1. 知识库挂载 / 2. 知识库训练 */}
      <div className="grid grid-cols-2 p-2 gap-1 border-b border-border bg-surface-100/50">
        <button
          type="button"
          onClick={() => setActiveSection('mount')}
          className={`flex items-center justify-center gap-1.5 py-1.5 px-3 rounded-lg text-xs font-medium transition-all ${
            activeSection === 'mount'
              ? 'bg-surface-card text-foreground shadow-sm border border-border font-semibold'
              : 'text-muted-foreground hover:text-foreground hover:bg-surface-200/50'
          }`}
        >
          <HardDrive className="w-3.5 h-3.5 text-sky-500" />
          <span>1. 知识库挂载</span>
          <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-muted text-muted-foreground">
            {mountsList.length}
          </span>
        </button>

        <button
          type="button"
          onClick={() => setActiveSection('train')}
          className={`flex items-center justify-center gap-1.5 py-1.5 px-3 rounded-lg text-xs font-medium transition-all ${
            activeSection === 'train'
              ? 'bg-surface-card text-foreground shadow-sm border border-border font-semibold'
              : 'text-muted-foreground hover:text-foreground hover:bg-surface-200/50'
          }`}
        >
          <Sparkles className="w-3.5 h-3.5 text-amber-500" />
          <span>2. 知识库训练</span>
          <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-muted text-muted-foreground">
            {kbsList.length}
          </span>
        </button>
      </div>

      {/* 抽屉滚动内容区 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* ========================================================= */}
        {/* 第一部分：知识库挂载                                      */}
        {/* ========================================================= */}
        {activeSection === 'mount' && (
          <div className="space-y-4 animate-fade-in">
            {/* 存储提供商选择 Pills */}
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1.5 block">选择云端存储类型</label>
              <div className="grid grid-cols-3 gap-1 bg-surface-200/50 p-1 rounded-lg border border-border">
                <button
                  type="button"
                  onClick={() => {
                    setProviderTab('wecom');
                    setTestResult(null);
                  }}
                  className={`flex items-center justify-center gap-1 py-1 rounded text-xs transition-colors ${
                    providerTab === 'wecom'
                      ? 'bg-surface-card text-foreground font-medium shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <Cloud className="h-3 w-3 text-sky-500" /> 企微信微盘
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setProviderTab('webdav');
                    setTestResult(null);
                  }}
                  className={`flex items-center justify-center gap-1 py-1 rounded text-xs transition-colors ${
                    providerTab === 'webdav'
                      ? 'bg-surface-card text-foreground font-medium shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <HardDrive className="h-3 w-3 text-emerald-500" /> WebDAV
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setProviderTab('oss');
                    setTestResult(null);
                  }}
                  className={`flex items-center justify-center gap-1 py-1 rounded text-xs transition-colors ${
                    providerTab === 'oss'
                      ? 'bg-surface-card text-foreground font-medium shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <Database className="h-3 w-3 text-amber-500" /> S3 / OSS
                </button>
              </div>
            </div>

            {/* 表单配置 */}
            <div className="rounded-xl border border-border bg-surface-100/40 p-3.5 space-y-3 text-xs">
              <div>
                <label className="font-medium text-foreground">挂载展示名称</label>
                <input
                  type="text"
                  placeholder="如: 赛事技术规程库 / 财务制度"
                  value={mountName}
                  onChange={(e) => setMountName(e.target.value)}
                  className="mt-1 w-full rounded border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-primary"
                />
              </div>

              <div>
                <label className="font-medium text-foreground">权威等级仲裁 (Authority Tier)</label>
                <select
                  value={authorityTier}
                  onChange={(e) => setAuthorityTier(Number(e.target.value))}
                  className="mt-1 w-full rounded border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none"
                >
                  <option value={1}>Tier 1: 正式发布库 / 定稿规程 (置信权重 1.25)</option>
                  <option value={2}>Tier 2: 工作区日常参考库 (置信权重 1.00)</option>
                  <option value={3}>Tier 3: 草稿与归档旧版 (置信权重 0.60)</option>
                </select>
              </div>

              {/* 企微专有字段 */}
              {providerTab === 'wecom' && (
                <div className="space-y-2 pt-1 border-t border-border/60">
                  <div>
                    <label className="text-muted-foreground">CorpId (企业ID)</label>
                    <input
                      type="text"
                      placeholder="ww0b918f..."
                      value={wecomForm.corpid}
                      onChange={(e) => setWecomForm({ ...wecomForm, corpid: e.target.value })}
                      className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                    />
                  </div>
                  <div>
                    <label className="text-muted-foreground">CorpSecret (自建应用密钥)</label>
                    <input
                      type="password"
                      placeholder="••••••••••••••••"
                      value={wecomForm.corpsecret}
                      onChange={(e) => setWecomForm({ ...wecomForm, corpsecret: e.target.value })}
                      className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                    />
                  </div>
                  <div>
                    <div className="flex items-center justify-between">
                      <label className="text-muted-foreground">微盘 SpaceId (必填)</label>
                      <span className="text-[10px] text-amber-500">必填</span>
                    </div>
                    <input
                      type="text"
                      placeholder="如 s.ww0b918f... 或粘贴空间分享链接"
                      value={wecomForm.space_id}
                      onChange={(e) => setWecomForm({ ...wecomForm, space_id: e.target.value })}
                      className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                    />
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      可在微盘空间右键点击分享链接复制获取，或在管理后台微盘中抓取。
                    </p>
                  </div>
                </div>
              )}

              {/* WebDAV 专有字段 */}
              {providerTab === 'webdav' && (
                <div className="space-y-2 pt-1 border-t border-border/60">
                  <div>
                    <label className="text-muted-foreground">WebDAV 端点 URL</label>
                    <input
                      type="text"
                      placeholder="http://127.0.0.1:5244/dav"
                      value={webdavForm.base_url}
                      onChange={(e) => setWebdavForm({ ...webdavForm, base_url: e.target.value })}
                      className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-muted-foreground">用户名</label>
                      <input
                        type="text"
                        placeholder="admin"
                        value={webdavForm.username}
                        onChange={(e) => setWebdavForm({ ...webdavForm, username: e.target.value })}
                        className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                      />
                    </div>
                    <div>
                      <label className="text-muted-foreground">密码</label>
                      <input
                        type="password"
                        placeholder="••••••••"
                        value={webdavForm.password}
                        onChange={(e) => setWebdavForm({ ...webdavForm, password: e.target.value })}
                        className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="text-muted-foreground">根路径</label>
                    <input
                      type="text"
                      placeholder="/"
                      value={webdavForm.root_path}
                      onChange={(e) => setWebdavForm({ ...webdavForm, root_path: e.target.value })}
                      className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                    />
                  </div>
                </div>
              )}

              {/* OSS 专有字段 */}
              {providerTab === 'oss' && (
                <div className="space-y-2 pt-1 border-t border-border/60">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-muted-foreground">Endpoint</label>
                      <input
                        type="text"
                        placeholder="oss-cn-beijing.aliyuncs.com"
                        value={ossForm.endpoint}
                        onChange={(e) => setOssForm({ ...ossForm, endpoint: e.target.value })}
                        className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                      />
                    </div>
                    <div>
                      <label className="text-muted-foreground">Bucket</label>
                      <input
                        type="text"
                        placeholder="yybasket"
                        value={ossForm.bucket}
                        onChange={(e) => setOssForm({ ...ossForm, bucket: e.target.value })}
                        className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-muted-foreground">AccessKey</label>
                      <input
                        type="text"
                        placeholder="LTAI5t..."
                        value={ossForm.access_key}
                        onChange={(e) => setOssForm({ ...ossForm, access_key: e.target.value })}
                        className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                      />
                    </div>
                    <div>
                      <label className="text-muted-foreground">SecretKey</label>
                      <input
                        type="password"
                        placeholder="••••••••••••••••"
                        value={ossForm.secret_key}
                        onChange={(e) => setOssForm({ ...ossForm, secret_key: e.target.value })}
                        className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* 连通性测试回显 */}
              {testResult && (
                <div
                  className={`flex items-center gap-1.5 rounded p-2 text-xs ${
                    testResult.success ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : 'bg-red-500/10 text-red-500'
                  }`}
                >
                  {testResult.success ? <Check className="h-3.5 w-3.5 shrink-0" /> : <AlertCircle className="h-3.5 w-3.5 shrink-0" />}
                  <span className="leading-tight">{testResult.message}</span>
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2 border-t border-border/60">
                <button
                  type="button"
                  onClick={handleTestConnection}
                  disabled={testing}
                  className="flex items-center gap-1 rounded border border-border px-3 py-1.5 text-xs hover:bg-surface-200 transition-colors"
                >
                  <PlugZap className="h-3 w-3" /> {testing ? '测试中...' : '测试连通性'}
                </button>
                <button
                  type="button"
                  onClick={handleSaveMount}
                  disabled={saving}
                  className="flex items-center gap-1 rounded bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
                >
                  <Check className="h-3 w-3" /> 保存挂载源
                </button>
              </div>
            </div>

            {/* 已挂载列表 */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs font-semibold text-foreground">已挂载的数据源 ({mountsList.length})</h3>
                <span className="text-[11px] text-muted-foreground">可单独删除或在第2步执行训练</span>
              </div>

              <div className="space-y-2">
                {mountsList.length === 0 ? (
                  <div className="py-6 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
                    暂未挂载外部知识库源，请在上方添加第一个数据源
                  </div>
                ) : (
                  mountsList.map((m) => (
                    <div
                      key={m.mount_id}
                      className="flex items-center justify-between rounded-lg border border-border bg-surface-card p-2.5 text-xs hover:border-border/80 transition-colors"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        {m.provider_type === 'wecom' && <Cloud className="h-3.5 w-3.5 text-sky-500 shrink-0" />}
                        {m.provider_type === 'webdav' && <HardDrive className="h-3.5 w-3.5 text-emerald-500 shrink-0" />}
                        {m.provider_type === 'oss' && <Database className="h-3.5 w-3.5 text-amber-500 shrink-0" />}
                        <span className="font-semibold truncate">{m.mount_name}</span>
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground uppercase">
                          {m.provider_type}
                        </span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] ${
                            m.authority_tier === 1
                              ? 'bg-sky-500/10 text-sky-500'
                              : m.authority_tier === 2
                              ? 'bg-muted text-foreground'
                              : 'bg-amber-500/10 text-amber-500'
                          }`}
                        >
                          Tier {m.authority_tier}
                        </span>
                      </div>
                      <button
                        onClick={() => handleDeleteMount(m.mount_id)}
                        className="text-muted-foreground hover:text-red-500 p-1 rounded transition-colors"
                        title="移除此挂载"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

        {/* ========================================================= */}
        {/* 第二部分：知识库训练                                      */}
        {/* ========================================================= */}
        {activeSection === 'train' && (
          <div className="space-y-4 animate-fade-in">
            {/* 训练操作主卡片 */}
            <div className="rounded-xl border border-border bg-surface-100/50 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                    <Sparkles className="w-4 h-4 text-amber-500" />
                    规程切片与向量索引训练
                  </h3>
                  <p className="text-[11px] text-muted-foreground mt-0.5">
                    拉取已挂载数据源文档，执行分块、语义 Embedding 与实体图谱提取
                  </p>
                </div>
              </div>

              <div className="pt-2">
                <button
                  type="button"
                  onClick={() => handleTriggerTrain()}
                  disabled={training || mountsList.length === 0}
                  className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-medium text-xs shadow transition-all disabled:opacity-50"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${training ? 'animate-spin' : ''}`} />
                  {training ? '正在增量解析文档与生成向量索引...' : '一键启动知识库增量训练与索引'}
                </button>
              </div>

              {trainStatusMsg && (
                <div className="p-2.5 rounded-lg bg-surface-card border border-border text-xs text-foreground leading-relaxed whitespace-pre-wrap">
                  {trainStatusMsg}
                </div>
              )}
            </div>

            {/* 已生成知识库资产清单 */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs font-semibold text-foreground">独立知识库资产 ({kbsList.length})</h3>
                <span className="text-[11px] text-muted-foreground">已解耦自包含 SQLite index.db</span>
              </div>

              <div className="space-y-2">
                {kbsList.length === 0 ? (
                  <div className="py-6 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
                    暂无训练资产，请先在挂载源上点击上方训练按钮
                  </div>
                ) : (
                  kbsList.map((kb) => (
                    <div
                      key={kb.kb_id}
                      className={`rounded-lg border p-3 text-xs space-y-2 transition-all ${
                        selectedKbId === kb.kb_id ? 'border-sky-500/60 bg-sky-500/5' : 'border-border bg-surface-card'
                      }`}
                      onClick={() => setSelectedKbId(kb.kb_id)}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-foreground">{kb.name}</span>
                          <span className="px-1.5 py-0.5 rounded bg-muted text-[10px] font-mono text-muted-foreground">
                            {kb.active_version}
                          </span>
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] ${
                              kb.ownership_type === 'subscribed_ro'
                                ? 'bg-amber-500/10 text-amber-500'
                                : kb.ownership_type === 'frozen_snapshot'
                                ? 'bg-muted text-muted-foreground'
                                : 'bg-emerald-500/10 text-emerald-500'
                            }`}
                          >
                            {kb.ownership_type === 'subscribed_ro'
                              ? '🔒 订阅只读'
                              : kb.ownership_type === 'frozen_snapshot'
                              ? '🔒 离线快照'
                              : '本地可编辑'}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handlePruneKb(kb.kb_id);
                            }}
                            className="p-1 text-muted-foreground hover:text-foreground hover:bg-surface-200 rounded"
                            title="清理历史旧版本快照 (Prune 瘦身)"
                          >
                            <Scissors className="w-3.5 h-3.5" />
                          </button>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteKb(kb.kb_id, kb.name);
                            }}
                            className="p-1 text-muted-foreground hover:text-rose-500 hover:bg-rose-500/10 rounded transition-colors"
                            title="彻底删除此知识库"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>

                      <div className="flex items-center gap-4 text-[11px] text-muted-foreground">
                        <span>规程切片: <strong className="text-foreground">{kb.chunk_count}</strong></span>
                        <span>图谱三元组: <strong className="text-foreground">{kb.triple_count}</strong></span>
                        <span>磁盘占用: <strong className="text-foreground">{(kb.disk_size_bytes / (1024 * 1024)).toFixed(2)} MB</strong></span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* 检索命中率实时测试台 (Playground) */}
            <div className="rounded-xl border border-border bg-surface-100/40 p-3.5 space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                  <Search className="w-3.5 h-3.5 text-sky-500" />
                  检索命中率即测验货台 (Playground)
                </h4>
              </div>

              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="输入业务问题，例如: 比赛报名费或押金退费标准..."
                  value={playgroundQuery}
                  onChange={(e) => setPlaygroundQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleTestPlayground()}
                  className="flex-1 rounded border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-sky-500"
                />
                <button
                  type="button"
                  onClick={handleTestPlayground}
                  disabled={playgroundTesting}
                  className="px-3 py-1.5 rounded bg-sky-600 hover:bg-sky-700 text-white text-xs font-medium shrink-0 disabled:opacity-50"
                >
                  {playgroundTesting ? '检索中...' : '测试命中'}
                </button>
              </div>

              {playgroundHits && (
                <div className="space-y-2 pt-2 border-t border-border/60">
                  <div className="text-[11px] text-muted-foreground flex justify-between">
                    <span>召回 Top 切片数: {playgroundHits.length}</span>
                  </div>
                  {playgroundHits.length === 0 ? (
                    <div className="py-2 text-center text-[11px] text-muted-foreground">未检索到匹配的切片证据</div>
                  ) : (
                    playgroundHits.map((h, i) => (
                      <div key={i} className="p-2 rounded bg-surface-card border border-border text-xs space-y-1">
                        <div className="flex items-center justify-between text-[11px]">
                          <span className="font-semibold text-foreground truncate max-w-[280px]">{h.title}</span>
                          <span className="text-emerald-500 font-mono">得分: {h.final_score.toFixed(3)}</span>
                        </div>
                        <p className="text-muted-foreground text-[11px] leading-relaxed line-clamp-3 whitespace-pre-wrap">
                          {h.content}
                        </p>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

