'use client';

import React, { useEffect, useState } from 'react';
import {
  Clock,
  Play,
  Pause,
  Plus,
  Trash2,
  CheckCircle2,
  AlertCircle,
  MessageSquare,
  Loader2,
  History,
  X,
  Timer,
  CalendarClock,
  ChevronRight,
} from 'lucide-react';
import { Workspace, ScheduledTaskItem, ScheduledTaskRunItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import { useLanguage } from '@/context/LanguageContext';
import { ScheduledTaskDialogModal } from './ScheduledTaskDialogModal';

interface ScheduledViewProps {
  workspace: Workspace | undefined;
}

function formatDateTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatDuration(ms?: number | null): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function ScheduledView({ workspace }: ScheduledViewProps) {
  const { t } = useLanguage();
  const [tasks, setTasks] = useState<ScheduledTaskItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [runningTaskId, setRunningTaskId] = useState<string | null>(null);

  // 任务详情与运行历史弹窗状态
  const [detailTask, setDetailTask] = useState<ScheduledTaskItem | null>(null);
  const [taskRuns, setTaskRuns] = useState<ScheduledTaskRunItem[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const fetchTasks = async () => {
    if (!workspace) return;
    setLoading(true);
    try {
      const res = await apiFetch(`/api/v1/scheduled?workspace_id=${workspace.id}`);
      if (res.ok) {
        const data = await res.json();
        const nextTasks: ScheduledTaskItem[] = data.tasks || [];
        setTasks(nextTasks);
        // 若详情弹窗正开着，同步更新该任务的最新摘要信息
        if (detailTask) {
          const updated = nextTasks.find((item) => item.id === detailTask.id);
          if (updated) setDetailTask(updated);
        }
      }
    } catch (e) {
      console.error('拉取计划任务失败:', e);
    } finally {
      setLoading(false);
    }
  };

  const fetchTaskRuns = async (taskId: string) => {
    setRunsLoading(true);
    try {
      const res = await apiFetch(`/api/v1/scheduled/${taskId}/runs?limit=20`);
      if (res.ok) {
        const data = await res.json();
        const runs: ScheduledTaskRunItem[] = data.runs || [];
        setTaskRuns(runs);
        setSelectedRunId(runs[0]?.id ?? null);
      }
    } catch (e) {
      console.error('拉取运行历史失败:', e);
    } finally {
      setRunsLoading(false);
    }
  };

  useEffect(() => {
    fetchTasks();
  }, [workspace?.id]);

  const handleOpenDetail = (task: ScheduledTaskItem) => {
    setDetailTask(task);
    setTaskRuns([]);
    setSelectedRunId(null);
    fetchTaskRuns(task.id);
  };

  const handleToggleStatus = async (task: ScheduledTaskItem) => {
    const nextStatus = task.status === 'active' ? 'paused' : 'active';
    try {
      await apiFetch(`/api/v1/scheduled/${task.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: nextStatus }),
      });
      fetchTasks();
    } catch (e) {
      console.error('切换任务状态失败:', e);
    }
  };

  const handleRunNow = async (taskId: string) => {
    setRunningTaskId(taskId);
    try {
      await apiFetch(`/api/v1/scheduled/${taskId}/run`, { method: 'POST' });
      await fetchTasks();
      if (detailTask?.id === taskId) {
        await fetchTaskRuns(taskId);
      }
    } catch (e) {
      console.error('立即运行失败:', e);
    } finally {
      setRunningTaskId(null);
    }
  };

  const handleDelete = async (taskId: string) => {
    if (!window.confirm(t('确定要删除此计划任务吗？'))) return;
    try {
      await apiFetch(`/api/v1/scheduled/${taskId}`, { method: 'DELETE' });
      if (detailTask?.id === taskId) {
        setDetailTask(null);
      }
      fetchTasks();
    } catch (e) {
      console.error('删除任务失败:', e);
    }
  };

  const renderStatusBadge = (task: ScheduledTaskItem) => {
    const isRunning = runningTaskId === task.id || task.status === 'running';
    if (isRunning) {
      return (
        <span className="inline-flex items-center space-x-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30">
          <Loader2 className="w-3 h-3 animate-spin" />
          <span>{t('运行中')}</span>
        </span>
      );
    }
    if (task.last_status === 'success') {
      return (
        <span className="inline-flex items-center space-x-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30">
          <CheckCircle2 className="w-3 h-3" />
          <span>{t('上次成功')}</span>
        </span>
      );
    }
    if (task.last_status === 'failed') {
      return (
        <span className="inline-flex items-center space-x-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30">
          <AlertCircle className="w-3 h-3" />
          <span>{t('上次失败')}</span>
        </span>
      );
    }
    return (
      <span className="inline-flex items-center space-x-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-surface-200 text-muted-foreground border border-border">
        <Clock className="w-3 h-3" />
        <span>{task.status === 'paused' ? t('已暂停') : t('待运行')}</span>
      </span>
    );
  };

  const selectedRun = taskRuns.find((r) => r.id === selectedRunId) || taskRuns[0] || null;

  return (
    <div className="flex-1 h-full overflow-y-auto bg-background text-foreground p-6 md:p-10 select-text">
      <div className="max-w-5xl mx-auto space-y-8">
        {/* 顶部标题栏 */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-border pb-6">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-foreground">
              {t('已安排计划任务')}
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              {t('让智能体在后台静默定时调度：拉取外部数据、自动推理核验并落盘报告')}
              {workspace && (
                <span className="ml-2 px-2 py-0.5 rounded bg-surface-200 text-foreground border border-border font-mono text-xs">
                  {workspace.name}
                </span>
              )}
            </p>
          </div>

          <button
            onClick={() => setIsDialogOpen(true)}
            className="flex items-center space-x-2 px-4 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm transition self-start md:self-auto group"
          >
            <Plus className="w-4 h-4" />
            <span>{t('新建自动化任务')}</span>
          </button>
        </div>

        {/* 快速自然语言对话触发横幅 (Banner) */}
        <div
          onClick={() => setIsDialogOpen(true)}
          className="p-4 rounded-2xl border border-dashed border-border hover:border-emerald-500/60 bg-surface-card hover:bg-surface-50 transition cursor-pointer flex items-center justify-between shadow-sm group"
        >
          <div className="flex items-center space-x-3.5">
            <div className="p-2.5 rounded-xl bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 group-hover:scale-105 transition-transform">
              <MessageSquare className="w-5 h-5" />
            </div>
            <div>
              <h4 className="text-xs font-semibold text-foreground flex items-center space-x-1.5">
                <span>{t('对话式智能规划：告诉 AI 何时执行何种动作')}</span>
                <span className="text-[10px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 px-1.5 py-0.2 rounded font-medium">
                  {t('点此对话')}
                </span>
              </h4>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                {t('例如：“工作日每天早上 9 点检查 163 邮箱未读邮件并把摘要写入资产库”')}
              </p>
            </div>
          </div>
          <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium px-3 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 group-hover:bg-emerald-500/20 transition">
            {t('开始对话')} →
          </span>
        </div>

        {/* 计划任务列表 */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-foreground">
              {t('任务列表')} ({tasks.length})
            </h3>
          </div>

          {loading ? (
            <div className="text-center py-12 text-muted-foreground text-xs">{t('正在加载...')}</div>
          ) : tasks.length === 0 ? (
            <div
              onClick={() => setIsDialogOpen(true)}
              className="text-center py-16 border border-dashed border-border rounded-2xl p-8 hover:border-border-hover bg-surface-card/40 hover:bg-surface-card transition cursor-pointer"
            >
              <Clock className="w-8 h-8 mx-auto text-muted-foreground/60 mb-3" />
              <p className="text-sm text-foreground font-medium">{t('当前工作区暂无计划任务')}</p>
              <p className="text-xs text-muted-foreground mt-1">
                {t('点击此处或右上角「新建自动化任务」，用自然语言对话快速添加任务')}
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {tasks.map((task) => {
                const isRunning = runningTaskId === task.id || task.status === 'running';
                return (
                  <div
                    key={task.id}
                    className="p-4 rounded-xl border border-border bg-surface-card hover:border-border-hover transition flex flex-col md:flex-row md:items-start justify-between gap-4 shadow-sm"
                  >
                    <div className="min-w-0 flex-1 space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={`w-2 h-2 rounded-full flex-shrink-0 ${
                            task.status === 'active'
                              ? 'bg-emerald-500'
                              : task.status === 'running'
                              ? 'bg-amber-500 animate-pulse'
                              : 'bg-muted-foreground/40'
                          }`}
                        />
                        <h4 className="text-sm font-semibold text-foreground truncate">{task.name}</h4>
                        <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-surface-200 text-muted-foreground border border-border">
                          {task.cron_expr}
                        </span>
                        {renderStatusBadge(task)}
                      </div>

                      <p className="text-xs text-muted-foreground line-clamp-2">
                        {task.prompt_template}
                      </p>

                      {/* 调度时间与耗时元信息 */}
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground pt-0.5">
                        <span className="inline-flex items-center space-x-1">
                          <History className="w-3 h-3" />
                          <span>{t('上次运行')}: {formatDateTime(task.last_run_at)}</span>
                        </span>
                        <span className="inline-flex items-center space-x-1">
                          <CalendarClock className="w-3 h-3" />
                          <span>{t('下次计划')}: {task.status === 'paused' ? t('已暂停') : formatDateTime(task.next_run_at)}</span>
                        </span>
                        {task.last_duration_ms !== null && task.last_duration_ms !== undefined && (
                          <span className="inline-flex items-center space-x-1">
                            <Timer className="w-3 h-3" />
                            <span>{t('耗时')}: {formatDuration(task.last_duration_ms)}</span>
                          </span>
                        )}
                      </div>

                      {/* 最近一次输出或错误摘要（点击打开完整详情） */}
                      {(task.last_output || task.last_error) && (
                        <button
                          type="button"
                          onClick={() => handleOpenDetail(task)}
                          className={`w-full text-left mt-1.5 px-3 py-2 rounded-lg border text-xs transition flex items-start justify-between gap-2 ${
                            task.last_status === 'failed'
                              ? 'bg-rose-500/5 border-rose-500/20 text-rose-700 dark:text-rose-300 hover:bg-rose-500/10'
                              : 'bg-surface-100/70 border-border text-muted-foreground hover:bg-surface-200/70'
                          }`}
                        >
                          <span className="line-clamp-2 whitespace-pre-wrap break-words flex-1">
                            {task.last_status === 'failed'
                              ? task.last_error || task.last_output
                              : task.last_output}
                          </span>
                          <span className="inline-flex items-center text-[11px] font-medium text-emerald-600 dark:text-emerald-400 flex-shrink-0">
                            <span>{t('详情')}</span>
                            <ChevronRight className="w-3.5 h-3.5" />
                          </span>
                        </button>
                      )}
                    </div>

                    <div className="flex items-center space-x-2 flex-shrink-0 self-end md:self-center">
                      {/* 查看历史与完整输出 */}
                      <button
                        onClick={() => handleOpenDetail(task)}
                        className="p-2 rounded-lg bg-surface-100 hover:bg-surface-200 text-foreground transition border border-border"
                        title={t('查看运行历史与完整输出')}
                      >
                        <History className="w-3.5 h-3.5" />
                      </button>

                      {/* 立即执行 */}
                      <button
                        onClick={() => handleRunNow(task.id)}
                        disabled={isRunning}
                        className="p-2 rounded-lg bg-surface-100 hover:bg-surface-200 text-foreground transition disabled:opacity-50 border border-border"
                        title={t('立即执行一次')}
                      >
                        <Play className={`w-3.5 h-3.5 ${isRunning ? 'animate-spin' : ''}`} />
                      </button>

                      {/* 启停状态 */}
                      <button
                        onClick={() => handleToggleStatus(task)}
                        className={`p-2 rounded-lg transition border ${
                          task.status === 'active'
                            ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/25 hover:bg-emerald-500/20'
                            : 'bg-surface-100 text-muted-foreground hover:bg-surface-200 border-border'
                        }`}
                        title={task.status === 'active' ? t('暂停任务') : t('恢复任务')}
                      >
                        <Pause className="w-3.5 h-3.5" />
                      </button>

                      {/* 删除任务 */}
                      <button
                        onClick={() => handleDelete(task.id)}
                        className="p-2 rounded-lg bg-surface-100 hover:bg-rose-500/15 hover:text-rose-600 text-muted-foreground transition border border-border"
                        title={t('删除任务')}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* 任务执行详情与运行历史弹窗 */}
      {detailTask && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="w-full max-w-4xl max-h-[85vh] rounded-2xl border border-border bg-surface-card shadow-2xl flex flex-col overflow-hidden">
            {/* 弹窗头部 */}
            <div className="px-6 py-4 border-b border-border flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center space-x-2.5">
                  <h3 className="text-base font-semibold text-foreground truncate">{detailTask.name}</h3>
                  <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-surface-200 text-muted-foreground border border-border">
                    {detailTask.cron_expr}
                  </span>
                  {renderStatusBadge(detailTask)}
                </div>
                <p className="text-xs text-muted-foreground mt-1 whitespace-pre-wrap break-words">
                  {detailTask.prompt_template}
                </p>
              </div>
              <button
                onClick={() => setDetailTask(null)}
                className="p-1.5 rounded-lg hover:bg-surface-200 text-muted-foreground transition flex-shrink-0"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* 弹窗主体：左侧历史列表 + 右侧完整输出 */}
            <div className="flex-1 min-h-0 flex flex-col md:flex-row overflow-hidden">
              {/* 左侧最近运行历史 */}
              <div className="w-full md:w-72 border-b md:border-b-0 md:border-r border-border overflow-y-auto p-3 space-y-2 bg-surface-100/40">
                <div className="text-[11px] font-semibold text-muted-foreground px-2 py-1">
                  {t('最近运行历史')} ({taskRuns.length})
                </div>
                {runsLoading ? (
                  <div className="text-xs text-muted-foreground px-2 py-4">{t('正在加载历史...')}</div>
                ) : taskRuns.length === 0 ? (
                  <div className="text-xs text-muted-foreground px-2 py-4">{t('暂无执行历史记录')}</div>
                ) : (
                  taskRuns.map((run) => {
                    const isSelected = selectedRun?.id === run.id;
                    return (
                      <button
                        key={run.id}
                        type="button"
                        onClick={() => setSelectedRunId(run.id)}
                        className={`w-full text-left p-2.5 rounded-xl border transition text-xs space-y-1 ${
                          isSelected
                            ? 'bg-emerald-500/10 border-emerald-500/40 text-foreground'
                            : 'bg-surface-card border-border hover:border-border-hover text-muted-foreground'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span
                            className={`font-medium ${
                              run.status === 'success'
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : run.status === 'failed'
                                ? 'text-rose-600 dark:text-rose-400'
                                : 'text-amber-600 dark:text-amber-400'
                            }`}
                          >
                            {run.status === 'success'
                              ? t('执行成功')
                              : run.status === 'failed'
                              ? t('执行失败')
                              : t('运行中')}
                          </span>
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-200 text-muted-foreground font-mono">
                            {run.trigger_type === 'cron' ? 'CRON' : 'MANUAL'}
                          </span>
                        </div>
                        <div className="text-[11px] text-muted-foreground">
                          {formatDateTime(run.started_at)}
                        </div>
                        <div className="text-[10px] text-muted-foreground/80">
                          {t('耗时')}: {formatDuration(run.duration_ms)}
                        </div>
                      </button>
                    );
                  })
                )}
              </div>

              {/* 右侧完整输出正文 / 错误详情 */}
              <div className="flex-1 overflow-y-auto p-5 space-y-4">
                {selectedRun ? (
                  <>
                    <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground pb-3 border-b border-border">
                      <span>{t('开始')}: {formatDateTime(selectedRun.started_at)}</span>
                      <span>{t('结束')}: {formatDateTime(selectedRun.finished_at)}</span>
                      <span>{t('耗时')}: {formatDuration(selectedRun.duration_ms)}</span>
                      {selectedRun.conversation_id && (
                        <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-surface-200 border border-border">
                          {t('会话')}: {selectedRun.conversation_id}
                        </span>
                      )}
                    </div>

                    {selectedRun.error_message && (
                      <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-700 dark:text-rose-300 text-xs space-y-1">
                        <div className="font-semibold flex items-center space-x-1.5">
                          <AlertCircle className="w-4 h-4" />
                          <span>{t('错误信息')}</span>
                        </div>
                        <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed">
                          {selectedRun.error_message}
                        </pre>
                      </div>
                    )}

                    <div className="space-y-1.5">
                      <div className="text-xs font-semibold text-foreground">{t('完整执行输出')}</div>
                      <div className="p-4 rounded-xl bg-surface-100 border border-border text-xs text-foreground whitespace-pre-wrap break-words leading-relaxed font-sans">
                        {selectedRun.output_text || t('（无文本输出内容）')}
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="space-y-3">
                    <div className="text-xs font-semibold text-foreground">{t('最近一次输出摘要')}</div>
                    <div className="p-4 rounded-xl bg-surface-100 border border-border text-xs text-foreground whitespace-pre-wrap break-words leading-relaxed">
                      {detailTask.last_error || detailTask.last_output || t('暂无输出内容')}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 对话式计划任务模态框 */}
      {workspace && (
        <ScheduledTaskDialogModal
          isOpen={isDialogOpen}
          workspaceId={workspace.id}
          onClose={() => setIsDialogOpen(false)}
          onTaskCreated={() => fetchTasks()}
        />
      )}
    </div>
  );
}
