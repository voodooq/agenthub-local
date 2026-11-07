import { apiFetch } from './apiClient';

/**
 * 组装注入模型的动态系统指令 (System Instruction)
 * 包含：用户选中的性格模版 + 激活的增强技能包 + 自定义全局指令
 */
export async function buildActiveSystemOverride(): Promise<string | undefined> {
  try {
    let savedConfig: any = null;
    const local = localStorage.getItem('geminihub_personality_config');
    if (local) {
      try {
        savedConfig = JSON.parse(local);
      } catch (e) {}
    }

    // 若本地没有，则从服务端拉取一次
    let presets: any[] = [];
    let skills: any[] = [];

    const res = await apiFetch('/api/v1/settings/personality');
    if (res.ok) {
      const data = await res.json();
      presets = data.presets || [];
      skills = data.skills || [];
      if (!savedConfig && data.config) {
        savedConfig = data.config;
      }
    }

    if (!savedConfig) {
      savedConfig = {
        selected_persona_id: 'architect',
        active_skill_ids: ['deep_debugger'],
        custom_system_prompt: '',
      };
    }

    const currentPersona = presets.find((p) => p.id === savedConfig.selected_persona_id);
    const activeSkills = skills.filter((s) => (savedConfig.active_skill_ids || []).includes(s.id));

    const parts: string[] = [];

    // 1. 性格基底
    if (currentPersona && currentPersona.system_prompt) {
      parts.push(`【核心性格基底】\n${currentPersona.system_prompt.trim()}`);
    }

    // 2. 激活的增强技能
    if (activeSkills.length > 0) {
      const skillText = activeSkills.map((s) => s.prompt_fragment.trim()).join('\n\n');
      parts.push(`【激活技能包 (SKILLs)】\n${skillText}`);
    }

    // 3. 自定义补充指令
    if (savedConfig.custom_system_prompt && savedConfig.custom_system_prompt.trim()) {
      parts.push(`【用户自定义补充规则】\n${savedConfig.custom_system_prompt.trim()}`);
    }

    if (parts.length === 0) return undefined;
    return parts.join('\n\n--------------------\n\n');
  } catch (err) {
    console.warn('获取个性化性格配置失败:', err);
    return undefined;
  }
}

