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
}

export const REAL_MODEL_LIST: ModelOption[] = [
  {
    id: 'gemini-4-argon',
    name: 'Gemini 4 Argon',
    provider: 'Antigravity CLI',
    description: 'Google 最新旗舰 Argon 推理引擎 · 深度架构设计与复杂逻辑推演',
    badge: 'CLI 旗舰 · Argon',
    hasThinking: true,
  },
  {
    id: 'gemini-3.8-flash',
    name: 'Gemini 3.8 Flash',
    provider: 'Antigravity CLI',
    description: '宿主机 CLI 原生高推理极速引擎 · 兼顾响应速度与系统工具调度',
    badge: 'CLI 原生 · 极速',
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
}: ModelSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isThinkingSubmenuOpen, setIsThinkingSubmenuOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const activeList = models && models.length > 0 ? models : REAL_MODEL_LIST;

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
    if (!selectedOpt) return currentModel;
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
            selectedOpt?.test_status?.available
              ? 'bg-emerald-500'
              : 'bg-blue-500 animate-pulse'
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
              {accountName && accountName !== 'default'
                ? `账号 [${accountName}] 匹配模型`
                : authLabel || '当前账号可用模型'}
            </span>
            {isTested ? (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30 flex items-center space-x-1 shrink-0 font-medium">
                <CheckCircle2 className="w-2.5 h-2.5" />
                <span>实测可用 ({activeList.length})</span>
              </span>
            ) : (
              <span className="text-[9px] text-blue-500 font-medium shrink-0">
                按账号属性自适应
              </span>
            )}
          </div>

          <div className="space-y-1 max-h-80 overflow-y-auto pr-0.5">
            {activeList.map((m) => {
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
                        ? 'bg-blue-600/15 border border-blue-500/30 text-blue-600 dark:text-white'
                        : 'hover:bg-foreground/[0.04] text-foreground'
                    }`}
                  >
                    <div className="flex items-start space-x-2.5 min-w-0 pr-1">
                      <div className="mt-0.5 shrink-0">
                        {isSelected ? (
                          <Check className="w-4 h-4 text-blue-500" />
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
                                  ? 'bg-blue-600/20 text-blue-600 dark:text-white font-semibold'
                                  : 'hover:bg-foreground/[0.04] text-foreground'
                              }`}
                            >
                              <div className="w-3.5 h-3.5 flex items-center justify-center">
                                {isLevelSelected && <Check className="w-3.5 h-3.5 text-blue-500" />}
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
            })}
          </div>

          {/* 底部快捷进入设置测试模型按钮 */}
          {onOpenModelTest && (
            <div className="mt-1.5 pt-1.5 border-t border-border">
              <button
                type="button"
                onClick={() => {
                  setIsOpen(false);
                  setIsThinkingSubmenuOpen(false);
                  onOpenModelTest();
                }}
                className="w-full flex items-center justify-between px-3 py-2 rounded-xl bg-blue-500/10 hover:bg-blue-500/20 border border-blue-500/25 text-xs text-blue-600 dark:text-blue-300 font-semibold transition-colors shadow-sm"
              >
                <span className="flex items-center space-x-1.5">
                  <Activity className="w-3.5 h-3.5 text-blue-500" />
                  <span>一键测试模型可用性并筛选</span>
                </span>
                <Sliders className="w-3.5 h-3.5 text-blue-500" />
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
