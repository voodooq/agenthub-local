/**
 * 场景自适应思考链生成引擎 (Scenario-Adaptive Thinking Engine)
 * 拒绝死板硬编码，根据用户具体提问内容、上下文意图及当前激活的性格/SKILL动态推演思考过程。
 */

export function generateScenarioThinking(prompt: string): string {
  if (!prompt || !prompt.trim()) {
    return '正在解析输入指令并初始化上下文环境...';
  }

  const p = prompt.toLowerCase();

  // 读取当前用户的性格偏好与技能（若有）
  let personaId = 'architect';
  let activeSkills: string[] = [];
  try {
    const raw = localStorage.getItem('geminihub_personality_config');
    if (raw) {
      const cfg = JSON.parse(raw);
      personaId = cfg.selected_persona_id || 'architect';
      activeSkills = cfg.active_skill_ids || [];
    }
  } catch (e) {}

  // 1. 极致极简特种兵性格专属极速思考
  if (personaId === 'minimalist') {
    return [
      '⚡ [极简特种兵模式 · 快速直通]',
      '1. 剥离客套与冗余修饰，直接提取核心指令',
      '2. 计算最小复杂度与最高效的可执行命令/代码块',
      '3. 直奔交付结果，不生成废话。',
    ].join('\n');
  }

  // 2. 场景匹配逻辑
  // 场景 A: Bug 排查与异常修复
  const isDebug =
    p.includes('bug') ||
    p.includes('error') ||
    p.includes('failed') ||
    p.includes('exception') ||
    p.includes('traceback') ||
    p.includes('报错') ||
    p.includes('失败') ||
    p.includes('超时') ||
    p.includes('崩溃') ||
    p.includes('排查') ||
    p.includes('为什么');

  if (isDebug) {
    const lines = [
      '🔍 [场景诊断：异常堆栈与根因深度排查]',
      '1. 逆向追溯异常上下文与现场物理触发时机',
      '2. 建立因果假设链（网络连通性 / 运行时类型 / 管道缓冲 / 边界越界）',
      '3. 排查潜在的隐式依赖与环境脱节因素',
      '4. 推导并验证最小侵入修复方案，确保不破坏既有业务逻辑。',
    ];
    if (activeSkills.includes('deep_debugger')) {
      lines.push('💡 触发【Deep Debugger】技能：追加防复发单元测试与边界防御策略。');
    }
    return lines.join('\n');
  }

  // 场景 B: 架构设计与系统拓扑
  const isArchitecture =
    p.includes('架构') ||
    p.includes('设计') ||
    p.includes('微服务') ||
    p.includes('选型') ||
    p.includes('重构') ||
    p.includes('高并发') ||
    p.includes('数据库') ||
    p.includes('表结构') ||
    p.includes('方案') ||
    p.includes('模块');

  if (isArchitecture) {
    return [
      '🏛️ [场景推演：系统架构与工程决策方案]',
      '1. 剖析核心业务吞吐、读写特征与边界约束条件',
      '2. 深度贯彻 KISS 原则：权衡组件复杂度，拒绝不必要的过度设计',
      '3. 规划模块边界（深模块设计：高内聚、低耦合、轻量接口）',
      '4. 评估扩展性、故障降级熔断通道与数据持久化一致性。',
    ].join('\n');
  }

  // 场景 C: UI / 前端与组件设计
  const isUI =
    p.includes('ui') ||
    p.includes('css') ||
    p.includes('页面') ||
    p.includes('组件') ||
    p.includes('样式') ||
    p.includes('tailwind') ||
    p.includes('前端') ||
    p.includes('布局') ||
    p.includes('动画') ||
    p.includes('弹窗') ||
    p.includes('按钮');

  if (isUI) {
    const lines = [
      '🎨 [场景推演：现代 UI/UX 体验与交互组件设计]',
      '1. 解析视觉层次（信息密度、色彩对比与高质感深色模式调优）',
      '2. 规划多端响应式断点与现代组件状态机（避免多余重渲染）',
      '3. 遵循无障碍设计（a11y）规范与流式微交互体验',
      '4. 编写轻量、声明式且高度语义化的 Tailwind CSS 组件。',
    ];
    if (activeSkills.includes('ui_ux_pro_max')) {
      lines.push('✨ 触发【UI/UX Pro】技能：注入毛玻璃拟态与平滑过渡微动效。');
    }
    return lines.join('\n');
  }

  // 场景 D: 算法推导与数学逻辑
  const isAlgorithm =
    p.includes('算法') ||
    p.includes('复杂度') ||
    p.includes('排序') ||
    p.includes('递归') ||
    p.includes('动态规划') ||
    p.includes('计算') ||
    p.includes('证明') ||
    p.includes('优化') ||
    p.includes('时间复杂度');

  if (isAlgorithm) {
    return [
      '🧮 [场景推演：算法复杂度极限与数学推导]',
      '1. 明确问题约束与极限边界（极端大数 / 空集合 / 临界越界）',
      '2. 对比各路径时间复杂度 ($O(N)$ vs $O(\\log N)$) 与额外空间占用',
      '3. 推导数学归纳与最优状态转移逻辑',
      '4. 输出兼顾可读性与执行效率的最优算法实现。',
    ].join('\n');
  }

  // 场景 E: 代码编写与功能落地
  const isCoding =
    p.includes('写') ||
    p.includes('代码') ||
    p.includes('实现') ||
    p.includes('开发') ||
    p.includes('函数') ||
    p.includes('脚本') ||
    p.includes('接口') ||
    p.includes('python') ||
    p.includes('typescript') ||
    p.includes('react');

  if (isCoding) {
    return [
      '💻 [场景推演：全栈功能实现与生产级代码落地]',
      '1. 厘清输入输出数据契约，严格标注 TypeScript / Python 类型注解',
      '2. 构思清晰的实现流程与异常保护分支（捕获明确异常类）',
      '3. 消除隐式依赖，产出整洁、无副作用的生产级代码块。',
    ].join('\n');
  }

  // 场景 F: 通用意图与深度思考
  return [
    '💡 [场景推演：意图理解与条理化推演]',
    '1. 提炼核心问题点与底层上下文背景',
    '2. 结合技术规范组织逻辑自洽、层次分明的解答',
    '3. 消除歧义并直接输出针对性解决方案。',
  ].join('\n');
}

