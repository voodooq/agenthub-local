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
  FolderLock,
  Clock,
  Loader2,
  Sliders,
  ChevronDown,
  ChevronUp,
  FileCheck,
  Target,
  Compass,
} from 'lucide-react';
import { Workspace, SkillItem, AgentItem, GroundingConfig } from '@/types';
import { SkillCategoryTabs, ALL_CATEGORY, filterSkillsByCategory } from './SkillCategoryTabs';
import { ExpertKnowledgeAndApiPanel, BoundKBItem } from './ExpertKnowledgeAndApiPanel';
import { ModelOption } from '@/components/ModelSelector';
import { apiFetch } from '@/utils/apiClient';
import {
  fetchWorkspaceSkills,
  getCachedWorkspaceSkills,
  subscribeSkillsUpdated,
} from '@/utils/skillCache';

export type { AgentItem, GroundingConfig } from '@/types';

interface AgentsViewProps {
  workspace: Workspace | undefined;
  availableModels?: ModelOption[];
  currentModel?: string;
  onLaunchSandboxChat?: (agent: AgentItem, selectedSkills: SkillItem[]) => void;
}

// 预设默认官方专家（挂载真实核心系统技能与防幻觉配置）
const DEFAULT_OFFICIAL_AGENTS: AgentItem[] = [
  {
    id: 'agent_code_architect',
    name: '代码重构与架构导师',
    description: '基于《软件设计的哲学》进行深模块划分与双视角代码审查，结合 TDD 保证高质量交付。',
    base_model: '',
    context_prompt: '你是代码重构与架构导师，基于《软件设计的哲学》指导深模块设计、消除浅模块复杂度，并严格执行红-绿-重构 TDD 与双视角代码审查。',
    skill_ids: ['codebase-design', 'code-review', 'tdd', 'diagnosing-bugs'],
    tools_count: 8,
    ownership: 'official',
    status: 'active',
    grounding_config: {
      strict_mode: true,
      temperature: 0.1,
      fail_fast_on_missing: true,
      verify_tool_call: true,
      require_evidence: true,
      custom_rules: '重构与修改代码前必须通过调研现有代码，严禁凭空盲写；改动后必须运行单测验证。',
    },
    created_at: '2026-10-28',
    updated_at: '2026-11-17',
  },
  {
    id: 'agent_deep_researcher',
    name: '深度研究专家 (Deep Researcher)',
    description: '结合多维度全网一手资料与官方权威文档进行深度调研，沉淀结构化技术与行业研报。',
    base_model: '',
    context_prompt: '你是深度研究专家 (Deep Researcher)，专注于多维度全网权威资料调研与结构化研究报告沉淀。所有论断必须基于一手信源与真实证据。',
    skill_ids: ['research', 'generative_ui', 'slides'],
    tools_count: 6,
    ownership: 'official',
    status: 'active',
    grounding_config: {
      strict_mode: true,
      temperature: 0.2,
      fail_fast_on_missing: true,
      verify_tool_call: true,
      require_evidence: true,
      custom_rules: '所有论断必须有一手官方文档或抓取结果为事实依据，禁止无证据猜测推论。',
    },
    created_at: '2026-09-20',
    updated_at: '2026-11-17',
  },
  {
    id: 'agent_ui_ux_master',
    name: 'UI/UX 前端体验设计师',
    description: '专注于响应式高质感界面打造、设计系统规范维护与 Radix/Tailwind 样式调优。',
    base_model: '',
    context_prompt: '你是 UI/UX 前端体验设计专家，专注于构建高质感现代 Web 界面、Tailwind 样式调优与组件设计规范。',
    skill_ids: ['ui-ux-pro-max', 'ui-styling', 'design-system'],
    tools_count: 5,
    ownership: 'official',
    status: 'active',
    grounding_config: {
      strict_mode: false,
      temperature: 0.6,
      fail_fast_on_missing: false,
      verify_tool_call: false,
      require_evidence: false,
      custom_rules: '界面与样式设计必须遵循单色扁平化与暗黑模式适配原则，杜绝刺眼无规则色彩。',
    },
    created_at: '2026-11-01',
    updated_at: '2026-11-17',
  },
  {
    id: 'agent_plugin_builder',
    name: 'Plugin & MCP 构建专家',
    description: '负责标准化 MCP 协议服务端构建、插件脚手架生成与自动化规范坏味道排查。',
    base_model: '',
    context_prompt: '你是 Plugin & MCP 构建专家，负责标准化 MCP 协议服务端开发、自定义 Skill 打包与工具测试。',
    skill_ids: ['plugin', 'generative_ui'],
    tools_count: 7,
    ownership: 'official',
    status: 'active',
    grounding_config: {
      strict_mode: true,
      temperature: 0.1,
      fail_fast_on_missing: true,
      verify_tool_call: true,
      require_evidence: true,
      custom_rules: '所有 MCP 工具定义与 Schema 必须通过 JSON Schema 严格校验，严禁输出非标准字段。',
    },
    created_at: '2026-10-14',
    updated_at: '2026-11-17',
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
  const [formAllowedTools, setFormAllowedTools] = useState('');
  const [formAllowedWriteDirs, setFormAllowedWriteDirs] = useState('');
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [isSkillPickerOpen, setIsSkillPickerOpen] = useState(false);
  const [skillSearchQuery, setSkillSearchQuery] = useState('');
  const [skillCategory, setSkillCategory] = useState(ALL_CATEGORY);
  const [savingAgent, setSavingAgent] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [nlPrompt, setNlPrompt] = useState('');
  const [nlGenerating, setNlGenerating] = useState(false);
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [scheduleCron, setScheduleCron] = useState('0 18 * * *');
  const [scheduleHuman, setScheduleHuman] = useState('');
  const [scheduleName, setScheduleName] = useState('');
  const [schedulePrompt, setSchedulePrompt] = useState('');
  const [bannerNotice, setBannerNotice] = useState<string | null>(null);

  // 防幻觉与严谨度配置表单状态
  const [formStrictMode, setFormStrictMode] = useState(true);
  const [formTemperature, setFormTemperature] = useState(0.1);
  const [formFailFast, setFormFailFast] = useState(true);
  const [formVerifyToolCall, setFormVerifyToolCall] = useState(true);
  const [formRequireEvidence, setFormRequireEvidence] = useState(true);
  const [formCustomRules, setFormCustomRules] = useState('');
  const [isGroundingPanelOpen, setIsGroundingPanelOpen] = useState(true);

  // v1.5.9 专家知识底座与对外 API 状态
  const [formKbIsolationMode, setFormKbIsolationMode] = useState<string>('strict_bound_only');
  const [formStrictGrounding, setFormStrictGrounding] = useState<boolean>(true);
  const [formMinRelevanceScore, setFormMinRelevanceScore] = useState<number>(0.42);
  const [formApiServiceEnabled, setFormApiServiceEnabled] = useState<boolean>(false);
  const [formApiModelSlug, setFormApiModelSlug] = useState<string>('');
  const [formBoundKbs, setFormBoundKbs] = useState<BoundKBItem[]>([]);

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

  // 辅助：将后端或本地专家列表规范化并回写本地缓存（仅缓存自定义专家）
  const syncCustomAgentsLocalCache = (list: AgentItem[]) => {
    try {
      const customs = list.filter((a) => a.ownership === 'custom');
      localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(customs));
    } catch { }
  };

  // 从后端 /api/v1/agents 加载专家（失败时平滑回退本地持久化）
  useEffect(() => {
    let isMounted = true;
    const initedOfficial = DEFAULT_OFFICIAL_AGENTS.map((a) => ({
      ...a,
      base_model: '',
    }));

    const loadFallback = () => {
      try {
        const saved = localStorage.getItem(LOCAL_STORAGE_KEY);
        if (saved) {
          const parsed: AgentItem[] = JSON.parse(saved);
          const customAgents = parsed.filter((a) => a.ownership === 'custom');
          if (isMounted) setAgents([...customAgents, ...initedOfficial]);
          return;
        }
      } catch { }
      if (isMounted) setAgents(initedOfficial);
    };

    apiFetch('/api/v1/agents')
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const remoteAgents: AgentItem[] = Array.isArray(data?.agents) ? data.agents : [];
        if (remoteAgents.length > 0) {
          if (isMounted) {
            setAgents(remoteAgents);
            syncCustomAgentsLocalCache(remoteAgents);
          }
        } else {
          loadFallback();
        }
      })
      .catch(() => {
        loadFallback();
      });

    return () => {
      isMounted = false;
    };
  }, [currentModel, availableModels]);

  // 当打开创建模态框时，默认跟随当前会话模型（也可按需指定）
  const handleOpenCreateModal = () => {
    setEditingAgent(null);
    setFormName('');
    setFormDesc('');
    setFormModel('');
    setFormAllowedTools('');
    setFormAllowedWriteDirs('');
    setSelectedSkillIds([]);
    setIsSkillPickerOpen(false);
    setSkillSearchQuery('');
    setApiError(null);
    setNlPrompt('');
    setScheduleEnabled(false);
    setScheduleCron('0 18 * * *');
    setScheduleHuman('');
    setScheduleName('');
    setSchedulePrompt('');
    setFormStrictMode(true);
    setFormTemperature(0.1);
    setFormFailFast(true);
    setFormVerifyToolCall(true);
    setFormRequireEvidence(true);
    setFormCustomRules('');
    setFormKbIsolationMode('strict_bound_only');
    setFormStrictGrounding(true);
    setFormMinRelevanceScore(0.42);
    setFormApiServiceEnabled(false);
    setFormApiModelSlug('');
    setFormBoundKbs([]);
    setIsGroundingPanelOpen(true);
    setIsModalOpen(true);
  };

  // 幂等注入防幻觉与事实基准规范模版
  const handleInjectAntiHallucinationTemplate = () => {
    const current = formDesc.trim();
    if (
      current.includes('【事实唯一基准】') ||
      current.includes('【缺失如实拒答') ||
      current.includes('防幻觉与事实基准')
    ) {
      setBannerNotice('当前提示词已包含防幻觉与事实基准规约，无需重复注入。');
      setTimeout(() => setBannerNotice(null), 3000);
      return;
    }
    const template = `\n\n### 核心执行规约（防幻觉与事实基准）：\n1. 【事实唯一基准】：所有数据、指标与分析结论必须 100% 依托工具读取到的本地文件或真实记录，严禁无事实依据凭空推演。\n2. 【缺失如实拒答 (Fail-Fast)】：未检索到真实记录或文件缺失时，必须如实回答“无相关记录”，严禁为了交差编造模拟/测试假数据！\n3. 【真实调用验证】：严禁在未真正收到工具成功回包前宣称已完成。`;
    setFormDesc(current ? `${current}${template}` : template.trim());
    setFormStrictMode(true);
    setFormTemperature(0.1);
    setFormFailFast(true);
    setFormVerifyToolCall(true);
    setFormRequireEvidence(true);
    setBannerNotice('已成功注入防幻觉与事实基准规约模版，并已将模式设为严谨工作 (T=0.1)。');
    setTimeout(() => setBannerNotice(null), 3000);
  };

  // 一句话自然语言智能装配专家配置与联动定时计划
  const handleGenerateFromNaturalLanguage = async () => {
    const raw = nlPrompt.trim();
    if (!raw || nlGenerating) return;
    setNlGenerating(true);
    setApiError(null);
    try {
      const res = await apiFetch('/api/v1/agents/generate-from-prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_prompt: raw,
          workspace_id: workspace?.id || 'default',
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setApiError(err?.detail || '自然语言解析失败');
        return;
      }
      const data = await res.json();
      const draft = data?.draft;
      if (!draft) return;

      if (draft.name) setFormName(draft.name);
      if (draft.context_prompt) setFormDesc(draft.context_prompt);
      if (Array.isArray(draft.skill_ids)) setSelectedSkillIds(draft.skill_ids);
      if (Array.isArray(draft.allowed_tools)) setFormAllowedTools(draft.allowed_tools.join(', '));
      if (Array.isArray(draft.allowed_write_dirs)) setFormAllowedWriteDirs(draft.allowed_write_dirs.join(', '));

      if (draft.grounding_config) {
        const gc = draft.grounding_config;
        setFormStrictMode(gc.strict_mode ?? true);
        setFormTemperature(typeof gc.temperature === 'number' ? gc.temperature : 0.1);
        setFormFailFast(gc.fail_fast_on_missing ?? true);
        setFormVerifyToolCall(gc.verify_tool_call ?? true);
        setFormRequireEvidence(gc.require_evidence ?? true);
        setFormCustomRules(gc.custom_rules || '');
      }

      if (draft.schedule && draft.schedule.enabled) {
        setScheduleEnabled(true);
        setScheduleCron(draft.schedule.cron_expr || '0 18 * * *');
        setScheduleHuman(draft.schedule.human_schedule || '');
        setScheduleName(draft.schedule.name || `${draft.name} · 定时任务`);
        setSchedulePrompt(draft.schedule.prompt_template || raw);
      } else {
        setScheduleEnabled(false);
      }
    } catch {
      setApiError('网络请求异常，请检查后端服务是否在线');
    } finally {
      setNlGenerating(false);
    }
  };

  // 打开修改个人专家模态框
  const handleOpenEditModal = (agent: AgentItem, e: React.MouseEvent) => {
    e.stopPropagation();
    // 官方智能体受系统保护，绝不允许修改
    if (agent.ownership === 'official') return;

    setEditingAgent(agent);
    setFormName(agent.name);
    setFormDesc(agent.context_prompt || agent.description || '');
    setFormModel(agent.base_model && agent.base_model.toLowerCase() !== 'auto' ? agent.base_model : '');
    setFormAllowedTools((agent.allowed_tools || []).join(', '));
    setFormAllowedWriteDirs((agent.allowed_write_dirs || []).join(', '));
    setSelectedSkillIds([...(agent.skill_ids || [])]);
    setIsSkillPickerOpen(false);
    setSkillSearchQuery('');
    setApiError(null);
    setNlPrompt('');
    setScheduleEnabled(false);
    setScheduleCron('0 18 * * *');
    setScheduleHuman('');
    setScheduleName('');
    setSchedulePrompt('');

    const gc = agent.grounding_config || {};
    setFormStrictMode(gc.strict_mode ?? true);
    setFormTemperature(typeof gc.temperature === 'number' ? gc.temperature : (gc.strict_mode === false ? 0.7 : 0.1));
    setFormFailFast(gc.fail_fast_on_missing ?? true);
    setFormVerifyToolCall(gc.verify_tool_call ?? true);
    setFormRequireEvidence(gc.require_evidence ?? true);
    setFormCustomRules(gc.custom_rules || '');
    setFormKbIsolationMode((agent as any).kb_isolation_mode || 'strict_bound_only');
    setFormStrictGrounding((agent as any).strict_grounding !== undefined ? Boolean((agent as any).strict_grounding) : true);
    setFormMinRelevanceScore(typeof (agent as any).min_relevance_score === 'number' ? (agent as any).min_relevance_score : 0.42);
    setFormApiServiceEnabled(Boolean((agent as any).api_service_enabled));
    setFormApiModelSlug((agent as any).api_model_slug || '');
    setFormBoundKbs([]);

    // 异步拉取已绑定的知识库
    apiFetch(`/api/v1/agents/${encodeURIComponent(agent.id)}/knowledge-bases`)
      .then(async (res) => {
        if (res.ok) {
          const json = await res.json();
          if (json.bound_knowledge_bases) {
            setFormBoundKbs(
              json.bound_knowledge_bases.map((b: any) => ({
                kb_id: b.kb_id,
                name: b.name,
                priority_weight: b.priority_weight ?? 1.0,
                top_k_override: b.top_k_override ?? 5,
              }))
            );
          }
        }
      })
      .catch(() => {});

    setIsGroundingPanelOpen(true);

    setIsModalOpen(true);
  };

  const handleToggleSkill = (skillId: string) => {
    setSelectedSkillIds((prev) =>
      prev.includes(skillId) ? prev.filter((id) => id !== skillId) : [...prev, skillId]
    );
  };

  const parseCommaList = (raw: string): string[] =>
    raw
      .split(/[,，\n]/)
      .map((s) => s.trim())
      .filter(Boolean);

  // 保存专家（创建或修改，优先走后端持久化，兼容回退本地）
  const handleSaveAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim() || savingAgent) return;

    const todayStr = new Date().toISOString().split('T')[0];
    const targetModel = formModel.trim();
    const targetPrompt = formDesc.trim();
    const allowedTools = parseCommaList(formAllowedTools);
    const allowedWriteDirs = parseCommaList(formAllowedWriteDirs);
    const descText = targetPrompt || '自定义专业智能体，搭载专属技能集与独立沙盒运行环境。';

    const targetGroundingConfig: GroundingConfig = {
      strict_mode: formStrictMode,
      temperature: formTemperature,
      fail_fast_on_missing: formFailFast,
      verify_tool_call: formVerifyToolCall,
      require_evidence: formRequireEvidence,
      custom_rules: formCustomRules.trim().slice(0, 500),
    };

    setSavingAgent(true);
    setApiError(null);
    let savedTargetId: string | null = null;

    try {
      if (editingAgent) {
        savedTargetId = editingAgent.id;
        // 严格安全防卫：禁止修改官方智能体
        if (editingAgent.ownership === 'official') return;

        let savedAgent: AgentItem | null = null;
        try {
          const res = await apiFetch(`/api/v1/agents/${encodeURIComponent(editingAgent.id)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              name: formName.trim(),
              description: descText,
              base_model: targetModel,
              context_prompt: targetPrompt || undefined,
              skill_ids: selectedSkillIds,
              allowed_tools: allowedTools,
              allowed_write_dirs: allowedWriteDirs,
              grounding_config: targetGroundingConfig,
              kb_isolation_mode: formKbIsolationMode,
              strict_grounding: formStrictGrounding ? 1 : 0,
              min_relevance_score: formMinRelevanceScore,
              api_service_enabled: formApiServiceEnabled ? 1 : 0,
              api_model_slug: formApiModelSlug.trim() || undefined,
            }),
          });
          if (res.ok) {
            const data = await res.json();
            if (data?.agent) savedAgent = data.agent;
          } else if (res.status === 403 || res.status === 400) {
            const errJson = await res.json().catch(() => ({}));
            setApiError(errJson?.detail || '修改专家失败');
            return;
          }
        } catch {
          // 后端不可达时平滑回退本地更新
        }

        const nextList = agents.map((a) => {
          if (a.id !== editingAgent.id) return a;
          if (savedAgent) return savedAgent;
          return {
            ...a,
            name: formName.trim(),
            description: descText,
            base_model: targetModel,
            context_prompt: targetPrompt || undefined,
            skill_ids: selectedSkillIds,
            tools_count: Math.max(2, selectedSkillIds.length * 2),
            allowed_tools: allowedTools,
            allowed_write_dirs: allowedWriteDirs,
            grounding_config: targetGroundingConfig,
            updated_at: todayStr,
          };
        });

        setAgents(nextList);
        syncCustomAgentsLocalCache(nextList);
      } else {
        // 创建全新个人智能体
        let createdAgent: AgentItem | null = null;
        try {
          const res = await apiFetch('/api/v1/agents', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              name: formName.trim(),
              description: descText,
              base_model: targetModel,
              context_prompt: targetPrompt || undefined,
              skill_ids: selectedSkillIds,
              allowed_tools: allowedTools,
              allowed_write_dirs: allowedWriteDirs,
              grounding_config: targetGroundingConfig,
              status: 'active',
              kb_isolation_mode: formKbIsolationMode,
              strict_grounding: formStrictGrounding ? 1 : 0,
              min_relevance_score: formMinRelevanceScore,
              api_service_enabled: formApiServiceEnabled ? 1 : 0,
              api_model_slug: formApiModelSlug.trim() || undefined,
            }),
          });
          if (res.ok) {
            const data = await res.json();
            if (data?.agent) createdAgent = data.agent;
          } else if (res.status >= 400 && res.status < 500) {
            const errJson = await res.json().catch(() => ({}));
            setApiError(errJson?.detail || '创建专家失败');
            return;
          }
        } catch {
          // 后端不可达时平滑回退本地创建
        }

        const newAgent: AgentItem = createdAgent || {
          id: `agent_${Date.now().toString(36)}`,
          name: formName.trim(),
          description: descText,
          base_model: targetModel,
          context_prompt: targetPrompt || undefined,
          skill_ids: selectedSkillIds,
          tools_count: Math.max(2, selectedSkillIds.length * 2),
          ownership: 'custom',
          status: 'active',
          allowed_tools: allowedTools,
          allowed_write_dirs: allowedWriteDirs,
          grounding_config: targetGroundingConfig,
          created_at: todayStr,
          updated_at: todayStr,
        };

        const nextList = [newAgent, ...agents.filter((a) => a.id !== newAgent.id)];
        setAgents(nextList);
        syncCustomAgentsLocalCache(nextList);

        // 若启用了联动定时调度，自动向 /api/v1/scheduled 注册绑定该专家的计划任务
        if (scheduleEnabled && scheduleCron.trim()) {
          try {
            const schedRes = await apiFetch('/api/v1/scheduled', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                workspace_id: workspace?.id || 'default',
                name: scheduleName.trim() || `${newAgent.name} · 定时自动执行`,
                cron_expr: scheduleCron.trim(),
                prompt_template: schedulePrompt.trim() || descText,
                agent_id: newAgent.id,
              }),
            });
            if (schedRes.ok) {
              setBannerNotice(
                `已创建专家【${newAgent.name}】并同步注册定时任务（${scheduleHuman || scheduleCron.trim()}），可在「已安排调度」中查看运行记录。`
              );
            } else {
              setBannerNotice(`已创建专家【${newAgent.name}】，但关联定时任务创建失败，请前往「已安排调度」手动补充。`);
            }
          } catch {
            setBannerNotice(`已创建专家【${newAgent.name}】，定时任务注册遇到网络异常。`);
          }
        }
        savedTargetId = newAgent.id;
      }

      // 同步保存专家的知识底座与对外 API 策略
      if (savedTargetId) {
        try {
          await apiFetch(`/api/v1/agents/${encodeURIComponent(savedTargetId)}/knowledge-bases`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              knowledge_bases: formBoundKbs.map((b) => ({
                kb_id: b.kb_id,
                priority_weight: b.priority_weight,
                top_k_override: b.top_k_override,
              })),
              kb_isolation_mode: formKbIsolationMode,
              strict_grounding: formStrictGrounding ? 1 : 0,
              min_relevance_score: formMinRelevanceScore,
              api_service_enabled: formApiServiceEnabled ? 1 : 0,
              api_model_slug: formApiModelSlug.trim() || undefined,
            }),
          });
        } catch (kbSyncErr) {
          console.error('同步专家知识底座配置失败:', kbSyncErr);
        }
      }

      setIsModalOpen(false);
      setEditingAgent(null);
    } finally {
      setSavingAgent(false);
    }
  };

  // 删除个人专家
  const handleDeleteAgent = async (agent: AgentItem, e: React.MouseEvent) => {
    e.stopPropagation();
    // 严格安全防卫：官方专家不可删除
    if (agent.ownership === 'official') return;

    const confirmed = window.confirm(
      `确定要删除个人专家【${agent.name}】吗？\n删除后不可恢复。`
    );
    if (!confirmed) return;

    try {
      const res = await apiFetch(`/api/v1/agents/${encodeURIComponent(agent.id)}`, {
        method: 'DELETE',
      });
      if (!res.ok && res.status === 403) {
        return;
      }
    } catch {
      // 后端不可达时平滑回退本地删除
    }

    const nextList = agents.filter((a) => a.id !== agent.id);
    setAgents(nextList);
    syncCustomAgentsLocalCache(nextList);
  };

  const filteredAgents = agents.filter((agent) => {
    const matchesSearch =
      agent.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      agent.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      agent.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesFilter = filterOwnership === 'all' || agent.ownership === filterOwnership;
    return matchesSearch && matchesFilter;
  });

  const filteredSkillsForPicker = filterSkillsByCategory(allSkills, skillCategory).filter((s) => {
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
    <div className="flex-1 flex flex-col h-full bg-background overflow-hidden">
      {/* 头部标题与操作区 */}
      <div className="p-6 border-b border-border flex items-center justify-between shrink-0 bg-background">
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
                ? 'bg-surface-card text-foreground font-semibold shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            全部 ({agents.length})
          </button>
          <button
            onClick={() => setFilterOwnership('official')}
            className={`px-3 py-1 rounded-md transition-colors ${
              filterOwnership === 'official'
                ? 'bg-surface-card text-foreground font-semibold shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            官方 ({agents.filter((a) => a.ownership === 'official').length})
          </button>
          <button
            onClick={() => setFilterOwnership('custom')}
            className={`px-3 py-1 rounded-md transition-colors ${
              filterOwnership === 'custom'
                ? 'bg-surface-card text-foreground font-semibold shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            个人自定义 ({agents.filter((a) => a.ownership === 'custom').length})
          </button>
        </div>
      </div>

      {/* 智能体卡片与表格列表 */}
      <div className="flex-1 overflow-y-auto p-6">
        {bannerNotice && (
          <div className="mb-4 px-4 py-2.5 rounded-xl bg-emerald-500/12 border border-emerald-500/30 text-xs text-emerald-700 dark:text-emerald-300 flex items-center justify-between">
            <span>{bannerNotice}</span>
            <button
              type="button"
              onClick={() => setBannerNotice(null)}
              className="p-0.5 rounded hover:bg-emerald-500/20"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
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
                          {agent.grounding_config?.strict_mode ? (
                            <span
                              className="text-[10px] px-1.5 py-0.5 rounded font-mono inline-flex items-center gap-1 bg-zinc-800/80 text-zinc-300 border border-zinc-700"
                              title={`严谨工作模式 (温度 T=${agent.grounding_config?.temperature ?? 0.1}) · 强制事实锚定`}
                            >
                              <Shield className="w-2.5 h-2.5 text-zinc-400" />
                              <span>严谨 · T={agent.grounding_config?.temperature ?? 0.1}</span>
                            </span>
                          ) : (
                            <span
                              className="text-[10px] px-1.5 py-0.5 rounded font-mono inline-flex items-center gap-1 bg-zinc-900/50 text-zinc-400 border border-zinc-800"
                              title={`自由构想模式 (温度 T=${agent.grounding_config?.temperature ?? 0.7})`}
                            >
                              <Sliders className="w-2.5 h-2.5 text-zinc-500" />
                              <span>构想 · T={agent.grounding_config?.temperature ?? 0.7}</span>
                            </span>
                          )}
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
                        {agent.base_model
                          ? agent.base_model
                          : currentModel
                          ? `跟随当前模型 (${currentModel})`
                          : '跟随当前会话模型'}
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

                    {/* 工具白名单与目录隔离徽标 */}
                    {((agent.allowed_tools && agent.allowed_tools.length > 0) ||
                      (agent.allowed_write_dirs && agent.allowed_write_dirs.length > 0)) && (
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {agent.allowed_tools && agent.allowed_tools.length > 0 && (
                          <span
                            className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-violet-500/10 text-violet-500 dark:text-violet-300 border border-violet-500/25 text-[10px] font-mono"
                            title={`工具白名单: ${agent.allowed_tools.join(', ')}`}
                          >
                            <Wrench className="w-2.5 h-2.5 shrink-0" />
                            <span>
                              工具隔离 ({agent.allowed_tools.length}): {agent.allowed_tools.slice(0, 2).join(', ')}
                              {agent.allowed_tools.length > 2 ? '…' : ''}
                            </span>
                          </span>
                        )}
                        {agent.allowed_write_dirs && agent.allowed_write_dirs.length > 0 && (
                          <span
                            className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-amber-500/10 text-amber-600 dark:text-amber-300 border border-amber-500/25 text-[10px] font-mono"
                            title={`允许写入目录: ${agent.allowed_write_dirs.join(', ')}`}
                          >
                            <FolderLock className="w-2.5 h-2.5 shrink-0" />
                            <span>
                              目录隔离: {agent.allowed_write_dirs.slice(0, 2).join(', ')}
                              {agent.allowed_write_dirs.length > 2 ? '…' : ''}
                            </span>
                          </span>
                        )}
                      </div>
                    )}
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
        <div 
          onClick={() => {
            setIsModalOpen(false);
            setEditingAgent(null);
          }}
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 animate-fade-in"
        >
          <div 
            onClick={(e) => e.stopPropagation()}
            className="bg-surface-card border border-border w-full max-w-xl max-h-[90vh] rounded-2xl p-6 shadow-2xl flex flex-col animate-scale-in text-foreground"
          >
            <div className="flex items-center justify-between pb-3 border-b border-border shrink-0">
              <div className="flex items-center space-x-2.5">
                <div className="p-2 rounded-xl bg-surface-100 text-foreground border border-border">
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
                className="p-1.5 rounded-lg hover:bg-surface-100 text-muted-foreground hover:text-foreground transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSaveAgent} className="flex-1 overflow-y-auto py-4 space-y-4 pr-1">
              {bannerNotice && (
                <div className="p-2.5 rounded-lg border border-border bg-surface-100 text-foreground text-xs flex items-center space-x-2 animate-fadeIn">
                  <Check className="w-3.5 h-3.5 text-foreground shrink-0" />
                  <span>{bannerNotice}</span>
                </div>
              )}
              {!editingAgent && (
                <div className="p-3.5 rounded-xl bg-surface-100 border border-border space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground flex items-center space-x-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-foreground" />
                      <span>一句话自然语言创建（自动装配技能 + 定时调度）</span>
                    </span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <input
                      type="text"
                      placeholder="例如：每天18:00定时帮我收集今天所有的交互内容写日报并作记录"
                      value={nlPrompt}
                      onChange={(e) => setNlPrompt(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          handleGenerateFromNaturalLanguage();
                        }
                      }}
                      className="flex-1 px-3 py-1.5 rounded-lg bg-surface-200 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-foreground/40"
                    />
                    <button
                      type="button"
                      onClick={handleGenerateFromNaturalLanguage}
                      disabled={nlGenerating || !nlPrompt.trim()}
                      className="px-3 py-1.5 rounded-lg bg-foreground hover:opacity-90 disabled:opacity-40 text-background text-xs font-semibold flex items-center space-x-1 shrink-0 transition-opacity"
                    >
                      {nlGenerating ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Sparkles className="w-3.5 h-3.5" />
                      )}
                      <span>{nlGenerating ? '解析中' : '智能装配'}</span>
                    </button>
                  </div>
                </div>
              )}

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
                  className="w-full px-3 py-2 rounded-xl bg-surface-100 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-foreground/40"
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-xs font-semibold text-foreground">
                    职能描述 & SOP 指导原则
                  </label>
                  <button
                    type="button"
                    onClick={handleInjectAntiHallucinationTemplate}
                    className="text-[11px] text-muted-foreground hover:text-foreground flex items-center space-x-1 transition-colors px-2 py-0.5 rounded border border-border bg-surface-100"
                    title="一键注入事实唯一基准、缺失拒答与工具执行验证规约"
                  >
                    <FileCheck className="w-3 h-3 text-muted-foreground" />
                    <span>规范化 / 注入防幻觉模版</span>
                  </button>
                </div>
                <textarea
                  rows={2}
                  placeholder="清晰描述专家的主要职责、擅长领域、行为准则与交付标准..."
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl bg-surface-100 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-foreground/40 resize-none font-mono"
                />
              </div>

              {/* 防幻觉与严谨度配置面板 (Grounding & Anti-Hallucination) */}
              <div className="p-3.5 rounded-xl border border-border bg-surface-100 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <Shield className="w-4 h-4 text-foreground" />
                    <span className="text-xs font-bold text-foreground">
                      防幻觉与严谨度配置 (Grounding Guardrails)
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsGroundingPanelOpen((prev) => !prev)}
                    className="text-[11px] text-muted-foreground hover:text-foreground flex items-center space-x-1 transition-colors"
                  >
                    <span>{isGroundingPanelOpen ? '收起配置' : '展开微调'}</span>
                    {isGroundingPanelOpen ? (
                      <ChevronUp className="w-3.5 h-3.5" />
                    ) : (
                      <ChevronDown className="w-3.5 h-3.5" />
                    )}
                  </button>
                </div>

                {/* 模式快速切换单选块 */}
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <button
                    type="button"
                    onClick={() => {
                      setFormStrictMode(true);
                      setFormTemperature(0.1);
                      setFormFailFast(true);
                      setFormVerifyToolCall(true);
                      setFormRequireEvidence(true);
                    }}
                    className={`p-2.5 rounded-lg border text-left transition-all ${
                      formStrictMode
                        ? 'border-foreground/40 bg-surface-200 text-foreground shadow-sm'
                        : 'border-border bg-surface-50 text-muted-foreground hover:border-border-hover hover:text-foreground'
                    }`}
                  >
                    <div className="font-semibold flex items-center justify-between">
                      <span className="flex items-center space-x-1.5">
                        <Target className="w-3.5 h-3.5 text-foreground" />
                        <span>严谨工作模式</span>
                      </span>
                      {formStrictMode && <Check className="w-3.5 h-3.5 text-foreground" />}
                    </div>
                    <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">
                      推荐工作场景：低温 (T=0.1)、事实锚定、数据缺失如实拒答、防虚假完成声明。
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setFormStrictMode(false);
                      setFormTemperature(0.7);
                      setFormFailFast(false);
                      setFormVerifyToolCall(false);
                    }}
                    className={`p-2.5 rounded-lg border text-left transition-all ${
                      !formStrictMode
                        ? 'border-foreground/40 bg-surface-200 text-foreground shadow-sm'
                        : 'border-border bg-surface-50 text-muted-foreground hover:border-border-hover hover:text-foreground'
                    }`}
                  >
                    <div className="font-semibold flex items-center justify-between">
                      <span className="flex items-center space-x-1.5">
                        <Compass className="w-3.5 h-3.5 text-foreground" />
                        <span>自由构想模式</span>
                      </span>
                      {!formStrictMode && <Check className="w-3.5 h-3.5 text-foreground" />}
                    </div>
                    <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">
                      适合头脑风暴与创意探索：温度 (T=0.7)，允许发散思维与灵感构想。
                    </p>
                  </button>
                </div>

                {/* 展开的微调选项 */}
                {isGroundingPanelOpen && (
                  <div className="space-y-3 pt-2 border-t border-border text-xs text-foreground">
                    {/* 温度滑块 */}
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[11px] text-muted-foreground flex items-center gap-1">
                          <Sliders className="w-3 h-3 text-muted-foreground" />
                          <span>模型采样温度 (Temperature):</span>
                        </span>
                        <span className="font-mono text-[11px] text-foreground font-bold">
                          {formTemperature.toFixed(2)} {formTemperature <= 0.2 ? '(严谨精确)' : formTemperature >= 0.7 ? '(发散创意)' : '(平衡)'}
                        </span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.05"
                        value={formTemperature}
                        onChange={(e) => setFormTemperature(parseFloat(e.target.value))}
                        className="w-full h-1.5 bg-surface-200 rounded-lg appearance-none cursor-pointer accent-foreground"
                      />
                    </div>

                    {/* 三重防线复选框 */}
                    <div className="space-y-2 pt-1 text-[11px]">
                      <label className="flex items-start space-x-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={formFailFast}
                          onChange={(e) => setFormFailFast(e.target.checked)}
                          className="mt-0.5 rounded border-border bg-surface-200 text-foreground accent-foreground"
                        />
                        <div>
                          <span className="font-medium text-foreground">未检索到数据如实拒答 (Fail-Fast)</span>
                          <p className="text-[10px] text-muted-foreground">
                            当本地日志为空或目标文件不存在时，系统硬性规定直接说明无记录，禁止编造假样本。
                          </p>
                        </div>
                      </label>

                      <label className="flex items-start space-x-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={formVerifyToolCall}
                          onChange={(e) => setFormVerifyToolCall(e.target.checked)}
                          className="mt-0.5 rounded border-border bg-surface-200 text-foreground accent-foreground"
                        />
                        <div>
                          <span className="font-medium text-foreground">工具调用真实性审计</span>
                          <p className="text-[10px] text-muted-foreground">
                            严禁模型在未成功调用工具并获得返回前宣称“已为您查询/已为您整理/已成功保存”。
                          </p>
                        </div>
                      </label>

                      <label className="flex items-start space-x-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={formRequireEvidence}
                          onChange={(e) => setFormRequireEvidence(e.target.checked)}
                          className="mt-0.5 rounded border-border bg-surface-200 text-foreground accent-foreground"
                        />
                        <div>
                          <span className="font-medium text-foreground">输出结论必须附带事实来源</span>
                          <p className="text-[10px] text-muted-foreground">
                            强制要求分析报告与结论关联具体读取到的文件路径或工具数据片段。
                          </p>
                        </div>
                      </label>
                    </div>

                    {/* 自定义防虚构业务红线 */}
                    <div className="pt-1">
                      <label className="block text-[11px] text-muted-foreground mb-1">
                        自定义业务防虚构红线（可选，最多 500 字符）:
                      </label>
                      <input
                        type="text"
                        maxLength={500}
                        placeholder="例如：涉及财务金额必须精确对账；未找到今日工作记录直接输出无记录..."
                        value={formCustomRules}
                        onChange={(e) => setFormCustomRules(e.target.value)}
                        className="w-full px-3 py-1.5 rounded-lg bg-surface-200 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-foreground/40"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* v1.5.9 专属规程知识底座与对外 API 服务化面板 */}
              <ExpertKnowledgeAndApiPanel
                agentId={editingAgent?.id}
                kbIsolationMode={formKbIsolationMode}
                onChangeIsolationMode={setFormKbIsolationMode}
                strictGrounding={formStrictGrounding}
                onChangeStrictGrounding={setFormStrictGrounding}
                minRelevanceScore={formMinRelevanceScore}
                onChangeMinRelevanceScore={setFormMinRelevanceScore}
                apiServiceEnabled={formApiServiceEnabled}
                onChangeApiServiceEnabled={setFormApiServiceEnabled}
                apiModelSlug={formApiModelSlug}
                onChangeApiModelSlug={setFormApiModelSlug}
                boundKbs={formBoundKbs}
                onChangeBoundKbs={setFormBoundKbs}
              />

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
                  className="w-full px-3 py-2 rounded-xl bg-surface-100 border border-border text-xs text-foreground focus:outline-none focus:border-foreground/40 font-mono"
                >
                  <option value="">
                    跟随当前会话模型 (自动继承{currentModel ? ` · ${currentModel}` : ''})
                  </option>
                  {availableModels.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name || m.id} ({m.provider || 'Ready'}{m.badge ? ` · ${m.badge}` : ''})
                    </option>
                  ))}
                </select>
              </div>

              {/* 真实技能装配版块 */}
              <div className="space-y-2 pt-1">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-semibold text-foreground flex items-center space-x-1.5">
                    <Layers className="w-3.5 h-3.5 text-foreground" />
                    <span>装配技能集 (Skills · 多选)</span>
                    <span className="text-[10px] px-1.5 py-0.2 rounded bg-surface-200 border border-border text-foreground font-mono font-medium">
                      已选 {selectedSkillIds.length} 项
                    </span>
                  </label>
                  <button
                    type="button"
                    onClick={() => setIsSkillPickerOpen((prev) => !prev)}
                    className="text-xs font-semibold text-foreground hover:opacity-80 flex items-center space-x-1 transition-opacity"
                  >
                    <span>{isSkillPickerOpen ? '收起技能库' : '+ 添加/调整技能'}</span>
                  </button>
                </div>

                {/* 已选技能标签横条 */}
                <div className="p-2.5 rounded-xl bg-surface-100 border border-border min-h-[46px] flex flex-wrap gap-1.5 items-center">
                  {selectedSkillIds.length > 0 ? (
                    selectedSkillIds.map((id) => {
                      const sk = skillMap.get(id);
                      return (
                        <span
                          key={id}
                          className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-surface-200 border border-border text-foreground text-xs font-mono group"
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
                  <div className="p-3 rounded-xl bg-surface-100 border border-border space-y-2.5 animate-fadeIn">
                    <div className="relative">
                      <Search className="w-3.5 h-3.5 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
                      <input
                        type="text"
                        placeholder="搜索技能名称或职能..."
                        value={skillSearchQuery}
                        onChange={(e) => setSkillSearchQuery(e.target.value)}
                        className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-surface-200 border border-border text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-foreground/40"
                      />
                    </div>

                    {allSkills.length > 0 && (
                      <SkillCategoryTabs
                        skills={allSkills}
                        active={skillCategory}
                        onChange={setSkillCategory}
                        compact
                      />
                    )}

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
                                  ? 'bg-surface-200 border-foreground/40 text-foreground font-medium shadow-sm'
                                  : 'bg-surface-50 hover:bg-surface-200 border-border text-muted-foreground hover:text-foreground'
                              }`}
                            >
                              <div className="flex items-center space-x-2.5 min-w-0 flex-1 pr-2">
                                {isSelected ? (
                                  <CheckSquare className="w-4 h-4 text-foreground shrink-0" />
                                ) : (
                                  <Square className="w-4 h-4 text-muted-foreground shrink-0" />
                                )}
                                <div className="min-w-0">
                                  <div className="flex items-center space-x-1.5">
                                    <span className="font-semibold text-foreground truncate">{sk.name || sk.id}</span>
                                    {sk.command && (
                                      <span className="text-[10px] font-mono px-1 rounded bg-surface-200 border border-border text-foreground">
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
                                className={`text-[10px] px-1.5 py-0.5 rounded font-mono shrink-0 border border-border ${
                                  sk.scope === 'workspace' ? 'bg-surface-200 text-foreground' : 'bg-surface-100 text-muted-foreground'
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

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                <div>
                  <label className="block text-xs font-semibold text-foreground mb-1 flex items-center space-x-1">
                    <Wrench className="w-3 h-3 text-muted-foreground" />
                    <span>工具白名单 (可选，逗号分隔)</span>
                  </label>
                  <input
                    type="text"
                    placeholder="如: read_file, write_file, run_command"
                    value={formAllowedTools}
                    onChange={(e) => setFormAllowedTools(e.target.value)}
                    className="w-full px-3 py-1.5 rounded-xl bg-surface-100 border border-border text-xs font-mono text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-foreground/40"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-foreground mb-1 flex items-center space-x-1">
                    <FolderLock className="w-3 h-3 text-muted-foreground" />
                    <span>允许写入目录 (可选，逗号分隔)</span>
                  </label>
                  <input
                    type="text"
                    placeholder="如: frontend/, docs/"
                    value={formAllowedWriteDirs}
                    onChange={(e) => setFormAllowedWriteDirs(e.target.value)}
                    className="w-full px-3 py-1.5 rounded-xl bg-surface-100 border border-border text-xs font-mono text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-foreground/40"
                  />
                </div>
              </div>

              {!editingAgent && (
                <div className="p-3 rounded-xl bg-surface-100 border border-border space-y-2">
                  <label className="flex items-center justify-between cursor-pointer">
                    <span className="text-xs font-semibold text-foreground flex items-center space-x-1.5">
                      <Clock className="w-3.5 h-3.5 text-foreground" />
                      <span>联动定时任务调度（支持按计划自动唤醒该专家执行）</span>
                    </span>
                    <input
                      type="checkbox"
                      checked={scheduleEnabled}
                      onChange={(e) => setScheduleEnabled(e.target.checked)}
                      className="rounded border-border text-foreground accent-foreground"
                    />
                  </label>
                  {scheduleEnabled && (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 pt-1">
                      <div>
                        <span className="block text-[10px] text-muted-foreground mb-0.5">
                          Cron 表达式 {scheduleHuman ? `(${scheduleHuman})` : ''}
                        </span>
                        <input
                          type="text"
                          value={scheduleCron}
                          onChange={(e) => setScheduleCron(e.target.value)}
                          placeholder="0 18 * * *"
                          className="w-full px-2.5 py-1.5 rounded-lg bg-surface-200 border border-border text-xs font-mono text-foreground focus:outline-none focus:border-foreground/40"
                        />
                      </div>
                      <div>
                        <span className="block text-[10px] text-muted-foreground mb-0.5">计划任务名称</span>
                        <input
                          type="text"
                          value={scheduleName}
                          onChange={(e) => setScheduleName(e.target.value)}
                          placeholder={`${formName || '专家'} · 定时执行`}
                          className="w-full px-2.5 py-1.5 rounded-lg bg-surface-200 border border-border text-xs text-foreground focus:outline-none focus:border-foreground/40"
                        />
                      </div>
                    </div>
                  )}
                </div>
              )}

              {apiError && (
                <div className="px-3 py-2 rounded-xl bg-surface-100 border border-border text-xs text-rose-500">
                  {apiError}
                </div>
              )}

              <div className="p-3 rounded-xl bg-surface-100 border border-border text-[11px] text-muted-foreground space-y-1">
                <div className="font-semibold text-foreground flex items-center space-x-1">
                  <Shield className="w-3.5 h-3.5 text-foreground" />
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
                  className="px-4 py-2 rounded-xl border border-border hover:bg-surface-100 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 rounded-xl bg-foreground text-background hover:opacity-90 text-xs font-semibold shadow-sm transition-opacity"
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