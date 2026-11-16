'use client';

import React, { useState } from 'react';
import {
  Bot,
  Plus,
  Search,
  Sparkles,
  Layers,
  Wrench,
  Boxes,
  Play,
  MoreVertical,
  CheckCircle2,
  Clock,
  Trash2,
  Edit,
  ExternalLink,
  Shield,
  Zap,
} from 'lucide-react';
import { Workspace } from '@/types';
import { useLanguage } from '@/context/LanguageContext';

export interface AgentItem {
  id: string;
  name: string;
  description: string;
  base_model: string;
  ownership: 'official' | 'custom';
  status: 'active' | 'draft';
  skills_count: number;
  tools_count: number;
  created_at: string;
  updated_at: string;
}

interface AgentsViewProps {
  workspace: Workspace | undefined;
  onLaunchSandboxChat?: (agent: AgentItem) => void;
}

// 预设默认专家列表（对齐截图中的架构体系）
const INITIAL_AGENTS: AgentItem[] = [
  {
    id: 'agent_plugin_builder',
    name: 'Plugin Builder',
    description: '负责插件生成与 MCP 适配的构建专家，自带工具链与规范检测。',
    base_model: '4 Argon',
    ownership: 'official',
    status: 'active',
    skills_count: 5,
    tools_count: 8,
    created_at: '2026-10-14',
    updated_at: '2026-11-16',
  },
  {
    id: 'agent_deep_researcher',
    name: '深度研究专家 (Deep Researcher)',
    description: '结合多维度一手文档与权威检索的调研专家，支持沉淀研报。',
    base_model: '4 Argon',
    ownership: 'official',
    status: 'active',
    skills_count: 3,
    tools_count: 6,
    created_at: '2026-09-20',
    updated_at: '2026-11-15',
  },
  {
    id: 'agent_ocr_vision_analyst',
    name: 'OCR 与视觉分析助手',
    description: '自动解析复杂文档、公式与版面结构并执行精准识别。',
    base_model: '4 Argon',
    ownership: 'custom',
    status: 'active',
    skills_count: 2,
    tools_count: 4,
    created_at: '2026-11-10',
    updated_at: '2026-11-16',
  },
  {
    id: 'agent_code_architect',
    name: '代码重构与架构导师',
    description: '根据软件设计哲学进行深模块划分与双视角代码审查。',
    base_model: '4 Argon',
    ownership: 'custom',
    status: 'active',
    skills_count: 4,
    tools_count: 12,
    created_at: '2026-10-28',
    updated_at: '2026-11-12',
  },
];

export function AgentsView({ workspace, onLaunchSandboxChat }: AgentsViewProps) {
  const { t } = useLanguage();
  const [agents, setAgents] = useState<AgentItem[]>(INITIAL_AGENTS);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterOwnership, setFilterOwnership] = useState<'all' | 'official' | 'custom'>('all');
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  // 新建表单
  const [formName, setFormName] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formModel, setFormModel] = useState('4 Argon');

  const filteredAgents = agents.filter((agent) => {
    const matchesSearch =
      agent.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      agent.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      agent.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesFilter = filterOwnership === 'all' || agent.ownership === filterOwnership;
    return matchesSearch && matchesFilter;
  });

  const handleCreateAgent = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim()) return;

    const newAgent: AgentItem = {
      id: `agent_${Date.now().toString(36)}`,
      name: formName.trim(),
      description: formDesc.trim() || '自定义业务专家，搭载专属技能与沙盒配置。',
      base_model: formModel,
      ownership: 'custom',
      status: 'active',
      skills_count: 1,
      tools_count: 4,
      created_at: new Date().toISOString().split('T')[0],
      updated_at: new Date().toISOString().split('T')[0],
    };

    setAgents([newAgent, ...agents]);
    setIsCreateOpen(false);
    setFormName('');
    setFormDesc('');
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-surface-base overflow-hidden">
      {/* 头部标题与操作区 */}
      <div className="p-6 border-b border-border flex items-center justify-between">
        <div>
          <div className="flex items-center space-x-2.5">
            <div className="p-2 rounded-xl bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
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
                自定义装配业务专家，绑定原子技能与嵌套 SOP，并在独立沙盒中启动交互。
              </p>
            </div>
          </div>
        </div>

        <button
          onClick={() => setIsCreateOpen(true)}
          className="flex items-center space-x-1.5 px-3.5 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-white text-xs font-semibold shadow-md transition-all active:scale-95"
        >
          <Plus className="w-4 h-4" />
          <span>创建智能体</span>
        </button>
      </div>

      {/* 搜索与过滤工具栏 */}
      <div className="px-6 py-3 border-b border-border bg-surface-100/50 flex items-center justify-between">
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
            全部
          </button>
          <button
            onClick={() => setFilterOwnership('official')}
            className={`px-3 py-1 rounded-md transition-colors ${
              filterOwnership === 'official'
                ? 'bg-surface-base text-foreground font-semibold shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            官方
          </button>
          <button
            onClick={() => setFilterOwnership('custom')}
            className={`px-3 py-1 rounded-md transition-colors ${
              filterOwnership === 'custom'
                ? 'bg-surface-base text-foreground font-semibold shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            个人自定义
          </button>
        </div>
      </div>

      {/* 智能体卡片与表格列表 */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-2 gap-4">
          {filteredAgents.map((agent) => (
            <div
              key={agent.id}
              className="p-4 rounded-2xl bg-surface-100/70 border border-border hover:border-emerald-500/40 hover:bg-surface-100 transition-all flex flex-col justify-between group shadow-sm hover:shadow"
            >
              <div>
                <div className="flex items-start justify-between">
                  <div className="flex items-center space-x-2.5">
                    <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-500 shrink-0">
                      <Bot className="w-5 h-5" />
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

                  <span className="flex items-center space-x-1 text-[11px] text-emerald-500 font-medium">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    <span>可用</span>
                  </span>
                </div>

                <p className="text-xs text-muted-foreground/90 mt-3 line-clamp-2">
                  {agent.description}
                </p>

                {/* 挂载元数据徽标 */}
                <div className="flex flex-wrap gap-2 mt-4 text-[11px]">
                  <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-surface-200 border border-border text-muted-foreground">
                    <Zap className="w-3 h-3 text-amber-400" />
                    <span>基座: {agent.base_model}</span>
                  </span>
                  <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-surface-200 border border-border text-muted-foreground">
                    <Layers className="w-3 h-3 text-sky-400" />
                    <span>技能: {agent.skills_count} 个</span>
                  </span>
                  <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-surface-200 border border-border text-muted-foreground">
                    <Wrench className="w-3 h-3 text-indigo-400" />
                    <span>工具: {agent.tools_count} 项</span>
                  </span>
                </div>
              </div>

              {/* 卡片底部操作栏 */}
              <div className="mt-5 pt-3 border-t border-border/60 flex items-center justify-between text-xs text-muted-foreground">
                <span className="font-mono text-[10px]">更新于: {agent.updated_at}</span>
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => onLaunchSandboxChat?.(agent)}
                    className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/30 text-emerald-600 dark:text-emerald-400 font-semibold transition-colors"
                    title="在独立沙盒中启动该专家的交互会话"
                  >
                    <Boxes className="w-3.5 h-3.5" />
                    <span>启动沙盒</span>
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 创建专家模态抽屉 */}
      {isCreateOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
          <div className="bg-surface-modal border border-border w-full max-w-lg rounded-2xl p-6 shadow-2xl space-y-4 animate-scale-in">
            <div className="flex items-center justify-between pb-3 border-b border-border">
              <div className="flex items-center space-x-2.5">
                <Bot className="w-5 h-5 text-emerald-500" />
                <h3 className="text-base font-bold text-foreground">创建新智能体专家</h3>
              </div>
              <button
                onClick={() => setIsCreateOpen(false)}
                className="p-1.5 rounded-lg hover:bg-surface-100 text-muted-foreground hover:text-foreground"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateAgent} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-foreground mb-1">专家名称</label>
                <input
                  type="text"
                  required
                  placeholder="例如：财报研报专家、代码审查管家..."
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-surface-100 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-foreground mb-1">
                  职能描述 & SOP 说明
                </label>
                <textarea
                  rows={3}
                  placeholder="清晰描述专家的主要职责、擅长领域与交互标准..."
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-surface-100 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-emerald-500 resize-none"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-foreground mb-1">默认基座模型</label>
                <select
                  value={formModel}
                  onChange={(e) => setFormModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-surface-100 border border-border text-xs text-foreground focus:outline-none focus:border-emerald-500"
                >
                  <option value="4 Argon">4 Argon (主力高智力模型)</option>
                  <option value="gemini-2.0-flash">Gemini 2.0 Flash (极速并发模型)</option>
                  <option value="nvidia/nemotron-3.5">NVIDIA Nemotron 3.5</option>
                </select>
              </div>

              <div className="p-3 rounded-xl bg-surface-100/80 border border-border text-[11px] text-muted-foreground space-y-1">
                <div className="font-semibold text-foreground flex items-center space-x-1">
                  <Shield className="w-3.5 h-3.5 text-emerald-500" />
                  <span>自动创建隔离沙盒说明</span>
                </div>
                <p>保存后，该专家将自动具备专属工作区路径与 `.agenthub/skills/` 挂载环境，随时可派生独立交互沙盒。</p>
              </div>

              <div className="flex items-center justify-end space-x-2 pt-3 border-t border-border">
                <button
                  type="button"
                  onClick={() => setIsCreateOpen(false)}
                  className="px-3 py-1.5 rounded-lg border border-border hover:bg-surface-100 text-xs text-muted-foreground"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-4 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white text-xs font-semibold shadow"
                >
                  确认创建
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
