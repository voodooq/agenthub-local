'use client';

import React, { useState, useEffect, useCallback } from 'react';
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
  Upload,
  Download,
  History,
  FlaskConical,
  MessageSquare,
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  HelpCircle,
  Edit2,
  Plus,
  ShieldAlert,
  MoreHorizontal,
} from 'lucide-react';
import { Workspace } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import { useDevDebugMode } from '@/hooks/useDevDebugMode';
import { WikiWorkspaceView } from './wiki/WikiWorkspaceView';
import { KBImportConflictModal } from './KBImportConflictModal';
import { KBVersionDrawer } from './KBVersionDrawer';

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
  domain_type?: string;
  rule_profile_json?: string;
  is_published?: boolean;
}

const DOMAIN_OPTIONS = [
  { id: 'general', label: '📋 通用规章', desc: '企业财务报销与综合制度' },
  { id: 'fencing', label: '🤺 击剑赛事', desc: '击剑联赛/组别/器材/350N/800N' },
  { id: 'equestrian', label: '🐎 马术赛事', desc: '场地障碍/盛装舞步/通级/马匹检疫' },
  { id: 'rugby', label: '🏉 橄榄球规程', desc: '7人/15人制/触式/达阵/红黄牌' },
  { id: 'custom', label: '⚙️ 自定义行业', desc: 'AI自动提取首份文档规则' },
];

export function LLMWikiView({ workspace, onOpenChat }: LLMWikiViewProps) {
  // 四大主功能区：'wiki' = 百科工作台 (默认), 'train' = 资产清单, 'mount' = 数据源挂载, 'test' = 召回测试
  const [activeSection, setActiveSection] = useState<'wiki' | 'test' | 'train' | 'mount'>('wiki');
  const { isDevDebug } = useDevDebugMode();
  const [actionMenuKbId, setActionMenuKbId] = useState<string | null>(null);

  useEffect(() => {
    const handleGlobalClick = () => setActionMenuKbId(null);
    window.addEventListener('click', handleGlobalClick);
    return () => window.removeEventListener('click', handleGlobalClick);
  }, []);

  // ── 知识库测试 (Playground) 与版本发布管理状态 ──
  const [selectedVersion, setSelectedVersion] = useState<string>('');
  const [kbVersionsList, setKbVersionsList] = useState<any[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [isPublishingVersion, setIsPublishingVersion] = useState(false);

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
  const [playgroundSimulatedAnswer, setPlaygroundSimulatedAnswer] = useState<string | null>(null);
  const [playgroundGroundingStatus, setPlaygroundGroundingStatus] = useState<any | null>(null);
  const [togglingPublishKbId, setTogglingPublishKbId] = useState<string | null>(null);

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
  const [totalAllRowsCount, setTotalAllRowsCount] = useState<number>(0);
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

  // ── v1.5.9 导入导出与版本抽屉状态 ──
  const [versionDrawerKb, setVersionDrawerKb] = useState<KnowledgeBaseItem | null>(null);
  const [conflictModalOpen, setConflictModalOpen] = useState(false);
  const [pendingConflictData, setPendingConflictData] = useState<any | null>(null);
  const [currentImportFile, setCurrentImportFile] = useState<File | null>(null);
  const [isImporting, setIsImporting] = useState(false);
  const [exportingKbId, setExportingKbId] = useState<string | null>(null);

  // ── v1.5.9 收口补丁：分层索引与后台口语化增强状态 ──
  const [kbStatusMap, setKbStatusMap] = useState<Record<string, {
    hierarchy?: { needs_upgrade: boolean; total_chunks: number; parent_chunks: number; hype_questions: number };
    enhancement?: { total_parents: number; hype_completed: number; rules_extracted: number; status: string };
  }>>({});
  const [upgradingKbId, setUpgradingKbId] = useState<string | null>(null);
  const [exportedModalInfo, setExportedModalInfo] = useState<{
    kbName: string;
    filePath: string;
    filename: string;
    downloadUrl?: string;
  } | null>(null);
  const [isRevealing, setIsRevealing] = useState(false);
  const [isPathCopied, setIsPathCopied] = useState(false);

  // ── 口语化提炼结果弹窗状态 ──
  const [oralModalKb, setOralModalKb] = useState<KnowledgeBaseItem | null>(null);
  const [oralModalOpen, setOralModalOpen] = useState(false);
  const [cleaningGarbledKbId, setCleaningGarbledKbId] = useState<string | null>(null);
  const [rebuildingHypeKbId, setRebuildingHypeKbId] = useState<string | null>(null);
  const [renamingKbId, setRenamingKbId] = useState<string | null>(null);
  const [renamingValue, setRenamingValue] = useState<string>('');
  const [oralResultsData, setOralResultsData] = useState<{
    kb_id: string;
    kb_name: string;
    version: string;
    total_parents: number;
    matched_parents: number;
    total_questions: number;
    rules_extracted: number;
    status: string;
    hype_completed: number;
    items: Array<{
      parent_chunk_id: string;
      title: string;
      section_path: string;
      remote_uri: string;
      snippet: string;
      full_content: string;
      oral_questions: string[];
      oral_question_items?: Array<{
        id: string;
        content: string;
      }>;
      rules: Array<{
        rule_name: string;
        rule_type: string;
        condition: string;
        action: string;
      }>;
    }>;
    page: number;
    page_size: number;
  } | null>(null);
  const [oralLoading, setOralLoading] = useState(false);
  const [oralPage, setOralPage] = useState(1);
  const [oralKeyword, setOralKeyword] = useState('');
  const [expandedParentIds, setExpandedParentIds] = useState<Record<string, boolean>>({});
  const [addingOralParentId, setAddingOralParentId] = useState<string | null>(null);
  const [addingOralText, setAddingOralText] = useState('');
  const [editingOralId, setEditingOralId] = useState<string | null>(null);
  const [editingOralText, setEditingOralText] = useState('');
  const [oralMutating, setOralMutating] = useState(false);

  const fetchOralResults = useCallback(async (kb: KnowledgeBaseItem, page = 1, keyword = '') => {
    setOralLoading(true);
    try {
      const ver = kb.active_version || 'v1.0.0';
      const q = keyword.trim() ? `&keyword=${encodeURIComponent(keyword.trim())}` : '';
      const res = await apiFetch(`/api/v1/kb/bases/${kb.kb_id}/oralized-results?version=${encodeURIComponent(ver)}&page=${page}&page_size=10${q}`);
      if (res.ok) {
        const json = await res.json();
        setOralResultsData(json);
        setOralPage(page);
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`加载口语化结果失败: ${err.detail || res.statusText}`);
      }
    } catch (e: any) {
      console.error('加载口语化结果异常:', e);
    } finally {
      setOralLoading(false);
    }
  }, []);

  const handleOpenOralResults = (kb: KnowledgeBaseItem) => {
    setOralModalKb(kb);
    setOralKeyword('');
    setOralPage(1);
    setExpandedParentIds({});
    setAddingOralParentId(null);
    setAddingOralText('');
    setEditingOralId(null);
    setEditingOralText('');
    setOralModalOpen(true);
    fetchOralResults(kb, 1, '');
  };

  const handleCreateOralQuestion = async (parentChunkId: string) => {
    if (!oralModalKb || !addingOralText.trim() || oralMutating) return;
    setOralMutating(true);
    try {
      const ver = oralModalKb.active_version || 'v1.0.0';
      const res = await apiFetch(`/api/v1/kb/bases/${oralModalKb.kb_id}/oralized-results`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parent_chunk_id: parentChunkId,
          content: addingOralText.trim(),
          version: ver,
        }),
      });
      if (res.ok) {
        setAddingOralParentId(null);
        setAddingOralText('');
        await fetchOralResults(oralModalKb, oralPage, oralKeyword);
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`新增口语问法失败: ${err.detail || res.statusText}`);
      }
    } catch (e: any) {
      alert(`新增口语问法异常: ${e?.message || e}`);
    } finally {
      setOralMutating(false);
    }
  };

  const handleUpdateOralQuestion = async (questionId: string) => {
    if (!oralModalKb || !editingOralText.trim() || oralMutating) return;
    setOralMutating(true);
    try {
      const ver = oralModalKb.active_version || 'v1.0.0';
      const res = await apiFetch(`/api/v1/kb/bases/${oralModalKb.kb_id}/oralized-results/${encodeURIComponent(questionId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: editingOralText.trim(),
          version: ver,
        }),
      });
      if (res.ok) {
        setEditingOralId(null);
        setEditingOralText('');
        await fetchOralResults(oralModalKb, oralPage, oralKeyword);
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`修改口语问法失败: ${err.detail || res.statusText}`);
      }
    } catch (e: any) {
      alert(`修改口语问法异常: ${e?.message || e}`);
    } finally {
      setOralMutating(false);
    }
  };

  const handleDeleteOralQuestion = async (questionId: string) => {
    if (!oralModalKb || oralMutating) return;
    setOralMutating(true);
    try {
      const ver = oralModalKb.active_version || 'v1.0.0';
      const res = await apiFetch(
        `/api/v1/kb/bases/${oralModalKb.kb_id}/oralized-results/${encodeURIComponent(questionId)}?version=${encodeURIComponent(ver)}`,
        { method: 'DELETE' }
      );
      if (res.ok) {
        await fetchOralResults(oralModalKb, oralPage, oralKeyword);
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`删除口语问法失败: ${err.detail || res.statusText}`);
      }
    } catch (e: any) {
      alert(`删除口语问法异常: ${e?.message || e}`);
    } finally {
      setOralMutating(false);
    }
  };

  const handleRevealLocalFile = async (filePath: string) => {
    setIsRevealing(true);
    try {
      const res = await apiFetch('/api/v1/kb/reveal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: filePath }),
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => '');
        throw new Error(detail || '打开系统文件管理器失败');
      }
    } catch (err: any) {
      alert(`定位文件失败: ${err.message || err}`);
    } finally {
      setIsRevealing(false);
    }
  };

  const handleExportBundle = async (kb: KnowledgeBaseItem) => {
    setExportingKbId(kb.kb_id);
    try {
      const response = await apiFetch(`/api/v1/kb/${kb.kb_id}/export?version=${encodeURIComponent(kb.active_version || 'v1.0.0')}`);
      if (!response.ok) {
        const errorDetail = await response.text().catch(() => '');
        throw new Error(`导出失败: ${response.statusText}${errorDetail ? ` (${errorDetail})` : ''}`);
      }
      const blob = await response.blob();
      const safeName = kb.name.replace(/[\\/*?:"<>|]/g, '_');
      const defaultFilename = `${safeName}_${kb.active_version || 'v1.0.0'}.ahkb`;

      let encodedPath = response.headers.get('X-Local-File-Path') || '';
      let localPath = '';
      try {
        localPath = encodedPath ? decodeURIComponent(encodedPath) : '';
      } catch {
        localPath = encodedPath;
      }

      // 创建持久的 Blob URL，供自动下载与弹窗手动重复下载
      const blobUrl = window.URL.createObjectURL(blob);

      // 1. 优先尝试现代浏览器原生 showSaveFilePicker API (支持用户自由选择任意盘符与保存目录)
      let savedViaPicker = false;
      if (typeof (window as any).showSaveFilePicker === 'function') {
        try {
          const fileHandle = await (window as any).showSaveFilePicker({
            suggestedName: defaultFilename,
            types: [{
              description: 'AgentHub 知识库资产包 (*.ahkb)',
              accept: { 'application/zip': ['.ahkb'] },
            }],
          });
          const writable = await fileHandle.createWritable();
          await writable.write(blob);
          await writable.close();
          savedViaPicker = true;
        } catch (pickerErr: any) {
          if (pickerErr.name === 'AbortError') {
            // 用户在文件选择器中点击了“取消”
            window.URL.revokeObjectURL(blobUrl);
            return;
          }
          // 其他环境错误（如权限或异步激活锁过期）则平稳降级至 DOM 下载
        }
      }

      // 2. 若未通过文件选择器保存，执行标准 DOM a 标签下载 (触发浏览器默认 Downloads 保存)
      if (!savedViaPicker) {
        try {
          const a = document.createElement('a');
          a.style.display = 'none';
          a.href = blobUrl;
          a.download = defaultFilename;
          document.body.appendChild(a);
          a.click();
          setTimeout(() => {
            try {
              a.remove();
            } catch {}
          }, 30000);
        } catch (domErr) {
          console.warn('[LLMWiki] DOM 下载触发异常:', domErr);
        }
      }

      // 3. 始终弹出清晰的落盘与下载交互卡片，提供完整路径、定位文件夹与重新下载入口
      setExportedModalInfo({
        kbName: kb.name,
        filePath: localPath || `${defaultFilename}（已触发下载至本地下载目录）`,
        filename: defaultFilename,
        downloadUrl: blobUrl,
      });
    } catch (err: any) {
      alert(`导出知识包失败: ${err.message || err}`);
    } finally {
      setExportingKbId(null);
    }
  };

  const handleImportFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setCurrentImportFile(file);
    await doImportBundle(file, 'ask');
    e.target.value = '';
  };

  const doImportBundle = async (file: File, strategy: string) => {
    setIsImporting(true);
    const formData = new FormData();
    formData.append('file', file);
    formData.append('conflict_strategy', strategy);

    try {
      const res = await apiFetch('/api/v1/kb/import', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.detail || data.message || '导入异常');
      }

      const resData = data.data;
      if (resData.status === 'identical_skipped') {
        alert(resData.message || '内容一致，已自动跳过重复导入。');
      } else if (resData.status === 'conflict_confirm_required') {
        setPendingConflictData(resData);
        setConflictModalOpen(true);
      } else if (resData.status === 'backed_up_and_overwritten') {
        alert(`已备份原版本并成功覆盖导入！新版本: ${resData.new_version}`);
        setConflictModalOpen(false);
        loadKBs();
      } else if (resData.status === 'imported_new') {
        alert('知识包已成功导入为新知识库！');
        setConflictModalOpen(false);
        loadKBs();
      }
    } catch (err: any) {
      alert(`导入知识包失败: ${err.message || err}`);
    } finally {
      setIsImporting(false);
    }
  };

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

  const fetchKbExtraStatus = async (kbList: KnowledgeBaseItem[]) => {
    const newMap: Record<string, any> = {};
    await Promise.all(
      kbList.map(async (kb) => {
        try {
          const [hRes, eRes] = await Promise.all([
            apiFetch(`/api/v1/kb/${kb.kb_id}/hierarchy-status?version=${encodeURIComponent(kb.active_version || 'v1.0.0')}`),
            apiFetch(`/api/v1/kb/${kb.kb_id}/enhancement-status?version=${encodeURIComponent(kb.active_version || 'v1.0.0')}`),
          ]);
          const hData = hRes.ok ? (await hRes.json()).data : null;
          const eData = eRes.ok ? (await eRes.json()).data : null;
          newMap[kb.kb_id] = { hierarchy: hData, enhancement: eData };
        } catch {
          // ignore
        }
      })
    );
    setKbStatusMap((prev) => ({ ...prev, ...newMap }));
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
        fetchKbExtraStatus(list);
      }
    } catch (e) {
      console.error('加载知识库资产失败:', e);
    } finally {
      setLoadingKbs(false);
    }
  };

  // 监听是否有知识库处于口语增强处理中，每 3 秒自动轮询进度直到完成
  useEffect(() => {
    if (kbsList.length === 0) return;
    const hasActiveEnhancement = kbsList.some((kb) => {
      const ext = kbStatusMap[kb.kb_id];
      if (!ext?.enhancement) return false;
      const { status, total_parents, hype_completed } = ext.enhancement;
      return status === 'running' || (total_parents > 0 && hype_completed < total_parents);
    });
    if (!hasActiveEnhancement) return;

    const timer = setInterval(() => {
      fetchKbExtraStatus(kbsList);
    }, 3000);
    return () => clearInterval(timer);
  }, [kbsList, kbStatusMap]);

  const handleUpgradeHierarchy = async (kb: KnowledgeBaseItem) => {
    if (!confirm(`确定要将知识库【${kb.name}】一键重构为父子分层结构吗？\n升级前系统将自动生成 auto_backup 快照备份，可随时无损回滚。`)) {
      return;
    }
    setUpgradingKbId(kb.kb_id);
    try {
      const res = await apiFetch(`/api/v1/kb/${kb.kb_id}/upgrade-to-parent-child?version=${encodeURIComponent(kb.active_version || 'v1.0.0')}`, {
        method: 'POST',
      });
      if (res.ok) {
        alert(`知识库【${kb.name}】已成功重塑为父子分层结构，口语化问答正于后台异步提炼中！`);
        await loadKBs();
      } else {
        const err = await res.json();
        alert(`升级失败: ${err.detail || '未知错误'}`);
      }
    } catch (e: any) {
      alert(`升级失败: ${e.message}`);
    } finally {
      setUpgradingKbId(null);
    }
  };

  const handleUpdateDomain = async (kbId: string, domainType: string) => {
    try {
      const resp = await apiFetch(`/api/v1/kb/bases/${kbId}/domain`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain_type: domainType }),
      });
      if (resp.ok) {
        await loadKBs();
      } else {
        const err = await resp.json();
        alert('更新领域模板失败: ' + (err.detail || '未知错误'));
      }
    } catch (e: any) {
      alert('更新领域模板失败: ' + e.message);
    }
  };

  const handleCleanupGarbled = async (kb: KnowledgeBaseItem) => {
    const confirmed = window.confirm(
      `确定要对知识库【${kb.name}】进行乱码切片清理吗？\n系统将自动检测并移除二进制乱码、损坏解析切片及孤立无意义碎片，并同步重建全文检索索引。`
    );
    if (!confirmed) return;
    setCleaningGarbledKbId(kb.kb_id);
    try {
      const res = await apiFetch(
        `/api/v1/kb/${kb.kb_id}/cleanup-garbled?version=${encodeURIComponent(kb.active_version || 'v1.0.0')}`,
        { method: 'POST' }
      );
      if (res.ok) {
        const data = await res.json();
        const cleaned = data.cleaned_count ?? ((data.removed_parents || 0) + (data.removed_children || 0) + (data.removed_hype || 0));
        alert(data.message || `成功清理 ${cleaned} 个乱码切片！`);
        await loadKBs();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`清理乱码失败: ${err.detail || res.statusText}`);
      }
    } catch (err: any) {
      alert(`清理异常: ${err.message || err}`);
    } finally {
      setCleaningGarbledKbId(null);
    }
  };

  const handleRebuildHype = async (kb: KnowledgeBaseItem) => {
    const confirmed = window.confirm(
      `确定要清空知识库【${kb.name}】的已有口语切片并重新提炼吗？\n系统将彻底清空历史旧口语问答，换装 6~16 字微信大白话生成模型在后台重新索引。`
    );
    if (!confirmed) return;
    setRebuildingHypeKbId(kb.kb_id);
    try {
      const res = await apiFetch(
        `/api/v1/kb/${kb.kb_id}/rebuild-hype?version=${encodeURIComponent(kb.active_version || 'v1.0.0')}`,
        { method: 'POST' }
      );
      if (res.ok) {
        const data = await res.json();
        alert(data.message || '已清空旧口语索引，新口语化提炼已在后台启动！');
        await loadKBs();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`重构口语失败: ${err.detail || res.statusText}`);
      }
    } catch (err: any) {
      alert(`操作异常: ${err.message || err}`);
    } finally {
      setRebuildingHypeKbId(null);
    }
  };

  const handleRenameKb = async (kbId: string, newName: string) => {
    const trimmed = newName.trim();
    if (!trimmed) {
      setRenamingKbId(null);
      return;
    }
    // 立即乐观更新本地列表显示，彻底防止网络延迟期间名称跳回
    setKbsList((prev) =>
      prev.map((item) => (item.kb_id === kbId ? { ...item, name: trimmed } : item))
    );
    setRenamingKbId(null);
    try {
      const res = await apiFetch(`/api/v1/kb/${kbId}/rename`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: trimmed }),
      });
      if (res.ok) {
        await loadKBs();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`重命名失败: ${err.detail || res.statusText}`);
        await loadKBs();
      }
    } catch (err: any) {
      alert(`重命名异常: ${err.message || err}`);
      await loadKBs();
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

  // 拉取选定知识库的所有历史与草稿版本列表
  const loadKbVersions = async (kbId: string) => {
    if (!kbId) {
      setKbVersionsList([]);
      return;
    }
    setLoadingVersions(true);
    try {
      const res = await apiFetch(`/api/v1/kb/bases/${kbId}/versions`);
      if (res.ok) {
        const data = await res.json();
        const vers = Array.isArray(data?.data) ? data.data : [];
        setKbVersionsList(vers);
        const targetKb = kbsList.find((k) => k.kb_id === kbId);
        setSelectedVersion((prev) => {
          if (prev && vers.some((v: any) => v.version === prev)) return prev;
          return targetKb?.active_version || vers[0]?.version || 'v1.0.0';
        });
      }
    } catch (e) {
      console.error('拉取知识库版本列表失败:', e);
    } finally {
      setLoadingVersions(false);
    }
  };

  useEffect(() => {
    if (selectedKbId) {
      loadKbVersions(selectedKbId);
    }
  }, [selectedKbId]);

  // 将测试版本发布为正式生效版本 (对外专家正式采纳)
  const handlePublishVersion = async (kbId: string, versionToPublish: string) => {
    if (!confirm(`确定将版本【${versionToPublish}】发布为正式生效版本吗？\n发布后，所有绑定该知识库的智能体专家将立即以此版本的规程事实对外提供问答与审查。`)) {
      return;
    }
    setIsPublishingVersion(true);
    try {
      const resp = await apiFetch(`/api/v1/kb/bases/${kbId}/rollback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_version: versionToPublish }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.success) {
        throw new Error(data.detail || data.message || '发布版本失败');
      }
      alert(`版本【${versionToPublish}】已成功发布为正式生效版本！`);
      await loadKBs();
      await loadKbVersions(kbId);
    } catch (err: any) {
      alert(`发布失败: ${err.message || err}`);
    } finally {
      setIsPublishingVersion(false);
    }
  };

  // 命中率实时评测台 (支持按特定测试版本进行切片验货与自然语言专家问答模拟)
  const handleTestPlayground = async () => {
    if (!playgroundQuery.trim()) return;
    if (!selectedKbId && kbsList.length === 0) {
      alert('请先挂载并训练至少一个知识库');
      return;
    }
    const kbId = selectedKbId || (kbsList[0]?.kb_id ?? '');
    setPlaygroundTesting(true);
    setPlaygroundHits(null);
    setPlaygroundSimulatedAnswer(null);
    setPlaygroundGroundingStatus(null);
    try {
      const resp = await apiFetch(`/api/v1/kb/bases/${kbId}/playground`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: playgroundQuery.trim(),
          top_k: 5,
          version: selectedVersion || undefined,
          simulate_chat: true,
        }),
      });
      const data = await resp.json();
      setPlaygroundHits(data.chunks || []);
      setPlaygroundSimulatedAnswer(data.simulated_answer || null);
      setPlaygroundGroundingStatus(data.grounding_status || null);
    } catch (e: any) {
      console.error('Playground 测试异常:', e);
    } finally {
      setPlaygroundTesting(false);
    }
  };

  // 切换知识库版本的发布状态（只有发布的版本才能被外部专家调用，未发布仅限测试检测）
  const handleTogglePublish = async (kb: KnowledgeBaseItem, targetVersion?: string, publish = true) => {
    const ver = targetVersion || kb.active_version || 'v1.0.0';
    const actionText = publish ? '发布为此知识库的正式对外版本' : '取消发布（设为测试维护状态）';
    if (!confirm(`确定要将知识库【${kb.name}】(${ver}) ${actionText}吗？\n${publish ? '发布后绑定的智能体专家即可正式检索调用。' : '取消发布后外部专家将暂停调用，仅限在测试环节检测。'}`)) {
      return;
    }
    setTogglingPublishKbId(kb.kb_id);
    try {
      const resp = await apiFetch(`/api/v1/kb/bases/${kb.kb_id}/publish-toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: ver,
          publish: publish,
        }),
      });
      if (resp.ok) {
        const data = await resp.json();
        alert(data.message || '操作成功');
        await loadKBs();
        if (selectedKbId === kb.kb_id) {
          await loadKbVersions(kb.kb_id);
        }
      } else {
        const err = await resp.json().catch(() => ({}));
        alert(`操作失败: ${err.detail || '网络错误'}`);
      }
    } catch (e: any) {
      alert(`操作异常: ${e.message}`);
    } finally {
      setTogglingPublishKbId(null);
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
    if (!confirm(`确定要彻底物理销毁独立知识库【${kbName}】吗？\n该操作将删除磁盘所有 index.db 物理文件、清空其全部切片与图谱三元组，并重置增量同步水位线，不可恢复。`)) return;
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
    if (!confirm(`确定要清空并物理销毁当前系统所有独立知识库资产吗？（共 ${kbsList.length} 个）\n该操作将彻底物理删除磁盘所有版本的 index.db 文件、清空所有规程切片与图谱三元组，并重置增量同步水位线，不可恢复！`)) return;
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
        setTotalAllRowsCount(json.all_rows_count || json.total_count || 0);
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

      {/* 第二层：平级线性工具风 Tab 导航栏 */}
      <div className="px-6 border-b border-border bg-surface-100/40 flex items-center justify-between shrink-0">
        <div className="flex items-center space-x-6 text-xs sm:text-sm">
          <button
            type="button"
            onClick={() => setActiveSection('wiki')}
            className={`py-3 border-b-2 font-medium transition-all flex items-center gap-2 ${
              activeSection === 'wiki'
                ? 'border-emerald-500 text-foreground font-semibold'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <span>百科工作台 (Wiki)</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveSection('train')}
            className={`py-3 border-b-2 font-medium transition-all flex items-center gap-2 ${
              activeSection === 'train'
                ? 'border-emerald-500 text-foreground font-semibold'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <span>资产清单</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-surface-200 text-muted-foreground font-mono">
              {kbsList.length}
            </span>
          </button>

          <button
            type="button"
            onClick={() => setActiveSection('mount')}
            className={`py-3 border-b-2 font-medium transition-all flex items-center gap-2 ${
              activeSection === 'mount'
                ? 'border-emerald-500 text-foreground font-semibold'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <span>数据源挂载</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-surface-200 text-muted-foreground font-mono">
              {mountsList.length}
            </span>
          </button>

          <button
            type="button"
            onClick={() => setActiveSection('test')}
            className={`py-3 border-b-2 font-medium transition-all flex items-center gap-2 ${
              activeSection === 'test'
                ? 'border-emerald-500 text-foreground font-semibold'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <span>召回测试</span>
          </button>
        </div>

        <div className="flex items-center gap-2 py-2">
          <label
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface-100 hover:bg-surface-200 text-foreground text-xs font-medium cursor-pointer transition-all ${
              isImporting ? 'opacity-50 pointer-events-none' : ''
            }`}
            title="导入免重新训练的 .ahkb 离线归档知识包"
          >
            <Upload className="w-3.5 h-3.5 text-muted-foreground" />
            <span>{isImporting ? '导入中...' : '导入 .ahkb'}</span>
            <input
              type="file"
              accept=".ahkb,.zip"
              onChange={handleImportFileSelect}
              className="hidden"
            />
          </label>
        </div>
      </div>

      {/* 第三层：功能操作区 */}
      {activeSection === 'wiki' ? (
        <div className="flex-1 min-h-0 overflow-hidden">
          <WikiWorkspaceView />
        </div>
      ) : (
      <div className="flex-1 overflow-y-auto p-6 space-y-6 select-text">

        {/* ========================================================= */}
        {/* 第一部分：1、知识库测试 (默认)                            */}
        {/* ========================================================= */}
        {activeSection === 'test' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 animate-fade-in">
            {/* 左侧：知识库选择、版本切换与发布控制台 (4 列) */}
            <div className="lg:col-span-4 space-y-5">
              <div className="rounded-2xl border border-border bg-surface-card p-5 shadow-sm space-y-4">
                <div className="flex items-center justify-between border-b border-border pb-3">
                  <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                    <FlaskConical className="w-4 h-4 text-sky-500" />
                    知识库与版本选择
                  </h3>
                  <span className="text-[10px] text-muted-foreground font-mono">
                    {kbsList.length} 个知识库
                  </span>
                </div>

                <div>
                  <label className="text-xs font-medium text-muted-foreground block mb-1">
                    选择待测试的知识库
                  </label>
                  <select
                    value={selectedKbId}
                    onChange={(e) => setSelectedKbId(e.target.value)}
                    className="w-full rounded-xl border border-border bg-surface-100 px-3 py-2 text-xs outline-none focus:border-sky-500"
                  >
                    {kbsList.map((kb) => (
                      <option key={kb.kb_id} value={kb.kb_id}>
                        {kb.name} {kb.is_published ? `(已发布: ${kb.active_version})` : `(未发布·当前: ${kb.active_version})`}
                      </option>
                    ))}
                  </select>
                </div>

                {/* 版本选择器 */}
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-medium text-muted-foreground">
                      选择测试版本
                    </label>
                    {loadingVersions && (
                      <span className="text-[10px] text-muted-foreground animate-pulse">
                        加载版本中...
                      </span>
                    )}
                  </div>
                  <select
                    value={selectedVersion}
                    onChange={(e) => setSelectedVersion(e.target.value)}
                    className="w-full rounded-xl border border-border bg-surface-100 px-3 py-2 text-xs outline-none focus:border-sky-500 font-mono"
                  >
                    {kbVersionsList.map((v) => {
                      const curKb = kbsList.find((k) => k.kb_id === selectedKbId);
                      const isAct = v.version === curKb?.active_version;
                      const isPublishedRelease = Boolean(curKb?.is_published && isAct);
                      return (
                        <option key={v.version} value={v.version}>
                          {v.version} {isPublishedRelease ? '★ [当前正式发布版]' : isAct ? '· [测试维护版 (未发布)]' : '· [历史测试版]'} (切片: {v.chunk_count})
                        </option>
                      );
                    })}
                  </select>
                </div>

                {/* 当前版本的发布状态卡片 */}
                {(() => {
                  const curKb = kbsList.find((k) => k.kb_id === selectedKbId);
                  const isTargetVersionActive = Boolean(
                    curKb && (selectedVersion === curKb.active_version || (!selectedVersion && curKb.active_version))
                  );
                  // 只有当知识库已开启发布，且当前选中的是 active_version，才算作对外生效的正式发布版本！
                  const isCurrentRelease = Boolean(curKb?.is_published && isTargetVersionActive);
                  const matchedVerInfo = kbVersionsList.find((v) => v.version === selectedVersion);
                  const chunkCount = matchedVerInfo?.chunk_count ?? curKb?.chunk_count ?? 0;
                  const tripleCount = matchedVerInfo?.triple_count ?? curKb?.triple_count ?? 0;
                  const diskMb = ((matchedVerInfo?.disk_size_bytes ?? curKb?.disk_size_bytes ?? 0) / (1024 * 1024)).toFixed(2);

                  return (
                    <div className="p-3.5 rounded-xl border border-border bg-surface-100/60 space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-muted-foreground">版本生效状态:</span>
                        {isCurrentRelease ? (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 flex items-center gap-1 font-semibold">
                            <CheckCircle2 className="w-3 h-3 text-emerald-500" />
                            正式发布版本 (对外生效中)
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/10 text-amber-700 dark:text-amber-300 border border-amber-500/25 flex items-center gap-1 font-semibold">
                            <AlertCircle className="w-3 h-3 text-amber-500" />
                            测试维护版本 (未发布 · 外部专家不可调用)
                          </span>
                        )}
                      </div>

                      {/* 若当前版本未发布，提供正式发布按钮 */}
                      {!isCurrentRelease && curKb && (
                        <button
                          type="button"
                          disabled={togglingPublishKbId === curKb.kb_id || !selectedVersion}
                          onClick={() => handleTogglePublish(curKb, selectedVersion, true)}
                          className="w-full flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm transition disabled:opacity-50"
                          title="在此测试台充分测试验收无误后，点击将此版本发布对外正式生效，绑定的智能体专家方可检索调用"
                        >
                          <ShieldCheck className="w-4 h-4" />
                          <span>{togglingPublishKbId === curKb.kb_id ? '发布中...' : '测试验收通过，发布此版本为正式版 ★'}</span>
                        </button>
                      )}

                      {/* 若当前版本已发布，提供取消发布按钮 */}
                      {isCurrentRelease && curKb && (
                        <button
                          type="button"
                          disabled={togglingPublishKbId === curKb.kb_id || !selectedVersion}
                          onClick={() => handleTogglePublish(curKb, selectedVersion, false)}
                          className="w-full flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl bg-amber-500/10 hover:bg-amber-500/20 text-amber-700 dark:text-amber-300 border border-amber-500/30 text-xs font-medium shadow-sm transition disabled:opacity-50"
                          title="将当前版本转为未发布测试维护状态，外部专家将暂停调用"
                        >
                          <AlertCircle className="w-4 h-4 text-amber-500" />
                          <span>{togglingPublishKbId === curKb.kb_id ? '处理中...' : '暂停外部专家调用，转为测试维护中'}</span>
                        </button>
                      )}

                      <div className="grid grid-cols-3 gap-2 pt-2 border-t border-border/60 text-center">
                        <div className="p-1 rounded bg-surface-200/50">
                          <span className="text-[10px] text-muted-foreground block">规程切片</span>
                          <span className="text-xs font-bold font-mono text-foreground">{chunkCount}</span>
                        </div>
                        <div className="p-1 rounded bg-surface-200/50">
                          <span className="text-[10px] text-muted-foreground block">图谱三元组</span>
                          <span className="text-xs font-bold font-mono text-foreground">{tripleCount}</span>
                        </div>
                        <div className="p-1 rounded bg-surface-200/50">
                          <span className="text-[10px] text-muted-foreground block">库体积</span>
                          <span className="text-xs font-bold font-mono text-foreground">{diskMb} MB</span>
                        </div>
                      </div>
                    </div>
                  );
                })()}

                <div className="text-[11px] text-muted-foreground bg-primary/5 p-3 rounded-xl border border-primary/10 leading-relaxed flex items-start gap-2">
                  <HelpCircle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
                  <div>
                    <strong>版本准出硬性规范</strong>：未经测试验收的版本严禁发布对外调用。新训练或更新的知识库默认处于【测试维护中（未发布）】状态；请先在右侧验货台中充分发起自然语言提问，验证切片召回质量。验收无误后点击上方【测试验收通过，发布此版本为正式版】，绑定的智能体专家方可采纳使用。
                  </div>
                </div>
              </div>
            </div>

            {/* 右侧：实测验货与提问评测台 (8 列) */}
            <div className="lg:col-span-8 space-y-5">
              <div className="rounded-2xl border border-border bg-surface-card p-5 shadow-sm space-y-4">
                <div className="flex items-center justify-between border-b border-border pb-3">
                  <div>
                    <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                      <Search className="w-4 h-4 text-sky-500" />
                      检索命中率即测验货台 (Playground)
                    </h3>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      向指定版本的规程切片发起语义向量与 BM25 混合检索，测试置信召回精度
                    </p>
                  </div>
                  {(() => {
                    const curKb = kbsList.find((k) => k.kb_id === selectedKbId);
                    const isTargetVersionActive = Boolean(
                      curKb && (selectedVersion === curKb.active_version || (!selectedVersion && curKb.active_version))
                    );
                    const isCurrentRelease = Boolean(curKb?.is_published && isTargetVersionActive);
                    return isCurrentRelease ? (
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 flex items-center gap-1 font-semibold">
                        <CheckCircle2 className="w-3 h-3 text-emerald-500" />
                        已发布正式版: {selectedVersion || '默认'}
                      </span>
                    ) : (
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/10 text-amber-700 dark:text-amber-300 border border-amber-500/25 flex items-center gap-1 font-semibold">
                        <AlertCircle className="w-3 h-3 text-amber-500" />
                        测试维护版 (未发布): {selectedVersion || '默认'}
                      </span>
                    );
                  })()}
                </div>

                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="输入测试问题，如: 比赛报名费或押金退还标准、2024年开票记录..."
                    value={playgroundQuery}
                    onChange={(e) => setPlaygroundQuery(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleTestPlayground()}
                    className="flex-1 rounded-xl border border-border bg-surface-100 px-3.5 py-2.5 text-xs outline-none focus:border-sky-500 focus:bg-surface-card transition-all"
                  />
                  <button
                    type="button"
                    onClick={handleTestPlayground}
                    disabled={playgroundTesting}
                    className="px-5 py-2.5 rounded-xl bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold shrink-0 disabled:opacity-50 transition-all shadow-sm flex items-center gap-1.5"
                  >
                    <Search className={`w-3.5 h-3.5 ${playgroundTesting ? 'animate-spin' : ''}`} />
                    <span>{playgroundTesting ? '检索中...' : '测试命中'}</span>
                  </button>
                </div>

                {/* 自然语言模拟检测回答与 Grounding 评估区 */}
                {playgroundTesting && (
                  <div className="p-4 rounded-xl border border-sky-500/30 bg-sky-500/5 text-xs text-sky-600 dark:text-sky-400 flex items-center gap-2.5 animate-pulse">
                    <Sparkles className="w-4 h-4 animate-spin text-sky-500" />
                    <span>正在检索当前版本切片，并调用大模型模拟专家生成自然语言回答...</span>
                  </div>
                )}

                {playgroundSimulatedAnswer && !playgroundTesting && (
                  <div className="rounded-xl border border-primary/25 bg-surface-100/70 p-4 space-y-2.5 shadow-sm">
                    <div className="flex items-center justify-between border-b border-border/60 pb-2">
                      <div className="flex items-center gap-1.5">
                        <MessageSquare className="w-4 h-4 text-primary" />
                        <span className="text-xs font-semibold text-foreground">
                          智能体专家模拟答复 (自然语言检测)
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        {playgroundGroundingStatus && (
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-medium border ${
                              playgroundGroundingStatus.is_grounded
                                ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20'
                                : 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20'
                            }`}
                          >
                            {playgroundGroundingStatus.is_grounded
                              ? `事实依据充分 (召回 ${playgroundGroundingStatus.used_chunks_count ?? playgroundHits?.length ?? 0} 条切片)`
                              : '缺少明确依据 (知识库未充分覆盖)'}
                          </span>
                        )}
                        <button
                          type="button"
                          onClick={() => handleCopyChunkText('simulated_answer', playgroundSimulatedAnswer)}
                          className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-surface-200 transition"
                          title="复制模拟回答"
                        >
                          {copiedChunkId === 'simulated_answer' ? (
                            <Check className="w-3.5 h-3.5 text-emerald-500" />
                          ) : (
                            <Copy className="w-3.5 h-3.5" />
                          )}
                        </button>
                      </div>
                    </div>
                    <div className="text-xs text-foreground/90 leading-relaxed whitespace-pre-wrap bg-surface-card p-3 rounded-lg border border-border/50 select-text font-sans">
                      {playgroundSimulatedAnswer}
                    </div>
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground pt-1">
                      <span>
                        🎯 提示：检测结果满意后，请发布此版本；只有发布的知识库版本才能被外部专家正常调用。
                      </span>
                    </div>
                  </div>
                )}

                {playgroundHits && (
                  <div className="space-y-3 pt-3 border-t border-border">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-semibold text-foreground flex items-center gap-1.5">
                        <FileText className="w-3.5 h-3.5 text-sky-500" />
                        召回证据切片 ({playgroundHits.length})
                      </span>
                      <span className="text-[10px] text-muted-foreground">
                        {isDevDebug ? '按综合置信度得分降序' : '按业务相关性排序'}
                      </span>
                    </div>

                    {playgroundHits.length === 0 ? (
                      <div className="py-12 text-center text-xs text-muted-foreground border border-dashed border-border rounded-xl">
                        未检索到与查询匹配的相关规程切片，建议调整测试提问或检查该版本是否切片就绪
                      </div>
                    ) : (
                      <div className="space-y-3 max-h-[520px] overflow-y-auto pr-1">
                        {playgroundHits.map((h, i) => {
                          const isExpanded = expandedHitIdx === i;
                          return (
                            <div
                              key={i}
                              className="p-3.5 rounded-xl bg-surface-100/60 border border-border text-xs space-y-2 hover:border-sky-500/40 transition-all"
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="font-semibold text-foreground truncate max-w-md" title={h.title}>
                                  {isDevDebug ? `[${i + 1}] ${h.title}` : `来源：${h.title}${h.section_path ? ` · ${h.section_path}` : ''}`}
                                </span>
                                <div className="flex items-center gap-1.5 shrink-0">
                                  {isDevDebug && (
                                    <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/10 text-emerald-500 font-semibold border border-emerald-500/20 tabular-nums">
                                      得分: {Number(h.final_score || h.score || 0).toFixed(3)} · {h.chunk_role || 'child'}
                                    </span>
                                  )}
                                  <button
                                    type="button"
                                    onClick={() => handleCopyChunkText(`hit_${i}`, h.content)}
                                    className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-surface-200 transition"
                                    title="复制此切片原文"
                                  >
                                    {copiedChunkId === `hit_${i}` ? (
                                      <Check className="w-3.5 h-3.5 text-emerald-500" />
                                    ) : (
                                      <Copy className="w-3.5 h-3.5" />
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
                                  {isExpanded ? '收起原文 ↑' : '展开查看切片全文 ↓'}
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

        {/* ========================================================= */}
        {/* 第三部分：3、知识库云盘挂载                               */}
        {/* ========================================================= */}
        {activeSection === 'mount' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 animate-fade-in">
            {/* 顶部横幅提示：后期对接 Ontology 与外部抓取 */}
            <div className="lg:col-span-12 p-3.5 rounded-xl border border-sky-500/20 bg-sky-500/5 text-xs text-sky-700 dark:text-sky-300 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <HardDrive className="w-4 h-4 text-sky-500 shrink-0" />
                <span>
                  <strong>CloudVFS 存储接入层</strong>：当前已支持企业微信微盘、WebDAV 私有云与 S3/OSS 对象存储挂载；后期将全面对接 <strong>Ontology 本体知识图谱体系</strong> 及 <strong>外部自动化抓取数据源</strong>。
                </span>
              </div>
            </div>

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
                      className={`flex flex-col items-center justify-center p-3 rounded-xl border text-xs transition-all ${providerTab === 'wecom'
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
                      className={`flex flex-col items-center justify-center p-3 rounded-xl border text-xs transition-all ${providerTab === 'webdav'
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
                      className={`flex flex-col items-center justify-center p-3 rounded-xl border text-xs transition-all ${providerTab === 'oss'
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
                        className={`w-full rounded-lg border bg-surface-card px-2.5 py-1.5 text-xs outline-none transition-colors ${webdavForm.base_url.includes('/dev')
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
                    className={`flex items-start gap-2.5 rounded-xl p-3 text-xs leading-relaxed transition-all ${testResult.success
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
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${m.authority_tier === 1
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
        {/* 第二部分：2、知识库的训练与维护                           */}
        {/* ========================================================= */}
        {activeSection === 'train' && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 animate-fade-in">
            {/* 全宽：训练调度与知识库资产维护中心 (12 列) */}
            <div className="lg:col-span-12 space-y-6">
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
              <div className="space-y-3">
                {kbsList.length === 0 ? (
                  <div className="py-12 text-center text-xs text-muted-foreground border border-dashed border-border rounded-xl space-y-2 bg-surface-card">
                    <Database className="w-8 h-8 text-muted-foreground/40 mx-auto" />
                    <div>暂无已训练的知识库资产</div>
                    <p className="text-[11px] text-muted-foreground/80 max-w-[240px] mx-auto">
                      请在上方点击“启动增量训练”生成第一个知识库，或直接点击上方“导入 .ahkb”
                    </p>
                  </div>
                ) : (
                  kbsList.map((kb) => {
                    const isSelected = selectedKbId === kb.kb_id;
                    const extra = kbStatusMap[kb.kb_id];
                    return (
                      <div
                        key={kb.kb_id}
                        onClick={() => setSelectedKbId(kb.kb_id)}
                        className={`rounded-xl border p-4 text-xs transition-all cursor-pointer space-y-3 relative ${
                          isSelected
                            ? 'border-emerald-500/50 bg-surface-card shadow-sm ring-1 ring-emerald-500/20'
                            : 'border-border bg-surface-card hover:border-border/90'
                        }`}
                      >
                        {/* 顶栏：信息与操作收口 */}
                        <div className="flex items-center justify-between gap-3 flex-wrap">
                          {/* 左侧：名称 + 版本号 + 状态点 + 规则模板 */}
                          <div className="flex items-center gap-3 flex-wrap min-w-0">
                            {renamingKbId === kb.kb_id ? (
                              <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                                <input
                                  type="text"
                                  autoFocus
                                  value={renamingValue}
                                  onChange={(e) => setRenamingValue(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleRenameKb(kb.kb_id, renamingValue);
                                    if (e.key === 'Escape') setRenamingKbId(null);
                                  }}
                                  className="font-bold text-sm px-2 py-0.5 rounded border border-blue-500 bg-surface-100 text-foreground outline-none min-w-[200px]"
                                />
                                <button
                                  type="button"
                                  onClick={() => handleRenameKb(kb.kb_id, renamingValue)}
                                  className="p-1 rounded text-emerald-500 hover:bg-emerald-500/15 transition-colors border border-emerald-500/30"
                                  title="保存新名称 (Enter)"
                                >
                                  <Check className="w-3.5 h-3.5" />
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setRenamingKbId(null)}
                                  className="p-1 rounded text-muted-foreground hover:bg-surface-200 transition-colors border border-border"
                                  title="取消 (Esc)"
                                >
                                  <X className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            ) : (
                              <div className="flex items-center gap-1.5 group/title">
                                <span className="font-bold text-sm text-foreground tracking-tight select-text" title={kb.name}>
                                  {kb.name}
                                </span>
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setRenamingKbId(kb.kb_id);
                                    setRenamingValue(kb.name);
                                  }}
                                  className="p-0.5 rounded text-muted-foreground/40 hover:text-foreground transition-colors opacity-0 group-hover/title:opacity-100"
                                  title="修改知识库名称"
                                >
                                  <Edit2 className="w-3 h-3" />
                                </button>
                              </div>
                            )}

                            {/* v1.0.0 纯等宽浅灰小字，点击呼出版本抽屉 */}
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setVersionDrawerKb(kb);
                              }}
                              className="font-mono text-xs text-muted-foreground hover:text-foreground hover:underline transition-colors flex items-center gap-0.5 cursor-pointer"
                              title="点击打开版本管理与回滚抽屉"
                            >
                              <span>{kb.active_version || 'v1.0.0'}</span>
                            </button>

                            {/* 状态圆点 + 轻量文本 */}
                            {kb.is_published ? (
                              <span className="flex items-center gap-1.5 text-xs text-emerald-500 font-medium" title="已发布正式版：绑定的智能体专家即可检索调用">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                                <span>已发布</span>
                              </span>
                            ) : (
                              <span className="flex items-center gap-1.5 text-xs text-amber-500 font-medium" title="未发布：仅限在召回测试中检测，外部专家暂停调用">
                                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
                                <span>未发布</span>
                              </span>
                            )}

                            {/* 只读锁定态轻量提示 */}
                            {(kb.ownership_type === 'subscribed_ro' || kb.ownership_type === 'frozen_snapshot') && (
                              <span className="text-[11px] text-muted-foreground bg-surface-200 px-1.5 py-0.5 rounded">
                                🔒 {kb.ownership_type === 'subscribed_ro' ? '订阅只读' : '离线快照'}
                              </span>
                            )}

                            {/* 领域规程模板下拉框 */}
                            <select
                              value={kb.domain_type || 'general'}
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) => {
                                e.stopPropagation();
                                handleUpdateDomain(kb.kb_id, e.target.value);
                              }}
                              className="px-2 py-0.5 rounded text-[11px] bg-surface-100 border border-border/80 text-muted-foreground hover:text-foreground hover:border-border outline-none cursor-pointer transition-colors"
                              title="切换此知识库关联的业务领域规则模板 (击剑/马术/橄榄球/通用规章)"
                            >
                              {DOMAIN_OPTIONS.map((opt) => (
                                <option key={opt.id} value={opt.id}>
                                  {opt.label}
                                </option>
                              ))}
                            </select>
                          </div>

                          {/* 右侧：动作严格分级收口（2 显性 + 1 折叠菜单） */}
                          <div className="flex items-center gap-2 shrink-0 ml-auto relative">
                            {/* 1. 高频查看：管理内容 */}
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleViewKbChunks(kb, 1, '');
                              }}
                              className="px-2.5 py-1 text-xs rounded-lg border border-border bg-surface-100 hover:bg-surface-200 text-foreground font-medium transition-colors shadow-xs"
                              title="管理规程切片明细与分块"
                            >
                              管理内容
                            </button>

                            {/* 2. 主操作：发布 / 取消发布（全卡片唯一主高亮色） */}
                            {kb.is_published ? (
                              <button
                                type="button"
                                disabled={togglingPublishKbId === kb.kb_id}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleTogglePublish(kb, kb.active_version, false);
                                }}
                                className="px-2.5 py-1 text-xs rounded-lg border border-border bg-surface-100 hover:bg-surface-200 text-muted-foreground hover:text-amber-500 font-medium transition-colors disabled:opacity-50"
                                title="取消发布：转为未发布测试状态"
                              >
                                {togglingPublishKbId === kb.kb_id ? '处理中...' : '取消发布'}
                              </button>
                            ) : (
                              <button
                                type="button"
                                disabled={togglingPublishKbId === kb.kb_id}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleTogglePublish(kb, kb.active_version, true);
                                }}
                                className="px-3 py-1 text-xs rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium shadow-xs transition-colors disabled:opacity-50"
                                title="正式发布上线对外开放"
                              >
                                {togglingPublishKbId === kb.kb_id ? '发布中...' : '发布'}
                              </button>
                            )}

                            {/* 3. 低频维护收口：[ ⋯ ] 折叠菜单 */}
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setActionMenuKbId(actionMenuKbId === kb.kb_id ? null : kb.kb_id);
                              }}
                              className={`p-1.5 rounded-lg border text-muted-foreground hover:text-foreground transition-colors ${
                                actionMenuKbId === kb.kb_id
                                  ? 'border-border bg-surface-200 text-foreground'
                                  : 'border-border/80 bg-surface-100 hover:bg-surface-200'
                              }`}
                              title="更多维护操作"
                            >
                              <MoreHorizontal className="w-3.5 h-3.5" />
                            </button>

                            {/* 下拉折叠菜单 Popup */}
                            {actionMenuKbId === kb.kb_id && (
                              <div
                                onClick={(e) => e.stopPropagation()}
                                className="absolute right-0 top-full mt-1.5 w-48 rounded-xl border border-border bg-surface-100 p-1 shadow-xl z-30 text-xs animate-fade-in"
                              >
                                <button
                                  type="button"
                                  disabled={exportingKbId === kb.kb_id}
                                  onClick={() => {
                                    setActionMenuKbId(null);
                                    handleExportBundle(kb);
                                  }}
                                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-foreground hover:bg-surface-200 transition-colors text-left"
                                >
                                  <Download className="w-3.5 h-3.5 text-muted-foreground" />
                                  <span>{exportingKbId === kb.kb_id ? '导出中...' : '导出知识包 (.ahkb)'}</span>
                                </button>

                                <button
                                  type="button"
                                  disabled={extractingGraphKbId === kb.kb_id}
                                  onClick={() => {
                                    setActionMenuKbId(null);
                                    handleExtractGraph(kb.kb_id);
                                  }}
                                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-foreground hover:bg-surface-200 transition-colors text-left"
                                >
                                  <Zap className="w-3.5 h-3.5 text-muted-foreground" />
                                  <span>{extractingGraphKbId === kb.kb_id ? '提取中...' : '提取图谱三元组'}</span>
                                </button>

                                <button
                                  type="button"
                                  disabled={rebuildingHypeKbId === kb.kb_id}
                                  onClick={() => {
                                    setActionMenuKbId(null);
                                    handleRebuildHype(kb);
                                  }}
                                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-foreground hover:bg-surface-200 transition-colors text-left"
                                >
                                  <Sparkles className="w-3.5 h-3.5 text-muted-foreground" />
                                  <span>{rebuildingHypeKbId === kb.kb_id ? '提炼中...' : '重构短口语'}</span>
                                </button>

                                <button
                                  type="button"
                                  disabled={cleaningGarbledKbId === kb.kb_id}
                                  onClick={() => {
                                    setActionMenuKbId(null);
                                    handleCleanupGarbled(kb);
                                  }}
                                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-foreground hover:bg-surface-200 transition-colors text-left"
                                >
                                  <ShieldAlert className="w-3.5 h-3.5 text-muted-foreground" />
                                  <span>{cleaningGarbledKbId === kb.kb_id ? '清理中...' : '清理乱码切片'}</span>
                                </button>

                                <button
                                  type="button"
                                  onClick={() => {
                                    setActionMenuKbId(null);
                                    handlePruneKb(kb.kb_id);
                                  }}
                                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-foreground hover:bg-surface-200 transition-colors text-left"
                                >
                                  <Scissors className="w-3.5 h-3.5 text-muted-foreground" />
                                  <span>数据库瘦身 (VACUUM)</span>
                                </button>

                                <button
                                  type="button"
                                  onClick={() => {
                                    setActionMenuKbId(null);
                                    setVersionDrawerKb(kb);
                                  }}
                                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-foreground hover:bg-surface-200 transition-colors text-left"
                                >
                                  <History className="w-3.5 h-3.5 text-muted-foreground" />
                                  <span>版本历史与回滚</span>
                                </button>

                                {extra?.hierarchy?.needs_upgrade && (
                                  <button
                                    type="button"
                                    disabled={upgradingKbId === kb.kb_id}
                                    onClick={() => {
                                      setActionMenuKbId(null);
                                      handleUpgradeHierarchy(kb);
                                    }}
                                    className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-amber-500 hover:bg-surface-200 transition-colors text-left"
                                  >
                                    <Sparkles className="w-3.5 h-3.5 text-amber-500" />
                                    <span>重塑分层索引</span>
                                  </button>
                                )}

                                <div className="my-1 border-t border-border" />

                                <button
                                  type="button"
                                  onClick={() => {
                                    setActionMenuKbId(null);
                                    handleDeleteKb(kb.kb_id, kb.name);
                                  }}
                                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-rose-500 hover:bg-rose-500/10 transition-colors text-left"
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                  <span>彻底销毁知识库</span>
                                </button>
                              </div>
                            )}
                          </div>
                        </div>

                        {/* 底栏：细分割线下的 4 列等宽统计条 */}
                        <div className="pt-2.5 border-t border-border/40 grid grid-cols-4 gap-2 text-xs">
                          {/* 切片数 */}
                          <div
                            onClick={(e) => {
                              e.stopPropagation();
                              handleViewKbChunks(kb, 1, '');
                            }}
                            className="cursor-pointer group/stat"
                            title="点击查看所有规程切片明细"
                          >
                            <span className="text-muted-foreground text-[11px] block">正文切片</span>
                            <span className="font-bold text-foreground font-mono group-hover/stat:text-sky-400 group-hover/stat:underline tabular-nums transition-colors">
                              {kb.chunk_count.toLocaleString()}
                            </span>
                          </div>

                          {/* 图谱三元组 */}
                          <div
                            onClick={(e) => {
                              e.stopPropagation();
                              handleViewKbTriples(kb, 1, '');
                            }}
                            className="cursor-pointer group/stat"
                            title="点击查看知识图谱实体与三元组资产"
                          >
                            <span className="text-muted-foreground text-[11px] block">图谱三元组</span>
                            <span className="font-bold text-foreground font-mono group-hover/stat:text-amber-400 group-hover/stat:underline tabular-nums transition-colors">
                              {kb.triple_count.toLocaleString()}
                            </span>
                          </div>

                          {/* 口语增强进度 */}
                          <div
                            onClick={(e) => {
                              e.stopPropagation();
                              handleOpenOralResults(kb);
                            }}
                            className="cursor-pointer group/stat"
                            title="点击查看口语化问答与规则映射结果"
                          >
                            <span className="text-muted-foreground text-[11px] block">口语增强</span>
                            {extra?.enhancement && extra.enhancement.total_parents > 0 ? (
                              (extra.enhancement.hype_completed >= extra.enhancement.total_parents || extra.enhancement.status === 'completed') ? (
                                <span className="text-emerald-500 font-semibold group-hover/stat:underline transition-colors">
                                  就绪
                                </span>
                              ) : (
                                <span className="text-purple-400 font-mono group-hover/stat:underline tabular-nums transition-colors">
                                  {extra.enhancement.hype_completed}/{extra.enhancement.total_parents}
                                </span>
                              )
                            ) : (
                              <span className="text-muted-foreground/60 group-hover/stat:underline transition-colors">
                                未提炼
                              </span>
                            )}
                          </div>

                          {/* 数据库体积 */}
                          <div
                            onClick={(e) => {
                              e.stopPropagation();
                              handlePruneKb(kb.kb_id);
                            }}
                            className="cursor-pointer group/stat"
                            title="点击执行数据库 VACUUM 瘦身清理"
                          >
                            <span className="text-muted-foreground text-[11px] block">体积</span>
                            <span className="font-bold text-foreground font-mono group-hover/stat:text-purple-400 group-hover/stat:underline tabular-nums transition-colors">
                              {(kb.disk_size_bytes / (1024 * 1024)).toFixed(2)} MB
                            </span>
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}

                {/* 底部弱化批量清空 */}
                {kbsList.length > 0 && (
                  <div className="flex justify-end pt-3">
                    <button
                      type="button"
                      onClick={handlePurgeAllKbs}
                      className="text-xs text-muted-foreground/40 hover:text-rose-500 transition-colors flex items-center gap-1.5"
                      title="清空并彻底物理销毁所有独立知识库资产"
                    >
                      <Trash2 className="w-3 h-3" />
                      <span>批量清空全部知识库</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
      )}

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
                    <span>正文切片：<strong className="text-foreground">{totalChunksCount}</strong> 个{totalAllRowsCount > totalChunksCount ? `（含口语/摘要衍生共 ${totalAllRowsCount} 条）` : ''}</span>
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

                    <div className="flex items-center gap-2 shrink-0">
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20 font-mono font-medium" title="知识边被切片印证的频次与置信权重">
                        印证权重 {Number(t.confidence || 1).toFixed(0)}x
                      </span>

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
                    <span>正文切片：<strong className="text-foreground">{totalChunksCount}</strong> 个{totalAllRowsCount > totalChunksCount ? `（含口语/摘要衍生共 ${totalAllRowsCount} 条）` : ''}</span>
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

                    <div className="flex items-center gap-2 shrink-0">
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20 font-mono font-medium" title="知识边被切片印证的频次与置信权重">
                        印证权重 {Number(t.confidence || 1).toFixed(0)}x
                      </span>

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

      {/* v1.5.9 知识库导出成功与路径交互弹窗 */}
      {exportedModalInfo && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200">
          <div className="bg-surface-100 border border-border rounded-xl shadow-2xl max-w-lg w-full overflow-hidden">
            <div className="px-5 py-4 border-b border-border flex items-center justify-between bg-surface-200/50">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-lg bg-emerald-500/10 text-emerald-500 flex items-center justify-center font-bold">
                  ✓
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-foreground">知识库资产包已生成</h3>
                  <p className="text-[11px] text-muted-foreground">可直接在系统文件管理器中查看或复制路径</p>
                </div>
              </div>
              <button
                onClick={() => {
                  setExportedModalInfo(null);
                  setIsPathCopied(false);
                }}
                className="text-muted-foreground hover:text-foreground text-sm p-1 rounded-md hover:bg-surface-300 transition"
              >
                ✕
              </button>
            </div>

            <div className="p-5 space-y-4">
              <div>
                <label className="text-xs text-muted-foreground block mb-1">知识库名称</label>
                <div className="text-sm font-medium text-foreground">{exportedModalInfo.kbName}</div>
              </div>

              <div>
                <label className="text-xs text-muted-foreground block mb-1">本地落盘路径</label>
                <div className="p-2.5 rounded-lg bg-surface-200/80 border border-border text-xs text-foreground font-mono break-all select-all">
                  {exportedModalInfo.filePath}
                </div>
              </div>

              <div className="text-[11px] text-muted-foreground bg-primary/5 p-3 rounded-lg border border-primary/10">
                💡 资产包包含完整的自包含规程分块、向量与口语化 QA 索引，支持随时在其他工作区或 AgentHub 节点中一键无损导入恢复。
              </div>
            </div>

            <div className="px-5 py-3.5 bg-surface-200/30 border-t border-border flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(exportedModalInfo.filePath);
                    setIsPathCopied(true);
                    setTimeout(() => setIsPathCopied(false), 2000);
                  }}
                  className="px-3 py-1.5 rounded-lg border border-border text-xs text-foreground hover:bg-surface-200 transition flex items-center gap-1.5"
                >
                  {isPathCopied ? '已复制路径 ✓' : '复制完整路径'}
                </button>

                {exportedModalInfo.downloadUrl && (
                  <a
                    href={exportedModalInfo.downloadUrl}
                    download={exportedModalInfo.filename}
                    className="px-3 py-1.5 rounded-lg border border-blue-500/30 text-xs text-blue-600 dark:text-blue-400 bg-blue-500/10 hover:bg-blue-500/20 transition flex items-center gap-1.5 font-medium"
                    title="在浏览器中重新触发下载并保存"
                  >
                    重新下载 ⬇️
                  </a>
                )}
              </div>

              <div className="flex items-center gap-2">
                {exportedModalInfo.filePath && !exportedModalInfo.filePath.includes('已触发下载') && (
                  <button
                    onClick={() => handleRevealLocalFile(exportedModalInfo.filePath)}
                    disabled={isRevealing}
                    className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium transition shadow-sm flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {isRevealing ? '正在定位...' : '在文件夹中打开 📂'}
                  </button>
                )}
                <button
                  onClick={() => {
                    if (exportedModalInfo.downloadUrl) {
                      try {
                        window.URL.revokeObjectURL(exportedModalInfo.downloadUrl);
                      } catch {}
                    }
                    setExportedModalInfo(null);
                    setIsPathCopied(false);
                  }}
                  className="px-3 py-1.5 rounded-lg bg-surface-300 hover:bg-surface-400 text-xs text-foreground transition font-medium"
                >
                  完成
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* v1.5.9 版本冲突仲裁弹窗 */}
      <KBImportConflictModal
        isOpen={conflictModalOpen}
        onClose={() => setConflictModalOpen(false)}
        kbName={pendingConflictData?.manifest?.kb_name || '知识库'}
        localVersion={pendingConflictData?.local_version || 'v1.0.0'}
        localMd5={pendingConflictData?.local_md5 || ''}
        incomingVersion={pendingConflictData?.incoming_version || 'v1.0.0'}
        incomingMd5={pendingConflictData?.incoming_md5 || ''}
        diff={pendingConflictData?.diff || null}
        isProcessing={isImporting}
        onConfirmOverwrite={() => {
          if (currentImportFile) {
            doImportBundle(currentImportFile, 'backup_and_overwrite');
          }
        }}
        onImportAsNew={() => {
          if (currentImportFile) {
            doImportBundle(currentImportFile, 'import_as_new');
          }
        }}
      />

      {/* v1.5.9 版本演进与回滚抽屉 */}
      {versionDrawerKb && (
        <KBVersionDrawer
          isOpen={!!versionDrawerKb}
          onClose={() => setVersionDrawerKb(null)}
          kbId={versionDrawerKb.kb_id}
          kbName={versionDrawerKb.name}
          currentVersion={versionDrawerKb.active_version}
          onRollbackSuccess={() => {
            loadKBs();
          }}
        />
      )}

      {/* 口语化问答与刚性规则提炼结果弹窗 */}
      {oralModalOpen && oralModalKb && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
          <div className="bg-surface-100 border border-border rounded-2xl shadow-2xl max-w-4xl w-full flex flex-col max-h-[88vh] overflow-hidden">
            {/* Header */}
            <div className="px-6 py-4 border-b border-border flex items-center justify-between bg-surface-200/50">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
                  <Sparkles className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-base font-semibold text-foreground">
                      【{oralModalKb.name}】口语化提炼与规程映射
                    </h3>
                    <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-surface-300 text-muted-foreground border border-border">
                      {oralModalKb.active_version || 'v1.0.0'}
                    </span>
                    {oralResultsData?.status === 'completed' ? (
                      <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" /> 口语化就绪
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-purple-500/15 text-purple-600 dark:text-purple-400 border border-purple-500/30 animate-pulse flex items-center gap-1">
                        <RefreshCw className="w-3 h-3 animate-spin" /> 后台提炼中 ({oralResultsData?.hype_completed ?? 0}/{oralResultsData?.total_parents ?? 0})
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    将生硬公文段落自动重塑为员工大白话口语提问，直接击穿专业词汇与日常习惯的表达断层
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setOralModalOpen(false)}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-300 transition-colors"
                title="关闭"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 指标栏 */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-5 bg-surface-100 border-b border-border text-xs">
              <div className="p-3 rounded-xl bg-surface-200/60 border border-border/80">
                <div className="text-muted-foreground flex items-center gap-1.5 mb-1">
                  <MessageSquare className="w-3.5 h-3.5 text-emerald-500" />
                  <span>口语问答总数</span>
                </div>
                <div className="text-xl font-bold text-foreground">
                  {oralResultsData?.total_questions ?? 0}
                  <span className="text-xs font-normal text-muted-foreground ml-1">条问法</span>
                </div>
              </div>

              <div className="p-3 rounded-xl bg-surface-200/60 border border-border/80">
                <div className="text-muted-foreground flex items-center gap-1.5 mb-1">
                  <Layers className="w-3.5 h-3.5 text-blue-500" />
                  <span>覆盖规程段落</span>
                </div>
                <div className="text-xl font-bold text-foreground">
                  {oralResultsData?.total_parents ?? 0}
                  <span className="text-xs font-normal text-muted-foreground ml-1">个父切片</span>
                </div>
              </div>

              <div className="p-3 rounded-xl bg-surface-200/60 border border-border/80">
                <div className="text-muted-foreground flex items-center gap-1.5 mb-1">
                  <ShieldCheck className="w-3.5 h-3.5 text-purple-500" />
                  <span>抽取刚性规则</span>
                </div>
                <div className="text-xl font-bold text-foreground">
                  {oralResultsData?.rules_extracted ?? 0}
                  <span className="text-xs font-normal text-muted-foreground ml-1">条</span>
                </div>
              </div>

              <div className="p-3 rounded-xl bg-surface-200/60 border border-border/80">
                <div className="text-muted-foreground flex items-center gap-1.5 mb-1">
                  <Activity className="w-3.5 h-3.5 text-amber-500" />
                  <span>提炼完成率</span>
                </div>
                <div className="text-xl font-bold text-foreground">
                  {oralResultsData && oralResultsData.total_parents > 0
                    ? `${Math.min(100, Math.round(((oralResultsData.hype_completed || 0) / oralResultsData.total_parents) * 100))}%`
                    : '100%'}
                </div>
              </div>
            </div>

            {/* 搜索与筛选工具栏 */}
            <div className="px-5 py-3 border-b border-border bg-surface-200/40 flex items-center justify-between gap-3">
              <div className="relative flex-1 max-w-md">
                <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="text"
                  placeholder="搜索口语问题、公文标题或段落正文..."
                  value={oralKeyword}
                  onChange={(e) => setOralKeyword(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      fetchOralResults(oralModalKb, 1, oralKeyword);
                    }
                  }}
                  className="w-full pl-9 pr-3 py-1.5 text-xs rounded-xl bg-surface-100 border border-border text-foreground placeholder-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => fetchOralResults(oralModalKb, 1, oralKeyword)}
                  className="px-3 py-1.5 rounded-xl bg-primary hover:bg-primary/90 text-primary-foreground text-xs font-medium transition-colors shadow-sm"
                >
                  搜索
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setOralKeyword('');
                    fetchOralResults(oralModalKb, 1, '');
                  }}
                  className="px-2.5 py-1.5 rounded-xl border border-border hover:bg-surface-300 text-muted-foreground text-xs transition-colors"
                  title="刷新"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${oralLoading ? 'animate-spin' : ''}`} />
                </button>
              </div>
            </div>

            {/* 内容列表区 */}
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              {oralLoading ? (
                <div className="py-16 text-center text-muted-foreground text-xs flex flex-col items-center justify-center gap-2">
                  <RefreshCw className="w-6 h-6 animate-spin text-primary" />
                  <span>正在调取口语化问答与规则映射...</span>
                </div>
              ) : !oralResultsData || oralResultsData.items.length === 0 ? (
                <div className="py-16 text-center text-muted-foreground text-xs space-y-2">
                  <HelpCircle className="w-8 h-8 mx-auto text-muted-foreground/50" />
                  <div className="font-medium text-foreground">暂无符合条件的口语化问答</div>
                  <p className="text-[11px] text-muted-foreground">
                    若知识库刚完成训练，后台正基于大模型异步分析规程并提炼日常问题，稍后刷新即可展现。
                  </p>
                </div>
              ) : (
                oralResultsData.items.map((item, idx) => {
                  const isExpanded = Boolean(expandedParentIds[item.parent_chunk_id]);
                  return (
                    <div
                      key={item.parent_chunk_id || idx}
                      className="p-4 rounded-xl bg-surface-200/50 border border-border hover:border-border/80 transition-all space-y-3"
                    >
                      {/* 父段落标题与路径 */}
                      <div className="flex items-start justify-between gap-2">
                        <div className="space-y-0.5">
                          <div className="text-sm font-semibold text-foreground flex items-center gap-2">
                            <FileText className="w-4 h-4 text-emerald-500 shrink-0" />
                            <span>{item.title}</span>
                          </div>
                          {item.section_path && (
                            <div className="text-[11px] text-muted-foreground font-mono">
                              {item.section_path}
                            </div>
                          )}
                        </div>
                        <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-surface-300 text-muted-foreground border border-border shrink-0">
                          {item.parent_chunk_id}
                        </span>
                      </div>

                      {/* 口语化提炼问法（支持手动增删改） */}
                      <div className="p-3 rounded-lg bg-surface-100 border border-emerald-500/25 space-y-2">
                        <div className="flex items-center justify-between gap-2">
                          <div className="text-xs font-semibold text-emerald-700 dark:text-emerald-300 flex items-center gap-1.5">
                            <MessageSquare className="w-3.5 h-3.5 text-emerald-500" />
                            <span>已提炼的大白话日常提问（{(item.oral_question_items ?? item.oral_questions).length}）</span>
                          </div>
                          <button
                            type="button"
                            onClick={() => {
                              setAddingOralParentId(item.parent_chunk_id);
                              setAddingOralText('');
                            }}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-300 border border-emerald-500/25 transition-colors"
                          >
                            <Plus className="w-3 h-3" />
                            <span>新增口语</span>
                          </button>
                        </div>

                        {addingOralParentId === item.parent_chunk_id && (
                          <div className="flex items-center gap-1.5 pt-1">
                            <input
                              type="text"
                              value={addingOralText}
                              onChange={(e) => setAddingOralText(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') handleCreateOralQuestion(item.parent_chunk_id);
                                if (e.key === 'Escape') {
                                  setAddingOralParentId(null);
                                  setAddingOralText('');
                                }
                              }}
                              placeholder="输入大白话问题，例如：1.00米赛怎么算名次？"
                              className="flex-1 px-2.5 py-1 rounded-md bg-surface-200 border border-emerald-500/40 text-xs text-foreground focus:outline-none focus:border-emerald-500"
                              autoFocus
                            />
                            <button
                              type="button"
                              disabled={oralMutating || !addingOralText.trim()}
                              onClick={() => handleCreateOralQuestion(item.parent_chunk_id)}
                              className="px-2 py-1 rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs font-medium inline-flex items-center gap-1"
                            >
                              <Check className="w-3 h-3" />
                              <span>保存</span>
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setAddingOralParentId(null);
                                setAddingOralText('');
                              }}
                              className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-surface-200"
                              title="取消"
                            >
                              <X className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        )}

                        {(item.oral_question_items && item.oral_question_items.length > 0) ? (
                          <div className="flex flex-wrap gap-1.5">
                            {item.oral_question_items.map((qItem) => (
                              editingOralId === qItem.id ? (
                                <div
                                  key={qItem.id}
                                  className="flex items-center gap-1 px-2 py-1 rounded-lg bg-surface-200 border border-emerald-500/50 w-full sm:w-auto min-w-[260px]"
                                >
                                  <input
                                    type="text"
                                    value={editingOralText}
                                    onChange={(e) => setEditingOralText(e.target.value)}
                                    onKeyDown={(e) => {
                                      if (e.key === 'Enter') handleUpdateOralQuestion(qItem.id);
                                      if (e.key === 'Escape') {
                                        setEditingOralId(null);
                                        setEditingOralText('');
                                      }
                                    }}
                                    className="flex-1 bg-transparent text-xs text-foreground focus:outline-none"
                                    autoFocus
                                  />
                                  <button
                                    type="button"
                                    disabled={oralMutating || !editingOralText.trim()}
                                    onClick={() => handleUpdateOralQuestion(qItem.id)}
                                    className="p-0.5 text-emerald-500 hover:text-emerald-400"
                                    title="保存修改"
                                  >
                                    <Check className="w-3.5 h-3.5" />
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setEditingOralId(null);
                                      setEditingOralText('');
                                    }}
                                    className="p-0.5 text-muted-foreground hover:text-foreground"
                                    title="取消"
                                  >
                                    <X className="w-3.5 h-3.5" />
                                  </button>
                                </div>
                              ) : (
                                <div
                                  key={qItem.id}
                                  className="group px-2.5 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-xs text-emerald-800 dark:text-emerald-200 font-medium flex items-center gap-1.5"
                                >
                                  <span className="text-emerald-500">💬</span>
                                  <span>{qItem.content}</span>
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setEditingOralId(qItem.id);
                                      setEditingOralText(qItem.content);
                                    }}
                                    className="opacity-60 hover:opacity-100 text-emerald-600 dark:text-emerald-300 ml-0.5"
                                    title="修改该口语问法"
                                  >
                                    <Edit2 className="w-3 h-3" />
                                  </button>
                                  <button
                                    type="button"
                                    disabled={oralMutating}
                                    onClick={() => handleDeleteOralQuestion(qItem.id)}
                                    className="opacity-60 hover:opacity-100 text-rose-500"
                                    title="删除该口语问法"
                                  >
                                    <Trash2 className="w-3 h-3" />
                                  </button>
                                </div>
                              )
                            ))}
                          </div>
                        ) : item.oral_questions.length > 0 ? (
                          <div className="flex flex-wrap gap-1.5">
                            {item.oral_questions.map((q, qIdx) => (
                              <div
                                key={qIdx}
                                className="px-2.5 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-xs text-emerald-800 dark:text-emerald-200 font-medium flex items-center gap-1.5"
                              >
                                <span className="text-emerald-500">💬</span>
                                <span>{q}</span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-xs text-muted-foreground italic">
                            后台正在为该段落提炼大白话问法中...
                          </div>
                        )}
                      </div>

                      {/* 刚性规则（若有） */}
                      {item.rules && item.rules.length > 0 && (
                        <div className="p-3 rounded-lg bg-purple-500/5 border border-purple-500/20 space-y-2">
                          <div className="text-xs font-semibold text-purple-700 dark:text-purple-300 flex items-center gap-1.5">
                            <ShieldCheck className="w-3.5 h-3.5 text-purple-500" />
                            <span>抽取规程刚性判定规则（{item.rules.length}）</span>
                          </div>
                          <div className="space-y-1.5">
                            {item.rules.map((r, rIdx) => (
                              <div
                                key={rIdx}
                                className="p-2 rounded bg-surface-100 border border-purple-500/20 text-xs space-y-1"
                              >
                                <div className="font-semibold text-foreground flex items-center gap-2">
                                  <span>{r.rule_name}</span>
                                  <span className="px-1.5 py-0.5 rounded text-[10px] bg-purple-500/10 text-purple-600 dark:text-purple-400">
                                    {r.rule_type}
                                  </span>
                                </div>
                                {r.condition && (
                                  <div className="text-[11px] text-muted-foreground">
                                    <strong>触发条件:</strong> {r.condition}
                                  </div>
                                )}
                                {r.action && (
                                  <div className="text-[11px] text-foreground font-medium">
                                    <strong>裁定标准:</strong> {r.action}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* 规程原始段落（可折叠） */}
                      <div className="text-xs bg-surface-100/60 p-2.5 rounded-lg border border-border">
                        <div className="flex items-center justify-between text-muted-foreground mb-1 text-[11px]">
                          <span>对应规程原文段落：</span>
                          <button
                            type="button"
                            onClick={() =>
                              setExpandedParentIds((prev) => ({
                                ...prev,
                                [item.parent_chunk_id]: !prev[item.parent_chunk_id],
                              }))
                            }
                            className="text-primary hover:underline flex items-center gap-0.5"
                          >
                            <span>{isExpanded ? '收起段落' : '展开全文'}</span>
                            {isExpanded ? (
                              <ChevronUp className="w-3 h-3" />
                            ) : (
                              <ChevronDown className="w-3 h-3" />
                            )}
                          </button>
                        </div>
                        <p className="text-muted-foreground leading-relaxed whitespace-pre-wrap select-text">
                          {isExpanded ? item.full_content : item.snippet}
                        </p>
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            {/* 分页栏 */}
            <div className="px-6 py-3 border-t border-border bg-surface-200/50 flex items-center justify-between text-xs text-muted-foreground">
              <div>
                共 {oralResultsData?.matched_parents ?? 0} 个段落
                {oralResultsData && oralResultsData.matched_parents > 0 && (
                  <span>
                    ，当前第 {oralPage} / {Math.max(1, Math.ceil((oralResultsData.matched_parents || 1) / (oralResultsData.page_size || 10)))} 页
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={oralPage <= 1 || oralLoading}
                  onClick={() => fetchOralResults(oralModalKb, oralPage - 1, oralKeyword)}
                  className="px-3 py-1.5 rounded-lg border border-border hover:bg-surface-300 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1 transition-colors"
                >
                  <ChevronLeft className="w-3.5 h-3.5" /> 上一页
                </button>
                <button
                  type="button"
                  disabled={
                    !oralResultsData ||
                    oralPage >= Math.max(1, Math.ceil((oralResultsData.matched_parents || 1) / (oralResultsData.page_size || 10))) ||
                    oralLoading
                  }
                  onClick={() => fetchOralResults(oralModalKb, oralPage + 1, oralKeyword)}
                  className="px-3 py-1.5 rounded-lg border border-border hover:bg-surface-300 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1 transition-colors"
                >
                  下一页 <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

