'use client';

import React, { useState, useEffect, useRef } from 'react';
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
  Layers,
  Save,
  RefreshCw,
  Upload,
  CheckCircle2,
  AlertTriangle,
  Eye,
  Clock,
  RotateCcw,
  Archive,
  Lock,
  History,
  ChevronLeft,
  ChevronRight,
  Globe,
  FolderGit2,
  BookOpen,
} from 'lucide-react';
import { EventLedgerItem, LongTermMemoryItem, MemoryFactItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import {
  fetchExtensionSkillDetail,
  fetchExtensionSkills,
  getCachedExtensionSkills,
  invalidateWorkspaceSkills,
  setExtensionSkillsCache,
} from '@/utils/skillCache';
import { PersonaPreset, SkillItem } from './SettingsModal';

export type MemoryHubTab = 'memories' | 'facts' | 'ledger' | 'personality' | 'extensions';

interface MemoryHubModalProps {
  isOpen: boolean;
  onClose: () => void;
  onProvidersChanged?: () => void;
  workspaceId?: string;
  initialTab?: MemoryHubTab;
  onSkillsUpdated?: () => void;
}

interface SkillInfo {
  id: string;
  name: string;
  description: string;
  path?: string;
  has_content?: boolean;
  content?: string;
  is_builtin?: boolean;
  has_knowledge?: boolean;
  knowledge_files?: string[];
}

const CATEGORY_MAP: Record<string, { label: string; colorClass: string }> = {
  rule: {
    label: '工程规范',
    colorClass: 'bg-purple-500/15 text-purple-600 dark:text-purple-400 border-purple-500/30',
  },
  preference: {
    label: '个人偏好',
    colorClass: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30',
  },
  env_info: {
    label: '本机环境',
    colorClass: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30',
  },
};

const FACT_CATEGORY_MAP: Record<string, { label: string; colorClass: string }> = {
  biz_rule: {
    label: '业务规则',
    colorClass: 'bg-teal-500/15 text-teal-600 dark:text-teal-400 border-teal-500/30',
  },
  user_pref: {
    label: '个人偏好',
    colorClass: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30',
  },
  env_config: {
    label: '环境契约',
    colorClass: 'bg-indigo-500/15 text-indigo-600 dark:text-indigo-400 border-indigo-500/30',
  },
};

const FACT_SOURCE_LABELS: Record<string, string> = {
  manual: '手动录入',
  inquiry: '对齐卡确认',
  explicit_cmd: '显式指令',
  verified_env: '环境验证',
  rollback: '版本回滚',
};

const LEDGER_EVENT_TYPE_MAP: Record<string, { label: string; badgeClass: string }> = {
  inquiry_confirmed: {
    label: '对齐卡确认',
    badgeClass: 'bg-teal-500/15 text-teal-600 dark:text-teal-300 border-teal-500/30',
  },
  tool_error_recovered: {
    label: '故障自愈恢复',
    badgeClass: 'bg-amber-500/15 text-amber-600 dark:text-amber-300 border-amber-500/30',
  },
  user_intervention: {
    label: '人工纠偏干预',
    badgeClass: 'bg-rose-500/15 text-rose-600 dark:text-rose-300 border-rose-500/30',
  },
  artifact_produced: {
    label: '产出资产落盘',
    badgeClass: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border-emerald-500/30',
  },
  tool_executed: {
    label: '工具算子执行',
    badgeClass: 'bg-indigo-500/15 text-indigo-600 dark:text-indigo-300 border-indigo-500/30',
  },
  user_input: {
    label: '用户输入指令',
    badgeClass: 'bg-surface-200 text-muted-foreground border-border',
  },
};

function formatIsoTime(iso?: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function MemoryHubModal({
  isOpen,
  onClose,
  onProvidersChanged,
  workspaceId = 'default',
  initialTab = 'memories',
  onSkillsUpdated,
}: MemoryHubModalProps) {
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  const [activeTab, setActiveTab] = useState<MemoryHubTab>(initialTab);
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

  // ──────────── 精确事实视图 (双时间轴 L4) 状态 ────────────
  const [facts, setFacts] = useState<MemoryFactItem[]>([]);
  const [factsLoading, setFactsLoading] = useState(false);
  const [factScopeFilter, setFactScopeFilter] = useState<'merged' | 'workspace' | 'global'>('merged');
  const [factIncludeHistory, setFactIncludeHistory] = useState(false);
  const [factCategoryFilter, setFactCategoryFilter] = useState<
    'all' | 'biz_rule' | 'user_pref' | 'env_config'
  >('all');
  const [factFeedback, setFactFeedback] = useState<{ success: boolean; msg: string } | null>(null);
  const [isAddingFact, setIsAddingFact] = useState(false);
  const [newFactKey, setNewFactKey] = useState('');
  const [newFactValue, setNewFactValue] = useState('');
  const [newFactCategory, setNewFactCategory] = useState<'biz_rule' | 'user_pref' | 'env_config'>(
    'biz_rule'
  );
  const [newFactScope, setNewFactScope] = useState<'workspace' | 'global'>('workspace');
  const [editingFactId, setEditingFactId] = useState<string | null>(null);
  const [editingFactValue, setEditingFactValue] = useState('');
  const [factActionId, setFactActionId] = useState<string | null>(null);

  // ──────────── 追加式事件账本 (Append-Only Ledger) 状态 ────────────
  const [ledgerEvents, setLedgerEvents] = useState<EventLedgerItem[]>([]);
  const [ledgerTotal, setLedgerTotal] = useState(0);
  const [ledgerLoading, setLedgerLoading] = useState(false);
  const [ledgerWorkspaceOnly, setLedgerWorkspaceOnly] = useState(true);
  const [ledgerEventType, setLedgerEventType] = useState('');
  const [ledgerOffset, setLedgerOffset] = useState(0);
  const ledgerPageSize = 30;

  // ──────────── 回复性格与 SKILL 定制状态 ────────────
  const [presets, setPresets] = useState<PersonaPreset[]>([]);
  const [promptSkills, setPromptSkills] = useState<SkillItem[]>([]);
  const [customPromptSkills, setCustomPromptSkills] = useState<SkillItem[]>([]);
  const [selectedPersonaId, setSelectedPersonaId] = useState<string>('architect');
  const [activeSkillIds, setActiveSkillIds] = useState<string[]>(['deep_debugger']);
  const [customPrompt, setCustomPrompt] = useState<string>('');
  const [savingPersonality, setSavingPersonality] = useState(false);
  const [personalityFeedback, setPersonalityFeedback] = useState<{
    success: boolean;
    msg: string;
  } | null>(null);

  // 新增自定义叠加增强技能 (SKILL Prompt) 表单
  const [showAddPromptSkill, setShowAddPromptSkill] = useState(false);
  const [newPromptSkillName, setNewPromptSkillName] = useState('');
  const [newPromptSkillIcon, setNewPromptSkillIcon] = useState('⚡');
  const [newPromptSkillDesc, setNewPromptSkillDesc] = useState('');
  const [newPromptSkillFragment, setNewPromptSkillFragment] = useState('');

  // ──────────── Workspace Skills & MCP 扩展总线状态 ────────────
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [mcpServers, setMcpServers] = useState<Record<string, any>>({});
  const [extFeedback, setExtFeedback] = useState<{ success: boolean; msg: string } | null>(null);
  const [previewSkillId, setPreviewSkillId] = useState<string | null>(null);
  const [loadingPreviewId, setLoadingPreviewId] = useState<string | null>(null);

  // 新建/导入 Workspace Skill 状态
  const [skillFormMode, setSkillFormMode] = useState<'none' | 'create' | 'import'>('none');
  const [newSkillName, setNewSkillName] = useState('');
  const [newSkillDesc, setNewSkillDesc] = useState('');
  const [newSkillContent, setNewSkillContent] = useState('');
  const [importSkillText, setImportSkillText] = useState('');
  const [importSkillFilename, setImportSkillFilename] = useState('SKILL.md');
  const skillFileInputRef = useRef<HTMLInputElement>(null);

  // 新建/导入 MCP Server 状态
  const [mcpFormMode, setMcpFormMode] = useState<'none' | 'create' | 'import'>('none');
  const [newMcpName, setNewMcpName] = useState('');
  const [newMcpCommand, setNewMcpCommand] = useState('npx');
  const [newMcpArgs, setNewMcpArgs] = useState('-y @modelcontextprotocol/server-filesystem .');
  const [newMcpDesc, setNewMcpDesc] = useState('');
  const [newMcpEnvJson, setNewMcpEnvJson] = useState('');
  const [importMcpJson, setImportMcpJson] = useState('');
  const mcpFileInputRef = useRef<HTMLInputElement>(null);

  const fetchMemories = async (isSilent = false) => {
    // 仅在首次无数据时展示加载占位，避免列表刷新卸载 DOM 导致滚动位置回跳
    if (!isSilent && memories.length === 0) {
      setLoading(true);
    }
    try {
      const res = await apiFetch('/api/v1/memories');
      if (res.ok) {
        const data = await res.json();
        setMemories(data.items || []);
      }
    } catch (e) {
      console.warn('获取长期记忆失败:', e);
    } finally {
      if (!isSilent) {
        setLoading(false);
      }
    }
  };

  const fetchFacts = async (silent = false) => {
    if (!silent) setFactsLoading(true);
    try {
      const params = new URLSearchParams();
      if (factScopeFilter === 'global') {
        params.set('workspace_id', 'GLOBAL');
        params.set('include_global', 'false');
      } else if (factScopeFilter === 'workspace') {
        params.set('workspace_id', workspaceId || 'default');
        params.set('include_global', 'false');
      } else {
        params.set('workspace_id', workspaceId || 'default');
        params.set('include_global', 'true');
      }
      params.set('include_history', String(factIncludeHistory));
      if (factCategoryFilter !== 'all') {
        params.set('category', factCategoryFilter);
      }
      const res = await apiFetch(`/api/v1/memories/facts?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setFacts(data.items || []);
      }
    } catch (e) {
      console.warn('获取精确事实视图失败:', e);
    } finally {
      if (!silent) setFactsLoading(false);
    }
  };

  const fetchLedgerEvents = async (silent = false) => {
    if (!silent) setLedgerLoading(true);
    try {
      const params = new URLSearchParams({
        limit: String(ledgerPageSize),
        offset: String(ledgerOffset),
      });
      if (ledgerWorkspaceOnly && workspaceId) {
        params.set('workspace_id', workspaceId);
      }
      if (ledgerEventType) {
        params.set('event_type', ledgerEventType);
      }
      const res = await apiFetch(`/api/v1/memories/ledger?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setLedgerEvents(data.items || []);
        setLedgerTotal(typeof data.total === 'number' ? data.total : 0);
      }
    } catch (e) {
      console.warn('获取事件账本失败:', e);
    } finally {
      if (!silent) setLedgerLoading(false);
    }
  };

  const fetchPersonalitySettings = async () => {
    try {
      const res = await apiFetch('/api/v1/settings/personality');
      if (res.ok) {
        const data = await res.json();
        setPresets(data.presets || []);
        setPromptSkills(data.skills || []);
        if (data.config) {
          setSelectedPersonaId(data.config.selected_persona_id || 'architect');
          setActiveSkillIds(data.config.active_skill_ids || ['deep_debugger']);
          setCustomPrompt(data.config.custom_system_prompt || '');
          setCustomPromptSkills(data.config.custom_skills || []);
        }
      }
    } catch (e) {
      console.warn('拉取性格设置失败:', e);
    }
  };

  const fetchExtensions = async () => {
    const cachedSkills = getCachedExtensionSkills();
    if (cachedSkills) {
      setSkills(cachedSkills);
    }
    try {
      const [extSkills, mRes] = await Promise.all([
        fetchExtensionSkills({ force: true }),
        apiFetch('/api/v1/extensions/mcp'),
      ]);
      setSkills(extSkills);
      if (mRes.ok) {
        const mData = await mRes.json();
        setMcpServers(mData.mcpServers || {});
      }
    } catch (e) {}
  };

  // 独立监听 ESC 关闭事件，避免父组件轮询引起 onClose 引用变化时连带重置用户输入
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCloseRef.current();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  // 弹窗打开时按调用方传入的 initialTab 自动切换目标视图，并拉取初始化数据
  useEffect(() => {
    if (!isOpen) return;
    setActiveTab(initialTab);
    fetchMemories();
    fetchPersonalitySettings();
    fetchExtensions();
    setPersonalityFeedback(null);
    setExtFeedback(null);
    setFactFeedback(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, initialTab]);

  // 当事实视图筛选条件或当前工作区变化时刷新结构化事实列表
  useEffect(() => {
    if (!isOpen) return;
    fetchFacts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, workspaceId, factScopeFilter, factIncludeHistory, factCategoryFilter]);

  // 当切换至事件账本或过滤/分页变化时按需拉取审计流水
  useEffect(() => {
    if (!isOpen || activeTab !== 'ledger') return;
    fetchLedgerEvents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, activeTab, workspaceId, ledgerWorkspaceOnly, ledgerEventType, ledgerOffset]);

  if (!isOpen) return null;

  // ──────────── 精确事实 (Bi-Temporal Facts) 操作 ────────────
  const handleAddFact = async () => {
    if (!newFactKey.trim() || !newFactValue.trim()) {
      setFactFeedback({ success: false, msg: '事实键 (fact_key) 与事实值均不能为空' });
      return;
    }
    setFactActionId('add');
    setFactFeedback(null);
    try {
      const targetWs = newFactScope === 'global' ? 'GLOBAL' : workspaceId || 'default';
      const res = await apiFetch('/api/v1/memories/facts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fact_key: newFactKey.trim(),
          fact_value: newFactValue.trim(),
          workspace_id: targetWs,
          category: newFactCategory,
          source_type: 'manual',
          confidence: 1.0,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setFactFeedback({ success: false, msg: data.detail || '写入结构化事实失败' });
        return;
      }
      const hasRedacted = Array.isArray(data.redactions) && data.redactions.length > 0;
      setNewFactKey('');
      setNewFactValue('');
      setIsAddingFact(false);
      setFactFeedback({
        success: true,
        msg: hasRedacted
          ? '✅ 事实已写入并触发双时间轴更新，其中的敏感凭据已自动脱敏'
          : data.action === 'updated'
          ? '✅ 已触发双时间轴覆盖更新，旧版本已自动归档留痕'
          : '✅ 已新增工作区/全局确定性事实',
      });
      await fetchFacts(true);
    } catch (e: any) {
      setFactFeedback({ success: false, msg: e?.message || '网络异常' });
    } finally {
      setFactActionId(null);
    }
  };

  const handleSaveEditFact = async (item: MemoryFactItem) => {
    if (!editingFactValue.trim()) return;
    setFactActionId(item.fact_id);
    setFactFeedback(null);
    try {
      const res = await apiFetch('/api/v1/memories/facts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fact_key: item.fact_key,
          fact_value: editingFactValue.trim(),
          workspace_id: item.workspace_id,
          category: item.category,
          source_type: 'manual',
          confidence: 1.0,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setFactFeedback({ success: false, msg: data.detail || '更新事实失败' });
        return;
      }
      setEditingFactId(null);
      setFactFeedback({
        success: true,
        msg:
          data.action === 'unchanged'
            ? '事实值未发生实质变化，已保持原版本'
            : '✅ 已激活新版本并自动关闭旧版本有效时间线',
      });
      await fetchFacts(true);
    } catch (e: any) {
      setFactFeedback({ success: false, msg: e?.message || '网络异常' });
    } finally {
      setFactActionId(null);
    }
  };

  const handleRollbackFact = async (factId: string) => {
    setFactActionId(factId);
    setFactFeedback(null);
    try {
      const res = await apiFetch(`/api/v1/memories/facts/${encodeURIComponent(factId)}/rollback`, {
        method: 'POST',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setFactFeedback({ success: false, msg: data.detail || '版本回滚失败' });
        return;
      }
      setFactFeedback({
        success: true,
        msg: '✅ 已将该历史版本重新激活为当前有效契约（完整演进链已保留）',
      });
      await fetchFacts(true);
    } catch (e: any) {
      setFactFeedback({ success: false, msg: e?.message || '网络异常' });
    } finally {
      setFactActionId(null);
    }
  };

  const handleDeactivateFact = async (factId: string) => {
    setFactActionId(factId);
    setFactFeedback(null);
    try {
      const res = await apiFetch(
        `/api/v1/memories/facts/${encodeURIComponent(factId)}/deactivate`,
        { method: 'POST' }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setFactFeedback({ success: false, msg: data.detail || '停用归档失败' });
        return;
      }
      setFactFeedback({
        success: true,
        msg: '已将该条事实软停用归档，保留于双时间轴历史记录中',
      });
      await fetchFacts(true);
    } catch (e: any) {
      setFactFeedback({ success: false, msg: e?.message || '网络异常' });
    } finally {
      setFactActionId(null);
    }
  };

  // ──────────── 长期记忆操作 ────────────
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

  // ──────────── 回复性格与 SKILL 定制操作 ────────────
  const handleTogglePromptSkill = (skillId: string) => {
    setActiveSkillIds((prev) =>
      prev.includes(skillId) ? prev.filter((id) => id !== skillId) : [...prev, skillId]
    );
  };

  const handleAddCustomPromptSkill = () => {
    if (!newPromptSkillName.trim() || !newPromptSkillFragment.trim()) return;
    const newId = `custom_${Date.now()}`;
    const newItem: SkillItem = {
      id: newId,
      name: newPromptSkillName.trim(),
      icon: newPromptSkillIcon.trim() || '⚡',
      description: newPromptSkillDesc.trim() || '用户自定义叠加增强技能',
      prompt_fragment: newPromptSkillFragment.trim(),
      is_default: false,
    };
    const nextCustom = [...customPromptSkills, newItem];
    setCustomPromptSkills(nextCustom);
    setPromptSkills((prev) => [...prev, newItem]);
    setActiveSkillIds((prev) => [...prev, newId]);
    setNewPromptSkillName('');
    setNewPromptSkillDesc('');
    setNewPromptSkillFragment('');
    setShowAddPromptSkill(false);
  };

  const handleDeleteCustomPromptSkill = (e: React.MouseEvent, skillId: string) => {
    e.stopPropagation();
    setCustomPromptSkills((prev) => prev.filter((s) => s.id !== skillId));
    setPromptSkills((prev) => prev.filter((s) => s.id !== skillId));
    setActiveSkillIds((prev) => prev.filter((id) => id !== skillId));
  };

  const handleSavePersonality = async () => {
    try {
      setSavingPersonality(true);
      setPersonalityFeedback(null);
      const payload = {
        selected_persona_id: selectedPersonaId,
        active_skill_ids: activeSkillIds,
        custom_system_prompt: customPrompt,
        custom_skills: customPromptSkills,
      };

      const res = await apiFetch('/api/v1/settings/personality', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const resData = await res.json();
      if (res.ok) {
        localStorage.setItem('agenthub_personality_config', JSON.stringify(payload));
        setPersonalityFeedback({
          success: true,
          msg: '回复性格与叠加 SKILL 设置已成功保存并立即生效！',
        });
        onProvidersChanged?.();
      } else {
        setPersonalityFeedback({ success: false, msg: resData.detail || '保存失败' });
      }
    } catch (e: any) {
      setPersonalityFeedback({ success: false, msg: e.message || '网络异常' });
    } finally {
      setSavingPersonality(false);
    }
  };

  // ──────────── Workspace Skills 与 MCP 操作 ────────────
  const handleCreateSkill = async () => {
    if (!newSkillName.trim() || !newSkillContent.trim()) {
      setExtFeedback({ success: false, msg: '请填写技能名称与 Markdown 规程正文' });
      return;
    }
    try {
      const res = await apiFetch('/api/v1/extensions/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newSkillName.trim(),
          description: newSkillDesc.trim() || '自定义按需专业技能包',
          content: newSkillContent.trim(),
        }),
      });
      const data = await res.json();
      if (res.ok) {
        const freshSkills = data.skills || [];
        setSkills(freshSkills);
        setExtensionSkillsCache(freshSkills);
        invalidateWorkspaceSkills(workspaceId);
        onSkillsUpdated?.();
        setNewSkillName('');
        setNewSkillDesc('');
        setNewSkillContent('');
        setSkillFormMode('none');
        setExtFeedback({ success: true, msg: `✅ 已成功创建并挂载技能包：${data.skill?.name}` });
      } else {
        setExtFeedback({ success: false, msg: data.detail || '创建 Skill 失败' });
      }
    } catch (e: any) {
      setExtFeedback({ success: false, msg: e.message || '网络异常' });
    }
  };

  const handleImportSkill = async () => {
    if (!importSkillText.trim()) {
      setExtFeedback({ success: false, msg: '请先选择 SKILL.md 文件或粘贴技能 Markdown 内容' });
      return;
    }
    try {
      const res = await apiFetch('/api/v1/extensions/skills/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: importSkillFilename,
          content: importSkillText.trim(),
        }),
      });
      const data = await res.json();
      if (res.ok) {
        const freshSkills = data.skills || [];
        setSkills(freshSkills);
        setExtensionSkillsCache(freshSkills);
        invalidateWorkspaceSkills(workspaceId);
        onSkillsUpdated?.();
        setImportSkillText('');
        setSkillFormMode('none');
        setExtFeedback({ success: true, msg: `✅ 成功导入并挂载技能包：${data.skill?.name}` });
      } else {
        setExtFeedback({ success: false, msg: data.detail || '导入 Skill 失败' });
      }
    } catch (e: any) {
      setExtFeedback({ success: false, msg: e.message || '导入失败' });
    }
  };

  const handleSkillFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportSkillFilename(file.name);
    const reader = new FileReader();
    reader.onload = (ev) => {
      setImportSkillText(String(ev.target?.result || ''));
    };
    reader.readAsText(file, 'utf-8');
    e.target.value = '';
  };

  const handleDeleteSkill = async (skillId: string) => {
    try {
      const res = await apiFetch(`/api/v1/extensions/skills/${encodeURIComponent(skillId)}`, {
        method: 'DELETE',
      });
      const data = await res.json();
      if (res.ok) {
        const freshSkills = data.skills || [];
        setSkills(freshSkills);
        setExtensionSkillsCache(freshSkills);
        invalidateWorkspaceSkills(workspaceId);
        onSkillsUpdated?.();
        setExtFeedback({ success: true, msg: `已卸载技能包 [${skillId}]` });
      }
    } catch (e) {}
  };

  const handleToggleSkillPreview = async (sk: SkillInfo) => {
    if (previewSkillId === sk.id) {
      setPreviewSkillId(null);
      return;
    }
    setPreviewSkillId(sk.id);
    if (sk.content) return;

    setLoadingPreviewId(sk.id);
    try {
      const detail = await fetchExtensionSkillDetail(sk.id);
      if (detail?.content) {
        setSkills((prev) =>
          prev.map((item) => (item.id === sk.id ? { ...item, content: detail.content } : item))
        );
      }
    } catch {
      // 忽略瞬时异常
    } finally {
      setLoadingPreviewId((curr) => (curr === sk.id ? null : curr));
    }
  };

  const handleAddMcpServer = async () => {
    if (!newMcpName.trim() || !newMcpCommand.trim()) {
      setExtFeedback({ success: false, msg: 'MCP 服务名称与启动命令不能为空' });
      return;
    }
    let parsedEnv: Record<string, string> = {};
    if (newMcpEnvJson.trim()) {
      try {
        parsedEnv = JSON.parse(newMcpEnvJson.trim());
      } catch {
        setExtFeedback({ success: false, msg: '环境变量必须为合法的 JSON 对象（如 {"GITHUB_TOKEN": "xxx"}）' });
        return;
      }
    }
    const argsList = newMcpArgs
      .split(/\s+/)
      .map((s) => s.trim())
      .filter(Boolean);

    try {
      const res = await apiFetch('/api/v1/extensions/mcp/server', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newMcpName.trim(),
          command: newMcpCommand.trim(),
          args: argsList,
          env: parsedEnv,
          description: newMcpDesc.trim(),
          enabled: true,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setMcpServers(data.mcpServers || {});
        setNewMcpName('');
        setNewMcpDesc('');
        setNewMcpEnvJson('');
        setMcpFormMode('none');
        setExtFeedback({ success: true, msg: `✅ 已成功添加并启用 MCP 服务：${newMcpName.trim()}` });
      } else {
        setExtFeedback({ success: false, msg: data.detail || '添加 MCP 服务失败' });
      }
    } catch (e: any) {
      setExtFeedback({ success: false, msg: e.message || '网络异常' });
    }
  };

  const handleImportMcpJson = async () => {
    if (!importMcpJson.trim()) {
      setExtFeedback({ success: false, msg: '请选择 JSON 配置文件或粘贴 MCP JSON 配置' });
      return;
    }
    try {
      const res = await apiFetch('/api/v1/extensions/mcp/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_json: importMcpJson.trim() }),
      });
      const data = await res.json();
      if (res.ok) {
        setMcpServers(data.mcpServers || {});
        setImportMcpJson('');
        setMcpFormMode('none');
        setExtFeedback({
          success: true,
          msg: `✅ 成功导入 ${data.imported_count || 1} 个 MCP 服务节点！`,
        });
      } else {
        setExtFeedback({ success: false, msg: data.detail || '导入 MCP 配置失败' });
      }
    } catch (e: any) {
      setExtFeedback({ success: false, msg: e.message || '网络异常' });
    }
  };

  const handleMcpFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setImportMcpJson(String(ev.target?.result || ''));
    };
    reader.readAsText(file, 'utf-8');
    e.target.value = '';
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

  const handleDeleteMcpServer = async (srvName: string) => {
    try {
      const res = await apiFetch(`/api/v1/extensions/mcp/${encodeURIComponent(srvName)}`, {
        method: 'DELETE',
      });
      const data = await res.json();
      if (res.ok) {
        setMcpServers(data.mcpServers || {});
        setExtFeedback({ success: true, msg: `已移除 MCP 服务 [${srvName}]` });
      }
    } catch (e) {}
  };

  const customSkillIdSet = new Set(customPromptSkills.map((s) => s.id));

  const activeFactsCount = facts.filter((f) => f.is_active).length;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-4xl max-h-[88vh] flex flex-col rounded-2xl bg-surface-card border border-border shadow-2xl overflow-hidden text-foreground"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 顶部标题栏 */}
        <div className="px-6 py-4 border-b border-border flex items-center justify-between bg-surface-100/60">
          <div className="flex items-center space-x-2.5">
            <div className="p-2 rounded-xl bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
              <Brain className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-[15px] font-bold text-foreground">
                记忆中枢 · 精确事实双时间轴 · 账本追溯 · Skill 与 MCP
              </h2>
              <p className="text-xs text-muted-foreground">
                L4 确定性 KV 契约 · Append-Only 审计账本 · 长期记忆画像 · 回复性格与自演化 Skill 扩展总线
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
        <div className="px-6 pt-3 border-b border-border flex items-center justify-between gap-3 bg-surface-100/30 overflow-x-auto">
          <div className="flex items-center space-x-4 whitespace-nowrap shrink-0">
            <button
              onClick={() => setActiveTab('memories')}
              className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors flex items-center space-x-1.5 ${
                activeTab === 'memories'
                  ? 'border-emerald-500 text-emerald-600 dark:text-emerald-400'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>长期记忆画像 ({memories.length})</span>
            </button>

            <button
              onClick={() => setActiveTab('facts')}
              className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors flex items-center space-x-1.5 ${
                activeTab === 'facts'
                  ? 'border-teal-500 text-teal-600 dark:text-teal-400'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Clock className="w-3.5 h-3.5 text-teal-500" />
              <span>精确事实视图 ({activeFactsCount})</span>
            </button>

            <button
              onClick={() => setActiveTab('ledger')}
              className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors flex items-center space-x-1.5 ${
                activeTab === 'ledger'
                  ? 'border-amber-500 text-amber-600 dark:text-amber-400'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <History className="w-3.5 h-3.5 text-amber-500" />
              <span>事件账本追溯</span>
            </button>

            <button
              onClick={() => setActiveTab('personality')}
              className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors flex items-center space-x-1.5 ${
                activeTab === 'personality'
                  ? 'border-emerald-500 text-emerald-600 dark:text-emerald-400'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Brain className="w-3.5 h-3.5 text-purple-500" />
              <span>回复性格与 SKILL</span>
            </button>

            <button
              onClick={() => setActiveTab('extensions')}
              className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors flex items-center space-x-1.5 ${
                activeTab === 'extensions'
                  ? 'border-emerald-500 text-emerald-600 dark:text-emerald-400'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Puzzle className="w-3.5 h-3.5 text-teal-500" />
              <span>Skill 与 MCP ({skills.length + Object.keys(mcpServers).length})</span>
            </button>
          </div>

          {activeTab === 'memories' && (
            <div className="flex items-center space-x-2 pb-2 shrink-0">
              <button
                onClick={() => setIsAdding(!isAdding)}
                className="flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium transition-colors"
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

          {activeTab === 'facts' && (
            <div className="flex items-center space-x-2 pb-2 shrink-0">
              <button
                onClick={() => setIsAddingFact((prev) => !prev)}
                className="flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-teal-600 hover:bg-teal-500 text-white text-xs font-medium transition-colors whitespace-nowrap"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>新增契约事实</span>
              </button>
            </div>
          )}
        </div>

        {/* 内容区 */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {/* ────────── TAB 1: 长期记忆画像 ────────── */}
          {activeTab === 'memories' && (
            <>
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
                      className="flex-1 px-3 py-2 rounded-xl bg-surface-card border border-border text-xs text-foreground outline-none focus:border-emerald-500"
                    />
                    <button
                      type="button"
                      onClick={handleAddMemory}
                      className="px-3.5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium transition-colors"
                    >
                      保存
                    </button>
                  </div>
                </div>
              )}

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
                                className="flex-1 px-2.5 py-1.5 rounded-lg bg-surface-card border border-emerald-500 text-xs text-foreground outline-none"
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

          {/* ────────── TAB 1B: 精确事实视图 (双时间轴 L4) ────────── */}
          {activeTab === 'facts' && (
            <div className="space-y-4">
              {factFeedback && (
                <div
                  className={`p-3 rounded-xl text-xs flex items-center justify-between animate-fadeIn ${
                    factFeedback.success
                      ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300 border border-emerald-500/20'
                      : 'bg-rose-500/10 text-rose-600 dark:text-rose-300 border border-rose-500/20'
                  }`}
                >
                  <span>{factFeedback.msg}</span>
                  <button
                    type="button"
                    onClick={() => setFactFeedback(null)}
                    className="text-muted-foreground hover:text-foreground ml-2"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}

              {/* 筛选工具栏 */}
              <div className="flex flex-wrap items-center justify-between gap-2.5 p-3 rounded-xl bg-surface-100/70 border border-border">
                <div className="flex flex-wrap items-center gap-1.5">
                  {(
                    [
                      { id: 'merged', label: '当前合并视图 (Workspace 覆盖 GLOBAL)' },
                      { id: 'workspace', label: '仅当前工作区' },
                      { id: 'global', label: '仅全局 GLOBAL' },
                    ] as const
                  ).map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => setFactScopeFilter(opt.id)}
                      className={`px-2.5 py-1 rounded-lg text-[11px] font-medium border transition-colors ${
                        factScopeFilter === opt.id
                          ? 'bg-teal-600 text-white border-teal-600'
                          : 'bg-surface-card border-border text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={factCategoryFilter}
                    onChange={(e) =>
                      setFactCategoryFilter(
                        e.target.value as 'all' | 'biz_rule' | 'user_pref' | 'env_config'
                      )
                    }
                    className="px-2.5 py-1 rounded-lg bg-surface-card border border-border text-[11px] text-foreground outline-none"
                  >
                    <option value="all">全部分类</option>
                    <option value="biz_rule">业务规则</option>
                    <option value="user_pref">个人偏好</option>
                    <option value="env_config">环境契约</option>
                  </select>

                  <label className="flex items-center space-x-1.5 text-[11px] text-foreground cursor-pointer px-2 py-1 rounded-lg bg-surface-card border border-border">
                    <input
                      type="checkbox"
                      checked={factIncludeHistory}
                      onChange={(e) => setFactIncludeHistory(e.target.checked)}
                    />
                    <span>显示已归档历史版本</span>
                  </label>
                </div>
              </div>

              {/* 新增/覆盖契约事实表单 */}
              {isAddingFact && (
                <div className="p-4 rounded-xl bg-surface-100 border border-teal-500/35 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center space-x-2">
                      <span className="text-xs font-medium text-muted-foreground">事实分类:</span>
                      {(['biz_rule', 'user_pref', 'env_config'] as const).map((cat) => (
                        <button
                          key={cat}
                          type="button"
                          onClick={() => setNewFactCategory(cat)}
                          className={`px-2.5 py-1 rounded-lg text-xs border transition-all ${
                            newFactCategory === cat
                              ? FACT_CATEGORY_MAP[cat].colorClass + ' font-semibold'
                              : 'border-border text-muted-foreground hover:text-foreground'
                          }`}
                        >
                          {FACT_CATEGORY_MAP[cat].label}
                        </button>
                      ))}
                    </div>

                    <div className="flex items-center rounded-lg bg-surface-card border border-border p-0.5 text-[11px]">
                      <button
                        type="button"
                        onClick={() => setNewFactScope('workspace')}
                        className={`px-2 py-0.5 rounded-md flex items-center space-x-1 transition-colors ${
                          newFactScope === 'workspace'
                            ? 'bg-teal-600 text-white font-medium'
                            : 'text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        <FolderGit2 className="w-3 h-3" />
                        <span>当前工作区</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setNewFactScope('global')}
                        className={`px-2 py-0.5 rounded-md flex items-center space-x-1 transition-colors ${
                          newFactScope === 'global'
                            ? 'bg-teal-600 text-white font-medium'
                            : 'text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        <Globe className="w-3 h-3" />
                        <span>全局 GLOBAL</span>
                      </button>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <input
                      type="text"
                      value={newFactKey}
                      onChange={(e) => setNewFactKey(e.target.value)}
                      placeholder="事实键 (如 export_format)"
                      className="px-3 py-2 rounded-xl bg-surface-card border border-border text-xs font-mono text-foreground outline-none focus:border-teal-500"
                    />
                    <input
                      type="text"
                      value={newFactValue}
                      onChange={(e) => setNewFactValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleAddFact();
                      }}
                      placeholder="确定性事实值 (如 带高亮差异的 .xlsx)"
                      className="sm:col-span-2 px-3 py-2 rounded-xl bg-surface-card border border-border text-xs text-foreground outline-none focus:border-teal-500"
                    />
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                    <span>若同作用域下该事实键已存在，将自动关闭旧版本有效时间线并激活新值（凭据将自动脱敏）</span>
                    <div className="flex items-center space-x-2">
                      <button
                        type="button"
                        onClick={() => setIsAddingFact(false)}
                        className="px-3 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-surface-200"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        disabled={factActionId === 'add'}
                        onClick={handleAddFact}
                        className="px-4 py-1.5 rounded-lg bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold disabled:opacity-50"
                      >
                        {factActionId === 'add' ? '保存中...' : '保存并生效'}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {factsLoading ? (
                <div className="text-center py-12 text-xs text-muted-foreground">
                  正在加载双时间轴结构化事实视图...
                </div>
              ) : facts.length === 0 ? (
                <div className="text-center py-12 space-y-2">
                  <p className="text-sm font-medium text-foreground">暂无匹配的结构化契约事实</p>
                  <p className="text-xs text-muted-foreground max-w-md mx-auto">
                    在交互对齐卡中勾选参数、下达显式规则指令或点击右上角「新增契约事实」后，确定性键值将沉淀于此并优先注入推理上下文。
                  </p>
                </div>
              ) : (
                <div className="space-y-2.5">
                  {facts.map((item) => {
                    const catMeta = FACT_CATEGORY_MAP[item.category] || FACT_CATEGORY_MAP.biz_rule;
                    const isEditing = editingFactId === item.fact_id;
                    const isBusy = factActionId === item.fact_id;
                    const isGlobal = item.workspace_id === 'GLOBAL' || item.scope === 'global';

                    return (
                      <div
                        key={item.fact_id}
                        className={`p-3.5 rounded-xl border flex items-start justify-between gap-3 transition-colors ${
                          item.is_active
                            ? 'bg-surface-100/75 border-border hover:bg-surface-100'
                            : 'bg-surface-100/35 border-border/50 opacity-75'
                        }`}
                      >
                        <div className="flex-1 min-w-0 space-y-1.5">
                          <div className="flex items-center flex-wrap gap-1.5">
                            <span
                              className={`px-2 py-0.5 rounded-md text-[10px] border font-semibold ${
                                item.is_active
                                  ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                                  : 'bg-surface-200 text-muted-foreground border-border'
                              }`}
                            >
                              {item.is_active ? '生效中' : '历史已归档'}
                            </span>
                            <span
                              className={`px-2 py-0.5 rounded-md text-[10px] border font-medium ${
                                isGlobal
                                  ? 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/25'
                                  : 'bg-teal-500/15 text-teal-600 dark:text-teal-300 border-teal-500/30'
                              }`}
                            >
                              {isGlobal ? '🌐 GLOBAL 全局' : `📁 工作区 (${item.workspace_id})`}
                            </span>
                            <span
                              className={`px-2 py-0.5 rounded-md text-[10px] border font-medium ${catMeta.colorClass}`}
                            >
                              {catMeta.label}
                            </span>
                            <span className="px-1.5 py-0.5 rounded text-[10px] bg-surface-200 text-muted-foreground">
                              来源: {FACT_SOURCE_LABELS[item.source_type] || item.source_type}
                            </span>
                            {item.overridden_global && (
                              <span
                                className="px-2 py-0.5 rounded-md text-[10px] bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 font-medium"
                                title={`全局原值为: ${item.global_fallback_value}`}
                              >
                                ⚡ 已覆盖全局默认值: {item.global_fallback_value}
                              </span>
                            )}
                          </div>

                          <div className="flex items-baseline flex-wrap gap-2">
                            <code className="px-2 py-0.5 rounded bg-surface-card border border-border text-xs font-mono font-bold text-foreground">
                              {item.fact_key}
                            </code>
                            <span className="text-muted-foreground text-xs">=</span>
                            {isEditing ? (
                              <div className="flex-1 flex items-center space-x-2 min-w-[200px]">
                                <input
                                  type="text"
                                  value={editingFactValue}
                                  onChange={(e) => setEditingFactValue(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleSaveEditFact(item);
                                  }}
                                  className="flex-1 px-2.5 py-1 rounded-lg bg-surface-card border border-teal-500 text-xs text-foreground outline-none"
                                />
                                <button
                                  type="button"
                                  disabled={isBusy}
                                  onClick={() => handleSaveEditFact(item)}
                                  className="p-1.5 rounded-lg bg-teal-600 text-white hover:bg-teal-500 disabled:opacity-50"
                                  title="确认更新并生成新版本"
                                >
                                  <Check className="w-3.5 h-3.5" />
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setEditingFactId(null)}
                                  className="p-1.5 rounded-lg bg-surface-200 text-muted-foreground hover:text-foreground"
                                  title="取消编辑"
                                >
                                  <X className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            ) : (
                              <span className="text-[13px] font-medium text-foreground break-anywhere">
                                {item.fact_value}
                              </span>
                            )}
                          </div>

                          <div className="text-[10px] font-mono text-muted-foreground flex flex-wrap gap-x-4 gap-y-1">
                            <span>有效起点: {formatIsoTime(item.valid_from)}</span>
                            <span>
                              失效时间: {item.valid_to ? formatIsoTime(item.valid_to) : '至今持续生效'}
                            </span>
                            {item.source_event_id && <span>溯源事件: {item.source_event_id}</span>}
                          </div>
                        </div>

                        <div className="flex items-center space-x-1 shrink-0">
                          {item.is_active ? (
                            <>
                              <button
                                type="button"
                                disabled={isBusy}
                                onClick={() => {
                                  setEditingFactId(item.fact_id);
                                  setEditingFactValue(item.fact_value);
                                }}
                                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
                                title="更新覆盖此契约（保留历史演进线）"
                              >
                                <Edit3 className="w-3.5 h-3.5" />
                              </button>
                              <button
                                type="button"
                                disabled={isBusy}
                                onClick={() => handleDeactivateFact(item.fact_id)}
                                className="p-1.5 rounded-lg text-muted-foreground hover:text-amber-600 hover:bg-amber-500/10 transition-colors"
                                title="软停用归档（保留在历史时间轴）"
                              >
                                <Archive className="w-3.5 h-3.5" />
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              disabled={isBusy}
                              onClick={() => handleRollbackFact(item.fact_id)}
                              className="px-2.5 py-1 rounded-lg bg-teal-600/15 hover:bg-teal-600/25 text-teal-600 dark:text-teal-300 border border-teal-500/30 text-[11px] font-medium flex items-center space-x-1 transition-colors disabled:opacity-50"
                              title="重新激活该历史版本为当前有效契约"
                            >
                              <RotateCcw className="w-3 h-3" />
                              <span>回滚激活</span>
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* ────────── TAB 1C: 追加式事件账本审计视图 ────────── */}
          {activeTab === 'ledger' && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2.5 p-3 rounded-xl bg-surface-100/70 border border-border">
                <div className="flex items-center space-x-2 text-[11px] text-muted-foreground">
                  <Lock className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                  <span>
                    Append-Only 只增不改事件流水（共 {ledgerTotal} 条记录 · 敏感信息在落盘前自动脱敏）
                  </span>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setLedgerWorkspaceOnly((prev) => !prev);
                      setLedgerOffset(0);
                    }}
                    className={`px-2.5 py-1 rounded-lg text-[11px] border font-medium transition-colors ${
                      ledgerWorkspaceOnly
                        ? 'bg-amber-500/15 border-amber-500/35 text-amber-700 dark:text-amber-300'
                        : 'bg-surface-card border-border text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {ledgerWorkspaceOnly ? `仅当前工作区 (${workspaceId})` : '全部工作区'}
                  </button>

                  <select
                    value={ledgerEventType}
                    onChange={(e) => {
                      setLedgerEventType(e.target.value);
                      setLedgerOffset(0);
                    }}
                    className="px-2.5 py-1 rounded-lg bg-surface-card border border-border text-[11px] text-foreground outline-none"
                  >
                    <option value="">全部事件类型</option>
                    <option value="inquiry_confirmed">对齐卡确认</option>
                    <option value="tool_error_recovered">故障自愈恢复</option>
                    <option value="user_intervention">人工纠偏干预</option>
                    <option value="artifact_produced">产出资产落盘</option>
                    <option value="tool_executed">工具算子执行</option>
                    <option value="user_input">用户输入指令</option>
                  </select>

                  <button
                    type="button"
                    onClick={() => fetchLedgerEvents()}
                    className="p-1.5 rounded-lg bg-surface-card border border-border text-muted-foreground hover:text-foreground transition-colors"
                    title="刷新账本"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${ledgerLoading ? 'animate-spin' : ''}`} />
                  </button>
                </div>
              </div>

              {ledgerLoading ? (
                <div className="text-center py-12 text-xs text-muted-foreground">
                  正在读取本地 event_ledger 审计账本...
                </div>
              ) : ledgerEvents.length === 0 ? (
                <div className="text-center py-12 space-y-2">
                  <p className="text-sm font-medium text-foreground">暂无事件账本记录</p>
                  <p className="text-xs text-muted-foreground max-w-md mx-auto">
                    在工作区正常发起对话、通过对齐卡确认参数或执行自主任务工具调用后，审计流水将自动按时间序列追加至此（无痕模式不记录）。
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {ledgerEvents.map((evt) => {
                    const typeMeta = LEDGER_EVENT_TYPE_MAP[evt.event_type] || {
                      label: evt.event_type,
                      badgeClass: 'bg-surface-200 text-muted-foreground border-border',
                    };
                    const cleanPayload = { ...(evt.payload || {}) };
                    delete cleanPayload._redacted_types;
                    const serialized = JSON.stringify(cleanPayload, null, 2);

                    return (
                      <div
                        key={evt.id}
                        className="p-3 rounded-xl bg-surface-100/70 border border-border/80 space-y-1.5"
                      >
                        <div className="flex items-center justify-between flex-wrap gap-2">
                          <div className="flex items-center flex-wrap gap-1.5">
                            <span
                              className={`px-2 py-0.5 rounded-md text-[10px] border font-semibold ${typeMeta.badgeClass}`}
                            >
                              {typeMeta.label}
                            </span>
                            <span className="px-1.5 py-0.5 rounded bg-surface-200 text-[10px] font-mono text-muted-foreground">
                              {evt.actor}
                            </span>
                            <span className="text-[11px] font-mono text-muted-foreground">
                              Turn #{evt.turn_id}
                            </span>
                            {evt.skill_id && (
                              <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 text-[10px] font-mono">
                                /{evt.skill_id}
                              </span>
                            )}
                            {evt.is_redacted && (
                              <span
                                className="px-2 py-0.5 rounded-md bg-rose-500/15 text-rose-600 dark:text-rose-300 border border-rose-500/30 text-[10px] font-semibold flex items-center space-x-1"
                                title={
                                  evt.redactions?.length
                                    ? `已脱敏凭据类型: ${evt.redactions.join(', ')}`
                                    : '包含敏感凭据，落盘前已脱敏'
                                }
                              >
                                <Lock className="w-2.5 h-2.5" />
                                <span>
                                  🔒 已脱敏
                                  {evt.redactions && evt.redactions.length > 0
                                    ? ` (${evt.redactions.join(', ')})`
                                    : ''}
                                </span>
                              </span>
                            )}
                          </div>
                          <span className="text-[10px] font-mono text-muted-foreground">
                            {formatIsoTime(evt.created_at)}
                          </span>
                        </div>

                        <pre className="p-2 rounded-lg bg-surface-card border border-border/70 text-[11px] font-mono text-foreground/85 whitespace-pre-wrap break-all max-h-36 overflow-y-auto">
                          {serialized}
                        </pre>
                      </div>
                    );
                  })}

                  {/* 分页栏 */}
                  <div className="flex items-center justify-between pt-2 text-xs text-muted-foreground">
                    <span>
                      第 {Math.floor(ledgerOffset / ledgerPageSize) + 1} /{' '}
                      {Math.max(1, Math.ceil(ledgerTotal / ledgerPageSize))} 页 · 共 {ledgerTotal} 条
                    </span>
                    <div className="flex items-center space-x-2">
                      <button
                        type="button"
                        disabled={ledgerOffset <= 0}
                        onClick={() => setLedgerOffset((prev) => Math.max(0, prev - ledgerPageSize))}
                        className="px-2.5 py-1 rounded-lg border border-border bg-surface-card hover:bg-surface-200 disabled:opacity-40 flex items-center space-x-1"
                      >
                        <ChevronLeft className="w-3.5 h-3.5" />
                        <span>上一页</span>
                      </button>
                      <button
                        type="button"
                        disabled={ledgerOffset + ledgerPageSize >= ledgerTotal}
                        onClick={() => setLedgerOffset((prev) => prev + ledgerPageSize)}
                        className="px-2.5 py-1 rounded-lg border border-border bg-surface-card hover:bg-surface-200 disabled:opacity-40 flex items-center space-x-1"
                      >
                        <span>下一页</span>
                        <ChevronRight className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ────────── TAB 2: 回复性格与 SKILL 定制 ────────── */}
          {activeTab === 'personality' && (
            <div className="space-y-6">
              {personalityFeedback && (
                <div
                  className={`p-3.5 rounded-xl text-xs flex items-center space-x-2 animate-fadeIn ${
                    personalityFeedback.success
                      ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300 border border-emerald-500/20'
                      : 'bg-rose-500/10 text-rose-600 dark:text-rose-300 border border-rose-500/20'
                  }`}
                >
                  {personalityFeedback.success ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                  ) : (
                    <AlertTriangle className="w-4 h-4 text-rose-500 shrink-0" />
                  )}
                  <span>{personalityFeedback.msg}</span>
                </div>
              )}

              {/* 1. 回复性格模板 (Persona) */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-bold text-foreground flex items-center space-x-2">
                    <Brain className="w-4 h-4 text-purple-500" />
                    <span>核心回复性格模板</span>
                  </label>
                  <span className="text-[11px] text-muted-foreground">点击卡片即可切换</span>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  {presets.map((persona) => {
                    const isSelected = persona.id === selectedPersonaId;
                    return (
                      <div
                        key={persona.id}
                        onClick={() => setSelectedPersonaId(persona.id)}
                        className={`p-3.5 rounded-2xl border transition-all cursor-pointer relative ${
                          isSelected
                            ? 'bg-emerald-600/10 border-emerald-500/60 shadow-lg shadow-emerald-500/10'
                            : 'bg-surface-100 border-border hover:border-border-hover'
                        }`}
                      >
                        <div className="flex items-start justify-between">
                          <div className="flex items-center space-x-2">
                            <span className="text-xl">{persona.avatar}</span>
                            <div>
                              <div className="text-xs font-bold text-foreground flex items-center space-x-1.5">
                                <span>{persona.name}</span>
                                {isSelected && (
                                  <span className="w-4 h-4 rounded-full bg-emerald-500 text-white flex items-center justify-center text-[10px]">
                                    <Check className="w-2.5 h-2.5 stroke-[3]" />
                                  </span>
                                )}
                              </div>
                              <p className="text-[10px] text-emerald-500 font-mono mt-0.5">
                                {persona.tagline}
                              </p>
                            </div>
                          </div>
                        </div>
                        <p className="text-[11px] text-muted-foreground mt-2 leading-relaxed break-anywhere">
                          {persona.description}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* 2. 叠加增强技能 (SKILL Prompts) */}
              <div className="space-y-3 pt-2 border-t border-border">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-bold text-foreground flex items-center space-x-2">
                    <Layers className="w-4 h-4 text-teal-500" />
                    <span>叠加增强技能 (SKILL Prompts)</span>
                  </label>
                  <div className="flex items-center space-x-2">
                    <span className="text-[11px] text-muted-foreground">可多选叠加激活</span>
                    <button
                      type="button"
                      onClick={() => setShowAddPromptSkill(!showAddPromptSkill)}
                      className="px-2.5 py-1 rounded-lg bg-teal-600/15 hover:bg-teal-600/25 text-teal-600 dark:text-teal-400 border border-teal-500/30 text-[11px] font-semibold flex items-center space-x-1 transition-colors"
                    >
                      <Plus className="w-3 h-3" />
                      <span>自定义增强技能</span>
                    </button>
                  </div>
                </div>

                {showAddPromptSkill && (
                  <div className="p-3.5 rounded-xl bg-surface-100 border border-teal-500/30 space-y-2.5">
                    <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
                      <input
                        type="text"
                        value={newPromptSkillIcon}
                        onChange={(e) => setNewPromptSkillIcon(e.target.value)}
                        placeholder="图标 (如 ⚡)"
                        className="px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none"
                      />
                      <input
                        type="text"
                        value={newPromptSkillName}
                        onChange={(e) => setNewPromptSkillName(e.target.value)}
                        placeholder="技能名称 (如 Rust 零拷贝专家)"
                        className="sm:col-span-3 px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none"
                      />
                    </div>
                    <input
                      type="text"
                      value={newPromptSkillDesc}
                      onChange={(e) => setNewPromptSkillDesc(e.target.value)}
                      placeholder="简短描述（显示在卡片上）"
                      className="w-full px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none"
                    />
                    <textarea
                      rows={2}
                      value={newPromptSkillFragment}
                      onChange={(e) => setNewPromptSkillFragment(e.target.value)}
                      placeholder="注入 System Prompt 的具体技能规则片段..."
                      className="w-full px-2.5 py-1.5 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none font-mono resize-none"
                    />
                    <div className="flex justify-end space-x-2">
                      <button
                        type="button"
                        onClick={() => setShowAddPromptSkill(false)}
                        className="px-3 py-1 rounded-lg text-xs text-muted-foreground hover:bg-surface-200"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        onClick={handleAddCustomPromptSkill}
                        className="px-3.5 py-1 rounded-lg bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold"
                      >
                        添加至列表
                      </button>
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3">
                  {promptSkills.map((skill) => {
                    const isActive = activeSkillIds.includes(skill.id);
                    const isCustom = customSkillIdSet.has(skill.id);
                    return (
                      <div
                        key={skill.id}
                        onClick={() => handleTogglePromptSkill(skill.id)}
                        className={`p-3 rounded-2xl border transition-all cursor-pointer flex items-start space-x-3 ${
                          isActive
                            ? 'bg-teal-500/10 border-teal-500/50'
                            : 'bg-surface-100 border-border hover:border-border-hover'
                        }`}
                      >
                        <span className="text-xl shrink-0 mt-0.5">{skill.icon}</span>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-bold text-foreground truncate">
                              {skill.name}
                            </span>
                            <div className="flex items-center space-x-1.5">
                              {isCustom && (
                                <button
                                  type="button"
                                  onClick={(e) => handleDeleteCustomPromptSkill(e, skill.id)}
                                  className="p-0.5 text-muted-foreground hover:text-rose-500"
                                  title="删除此自定义技能"
                                >
                                  <Trash2 className="w-3 h-3" />
                                </button>
                              )}
                              <input
                                type="checkbox"
                                checked={isActive}
                                onChange={() => {}}
                                className="rounded border-border bg-surface-200 text-teal-500 focus:ring-0"
                              />
                            </div>
                          </div>
                          <p className="text-[11px] text-muted-foreground mt-1 leading-snug break-anywhere">
                            {skill.description}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* 3. 自定义系统指令补充与保存按钮 */}
              <div className="space-y-3 pt-2 border-t border-border">
                <label className="text-xs font-bold text-foreground flex items-center justify-between">
                  <span>自定义系统指令补充 (选填，将在每次请求追加至模型头部)</span>
                  <span className="text-[10px] text-muted-foreground">支持个人化习惯定制</span>
                </label>
                <textarea
                  rows={3}
                  placeholder="例如：回答始终使用中文、涉及 Python 必须标注类型注解、避免长篇大论..."
                  value={customPrompt}
                  onChange={(e) => setCustomPrompt(e.target.value)}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-surface-100 border border-border text-foreground placeholder-muted-foreground text-xs focus:outline-none focus:border-emerald-500 resize-none font-mono"
                />

                <div className="flex justify-end pt-1">
                  <button
                    type="button"
                    onClick={handleSavePersonality}
                    disabled={savingPersonality}
                    className="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs transition-colors shadow-md shadow-emerald-500/20 flex items-center space-x-1.5 disabled:opacity-50"
                  >
                    {savingPersonality ? (
                      <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Save className="w-3.5 h-3.5" />
                    )}
                    <span>保存性格与 SKILL 设置</span>
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* ────────── TAB 3: Skill 技能包与 MCP 扩展 (支持自定义添加与导入) ────────── */}
          {activeTab === 'extensions' && (
            <div className="space-y-6">
              {extFeedback && (
                <div
                  className={`p-3 rounded-xl text-xs flex items-center justify-between animate-fadeIn ${
                    extFeedback.success
                      ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300 border border-emerald-500/20'
                      : 'bg-rose-500/10 text-rose-600 dark:text-rose-300 border border-rose-500/20'
                  }`}
                >
                  <span>{extFeedback.msg}</span>
                  <button
                    type="button"
                    onClick={() => setExtFeedback(null)}
                    className="text-muted-foreground hover:text-foreground ml-2"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}

              {/* 1. Workspace Skills 区域 */}
              <div className="space-y-3">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <div>
                    <h3 className="text-xs font-bold text-foreground flex items-center space-x-1.5">
                      <Terminal className="w-3.5 h-3.5 text-emerald-500" />
                      <span>按需专业技能包 (Workspace Skills · ./skills/*/SKILL.md)</span>
                    </h3>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      平时仅向模型暴露摘要（省 Token），触发任务时按需自动注入完整 SKILL.md 规程
                    </p>
                  </div>

                  <div className="flex items-center space-x-2">
                    <button
                      type="button"
                      onClick={() =>
                        setSkillFormMode(skillFormMode === 'create' ? 'none' : 'create')
                      }
                      className="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium transition-colors"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      <span>新建 Skill</span>
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setSkillFormMode(skillFormMode === 'import' ? 'none' : 'import')
                      }
                      className="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg bg-surface-100 hover:bg-surface-200 border border-border text-foreground text-xs font-medium transition-colors"
                    >
                      <Upload className="w-3.5 h-3.5 text-emerald-500" />
                      <span>导入 SKILL.md</span>
                    </button>
                  </div>
                </div>

                {/* 新建自定义 Skill 表单 */}
                {skillFormMode === 'create' && (
                  <div className="p-4 rounded-xl bg-surface-100 border border-emerald-500/30 space-y-3">
                    <div className="text-xs font-bold text-foreground">
                      新建自定义按需技能包 (自动生成 skills/&lt;name&gt;/SKILL.md)
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                      <input
                        type="text"
                        value={newSkillName}
                        onChange={(e) => setNewSkillName(e.target.value)}
                        placeholder="技能标识名称 (如 docker-deploy-helper)"
                        className="px-3 py-2 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none font-mono"
                      />
                      <input
                        type="text"
                        value={newSkillDesc}
                        onChange={(e) => setNewSkillDesc(e.target.value)}
                        placeholder="一句话触发条件与用途说明"
                        className="px-3 py-2 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none"
                      />
                    </div>
                    <textarea
                      rows={5}
                      value={newSkillContent}
                      onChange={(e) => setNewSkillContent(e.target.value)}
                      placeholder={
                        '# 技能执行规程标题\n\n1. 第一步执行的检查指令...\n2. 第二步处理规范...\n3. 边界条件与安全注意事项...'
                      }
                      className="w-full px-3 py-2 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none font-mono resize-y"
                    />
                    <div className="flex justify-end space-x-2">
                      <button
                        type="button"
                        onClick={() => setSkillFormMode('none')}
                        className="px-3 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-surface-200"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        onClick={handleCreateSkill}
                        className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold"
                      >
                        保存并挂载 Skill
                      </button>
                    </div>
                  </div>
                )}

                {/* 导入 SKILL.md 表单 */}
                {skillFormMode === 'import' && (
                  <div className="p-4 rounded-xl bg-surface-100 border border-emerald-500/30 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold text-foreground">
                        导入外部 SKILL.md 文件或直接粘贴 Markdown 规程
                      </span>
                      <div>
                        <input
                          ref={skillFileInputRef}
                          type="file"
                          accept=".md,.txt"
                          onChange={handleSkillFileSelected}
                          className="hidden"
                        />
                        <button
                          type="button"
                          onClick={() => skillFileInputRef.current?.click()}
                          className="px-3 py-1 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 text-xs font-medium flex items-center space-x-1"
                        >
                          <Upload className="w-3 h-3" />
                          <span>选择本地 SKILL.md 文件</span>
                        </button>
                      </div>
                    </div>
                    <textarea
                      rows={5}
                      value={importSkillText}
                      onChange={(e) => setImportSkillText(e.target.value)}
                      placeholder={
                        '---\nname: my-custom-skill\ndescription: 当需要执行特定领域任务时调用此技能\n---\n\n# 详细技能操作指南...'
                      }
                      className="w-full px-3 py-2 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none font-mono resize-y"
                    />
                    <div className="flex justify-end space-x-2">
                      <button
                        type="button"
                        onClick={() => setSkillFormMode('none')}
                        className="px-3 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-surface-200"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        onClick={handleImportSkill}
                        className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold"
                      >
                        确认导入并挂载
                      </button>
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-1 gap-2.5">
                  {skills.map((sk) => {
                    const isPreviewing = previewSkillId === sk.id;
                    return (
                      <div
                        key={sk.id}
                        className="p-3.5 rounded-xl bg-surface-100/70 border border-border space-y-2"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="space-y-1 flex-1 min-w-0">
                            <div className="flex items-center space-x-2">
                              <span className="text-xs font-bold text-foreground font-mono">
                                {sk.name}
                              </span>
                              <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
                                已就绪
                              </span>
                              {sk.is_builtin && (
                                <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
                                  内置示例
                                </span>
                              )}
                              {sk.knowledge_files?.includes('experience_playbook.md') && (
                                <span
                                  className="px-1.5 py-0.5 rounded text-[10px] bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/25 flex items-center space-x-1"
                                  title="已挂载自演化经验守则 (knowledge/experience_playbook.md)"
                                >
                                  <BookOpen className="w-2.5 h-2.5" />
                                  <span>含经验守则</span>
                                </span>
                              )}
                            </div>
                            <p className="text-xs text-muted-foreground break-anywhere">
                              {sk.description}
                            </p>
                          </div>

                          <div className="flex items-center space-x-1 shrink-0">
                            {(sk.content || sk.has_content) && (
                              <button
                                type="button"
                                onClick={() => handleToggleSkillPreview(sk)}
                                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
                                title="查看 SKILL.md 内容"
                              >
                                <Eye className="w-3.5 h-3.5" />
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => handleDeleteSkill(sk.id)}
                              className="p-1.5 rounded-lg text-muted-foreground hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
                              title="删除此技能包"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>

                        {isPreviewing && (
                          <pre className="p-3 rounded-lg bg-surface-card border border-border text-[11px] font-mono text-muted-foreground overflow-x-auto whitespace-pre-wrap max-h-48">
                            {loadingPreviewId === sk.id
                              ? '正在按需读取 SKILL.md 原文...'
                              : sk.content || '暂无内容'}
                          </pre>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* 2. MCP Servers 区域 */}
              <div className="space-y-3 pt-3 border-t border-border">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <div>
                    <h3 className="text-xs font-bold text-foreground flex items-center space-x-1.5">
                      <ShieldCheck className="w-3.5 h-3.5 text-purple-500" />
                      <span>MCP 外部扩展服务池 (data/mcp_servers.json)</span>
                    </h3>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      常驻 JSON-RPC 连接池 · 支持手动添加或直接导入 Claude Desktop / Cursor MCP JSON 配置
                    </p>
                  </div>

                  <div className="flex items-center space-x-2">
                    <button
                      type="button"
                      onClick={() =>
                        setMcpFormMode(mcpFormMode === 'create' ? 'none' : 'create')
                      }
                      className="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-xs font-medium transition-colors"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      <span>添加 MCP 服务</span>
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setMcpFormMode(mcpFormMode === 'import' ? 'none' : 'import')
                      }
                      className="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg bg-surface-100 hover:bg-surface-200 border border-border text-foreground text-xs font-medium transition-colors"
                    >
                      <Upload className="w-3.5 h-3.5 text-purple-500" />
                      <span>导入 MCP JSON</span>
                    </button>
                  </div>
                </div>

                {/* 手动添加 MCP 服务表单 */}
                {mcpFormMode === 'create' && (
                  <div className="p-4 rounded-xl bg-surface-100 border border-purple-500/30 space-y-3">
                    <div className="text-xs font-bold text-foreground">
                      添加自定义 stdio MCP 服务节点
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                      <input
                        type="text"
                        value={newMcpName}
                        onChange={(e) => setNewMcpName(e.target.value)}
                        placeholder="服务名称 (如 github / sqlite)"
                        className="px-3 py-2 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none font-mono"
                      />
                      <input
                        type="text"
                        value={newMcpCommand}
                        onChange={(e) => setNewMcpCommand(e.target.value)}
                        placeholder="启动命令 (如 npx / uvx / python)"
                        className="px-3 py-2 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none font-mono"
                      />
                      <input
                        type="text"
                        value={newMcpDesc}
                        onChange={(e) => setNewMcpDesc(e.target.value)}
                        placeholder="功能说明 (选填)"
                        className="px-3 py-2 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none"
                      />
                    </div>
                    <input
                      type="text"
                      value={newMcpArgs}
                      onChange={(e) => setNewMcpArgs(e.target.value)}
                      placeholder="启动参数（空格分隔，如 -y @modelcontextprotocol/server-github）"
                      className="w-full px-3 py-2 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none font-mono"
                    />
                    <input
                      type="text"
                      value={newMcpEnvJson}
                      onChange={(e) => setNewMcpEnvJson(e.target.value)}
                      placeholder='环境变量 JSON（选填，如 {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx"}）'
                      className="w-full px-3 py-2 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none font-mono"
                    />
                    <div className="flex justify-end space-x-2">
                      <button
                        type="button"
                        onClick={() => setMcpFormMode('none')}
                        className="px-3 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-surface-200"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        onClick={handleAddMcpServer}
                        className="px-4 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold"
                      >
                        保存并接入 MCP
                      </button>
                    </div>
                  </div>
                )}

                {/* 导入 MCP JSON 配置表单 */}
                {mcpFormMode === 'import' && (
                  <div className="p-4 rounded-xl bg-surface-100 border border-purple-500/30 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold text-foreground">
                        导入 mcp_servers.json / claude_desktop_config.json 配置
                      </span>
                      <div>
                        <input
                          ref={mcpFileInputRef}
                          type="file"
                          accept=".json"
                          onChange={handleMcpFileSelected}
                          className="hidden"
                        />
                        <button
                          type="button"
                          onClick={() => mcpFileInputRef.current?.click()}
                          className="px-3 py-1 rounded-lg bg-purple-500/15 hover:bg-purple-500/25 text-purple-600 dark:text-purple-400 border border-purple-500/30 text-xs font-medium flex items-center space-x-1"
                        >
                          <Upload className="w-3 h-3" />
                          <span>选择本地 JSON 文件</span>
                        </button>
                      </div>
                    </div>
                    <textarea
                      rows={5}
                      value={importMcpJson}
                      onChange={(e) => setImportMcpJson(e.target.value)}
                      placeholder={
                        '{\n  "mcpServers": {\n    "memory": {\n      "command": "npx",\n      "args": ["-y", "@modelcontextprotocol/server-memory"]\n    }\n  }\n}'
                      }
                      className="w-full px-3 py-2 rounded-lg bg-surface-card border border-border text-xs text-foreground outline-none font-mono resize-y"
                    />
                    <div className="flex justify-end space-x-2">
                      <button
                        type="button"
                        onClick={() => setMcpFormMode('none')}
                        className="px-3 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-surface-200"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        onClick={handleImportMcpJson}
                        className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold"
                      >
                        确认批量导入 MCP
                      </button>
                    </div>
                  </div>
                )}

                <div className="space-y-2">
                  {Object.entries(mcpServers).map(([srvName, cfg]: [string, any]) => (
                    <div
                      key={srvName}
                      className="p-3.5 rounded-xl bg-surface-100/70 border border-border flex items-center justify-between gap-3"
                    >
                      <div className="space-y-1 flex-1 min-w-0">
                        <div className="flex items-center flex-wrap gap-2">
                          <span className="text-xs font-bold text-foreground font-mono">
                            {srvName}
                          </span>
                          <span className="text-[11px] text-muted-foreground font-mono bg-surface-200 px-1.5 py-0.5 rounded truncate max-w-md">
                            {cfg.command} {(cfg.args || []).join(' ')}
                          </span>
                        </div>
                        {cfg.description && (
                          <p className="text-xs text-muted-foreground break-anywhere">
                            {cfg.description}
                          </p>
                        )}
                      </div>

                      <div className="flex items-center space-x-2 shrink-0">
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
                        <button
                          type="button"
                          onClick={() => handleDeleteMcpServer(srvName)}
                          className="p-1.5 rounded-lg text-muted-foreground hover:text-rose-500 hover:bg-rose-500/10 transition-colors"
                          title="删除此 MCP 服务"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
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
