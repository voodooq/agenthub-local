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
} from 'lucide-react';
import { LongTermMemoryItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import { PersonaPreset, SkillItem } from './SettingsModal';

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
  content?: string;
  is_builtin?: boolean;
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

export function MemoryHubModal({ isOpen, onClose, onProvidersChanged }: MemoryHubModalProps) {
  const [activeTab, setActiveTab] = useState<'memories' | 'personality' | 'extensions'>('memories');
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
      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          onClose();
        }
      };
      window.addEventListener('keydown', handleKeyDown);

      fetchMemories();
      fetchPersonalitySettings();
      fetchExtensions();
      setPersonalityFeedback(null);
      setExtFeedback(null);

      return () => {
        window.removeEventListener('keydown', handleKeyDown);
      };
    }
  }, [isOpen, onClose]);

  if (!isOpen) return null;

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
        setSkills(data.skills || []);
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
        setSkills(data.skills || []);
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
        setSkills(data.skills || []);
        setExtFeedback({ success: true, msg: `已卸载技能包 [${skillId}]` });
      }
    } catch (e) {}
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[88vh] flex flex-col rounded-2xl bg-surface-card border border-border shadow-2xl overflow-hidden text-foreground"
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
                记忆中枢 · 回复性格 · Skill 与 MCP 扩展总线
              </h2>
              <p className="text-xs text-muted-foreground">
                SQLite FTS5 长期记忆画像 · 回复性格与 SKILL 定制 · 支持自定义 Skill 与 MCP 服务添加及导入
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
                  ? 'border-emerald-500 text-emerald-600 dark:text-emerald-400'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>长期记忆画像 ({memories.length})</span>
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
              <span>回复性格与 SKILL 定制</span>
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
              <span>Skill 技能包与 MCP ({skills.length + Object.keys(mcpServers).length})</span>
            </button>
          </div>

          {activeTab === 'memories' && (
            <div className="flex items-center space-x-2 pb-2">
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
                            </div>
                            <p className="text-xs text-muted-foreground break-anywhere">
                              {sk.description}
                            </p>
                          </div>

                          <div className="flex items-center space-x-1 shrink-0">
                            {sk.content && (
                              <button
                                type="button"
                                onClick={() =>
                                  setPreviewSkillId(isPreviewing ? null : sk.id)
                                }
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

                        {isPreviewing && sk.content && (
                          <pre className="p-3 rounded-lg bg-surface-card border border-border text-[11px] font-mono text-muted-foreground overflow-x-auto whitespace-pre-wrap max-h-48">
                            {sk.content}
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
