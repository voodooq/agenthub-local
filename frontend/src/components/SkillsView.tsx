'use client';

import React, { useEffect, useState } from 'react';
import {
  Layers,
  Sparkles,
  Plus,
  Search,
  BookOpen,
  Terminal,
  Database,
  RotateCw,
  Trash2,
} from 'lucide-react';
import { Workspace, SkillItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import { useLanguage } from '@/context/LanguageContext';
import { SopDrawer } from './SopDrawer';

interface SkillsViewProps {
  workspace: Workspace | undefined;
}

export function SkillsView({ workspace }: SkillsViewProps) {
  const { t } = useLanguage();
  const [activeTab, setActiveTab] = useState<'explore' | 'mcp' | 'skills'>('skills');
  const [searchQuery, setSearchQuery] = useState('');
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState<SkillItem | null>(null);
  const [isCreating, setIsCreating] = useState(false);

  // 创建 L2 技能的表单状态
  const [newSkillName, setNewSkillName] = useState('');
  const [newSkillCommand, setNewSkillCommand] = useState('');
  const [newSkillDesc, setNewSkillDesc] = useState('');
  const [newSkillSop, setNewSkillSop] = useState('');

  const fetchSkills = async () => {
    setLoading(true);
    try {
      const qs = workspace ? `?workspace_id=${workspace.id}` : '';
      const res = await apiFetch(`/api/v1/skills${qs}`);
      if (res.ok) {
        const data = await res.json();
        setSkills(data.skills || []);
      }
    } catch (e) {
      console.error('拉取技能失败:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSkills();
  }, [workspace?.id]);

  const handleCreateSkill = async () => {
    if (!workspace || !newSkillName.trim()) return;
    try {
      const res = await apiFetch('/api/v1/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspace_id: workspace.id,
          name: newSkillName.trim(),
          command: newSkillCommand.trim() || `/${newSkillName.trim()}`,
          description: newSkillDesc.trim(),
          sop_content: newSkillSop.trim() || '# SOP 执行规程\n\n1. 检查输入契约\n2. 受控执行',
        }),
      });
      if (res.ok) {
        setIsCreating(false);
        setNewSkillName('');
        setNewSkillCommand('');
        setNewSkillDesc('');
        setNewSkillSop('');
        await fetchSkills();
      }
    } catch (e) {
      console.error('创建技能失败:', e);
    }
  };

  const handleDeleteSkill = async (e: React.MouseEvent, skill: SkillItem) => {
    e.stopPropagation();
    if (!workspace || skill.scope !== 'workspace') return;
    if (!window.confirm(t(`确定要在当前工作区永久删除技能「${skill.name}」吗？相关物理文件将被移除。`))) {
      return;
    }
    try {
      const res = await apiFetch(`/api/v1/skills/${encodeURIComponent(skill.name)}?workspace_id=${workspace.id}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        if (selectedSkill?.name === skill.name) {
          setSelectedSkill(null);
        }
        await fetchSkills();
      } else {
        const data = await res.json().catch(() => ({}));
        alert(data.detail || t('删除技能失败'));
      }
    } catch (err) {
      console.error('删除技能失败:', err);
    }
  };

  const filteredSkills = skills.filter(
    (s) =>
      s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (s.command && s.command.toLowerCase().includes(searchQuery.toLowerCase())) ||
      (s.description && s.description.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  return (
    <div className="flex-1 h-full overflow-y-auto bg-background text-foreground p-6 md:p-10 select-text">
      <div className="max-w-6xl mx-auto space-y-8">
        {/* 顶部三标签栏 */}
        <div className="flex items-center justify-between border-b border-border pb-4">
          <div className="flex items-center space-x-6">
            <button
              onClick={() => setActiveTab('explore')}
              className={`text-sm pb-2 font-medium transition ${
                activeTab === 'explore'
                  ? 'text-foreground border-b-2 border-emerald-500 font-semibold'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {t('探索')}
            </button>
            <button
              onClick={() => setActiveTab('mcp')}
              className={`text-sm pb-2 font-medium transition ${
                activeTab === 'mcp'
                  ? 'text-foreground border-b-2 border-emerald-500 font-semibold'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {t('关联应用 (MCP)')}
            </button>
            <button
              onClick={() => setActiveTab('skills')}
              className={`text-sm pb-2 font-medium transition ${
                activeTab === 'skills'
                  ? 'text-foreground border-b-2 border-emerald-500 font-semibold'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {t('技能 (Skills)')}
            </button>
          </div>

          <div className="flex items-center space-x-3">
            {activeTab === 'skills' && (
              <button
                onClick={fetchSkills}
                disabled={loading}
                title={t('重新扫描工作区与全局底座技能')}
                className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface-100 hover:bg-surface-200 text-foreground text-xs transition disabled:opacity-50"
              >
                <RotateCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-emerald-500' : ''}`} />
                <span>{t('扫描同步')}</span>
              </button>
            )}

            {activeTab === 'skills' && workspace && (
              <button
                onClick={() => setIsCreating(!isCreating)}
                className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium transition shadow-sm"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>{isCreating ? t('取消') : t('创建工作区专属技能')}</span>
              </button>
            )}
          </div>
        </div>

        {/* 新建工作区专属技能表单 */}
        {isCreating && (
          <div className="p-5 rounded-2xl border border-border bg-surface-card shadow-md space-y-4 animate-in fade-in duration-200">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-emerald-600 dark:text-emerald-400">
                {t('在当前工作区定义 L2 专属技能')} (<code>skills/&lt;name&gt;/SKILL.md</code>)
              </h3>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">{t('技能英文标识 (Folder Name)')}</label>
                <input
                  type="text"
                  value={newSkillName}
                  onChange={(e) => setNewSkillName(e.target.value)}
                  placeholder="e.g. email-assistant"
                  className="w-full text-xs font-mono px-3 py-2 bg-surface-200 border border-border rounded-lg text-foreground focus:outline-none focus:border-accent"
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">{t('呼出指令 (Command)')}</label>
                <input
                  type="text"
                  value={newSkillCommand}
                  onChange={(e) => setNewSkillCommand(e.target.value)}
                  placeholder="e.g. /mail-assistant"
                  className="w-full text-xs font-mono px-3 py-2 bg-surface-200 border border-border rounded-lg text-foreground focus:outline-none focus:border-accent"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs text-muted-foreground mb-1">{t('功能简述')}</label>
              <input
                type="text"
                value={newSkillDesc}
                onChange={(e) => setNewSkillDesc(e.target.value)}
                placeholder={t('说明该技能在何时触发与解决什么问题...')}
                className="w-full text-xs px-3 py-2 bg-surface-200 border border-border rounded-lg text-foreground focus:outline-none focus:border-accent"
              />
            </div>

            <div>
              <label className="block text-xs text-muted-foreground mb-1">{t('SOP 明文执行规程 (Markdown)')}</label>
              <textarea
                value={newSkillSop}
                onChange={(e) => setNewSkillSop(e.target.value)}
                placeholder="# 技能执行规程&#10;&#10;1. 检查环境变量...&#10;2. 调取数据并输出差异报告..."
                rows={4}
                className="w-full text-xs font-mono p-3 bg-surface-200 border border-border rounded-lg text-foreground focus:outline-none focus:border-accent"
              />
            </div>

            <div className="flex justify-end space-x-2 pt-2">
              <button
                onClick={() => setIsCreating(false)}
                className="px-3 py-1.5 rounded-lg bg-surface-100 hover:bg-surface-200 text-xs text-foreground border border-border transition"
              >
                {t('取消')}
              </button>
              <button
                onClick={handleCreateSkill}
                disabled={!newSkillName.trim()}
                className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-medium text-white disabled:opacity-50 transition shadow-sm"
              >
                {t('确认创建')}
              </button>
            </div>
          </div>
        )}

        {/* 技能卡片列表 */}
        {activeTab === 'skills' && (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-xl font-bold text-foreground">{t('技能中枢')}</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {t('已加载 L1 全局底座技能与当前工作区 L2 专属技能 (支持同名高优覆盖)')}
                </p>
              </div>

              <div className="relative">
                <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t('搜索技能或指令...')}
                  className="pl-8 pr-3 py-1.5 text-xs bg-surface-card border border-border rounded-lg text-foreground placeholder-muted-foreground focus:outline-none focus:border-accent w-52 transition"
                />
              </div>
            </div>

            {loading ? (
              <div className="text-center py-12 text-muted-foreground text-xs flex items-center justify-center space-x-2">
                <RotateCw className="w-4 h-4 animate-spin text-emerald-500" />
                <span>{t('正在同步与加载技能列表...')}</span>
              </div>
            ) : filteredSkills.length === 0 ? (
              <div className="text-center py-16 border border-dashed border-border rounded-2xl text-muted-foreground text-xs bg-surface-card/40">
                {t('暂未检索到技能')}
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {filteredSkills.map((sk) => {
                  const displayCmd = sk.command?.trim()
                    ? (sk.command.startsWith('/') ? sk.command : `/${sk.command}`)
                    : `/${sk.name}`;
                  return (
                    <div
                      key={sk.id}
                      onClick={() => setSelectedSkill(sk)}
                      className="group p-4 rounded-xl border border-border bg-surface-card hover:bg-surface-50 hover:border-border-hover transition flex items-start justify-between cursor-pointer relative shadow-sm"
                    >
                      <div className="flex items-start space-x-3 min-w-0 flex-1 pr-2">
                        <div className="p-2.5 rounded-lg bg-surface-200 text-foreground flex-shrink-0 group-hover:text-emerald-500 transition">
                          <BookOpen className="w-5 h-5" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center space-x-2">
                            <h4 className="text-sm font-semibold text-foreground truncate group-hover:text-emerald-600 dark:group-hover:text-emerald-400 transition">
                              {sk.name}
                            </h4>
                            {sk.scope === 'workspace' ? (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 flex-shrink-0 font-medium">
                                {sk.is_override ? t('工作区重写') : t('工作区专属')}
                              </span>
                            ) : (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-200 text-muted-foreground border border-border flex-shrink-0">
                                {t('全局底座')}
                              </span>
                            )}
                          </div>

                          <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                            {sk.description || t('暂无描述')}
                          </p>
                        </div>
                      </div>

                      <div className="flex flex-col items-end space-y-2 flex-shrink-0">
                        <span className="px-2 py-0.5 rounded font-mono text-xs font-semibold bg-surface-200 text-emerald-600 dark:text-emerald-400 border border-border group-hover:border-emerald-500/50 transition">
                          {displayCmd}
                        </span>

                        {sk.scope === 'workspace' && workspace && (
                          <button
                            onClick={(e) => handleDeleteSkill(e, sk)}
                            title={t('从工作区删除此技能')}
                            className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-rose-500/15 text-muted-foreground hover:text-rose-600 transition"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* MCP 关联应用 */}
        {activeTab === 'mcp' && (
          <div className="space-y-4">
            <h2 className="text-xl font-bold text-foreground">{t('已关联应用与 MCP 服务')}</h2>
            <p className="text-xs text-muted-foreground">
              {t('连接外部开发工具与数据协议，为 AI 具身操作赋能')}
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
              <div className="p-4 rounded-xl border border-border bg-surface-card flex items-center justify-between shadow-sm">
                <div className="flex items-center space-x-3">
                  <Terminal className="w-5 h-5 text-emerald-500" />
                  <div>
                    <h4 className="text-sm font-semibold text-foreground">Local Shell / Terminal</h4>
                    <p className="text-xs text-muted-foreground">{t('受控本地终端执行能力')}</p>
                  </div>
                </div>
                <span className="text-xs text-emerald-600 dark:text-emerald-400 font-mono">● {t('已启用')}</span>
              </div>
              <div className="p-4 rounded-xl border border-border bg-surface-card flex items-center justify-between shadow-sm">
                <div className="flex items-center space-x-3">
                  <Database className="w-5 h-5 text-blue-500" />
                  <div>
                    <h4 className="text-sm font-semibold text-foreground">SQLite FTS5 Local Memory</h4>
                    <p className="text-xs text-muted-foreground">{t('工作区零拷贝全文索引')}</p>
                  </div>
                </div>
                <span className="text-xs text-emerald-600 dark:text-emerald-400 font-mono">● {t('已就绪')}</span>
              </div>
            </div>
          </div>
        )}

        {/* 探索市场 */}
        {activeTab === 'explore' && (
          <div className="space-y-4">
            <h2 className="text-xl font-bold text-foreground">{t('探索与扩展技能')}</h2>
            <p className="text-xs text-muted-foreground">
              {t('在当前工作区自定义技能或扩展 MCP 工具链')}
            </p>
            <div className="p-8 border border-dashed border-border rounded-2xl text-center bg-surface-card/40">
              <Sparkles className="w-8 h-8 text-emerald-500 mx-auto mb-2" />
              <p className="text-sm text-foreground font-medium">
                {t('支持导入 Cursor / Claude 格式的 Skill 与 MCP')}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {t('在设置页面中可一键载入外部规则或扩展包')}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* SOP 明文抽屉 */}
      <SopDrawer
        skill={selectedSkill}
        workspaceId={workspace?.id}
        onClose={() => setSelectedSkill(null)}
        onSkillDeleted={() => fetchSkills()}
      />
    </div>
  );
}
