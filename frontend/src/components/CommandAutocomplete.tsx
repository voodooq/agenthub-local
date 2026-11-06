'use client';

import React from 'react';
import { Cpu, Terminal, FolderGit2, Wrench, Sparkles, Zap } from 'lucide-react';

interface CommandItem {
  id: string;
  trigger: string;
  label: string;
  description: string;
  insertText: string;
  icon: any;
}

const COMMAND_LIST: CommandItem[] = [
  // @ 上下文引用
  {
    id: 'at_hardware',
    trigger: '@',
    label: '@hardware',
    description: '注入宿主机最新硬件指标快照 (CPU/内存/GPU/磁盘)',
    insertText: '@hardware 请分析当前硬件状态并评估项目承载力。',
    icon: Cpu,
  },
  {
    id: 'at_process',
    trigger: '@',
    label: '@process',
    description: '引用当前内存高占用进程列表进行深度诊断',
    insertText: '@process 帮我排查当前是否有僵尸或异常耗能进程。',
    icon: Terminal,
  },
  {
    id: 'at_workspace',
    trigger: '@',
    label: '@workspace',
    description: '引用当前绑定的本地项目目录上下文',
    insertText: '@workspace 请阅读当前项目架构并给出重构建议。',
    icon: FolderGit2,
  },
  // / 技能指令
  {
    id: 'slash_optimize_dev',
    trigger: '/',
    label: '/optimize-dev',
    description: '【开发专项】扫描脱离终端的孤儿 node/python 进程并释放端口',
    insertText: '/optimize-dev 请全面体检本地研发环境，排查端口冲突与编译缓存。',
    icon: Wrench,
  },
  {
    id: 'slash_optimize_design',
    trigger: '/',
    label: '/optimize-design',
    description: '【设计专项】分析显存(VRAM)与 Adobe/Blender 暂存盘空间',
    insertText: '/optimize-design 请进行显存护航，清理占用显存的后台程序。',
    icon: Sparkles,
  },
  {
    id: 'slash_free_port',
    trigger: '/',
    label: '/free-port',
    description: '排查指定本地端口 (如 3000, 8080) 被哪个进程死锁占用',
    insertText: '/free-port 3000 排查 3000 端口被谁占用并释放。',
    icon: Zap,
  },
  {
    id: 'slash_clean_cache',
    trigger: '/',
    label: '/clean-cache',
    description: '扫描 npm / pip / 系统 Temp 目录并生成勾选清理单',
    insertText: '/clean-cache 扫描本地开发包缓存与临时文件。',
    icon: Terminal,
  },
];

interface CommandAutocompleteProps {
  input: string;
  onSelect: (insertText: string) => void;
  onClose: () => void;
}

export function CommandAutocomplete({ input, onSelect, onClose }: CommandAutocompleteProps) {
  // 检查最后一个词是否以 @ 或 / 开头
  const words = input.split(/\s+/);
  const lastWord = words[words.length - 1] || '';

  if (!lastWord.startsWith('@') && !lastWord.startsWith('/')) {
    return null;
  }

  const trigger = lastWord[0];
  const filterKey = lastWord.slice(1).toLowerCase();

  const filtered = COMMAND_LIST.filter(
    (item) => item.trigger === trigger && item.label.toLowerCase().includes(filterKey)
  );

  if (filtered.length === 0) return null;

  return (
    <div className="absolute bottom-full left-0 mb-2 w-full max-w-md bg-surface-100/95 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl p-2 z-30 animate-scale-in">
      <div className="px-3 py-1.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wider border-b border-white/5 flex items-center justify-between">
        <span>快捷指令推荐 (@上下文 / /技能)</span>
        <span className="text-slate-500 font-mono">按 Esc 取消</span>
      </div>
      <div className="max-h-56 overflow-y-auto space-y-1 mt-1">
        {filtered.map((item) => {
          const Icon = item.icon;
          return (
            <div
              key={item.id}
              onClick={() => onSelect(item.insertText)}
              className="flex items-center space-x-2.5 p-2 rounded-xl hover:bg-white/10 cursor-pointer transition-colors group"
            >
              <div className="p-1.5 rounded-lg bg-surface-200 group-hover:bg-accent/20 text-slate-300 group-hover:text-accent-light transition-colors">
                <Icon className="w-3.5 h-3.5" />
              </div>
              <div className="flex-1 truncate">
                <div className="font-mono text-xs font-semibold text-slate-100 group-hover:text-accent-light">
                  {item.label}
                </div>
                <div className="text-[11px] text-slate-400 truncate">{item.description}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

