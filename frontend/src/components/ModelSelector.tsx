'use client';

import React, { useState, useRef, useEffect } from 'react';
import {
  ChevronDown,
  Check,
  ChevronRight,
  Brain,
  Activity,
  CheckCircle2,
  Sliders,
} from 'lucide-react';

export interface ModelTestStatus {
  available: boolean;
  latency_ms: number;
  error?: string | null;
  tested_at?: string | null;
}

export interface ModelOption {
  id: string;
  name: string;
  provider: 'Google Official' | 'Antigravity CLI' | string;
  description: string;
  badge: string;
  hasThinking?: boolean;
  has_thinking?: boolean;
  is_default?: boolean;
  supported_auth_types?: string[];
  test_status?: ModelTestStatus | null;
}

export type ThinkingLevel = 'low' | 'medium' | 'high';

interface ModelSelectorProps {
  currentModel: string;
  thinkingLevel: ThinkingLevel;
  models?: ModelOption[];
  accountName?: string;
  authLabel?: string;
  isTested?: boolean;
  onSelectModel: (modelId: string) => void;
  onSelectThinking: (level: ThinkingLevel) => void;
  onOpenModelTest?: () => void;
  onOpenAccountModal?: () => void;
}

export const REAL_MODEL_LIST: ModelOption[] = [
  {
    id: 'gemini-4-argon',
    name: 'Gemini 4 Argon',
    provider: 'Google Official',
    description: 'Google 最新第 4 代 Argon 旗舰深度推理引擎 · 超深思维链与复杂架构推演',
    badge: '最新旗舰 · 4.0 Argon',
    hasThinking: true,
  },
  {
    id: 'gemini-3.8-flash',
    name: 'Gemini 3.8 Flash',
    provider: 'Google Official',
    description: 'Google 新一代 3.8 高推理极速主力模型 · 超低延迟响应与全能代码生成',
    badge: '高阶极速 · 3.8 Flash',
    hasThinking: true,
  },
  {
    id: 'gemini-3.5-flash-lite',
    name: 'Gemini 3.5 Flash-Lite',
    provider: 'Google Official',
    description: '3.5 代毫秒级轻量模型 · 极速问答、日常高频排查与工具调用',
    badge: '毫秒轻量 · 3.5',
    hasThinking: false,
  },
  {
    id: 'gemini-2.5-flash',
    name: 'Gemini 2.5 Flash',
    provider: 'Google Official',
    description: 'Google 官方全能高性价比模型 · 极低响应延迟与优秀的代码理解',
    badge: '官方主力 · 2.5 Flash',
    hasThinking: true,
  },
  {
    id: 'gemini-2.5-pro',
    name: 'Gemini 2.5 Pro',
    provider: 'Google Official',
    description: 'Google 官方成熟顶级旗舰推理模型 · 支持超长上下文与多级思维链',
    badge: '成熟旗舰 · 2.5 Pro',
    hasThinking: true,
  },
  {
    id: 'claude-sonnet-5-5',
    name: 'Claude Sonnet 5.5',
    provider: 'Antigravity CLI',
    description: '通过 Antigravity 官方通道调度的 Claude 5.5 主力工程编码思考模型',
    badge: 'CLI 原生 · 跨生态',
    hasThinking: true,
  },
  {
    id: 'claude-opus-5-5',
    name: 'Claude Opus 5.5',
    provider: 'Antigravity CLI',
    description: '通过 Antigravity 官方通道调度的 Claude 5.5 顶配深度推理模型',
    badge: 'CLI 顶配 · Opus',
    hasThinking: true,
  },
  {
    id: 'gpt-oss-120b-medium',
    name: 'GPT-OSS 120B',
    provider: 'Antigravity CLI',
    description: '120B 巨量参数开源推理模型 · 多视角代码审查与技术问答',
    badge: 'CLI 原生 · 120B',
    hasThinking: false,
  },
];

const THINKING_LEVELS: { id: ThinkingLevel; name: string; desc: string }[] = [
  { id: 'low', name: '轻度思考 (Low)', desc: '快速生成，思维链较短' },
  { id: 'medium', name: '深度适中 (Medium)', desc: '平衡思考与生成速度' },
  { id: 'high', name: '超级详尽 (High)', desc: '全量复杂推理与多级验算' },
];

export function ModelSelector({
  currentModel,
  thinkingLevel,
  models,
  accountName,
  authLabel,
  isTested,
  onSelectModel,
  onSelectThinking,
  onOpenModelTest,
  onOpenAccountModal,
}: ModelSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isThinkingSubmenuOpen, setIsThinkingSubmenuOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // 严格只展示当前账号或自定义通道实际可用的模型列表，未绑定 Google 账号时不默认显示 Gemini 模型
  const activeList = models || [];

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setIsThinkingSubmenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const supportsThinking = (opt: ModelOption) => {
    return Boolean(opt.hasThinking ?? opt.has_thinking);
  };

  const selectedOpt = activeList.find((m) => m.id === currentModel) || activeList[0];

  const getButtonLabel = () => {
    if (!selectedOpt) return currentModel || '未配置模型';
    if (supportsThinking(selectedOpt)) {
      const levelMap: Record<ThinkingLevel, string> = { low: '低', medium: '中', high: '高' };
      return `${selectedOpt.name} (${levelMap[thinkingLevel]})`;
    }
    return selectedOpt.name;
  };

  return (
    <div className="relative inline-block" ref={containerRef}>
      {/* 触发胶囊按钮 */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center space-x-2 px-3.5 py-1.5 rounded-full bg-surface-100 hover:bg-surface-50 border border-border text-xs font-semibold text-foreground transition-all hover:border-border-hover select-none shadow-sm min-h-[34px]"
        title={
          authLabel
            ? `当前按账号 [${accountName || '默认'}] (${authLabel}) 自动匹配可用模型`
            : '切换当前对话模型'
        }
      >
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${
            activeList.length === 0
              ? 'bg-amber-500 animate-pulse'
              : selectedOpt?.test_status?.available
                ? 'bg-emerald-500'
                : 'bg-emerald-500 animate-pulse'
          }`}
        ></span>
        <span className="truncate max-w-[150px]">{getButtonLabel()}</span>
        <ChevronDown
          className={`w-3.5 h-3.5 text-muted-foreground transition-transform duration-200 shrink-0 ${
            isOpen ? 'rotate-180' : ''
          }`}
        />
      </button>

      {/* 主下拉菜单 */}
      {isOpen && (
        <div className="absolute right-0 bottom-full mb-2 w-84 rounded-2xl glass-modal p-2 shadow-2xl border border-border z-50 animate-scale-in">
          <div className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground flex items-center justify-between border-b border-border/80 mb-1.5">
            <span className="truncate max-w-[170px]">
              {accountName && accountName !== 'default' && accountName !== '未配置'
                ? `账号 [${accountName}] 匹配模型`
                : authLabel || '当前可用模型'}
            </span>
            {activeList.length === 0 ? (
              <span className="text-[9px] text-amber-600 dark:text-amber-400 font-medium shrink-0">
                暂无可用模型
              </span>
            ) : isTested ? (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30 flex items-center space-x-1 shrink-0 font-medium">
                <CheckCircle2 className="w-2.5 h-2.5" />
                <span>实测可用 ({activeList.length})</span>
              </span>
            ) : (
              <span className="text-[9px] text-emerald-600 dark:text-emerald-400 font-medium shrink-0">
                按账号属性自适应
              </span>
            )}
          </div>

          <div className="space-y-1 max-h-80 overflow-y-auto pr-0.5">
            {activeList.length === 0 ? (
              <div className="p-4 rounded-xl bg-surface-200/50 border border-border/70 text-center space-y-2.5">
                <div className="text-xs font-semibold text-foreground">
                  尚未配置任何可用的模型或算力账号
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  请先在「账号与算力池」中添加本地模型（如 llama-server / Ollama）或第三方 API / Google 账号，系统将自动识别并显示您拥有的模型。
                </p>
                {onOpenAccountModal && (
                  <button
                    type="button"
                    onClick={() => {
                      setIsOpen(false);
                      onOpenAccountModal();
                    }}
                    className="w-full px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition-colors shadow-xs"
                  >
                    前往添加账号或本地模型
                  </button>
                )}
              </div>
            ) : (
              activeList.map((m) => {
              const isSelected = selectedOpt?.id === m.id;
              const hasThink = supportsThinking(m);
              const latencySec =
                m.test_status?.available && m.test_status.latency_ms
                  ? (m.test_status.latency_ms / 1000).toFixed(1) + 's'
                  : null;

              return (
                <div key={m.id} className="relative">
                  <button
                    type="button"
                    onClick={() => {
                      onSelectModel(m.id);
                      if (!hasThink) {
                        setIsOpen(false);
                        setIsThinkingSubmenuOpen(false);
                      } else {
                        setIsThinkingSubmenuOpen(!isThinkingSubmenuOpen);
                      }
                    }}
                    onMouseEnter={() => {
                      if (hasThink) setIsThinkingSubmenuOpen(true);
                      else setIsThinkingSubmenuOpen(false);
                    }}
                    className={`w-full flex items-center justify-between p-2.5 rounded-xl text-left transition-colors ${
                      isSelected
                        ? 'bg-emerald-500/15 border border-emerald-500/30 text-emerald-800 dark:text-emerald-200'
                        : 'hover:bg-foreground/[0.04] text-foreground'
                    }`}
                  >
                    <div className="flex items-start space-x-2.5 min-w-0 pr-1">
                      <div className="mt-0.5 shrink-0">
                        {isSelected ? (
                          <Check className="w-4 h-4 text-emerald-500" />
                        ) : (
                          <div className="w-4 h-4" />
                        )}
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center flex-wrap gap-1.5">
                          <span className="text-xs font-bold leading-tight">
                            {m.name}
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
                          {latencySec && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/25 font-mono">
                              ✓ {latencySec}
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-muted-foreground mt-1 leading-snug break-anywhere">
                          {m.description}
                        </div>
                      </div>
                    </div>

                    {hasThink && (
                      <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0 ml-1" />
                    )}
                  </button>

                  {/* 思考等级飞出子菜单 */}
                  {hasThink && isThinkingSubmenuOpen && isSelected && (
                    <div
                      className="absolute left-full bottom-0 ml-2 w-52 rounded-2xl glass-modal p-1.5 shadow-2xl border border-border z-50 animate-scale-in"
                      onMouseEnter={() => setIsThinkingSubmenuOpen(true)}
                    >
                      <div className="px-2.5 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b border-border/60 mb-1 flex items-center space-x-1">
                        <Brain className="w-3 h-3 text-purple-500" />
                        <span>思维链深度 (Thinking)</span>
                      </div>
                      <div className="space-y-0.5">
                        {THINKING_LEVELS.map((tl) => {
                          const isLevelSelected = thinkingLevel === tl.id;
                          return (
                            <button
                              key={tl.id}
                              type="button"
                              onClick={() => {
                                onSelectModel(m.id);
                                onSelectThinking(tl.id);
                                setIsOpen(false);
                                setIsThinkingSubmenuOpen(false);
                              }}
                              className={`w-full flex items-center space-x-2 p-2 rounded-xl text-left transition-colors ${
                                isLevelSelected
                                  ? 'bg-emerald-500/20 text-emerald-800 dark:text-emerald-200 font-semibold'
                                  : 'hover:bg-foreground/[0.04] text-foreground'
                              }`}
                            >
                              <div className="w-3.5 h-3.5 flex items-center justify-center">
                                {isLevelSelected && <Check className="w-3.5 h-3.5 text-emerald-500" />}
                              </div>
                              <div>
                                <div className="text-xs font-semibold">{tl.name}</div>
                                <div className="text-[10px] text-muted-foreground">{tl.desc}</div>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })
            )}
          </div>

          {/* 底部快捷进入设置测试模型按钮 */}
          {onOpenModelTest && activeList.length > 0 && (
            <div className="mt-1.5 pt-1.5 border-t border-border">
              <button
                type="button"
                onClick={() => {
                  setIsOpen(false);
                  setIsThinkingSubmenuOpen(false);
                  onOpenModelTest();
                }}
                className="w-full flex items-center justify-between px-3 py-2 rounded-xl bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/25 text-xs text-emerald-700 dark:text-emerald-300 font-semibold transition-colors shadow-sm"
              >
                <span className="flex items-center space-x-1.5">
                  <Activity className="w-3.5 h-3.5 text-emerald-500" />
                  <span>一键测试模型可用性并筛选</span>
                </span>
                <Sliders className="w-3.5 h-3.5 text-emerald-500" />
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
