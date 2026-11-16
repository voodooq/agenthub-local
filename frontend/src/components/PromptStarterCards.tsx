'use client';

import React from 'react';
import {
  Code2,
  ShieldCheck,
  Terminal,
  Layers,
  FileText,
  Binary,
  Table,
  Receipt,
  Search,
  Compass,
  TrendingUp,
  GraduationCap,
  Boxes,
  FileCode,
  Cpu,
  GitCompare,
  Bot,
  Sparkles,
} from 'lucide-react';
import { Workspace } from '@/types';

interface PromptStarterCardsProps {
  workspace?: Workspace;
  onSelectPrompt: (promptText: string) => void;
}

interface StarterCardItem {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  subtitle: string;
  prompt: string;
}

interface AgentStarterProfile {
  title: string;
  subtitle: string;
  cards: StarterCardItem[];
}

// 默认通用工作台提示卡片
const DEFAULT_PROFILE: AgentStarterProfile = {
  title: '需要我为你做什么？',
  subtitle: '支持多模态问答、代码调试、微信截图与文档粘贴',
  cards: [
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
  ],
};

// 深度研究专家专属引导
const DEEP_RESEARCHER_PROFILE: AgentStarterProfile = {
  title: '深度研究专家已就绪',
  subtitle: '深入全网一手权威资料与多维信源，沉淀高价值商业与技术研报',
  cards: [
    {
      icon: Search,
      title: '全网赛道深度研报',
      subtitle: '梳理行业演进脉络、头部格局与关键驱动要素',
      prompt: '请对当前关注的 [行业或技术领域] 进行全网一手权威资料调研，梳理发展现状、核心玩家与演进趋势，生成结构化调研报告。',
    },
    {
      icon: Compass,
      title: '权威技术选型对比',
      subtitle: '横向对比多套架构的技术参数、适用边界与性能表现',
      prompt: '请深入查阅官方源码与规范，横向对比两个技术方案的设计哲学、资源消耗与适用场景，给出权威选型决策指南。',
    },
    {
      icon: TrendingUp,
      title: '竞品商业与功能矩阵剖析',
      subtitle: '拆解竞品核心链路、商业变现与差异化护城河',
      prompt: '请深入剖析目标竞品的核心功能矩阵、底层架构与盈利模式，总结其优势与潜在破局机会。',
    },
    {
      icon: GraduationCap,
      title: '前沿学术论文综述',
      subtitle: '提炼最新权威论文的创新算法与实验验证结论',
      prompt: '请检索并精读关于该前沿课题的最新高引学术论文，提炼核心创新点、技术架构与量化评估指标。',
    },
  ],
};

// OCR 与视觉分析助手专属引导
const OCR_ANALYST_PROFILE: AgentStarterProfile = {
  title: 'OCR 与视觉分析助手已就绪',
  subtitle: '专注于高精度版面分析、复杂公式 LaTeX 提取与结构化表格还原',
  cards: [
    {
      icon: FileText,
      title: '提取图片全部文本',
      subtitle: '将上传的截图与扫描文档整齐还原为 Markdown',
      prompt: '请仔细识别我上传的图片，完整提取其中的所有文字内容，并根据排版还原为层次分明的 Markdown 格式。',
    },
    {
      icon: Binary,
      title: '复杂数学公式转 LaTeX',
      subtitle: '精准识别图片中的矩阵、积分与复杂公式符号',
      prompt: '请检测并提取图片中的全部数学公式与推导步骤，输出标准、可直接渲染的 LaTeX 源码块。',
    },
    {
      icon: Table,
      title: '多栏复杂表格结构化还原',
      subtitle: '识别行列跨格与表头对齐，输出结构化数据',
      prompt: '请分析图片中的表格区域，精确识别表头、跨行跨列并提取单元格数据，生成标准 Markdown 表格与 CSV。',
    },
    {
      icon: Receipt,
      title: '票据发票关键字段抽取',
      subtitle: '提取金额、纳税号、抬头与流水号结构化键值',
      prompt: '请识别上传的票据/发票图像，提取商户名称、金额、日期、发票代码与明细列表，以规范 JSON 结构输出。',
    },
  ],
};

// Plugin Builder 专家专属引导
const PLUGIN_BUILDER_PROFILE: AgentStarterProfile = {
  title: 'Plugin Builder 专家已就绪',
  subtitle: '负责标准化 MCP 插件构建、工具链集成与自动化协议规范校验',
  cards: [
    {
      icon: Boxes,
      title: '快速脚手架生成 MCP 插件',
      subtitle: '按照官方标准规范创建服务接口与 Tools 元数据',
      prompt: '请根据我的业务需求，为该服务生成符合标准 MCP 规范的 Python 服务端骨架，包含工具注册与入参 Schema。',
    },
    {
      icon: FileCode,
      title: '编写自动化 SKILL.md',
      subtitle: '梳理标准 SOP、触发契约与上下文最佳实践',
      prompt: '请为当前功能编写一份规范的 SKILL.md，包含标准 YAML Frontmatter、逐步执行规范与失败回退策略。',
    },
    {
      icon: ShieldCheck,
      title: '审查插件协议与坏味道',
      subtitle: '检测工具闭合隐患、未捕获异常与依赖项缺失',
      prompt: '请审查当前插件的实现代码，重点排查 Tool Call 协议闭合、参数验证以及跨平台路径兼容性问题。',
    },
    {
      icon: Cpu,
      title: '沙盒环境工具链联调',
      subtitle: '在隔离沙盒内运行测试脚本验证输入输出连通性',
      prompt: '请在当前沙盒环境下编写并执行端到端联调测试脚本，验证该 MCP 工具与本地环境交互的正确性。',
    },
  ],
};

// 代码重构与架构导师专属引导
const CODE_ARCHITECT_PROFILE: AgentStarterProfile = {
  title: '代码重构与架构导师已就绪',
  subtitle: '遵循《软件设计的哲学》高内聚低耦合原则，打造深模块架构与高可靠系统',
  cards: [
    {
      icon: Code2,
      title: '深模块设计与复杂度隐藏',
      subtitle: '识别浅模块与时序耦合，设计接口简洁实现深厚的好模块',
      prompt: '请审查当前代码库的模块划分，识别接口过宽、暴露过多实现细节的浅模块，并给出深模块重构方案。',
    },
    {
      icon: GitCompare,
      title: '双视角代码规范审查',
      subtitle: '从代码坏味道（Standards）与契约达成（Spec）双重视角验收',
      prompt: '请从代码规范、防御性过度、异常处理合理性以及需求契约达成度这几个维度，对最新的改动进行全面代码审查。',
    },
    {
      icon: Terminal,
      title: '红绿重构 TDD 先行',
      subtitle: '先设计失败的单测用例，再进行最小化安全实现',
      prompt: '请严格执行红-绿-重构循环：先为该功能编写能精准复现边界缺陷的失败测试用例，再给出优雅的最小实现代码。',
    },
    {
      icon: Layers,
      title: '接口异常收敛与韧性兜底',
      subtitle: '消除琐碎异常声明，设计自闭环的高可用错误恢复机制',
      prompt: '请梳理当前核心流程的异常处理逻辑，定义统一异常基类并消除无意义的细碎报错，实现自闭环的错误防御。',
    },
  ],
};

function resolveProfileForWorkspace(workspace?: Workspace): AgentStarterProfile {
  if (!workspace || workspace.id === 'default') {
    return DEFAULT_PROFILE;
  }

  const name = (workspace.name || '').toLowerCase();
  const prompt = (workspace.context_prompt || '').toLowerCase();

  if (name.includes('ocr') || name.includes('视觉') || prompt.includes('ocr') || prompt.includes('paddle')) {
    return OCR_ANALYST_PROFILE;
  }
  if (name.includes('研究') || name.includes('research') || prompt.includes('研究') || prompt.includes('research')) {
    return DEEP_RESEARCHER_PROFILE;
  }
  if (name.includes('plugin') || name.includes('插件') || prompt.includes('plugin') || prompt.includes('mcp')) {
    return PLUGIN_BUILDER_PROFILE;
  }
  if (name.includes('重构') || name.includes('架构') || prompt.includes('重构') || prompt.includes('架构') || prompt.includes('哲学')) {
    return CODE_ARCHITECT_PROFILE;
  }

  // 默认根据 workspace 的名称自定义专属欢迎语
  if (workspace.name) {
    return {
      title: `${workspace.name} 专属沙盒已就绪`,
      subtitle: workspace.context_prompt
        ? `${workspace.context_prompt.slice(0, 60)}...`
        : '当前沙盒已加载专属上下文与文件系统隔离，随时可以开始交互。',
      cards: DEFAULT_PROFILE.cards,
    };
  }

  return DEFAULT_PROFILE;
}

export function PromptStarterCards({ workspace, onSelectPrompt }: PromptStarterCardsProps) {
  const profile = resolveProfileForWorkspace(workspace);

  return (
    <div className="max-w-2xl mx-auto flex flex-col items-center justify-center space-y-8 select-none animate-fade-in my-auto py-10">
      {/* 居中标题与副标题 */}
      <div className="text-center space-y-2.5">
        <h2 className="text-2xl md:text-3xl font-bold tracking-tight text-foreground flex items-center justify-center space-x-2">
          <span>{profile.title}</span>
        </h2>
        <p className="text-xs md:text-sm text-muted-foreground font-light max-w-xl mx-auto">
          {profile.subtitle}
        </p>
      </div>

      {/* 极简卡片网格 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 w-full">
        {profile.cards.map((card, idx) => {
          const IconComponent = card.icon;
          return (
            <button
              key={idx}
              type="button"
              onClick={() => onSelectPrompt(card.prompt)}
              className="p-4 rounded-2xl bg-surface-100/70 hover:bg-surface-200/80 border border-border/70 hover:border-emerald-500/40 text-left transition-all duration-150 group shadow-sm flex items-start space-x-3.5 hover:shadow-md"
            >
              <div className="w-9 h-9 rounded-xl bg-surface-200 text-muted-foreground group-hover:text-emerald-500 group-hover:bg-emerald-500/10 flex items-center justify-center shrink-0 mt-0.5 transition-colors">
                <IconComponent className="w-4 h-4" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-xs font-semibold text-foreground group-hover:text-emerald-500 transition-colors">
                  {card.title}
                </div>
                <div className="text-[12px] text-muted-foreground mt-1 leading-relaxed line-clamp-2">
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
