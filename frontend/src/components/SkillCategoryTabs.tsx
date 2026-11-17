import { useMemo } from 'react';

export const SKILL_CATEGORIES = [
  '办公与文档',
  '工程与架构',
  '视觉与设计',
  '调研与分析',
  '插件与扩展',
  '系统与自动化',
] as const;

interface CategorizableSkill {
  id?: string;
  name?: string;
  command?: string;
  category?: string;
}

/**
 * 解析技能分类：优先使用后端 category，缺省时按关键词做轻量兜底映射
 * @param skill - 技能对象
 * @returns 分类名称
 */
export function resolveSkillCategory(skill: CategorizableSkill): string {
  if (skill.category) return skill.category;
  const text = `${skill.id || ''} ${skill.command || ''} ${skill.name || ''}`.toLowerCase();
  if (/doc|word|excel|pptx|pdf|office|sheet|slide|文档|办公/.test(text)) return '办公与文档';
  if (/design|ui|ux|brand|banner|logo|视觉|设计/.test(text)) return '视觉与设计';
  if (/research|analy|调研|分析/.test(text)) return '调研与分析';
  if (/plugin|mcp|extension|扩展|插件/.test(text)) return '插件与扩展';
  if (/auto|schedule|system|cron|系统|自动化/.test(text)) return '系统与自动化';
  return '工程与架构';
}

export const ALL_CATEGORY = '全部';

/** 按分类过滤技能列表 */
export function filterSkillsByCategory<T extends CategorizableSkill>(items: T[], active: string): T[] {
  if (active === ALL_CATEGORY) return items;
  return items.filter((s) => resolveSkillCategory(s) === active);
}

interface SkillCategoryTabsProps {
  skills: CategorizableSkill[];
  active: string;
  onChange: (category: string) => void;
  compact?: boolean;
}

export function SkillCategoryTabs({ skills, active, onChange, compact = false }: SkillCategoryTabsProps) {
  const counts = useMemo(() => {
    const map = new Map<string, number>();
    skills.forEach((s) => {
      const c = resolveSkillCategory(s);
      map.set(c, (map.get(c) || 0) + 1);
    });
    return map;
  }, [skills]);

  const extra = Array.from(counts.keys()).filter((c) => !(SKILL_CATEGORIES as readonly string[]).includes(c));
  const tabs = [
    { key: ALL_CATEGORY, label: ALL_CATEGORY, count: skills.length },
    ...[...SKILL_CATEGORIES, ...extra]
      .filter((c) => (counts.get(c) || 0) > 0)
      .map((c) => ({ key: c, label: c, count: counts.get(c) || 0 })),
  ];

  return (
    <div
      className={`flex items-center overflow-x-auto no-scrollbar ${compact ? 'gap-1' : 'gap-1.5'}`}
      onMouseDown={(e) => e.preventDefault()}
    >
      {tabs.map((tab) => {
        const selected = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            onClick={() => onChange(tab.key)}
            className={`shrink-0 rounded-full border font-medium whitespace-nowrap transition-colors ${
              compact ? 'px-2 py-0.5 text-[10px]' : 'px-3 py-1 text-xs'
            } ${
              selected
                ? 'bg-primary text-primary-foreground border-primary'
                : 'bg-muted/40 text-muted-foreground border-border hover:bg-muted hover:text-foreground'
            }`}
          >
            {tab.label} ({tab.count})
          </button>
        );
      })}
    </div>
  );
}
