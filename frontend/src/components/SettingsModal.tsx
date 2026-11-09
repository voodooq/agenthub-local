'use client';

import React, { useState, useEffect } from 'react';
import {
  X,
  Check,
  Brain,
  Sliders,
  Shield,
  RefreshCw,
  Save,
  CheckCircle2,
  AlertTriangle,
  Layers,
  Activity,
  Zap,
  Cpu,
  XCircle,
  Play,
  Sun,
  Moon,
  Laptop,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';
import { ModelOption } from './ModelSelector';
import { useTheme, ThemeMode } from '@/context/ThemeContext';

export interface PersonaPreset {
  id: string;
  name: string;
  avatar: string;
  tagline: string;
  description: string;
  system_prompt: string;
}

export interface SkillItem {
  id: string;
  name: string;
  icon: string;
  description: string;
  prompt_fragment: string;
  is_default?: boolean;
}

export interface ActiveModelsState {
  account_name: string;
  auth_type: string;
  auth_label: string;
  is_tested: boolean;
  verified_model_ids: string[];
  models: ModelOption[];
  candidates: ModelOption[];
}

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSettingsSaved?: () => void;
  onModelsUpdated?: (state: ActiveModelsState) => void;
  initialTab?: 'models' | 'appearance' | 'personality';
}

export function SettingsModal({
  isOpen,
  onClose,
  onSettingsSaved,
  onModelsUpdated,
  initialTab = 'models',
}: SettingsModalProps) {
  const [activeTab, setActiveTab] = useState<'appearance' | 'models' | 'context' | 'about'>(
    initialTab === 'appearance' ? 'appearance' : initialTab === 'models' ? 'models' : 'appearance'
  );
  const { theme, setTheme } = useTheme();

  // 上下文记忆轮数状态 (默认 10 轮)
  const [contextRounds, setContextRounds] = useState<number>(10);
  const [cleanupMessage, setCleanupMessage] = useState<string | null>(null);
  const [isCleaning, setIsCleaning] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  useEffect(() => {
    const savedRounds = localStorage.getItem('geminihub_context_rounds');
    if (savedRounds !== null) {
      setContextRounds(parseInt(savedRounds, 10));
    }
  }, []);

  const handleUpdateContextRounds = (rounds: number) => {
    setContextRounds(rounds);
    localStorage.setItem('geminihub_context_rounds', rounds.toString());
  };

  const handleCleanupDuplicates = async () => {
    try {
      setIsCleaning(true);
      setCleanupMessage(null);
      const res = await apiFetch('/api/v1/conversations/cleanup-duplicates', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setCleanupMessage(data.message || '清理完成');
        if (onSettingsSaved) onSettingsSaved();
      }
    } catch (e: any) {
      setCleanupMessage(`清理失败: ${e.message}`);
    } finally {
      setIsCleaning(false);
    }
  };

  // 1. 模型测试与可用性筛选状态
  const [modelState, setModelState] = useState<ActiveModelsState | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [testingAll, setTestingAll] = useState(false);
  const [testingSingleId, setTestingSingleId] = useState<string | null>(null);
  const [savingVerified, setSavingVerified] = useState(false);
  const [modelFeedback, setModelFeedback] = useState<{ success: boolean; msg: string } | null>(null);

  // 2. 回复性格与 SKILL 状态
  const [presets, setPresets] = useState<PersonaPreset[]>([]);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [selectedPersonaId, setSelectedPersonaId] = useState<string>('architect');
  const [activeSkillIds, setActiveSkillIds] = useState<string[]>(['deep_debugger']);
  const [customPrompt, setCustomPrompt] = useState<string>('');

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ success: boolean; msg: string } | null>(null);

  const fetchActiveModels = async () => {
    try {
      setLoadingModels(true);
      const res = await apiFetch('/api/v1/models/active');
      if (res.ok) {
        const data: ActiveModelsState = await res.json();
        setModelState(data);
        if (onModelsUpdated) onModelsUpdated(data);
      }
    } catch (e) {
      console.error('拉取账号模型状态失败:', e);
    } finally {
      setLoadingModels(false);
    }
  };

  const fetchSettings = async () => {
    try {
      setLoading(true);
      const res = await apiFetch('/api/v1/settings/personality');
      if (res.ok) {
        const data = await res.json();
        setPresets(data.presets || []);
        setSkills(data.skills || []);
        if (data.config) {
          setSelectedPersonaId(data.config.selected_persona_id || 'architect');
          setActiveSkillIds(data.config.active_skill_ids || ['deep_debugger']);
          setCustomPrompt(data.config.custom_system_prompt || '');
        }
      }
    } catch (e: any) {
      console.error('拉取设置失败:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      if (initialTab === 'models' || initialTab === 'appearance') {
        setActiveTab(initialTab);
      } else {
        setActiveTab('appearance');
      }
      fetchActiveModels();
      fetchSettings();
      setFeedback(null);
      setModelFeedback(null);
    }
  }, [isOpen, initialTab]);

  const handleTestAllModels = async () => {
    try {
      setTestingAll(true);
      setModelFeedback({
        success: true,
        msg: '正在并发实测当前账号下所有候选模型的真实连通性，请稍候...',
      });

      const res = await apiFetch('/api/v1/models/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_save: true }),
      });

      const resData = await res.json();
      if (res.ok) {
        if (resData.state) {
          setModelState(resData.state);
          if (onModelsUpdated) onModelsUpdated(resData.state);
        }
        setModelFeedback({
          success: resData.available_count > 0,
          msg: resData.message || '模型测试已完成！',
        });
      } else {
        setModelFeedback({
          success: false,
          msg: resData.detail || '测试模型失败，请检查账号状态',
        });
      }
    } catch (e: any) {
      setModelFeedback({
        success: false,
        msg: e.message || '网络请求异常',
      });
    } finally {
      setTestingAll(false);
    }
  };

  const handleTestSingleModel = async (modelId: string) => {
    try {
      setTestingSingleId(modelId);
      setModelFeedback(null);

      const res = await apiFetch('/api/v1/models/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_ids: [modelId], auto_save: true }),
      });

      const resData = await res.json();
      if (res.ok) {
        if (resData.state) {
          setModelState(resData.state);
          if (onModelsUpdated) onModelsUpdated(resData.state);
        }
        const singleRes = (resData.results || [])[0];
        if (singleRes?.available) {
          setModelFeedback({
            success: true,
            msg: `模型 [${modelId}] 测试通过 (${Math.round(singleRes.latency_ms)} ms)，已保留在输入框旁边！`,
          });
        } else {
          setModelFeedback({
            success: false,
            msg: `模型 [${modelId}] 测试未通过：${singleRes?.error || '不可用'}`,
          });
        }
      } else {
        setModelFeedback({
          success: false,
          msg: resData.detail || '单模型测试失败',
        });
      }
    } catch (e: any) {
      setModelFeedback({
        success: false,
        msg: e.message || '网络异常',
      });
    } finally {
      setTestingSingleId(null);
    }
  };

  const handleToggleVerifiedModel = async (modelId: string) => {
    if (!modelState) return;
    const currentIds = modelState.verified_model_ids || [];
    const exists = currentIds.includes(modelId);
    const nextIds = exists
      ? currentIds.filter((id) => id !== modelId)
      : [...currentIds, modelId];

    if (nextIds.length === 0) {
      setModelFeedback({
        success: false,
        msg: '请至少保留 1 个可用模型显示在输入框旁边！',
      });
      return;
    }

    try {
      setSavingVerified(true);
      const res = await apiFetch('/api/v1/models/verified', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ verified_model_ids: nextIds }),
      });
      const resData = await res.json();
      if (res.ok) {
        setModelState(resData);
        if (onModelsUpdated) onModelsUpdated(resData);
        setModelFeedback({
          success: true,
          msg: `已更新输入框旁边显示的模型列表（共保留 ${resData.models.length} 个模型）。`,
        });
      }
    } catch (e: any) {
      setModelFeedback({
        success: false,
        msg: e.message || '保存异常',
      });
    } finally {
      setSavingVerified(false);
    }
  };

  const handleToggleSkill = (skillId: string) => {
    setActiveSkillIds((prev) =>
      prev.includes(skillId) ? prev.filter((id) => id !== skillId) : [...prev, skillId]
    );
  };

  const handleSavePersonality = async () => {
    try {
      setSaving(true);
      setFeedback(null);
      const payload = {
        selected_persona_id: selectedPersonaId,
        active_skill_ids: activeSkillIds,
        custom_system_prompt: customPrompt,
      };

      const res = await apiFetch('/api/v1/settings/personality', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const resData = await res.json();
      if (res.ok) {
        localStorage.setItem('geminihub_personality_config', JSON.stringify(payload));
        setFeedback({ success: true, msg: '设置与个性化性格已成功保存并立即生效！' });
        if (onSettingsSaved) onSettingsSaved();
      } else {
        setFeedback({ success: false, msg: resData.detail || '保存失败' });
      }
    } catch (e: any) {
      setFeedback({ success: false, msg: e.message || '网络异常' });
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fadeIn"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-4xl h-[620px] rounded-3xl bg-surface-card border border-border shadow-2xl flex flex-row overflow-hidden text-foreground"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 左侧 Strata 风格分类导航栏 */}
        <div className="w-56 bg-surface-100/70 border-r border-border p-5 flex flex-col justify-between shrink-0 select-none">
          <div className="space-y-6">
            <div className="px-2">
              <h2 className="text-base font-bold text-foreground tracking-tight">设置</h2>
              <p className="text-[11px] text-muted-foreground mt-0.5">AgentHub Local</p>
            </div>

            <nav className="space-y-1.5">
              {[
                { id: 'appearance', label: '外观', icon: Sun },
                {
                  id: 'models',
                  label: '模型',
                  icon: Cpu,
                  badge: modelState ? `${modelState.models.length}/${modelState.candidates.length}` : undefined,
                },
                { id: 'context', label: '上下文与会话', icon: Layers },
                { id: 'about', label: '关于', icon: Shield },
              ].map((item) => {
                const Icon = item.icon;
                const isSelected = activeTab === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setActiveTab(item.id as any)}
                    className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-xs transition-all ${
                      isSelected
                        ? 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300 font-semibold shadow-xs'
                        : 'text-muted-foreground hover:text-foreground hover:bg-surface-200/60'
                    }`}
                  >
                    <div className="flex items-center space-x-2.5">
                      <Icon
                        className={`w-4 h-4 ${
                          isSelected ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'
                        }`}
                      />
                      <span>{item.label}</span>
                    </div>
                    {item.badge && (
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded-full font-mono ${
                          isSelected
                            ? 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300'
                            : 'bg-surface-200 text-muted-foreground'
                        }`}
                      >
                        {item.badge}
                      </span>
                    )}
                  </button>
                );
              })}
            </nav>
          </div>

          <div className="px-2 text-[11px] text-muted-foreground/70 font-mono">
            v1.1.2 · Strata Edition
          </div>
        </div>

        {/* 右侧主工作内容区 */}
        <div className="flex-1 flex flex-col min-w-0 bg-surface-card">
          {/* 右上角关闭按钮栏 */}
          <div className="flex items-center justify-between px-6 py-3.5 border-b border-border/50 shrink-0">
            <span className="text-xs font-semibold text-muted-foreground">
              {activeTab === 'appearance' && '外观与主题偏好'}
              {activeTab === 'models' && '模型自动识别与连通性测试'}
              {activeTab === 'context' && '单次对话记忆与会话管理'}
              {activeTab === 'about' && '关于系统与本地引擎'}
            </span>
            <button
              onClick={onClose}
              className="p-1.5 rounded-full text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
              title="关闭"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* 主内容滚动区 */}
          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {/* ==================== TAB 1: 模型自动识别与一键测试 ==================== */}
            {activeTab === 'models' && (
              <div className="space-y-5">
                {/* 当前活跃账号属性与一键测试横幅 */}
                <div className="p-4 rounded-2xl bg-surface-100 border border-emerald-500/30 flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-sm">
                  <div className="space-y-1.5">
                    <div className="flex items-center space-x-2">
                      <Cpu className="w-4 h-4 text-emerald-500" />
                      <span className="text-xs font-bold text-foreground">
                        当前调度账号：{modelState?.account_name || '检测中...'}
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/25 font-bold">
                        {modelState?.auth_label || '自动识别中'}
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-relaxed break-anywhere">
                      系统已根据该账号的认证属性自动筛选出匹配的候选模型库。点击右侧
                      <strong className="text-emerald-600 dark:text-emerald-400">「一键测试可用模型」</strong>
                      后，将自动实测每个模型并
                      <strong className="text-emerald-600 dark:text-emerald-400">仅保留可以使用的模型显示在输入框旁边</strong>。
                    </p>
                  </div>

                  <button
                    type="button"
                    onClick={handleTestAllModels}
                    disabled={testingAll || loadingModels}
                    className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs transition-all shadow-md shadow-emerald-500/25 flex items-center justify-center space-x-2 shrink-0 disabled:opacity-50 min-h-[38px]"
                  >
                    {testingAll ? (
                      <>
                        <RefreshCw className="w-4 h-4 animate-spin" />
                        <span>正在实测全部模型...</span>
                      </>
                    ) : (
                      <>
                        <Zap className="w-4 h-4 text-amber-300" />
                        <span>一键测试可用模型</span>
                      </>
                    )}
                  </button>
                </div>

                {/* 测试结果反馈条 */}
                {modelFeedback && (
                  <div
                    className={`p-3.5 rounded-xl text-xs flex items-center space-x-2 animate-fadeIn ${
                      modelFeedback.success
                        ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300 border border-emerald-500/20'
                        : 'bg-rose-500/10 text-rose-600 dark:text-rose-300 border border-rose-500/20'
                    }`}
                  >
                    {modelFeedback.success ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                    ) : (
                      <AlertTriangle className="w-4 h-4 text-rose-500 shrink-0" />
                    )}
                    <span className="break-anywhere leading-relaxed">{modelFeedback.msg}</span>
                  </div>
                )}

                {/* 候选模型列表与测试状态 */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-foreground">
                      当前账号属性匹配的候选模型 ({modelState?.candidates.length || 0})
                    </span>
                    <span className="text-[11px] text-muted-foreground">
                      勾选的模型将保留显示在输入框旁边的选择器中
                    </span>
                  </div>

                  {loadingModels && !modelState ? (
                    <div className="p-8 text-center text-muted-foreground text-xs flex items-center justify-center space-x-2">
                      <RefreshCw className="w-4 h-4 animate-spin text-emerald-500" />
                      <span>正在根据账号属性加载模型库...</span>
                    </div>
                  ) : (
                    <div className="space-y-2.5">
                      {modelState?.candidates.map((m) => {
                        const isRetained = modelState.verified_model_ids.includes(m.id);
                        const isTestingThis = testingAll || testingSingleId === m.id;
                        const status = m.test_status;

                        return (
                          <div
                            key={m.id}
                            className={`p-3.5 rounded-2xl border transition-all flex items-center justify-between gap-4 ${
                              isRetained
                                ? 'bg-surface-100 border-emerald-500/40 shadow-xs'
                                : 'bg-surface-200/40 border-border opacity-70'
                            }`}
                          >
                            <div className="flex items-start space-x-3 min-w-0 flex-1">
                              {/* 复选框 */}
                              <button
                                type="button"
                                disabled={savingVerified || testingAll}
                                onClick={() => handleToggleVerifiedModel(m.id)}
                                className={`mt-0.5 w-4 h-4 rounded flex items-center justify-center border transition-colors shrink-0 ${
                                  isRetained
                                    ? 'bg-emerald-600 border-emerald-500 text-white'
                                    : 'bg-transparent border-muted-foreground hover:border-foreground'
                                }`}
                                title={isRetained ? '已在输入框旁保留显示' : '点击保留到输入框旁显示'}
                              >
                                {isRetained && <Check className="w-3 h-3 stroke-[3]" />}
                              </button>

                              <div className="min-w-0 flex-1">
                                <div className="flex items-center flex-wrap gap-2">
                                  <span className="text-xs font-bold text-foreground">{m.name}</span>
                                  <span className="text-[10px] font-mono text-muted-foreground bg-surface-200 px-1.5 py-0.5 rounded border border-border/60">
                                    {m.id}
                                  </span>
                                  <span
                                    className={`text-[9px] px-1.5 py-0.5 rounded-full border ${
                                      m.provider === 'Antigravity CLI'
                                        ? 'bg-purple-500/10 text-purple-600 dark:text-purple-300 border-purple-500/20'
                                        : 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300 border-emerald-500/20'
                                    }`}
                                  >
                                    {m.badge}
                                  </span>

                                  {isTestingThis ? (
                                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30 flex items-center space-x-1">
                                      <RefreshCw className="w-2.5 h-2.5 animate-spin" />
                                      <span>测试中...</span>
                                    </span>
                                  ) : status ? (
                                    status.available ? (
                                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30 flex items-center space-x-1 font-medium">
                                        <CheckCircle2 className="w-3 h-3 text-emerald-500" />
                                        <span>可用 · {(status.latency_ms / 1000).toFixed(1)}s</span>
                                      </span>
                                    ) : (
                                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-rose-500/15 text-rose-600 dark:text-rose-300 border border-rose-500/30 flex items-center space-x-1 font-medium">
                                        <XCircle className="w-3 h-3 text-rose-500" />
                                        <span>不可用</span>
                                      </span>
                                    )
                                  ) : (
                                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-surface-200 text-muted-foreground border border-border">
                                      未测试
                                    </span>
                                  )}
                                </div>

                                <p className="text-[11px] text-muted-foreground mt-1 leading-snug break-anywhere">
                                  {m.description}
                                </p>

                                {status && !status.available && status.error && (
                                  <p className="text-[11px] text-rose-500 mt-1 font-mono bg-rose-500/10 border border-rose-500/20 rounded-lg px-2.5 py-1 break-anywhere">
                                    原因: {status.error}
                                  </p>
                                )}
                              </div>
                            </div>

                            <div className="flex items-center space-x-2 shrink-0">
                              <span
                                className={`text-[10px] px-2 py-0.5 rounded-lg border font-medium ${
                                  isRetained
                                    ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/25'
                                    : 'bg-surface-200 text-muted-foreground border-border'
                                }`}
                              >
                                {isRetained ? '输入框已保留' : '已隐藏'}
                              </span>

                              <button
                                type="button"
                                disabled={isTestingThis}
                                onClick={() => handleTestSingleModel(m.id)}
                                className="px-2.5 py-1.5 rounded-xl bg-surface-200 hover:bg-surface-50 border border-border text-xs text-foreground font-medium flex items-center space-x-1 transition-colors disabled:opacity-40"
                                title="单独测试该模型连通性"
                              >
                                {testingSingleId === m.id ? (
                                  <RefreshCw className="w-3 h-3 animate-spin text-emerald-500" />
                                ) : (
                                  <Play className="w-3 h-3 text-emerald-500" />
                                )}
                                <span>测试</span>
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            )}

          {/* ==================== TAB 2: 上下文记忆与会话管理 ==================== */}
          {activeTab === 'context' && (
            <div className="space-y-6">
              {/* 多轮对话上下文记忆轮数 */}
              <div className="p-4 rounded-2xl bg-surface-100 border border-border space-y-4">
                <div>
                  <label className="text-xs font-bold text-foreground flex items-center space-x-2">
                    <Layers className="w-4 h-4 text-emerald-500" />
                    <span>单次对话上下文记忆长度 (多轮对话)</span>
                  </label>
                  <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
                    控制模型在单次会话中向前回溯记忆的历史对话轮数（1 轮 = 1 次提问 + 1 次回复）。更长的轮数有助于保持长期代码推演记忆，适当轮数可提升生成响应速度。
                  </p>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5">
                  {[
                    { rounds: 0, label: '0 轮 (无记忆)', desc: '单次独立问答，省 Token 极速' },
                    { rounds: 3, label: '3 轮 (轻度)', desc: '近程短记忆，日常快速查询' },
                    { rounds: 6, label: '6 轮 (标准)', desc: '兼顾连续问答与响应流畅' },
                    { rounds: 10, label: '10 轮 (推荐)', desc: '平衡复杂逻辑推演与记忆深度' },
                    { rounds: 20, label: '20 轮 (深度)', desc: '超长上下文，多模块重构调试' },
                  ].map((item) => {
                    const isSelected = contextRounds === item.rounds;
                    return (
                      <button
                        key={item.rounds}
                        type="button"
                        onClick={() => handleUpdateContextRounds(item.rounds)}
                        className={`p-3 rounded-xl border text-left transition-all flex flex-col justify-between space-y-1.5 ${
                          isSelected
                            ? 'border-emerald-500 bg-emerald-500/10 shadow-sm'
                            : 'border-border bg-surface-200/50 hover:bg-surface-200'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className={`text-xs font-bold ${isSelected ? 'text-emerald-600 dark:text-emerald-400' : 'text-foreground'}`}>
                            {item.label}
                          </span>
                          {isSelected && <Check className="w-3.5 h-3.5 text-emerald-500 stroke-[3]" />}
                        </div>
                        <span className="text-[10px] text-muted-foreground leading-tight">
                          {item.desc}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* 会话列表去重与整理 */}
              <div className="p-4 rounded-2xl bg-surface-100 border border-border space-y-3">
                <div className="flex items-center justify-between">
                  <div className="space-y-1">
                    <span className="text-xs font-bold text-foreground flex items-center space-x-2">
                      <RefreshCw className="w-4 h-4 text-emerald-500" />
                      <span>会话列表智能整理与去重</span>
                    </span>
                    <p className="text-[11px] text-muted-foreground">
                      若左侧菜单因历史连续提问或误触产生了重复标题的废弃副本，点击可自动保留最新一条并清理冗余空记录。
                    </p>
                  </div>

                  <button
                    type="button"
                    disabled={isCleaning}
                    onClick={handleCleanupDuplicates}
                    className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs transition-all shadow-sm shadow-emerald-600/20 flex items-center space-x-1.5 shrink-0 disabled:opacity-50"
                  >
                    {isCleaning ? (
                      <>
                        <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                        <span>正在清理...</span>
                      </>
                    ) : (
                      <span>一键清理重复会话</span>
                    )}
                  </button>
                </div>

                {cleanupMessage && (
                  <div className="p-2.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-300 text-xs flex items-center space-x-2 animate-fadeIn">
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                    <span>{cleanupMessage}</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ==================== TAB 3: 界面显示外观 (主题色切换) ==================== */}
          {activeTab === 'appearance' && (
            <div className="space-y-6">
              <div>
                <label className="text-xs font-bold text-foreground flex items-center space-x-2">
                  <Sun className="w-4 h-4 text-emerald-500" />
                  <span>界面色彩模式 (支持浅色、深色、跟随系统三种选项)</span>
                </label>
                <p className="text-[11px] text-muted-foreground mt-1">
                  参考 Strata 设计哲学：深色为黑曜翡翠 (Obsidian Emerald)，浅色为柔和鼠尾草灰白 (Sage Minimal)，护眼舒适。
                </p>
              </div>

              <div className="grid grid-cols-3 gap-4">
                {[
                  {
                    mode: 'light' as ThemeMode,
                    title: '浅色模式 (Light)',
                    desc: 'Sage Minimal · 鼠尾草灰白，柔和不刺眼，舒适通读',
                    icon: <Sun className="w-6 h-6 text-amber-500" />,
                    bgDemo: 'bg-[#f4f6f5] border-slate-300/70 text-[#18221d]',
                  },
                  {
                    mode: 'dark' as ThemeMode,
                    title: '深色模式 (Dark)',
                    desc: 'Obsidian Emerald · 黑曜石与深翠绿融合，暗夜极客质感',
                    icon: <Moon className="w-6 h-6 text-emerald-400" />,
                    bgDemo: 'bg-[#0b0f0e] border-emerald-900/30 text-emerald-100',
                  },
                  {
                    mode: 'system' as ThemeMode,
                    title: '跟随系统 (System)',
                    desc: '自动同步 Windows 操作系统的浅色/深色设置',
                    icon: <Laptop className="w-6 h-6 text-purple-400" />,
                    bgDemo: 'bg-gradient-to-r from-slate-100 to-slate-900 border-border text-foreground',
                  },
                ].map((item) => {
                  const isSelected = theme === item.mode;
                  return (
                    <div
                      key={item.mode}
                      onClick={() => setTheme(item.mode)}
                      className={`p-4 rounded-2xl border-2 transition-all cursor-pointer relative flex flex-col justify-between space-y-3 ${
                        isSelected
                          ? 'border-emerald-500 bg-emerald-500/10 shadow-lg shadow-emerald-500/15'
                          : 'border-border bg-surface-100 hover:border-border-hover'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="p-2.5 rounded-xl bg-surface-200 border border-border">
                          {item.icon}
                        </div>
                        {isSelected && (
                          <span className="w-5 h-5 rounded-full bg-emerald-600 text-white flex items-center justify-center text-xs">
                            <Check className="w-3 h-3 stroke-[3]" />
                          </span>
                        )}
                      </div>

                      <div>
                        <div className="text-xs font-bold text-foreground">{item.title}</div>
                        <p className="text-[11px] text-muted-foreground mt-1 leading-snug break-anywhere">
                          {item.desc}
                        </p>
                      </div>

                      <div className={`h-8 rounded-lg border flex items-center px-2.5 text-[10px] font-mono ${item.bgDemo}`}>
                        <span>Aa Preview</span>
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="p-4 rounded-2xl bg-surface-100 border border-border space-y-2">
                <span className="text-xs font-bold text-foreground">排版与无障碍设计准则 (WCAG 2.2 AA)</span>
                <ul className="text-xs text-muted-foreground list-disc list-inside space-y-1">
                  <li>全量启用长 Token 自然折行保护 (`break-anywhere`)，杜绝长 URL 和代码标识符撑爆排版；</li>
                  <li>正文行高调至 1.68，保证中文与西文字符在各种分辨率下的舒适通读体验；</li>
                  <li>所有交互按键均满足最小 36px 舒适触达尺寸。</li>
                </ul>
              </div>
            </div>
          )}

          {/* ==================== TAB 4: 关于系统与本地引擎 ==================== */}
          {activeTab === 'about' && (
            <div className="space-y-5">
              <div className="p-5 rounded-2xl bg-surface-100 border border-border flex items-center justify-between">
                <div className="flex items-center space-x-3.5">
                  <div className="w-10 h-10 rounded-2xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-emerald-600 dark:text-emerald-400 font-bold font-mono text-sm">
                    AH
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-foreground flex items-center space-x-2">
                      <span>AgentHub Local</span>
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 font-mono">
                        v1.2.0 · Strata Edition
                      </span>
                    </h3>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      本地优先（Local-First）极客级 AI 智能工作流与 Agent 终端沙箱
                    </p>
                  </div>
                </div>
              </div>

              <div className="p-4 rounded-2xl bg-surface-100 border border-border space-y-3">
                <span className="text-xs font-bold text-foreground">隐私与安全边界说明</span>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  AgentHub Local 遵循严格的数据驻留规范：所有会话记录、长期记忆、账号凭据以及本地生成的代码均存储于您本地的 SQLite 数据库与物理硬盘（`skills/`）中。未经沙箱确认，AI 绝不会静默覆写或执行高危命令。
                </p>
              </div>

              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="p-3 rounded-xl bg-surface-200/50 border border-border">
                  <div className="text-muted-foreground text-[11px]">底层渲染引擎</div>
                  <div className="font-semibold text-foreground mt-0.5">WebView2 (Chromium) / Next.js</div>
                </div>
                <div className="p-3 rounded-xl bg-surface-200/50 border border-border">
                  <div className="text-muted-foreground text-[11px]">本地存储架构</div>
                  <div className="font-semibold text-foreground mt-0.5">SQLite WAL + Local ChromaDB</div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* 底部按钮栏 */}
        <div className="px-6 py-3.5 border-t border-border bg-surface-100 flex items-center justify-between shrink-0">
          <div className="text-[11px] text-muted-foreground flex items-center space-x-1">
            <Shield className="w-3.5 h-3.5 text-emerald-500" />
            <span>设置直接写入本地持久化，跨会话与刷新自动生效</span>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 rounded-xl bg-surface-200 hover:bg-surface-50 border border-border text-xs text-foreground font-medium transition-colors"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  </div>
);
}
