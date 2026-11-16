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
  Palette,
  Layout,
  Brush,
  PenTool,
  BarChart3,
  Scale,
  CheckCircle2,
  Sliders,
  Zap,
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

// UI/UX 前端体验设计师专属引导
const UI_UX_DESIGNER_PROFILE: AgentStarterProfile = {
  title: 'UI/UX 前端体验设计专家已就绪',
  subtitle: '专注于高质感 Web 界面打造、Tailwind 样式调优与三层 Design Token 规范',
  cards: [
    {
      icon: Palette,
      title: '打造高质感响应式 UI 组件',
      subtitle: '采用现代化设计语言，构建现代无障碍交互组件',
      prompt: '请帮我设计一个高质感的响应式 Web 组件，采用现代设计语言（如微渐变卡片与毛玻璃层级），提供完整优雅的 React + Tailwind CSS 代码并确保良好的 a11y 可访问性。',
    },
    {
      icon: Layers,
      title: '提炼设计系统与 Token 规范',
      subtitle: '梳理三层 Token 体系（基础→语义→组件）与深浅色模式变量',
      prompt: '请为当前系统设计一套规范的三层 Design Tokens 规范，涵盖色彩梯度、字体排版阶梯、圆角与间距规范，并提供可在 globals.css 中落地的完整 CSS 变量表。',
    },
    {
      icon: Sparkles,
      title: '界面视觉审查与交互动效调优',
      subtitle: '排查排版间距呼吸感、色彩对比度与操作微动效',
      prompt: '请审查当前界面的视觉设计与交互体验，重点排查排版留白、对比度是否达标（WCAG AA）、过渡微动效与布局在不同屏幕下的断点自适应表现，并给出针对性调优方案。',
    },
    {
      icon: Layout,
      title: '现代产品 Hero 区设计',
      subtitle: '打造具有视觉冲击力的首屏布局，突出核心价值主张',
      prompt: '请为我们的产品设计一个具有强视觉吸引力的 Hero 区（落地页首屏），包含醒目的标语排版、动态视觉背景、主次 CTA 按钮组与信任背书徽章。',
    },
  ],
};

// 品牌与视觉创意专家引导
const BRAND_DESIGN_PROFILE: AgentStarterProfile = {
  title: '品牌与视觉创意设计专家已就绪',
  subtitle: '专注于品牌识别规范、宣发海报横幅与高转化商业视觉物料',
  cards: [
    {
      icon: Brush,
      title: '多端宣发海报与横幅设计',
      subtitle: '针对不同社交媒体平台与广告位生成视觉横幅与排版方案',
      prompt: '请为我们的最新产品发布设计一组宣发横幅方案（包含推特、官网 Banner 及公众号封面规格），说明视觉主题、色彩搭配建议与文字排版层级。',
    },
    {
      icon: PenTool,
      title: '品牌语调 (Tone of Voice) 规范',
      subtitle: '建立一致的品牌沟通语调、文案框架与合规准则',
      prompt: '请为我们的品牌制定一份权威的品牌语调（Tone of Voice）规范手册，明确我们在面向开发者、企业客户及日常客服中的语言风格、用词避坑与示例。',
    },
    {
      icon: Layout,
      title: '商业 CIP 视觉识别系统',
      subtitle: '规范品牌 Logo 应用规范、辅色系与网格版式原则',
      prompt: '请为该品牌设计整套视觉识别系统（CIP）基础规范，包括 Logo 净空保护区、辅助图形设计理念、标准色值（HEX/RGB/CMYK）与排版网格规范。',
    },
    {
      icon: Sparkles,
      title: '高转化商业宣传文案矩阵',
      subtitle: '提炼痛点、解法与独特卖点（USP）的核心营销文案',
      prompt: '请针对目标受众，提炼我们产品的核心独特卖点（USP），编写一套覆盖落地页、朋友圈海报及邮件触达的高转化文案矩阵。',
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
      prompt: '请对当前关注的行业或技术领域进行全网一手权威资料调研，梳理发展现状、核心玩家与演进趋势，生成结构化调研报告。',
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

// 金融研报与数据分析专属引导
const FINANCE_DATA_PROFILE: AgentStarterProfile = {
  title: '金融研报与数据分析专家已就绪',
  subtitle: '专注于财报深入拆解、行业量化建模与商业数据可视化诊断',
  cards: [
    {
      icon: BarChart3,
      title: '财务三表深度健康度诊断',
      subtitle: '穿透现金流、毛利结构与非经常性损益真实质量',
      prompt: '请对上传的财务报告进行深度穿透诊断，重点评估经营现金流与净利润匹配度、资产负债结构与偿债能力，生成量化分析摘要。',
    },
    {
      icon: TrendingUp,
      title: '行业估值模型与对标分析',
      subtitle: '横向对比龙头企业 PE/PS/EV-EBITDA 与护城河指标',
      prompt: '请梳理目标企业与行业内 3 家头部可比公司的核心经营指标，建立估值对比矩阵并指出估值溢价/折价核心逻辑。',
    },
    {
      icon: Table,
      title: '多维业务数据异动归因',
      subtitle: '拆解转化漏斗与留存指标，找出业绩增长核心推手',
      prompt: '请分析近期业务运营数据的异动原因，基于第一性原理进行驱动因素拆解，并提供提升 ROI 的策略建议。',
    },
    {
      icon: PieChart,
      title: '一键生成分析图表与看板',
      subtitle: '基于 Chart.js 渲染高颜值交互式数据可视化图表',
      prompt: '请将上述数据整理为清晰的趋势图表，使用可视化图表规范直观呈现营收结构与同比/环比增速变化。',
    },
  ],
};

// 法律合规与合同审查专属引导
const LEGAL_COMPLIANCE_PROFILE: AgentStarterProfile = {
  title: '法律合规与法务审查专家已就绪',
  subtitle: '专注于商业合同风险排查、企业合规审查与权利责任边界界定',
  cards: [
    {
      icon: Scale,
      title: '商务合同风险条款深度审查',
      subtitle: '重点排查单方解除权、免责陷阱、违约责任与管辖漏洞',
      prompt: '请仔细审查这份商务合作合同，从我方利益出发，逐条排查潜在法律风险条款、不对等违约责任及争议解决管辖瑕疵，并给出修改批注。',
    },
    {
      icon: ShieldCheck,
      title: '企业合规与隐私协议评估',
      subtitle: '评估数据合规、用户个人信息保护合规性',
      prompt: '请评估我方拟上线功能的用户隐私协议与数据出境/留存合规性，对照最新法规要求指出可能存在的合规隐患与整改建议。',
    },
    {
      icon: FileText,
      title: '拟定标准化合作框架协议',
      subtitle: '起草权责清晰、违约救济明确的商业法律文书',
      prompt: '请根据我们的商业合作模式，起草一份结构严密、条款对等的框架合作协议，包含明确的知识产权归属、保密义务与争议退出机制。',
    },
    {
      icon: CheckCircle2,
      title: '知识产权与版权风险排查',
      subtitle: '评估软件著作权、商标侵权及开源协议商用传染性',
      prompt: '请对本项目引用的开源库与外部技术资产进行商用许可证合规审查，排查是否存在 GPL 传染风险或商业维权隐患。',
    },
  ],
};

function PieChart(props: { className?: string }) {
  return <BarChart3 {...props} />;
}

/**
 * 尝试从沙盒的 context_prompt 中解析所挂载的真实技能清单并自动生成卡片
 */
function extractDynamicCardsFromPrompt(contextPrompt: string): StarterCardItem[] | null {
  if (!contextPrompt || !contextPrompt.includes('【本专家专属挂载技能清单】')) {
    return null;
  }

  try {
    const listPart = contextPrompt.split('【本专家专属挂载技能清单】')[1];
    if (!listPart) return null;

    const lines = listPart
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.startsWith('- '));

    if (lines.length === 0) return null;

    const dynamicCards: StarterCardItem[] = [];

    // 正则匹配: - 技能名 (/command): SOP说明
    const reg = /^-\s*(.+?)\s*\((.+?)\):\s*(.+)$/;

    for (const line of lines) {
      const match = line.match(reg);
      if (match) {
        const name = match[1].trim();
        const cmd = match[2].trim();
        const desc = match[3].trim();

        // 根据指令类型自动选择最契合的图标
        let IconComponent = Zap;
        const lowerCmd = cmd.toLowerCase();
        if (lowerCmd.includes('ui') || lowerCmd.includes('design') || lowerCmd.includes('style')) {
          IconComponent = Palette;
        } else if (lowerCmd.includes('research') || lowerCmd.includes('search')) {
          IconComponent = Search;
        } else if (lowerCmd.includes('test') || lowerCmd.includes('tdd')) {
          IconComponent = Terminal;
        } else if (lowerCmd.includes('review') || lowerCmd.includes('code')) {
          IconComponent = Code2;
        } else if (lowerCmd.includes('plugin') || lowerCmd.includes('mcp')) {
          IconComponent = Boxes;
        } else if (lowerCmd.includes('slide')) {
          IconComponent = Layout;
        }

        dynamicCards.push({
          icon: IconComponent,
          title: `执行 ${name}`,
          subtitle: `${cmd} · ${desc.length > 28 ? desc.slice(0, 28) + '...' : desc}`,
          prompt: `请严格按照 ${cmd} 技能的标准 SOP 流程，协助我完成该领域的专业任务：\n\n[在此描述您的具体需求与目标]`,
        });
      }
    }

    if (dynamicCards.length > 0) {
      return dynamicCards.slice(0, 4);
    }
  } catch (e) {
    console.warn('解析挂载技能卡片异常:', e);
  }

  return null;
}

/**
 * 根据沙盒工作区的专家名称与上下文描述，智能生成高度匹配的卡片与引导语
 */
function resolveProfileForWorkspace(workspace?: Workspace): AgentStarterProfile {
  if (!workspace || workspace.id === 'default') {
    return DEFAULT_PROFILE;
  }

  const rawName = workspace.name || '';
  const cleanName = rawName.replace(/^\[沙盒\]\s*/, '').trim();
  const name = cleanName.toLowerCase();
  const prompt = (workspace.context_prompt || '').toLowerCase();

  // 1. 优先检测是否包含专属挂载技能清单，若包含则优先生成挂载技能卡片
  const dynamicCards = extractDynamicCardsFromPrompt(workspace.context_prompt || '');
  if (dynamicCards && dynamicCards.length >= 2) {
    return {
      title: `${cleanName} 专属沙盒已就绪`,
      subtitle: workspace.context_prompt
        ? `${workspace.context_prompt.split('\n')[0].slice(0, 75)}...`
        : '已装配专属专家技能与 SOP 标准约束，点击下方卡片即可直接触发对应技能指令。',
      cards: dynamicCards,
    };
  }

  // 2. UI / UX / 前端体验与组件设计专家
  if (
    name.includes('ui') ||
    name.includes('ux') ||
    name.includes('前端') ||
    name.includes('体验') ||
    name.includes('样式') ||
    name.includes('组件') ||
    prompt.includes('ui/ux') ||
    prompt.includes('tailwind') ||
    prompt.includes('前端体验') ||
    prompt.includes('design-system') ||
    prompt.includes('ui-styling')
  ) {
    return {
      title: `${cleanName} 专属沙盒已就绪`,
      subtitle: workspace.context_prompt
        ? `${workspace.context_prompt.split('\n')[0].slice(0, 75)}...`
        : UI_UX_DESIGNER_PROFILE.subtitle,
      cards: UI_UX_DESIGNER_PROFILE.cards,
    };
  }

  // 3. 品牌、横幅与视觉海报创意
  if (
    name.includes('海报') ||
    name.includes('横幅') ||
    name.includes('品牌') ||
    name.includes('banner') ||
    name.includes('brand') ||
    prompt.includes('banner-design') ||
    prompt.includes('brand')
  ) {
    return {
      title: `${cleanName} 专属沙盒已就绪`,
      subtitle: workspace.context_prompt
        ? `${workspace.context_prompt.split('\n')[0].slice(0, 75)}...`
        : BRAND_DESIGN_PROFILE.subtitle,
      cards: BRAND_DESIGN_PROFILE.cards,
    };
  }

  // 4. OCR 与视觉分析
  if (
    name.includes('ocr') ||
    name.includes('视觉') ||
    name.includes('识别') ||
    prompt.includes('ocr') ||
    prompt.includes('paddle') ||
    prompt.includes('公式')
  ) {
    return {
      title: `${cleanName} 专属沙盒已就绪`,
      subtitle: OCR_ANALYST_PROFILE.subtitle,
      cards: OCR_ANALYST_PROFILE.cards,
    };
  }

  // 5. 深度研究与权威调研
  if (
    name.includes('研究') ||
    name.includes('研报') ||
    name.includes('调研') ||
    name.includes('research') ||
    prompt.includes('research') ||
    prompt.includes('深度研究')
  ) {
    return {
      title: `${cleanName} 专属沙盒已就绪`,
      subtitle: DEEP_RESEARCHER_PROFILE.subtitle,
      cards: DEEP_RESEARCHER_PROFILE.cards,
    };
  }

  // 6. Plugin 与 MCP 构建
  if (
    name.includes('plugin') ||
    name.includes('插件') ||
    name.includes('mcp') ||
    prompt.includes('plugin') ||
    prompt.includes('mcp')
  ) {
    return {
      title: `${cleanName} 专属沙盒已就绪`,
      subtitle: PLUGIN_BUILDER_PROFILE.subtitle,
      cards: PLUGIN_BUILDER_PROFILE.cards,
    };
  }

  // 7. 代码架构与代码重构导师
  if (
    name.includes('重构') ||
    name.includes('架构') ||
    name.includes('代码') ||
    name.includes('tdd') ||
    prompt.includes('codebase-design') ||
    prompt.includes('code-review') ||
    prompt.includes('哲学')
  ) {
    return {
      title: `${cleanName} 专属沙盒已就绪`,
      subtitle: CODE_ARCHITECT_PROFILE.subtitle,
      cards: CODE_ARCHITECT_PROFILE.cards,
    };
  }

  // 8. 金融研报、数据分析与财报
  if (
    name.includes('金融') ||
    name.includes('财报') ||
    name.includes('股票') ||
    name.includes('数据') ||
    name.includes('财务') ||
    prompt.includes('财报') ||
    prompt.includes('金融')
  ) {
    return {
      title: `${cleanName} 专属沙盒已就绪`,
      subtitle: FINANCE_DATA_PROFILE.subtitle,
      cards: FINANCE_DATA_PROFILE.cards,
    };
  }

  // 9. 法律合规与合同审查
  if (
    name.includes('法律') ||
    name.includes('合同') ||
    name.includes('合规') ||
    name.includes('法务') ||
    prompt.includes('法律') ||
    prompt.includes('合同')
  ) {
    return {
      title: `${cleanName} 专属沙盒已就绪`,
      subtitle: LEGAL_COMPLIANCE_PROFILE.subtitle,
      cards: LEGAL_COMPLIANCE_PROFILE.cards,
    };
  }

  // 10. 用户自定义专家通用自适应生成（结合专家名称智能匹配，绝不回退到无关的代码架构）
  return {
    title: `${cleanName} 专属沙盒已就绪`,
    subtitle: workspace.context_prompt
      ? `${workspace.context_prompt.split('\n')[0].slice(0, 75)}...`
      : `当前沙盒已加载专属【${cleanName}】专家上下文，已就绪随时可进行专业对话。`,
    cards: [
      {
        icon: Sparkles,
        title: `梳理 ${cleanName} 执行 SOP`,
        subtitle: '拆解核心工作流、执行标准与阶段性交付物',
        prompt: `作为 ${cleanName}，请为我们当前所面临的核心任务梳理一份详尽的执行 SOP，包含标准实施流程与交付规范。`,
      },
      {
        icon: Search,
        title: '专业现状诊断与痛点排查',
        subtitle: '基于该领域专业标准评估现状与潜在风险',
        prompt: `请从 ${cleanName} 的专业视角，诊断我们目前方案或材料中存在的主要短板、潜在风险与优化空间。`,
      },
      {
        icon: Layers,
        title: '输出阶段性落地方案',
        subtitle: '制定可量化、可验证的多阶段推进执行路径',
        prompt: `请结合我们的实际资源，为该任务制定一份分阶段推进的实施方案，明确各里程碑的输入输出与检验指标。`,
      },
      {
        icon: CheckCircle2,
        title: '交付高标准成果物料',
        subtitle: '根据该专家职责产出结构化文档与执行模板',
        prompt: `请按照该领域的专业交付标准，直接输出可用于实际落地的完整交付成果与文档。`,
      },
    ],
  };
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