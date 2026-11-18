'use client';

import React, { useState, useEffect } from 'react';
import {
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
  Scissors,
  CheckCircle2,
  BookOpen,
  ArrowRight,
  ExternalLink,
  ShieldCheck,
  FileText,
  Activity,
  Copy,
} from 'lucide-react';
import { Workspace } from '@/types';
import { apiFetch } from '@/utils/apiClient';

interface LLMWikiViewProps {
  workspace?: Workspace;
  onOpenChat?: () => void;
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

export function LLMWikiView({ workspace, onOpenChat }: LLMWikiViewProps) {
  // 两大主分段：'mount' = 知识库挂载, 'train' = 知识库训练
  const [activeSection, setActiveSection] = useState<'mount' | 'train'>('mount');

  // ── 挂载部分状态 ──
  const [providerTab, setProviderTab] = useState<'wecom' | 'webdav' | 'oss'>('wecom');
  const [mountName, setMountName] = useState('');
  const [authorityTier, setAuthorityTier] = useState<number>(1);
  const [mountsList, setMountsList] = useState<CloudMountItem[]>([]);
  const [loadingMounts, setLoadingMounts] = useState(false);
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
  const [loadingKbs, setLoadingKbs] = useState(false);
  const [selectedMountForTrain, setSelectedMountForTrain] = useState<string>('all');
  const [playgroundQuery, setPlaygroundQuery] = useState('');
  const [playgroundTesting, setPlaygroundTesting] = useState(false);
  const [playgroundHits, setPlaygroundHits] = useState<any[] | null>(null);
  const [selectedKbId, setSelectedKbId] = useState<string>('');

  useEffect(() => {
    loadMounts();
    loadKBs();
  }, [workspace?.id]);

  const loadMounts = async () => {
    setLoadingMounts(true);
    try {
      const resp = await apiFetch('/api/v1/knowledge/mounts');
      if (resp.ok) {
        const json = await resp.json();
        setMountsList(json.data || []);
      }
    } catch (e) {
      console.error('加载挂载源失败:', e);
    } finally {
      setLoadingMounts(false);
    }
  };

  const loadKBs = async () => {
    setLoadingKbs(true);
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
    } finally {
      setLoadingKbs(false);
    }
  };

  const getCurrentConfig = () => {
    if (providerTab === 'wecom') return wecomForm;
    if (providerTab === 'webdav') return webdavForm;
    return ossForm;
  };

  // 连通性测试
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
      setTestResult({ success: false, message: e.message || '网络请求异常' });
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
        alert('挂载源保存成功！可切换至【2、知识库训练】开始切片与向量索引构建。');
      }
    } catch (e) {
      console.error('保存挂载源失败:', e);
    } finally {
      setSaving(false);
    }
  };

  // 移除挂载源
  const handleDeleteMount = async (mountId: string) => {
    if (!confirm('确定要解除并移除此挂载源吗？已训练沉淀的独立知识库版本仍会保留。')) return;
    try {
      await apiFetch(`/api/v1/knowledge/mounts/${mountId}`, { method: 'DELETE' });
      await loadMounts();
    } catch (e) {
      console.error('删除挂载失败:', e);
    }
  };

  // 触发增量训练/构建
  const handleTriggerTrain = async (targetMountId?: string) => {
    const finalMountId = targetMountId || (selectedMountForTrain === 'all' ? undefined : selectedMountForTrain);
    setTraining(true);
    setTrainStatusMsg(null);
    try {
      const resp = await apiFetch('/api/v1/knowledge/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspace_root: workspace?.root_path || '.',
          mount_id: finalMountId || null,
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

  // 命中率实时评测台
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

  return (
    <div className="flex-1 h-full overflow-y-auto bg-background text-foreground p-6 md:p-10 select-text">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* 顶部标题栏与中枢概览 */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-border pb-5">
          <div className="space-y-1">
            <div className="flex items-center space-x-2.5">
              <div className="p-2 rounded-xl bg-sky-500/10 text-sky-500 border border-sky-500/20">
                <BookOpen className="w-5 h-5" />
              </div>
              <h1 className="text-2xl font-bold tracking-tight text-foreground">
                LLMWiki 知识中枢
              </h1>
              <span className="px-2 py-0.5 rounded-full text-xs font-mono font-medium bg-sky-500/10 text-sky-500 border border-sky-500/20">
                v1.5.8 Strata
              </span>
            </div>
            <p className="text-sm text-muted-foreground">
              统一管理外部数据源（企业微信微盘、WebDAV、S3/OSS），执行规程切片向量化与独立知识库生命周期治理
              {workspace && (
                <span className="ml-2 px-2 py-0.5 rounded bg-surface-200 text-foreground border border-border font-mono text-xs">
                  工作区: {workspace.name}
                </span>
              )}
            </p>
          </div>

          {/* 两大主分段导航 Pill */}
          <div className="flex items-center bg-surface-200/80 p-1 rounded-xl border border-border shrink-0">
            <button
              type="button"
              onClick={() => setActiveSection('mount')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold transition-all ${
                activeSection === 'mount'
                  ? 'bg-surface-card text-foreground shadow-sm border border-border/80'
                  : 'text-muted-foreground hover:text-foreground hover:bg-surface-200'
              }`}
            >
              <HardDrive className="w-4 h-4 text-sky-500" />
              <span>1、知识库挂载</span>
              <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-sky-500/10 text-sky-500 font-mono">
                {mountsList.length}
              </span>
            </button>

            <button
              type="button"
              onClick={() => setActiveSection('train')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold transition-all ${
                activeSection === 'train'
                  ? 'bg-surface-card text-foreground shadow-sm border border-border/80'
                  : 'text-muted-foreground hover:text-foreground hover:bg-surface-200'
              }`}
            >
              <Sparkles className="w-4 h-4 text-amber-500" />
              <span>2、知识库训练</span>
              <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-amber-500/10 text-amber-500 font-mono">
                {kbsList.length}
              </span>
            </button>
          </div>
        </div>

        {/* ========================================================= */}
        {/* 第一部分：1、知识库挂载                                   */}
        {/* ========================================================= */}
        {activeSection === 'mount' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 animate-fade-in">
            {/* 左侧：添加挂载源配置表单 (7 列) */}
            <div className="lg:col-span-7 space-y-5">
              <div className="rounded-2xl border border-border bg-surface-card p-5 md:p-6 shadow-sm space-y-5">
                <div className="flex items-center justify-between border-b border-border pb-3">
                  <div>
                    <h2 className="text-base font-semibold text-foreground flex items-center gap-2">
                      <Cloud className="w-4 h-4 text-sky-500" />
                      挂接外部云端或本地存储
                    </h2>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      支持将企业内部现有的文档空间只读或读写挂载到虚拟文件系统 (CloudVFS)
                    </p>
                  </div>
                </div>

                {/* 存储类型选择选项卡 */}
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-2 block">
                    选择云端存储提供商
                  </label>
                  <div className="grid grid-cols-3 gap-2.5">
                    <button
                      type="button"
                      onClick={() => {
                        setProviderTab('wecom');
                        setTestResult(null);
                      }}
                      className={`flex flex-col items-center justify-center p-3 rounded-xl border text-xs transition-all ${
                        providerTab === 'wecom'
                          ? 'border-sky-500 bg-sky-500/5 font-semibold text-foreground shadow-sm'
                          : 'border-border bg-surface-100/60 text-muted-foreground hover:text-foreground hover:border-border/80'
                      }`}
                    >
                      <Cloud className="w-5 h-5 text-sky-500 mb-1" />
                      <span>企业微信微盘</span>
                      <span className="text-[10px] text-muted-foreground mt-0.5">Corp API</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setProviderTab('webdav');
                        setTestResult(null);
                      }}
                      className={`flex flex-col items-center justify-center p-3 rounded-xl border text-xs transition-all ${
                        providerTab === 'webdav'
                          ? 'border-emerald-500 bg-emerald-500/5 font-semibold text-foreground shadow-sm'
                          : 'border-border bg-surface-100/60 text-muted-foreground hover:text-foreground hover:border-border/80'
                      }`}
                    >
                      <HardDrive className="w-5 h-5 text-emerald-500 mb-1" />
                      <span>WebDAV</span>
                      <span className="text-[10px] text-muted-foreground mt-0.5">Alist / NAS / 本地盘</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setProviderTab('oss');
                        setTestResult(null);
                      }}
                      className={`flex flex-col items-center justify-center p-3 rounded-xl border text-xs transition-all ${
                        providerTab === 'oss'
                          ? 'border-amber-500 bg-amber-500/5 font-semibold text-foreground shadow-sm'
                          : 'border-border bg-surface-100/60 text-muted-foreground hover:text-foreground hover:border-border/80'
                      }`}
                    >
                      <Database className="w-5 h-5 text-amber-500 mb-1" />
                      <span>对象存储</span>
                      <span className="text-[10px] text-muted-foreground mt-0.5">S3 / OSS / MinIO</span>
                    </button>
                  </div>
                </div>

                {/* 通用基础设置 */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="text-xs font-semibold text-foreground block mb-1">
                      挂载展示名称 <span className="text-red-500">*</span>
                    </label>
                    <input
                      type="text"
                      placeholder="如: 击剑赛事技术规程库 / 财务制度"
                      value={mountName}
                      onChange={(e) => setMountName(e.target.value)}
                      className="w-full rounded-xl border border-border bg-surface-100 px-3 py-2 text-xs outline-none focus:border-sky-500 focus:bg-surface-card transition-all"
                    />
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-foreground block mb-1">
                      权威等级仲裁 (Authority Tier)
                    </label>
                    <select
                      value={authorityTier}
                      onChange={(e) => setAuthorityTier(Number(e.target.value))}
                      className="w-full rounded-xl border border-border bg-surface-100 px-3 py-2 text-xs outline-none focus:border-sky-500 focus:bg-surface-card transition-all"
                    >
                      <option value={1}>Tier 1: 正式发布库 / 定稿规程 (置信权重 1.25)</option>
                      <option value={2}>Tier 2: 工作区日常参考库 (置信权重 1.00)</option>
                      <option value={3}>Tier 3: 草稿与归档旧版 (置信权重 0.60)</option>
                    </select>
                  </div>
                </div>

                {/* 企微专有配置 */}
                {providerTab === 'wecom' && (
                  <div className="rounded-xl border border-border/80 bg-surface-100/40 p-4 space-y-3.5">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs font-medium text-muted-foreground block mb-1">
                          CorpId (企业ID)
                        </label>
                        <input
                          type="text"
                          placeholder="ww0b918f532c2838cb..."
                          value={wecomForm.corpid}
                          onChange={(e) => setWecomForm({ ...wecomForm, corpid: e.target.value })}
                          className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-sky-500"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-muted-foreground block mb-1">
                          CorpSecret (微盘自建应用密钥)
                        </label>
                        <input
                          type="password"
                          placeholder="••••••••••••••••"
                          value={wecomForm.corpsecret}
                          onChange={(e) => setWecomForm({ ...wecomForm, corpsecret: e.target.value })}
                          className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-sky-500"
                        />
                      </div>
                    </div>

                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs font-semibold text-foreground">
                          微盘 SpaceId <span className="text-red-500">*</span>
                        </label>
                        <span className="text-[10px] text-amber-500 font-mono">必填</span>
                      </div>
                      <input
                        type="text"
                        placeholder="如 s.1970324948326374... 或直接粘贴空间分享链接"
                        value={wecomForm.space_id}
                        onChange={(e) => setWecomForm({ ...wecomForm, space_id: e.target.value })}
                        className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-sky-500"
                      />
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        在企业微信电脑端打开微盘空间，右键点击“复制链接”即可直接粘贴，系统将自动识别提取 SpaceId。
                      </p>
                    </div>
                  </div>
                )}

                {/* WebDAV 专有配置 */}
                {providerTab === 'webdav' && (
                  <div className="rounded-xl border border-border/80 bg-surface-100/40 p-4 space-y-3.5">
                    <div>
                      <label className="text-xs font-semibold text-foreground block mb-1">
                        WebDAV 端点 URL <span className="text-red-500">*</span>
                      </label>
                      <input
                        type="text"
                        placeholder="http://127.0.0.1:5244/dav 或 https://nas.local:5006"
                        value={webdavForm.base_url}
                        onChange={(e) => setWebdavForm({ ...webdavForm, base_url: e.target.value })}
                        className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-emerald-500"
                      />
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs font-medium text-muted-foreground block mb-1">用户名</label>
                        <input
                          type="text"
                          placeholder="admin"
                          value={webdavForm.username}
                          onChange={(e) => setWebdavForm({ ...webdavForm, username: e.target.value })}
                          className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-emerald-500"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-muted-foreground block mb-1">密码</label>
                        <input
                          type="password"
                          placeholder="••••••••"
                          value={webdavForm.password}
                          onChange={(e) => setWebdavForm({ ...webdavForm, password: e.target.value })}
                          className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-emerald-500"
                        />
                      </div>
                    </div>

                    <div>
                      <label className="text-xs font-medium text-muted-foreground block mb-1">根路径 (Root Path)</label>
                      <input
                        type="text"
                        placeholder="/"
                        value={webdavForm.root_path}
                        onChange={(e) => setWebdavForm({ ...webdavForm, root_path: e.target.value })}
                        className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-emerald-500"
                      />
                    </div>
                  </div>
                )}

                {/* 对象存储 OSS 专有配置 */}
                {providerTab === 'oss' && (
                  <div className="rounded-xl border border-border/80 bg-surface-100/40 p-4 space-y-3.5">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs font-medium text-muted-foreground block mb-1">
                          Endpoint 端点
                        </label>
                        <input
                          type="text"
                          placeholder="https://oss-cn-beijing.aliyuncs.com"
                          value={ossForm.endpoint}
                          onChange={(e) => setOssForm({ ...ossForm, endpoint: e.target.value })}
                          className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-amber-500"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-muted-foreground block mb-1">
                          Bucket 桶名
                        </label>
                        <input
                          type="text"
                          placeholder="yybasket"
                          value={ossForm.bucket}
                          onChange={(e) => setOssForm({ ...ossForm, bucket: e.target.value })}
                          className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-amber-500"
                        />
                      </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs font-medium text-muted-foreground block mb-1">
                          AccessKey ID
                        </label>
                        <input
                          type="text"
                          placeholder="LTAI5t..."
                          value={ossForm.access_key}
                          onChange={(e) => setOssForm({ ...ossForm, access_key: e.target.value })}
                          className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-amber-500"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-muted-foreground block mb-1">
                          SecretKey Secret
                        </label>
                        <input
                          type="password"
                          placeholder="••••••••••••••••"
                          value={ossForm.secret_key}
                          onChange={(e) => setOssForm({ ...ossForm, secret_key: e.target.value })}
                          className="w-full rounded-lg border border-border bg-surface-card px-2.5 py-1.5 text-xs outline-none focus:border-amber-500"
                        />
                      </div>
                    </div>
                  </div>
                )}

                {/* 连通性测试结果面板 */}
                {testResult && (
                  <div
                    className={`flex items-start gap-2.5 rounded-xl p-3 text-xs leading-relaxed transition-all ${
                      testResult.success
                        ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20'
                        : 'bg-red-500/10 text-red-500 border border-red-500/20'
                    }`}
                  >
                    {testResult.success ? (
                      <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
                    ) : (
                      <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                    )}
                    <div className="space-y-0.5">
                      <div className="font-semibold">{testResult.success ? '连接测试成功' : '连接测试失败'}</div>
                      <div className="text-[11px] opacity-90">{testResult.message}</div>
                    </div>
                  </div>
                )}

                {/* 操作按键组 */}
                <div className="flex items-center justify-between pt-2 border-t border-border">
                  <span className="text-[11px] text-muted-foreground">
                    建议先测试服务连通性，确保权限校验无误后再行保存。
                  </span>

                  <div className="flex items-center gap-2.5">
                    <button
                      type="button"
                      onClick={handleTestConnection}
                      disabled={testing}
                      className="flex items-center gap-1.5 rounded-xl border border-border px-4 py-2 text-xs font-medium text-foreground hover:bg-surface-200 transition-colors disabled:opacity-50"
                    >
                      <PlugZap className="w-3.5 h-3.5 text-sky-500" />
                      {testing ? '正在测试连通性...' : '测试连通性'}
                    </button>

                    <button
                      type="button"
                      onClick={handleSaveMount}
                      disabled={saving}
                      className="flex items-center gap-1.5 rounded-xl bg-sky-600 hover:bg-sky-700 px-4 py-2 text-xs font-medium text-white shadow-sm transition-all disabled:opacity-50"
                    >
                      <Check className="w-3.5 h-3.5" />
                      {saving ? '保存中...' : '保存挂载'}
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* 右侧：已挂载的数据源列表与说明卡片 (5 列) */}
            <div className="lg:col-span-5 space-y-5">
              <div className="rounded-2xl border border-border bg-surface-card p-5 shadow-sm space-y-4">
                <div className="flex items-center justify-between border-b border-border pb-3">
                  <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                    <Layers className="w-4 h-4 text-sky-500" />
                    已挂载的虚拟数据源 ({mountsList.length})
                  </h3>
                  <button
                    onClick={loadMounts}
                    disabled={loadingMounts}
                    className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
                    title="刷新列表"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${loadingMounts ? 'animate-spin' : ''}`} />
                  </button>
                </div>

                <div className="space-y-3">
                  {mountsList.length === 0 ? (
                    <div className="py-12 text-center text-xs text-muted-foreground border border-dashed border-border rounded-xl space-y-2">
                      <HardDrive className="w-8 h-8 text-muted-foreground/40 mx-auto" />
                      <div>暂未挂载外部知识库源</div>
                      <p className="text-[11px] text-muted-foreground/80 max-w-[240px] mx-auto">
                        请在左侧表单中配置企业微信微盘、WebDAV 或对象存储
                      </p>
                    </div>
                  ) : (
                    mountsList.map((m) => (
                      <div
                        key={m.mount_id}
                        className="rounded-xl border border-border bg-surface-100/50 p-3.5 text-xs hover:border-border/80 transition-all space-y-2.5"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex items-center gap-2 min-w-0">
                            {m.provider_type === 'wecom' && (
                              <div className="p-1.5 rounded-lg bg-sky-500/10 text-sky-500">
                                <Cloud className="w-4 h-4" />
                              </div>
                            )}
                            {m.provider_type === 'webdav' && (
                              <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-500">
                                <HardDrive className="w-4 h-4" />
                              </div>
                            )}
                            {m.provider_type === 'oss' && (
                              <div className="p-1.5 rounded-lg bg-amber-500/10 text-amber-500">
                                <Database className="w-4 h-4" />
                              </div>
                            )}
                            <div className="min-w-0">
                              <h4 className="font-semibold text-foreground truncate">{m.mount_name}</h4>
                              <div className="flex items-center gap-1.5 mt-0.5">
                                <span className="rounded bg-surface-200 px-1.5 py-0.5 text-[10px] font-mono uppercase text-muted-foreground">
                                  {m.provider_type}
                                </span>
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                    m.authority_tier === 1
                                      ? 'bg-sky-500/10 text-sky-500'
                                      : m.authority_tier === 2
                                      ? 'bg-surface-200 text-foreground'
                                      : 'bg-amber-500/10 text-amber-500'
                                  }`}
                                >
                                  Tier {m.authority_tier}
                                </span>
                              </div>
                            </div>
                          </div>

                          <button
                            onClick={() => handleDeleteMount(m.mount_id)}
                            className="text-muted-foreground hover:text-red-500 p-1.5 rounded-lg hover:bg-surface-200 transition-colors"
                            title="解除此挂载"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>

                        <div className="pt-2 border-t border-border/60 flex items-center justify-between text-[11px] text-muted-foreground">
                          <span className="truncate max-w-[200px]">
                            {m.provider_type === 'wecom' && `微盘 Space: ${m.config?.space_id || '未知'}`}
                            {m.provider_type === 'webdav' && `URL: ${m.config?.base_url || '未知'}`}
                            {m.provider_type === 'oss' && `Bucket: ${m.config?.bucket || '未知'}`}
                          </span>
                          <button
                            onClick={() => {
                              setSelectedMountForTrain(m.mount_id);
                              setActiveSection('train');
                            }}
                            className="text-sky-500 hover:text-sky-600 font-medium flex items-center gap-0.5"
                          >
                            <span>前往训练</span>
                            <ArrowRight className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* 快速下一步引导卡片 */}
              <div className="rounded-2xl border border-sky-500/20 bg-sky-500/5 p-5 space-y-2.5">
                <div className="flex items-center gap-2 text-sky-600 dark:text-sky-400 font-semibold text-xs">
                  <ShieldCheck className="w-4 h-4" />
                  挂载与索引机制说明
                </div>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  挂载完成后，数据源即纳入本地虚拟文件系统（CloudVFS）。您可随时在“2、知识库训练”中触发增量切片与语义向量 Embedding 构建，规程文档将生成独立 SQLite 知识库，支持跨专家调用与高精度仲裁搜索。
                </p>
                <div className="pt-1">
                  <button
                    onClick={() => setActiveSection('train')}
                    className="inline-flex items-center gap-1.5 text-xs text-sky-600 dark:text-sky-400 font-medium hover:underline"
                  >
                    立即切换到知识库训练 <ArrowRight className="w-3 h-3" />
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ========================================================= */}
        {/* 第二部分：2、知识库训练                                   */}
        {/* ========================================================= */}
        {activeSection === 'train' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 animate-fade-in">
            {/* 左侧：训练调度与知识库资产清单 (7 列) */}
            <div className="lg:col-span-7 space-y-5">
              {/* 训练操作面板 */}
              <div className="rounded-2xl border border-border bg-surface-card p-5 md:p-6 shadow-sm space-y-5">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 border-b border-border pb-4">
                  <div>
                    <h2 className="text-base font-semibold text-foreground flex items-center gap-2">
                      <Sparkles className="w-4 h-4 text-amber-500" />
                      规程切片与向量索引训练
                    </h2>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      从挂载数据源拉取最新文档，执行自适应滑动窗口分块、向量 Embedding 及图谱三元组提取
                    </p>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="text-xs font-semibold text-foreground block mb-1">
                      选择训练数据源
                    </label>
                    <select
                      value={selectedMountForTrain}
                      onChange={(e) => setSelectedMountForTrain(e.target.value)}
                      className="w-full rounded-xl border border-border bg-surface-100 px-3 py-2 text-xs outline-none focus:border-amber-500"
                    >
                      <option value="all">全量同步 (所有已挂载数据源)</option>
                      {mountsList.map((m) => (
                        <option key={m.mount_id} value={m.mount_id}>
                          {m.mount_name} ({m.provider_type})
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-foreground block mb-1">
                      切片算法策略
                    </label>
                    <div className="w-full rounded-xl border border-border bg-surface-100 px-3 py-2 text-xs text-muted-foreground flex items-center justify-between">
                      <span>自适应 Markdown / 标题感知切片</span>
                      <span className="font-mono text-[10px] text-amber-500">512~1024 Token</span>
                    </div>
                  </div>
                </div>

                <div>
                  <button
                    type="button"
                    onClick={() => handleTriggerTrain()}
                    disabled={training || mountsList.length === 0}
                    className="w-full flex items-center justify-center gap-2 py-3 px-4 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-medium text-xs shadow-md transition-all disabled:opacity-50"
                  >
                    <RefreshCw className={`w-4 h-4 ${training ? 'animate-spin' : ''}`} />
                    {training ? '正在增量解析文档与生成向量索引...' : '一键启动知识库增量训练与切片'}
                  </button>
                  {mountsList.length === 0 && (
                    <p className="text-[11px] text-amber-500 mt-1.5 text-center">
                      当前尚未添加任何挂载源，请先切换到“1、知识库挂载”添加文档源。
                    </p>
                  )}
                </div>

                {trainStatusMsg && (
                  <div className="p-3.5 rounded-xl bg-surface-100 border border-border text-xs text-foreground leading-relaxed whitespace-pre-wrap font-mono">
                    {trainStatusMsg}
                  </div>
                )}
              </div>

              {/* 独立知识库资产清单 (KB Assets) */}
              <div className="rounded-2xl border border-border bg-surface-card p-5 md:p-6 shadow-sm space-y-4">
                <div className="flex items-center justify-between border-b border-border pb-3">
                  <div>
                    <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                      <Database className="w-4 h-4 text-emerald-500" />
                      独立知识库资产清单 ({kbsList.length})
                    </h3>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      v1.5.8 独立自包含 SQLite index.db，支持快照复制、版本回滚与磁盘瘦身
                    </p>
                  </div>
                  <button
                    onClick={loadKBs}
                    disabled={loadingKbs}
                    className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
                    title="刷新知识库资产"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${loadingKbs ? 'animate-spin' : ''}`} />
                  </button>
                </div>

                <div className="space-y-3">
                  {kbsList.length === 0 ? (
                    <div className="py-12 text-center text-xs text-muted-foreground border border-dashed border-border rounded-xl space-y-2">
                      <Database className="w-8 h-8 text-muted-foreground/40 mx-auto" />
                      <div>暂无已训练的知识库资产</div>
                      <p className="text-[11px] text-muted-foreground/80 max-w-[240px] mx-auto">
                        请在上方点击“一键启动知识库增量训练与切片”生成第一个知识库
                      </p>
                    </div>
                  ) : (
                    kbsList.map((kb) => {
                      const isSelected = selectedKbId === kb.kb_id;
                      return (
                        <div
                          key={kb.kb_id}
                          onClick={() => setSelectedKbId(kb.kb_id)}
                          className={`rounded-xl border p-4 text-xs transition-all cursor-pointer space-y-3 ${
                            isSelected
                              ? 'border-sky-500 bg-sky-500/5 shadow-sm'
                              : 'border-border bg-surface-100/50 hover:border-border/80'
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <span className="font-semibold text-sm text-foreground">{kb.name}</span>
                              <span className="px-2 py-0.5 rounded font-mono text-[10px] bg-surface-200 text-foreground border border-border">
                                {kb.active_version}
                              </span>
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                                  kb.ownership_type === 'subscribed_ro'
                                    ? 'bg-amber-500/10 text-amber-500 border border-amber-500/20'
                                    : kb.ownership_type === 'frozen_snapshot'
                                    ? 'bg-muted text-muted-foreground border border-border'
                                    : 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                                }`}
                              >
                                {kb.ownership_type === 'subscribed_ro'
                                  ? '🔒 团队订阅只读'
                                  : kb.ownership_type === 'frozen_snapshot'
                                  ? '🔒 离线安全快照'
                                  : '本地可编辑'}
                              </span>
                            </div>

                            <div className="flex items-center gap-2">
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handlePruneKb(kb.kb_id);
                                }}
                                className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-surface-200 rounded-lg border border-border transition-colors"
                                title="清理旧历史版本快照，执行 VACUUM 释放磁盘"
                              >
                                <Scissors className="w-3 h-3" />
                                <span>磁盘瘦身</span>
                              </button>
                            </div>
                          </div>

                          <div className="grid grid-cols-3 gap-2 py-2 px-3 rounded-lg bg-surface-card border border-border/60 text-[11px]">
                            <div>
                              <span className="text-muted-foreground block">规程切片数</span>
                              <strong className="text-foreground text-xs">{kb.chunk_count}</strong>
                            </div>
                            <div>
                              <span className="text-muted-foreground block">图谱三元组</span>
                              <strong className="text-foreground text-xs">{kb.triple_count}</strong>
                            </div>
                            <div>
                              <span className="text-muted-foreground block">独立数据库大小</span>
                              <strong className="text-foreground text-xs font-mono">
                                {(kb.disk_size_bytes / (1024 * 1024)).toFixed(2)} MB
                              </strong>
                            </div>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>

            {/* 右侧：检索命中率实时测试台 (Playground, 5 列) */}
            <div className="lg:col-span-5 space-y-5">
              <div className="rounded-2xl border border-border bg-surface-card p-5 shadow-sm space-y-4">
                <div className="flex items-center justify-between border-b border-border pb-3">
                  <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                    <Search className="w-4 h-4 text-sky-500" />
                    检索命中率即测验货台 (Playground)
                  </h3>
                  <span className="text-[10px] text-muted-foreground">权威权重重排</span>
                </div>

                <p className="text-xs text-muted-foreground">
                  直接向训练好的知识库提问，实时测试语义向量与 BM25 混合召回精度、权威等级仲裁得分及前 5 条切片证据。
                </p>

                <div>
                  <label className="text-xs font-medium text-muted-foreground block mb-1">
                    当前测试的知识库
                  </label>
                  <select
                    value={selectedKbId}
                    onChange={(e) => setSelectedKbId(e.target.value)}
                    className="w-full rounded-xl border border-border bg-surface-100 px-3 py-2 text-xs outline-none focus:border-sky-500"
                  >
                    {kbsList.map((kb) => (
                      <option key={kb.kb_id} value={kb.kb_id}>
                        {kb.name} ({kb.active_version})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="输入测试问题，如: 比赛报名费或押金退还标准..."
                    value={playgroundQuery}
                    onChange={(e) => setPlaygroundQuery(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleTestPlayground()}
                    className="flex-1 rounded-xl border border-border bg-surface-100 px-3 py-2 text-xs outline-none focus:border-sky-500 focus:bg-surface-card transition-all"
                  />
                  <button
                    type="button"
                    onClick={handleTestPlayground}
                    disabled={playgroundTesting}
                    className="px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-700 text-white text-xs font-medium shrink-0 disabled:opacity-50 transition-all shadow-sm"
                  >
                    {playgroundTesting ? '检索中...' : '测试命中'}
                  </button>
                </div>

                {playgroundHits && (
                  <div className="space-y-3 pt-3 border-t border-border">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-semibold text-foreground">
                        召回证据切片 ({playgroundHits.length})
                      </span>
                      <span className="text-[10px] text-muted-foreground">按最终置信得分降序</span>
                    </div>

                    {playgroundHits.length === 0 ? (
                      <div className="py-8 text-center text-xs text-muted-foreground border border-dashed border-border rounded-xl">
                        未检索到与查询匹配的相关规程切片
                      </div>
                    ) : (
                      <div className="space-y-2.5 max-h-[460px] overflow-y-auto pr-1">
                        {playgroundHits.map((h, i) => (
                          <div
                            key={i}
                            className="p-3 rounded-xl bg-surface-100/60 border border-border text-xs space-y-1.5 hover:border-border/80 transition-all"
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-semibold text-foreground truncate max-w-[200px]">
                                {h.title}
                              </span>
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-500/10 text-emerald-500 font-semibold">
                                得分: {h.final_score.toFixed(3)}
                              </span>
                            </div>
                            <p className="text-muted-foreground text-[11px] leading-relaxed line-clamp-3 whitespace-pre-wrap">
                              {h.content}
                            </p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

