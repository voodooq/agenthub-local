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
} from 'lucide-react';
import { Workspace, ScheduledTaskItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import { useLanguage } from '@/context/LanguageContext';

interface ScheduledViewProps {
  workspace: Workspace | undefined;
}

export function ScheduledView({ workspace }: ScheduledViewProps) {
  const { t } = useLanguage();
  const [taskName, setTaskName] = useState('');
  const [cronExpr, setCronExpr] = useState('0 9 * * *');
  const [taskPrompt, setTaskPrompt] = useState('');
  const [tasks, setTasks] = useState<ScheduledTaskItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
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

  const handleCreate = async () => {
    if (!workspace || !taskName.trim() || !taskPrompt.trim()) return;
    try {
      const res = await apiFetch('/api/v1/scheduled', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspace_id: workspace.id,
          name: taskName.trim(),
          cron_expr: cronExpr.trim(),
          prompt_template: taskPrompt.trim(),
        }),
      });
      if (res.ok) {
        setTaskName('');
        setTaskPrompt('');
        setIsCreating(false);
        fetchTasks();
      }
    } catch (e) {
      console.error('创建任务失败:', e);
    }
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
      fetchTasks();
    } catch (e) {
      console.error('立即运行失败:', e);
    } finally {
      setRunningTaskId(null);
    }
  };

  const handleDelete = async (taskId: string) => {
    try {
      await apiFetch(`/api/v1/scheduled/${taskId}`, { method: 'DELETE' });
      fetchTasks();
    } catch (e) {
      console.error('删除任务失败:', e);
    }
  };

  return (
    <div className="flex-1 h-full overflow-y-auto bg-[#131314] text-zinc-100 p-6 md:p-10 select-text">
      <div className="max-w-5xl mx-auto space-y-8">
        {/* 顶部标题栏 */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-zinc-800/80 pb-6">
          <div>
            <div className="flex items-center space-x-3">
              <span className="text-2xl">⏱️</span>
              <h1 className="text-2xl font-bold tracking-tight text-white">
                {t('已安排计划任务')}
              </h1>
            </div>
            <p className="text-sm text-zinc-400 mt-1">
              {t('让智能体在后台静默定时调度：拉取外部数据、自动推理核验并落盘报告')}
              {workspace && (
                <span className="ml-2 px-2 py-0.5 rounded bg-zinc-800 text-zinc-300 font-mono text-xs">
                  {workspace.name}
                </span>
              )}
            </p>
          </div>

          <button
            onClick={() => setIsCreating(!isCreating)}
            className="flex items-center space-x-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium shadow-lg shadow-emerald-950/30 transition self-start md:self-auto"
          >
            <Plus className="w-4 h-4" />
            <span>{isCreating ? t('取消') : t('新建自动化任务')}</span>
          </button>
        </div>

        {/* 新建任务表单抽屉 */}
        {isCreating && (
          <div className="p-5 rounded-2xl border border-emerald-800/40 bg-zinc-900/90 shadow-xl space-y-4 animate-in fade-in duration-200">
            <h3 className="text-sm font-semibold text-emerald-300 flex items-center space-x-2">
              <Sparkles className="w-4 h-4" />
              <span>{t('配置新计划任务')}</span>
            </h3>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-zinc-400 mb-1">{t('任务名称')}</label>
                <input
                  type="text"
                  value={taskName}
                  onChange={(e) => setTaskName(e.target.value)}
                  placeholder={t('例如：每日代码健康度与测试自愈巡检')}
                  className="w-full text-xs px-3 py-2 bg-black/60 border border-zinc-700 rounded-lg text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div>
                <label className="block text-xs text-zinc-400 mb-1">
                  {t('执行周期 (Cron 或日常标识)')}
                </label>
                <input
                  type="text"
                  value={cronExpr}
                  onChange={(e) => setCronExpr(e.target.value)}
                  placeholder="0 9 * * * (每天 09:00) 或 hourly / daily"
                  className="w-full text-xs font-mono px-3 py-2 bg-black/60 border border-zinc-700 rounded-lg text-zinc-100 focus:outline-none focus:border-emerald-500"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs text-zinc-400 mb-1">{t('执行目标指令 / 提示词')}</label>
              <textarea
                value={taskPrompt}
                onChange={(e) => setTaskPrompt(e.target.value)}
                placeholder={t('详细描述触发此任务时让 AI 自动执行的具身目标与落盘要求...')}
                rows={3}
                className="w-full text-xs p-3 bg-black/60 border border-zinc-700 rounded-lg text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-emerald-500"
              />
            </div>

            <div className="flex justify-end space-x-2 pt-2">
              <button
                onClick={() => setIsCreating(false)}
                className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-300"
              >
                {t('取消')}
              </button>
              <button
                onClick={handleCreate}
                disabled={!taskName.trim() || !taskPrompt.trim()}
                className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-medium text-white disabled:opacity-50"
              >
                {t('确认创建')}
              </button>
            </div>
          </div>
        )}

        {/* 计划任务列表 */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-zinc-300">
              {t('任务列表')} ({tasks.length})
            </h3>
          </div>

          {loading ? (
            <div className="text-center py-12 text-zinc-500 text-xs">{t('正在加载...')}</div>
          ) : tasks.length === 0 ? (
            <div className="text-center py-16 border border-dashed border-zinc-800 rounded-2xl p-8">
              <Clock className="w-10 h-10 mx-auto text-zinc-600 mb-3" />
              <p className="text-sm text-zinc-400">{t('当前工作区暂无计划任务')}</p>
              <p className="text-xs text-zinc-600 mt-1">
                {t('点击上方「新建自动化任务」即可添加每日对账、定时巡检等后台守护任务')}
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {tasks.map((task) => (
                <div
                  key={task.id}
                  className="p-4 rounded-xl border border-zinc-800/80 bg-zinc-900/60 hover:border-zinc-700 transition flex flex-col md:flex-row md:items-center justify-between gap-4"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center space-x-2.5">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          task.status === 'active' ? 'bg-emerald-500' : 'bg-zinc-600'
                        }`}
                      />
                      <h4 className="text-sm font-semibold text-zinc-100 truncate">{task.name}</h4>
                      <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700/50">
                        {task.cron_expr}
                      </span>
                    </div>

                    <p className="text-xs text-zinc-400 mt-1.5 line-clamp-1">
                      {task.prompt_template}
                    </p>

                    {task.last_output && (
                      <div className="mt-2 text-[11px] text-zinc-500 flex items-center space-x-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                        <span className="truncate">{task.last_output}</span>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center space-x-2 flex-shrink-0">
                    <button
                      onClick={() => handleRunNow(task.id)}
                      disabled={runningTaskId === task.id}
                      className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-medium flex items-center space-x-1.5 transition disabled:opacity-50"
                      title={t('立即在后台执行一次')}
                    >
                      <Play className="w-3.5 h-3.5 text-emerald-400" />
                      <span>{runningTaskId === task.id ? '...' : t('立即测试')}</span>
                    </button>

                    <button
                      onClick={() => handleToggleStatus(task)}
                      className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 transition"
                      title={task.status === 'active' ? t('暂停任务') : t('恢复任务')}
                    >
                      {task.status === 'active' ? (
                        <Pause className="w-4 h-4 text-amber-400" />
                      ) : (
                        <Play className="w-4 h-4 text-emerald-400" />
                      )}
                    </button>

                    <button
                      onClick={() => handleDelete(task.id)}
                      className="p-1.5 rounded-lg hover:bg-red-950/40 text-zinc-500 hover:text-red-400 transition"
                      title={t('删除任务')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
