'use client';

import React, { useState, useEffect } from 'react';
import {
  Bot,
  Plus,
  Search,
  Sparkles,
  Layers,
  Wrench,
  Boxes,
  Zap,
  X,
  Check,
  CheckSquare,
  Square,
  Trash2,
  ExternalLink,
  Shield,
  BookOpen,
  Filter,
  Pencil,
  Lock,
} from 'lucide-react';
import { Workspace, SkillItem } from '@/types';
import { ModelOption } from '@/components/ModelSelector';
import {
  fetchWorkspaceSkills,
  getCachedWorkspaceSkills,
  subscribeSkillsUpdated,
} from '@/utils/skillCache';

export interface AgentItem {
  id: string;
  name: string;
  description: string;
  base_model: string;
  context_prompt?: string;
  skill_ids: string[];
  tools_count: number;
  ownership: 'official' | 'custom';
  status: 'active' | 'draft';
  created_at: string;
  updated_at: string;
}

interface AgentsViewProps {
  workspace: Workspace | undefined;
  availableModels?: ModelOption[];
  currentModel?: string;
  onLaunchSandboxChat?: (agent: AgentItem, selectedSkills: SkillItem[]) => void;
}

// 预设默认官方专家（挂载真实核心系统技能）
const DEFAULT_OFFICIAL_AGENTS: AgentItem[] = [
  {
    id: 'agent_code_architect',
    name: '代码重构与架构导师',
    description: '基于《软件设计的哲学》进行深模块划分与双视角代码审查，结合 TDD 保证高质量交付。',
    base_model: 'gemini-2.5-flash',
    context_prompt: '你是代码重构与架构导师，基于《软件设计的哲学》指导深模块设计、消除浅模块复杂度，并严格执行红-绿-重构 TDD 与双视角代码审查。',
    skill_ids: ['codebase-design', 'code-review', 'tdd', 'diagnosing-bugs'],
    tools_count: 8,
    ownership: 'official',
    status: 'active',
    created_at: '2026-10-28',
    updated_at: '2026-11-16',
  },
  {
    id: 'agent_deep_researcher',
    name: '深度研究专家 (Deep Researcher)',
    description: '结合多维度全网一手资料与官方权威文档进行深度调研，沉淀结构化技术与行业研报。',
    base_model: 'gemini-2.5-flash',
    context_prompt: '你是深度研究专家 (Deep Researcher)，专注于多维度全网权威资料调研与结构化研究报告沉淀。所有论断必须基于一手信源与真实证据。',
    skill_ids: ['research', 'generative_ui', 'slides'],
    tools_count: 6,
    ownership: 'official',
    status: 'active',
    created_at: '2026-09-20',
    updated_at: '2026-11-16',
  },
  {
    id: 'agent_ui_ux_master',
    name: 'UI/UX 前端体验设计师',
    description: '专注于响应式高质感界面打造、设计系统规范维护与 Radix/Tailwind 样式调优。',
    base_model: 'gemini-2.5-flash',
    context_prompt: '你是 UI/UX 前端体验设计专家，专注于构建高质感现代 Web 界面、Tailwind 样式调优与组件设计规范。',
    skill_ids: ['ui-ux-pro-max', 'ui-styling', 'design-system'],
    tools_count: 5,
    ownership: 'official',
    status: 'active',
    created_at: '2026-11-01',
    updated_at: '2026-11-16',
  },
  {
    id: 'agent_plugin_builder',
    name: 'Plugin & MCP 构建专家',
    description: '负责标准化 MCP 协议服务端构建、插件脚手架生成与自动化规范坏味道排查。',
    base_model: 'gemini-2.5-flash',
    context_prompt: '你是 Plugin & MCP 构建专家，负责标准化 MCP 协议服务端开发、自定义 Skill 打包与工具测试。',
    skill_ids: ['plugin', 'generative_ui'],
    tools_count: 7,
    ownership: 'official',
    status: 'active',
    created_at: '2026-10-14',
    updated_at: '2026-11-16',
  },
];

const LOCAL_STORAGE_KEY = 'agenthub_custom_agents_v1';

export function AgentsView({
  workspace,
  availableModels = [],
  currentModel = '',
  onLaunchSandboxChat,
}: AgentsViewProps) {
  // 真实系统技能池
  const [allSkills, setAllSkills] = useState<SkillItem[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);

  // 专家列表
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterOwnership, setFilterOwnership] = useState<'all' | 'official' | 'custom'>('all');

  // 创建/修改专家弹窗与状态
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<AgentItem | null>(null);
  const [formName, setFormName] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formModel, setFormModel] = useState('');
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [isSkillPickerOpen, setIsSkillPickerOpen] = useState(false);
  const [skillSearchQuery, setSkillSearchQuery] = useState('');

  // 初始化拉取系统真实技能
  useEffect(() => {
    let isMounted = true;
    setSkillsLoading(true);
    fetchWorkspaceSkills(workspace?.id)
      .then((skills) => {
        if (isMounted) {
          setAllSkills(skills || []);
        }
      })
      .catch((e) => console.warn('拉取系统技能失败:', e))
      .finally(() => {
        if (isMounted) setSkillsLoading(false);
      });
    const unsubscribe = subscribeSkillsUpdated(() => {
      if (!isMounted) return;
      const latest = getCachedWorkspaceSkills(workspace?.id);
      if (latest) {
        setAllSkills(latest);
      } else {
        fetchWorkspaceSkills(workspace?.id, { force: true, refreshBackend: true })
          .then((skills) => {
            if (isMounted) setAllSkills(skills || []);
          })
          .catch(() => {});
      }
    });

    return () => {
      isMounted = false;
      unsubscribe();
    };
  }, [workspace?.id]);

  // 从本地持久化存储加载专家
  useEffect(() => {
    const effectiveModel = currentModel || (availableModels[0]?.id ?? 'gemini-2.5-flash');
    const initedOfficial = DEFAULT_OFFICIAL_AGENTS.map((a) => ({
      ...a,
      base_model: effectiveModel,
    }));

    try {
      const saved = localStorage.getItem(LOCAL_STORAGE_KEY);
      if (saved) {
        const parsed: AgentItem[] = JSON.parse(saved);
        // 分离个人自定义智能体和官方智能体，保证官方智能体始终不可被外部篡改
        const customAgents = parsed.filter((a) => a.ownership === 'custom');
        setAgents([...customAgents, ...initedOfficial]);
        return;
      }
    } catch { }

    setAgents(initedOfficial);
  }, [currentModel, availableModels]);

  // 当打开创建模态框时，同步默认选中的真实模型
  const handleOpenCreateModal = () => {
    const defaultModelId = currentModel || (availableModels[0]?.id ?? 'gemini-2.5-flash');
    setEditingAgent(null);
    setFormName('');
    setFormDesc('');
    setFormModel(defaultModelId);
    setSelectedSkillIds([]);
    setIsSkillPickerOpen(false);
    setSkillSearchQuery('');
    setIsModalOpen(true);
  };

  // 打开修改个人专家模态框
  const handleOpenEditModal = (agent: AgentItem, e: React.MouseEvent) => {
    e.stopPropagation();
    // 官方智能体受系统保护，绝不允许修改
    if (agent.ownership === 'official') return;

    setEditingAgent(agent);
    setFormName(agent.name);
    setFormDesc(agent.context_prompt || agent.description || '');
    setFormModel(agent.base_model || (availableModels[0]?.id ?? 'gemini-2.5-flash'));
    setSelectedSkillIds([...(agent.skill_ids || [])]);
    setIsSkillPickerOpen(false);
    setSkillSearchQuery('');
    setIsModalOpen(true);
  };

  const handleToggleSkill = (skillId: string) => {
    setSelectedSkillIds((prev) =>
      prev.includes(skillId) ? prev.filter((id) => id !== skillId) : [...prev, skillId]
    );
  };

  // 保存专家（创建或修改）
  const handleSaveAgent = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim()) return;

    const todayStr = new Date().toISOString().split('T')[0];
    const targetModel = formModel || (availableModels[0]?.id ?? 'gemini-2.5-flash');
    const targetPrompt = formDesc.trim();

    if (editingAgent) {
      // 严格安全防卫：禁止修改官方智能体
      if (editingAgent.ownership === 'official') return;

      const nextList = agents.map((a) => {
        if (a.id === editingAgent.id) {
          return {
            ...a,
            name: formName.trim(),
            description: formDesc.trim() || '自定义专业智能体，搭载专属技能集与独立沙盒运行环境。',
            base_model: targetModel,
            context_prompt: targetPrompt || undefined,
            skill_ids: selectedSkillIds,
            tools_count: Math.max(2, selectedSkillIds.length * 2),
            updated_at: todayStr,
          };
        }
        return a;
      });

      setAgents(nextList);
      try {
        const customs = nextList.filter((a) => a.ownership === 'custom');
        localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(customs));
      } catch { }
    } else {
      // 创建全新个人智能体
      const newAgent: AgentItem = {
        id: `agent_${Date.now().toString(36)}`,
        name: formName.trim(),
        description: formDesc.trim() || '自定义专业智能体，搭载专属技能集与独立沙盒运行环境。',
        base_model: targetModel,
        context_prompt: targetPrompt || undefined,
        skill_ids: selectedSkillIds,
        tools_count: Math.max(2, selectedSkillIds.length * 2),
        ownership: 'custom',
        status: 'active',
        created_at: todayStr,
        updated_at: todayStr,
      };

      const nextList = [newAgent, ...agents];
      setAgents(nextList);
      try {
        const customs = nextList.filter((a) => a.ownership === 'custom');
        localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(customs));
      } catch { }
    }

    setIsModalOpen(false);
    setEditingAgent(null);
  };

  // 删除个人专家
  const handleDeleteAgent = (agent: AgentItem, e: React.MouseEvent) => {
    e.stopPropagation();
    // 严格安全防卫：官方专家不可删除
    if (agent.ownership === 'official') return;

    const confirmed = window.confirm(
      `确定要删除个人专家【${agent.name}】吗？\n删除后不可恢复。`
    );
    if (!confirmed) return;

    const nextList = agents.filter((a) => a.id !== agent.id);
    setAgents(nextList);
    try {
      const customs = nextList.filter((a) => a.ownership === 'custom');
      localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(customs));
    } catch { }
  };

  const filteredAgents = agents.filter((agent) => {
    const matchesSearch =
      agent.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      agent.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      agent.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesFilter = filterOwnership === 'all' || agent.ownership === filterOwnership;
    return matchesSearch && matchesFilter;
  });

  const filteredSkillsForPicker = allSkills.filter((s) => {
    const q = skillSearchQuery.toLowerCase().trim();
    if (!q) return true;
    return (
      s.id.toLowerCase().includes(q) ||
      (s.name || '').toLowerCase().includes(q) ||
      (s.description || '').toLowerCase().includes(q)
    );
  });

  // 获取技能详细信息映射表
  const skillMap = new Map<string, SkillItem>();
  allSkills.forEach((s) => skillMap.set(s.id, s));

  return (
    <div className="flex-1 flex flex-col h-full bg-surface-base overflow-hidden">
      {/* 头部标题与操作区 */}
      <div className="p-6 border-b border-border flex items-center justify-between shrink-0 bg-surface-base">
        <div>
          <div className="flex items-center space-x-2.5">
            <div className="p-2.5 rounded-xl bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
              <Bot className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-foreground flex items-center space-x-2">
                <span>智能体 (专家中心)</span>
                <span className="px-2 py-0.5 rounded-full text-[11px] bg-emerald-500/10 text-emerald-500 font-mono">
                  {agents.length}
                </span>
              </h1>
              <p className="text-xs text-muted-foreground mt-0.5">
                装配真实基座模型与真实技能集，官方专家受系统保护，个人专家支持自由修改、删除与沙盒化交互。
              </p>
            </div>
          </div>
        </div>

        <button
          onClick={handleOpenCreateModal}
          className="flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-white text-xs font-semibold shadow-md transition-all active:scale-95"
        >
          <Plus className="w-4 h-4" />
          <span>创建智能体</span>
        </button>
      </div>

      {/* 搜索与过滤工具栏 */}
      <div className="px-6 py-3 border-b border-border bg-surface-100/40 flex items-center justify-between shrink-0">
        <div className="relative w-80">
          <Search className="w-3.5 h-3.5 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="搜索名称或精确智能体 ID..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-surface-200 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-emerald-500 transition-colors"
          />
        </div>

        <div className="flex items-center space-x-1 p-1 rounded-lg bg-surface-200 border border-border text-xs">
          <button
            onClick={() => setFilterOwnership('all')}
            className={`px-3 py-1 rounded-md transition-colors ${
              filterOwnership === 'all'
                ? 'bg-surface-base text-foreground font-semibold shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            全部 ({agents.length})
          </button>
          <button
            onClick={() => setFilterOwnership('official')}
            className={`px-3 py-1 rounded-md transition-colors ${
              filterOwnership === 'official'
                ? 'bg-surface-base text-foreground font-semibold shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            官方 ({agents.filter((a) => a.ownership === 'official').length})
          </button>
          <button
            onClick={() => setFilterOwnership('custom')}
            className={`px-3 py-1 rounded-md transition-colors ${
              filterOwnership === 'custom'
                ? 'bg-surface-base text-foreground font-semibold shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            个人自定义 ({agents.filter((a) => a.ownership === 'custom').length})
          </button>
        </div>
      </div>

      {/* 智能体卡片与表格列表 */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-2 gap-4">
          {filteredAgents.map((agent) => {
            const boundSkills = (agent.skill_ids || [])
              .map((id) => skillMap.get(id) || { id, name: id, scope: 'global' as const })
              .filter(Boolean);

            return (
              <div
                key={agent.id}
                className="p-5 rounded-2xl bg-surface-100/70 border border-border hover:border-emerald-500/40 hover:bg-surface-100 transition-all flex flex-col justify-between group shadow-sm hover:shadow-md"
              >
                <div>
                  <div className="flex items-start justify-between">
                    <div className="flex items-center space-x-3">
                      <div className="w-11 h-11 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-500 shrink-0">
                        <Bot className="w-6 h-6" />
                      </div>
                      <div>
                        <div className="flex items-center space-x-2">
                          <span className="font-bold text-sm text-foreground">{agent.name}</span>
                          <span
                            className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                              agent.ownership === 'official'
                                ? 'bg-sky-500/10 text-sky-400 border border-sky-500/20'
                                : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                            }`}
                          >
                            {agent.ownership === 'official' ? '官方' : '个人'}
                          </span>
                        </div>
                        <span className="text-[11px] font-mono text-muted-foreground">{agent.id}</span>
                      </div>
                    </div>

                    {/* 操作控制区：官方只读锁定，个人可修改、可删除 */}
                    <div className="flex items-center space-x-1.5">
                      {agent.ownership === 'official' ? (
                        <div
                          className="flex items-center space-x-1 px-2 py-0.5 rounded-md bg-surface-200/70 border border-border/60 text-muted-foreground text-[10px]"
                          title="官方内置专家受系统保护，不可修改或删除"
                        >
                          <Lock className="w-3 h-3 text-muted-foreground/80" />
                          <span>官方保护</span>
                        </div>
                      ) : (
                        <div className="flex items-center space-x-1">
                          <button
                            onClick={(e) => handleOpenEditModal(agent, e)}
                            className="flex items-center space-x-1 px-2 py-1 rounded-lg text-muted-foreground hover:text-emerald-500 hover:bg-emerald-500/10 transition-colors text-xs font-medium"
                            title="修改/配置此智能体技能与模型"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                            <span className="text-[11px]">修改</span>
                          </button>
                          <button
                            onClick={(e) => handleDeleteAgent(agent, e)}
                            className="flex items-center space-x-1 px-2 py-1 rounded-lg text-muted-foreground hover:text-rose-500 hover:bg-rose-500/10 transition-colors text-xs font-medium"
                            title="删除此个人智能体"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                            <span className="text-[11px]">删除</span>
                          </button>
                        </div>
                      )}
                    </div>
                  </div>

                  <p className="text-xs text-muted-foreground/90 mt-3 leading-relaxed line-clamp-2">
                    {agent.description}
                  </p>

                  {/* 真实属性徽标：真实基座模型 + 真实挂载技能列表 */}
                  <div className="space-y-2 mt-4">
                    <div className="flex items-center space-x-1.5 text-[11px]">
                      <Zap className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                      <span className="text-muted-foreground">基座模型:</span>
                      <span className="font-mono font-medium text-foreground bg-surface-200/80 px-2 py-0.5 rounded border border-border/60">
                        {agent.base_model}
                      </span>
                    </div>

                    <div className="text-[11px] space-y-1">
                      <div className="flex items-center space-x-1.5 text-muted-foreground">
                        <Layers className="w-3.5 h-3.5 text-sky-400 shrink-0" />
                        <span>挂载技能 ({boundSkills.length} 项):</span>
                      </div>
                      <div className="flex flex-wrap gap-1.5 pt-0.5">
                        {boundSkills.length > 0 ? (
                          boundSkills.map((sk) => (
                            <span
                              key={sk.id}
                              className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-sky-500/10 text-sky-400 border border-sky-500/20 text-[10px] font-mono"
                              title={sk.description || sk.id}
                            >
                              <span>{sk.name || sk.id}</span>
                            </span>
                          ))
                        ) : (
                          <span className="text-[10px] text-muted-foreground italic">未挂载特定技能 (通用)</span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                {/* 卡片底部操作栏 */}
                <div className="mt-5 pt-3 border-t border-border/60 flex items-center justify-between text-xs text-muted-foreground">
                  <span className="font-mono text-[10px]">更新时间: {agent.updated_at}</span>
                  <div className="flex items-center space-x-2">
                    {agent.ownership === 'custom' && (
                      <button
                        onClick={(e) => handleOpenEditModal(agent, e)}
                        className="px-2.5 py-1 rounded-lg border border-border/80 hover:bg-surface-200 text-foreground text-xs font-medium transition-colors"
                      >
                        编辑配置
                      </button>
                    )}
                    <button
                      onClick={() => {
                        const skills = (agent.skill_ids || [])
                          .map((id) => skillMap.get(id))
                          .filter((s): s is SkillItem => Boolean(s));
                        onLaunchSandboxChat?.(agent, skills);
                      }}
                      className="flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/35 text-emerald-600 dark:text-emerald-400 font-semibold transition-all shadow-sm active:scale-95"
                      title="在独立沙盒中启动该专家的交互会话"
                    >
                      <Boxes className="w-3.5 h-3.5" />
                      <span>启动沙盒</span>
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 创建 / 修改专家模态抽屉 */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
          <div className="bg-surface-modal border border-border w-full max-w-xl max-h-[90vh] rounded-2xl p-6 shadow-2xl flex flex-col animate-scale-in text-foreground">
            <div className="flex items-center justify-between pb-3 border-b border-border shrink-0">
              <div className="flex items-center space-x-2.5">
                <div className="p-2 rounded-xl bg-emerald-500/10 text-emerald-500">
                  <Bot className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-foreground">
                    {editingAgent ? '修改智能体专家' : '创建新智能体专家'}
                  </h3>
                  <p className="text-[11px] text-muted-foreground">
                    {editingAgent
                      ? `正在编辑个人专家【${editingAgent.name}】，调整技能装配与参数后将直接同步沙盒。`
                      : '装配真实可用的模型与技能，系统将自动分配独立沙盒工作区。'}
                  </p>
                </div>
              </div>
              <button
                onClick={() => {
                  setIsModalOpen(false);
                  setEditingAgent(null);
                }}
                className="p-1.5 rounded-lg hover:bg-surface-100 text-muted-foreground hover:text-foreground"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSaveAgent} className="flex-1 overflow-y-auto py-4 space-y-4 pr-1">
              <div>
                <label className="block text-xs font-semibold text-foreground mb-1">
                  专家名称 <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  required
                  placeholder="例如：财报研报专家、代码审查管家、自动测试大师..."
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl bg-surface-100 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-foreground mb-1">
                  职能描述 & SOP 指导原则
                </label>
                <textarea
                  rows={2}
                  placeholder="清晰描述专家的主要职责、擅长领域、行为准则与交付标准..."
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl bg-surface-100 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-emerald-500 resize-none"
                />
              </div>

              {/* 真实基座模型选择器 */}
              <div>
                <label className="block text-xs font-semibold text-foreground mb-1 flex items-center justify-between">
                  <span>默认基座模型 (从系统已激活账号池真实加载)</span>
                  <span className="text-[10px] text-muted-foreground font-mono">
                    共 {availableModels.length} 个可用模型
                  </span>
                </label>
                <select
                  value={formModel}
                  onChange={(e) => setFormModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl bg-surface-100 border border-border text-xs text-foreground focus:outline-none focus:border-emerald-500 font-mono"
                >
                  {availableModels.length > 0 ? (
                    availableModels.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name || m.id} ({m.provider || 'Ready'}{m.badge ? ` · ${m.badge}` : ''})
                      </option>
                    ))
                  ) : (
                    <option value={currentModel || 'gemini-2.5-flash'}>
                      {currentModel || 'gemini-2.5-flash'} (当前默认)
                    </option>
                  )}
                </select>
              </div>

              {/* 真实技能装配版块 */}
              <div className="space-y-2 pt-1">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-semibold text-foreground flex items-center space-x-1.5">
                    <Layers className="w-3.5 h-3.5 text-sky-400" />
                    <span>装配技能集 (Skills · 多选)</span>
                    <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-sky-500/15 text-sky-400 font-mono font-medium">
                      已选 {selectedSkillIds.length} 项
                    </span>
                  </label>
                  <button
                    type="button"
                    onClick={() => setIsSkillPickerOpen((prev) => !prev)}
                    className="text-xs font-semibold text-emerald-500 hover:text-emerald-400 flex items-center space-x-1"
                  >
                    <span>{isSkillPickerOpen ? '收起技能库' : '+ 添加/调整技能'}</span>
                  </button>
                </div>

                {/* 已选技能标签横条 */}
                <div className="p-2.5 rounded-xl bg-surface-100/80 border border-border min-h-[46px] flex flex-wrap gap-1.5 items-center">
                  {selectedSkillIds.length > 0 ? (
                    selectedSkillIds.map((id) => {
                      const sk = skillMap.get(id);
                      return (
                        <span
                          key={id}
                          className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-sky-500/15 border border-sky-500/30 text-sky-400 text-xs font-mono group"
                        >
                          <span>{sk?.name || id}</span>
                          <button
                            type="button"
                            onClick={() => handleToggleSkill(id)}
                            className="hover:text-rose-400 transition-colors ml-0.5"
                            title="移除此技能"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </span>
                      );
                    })
                  ) : (
                    <span className="text-xs text-muted-foreground italic px-1">
                      尚未挂载技能。点击右上方「+ 添加/调整技能」可从系统技能库多选装配。
                    </span>
                  )}
                </div>

                {/* 技能勾选选择器抽屉 */}
                {isSkillPickerOpen && (
                  <div className="p-3 rounded-xl bg-surface-100 border border-border/80 space-y-2.5 animate-fadeIn">
                    <div className="relative">
                      <Search className="w-3.5 h-3.5 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
                      <input
                        type="text"
                        placeholder="搜索技能名称或职能..."
                        value={skillSearchQuery}
                        onChange={(e) => setSkillSearchQuery(e.target.value)}
                        className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-surface-200 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-emerald-500"
                      />
                    </div>

                    <div className="max-h-48 overflow-y-auto space-y-1 pr-1">
                      {skillsLoading ? (
                        <div className="py-6 text-center text-xs text-muted-foreground">正在加载系统技能库...</div>
                      ) : filteredSkillsForPicker.length > 0 ? (
                        filteredSkillsForPicker.map((sk) => {
                          const isSelected = selectedSkillIds.includes(sk.id);
                          return (
                            <div
                              key={sk.id}
                              onClick={() => handleToggleSkill(sk.id)}
                              className={`p-2.5 rounded-lg border cursor-pointer transition-all flex items-center justify-between text-xs ${
                                isSelected
                                  ? 'bg-sky-500/15 border-sky-500/40 text-foreground font-medium'
                                  : 'bg-surface-200/50 hover:bg-surface-200 border-border/60 text-muted-foreground hover:text-foreground'
                              }`}
                            >
                              <div className="flex items-center space-x-2.5 min-w-0 flex-1 pr-2">
                                {isSelected ? (
                                  <CheckSquare className="w-4 h-4 text-sky-400 shrink-0" />
                                ) : (
                                  <Square className="w-4 h-4 text-muted-foreground shrink-0" />
                                )}
                                <div className="min-w-0">
                                  <div className="flex items-center space-x-1.5">
                                    <span className="font-semibold text-foreground truncate">{sk.name || sk.id}</span>
                                    {sk.command && (
                                      <span className="text-[10px] font-mono px-1 rounded bg-surface-100 text-sky-400">
                                        {sk.command}
                                      </span>
                                    )}
                                  </div>
                                  <div className="text-[11px] text-muted-foreground truncate mt-0.5">
                                    {sk.description || '标准执行 SOP 技能包'}
                                  </div>
                                </div>
                              </div>

                              <span
                                className={`text-[10px] px-1.5 py-0.5 rounded font-mono shrink-0 ${
                                  sk.scope === 'workspace' ? 'bg-amber-500/10 text-amber-400' : 'bg-surface-100 text-muted-foreground'
                                }`}
                              >
                                {sk.scope === 'workspace' ? '工作区' : '系统'}
                              </span>
                            </div>
                          );
                        })
                      ) : (
                        <div className="py-4 text-center text-xs text-muted-foreground">
                          未搜索到匹配的技能
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>

              <div className="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-[11px] text-muted-foreground space-y-1">
                <div className="font-semibold text-emerald-600 dark:text-emerald-400 flex items-center space-x-1">
                  <Shield className="w-3.5 h-3.5" />
                  <span>自动创建隔离沙盒说明</span>
                </div>
                <p>保存后，该专家将自动建立专属工作区路径；所勾选的技能 SOP 与真实模型将在启动沙盒时一次性注入会话。</p>
              </div>

              <div className="flex items-center justify-end space-x-2 pt-3 border-t border-border shrink-0">
                <button
                  type="button"
                  onClick={() => {
                    setIsModalOpen(false);
                    setEditingAgent(null);
                  }}
                  className="px-4 py-2 rounded-xl border border-border hover:bg-surface-100 text-xs text-muted-foreground"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-white text-xs font-semibold shadow-md active:scale-95 transition-all"
                >
                  {editingAgent ? '保存修改' : '确认创建'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}