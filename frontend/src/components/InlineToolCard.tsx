'use client';

import React, { useState } from 'react';
import {
  Terminal,
  FileCode,
  FileText,
  FileSearch,
  Globe,
  CheckCircle2,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Loader2,
  ShieldAlert,
  Copy,
  Check,
  Wrench,
} from 'lucide-react';
import { ToolExecutionCardItem } from '@/types';
import { DiffViewerCard } from './DiffViewerCard';

interface InlineToolCardProps {
  events: ToolExecutionCardItem[];
  onApprove: (callId: string, approved: boolean) => void;
}

/**
 * 智能解析工具调用类型、显示标题与图标色彩
 */
function resolveToolMeta(ev: ToolExecutionCardItem) {
  const name = (ev.name || '').toLowerCase();
  const cmd = (ev.command || '').trim();
  const comp = (ev.compressed || '').trim();

  // 1.0 精准代码局部补丁 (replace_in_file)
  if (
    name.includes('replace_in_file') ||
    comp.includes('局部修改') ||
    comp.includes('补丁') ||
    cmd.startsWith('replace_in_file')
  ) {
    let filePath = ev.file_path || '';
    if (!filePath) {
      const match = comp.match(/[`'"]([^`'"]+)[`'"]/);
      if (match) filePath = match[1];
      else if (cmd && !cmd.includes(' ')) filePath = cmd.replace('replace_in_file(', '').replace(')', '');
    }
    return {
      type: 'replace_in_file',
      label: '局部补丁',
      target: filePath,
      Icon: Wrench,
      colorClass: 'text-emerald-500 dark:text-emerald-400',
      badgeBg: 'bg-emerald-500/10 border-emerald-500/25 text-emerald-700 dark:text-emerald-300',
      actionText: comp || `已应用局部补丁 ${filePath}`,
    };
  }

  // 1.1 代码骨架提取 (get_repo_skeleton)
  if (name.includes('get_repo_skeleton') || comp.includes('骨架')) {
    const target = cmd.replace('get_repo_skeleton(', '').replace(')', '') || '.';
    return {
      type: 'get_repo_skeleton',
      label: '代码骨架',
      target: target,
      Icon: FileSearch,
      colorClass: 'text-indigo-500 dark:text-indigo-400',
      badgeBg: 'bg-indigo-500/10 border-indigo-500/25 text-indigo-700 dark:text-indigo-300',
      actionText: comp || `提取代码骨架: ${target}`,
    };
  }

  // 1.2 物理文件落盘 / 写入
  if (
    name.includes('write_local_file') ||
    comp.includes('落盘') ||
    comp.includes('写入') ||
    cmd.startsWith('write_local_file')
  ) {
    let filePath = '';
    const match = comp.match(/['"]([^'"]+)['"]/);
    if (match) {
      filePath = match[1];
    } else if (cmd && !cmd.includes(' ')) {
      filePath = cmd;
    }
    return {
      type: 'write_file',
      label: '文件落盘',
      target: filePath,
      Icon: FileCode,
      colorClass: 'text-sky-500 dark:text-sky-400',
      badgeBg: 'bg-sky-500/10 border-sky-500/25 text-sky-700 dark:text-sky-300',
      actionText: comp || `已落盘文件 ${cmd}`,
    };
  }

  // 2. 本地文件读取
  if (
    name.includes('read_local_file') ||
    name.includes('read_tool_ref') ||
    comp.includes('读取') ||
    cmd.startsWith('read_local_file')
  ) {
    let filePath = '';
    const match = comp.match(/['"]([^'"]+)['"]/);
    if (match) {
      filePath = match[1];
    } else if (cmd && !cmd.includes(' ')) {
      filePath = cmd;
    }
    return {
      type: 'read_file',
      label: '文件读取',
      target: filePath,
      Icon: FileText,
      colorClass: 'text-amber-500 dark:text-amber-400',
      badgeBg: 'bg-amber-500/10 border-amber-500/25 text-amber-700 dark:text-amber-300',
      actionText: comp || `读取文件 ${cmd}`,
    };
  }

  // 3. 知识库或技能检索
  if (
    name.includes('search') ||
    name.includes('find') ||
    comp.includes('检索') ||
    comp.includes('搜索')
  ) {
    return {
      type: 'search',
      label: '检索查询',
      target: '',
      Icon: FileSearch,
      colorClass: 'text-purple-500 dark:text-purple-400',
      badgeBg: 'bg-purple-500/10 border-purple-500/25 text-purple-700 dark:text-purple-300',
      actionText: comp || cmd,
    };
  }

  // 4. 网络抓取
  if (name.includes('web') || comp.includes('网络') || cmd.startsWith('http')) {
    return {
      type: 'web',
      label: '网络请求',
      target: '',
      Icon: Globe,
      colorClass: 'text-indigo-500 dark:text-indigo-400',
      badgeBg: 'bg-indigo-500/10 border-indigo-500/25 text-indigo-700 dark:text-indigo-300',
      actionText: comp || cmd,
    };
  }

  // 5. 本地终端命令 (默认)
  return {
    type: 'command',
    label: '终端命令',
    target: '',
    Icon: Terminal,
    colorClass: 'text-emerald-600 dark:text-emerald-400',
    badgeBg: 'bg-emerald-500/10 border-emerald-500/25 text-emerald-700 dark:text-emerald-300',
    actionText: comp || (cmd ? `$ ${cmd}` : '执行命令'),
  };
}

/**
 * 单步工具调用精美卡片（直显命令与输出，短输出无需二级点击即可一览无余）
 */
function SingleEventCard({ ev }: { ev: ToolExecutionCardItem }) {
  const [isOutputExpanded, setIsOutputExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const meta = resolveToolMeta(ev);
  const { Icon } = meta;

  const isRunning = ev.status === 'running';
  const isDenied = ev.status === 'denied' || ev.status === 'rejected';
  const isCompleted = ev.status === 'completed';

  const rawOutput = (ev.output || '').trim();
  const outputLines = rawOutput ? rawOutput.split('\n') : [];
  const isLongOutput = outputLines.length > 4 || rawOutput.length > 280;
  const visibleOutput = !isLongOutput || isOutputExpanded ? rawOutput : outputLines.slice(0, 4).join('\n');

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!ev.command) return;
    navigator.clipboard.writeText(ev.command).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      className={`rounded-xl border overflow-hidden text-xs transition-all ${
        isRunning
          ? 'border-emerald-500/40 bg-surface-100/90 shadow-sm'
          : isDenied
            ? 'border-rose-500/35 bg-surface-100/85'
            : 'border-border/75 bg-surface-100/75 hover:border-border transition-colors'
      }`}
    >
      {/* 卡片头部行 */}
      <div className="px-3.5 py-2 flex items-center justify-between border-b border-border/40 bg-surface-200/40 dark:bg-surface-200/20">
        <div className="flex items-center space-x-2 min-w-0 flex-1">
          <div className={`p-1 rounded-md shrink-0 ${meta.badgeBg}`}>
            <Icon className={`w-3.5 h-3.5 ${meta.colorClass}`} />
          </div>
          <span className="font-semibold text-foreground text-xs shrink-0">
            {meta.label}
          </span>
          {meta.target ? (
            <code className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-surface-300/40 dark:bg-surface-300/20 text-foreground truncate max-w-[280px] sm:max-w-[420px]">
              {meta.target}
            </code>
          ) : (
            <span className="font-mono text-[11px] text-muted-foreground truncate">
              {meta.actionText}
            </span>
          )}
        </div>

        {/* 右侧状态指示器 */}
        <div className="flex items-center space-x-1.5 shrink-0 ml-2">
          {isRunning ? (
            <div className="flex items-center space-x-1 text-emerald-600 dark:text-emerald-400 font-medium">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              <span className="text-[11px]">执行中...</span>
            </div>
          ) : isDenied ? (
            <div className="flex items-center space-x-1 text-rose-500 font-medium">
              <ShieldAlert className="w-3.5 h-3.5" />
              <span className="text-[11px]">已拦截</span>
            </div>
          ) : isCompleted ? (
            <div className="flex items-center space-x-1.5 text-muted-foreground">
              {typeof ev.exit_code === 'number' && ev.exit_code !== 0 ? (
                <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30">
                  退出码: {ev.exit_code}
                </span>
              ) : (
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
              )}
              <span className="text-[11px] text-emerald-600 dark:text-emerald-400 font-medium">完成</span>
            </div>
          ) : null}
        </div>
      </div>

      {/* 命令行区 (如果存在独立终端命令) */}
      {ev.command && (meta.type === 'command' || ev.command !== meta.target) && (
        <div className="px-3.5 py-1.5 bg-black/85 dark:bg-zinc-950/90 text-zinc-200 font-mono text-[11px] flex items-center justify-between border-b border-zinc-800/80">
          <div className="flex items-center space-x-2 truncate flex-1 min-w-0 mr-2">
            <span className="text-emerald-400 font-bold select-none">$</span>
            <span className="truncate select-all text-zinc-100">{ev.command}</span>
          </div>
          <button
            type="button"
            onClick={handleCopy}
            className="text-zinc-500 hover:text-zinc-300 p-0.5 rounded transition-colors shrink-0"
            title="复制命令行"
          >
            {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
          </button>
        </div>
      )}

      {/* 代码补丁红绿 Diff 呈现区 */}
      {(ev.unified_diff || (rawOutput.includes('```diff\n') && rawOutput.includes('@@'))) && (
        <div className="px-3.5 py-1.5 bg-black/85 dark:bg-zinc-950/90 border-t border-zinc-800/60">
          <DiffViewerCard
            filePath={meta.target || ev.file_path || 'file'}
            unifiedDiff={ev.unified_diff || (rawOutput.match(/```diff\n([\s\S]*?)\n```/)?.[1] || '')}
            snapshotTreeHash={ev.snapshot_tree_hash || (rawOutput.match(/快照 Hash:\s*([a-f0-9]{40})/i)?.[1])}
          />
        </div>
      )}

      {/* 输出内容呈现区 */}
      {rawOutput && !ev.unified_diff && !rawOutput.includes('```diff\n') ? (
        <div className="px-3.5 py-2 bg-black/80 dark:bg-zinc-950/80 text-zinc-300 font-mono text-[11px] leading-relaxed">
          <div className="whitespace-pre-wrap break-all max-h-56 overflow-y-auto selection:bg-emerald-500/30">
            {visibleOutput}
          </div>
          {isLongOutput && (
            <button
              type="button"
              onClick={() => setIsOutputExpanded(!isOutputExpanded)}
              className="mt-1.5 text-[10px] text-emerald-400 hover:text-emerald-300 inline-flex items-center space-x-1 font-sans font-medium"
            >
              {isOutputExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              <span>{isOutputExpanded ? '收起输出' : `展开完整输出 (共 ${outputLines.length} 行)`}</span>
            </button>
          )}
        </div>
      ) : ev.compressed && meta.type !== 'command' && !ev.unified_diff && !rawOutput.includes('```diff\n') ? (
        <div className="px-3.5 py-1.5 bg-surface-100/60 text-[11px] text-muted-foreground font-mono">
          {ev.compressed}
        </div>
      ) : null}
    </div>
  );
}

export function InlineToolCard({ events, onApprove }: InlineToolCardProps) {
  if (!events || events.length === 0) return null;

  // 待用户审批的高危命令始终独立高亮展示
  const pendingApprovals = events.filter((e) => e.status === 'require_approval');
  const regularEvents = events.filter((e) => e.status !== 'require_approval');

  const runningCount = regularEvents.filter((e) => e.status === 'running').length;
  const deniedCount = regularEvents.filter(
    (e) => e.status === 'denied' || e.status === 'rejected'
  ).length;
  const completedCount = regularEvents.filter((e) => e.status === 'completed').length;

  // 多事件时：若有正在运行中的步骤，默认展开；否则由用户按需展开
  const [isGroupExpanded, setIsGroupExpanded] = useState<boolean>(runningCount > 0);

  return (
    <div className="w-full max-w-[94%] md:max-w-[88%] space-y-2 my-1">
      {/* 1. 待人工审批项：独立显眼高危警告卡片 */}
      {pendingApprovals.map((ev) => (
        <div
          key={ev.call_id}
          className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3.5 space-y-2.5 shadow-sm animate-fade-in"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2 text-amber-600 dark:text-amber-400 text-xs font-bold">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>安全沙箱拦截：Agent 申请执行本机终端命令</span>
            </div>
            {ev.cwd && (
              <span className="text-[11px] text-muted-foreground font-mono truncate max-w-[220px]">
                目录: {ev.cwd}
              </span>
            )}
          </div>

          {ev.reason && (
            <p className="text-xs text-foreground/90">
              <span className="text-muted-foreground">执行目的：</span>
              {ev.reason}
            </p>
          )}

          <div className="px-3 py-2 rounded-lg bg-black/80 text-emerald-400 font-mono text-xs break-all">
            $ {ev.command}
          </div>

          <div className="flex items-center justify-end space-x-2 pt-1">
            <button
              type="button"
              onClick={() => onApprove(ev.call_id, false)}
              className="px-3 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 text-rose-600 dark:text-rose-400 text-xs font-semibold transition-colors"
            >
              拒绝执行
            </button>
            <button
              type="button"
              onClick={() => onApprove(ev.call_id, true)}
              className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition-colors shadow-sm"
            >
              允许执行
            </button>
          </div>
        </div>
      ))}

      {/* 2. 常规执行步骤：根据步骤数自适应单步直出或多步汇总 */}
      {regularEvents.length === 1 ? (
        // 核心解法：单步直接免折叠呈现！让命令、落盘路径和执行结论 100% 眼前可见
        <SingleEventCard ev={regularEvents[0]} />
      ) : regularEvents.length > 1 ? (
        // 多步场景：总览汇总栏 + 可展开分步明细列表
        <div
          className={`rounded-xl border overflow-hidden text-xs transition-all ${
            runningCount > 0
              ? 'border-emerald-500/35 bg-surface-100/90'
              : deniedCount > 0
                ? 'border-rose-500/30 bg-surface-100/85'
                : 'border-border/75 bg-surface-100/75'
          }`}
        >
          {/* 顶部总览栏 */}
          <button
            type="button"
            onClick={() => setIsGroupExpanded((prev) => !prev)}
            className="w-full px-3.5 py-2 flex items-center justify-between hover:bg-surface-200/50 transition-colors text-left"
          >
            <div className="flex items-center space-x-2 min-w-0 flex-1">
              {runningCount > 0 ? (
                <Loader2 className="w-3.5 h-3.5 text-emerald-500 animate-spin shrink-0" />
              ) : deniedCount > 0 ? (
                <ShieldAlert className="w-3.5 h-3.5 text-rose-500 shrink-0" />
              ) : (
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
              )}

              <span className="font-medium text-foreground truncate">
                执行步骤详情 · 共 {regularEvents.length} 步
                <span className="text-muted-foreground font-mono ml-2">
                  (完成 {completedCount}
                  {runningCount > 0 ? ` · 运行 ${runningCount}` : ''}
                  {deniedCount > 0 ? ` · 拦截 ${deniedCount}` : ''})
                </span>
              </span>
            </div>

            <div className="flex items-center space-x-1.5 shrink-0 ml-2 text-muted-foreground">
              <span className="text-[11px] font-medium">
                {isGroupExpanded ? '收起步骤' : `展开步骤 (${regularEvents.length})`}
              </span>
              {isGroupExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </div>
          </button>

          {/* 展开后的分步清单 */}
          {isGroupExpanded && (
            <div className="border-t border-border/50 divide-y divide-border/40 p-2 space-y-2 bg-background/30 max-h-[420px] overflow-y-auto">
              {regularEvents.map((ev, stepIdx) => (
                <div key={ev.call_id || stepIdx} className="space-y-1">
                  <div className="flex items-center space-x-2 px-1 text-[11px] text-muted-foreground font-mono">
                    <span className="px-1.5 py-0.2 rounded bg-surface-200 text-muted-foreground/80 font-bold">
                      步骤 #{stepIdx + 1}
                    </span>
                  </div>
                  <SingleEventCard ev={ev} />
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
