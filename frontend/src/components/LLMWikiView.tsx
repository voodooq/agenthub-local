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
  FolderOpen,
  FileSpreadsheet,
  FileCode,
  X,
  Zap,
  Network,
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
  const [expandedHitIdx, setExpandedHitIdx] = useState<number | null>(null);

  // ── 查看空间文件状态 ──
  const [viewingMount, setViewingMount] = useState<CloudMountItem | null>(null);
  const [mountFiles, setMountFiles] = useState<any[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [filesSearchQuery, setFilesSearchQuery] = useState('');

  // ── 查看知识库切片资产状态 ──
  const [viewingChunksKb, setViewingChunksKb] = useState<KnowledgeBaseItem | null>(null);
  const [kbChunks, setKbChunks] = useState<any[]>([]);
  const [totalChunksCount, setTotalChunksCount] = useState<number>(0);
  const [loadingChunks, setLoadingChunks] = useState(false);
  const [chunksPage, setChunksPage] = useState<number>(1);
  const [chunksSearchQuery, setChunksSearchQuery] = useState<string>('');
  const [copiedChunkId, setCopiedChunkId] = useState<string | null>(null);

  // ── 查看知识图谱三元组状态 ──
  const [viewingTriplesKb, setViewingTriplesKb] = useState<KnowledgeBaseItem | null>(null);
  const [kbTriples, setKbTriples] = useState<any[]>([]);
  const [totalTriplesCount, setTotalTriplesCount] = useState<number>(0);
  const [loadingTriples, setLoadingTriples] = useState(false);
  const [triplesPage, setTriplesPage] = useState<number>(1);
  const [triplesSearchQuery, setTriplesSearchQuery] = useState<string>('');
  const [extractingGraphKbId, setExtractingGraphKbId] = useState<string | null>(null);

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

  // 彻底物理删除单个独立知识库
  const handleDeleteKb = async (kbId: string, kbName: string) => {
    if (!confirm(`确定要彻底删除独立知识库【${kbName}】吗？\n该操作将物理销毁其在磁盘上的所有版本快照与向量数据库，不可恢复。`)) return;
    try {
      const resp = await apiFetch(`/api/v1/kb/bases/${kbId}`, { method: 'DELETE' });
      if (resp.ok) {
        if (selectedKbId === kbId) {
          const remaining = kbsList.filter((k) => k.kb_id !== kbId);
          setSelectedKbId(remaining[0]?.kb_id || '');
          setPlaygroundHits(null);
        }
        await loadKBs();
      } else {
        const data = await resp.json().catch(() => ({}));
        alert('删除失败: ' + (data?.message || '网络异常'));
      }
    } catch (e: any) {
      alert('删除异常: ' + e.message);
    }
  };

  // 一键清空物理销毁全部独立知识库资产
  const handlePurgeAllKbs = async () => {
    if (kbsList.length === 0) return;
    if (!confirm(`确定要清空并物理销毁当前系统所有独立知识库资产吗？（共 ${kbsList.length} 个）\n该操作将删除磁盘所有 index.db 并清空元数据，不可恢复！`)) return;
    try {
      const resp = await apiFetch('/api/v1/kb/bases/purge-all', { method: 'POST' });
      const data = await resp.json();
      alert(data.message || '已清空所有独立知识库资产');
      setSelectedKbId('');
      setPlaygroundHits(null);
      await loadKBs();
    } catch (e: any) {
      alert('清空异常: ' + e.message);
    }
  };

  // 实时读取空间文件列表
  const handleViewFiles = async (mount: CloudMountItem) => {
    setViewingMount(mount);
    setLoadingFiles(true);
    setFilesError(null);
    setMountFiles([]);
    setFilesSearchQuery('');
    try {
      const resp = await apiFetch(`/api/v1/knowledge/mounts/${mount.mount_id}/files`);
      const data = await resp.json();
      if (resp.ok && data.success) {
        setMountFiles(data.files || []);
      } else {
        setFilesError(data.detail || data.message || '读取空间文件列表失败');
      }
    } catch (e: any) {
      setFilesError(e.message || '网络请求异常');
    } finally {
      setLoadingFiles(false);
    }
  };


  // 查看知识库切片资产详情
  const handleViewKbChunks = async (kb: KnowledgeBaseItem, page: number = 1, search: string = '') => {
    setViewingChunksKb(kb);
    setChunksPage(page);
    setChunksSearchQuery(search);
    setLoadingChunks(true);
    try {
      const q = encodeURIComponent(search.trim());
      const resp = await apiFetch(`/api/v1/kb/bases/${kb.kb_id}/chunks?page=${page}&page_size=20&search=${q}`);
      if (resp.ok) {
        const json = await resp.json();
        setKbChunks(json.chunks || []);
        setTotalChunksCount(json.total_count || 0);
      }
    } catch (e) {
      console.error('加载切片失败:', e);
    } finally {
      setLoadingChunks(false);
    }
  };

  // 复制切片全文
  const handleCopyChunkText = (chunkId: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedChunkId(chunkId);
    setTimeout(() => setCopiedChunkId(null), 2000);
  };

  // 提取抽取知识图谱三元组
  const handleExtractGraph = async (kbId: string) => {
    setExtractingGraphKbId(kbId);
    try {
      const resp = await apiFetch(`/api/v1/kb/bases/${kbId}/extract-graph`, { method: 'POST' });
      const data = await resp.json();
      if (resp.ok) {
        alert(data.message || '图谱三元组提取完成');
        await loadKBs();
      } else {
        alert('提取失败: ' + (data.detail || data.message));
      }
    } catch (e: any) {
      alert('网络请求异常: ' + e.message);
    } finally {
      setExtractingGraphKbId(null);
    }
  };

  // 查看知识图谱三元组资产列表
  const handleViewKbTriples = async (kb: KnowledgeBaseItem, page: number = 1, search: string = '') => {
    setViewingTriplesKb(kb);
    setTriplesPage(page);
    setTriplesSearchQuery(search);
    setLoadingTriples(true);
    try {
      const q = encodeURIComponent(search.trim());
      const resp = await apiFetch(`/api/v1/kb/bases/${kb.kb_id}/triples?page=${page}&page_size=30&search=${q}`);
      if (resp.ok) {
        const json = await resp.json();
        setKbTriples(json.triples || []);
        setTotalTriplesCount(json.total_count || 0);
      }
    } catch (e) {
      console.error('加载三元组失败:', e);
    } finally {
      setLoadingTriples(false);
    }
  };


  return (
    <div className="flex-1 flex flex-col h-full bg-background overflow-hidden">
      {/* 第一层：图标，功能名称，功能描述，全局的功能按钮 放在页面顶部右侧 */}
      <div className="p-6 border-b border-border flex items-center justify-between shrink-0 bg-background">
        <div className="flex items-center space-x-2.5">
          <div className="p-2.5 rounded-xl bg-sky-500/10 text-sky-500 border border-sky-500/20">
            <BookOpen className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-foreground flex items-center space-x-2">
              <span>LLMwiki (知识中枢)</span>
              <span className="px-2 py-0.5 rounded-full text-[11px] bg-sky-500/10 text-sky-500 font-mono">
                {mountsList.length} 挂载 · {kbsList.length} 知识库
              </span>
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              统一管理外部数据源（企业微信微盘、WebDAV、S3/OSS），执行规程切片向量化与独立知识库生命周期治理。
            </p>
          </div>
        </div>

        {/* 全局的功能按钮 放在页面顶部右侧 */}
        <div className="flex items-center space-x-2.5">
          <button
            onClick={() => {
              loadMounts();
              loadKBs();
            }}
            className="flex items-center space-x-1.5 px-3 py-2 rounded-xl border border-border bg-surface-100 hover:bg-surface-200 text-foreground text-xs font-medium transition-all"
            title="刷新数据"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loadingMounts || loadingKbs ? 'animate-spin' : ''}`} />
            <span>刷新</span>
          </button>
          <button
            onClick={() => handleTriggerTrain()}
            disabled={training || mountsList.length === 0}
            className="flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-white text-xs font-semibold shadow-md transition-all active:scale-95 disabled:opacity-50"
          >
            <Sparkles className="w-4 h-4" />
            <span>{training ? '训练中...' : '启动增量训练'}</span>
          </button>
        </div>
      </div>

      {/* 第二层：功能区切换居右对齐 */}
      <div className="px-6 py-3 border-b border-border bg-surface-100/40 flex items-center justify-between shrink-0">
        <div className="flex items-center space-x-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">CloudVFS 虚拟文件系统</span>
          <span>·</span>
          <span>
            {workspace?.name ? `当前工作区：${workspace.name}` : '全系统范围'}
          </span>
        </div>

        {/* 功能区切换居右对齐 */}
        <div className="flex items-center space-x-1 p-1 rounded-lg bg-surface-200 border border-border text-xs">
          <button
            type="button"
            onClick={() => setActiveSection('mount')}
            className={`flex items-center space-x-1.5 px-3.5 py-1 rounded-md transition-all ${
              activeSection === 'mount'
                ? 'bg-surface-card text-foreground font-semibold shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <HardDrive className="w-3.5 h-3.5 text-sky-500" />
            <span>1、知识库挂载</span>
            <span className="ml-1 text-[10px] font-mono opacity-80">({mountsList.length})</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveSection('train')}
            className={`flex items-center space-x-1.5 px-3.5 py-1 rounded-md transition-all ${
              activeSection === 'train'
                ? 'bg-surface-card text-foreground font-semibold shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <Sparkles className="w-3.5 h-3.5 text-amber-500" />
            <span>2、知识库训练</span>
            <span className="ml-1 text-[10px] font-mono opacity-80">({kbsList.length})</span>
          </button>
        </div>
      </div>

      {/* 第三层：功能操作区 */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6 select-text">

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
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs font-semibold text-foreground">
                          WebDAV 端点 URL <span className="text-red-500">*</span>
                        </label>
                        <span className="text-[10px] text-muted-foreground">
                          Alist 路径为 <code className="text-emerald-500 font-bold">/dav</code>（字母 a，非 dev）
                        </span>
                      </div>
                      <input
                        type="text"
                        placeholder="http://127.0.0.1:5244/dav 或 https://nas.local:5006"
                        value={webdavForm.base_url}
                        onChange={(e) => setWebdavForm({ ...webdavForm, base_url: e.target.value })}
                        className={`w-full rounded-lg border bg-surface-card px-2.5 py-1.5 text-xs outline-none transition-colors ${
                          webdavForm.base_url.includes('/dev')
                            ? 'border-amber-500 focus:border-amber-500'
                            : 'border-border focus:border-emerald-500'
                        }`}
                      />
                      {webdavForm.base_url.includes('/dev') && (
                        <div className="mt-1.5 flex items-center justify-between p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400 text-[11px] animate-fade-in">
                          <span className="flex items-center gap-1">
                            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                            检测到输入包含 <code>/dev</code>，Alist 等服务标准路径为 <code>/dav</code>（字母 a）
                          </span>
                          <button
                            type="button"
                            onClick={() => setWebdavForm({ ...webdavForm, base_url: webdavForm.base_url.replace('/dev', '/dav') })}
                            className="px-2 py-0.5 rounded bg-amber-500 hover:bg-amber-600 text-white font-medium transition shrink-0 ml-2"
                          >
                            一键修正为 /dav
                          </button>
                        </div>
                      )}
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
                          <span className="truncate max-w-[170px]">
                            {m.provider_type === 'wecom' && `微盘 Space: ${m.config?.space_id || '未知'}`}
                            {m.provider_type === 'webdav' && `URL: ${m.config?.base_url || '未知'}`}
                            {m.provider_type === 'oss' && `Bucket: ${m.config?.bucket || '未知'}`}
                          </span>
                          <div className="flex items-center gap-2.5 shrink-0">
                            <button
                              type="button"
                              onClick={() => handleViewFiles(m)}
                              className="text-foreground hover:text-sky-500 font-medium flex items-center gap-1 hover:underline transition-colors"
                              title="实时查看空间内所有文件与目录"
                            >
                              <FolderOpen className="w-3.5 h-3.5 text-sky-500" />
                              <span>浏览文件</span>
                            </button>
                            <button
                              type="button"
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
                      v1.5.8 独立自包含 SQLite index.db，支持快照复制、版本回滚、磁盘瘦身与物理清理
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {kbsList.length > 0 && (
                      <button
                        type="button"
                        onClick={handlePurgeAllKbs}
                        className="flex items-center gap-1 px-2.5 py-1 text-xs text-rose-500 hover:text-rose-600 hover:bg-rose-500/10 rounded-lg border border-rose-500/20 transition-colors"
                        title="清空并彻底物理销毁所有独立知识库资产"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        <span>清空全部</span>
                      </button>
                    )}
                    <button
                      onClick={loadKBs}
                      disabled={loadingKbs}
                      className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
                      title="刷新知识库资产"
                    >
                      <RefreshCw className={`w-3.5 h-3.5 ${loadingKbs ? 'animate-spin' : ''}`} />
                    </button>
                  </div>
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

                            <div className="flex items-center gap-1.5">
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleViewKbChunks(kb, 1, '');
                                }}
                                className="flex items-center gap-1 px-2.5 py-1 text-xs text-sky-600 dark:text-sky-400 hover:text-sky-700 bg-sky-500/10 hover:bg-sky-500/15 rounded-lg border border-sky-500/20 transition-all font-medium"
                                title="查看此知识库中已训练切分的规程分块详情"
                              >
                                <FileText className="w-3.5 h-3.5 text-sky-500" />
                                <span>查看切片</span>
                              </button>

                              <button
                                type="button"
                                disabled={extractingGraphKbId === kb.kb_id}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleExtractGraph(kb.kb_id);
                                }}
                                className="flex items-center gap-1 px-2.5 py-1 text-xs text-amber-600 dark:text-amber-400 hover:text-amber-700 bg-amber-500/10 hover:bg-amber-500/15 rounded-lg border border-amber-500/20 transition-all font-medium disabled:opacity-50"
                                title="对已有切片重新批量提取知识图谱三元组"
                              >
                                <Zap className={`w-3.5 h-3.5 text-amber-500 ${extractingGraphKbId === kb.kb_id ? 'animate-bounce' : ''}`} />
                                <span>{extractingGraphKbId === kb.kb_id ? '提取中...' : '提取图谱'}</span>
                              </button>

                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleViewKbChunks(kb, 1, '');
                                }}
                                className="flex items-center gap-1 px-2.5 py-1 text-xs text-sky-600 dark:text-sky-400 hover:text-sky-700 bg-sky-500/10 hover:bg-sky-500/15 rounded-lg border border-sky-500/20 transition-all font-medium"
                                title="查看此知识库中已训练切分的规程分块详情"
                              >
                                <FileText className="w-3.5 h-3.5 text-sky-500" />
                                <span>查看切片</span>
                              </button>

                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleExtractGraph(kb.kb_id);
                                }}
                                className="flex items-center gap-1 px-2.5 py-1 text-xs text-amber-600 dark:text-amber-400 hover:text-amber-700 bg-amber-500/10 hover:bg-amber-500/15 rounded-lg border border-amber-500/20 transition-all font-medium disabled:opacity-50"
                                title="对已有切片重新批量提取知识图谱三元组"
                              >
                                <Zap className={`w-3.5 h-3.5 text-amber-500 ${extractingGraphKbId === kb.kb_id ? 'animate-bounce' : ''}`} />
                                <span>{extractingGraphKbId === kb.kb_id ? '提取中...' : '提取图谱'}</span>
                              </button>

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
                                <span>瘦身</span>
                              </button>

                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDeleteKb(kb.kb_id, kb.name);
                                }}
                                className="flex items-center gap-1 px-2 py-1 text-xs text-rose-500 hover:text-rose-600 hover:bg-rose-500/10 rounded-lg border border-rose-500/20 transition-colors"
                                title="彻底物理销毁此知识库及历史快照"
                              >
                                <Trash2 className="w-3 h-3" />
                                <span>清理</span>
                              </button>
                            </div>
                          </div>

                          <div className="grid grid-cols-3 gap-2 py-2 px-3 rounded-lg bg-surface-card border border-border/60 text-[11px]">
                            <div
                              onClick={(e) => {
                                e.stopPropagation();
                                handleViewKbChunks(kb, 1, '');
                              }}
                              className="cursor-pointer hover:bg-surface-200/50 p-1 rounded-md transition-colors"
                              title="点击查看所有规程切片明细"
                            >
                              <span className="text-muted-foreground flex items-center gap-1">
                                <span>规程切片数</span>
                                <FileText className="w-3 h-3 text-sky-500" />
                              </span>
                              <strong className="text-foreground text-xs hover:text-sky-500 underline underline-offset-2">
                                {kb.chunk_count}
                              </strong>
                            </div>

                            <div
                              onClick={(e) => {
                                e.stopPropagation();
                                handleViewKbTriples(kb, 1, '');
                              }}
                              className="cursor-pointer hover:bg-surface-200/50 p-1 rounded-md transition-colors"
                              title="点击查看知识图谱三元组资产明细"
                            >
                              <span className="text-muted-foreground flex items-center gap-1">
                                <span>图谱三元组</span>
                                <Network className="w-3 h-3 text-amber-500" />
                              </span>
                              <strong className="text-foreground text-xs hover:text-amber-500 underline underline-offset-2">
                                {kb.triple_count}
                              </strong>
                            </div>

                            <div className="p-1">
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
                        {playgroundHits.map((h, i) => {
                          const isExpanded = expandedHitIdx === i;
                          return (
                            <div
                              key={i}
                              className="p-3 rounded-xl bg-surface-100/60 border border-border text-xs space-y-2 hover:border-border/80 transition-all"
                            >
                              <div className="flex items-center justify-between">
                                <span className="font-semibold text-foreground truncate max-w-[210px]" title={h.title}>
                                  {h.title}
                                </span>
                                <div className="flex items-center gap-1.5 shrink-0">
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-500/10 text-emerald-500 font-semibold">
                                    得分: {h.final_score.toFixed(3)}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={() => handleCopyChunkText(`hit_${i}`, h.content)}
                                    className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-surface-200 transition"
                                    title="复制此切片原文"
                                  >
                                    {copiedChunkId === `hit_${i}` ? (
                                      <Check className="w-3 h-3 text-emerald-500" />
                                    ) : (
                                      <Copy className="w-3 h-3" />
                                    )}
                                  </button>
                                </div>
                              </div>
                              <p
                                className={`text-muted-foreground text-[11px] leading-relaxed whitespace-pre-wrap ${
                                  isExpanded ? '' : 'line-clamp-4'
                                }`}
                              >
                                {h.content}
                              </p>
                              {h.content.length > 140 && (
                                <button
                                  type="button"
                                  onClick={() => setExpandedHitIdx(isExpanded ? null : i)}
                                  className="text-[10px] text-sky-500 hover:text-sky-600 font-medium"
                                >
                                  {isExpanded ? '收起原文' : '展开查看切片全文...'}
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 空间文件列表浏览器模态框 */}
      {viewingMount && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-xs p-4 animate-fade-in"
          onClick={() => setViewingMount(null)}
        >
          <div
            className="w-full max-w-4xl max-h-[85vh] rounded-2xl border border-border bg-surface-card shadow-2xl flex flex-col overflow-hidden text-foreground animate-scale-in"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 模态框顶部 Header */}
            <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-4 bg-surface-100/60">
              <div className="flex items-center space-x-3 min-w-0">
                <div className="p-2 rounded-xl bg-sky-500/10 text-sky-500 border border-sky-500/20">
                  <FolderOpen className="w-5 h-5" />
                </div>
                <div className="min-w-0">
                  <div className="flex items-center space-x-2">
                    <h3 className="text-base font-bold text-foreground truncate">
                      {viewingMount.mount_name} · 空间文件列表
                    </h3>
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-mono uppercase bg-surface-200 text-muted-foreground border border-border">
                      {viewingMount.provider_type}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-xl">
                    {viewingMount.provider_type === 'wecom' && `微盘 SpaceId: ${viewingMount.config?.space_id || '未指定'}`}
                    {viewingMount.provider_type === 'webdav' && `WebDAV URL: ${viewingMount.config?.base_url || '未指定'}`}
                    {viewingMount.provider_type === 'oss' && `OSS Bucket: ${viewingMount.config?.bucket || '未指定'}`}
                  </p>
                </div>
              </div>

              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  onClick={() => handleViewFiles(viewingMount)}
                  disabled={loadingFiles}
                  className="flex items-center space-x-1 px-3 py-1.5 rounded-lg border border-border bg-surface-card hover:bg-surface-200 text-foreground text-xs font-medium transition disabled:opacity-50"
                  title="重新拉取微盘远程文件树"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingFiles ? 'animate-spin text-sky-500' : ''}`} />
                  <span>刷新</span>
                </button>
                <button
                  type="button"
                  onClick={() => setViewingMount(null)}
                  className="p-1.5 rounded-lg hover:bg-surface-200 text-muted-foreground hover:text-foreground transition"
                  title="关闭"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* 模态框二级搜索工具栏 */}
            <div className="px-6 py-2.5 border-b border-border bg-surface-100/30 flex items-center justify-between gap-4">
              <div className="relative flex-1 max-w-sm">
                <Search className="w-3.5 h-3.5 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="搜索空间中的文件名..."
                  value={filesSearchQuery}
                  onChange={(e) => setFilesSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-surface-200 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-sky-500"
                />
              </div>

              <div className="text-xs text-muted-foreground">
                共检索到 <strong className="text-foreground">{mountFiles.length}</strong> 个空间文件
              </div>
            </div>

            {/* 模态框主体内容列表 */}
            <div className="flex-1 overflow-y-auto p-6 space-y-3">
              {loadingFiles ? (
                <div className="py-20 text-center text-xs text-muted-foreground flex flex-col items-center justify-center space-y-2">
                  <RefreshCw className="w-6 h-6 animate-spin text-sky-500" />
                  <div>正在实时连接微盘空间拉取文件清单...</div>
                </div>
              ) : filesError ? (
                <div className="py-12 text-center text-xs text-rose-500 border border-dashed border-rose-500/30 rounded-xl p-6 bg-rose-500/5 space-y-2">
                  <AlertCircle className="w-8 h-8 text-rose-500 mx-auto" />
                  <div className="font-semibold text-sm">读取空间文件失败</div>
                  <p className="text-xs text-muted-foreground max-w-md mx-auto">{filesError}</p>
                </div>
              ) : mountFiles.length === 0 ? (
                <div className="py-16 text-center text-xs text-muted-foreground border border-dashed border-border rounded-xl space-y-2 bg-surface-100/20">
                  <FolderOpen className="w-8 h-8 text-muted-foreground/40 mx-auto" />
                  <div>该微盘空间根目录下暂无文件</div>
                  <p className="text-[11px] text-muted-foreground/80 max-w-xs mx-auto">
                    请在企业微信电脑端或微盘中将规程文档上传至该空间，然后点击右上角刷新。
                  </p>
                </div>
              ) : (
                <div className="rounded-xl border border-border overflow-hidden bg-surface-card">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-surface-100/80 border-b border-border text-muted-foreground font-medium">
                      <tr>
                        <th className="py-2.5 px-4">文件名称</th>
                        <th className="py-2.5 px-4 w-28">大小</th>
                        <th className="py-2.5 px-4 w-36">切片支持</th>
                        <th className="py-2.5 px-4 w-40">最后修改时间</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/60">
                      {mountFiles
                        .filter((f) =>
                          !filesSearchQuery.trim() ||
                          f.title.toLowerCase().includes(filesSearchQuery.toLowerCase())
                        )
                        .map((f, idx) => {
                          const isPdf = f.title.toLowerCase().endsWith('.pdf');
                          const isDocx = /\.(docx?|doc)$/i.test(f.title);
                          const isXlsx = /\.(xlsx?|xls|csv)$/i.test(f.title);
                          const isMd = /\.(md|markdown|txt)$/i.test(f.title);

                          return (
                            <tr key={idx} className="hover:bg-surface-100/50 transition-colors">
                              <td className="py-2.5 px-4">
                                <div className="flex items-center space-x-2.5 min-w-0">
                                  {f.is_dir ? (
                                    <FolderOpen className="w-4 h-4 text-sky-500 shrink-0" />
                                  ) : isPdf ? (
                                    <FileText className="w-4 h-4 text-rose-500 shrink-0" />
                                  ) : isDocx ? (
                                    <FileText className="w-4 h-4 text-blue-500 shrink-0" />
                                  ) : isXlsx ? (
                                    <FileSpreadsheet className="w-4 h-4 text-emerald-500 shrink-0" />
                                  ) : isMd ? (
                                    <FileCode className="w-4 h-4 text-amber-500 shrink-0" />
                                  ) : (
                                    <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                                  )}
                                  <span className="font-medium text-foreground truncate max-w-md">
                                    {f.title}
                                  </span>
                                </div>
                              </td>
                              <td className="py-2.5 px-4 font-mono text-[11px] text-muted-foreground">
                                {f.is_dir ? '—' : f.size_formatted || `${(f.size / 1024).toFixed(1)} KB`}
                              </td>
                              <td className="py-2.5 px-4">
                                {f.is_dir ? (
                                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] bg-sky-500/10 text-sky-500 font-medium">
                                    📁 目录
                                  </span>
                                ) : f.supported ? (
                                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium">
                                    <CheckCircle2 className="w-3 h-3" />
                                    <span>可切片入库</span>
                                  </span>
                                ) : (
                                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] bg-surface-200 text-muted-foreground">
                                    纯文本嗅探
                                  </span>
                                )}
                              </td>
                              <td className="py-2.5 px-4 text-muted-foreground font-mono text-[11px]">
                                {f.mtime ? new Date(f.mtime * 1000).toLocaleString('zh-CN') : '—'}
                              </td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* 模态框底部 Footer */}
            <div className="px-6 py-3 border-t border-border bg-surface-100/60 flex items-center justify-between text-xs">
              <span className="text-muted-foreground">
                提示：空间内所有规程文档将在点击【知识库训练】时自动增量解析并提取语义切片。
              </span>
              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  onClick={() => setViewingMount(null)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:text-foreground hover:bg-surface-200 transition"
                >
                  关闭
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedMountForTrain(viewingMount.mount_id);
                    setViewingMount(null);
                    setActiveSection('train');
                  }}
                  className="flex items-center space-x-1 px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-700 text-white text-xs font-semibold shadow-sm transition"
                >
                  <span>前往训练该数据源</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 知识库规程切片详情浏览器模态框 */}
      {viewingChunksKb && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-xs p-4 animate-fade-in"
          onClick={() => setViewingChunksKb(null)}
        >
          <div
            className="w-full max-w-4xl max-h-[85vh] rounded-2xl border border-border bg-surface-card shadow-2xl flex flex-col overflow-hidden text-foreground animate-scale-in"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 模态框顶部 Header */}
            <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-4 bg-surface-100/60">
              <div className="flex items-center space-x-3 min-w-0">
                <div className="p-2 rounded-xl bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
                  <FileText className="w-5 h-5" />
                </div>
                <div className="min-w-0">
                  <div className="flex items-center space-x-2">
                    <h3 className="text-base font-bold text-foreground truncate">
                      {viewingChunksKb.name} · 规程切片与分块详情
                    </h3>
                    <span className="px-2 py-0.5 rounded font-mono text-[10px] bg-surface-200 text-foreground border border-border">
                      {viewingChunksKb.active_version}
                    </span>
                  </div>
                  <div className="flex items-center space-x-2 text-xs text-muted-foreground mt-0.5">
                    <span>总计纳管切片：<strong className="text-foreground">{totalChunksCount}</strong> 个</span>
                    <span>·</span>
                    <span>独立 SQLite index.db 存储</span>
                  </div>
                </div>
              </div>

              <button
                type="button"
                onClick={() => setViewingChunksKb(null)}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 搜索与工具栏 */}
            <div className="p-4 border-b border-border bg-surface-100/40 flex items-center justify-between gap-3">
              <div className="relative flex-1 max-w-md">
                <Search className="w-4 h-4 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="搜索切片内容或文件标题（如: 报名、U10、参赛办法）..."
                  value={chunksSearchQuery}
                  onChange={(e) => {
                    setChunksSearchQuery(e.target.value);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleViewKbChunks(viewingChunksKb, 1, chunksSearchQuery);
                    }
                  }}
                  className="w-full pl-9 pr-3 py-1.5 rounded-xl border border-border bg-surface-card text-xs text-foreground placeholder:text-muted-foreground outline-none focus:border-sky-500 transition"
                />
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleViewKbChunks(viewingChunksKb, chunksPage, chunksSearchQuery)}
                  disabled={loadingChunks}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-border bg-surface-card text-xs text-foreground hover:bg-surface-200 transition"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingChunks ? 'animate-spin' : ''}`} />
                  <span>刷新</span>
                </button>
              </div>
            </div>

            {/* 切片卡片列表 Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              {loadingChunks ? (
                <div className="py-16 text-center text-xs text-muted-foreground space-y-2">
                  <RefreshCw className="w-6 h-6 animate-spin mx-auto text-sky-500" />
                  <div>正在加载规程切片与分块详情...</div>
                </div>
              ) : kbChunks.length === 0 ? (
                <div className="py-16 text-center text-xs text-muted-foreground space-y-2">
                  <FileText className="w-8 h-8 mx-auto text-muted-foreground/40" />
                  <div>暂未匹配到切片内容</div>
                  <p className="text-[11px] text-muted-foreground/80">
                    请尝试更换搜索关键字，或在上方点击“启动增量训练”重新解析
                  </p>
                </div>
              ) : (
                kbChunks.map((c, idx) => (
                  <div
                    key={c.id || idx}
                    className="rounded-xl border border-border bg-surface-100/50 p-4 space-y-3 hover:border-border/80 transition-all shadow-xs"
                  >
                    <div className="flex items-center justify-between border-b border-border/60 pb-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-mono text-[11px] font-semibold text-sky-500 bg-sky-500/10 px-2 py-0.5 rounded">
                          #{(chunksPage - 1) * 20 + idx + 1}
                        </span>
                        <h4 className="font-semibold text-xs text-foreground truncate max-w-lg" title={c.title}>
                          {c.title}
                        </h4>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[10px] text-muted-foreground font-mono">
                          {c.char_count || c.content?.length || 0} 字符
                        </span>
                        <span className="text-[10px] rounded px-1.5 py-0.5 bg-surface-200 text-muted-foreground font-medium">
                          Tier {c.authority_tier}
                        </span>
                        <button
                          type="button"
                          onClick={() => handleCopyChunkText(c.id, c.content)}
                          className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-muted-foreground hover:text-foreground hover:bg-surface-200 border border-border transition"
                          title="复制切片原文"
                        >
                          {copiedChunkId === c.id ? (
                            <>
                              <Check className="w-3 h-3 text-emerald-500" />
                              <span className="text-emerald-500 text-[10px]">已复制</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3" />
                              <span className="text-[10px]">复制</span>
                            </>
                          )}
                        </button>
                      </div>
                    </div>

                    <div className="rounded-lg bg-surface-card p-3 border border-border/50 text-xs text-foreground/90 font-mono leading-relaxed whitespace-pre-wrap max-h-56 overflow-y-auto">
                      {c.content}
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* 模态框底部 Footer 与分页 */}
            <div className="px-6 py-3 border-t border-border bg-surface-100/60 flex items-center justify-between text-xs">
              <span className="text-muted-foreground">
                共 {totalChunksCount} 条切片 · 当前第 {chunksPage} / {Math.ceil(totalChunksCount / 20) || 1} 页
              </span>

              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  disabled={chunksPage <= 1 || loadingChunks}
                  onClick={() => handleViewKbChunks(viewingChunksKb, chunksPage - 1, chunksSearchQuery)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-200 transition disabled:opacity-40"
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={chunksPage * 20 >= totalChunksCount || loadingChunks}
                  onClick={() => handleViewKbChunks(viewingChunksKb, chunksPage + 1, chunksSearchQuery)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-200 transition disabled:opacity-40"
                >
                  下一页
                </button>
                <button
                  type="button"
                  onClick={() => setViewingChunksKb(null)}
                  className="px-3 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-xs text-foreground font-medium transition"
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 知识图谱三元组资产浏览器模态框 */}
      {viewingTriplesKb && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-xs p-4 animate-fade-in"
          onClick={() => setViewingTriplesKb(null)}
        >
          <div
            className="w-full max-w-4xl max-h-[85vh] rounded-2xl border border-border bg-surface-card shadow-2xl flex flex-col overflow-hidden text-foreground animate-scale-in"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 模态框顶部 Header */}
            <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-4 bg-surface-100/60">
              <div className="flex items-center space-x-3 min-w-0">
                <div className="p-2 rounded-xl bg-amber-500/10 text-amber-500 border border-amber-500/20">
                  <Network className="w-5 h-5" />
                </div>
                <div className="min-w-0">
                  <div className="flex items-center space-x-2">
                    <h3 className="text-base font-bold text-foreground truncate">
                      {viewingTriplesKb.name} · 知识图谱三元组资产清单
                    </h3>
                    <span className="px-2 py-0.5 rounded font-mono text-[10px] bg-surface-200 text-foreground border border-border">
                      {viewingTriplesKb.active_version}
                    </span>
                  </div>
                  <div className="flex items-center space-x-2 text-xs text-muted-foreground mt-0.5">
                    <span>总计关系边：<strong className="text-foreground">{totalTriplesCount}</strong> 条</span>
                    <span>·</span>
                    <span>LightGraphRAG 实体语义网络</span>
                  </div>
                </div>
              </div>

              <button
                type="button"
                onClick={() => setViewingTriplesKb(null)}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 搜索与工具栏 */}
            <div className="p-4 border-b border-border bg-surface-100/40 flex items-center justify-between gap-3">
              <div className="relative flex-1 max-w-md">
                <Search className="w-4 h-4 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="搜索图谱主体、关系谓词或客体（如: 主办单位、初中组、U12）..."
                  value={triplesSearchQuery}
                  onChange={(e) => setTriplesSearchQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleViewKbTriples(viewingTriplesKb, 1, triplesSearchQuery);
                    }
                  }}
                  className="w-full pl-9 pr-3 py-1.5 rounded-xl border border-border bg-surface-card text-xs text-foreground placeholder:text-muted-foreground outline-none focus:border-amber-500 transition"
                />
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleViewKbTriples(viewingTriplesKb, triplesPage, triplesSearchQuery)}
                  disabled={loadingTriples}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-border bg-surface-card text-xs text-foreground hover:bg-surface-200 transition"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingTriples ? 'animate-spin' : ''}`} />
                  <span>刷新</span>
                </button>
              </div>
            </div>

            {/* 三元组卡片列表 Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-3">
              {loadingTriples ? (
                <div className="py-16 text-center text-xs text-muted-foreground space-y-2">
                  <RefreshCw className="w-6 h-6 animate-spin mx-auto text-amber-500" />
                  <div>正在加载图谱三元组资产...</div>
                </div>
              ) : kbTriples.length === 0 ? (
                <div className="py-16 text-center text-xs text-muted-foreground space-y-2">
                  <Network className="w-8 h-8 mx-auto text-muted-foreground/40" />
                  <div>暂未匹配到图谱三元组关系</div>
                  <p className="text-[11px] text-muted-foreground/80">
                    请尝试更换搜索关键字，或在卡片上点击“提取图谱”进行实体关系构建
                  </p>
                </div>
              ) : (
                kbTriples.map((t, idx) => (
                  <div
                    key={t.id || idx}
                    className="rounded-xl border border-border bg-surface-100/50 p-3.5 flex items-center justify-between gap-3 text-xs hover:border-border/80 transition-all shadow-xs"
                  >
                    <div className="flex items-center gap-2 flex-wrap min-w-0">
                      <span className="font-mono text-[10px] text-muted-foreground px-1.5 py-0.5 rounded bg-surface-200 shrink-0">
                        #{(triplesPage - 1) * 30 + idx + 1}
                      </span>

                      {/* 主体 */}
                      <span className="font-semibold px-2.5 py-1 rounded-lg bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/20 max-w-xs truncate" title={t.subject}>
                        {t.subject}
                      </span>

                      {/* 谓词/关系 */}
                      <span className="flex items-center gap-1 font-mono text-[11px] px-2 py-0.5 rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20 font-medium">
                        <span>--[ {t.predicate} ]--&gt;</span>
                      </span>

                      {/* 客体 */}
                      <span className="px-2.5 py-1 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 max-w-md truncate" title={t.object}>
                        {t.object}
                      </span>
                    </div>

                    <button
                      type="button"
                      onClick={() => handleCopyChunkText(`trip_${t.id}`, `(${t.subject}) --[${t.predicate}]--> (${t.object})`)}
                      className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-muted-foreground hover:text-foreground hover:bg-surface-200 border border-border shrink-0 transition"
                      title="复制三元组"
                    >
                      {copiedChunkId === `trip_${t.id}` ? (
                        <>
                          <Check className="w-3 h-3 text-emerald-500" />
                          <span className="text-emerald-500 text-[10px]">已复制</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3 h-3" />
                          <span className="text-[10px]">复制</span>
                        </>
                      )}
                    </button>
                  </div>
                ))
              )}
            </div>

            {/* 模态框底部 Footer 与分页 */}
            <div className="px-6 py-3 border-t border-border bg-surface-100/60 flex items-center justify-between text-xs">
              <span className="text-muted-foreground">
                共 {totalTriplesCount} 条关系三元组 · 当前第 {triplesPage} / {Math.ceil(totalTriplesCount / 30) || 1} 页
              </span>

              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  disabled={triplesPage <= 1 || loadingTriples}
                  onClick={() => handleViewKbTriples(viewingTriplesKb, triplesPage - 1, triplesSearchQuery)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-200 transition disabled:opacity-40"
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={triplesPage * 30 >= totalTriplesCount || loadingTriples}
                  onClick={() => handleViewKbTriples(viewingTriplesKb, triplesPage + 1, triplesSearchQuery)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-200 transition disabled:opacity-40"
                >
                  下一页
                </button>
                <button
                  type="button"
                  onClick={() => setViewingTriplesKb(null)}
                  className="px-3 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-xs text-foreground font-medium transition"
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 知识库规程切片详情浏览器模态框 */}
      {viewingChunksKb && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-xs p-4 animate-fade-in"
          onClick={() => setViewingChunksKb(null)}
        >
          <div
            className="w-full max-w-4xl max-h-[85vh] rounded-2xl border border-border bg-surface-card shadow-2xl flex flex-col overflow-hidden text-foreground animate-scale-in"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 模态框顶部 Header */}
            <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-4 bg-surface-100/60">
              <div className="flex items-center space-x-3 min-w-0">
                <div className="p-2 rounded-xl bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
                  <FileText className="w-5 h-5" />
                </div>
                <div className="min-w-0">
                  <div className="flex items-center space-x-2">
                    <h3 className="text-base font-bold text-foreground truncate">
                      {viewingChunksKb.name} · 规程切片与分块详情
                    </h3>
                    <span className="px-2 py-0.5 rounded font-mono text-[10px] bg-surface-200 text-foreground border border-border">
                      {viewingChunksKb.active_version}
                    </span>
                  </div>
                  <div className="flex items-center space-x-2 text-xs text-muted-foreground mt-0.5">
                    <span>总计纳管切片：<strong className="text-foreground">{totalChunksCount}</strong> 个</span>
                    <span>·</span>
                    <span>独立 SQLite index.db 存储</span>
                  </div>
                </div>
              </div>

              <button
                type="button"
                onClick={() => setViewingChunksKb(null)}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 搜索与工具栏 */}
            <div className="p-4 border-b border-border bg-surface-100/40 flex items-center justify-between gap-3">
              <div className="relative flex-1 max-w-md">
                <Search className="w-4 h-4 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="搜索切片内容或文件标题（如: 报名、U10、参赛办法）..."
                  value={chunksSearchQuery}
                  onChange={(e) => {
                    setChunksSearchQuery(e.target.value);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleViewKbChunks(viewingChunksKb, 1, chunksSearchQuery);
                    }
                  }}
                  className="w-full pl-9 pr-3 py-1.5 rounded-xl border border-border bg-surface-card text-xs text-foreground placeholder:text-muted-foreground outline-none focus:border-sky-500 transition"
                />
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleViewKbChunks(viewingChunksKb, chunksPage, chunksSearchQuery)}
                  disabled={loadingChunks}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-border bg-surface-card text-xs text-foreground hover:bg-surface-200 transition"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingChunks ? 'animate-spin' : ''}`} />
                  <span>刷新</span>
                </button>
              </div>
            </div>

            {/* 切片卡片列表 Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              {loadingChunks ? (
                <div className="py-16 text-center text-xs text-muted-foreground space-y-2">
                  <RefreshCw className="w-6 h-6 animate-spin mx-auto text-sky-500" />
                  <div>正在加载规程切片与分块详情...</div>
                </div>
              ) : kbChunks.length === 0 ? (
                <div className="py-16 text-center text-xs text-muted-foreground space-y-2">
                  <FileText className="w-8 h-8 mx-auto text-muted-foreground/40" />
                  <div>暂未匹配到切片内容</div>
                  <p className="text-[11px] text-muted-foreground/80">
                    请尝试更换搜索关键字，或在上方点击“启动增量训练”重新解析
                  </p>
                </div>
              ) : (
                kbChunks.map((c, idx) => (
                  <div
                    key={c.id || idx}
                    className="rounded-xl border border-border bg-surface-100/50 p-4 space-y-3 hover:border-border/80 transition-all shadow-xs"
                  >
                    <div className="flex items-center justify-between border-b border-border/60 pb-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-mono text-[11px] font-semibold text-sky-500 bg-sky-500/10 px-2 py-0.5 rounded">
                          #{(chunksPage - 1) * 20 + idx + 1}
                        </span>
                        <h4 className="font-semibold text-xs text-foreground truncate max-w-lg" title={c.title}>
                          {c.title}
                        </h4>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[10px] text-muted-foreground font-mono">
                          {c.char_count || c.content?.length || 0} 字符
                        </span>
                        <span className="text-[10px] rounded px-1.5 py-0.5 bg-surface-200 text-muted-foreground font-medium">
                          Tier {c.authority_tier}
                        </span>
                        <button
                          type="button"
                          onClick={() => handleCopyChunkText(c.id, c.content)}
                          className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-muted-foreground hover:text-foreground hover:bg-surface-200 border border-border transition"
                          title="复制切片原文"
                        >
                          {copiedChunkId === c.id ? (
                            <>
                              <Check className="w-3 h-3 text-emerald-500" />
                              <span className="text-emerald-500 text-[10px]">已复制</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3" />
                              <span className="text-[10px]">复制</span>
                            </>
                          )}
                        </button>
                      </div>
                    </div>

                    <div className="rounded-lg bg-surface-card p-3 border border-border/50 text-xs text-foreground/90 font-mono leading-relaxed whitespace-pre-wrap max-h-56 overflow-y-auto">
                      {c.content}
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* 模态框底部 Footer 与分页 */}
            <div className="px-6 py-3 border-t border-border bg-surface-100/60 flex items-center justify-between text-xs">
              <span className="text-muted-foreground">
                共 {totalChunksCount} 条切片 · 当前第 {chunksPage} / {Math.ceil(totalChunksCount / 20) || 1} 页
              </span>

              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  disabled={chunksPage <= 1 || loadingChunks}
                  onClick={() => handleViewKbChunks(viewingChunksKb, chunksPage - 1, chunksSearchQuery)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-200 transition disabled:opacity-40"
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={chunksPage * 20 >= totalChunksCount || loadingChunks}
                  onClick={() => handleViewKbChunks(viewingChunksKb, chunksPage + 1, chunksSearchQuery)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-200 transition disabled:opacity-40"
                >
                  下一页
                </button>
                <button
                  type="button"
                  onClick={() => setViewingChunksKb(null)}
                  className="px-3 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-xs text-foreground font-medium transition"
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 知识图谱三元组资产浏览器模态框 */}
      {viewingTriplesKb && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-xs p-4 animate-fade-in"
          onClick={() => setViewingTriplesKb(null)}
        >
          <div
            className="w-full max-w-4xl max-h-[85vh] rounded-2xl border border-border bg-surface-card shadow-2xl flex flex-col overflow-hidden text-foreground animate-scale-in"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 模态框顶部 Header */}
            <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-4 bg-surface-100/60">
              <div className="flex items-center space-x-3 min-w-0">
                <div className="p-2 rounded-xl bg-amber-500/10 text-amber-500 border border-amber-500/20">
                  <Network className="w-5 h-5" />
                </div>
                <div className="min-w-0">
                  <div className="flex items-center space-x-2">
                    <h3 className="text-base font-bold text-foreground truncate">
                      {viewingTriplesKb.name} · 知识图谱三元组资产清单
                    </h3>
                    <span className="px-2 py-0.5 rounded font-mono text-[10px] bg-surface-200 text-foreground border border-border">
                      {viewingTriplesKb.active_version}
                    </span>
                  </div>
                  <div className="flex items-center space-x-2 text-xs text-muted-foreground mt-0.5">
                    <span>总计关系边：<strong className="text-foreground">{totalTriplesCount}</strong> 条</span>
                    <span>·</span>
                    <span>LightGraphRAG 实体语义网络</span>
                  </div>
                </div>
              </div>

              <button
                type="button"
                onClick={() => setViewingTriplesKb(null)}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 搜索与工具栏 */}
            <div className="p-4 border-b border-border bg-surface-100/40 flex items-center justify-between gap-3">
              <div className="relative flex-1 max-w-md">
                <Search className="w-4 h-4 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="搜索图谱主体、关系谓词或客体（如: 主办单位、初中组、U12）..."
                  value={triplesSearchQuery}
                  onChange={(e) => setTriplesSearchQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleViewKbTriples(viewingTriplesKb, 1, triplesSearchQuery);
                    }
                  }}
                  className="w-full pl-9 pr-3 py-1.5 rounded-xl border border-border bg-surface-card text-xs text-foreground placeholder:text-muted-foreground outline-none focus:border-amber-500 transition"
                />
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleViewKbTriples(viewingTriplesKb, triplesPage, triplesSearchQuery)}
                  disabled={loadingTriples}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-border bg-surface-card text-xs text-foreground hover:bg-surface-200 transition"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingTriples ? 'animate-spin' : ''}`} />
                  <span>刷新</span>
                </button>
              </div>
            </div>

            {/* 三元组卡片列表 Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-3">
              {loadingTriples ? (
                <div className="py-16 text-center text-xs text-muted-foreground space-y-2">
                  <RefreshCw className="w-6 h-6 animate-spin mx-auto text-amber-500" />
                  <div>正在加载图谱三元组资产...</div>
                </div>
              ) : kbTriples.length === 0 ? (
                <div className="py-16 text-center text-xs text-muted-foreground space-y-2">
                  <Network className="w-8 h-8 mx-auto text-muted-foreground/40" />
                  <div>暂未匹配到图谱三元组关系</div>
                  <p className="text-[11px] text-muted-foreground/80">
                    请尝试更换搜索关键字，或在卡片上点击“提取图谱”进行实体关系构建
                  </p>
                </div>
              ) : (
                kbTriples.map((t, idx) => (
                  <div
                    key={t.id || idx}
                    className="rounded-xl border border-border bg-surface-100/50 p-3.5 flex items-center justify-between gap-3 text-xs hover:border-border/80 transition-all shadow-xs"
                  >
                    <div className="flex items-center gap-2 flex-wrap min-w-0">
                      <span className="font-mono text-[10px] text-muted-foreground px-1.5 py-0.5 rounded bg-surface-200 shrink-0">
                        #{(triplesPage - 1) * 30 + idx + 1}
                      </span>

                      {/* 主体 */}
                      <span className="font-semibold px-2.5 py-1 rounded-lg bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/20 max-w-xs truncate" title={t.subject}>
                        {t.subject}
                      </span>

                      {/* 谓词/关系 */}
                      <span className="flex items-center gap-1 font-mono text-[11px] px-2 py-0.5 rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20 font-medium">
                        <span>--[ {t.predicate} ]--&gt;</span>
                      </span>

                      {/* 客体 */}
                      <span className="px-2.5 py-1 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 max-w-md truncate" title={t.object}>
                        {t.object}
                      </span>
                    </div>

                    <button
                      type="button"
                      onClick={() => handleCopyChunkText(`trip_${t.id}`, `(${t.subject}) --[${t.predicate}]--> (${t.object})`)}
                      className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-muted-foreground hover:text-foreground hover:bg-surface-200 border border-border shrink-0 transition"
                      title="复制三元组"
                    >
                      {copiedChunkId === `trip_${t.id}` ? (
                        <>
                          <Check className="w-3 h-3 text-emerald-500" />
                          <span className="text-emerald-500 text-[10px]">已复制</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3 h-3" />
                          <span className="text-[10px]">复制</span>
                        </>
                      )}
                    </button>
                  </div>
                ))
              )}
            </div>

            {/* 模态框底部 Footer 与分页 */}
            <div className="px-6 py-3 border-t border-border bg-surface-100/60 flex items-center justify-between text-xs">
              <span className="text-muted-foreground">
                共 {totalTriplesCount} 条关系三元组 · 当前第 {triplesPage} / {Math.ceil(totalTriplesCount / 30) || 1} 页
              </span>

              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  disabled={triplesPage <= 1 || loadingTriples}
                  onClick={() => handleViewKbTriples(viewingTriplesKb, triplesPage - 1, triplesSearchQuery)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-200 transition disabled:opacity-40"
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={triplesPage * 30 >= totalTriplesCount || loadingTriples}
                  onClick={() => handleViewKbTriples(viewingTriplesKb, triplesPage + 1, triplesSearchQuery)}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-200 transition disabled:opacity-40"
                >
                  下一页
                </button>
                <button
                  type="button"
                  onClick={() => setViewingTriplesKb(null)}
                  className="px-3 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-xs text-foreground font-medium transition"
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

