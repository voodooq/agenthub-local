'use client';

import React, { useEffect, useState } from 'react';
import {
  Clock,
  Play,
  Pause,
  Plus,
  Trash2,
  Calendar,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  MessageSquare,
} from 'lucide-react';
import { Workspace, ScheduledTaskItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import { useLanguage } from '@/context/LanguageContext';
import { ScheduledTaskDialogModal } from './ScheduledTaskDialogModal';

interface ScheduledViewProps {
  workspace: Workspace | undefined;
}

export function ScheduledView({ workspace }: ScheduledViewProps) {
  const { t } = useLanguage();
  const [tasks, setTasks] = useState<ScheduledTaskItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [runningTaskId, setRunningTaskId] = useState<string | null>(null);

  const fetchTasks = async () => {
    if (!workspace) return;
    setLoading(true);
    try {
      const res = await apiFetch(`/api/v1/scheduled?workspace_id=${workspace.id}`);
      if (res.ok) {
        const data = await res.json();
        setTasks(data.tasks || []);
      }
    } catch (e) {
      console.error('拉取计划任务失败:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTasks();
  }, [workspace?.id]);

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
      fetchTasks();
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
      fetchTasks();
    } catch (e) {
      console.error('删除任务失败:', e);
    }
  };

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
              {tasks.map((task) => (
                <div
                  key={task.id}
                  className="p-4 rounded-xl border border-border bg-surface-card hover:border-border-hover transition flex flex-col md:flex-row md:items-center justify-between gap-4 shadow-sm"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center space-x-2.5">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          task.status === 'active' ? 'bg-emerald-500' : 'bg-muted-foreground/40'
                        }`}
                      />
                      <h4 className="text-sm font-semibold text-foreground truncate">{task.name}</h4>
                      <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-surface-200 text-muted-foreground border border-border">
                        {task.cron_expr}
                      </span>
                    </div>

                    <p className="text-xs text-muted-foreground mt-1.5 line-clamp-1">
                      {task.prompt_template}
                    </p>

                    {task.last_output && (
                      <div className="mt-2 text-[11px] text-muted-foreground flex items-center space-x-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                        <span className="truncate">{task.last_output}</span>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center space-x-2 flex-shrink-0 self-end md:self-auto">
                    {/* 立即执行 */}
                    <button
                      onClick={() => handleRunNow(task.id)}
                      disabled={runningTaskId === task.id}
                      className="p-2 rounded-lg bg-surface-100 hover:bg-surface-200 text-foreground transition disabled:opacity-50 border border-border"
                      title={t('立即执行一次')}
                    >
                      <Play className={`w-3.5 h-3.5 ${runningTaskId === task.id ? 'animate-spin' : ''}`} />
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
              ))}
            </div>
          )}
        </div>
      </div>

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
