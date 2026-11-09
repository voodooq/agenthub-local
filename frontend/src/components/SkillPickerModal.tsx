'use client';

import React, { useEffect, useState } from 'react';
import { Folder, Globe, FileText, Search, X } from 'lucide-react';
import { SkillItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import { useLanguage } from '@/context/LanguageContext';

interface SkillPickerModalProps {
  workspaceId: string;
  isOpen: boolean;
  onClose: () => void;
  onSelectSkill: (command: string) => void;
  onViewSop: (skill: SkillItem) => void;
}

export function SkillPickerModal({
  workspaceId,
  isOpen,
  onClose,
  onSelectSkill,
  onViewSop,
}: SkillPickerModalProps) {
  const { t } = useLanguage();
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    const fetchSkills = async () => {
      setLoading(true);
      try {
        const res = await apiFetch(`/api/v1/skills?workspace_id=${workspaceId}`);
        if (res.ok) {
          const data = await res.json();
          setSkills(data.skills || []);
        }
      } catch (e) {
        console.error('获取技能列表失败:', e);
      } finally {
        setLoading(false);
      }
    };
    fetchSkills();
    setSearch('');
  }, [isOpen, workspaceId]);

  if (!isOpen) return null;

  const getDisplayCommand = (sk: SkillItem): string => {
    if (sk.command?.trim()) {
      return sk.command.trim().startsWith('/') ? sk.command.trim() : `/${sk.command.trim()}`;
    }
    return `/${sk.name}`;
  };

  const filtered = skills.filter((s) => {
    const term = search.toLowerCase();
    const cmd = getDisplayCommand(s).toLowerCase();
    return (
      s.name.toLowerCase().includes(term) ||
      cmd.includes(term) ||
      (s.description && s.description.toLowerCase().includes(term))
    );
  });

  const workspaceSkills = filtered.filter((s) => s.scope === 'workspace');
  const globalSkills = filtered.filter((s) => s.scope === 'global');

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      onClose();
    } else if (e.key === 'Enter' && filtered.length > 0) {
      const topSkill = filtered[0];
      const cmd = getDisplayCommand(topSkill);
      onSelectSkill(`${cmd} `);
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-150 select-none"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-2xl bg-surface-card border border-border shadow-2xl overflow-hidden flex flex-col max-h-[80vh] text-foreground"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        {/* 搜索头 */}
        <div className="p-3.5 border-b border-border flex items-center space-x-2 bg-surface-50">
          <span className="text-muted-foreground font-mono text-sm pl-1">/</span>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('搜索技能或输入指令（按 Enter 快速回填第一项）...')}
            className="flex-1 bg-transparent text-sm text-foreground placeholder-muted-foreground focus:outline-none"
            autoFocus
          />
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-surface-200 text-muted-foreground hover:text-foreground transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* 技能列表 */}
        <div className="flex-1 overflow-y-auto p-3 space-y-4">
          {loading ? (
            <div className="text-center py-8 text-xs text-muted-foreground">{t('正在加载技能...')}</div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-8 text-xs text-muted-foreground">{t('未找到匹配技能')}</div>
          ) : (
            <>
              {workspaceSkills.length > 0 && (
                <div>
                  <div className="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider px-2 mb-1.5 flex items-center space-x-1.5">
                    <Folder className="w-3.5 h-3.5" />
                    <span>{t('当前工作区专属技能')}</span>
                  </div>
                  <div className="space-y-1">
                    {workspaceSkills.map((sk) => {
                      const displayCmd = getDisplayCommand(sk);
                      return (
                        <div
                          key={sk.id}
                          className="group flex items-center justify-between p-2 rounded-xl hover:bg-surface-100 border border-transparent hover:border-emerald-500/30 transition cursor-pointer"
                          onClick={() => {
                            onSelectSkill(`${displayCmd} `);
                            onClose();
                          }}
                        >
                          <div className="flex items-center space-x-2.5 min-w-0 pr-2">
                            <span className="font-mono text-xs font-semibold text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20 flex-shrink-0">
                              {displayCmd}
                            </span>
                            <span className="text-xs text-foreground truncate">{sk.description || sk.name}</span>
                            {sk.is_override && (
                              <span className="text-[10px] bg-amber-500/20 text-amber-600 dark:text-amber-300 border border-amber-500/30 px-1.5 py-0.2 rounded flex-shrink-0">
                                {t('覆盖全局')}
                              </span>
                            )}
                          </div>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onViewSop(sk);
                            }}
                            className="opacity-0 group-hover:opacity-100 text-[11px] px-2 py-1 rounded bg-surface-200 hover:bg-surface-300 text-foreground transition flex-shrink-0 flex items-center space-x-1"
                          >
                            <FileText className="w-3 h-3" />
                            <span>{t('SOP')}</span>
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {globalSkills.length > 0 && (
                <div>
                  <div className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider px-2 mb-1.5 flex items-center space-x-1.5">
                    <Globe className="w-3.5 h-3.5" />
                    <span>{t('全局通用技能')}</span>
                  </div>
                  <div className="space-y-1">
                    {globalSkills.map((sk) => {
                      const displayCmd = getDisplayCommand(sk);
                      return (
                        <div
                          key={sk.id}
                          className="group flex items-center justify-between p-2 rounded-xl hover:bg-surface-100 border border-transparent hover:border-border transition cursor-pointer"
                          onClick={() => {
                            onSelectSkill(`${displayCmd} `);
                            onClose();
                          }}
                        >
                          <div className="flex items-center space-x-2.5 min-w-0 pr-2">
                            <span className="font-mono text-xs font-semibold text-blue-600 dark:text-blue-400 bg-blue-500/10 px-2 py-0.5 rounded border border-blue-500/20 flex-shrink-0">
                              {displayCmd}
                            </span>
                            <span className="text-xs text-foreground truncate">{sk.description || sk.name}</span>
                          </div>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onViewSop(sk);
                            }}
                            className="opacity-0 group-hover:opacity-100 text-[11px] px-2 py-1 rounded bg-surface-200 hover:bg-surface-300 text-foreground transition flex-shrink-0 flex items-center space-x-1"
                          >
                            <FileText className="w-3 h-3" />
                            <span>{t('SOP')}</span>
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
