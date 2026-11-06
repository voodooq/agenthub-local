'use client';

import React from 'react';
import { Code2, ShieldCheck, Terminal, Layers } from 'lucide-react';

interface PromptStarterCardsProps {
  onSelectPrompt: (promptText: string) => void;
}

const CARDS = [
  {
    icon: Code2,
    title: '分析代码架构与依赖',
    subtitle: '排查循环引用、优化模块内聚与轻量接口设计',
    prompt: '请分析当前项目的架构设计原则与依赖分层，找出潜在的设计异味并提供重构建议。',
  },
  {
    icon: ShieldCheck,
    title: '高并发与故障平滑接力',
    subtitle: '设计滑动窗口防抖与零感知账号切换机制',
    prompt: '在多账号高并发调用时易触发限流，请详细设计一套毫秒级零感知故障转移与 Cooldown 状态机机制。',
  },
  {
    icon: Terminal,
    title: '编写健壮自动化测试',
    subtitle: '覆盖网络超时、边界数据与异常分支',
    prompt: '请为上述核心业务逻辑编写完备的自动化单元测试用例，重点覆盖边界条件、异常分支与网络超时重试。',
  },
  {
    icon: Layers,
    title: '生成分布式架构拓扑',
    subtitle: '渲染清晰架构 Mermaid 流程图并实时预览',
    prompt: '请用 Mermaid 格式绘制一个高可用网关与多节点算力池负载均衡的架构拓扑图，展示全链路调用过程。',
  },
];

export function PromptStarterCards({ onSelectPrompt }: PromptStarterCardsProps) {
  return (
    <div className="max-w-2xl mx-auto flex flex-col items-center justify-center space-y-8 select-none animate-fade-in my-auto py-10">
      {/* 极简居中标题 */}
      <div className="text-center space-y-2.5">
        <h2 className="text-3xl md:text-4xl font-normal tracking-tight text-foreground">
          需要我为你做什么？
        </h2>
        <p className="text-sm text-muted-foreground font-light">
          支持多模态问答、代码调试、微信截图与文档粘贴
        </p>
      </div>

      {/* 极简卡片网格 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 w-full">
        {CARDS.map((card, idx) => {
          const IconComponent = card.icon;
          return (
            <button
              key={idx}
              type="button"
              onClick={() => onSelectPrompt(card.prompt)}
              className="p-4 rounded-xl bg-surface-card hover:bg-surface-100 border border-border/70 hover:border-border text-left transition-all duration-150 group shadow-sm flex items-start space-x-3"
            >
              <div className="w-8 h-8 rounded-lg bg-surface-200/80 text-muted-foreground group-hover:text-blue-500 group-hover:bg-blue-500/10 flex items-center justify-center shrink-0 mt-0.5 transition-colors">
                <IconComponent className="w-4 h-4" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium text-foreground group-hover:text-blue-500 transition-colors">
                  {card.title}
                </div>
                <div className="text-[12px] text-muted-foreground mt-1 leading-snug line-clamp-2">
                  {card.subtitle}
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
