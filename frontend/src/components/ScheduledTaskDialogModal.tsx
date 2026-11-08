'use strict';

import React, { useState } from 'react';
import {
  Sparkles,
  Clock,
  Calendar,
  CheckCircle2,
  Send,
  RotateCcw,
  Plus,
  X,
  FileText,
  AlertCircle,
  HelpCircle,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';
import { useLanguage } from '@/context/LanguageContext';

interface ScheduledTaskDialogModalProps {
  isOpen: boolean;
  workspaceId: string;
  onClose: () => void;
  onTaskCreated: () => void;
}

interface ParsedScheduleData {
  name: string;
  cron_expr: string;
  human_schedule: string;
  prompt_template: string;
  explanation: string;
}

export function ScheduledTaskDialogModal({
  isOpen,
  workspaceId,
  onClose,
  onTaskCreated,
}: ScheduledTaskDialogModalProps) {
  const { t } = useLanguage();

  const [activeTab, setActiveTab] = useState<'chat' | 'guided'>('chat');
  const [userInput, setUserInput] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // 解析并可供二次微调的状态
  const [parsedData, setParsedData] = useState<ParsedScheduleData | null>(null);
  const [taskName, setTaskName] = useState('');
  const [cronExpr, setCronExpr] = useState('0 9 * * 1-5');
  const [humanSchedule, setHumanSchedule] = useState('工作日 (周一至周五) 09:00');
  const [promptTemplate, setPromptTemplate] = useState('');

  // 向导模式特定状态
  const [guidedFreq, setGuidedFreq] = useState<'workday' | 'daily' | 'interval' | 'weekly'>('workday');
  const [guidedTime, setGuidedTime] = useState('09:00');
  const [guidedIntervalHours, setGuidedIntervalHours] = useState('2');
  const [guidedAction, setGuidedAction] = useState('');

  if (!isOpen) return null;

  // 快捷灵感推荐
  const presets = [
    {
      icon: '🌅',
      label: '工作日早间邮件巡检',
      text: '工作日每天早上 9 点，检查邮箱未读邮件并把重要摘要保存到资产库',
    },
    {
      icon: '💻',
      label: '每日下班代码变动梳理',
      text: '每天傍晚 18:30，整理今天工作区的代码修改记录并输出总结报告',
    },
    {
      icon: '⏰',
      label: '每隔 2 小时服务巡检',
      text: '每隔 2 小时，自动巡检本地服务端口与网络代理连通性',
    },
    {
      icon: '📊',
      label: '每周五资产周报生成',
      text: '每周五下午 17:00，扫描工作区资产库生成本周工作周报',
    },
  ];

  // 快捷微调时机预设
  const scheduleShortcuts = [
    { label: '工作日 09:00', cron: '0 9 * * 1-5', human: '工作日 (周一至五) 09:00' },
    { label: '每天 09:00', cron: '0 9 * * *', human: '每天 09:00' },
    { label: '每天 18:30', cron: '30 18 * * *', human: '每天 18:30' },
    { label: '每隔 1 小时', cron: '0 * * * *', human: '每隔 1 小时' },
    { label: '每隔 2 小时', cron: '0 */2 * * *', human: '每隔 2 小时' },
    { label: '每周五 17:00', cron: '0 17 * * 5', human: '每周五 17:00' },
  ];

  const handleAnalyze = async (textToAnalyze?: string) => {
    const text = (textToAnalyze || userInput).trim();
    if (!text) return;

    setAnalyzing(true);
    try {
      const res = await apiFetch('/api/v1/scheduled/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspace_id: workspaceId,
          user_prompt: text,
        }),
      });

      if (res.ok) {
        const json = await res.json();
        const data: ParsedScheduleData = json.data;
        setParsedData(data);
        setTaskName(data.name);
        setCronExpr(data.cron_expr);
        setHumanSchedule(data.human_schedule);
        setPromptTemplate(data.prompt_template);
      } else {
        alert(t('分析失败，请检查输入或重试'));
      }
    } catch (e) {
      console.error('智能分析计划任务失败:', e);
      alert(t('请求异常，无法连接分析服务'));
    } finally {
      setAnalyzing(false);
    }
  };

  const handleApplyGuided = () => {
    if (!guidedAction.trim()) {
      alert(t('请填写要完成的动作目标'));
      return;
    }

    let calculatedCron = '0 9 * * 1-5';
    let calculatedHuman = '工作日 09:00';

    if (guidedFreq === 'workday') {
      const [h, m] = guidedTime.split(':');
      calculatedCron = `${parseInt(m || '0')} ${parseInt(h || '9')} * * 1-5`;
      calculatedHuman = `工作日 (周一至五) ${guidedTime}`;
    } else if (guidedFreq === 'daily') {
      const [h, m] = guidedTime.split(':');
      calculatedCron = `${parseInt(m || '0')} ${parseInt(h || '9')} * * *`;
      calculatedHuman = `每天 ${guidedTime}`;
    } else if (guidedFreq === 'interval') {
      const hours = parseInt(guidedIntervalHours) || 2;
      calculatedCron = `0 */${hours} * * *`;
      calculatedHuman = `每隔 ${hours} 小时`;
    } else if (guidedFreq === 'weekly') {
      const [h, m] = guidedTime.split(':');
      calculatedCron = `${parseInt(m || '0')} ${parseInt(h || '9')} * * 1`;
      calculatedHuman = `每周一 ${guidedTime}`;
    }

    const title = `${calculatedHuman.split(' ')[0]}：${guidedAction.trim().slice(0, 18)}`;
    const prompt = (
      `【自动计划任务 - ${title}】\n\n` +
      `🎯 任务目标：\n${guidedAction.trim()}\n\n` +
      `📋 执行与验收要求：\n` +
      `1. 受控调用相关工具或脚本执行上述要求；\n` +
      `2. 产出的数据、报告统一落盘至工作区资产库并自动登记；\n` +
      `3. 完成后通过 Windows 桌面通知发送执行结果。`
    );

    setParsedData({
      name: title,
      cron_expr: calculatedCron,
      human_schedule: calculatedHuman,
      prompt_template: prompt,
      explanation: `已根据向导设定：将在「${calculatedHuman}」自动执行「${title}」。`,
    });
    setTaskName(title);
    setCronExpr(calculatedCron);
    setHumanSchedule(calculatedHuman);
    setPromptTemplate(prompt);
    setActiveTab('chat');
  };

  const handleConfirmCreate = async () => {
    if (!taskName.trim() || !promptTemplate.trim()) {
      alert(t('任务名称与提示词不能为空'));
      return;
    }

    setSubmitting(true);
    try {
      const res = await apiFetch('/api/v1/scheduled', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspace_id: workspaceId,
          name: taskName.trim(),
          cron_expr: cronExpr.trim(),
          prompt_template: promptTemplate.trim(),
        }),
      });

      if (res.ok) {
        onTaskCreated();
        onClose();
      } else {
        const data = await res.json().catch(() => ({}));
        alert(data.detail || t('创建计划任务失败'));
      }
    } catch (e) {
      console.error('提交计划任务失败:', e);
      alert(t('网络错误，无法保存任务'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-md animate-in fade-in duration-200 select-text"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl bg-zinc-900 border border-zinc-700/80 rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[88vh] animate-in zoom-in-95 duration-150"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 顶部标题栏 */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-800 bg-zinc-900/90">
          <div className="flex items-center space-x-2.5">
            <div className="p-2 rounded-xl bg-emerald-950/60 border border-emerald-800/40 text-emerald-400">
              <Sparkles className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white flex items-center space-x-2">
                <span>{t('对话式新建自动化计划任务')}</span>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                  {t('AI 语义分析')}
                </span>
              </h2>
              <p className="text-xs text-zinc-400 mt-0.5">
                {t('告诉 AI 需要在什么时间自动完成什么动作，自动分析并加入计划')}
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2">
            {/* Tab 切换 */}
            <div className="flex items-center bg-zinc-800/80 rounded-lg p-0.5 border border-zinc-700 text-xs">
              <button
                onClick={() => setActiveTab('chat')}
                className={`px-3 py-1 rounded-md transition ${activeTab === 'chat'
                    ? 'bg-emerald-600 text-white font-medium shadow-sm'
                    : 'text-zinc-400 hover:text-zinc-200'
                  }`}
              >
                {t('💬 自然对话分析')}
              </button>
              <button
                onClick={() => setActiveTab('guided')}
                className={`px-3 py-1 rounded-md transition ${activeTab === 'guided'
                    ? 'bg-emerald-600 text-white font-medium shadow-sm'
                    : 'text-zinc-400 hover:text-zinc-200'
                  }`}
              >
                {t('📋 向导分步填写')}
              </button>
            </div>

            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 内容滚动画布 */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {activeTab === 'chat' ? (
            <div className="space-y-5">
              {/* 助手问候引导气泡 */}
              <div className="flex items-start space-x-3">
                <div className="w-8 h-8 rounded-full bg-emerald-600/30 border border-emerald-500/40 flex items-center justify-center text-sm flex-shrink-0">
                  🤖
                </div>
                <div className="flex-1 bg-zinc-800/60 border border-zinc-700/60 rounded-2xl p-4 text-xs text-zinc-300 leading-relaxed shadow-sm">
                  <p className="font-semibold text-emerald-400 mb-1">
                    {t('👋 请告诉我：你希望在什么时间，自动完成什么动作？')}
                  </p>
                  <p className="text-zinc-400">
                    {t('例如：“工作日每天早晨 9:30 检查未读邮件并把重要发票汇总到资产库” 或 “每隔 2 小时巡检系统”。AI 将自动提取周期与具身指令。')}
                  </p>
                </div>
              </div>

              {/* 快捷灵感药丸 */}
              {!parsedData && (
                <div className="space-y-2">
                  <div className="text-[11px] font-medium text-zinc-500 uppercase tracking-wider flex items-center space-x-1 pl-1">
                    <Sparkles className="w-3 h-3 text-emerald-400" />
                    <span>{t('或点击填入快捷灵感：')}</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {presets.map((p, idx) => (
                      <button
                        key={idx}
                        type="button"
                        onClick={() => {
                          setUserInput(p.text);
                          handleAnalyze(p.text);
                        }}
                        className="p-2.5 rounded-xl border border-zinc-800 bg-zinc-900/80 hover:bg-zinc-800 hover:border-emerald-500/40 text-left transition flex items-center space-x-2.5 group"
                      >
                        <span className="text-base">{p.icon}</span>
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-medium text-zinc-200 group-hover:text-emerald-300 truncate">
                            {p.label}
                          </p>
                          <p className="text-[11px] text-zinc-500 truncate mt-0.5">{p.text}</p>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* 用户对话输入框 */}
              <div className="rounded-2xl border border-zinc-700 bg-black/50 p-3 focus-within:border-emerald-500 transition shadow-inner">
                <textarea
                  value={userInput}
                  onChange={(e) => setUserInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleAnalyze();
                    }
                  }}
                  rows={2}
                  placeholder={t('用自然语言描述：在什么时间（如每天几点/每隔几小时）完成什么具体动作...（按 Enter 发送）')}
                  className="w-full bg-transparent border-none outline-none text-xs text-zinc-100 placeholder-zinc-500 resize-none leading-relaxed"
                />
                <div className="flex items-center justify-between pt-2 border-t border-zinc-800/80 select-none">
                  <span className="text-[11px] text-zinc-500">
                    {t('💡 支持智能识别工作日、周末、每天特定时分或间隔频率')}
                  </span>
                  <button
                    onClick={() => handleAnalyze()}
                    disabled={analyzing || !userInput.trim()}
                    className="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium transition disabled:opacity-50 shadow-sm"
                  >
                    {analyzing ? (
                      <>
                        <span className="w-3 h-3 border-2 border-white/20 border-t-white rounded-full animate-spin" />
                        <span>{t('正在深度分析...')}</span>
                      </>
                    ) : (
                      <>
                        <Sparkles className="w-3 h-3" />
                        <span>{t('✨ 智能分析')}</span>
                      </>
                    )}
                  </button>
                </div>
              </div>

              {/* 智能分析结果展示与微调卡片 */}
              {parsedData && (
                <div className="p-5 rounded-2xl border border-emerald-600/40 bg-zinc-950/80 shadow-2xl space-y-4 animate-in fade-in duration-200">
                  <div className="flex items-center justify-between border-b border-zinc-800/80 pb-3">
                    <div className="flex items-center space-x-2">
                      <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                      <h4 className="text-xs font-semibold text-emerald-300">
                        {t('AI 分析完成 · 自动化规划看板')}
                      </h4>
                    </div>
                    <span className="text-[11px] text-zinc-400">
                      {parsedData.explanation}
                    </span>
                  </div>

                  {/* 1. 任务名称 */}
                  <div>
                    <label className="block text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-1">
                      {t('🏷️ 任务名称 (可直接修改)')}
                    </label>
                    <input
                      type="text"
                      value={taskName}
                      onChange={(e) => setTaskName(e.target.value)}
                      className="w-full text-xs font-semibold px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-xl text-zinc-100 focus:outline-none focus:border-emerald-500"
                    />
                  </div>

                  {/* 2. 执行时间 (When) 与快捷微调 */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider flex items-center space-x-1">
                        <Clock className="w-3 h-3 text-emerald-400" />
                        <span>{t('⏰ 调度时间与周期')}</span>
                      </label>
                      <div className="flex items-center space-x-2 font-mono text-xs">
                        <span className="text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800/50">
                          {humanSchedule}
                        </span>
                        <span className="text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded border border-zinc-700">
                          {cronExpr}
                        </span>
                      </div>
                    </div>

                    {/* 快捷微调时机药丸 */}
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {scheduleShortcuts.map((sc, i) => (
                        <button
                          key={i}
                          type="button"
                          onClick={() => {
                            setCronExpr(sc.cron);
                            setHumanSchedule(sc.human);
                          }}
                          className={`text-[11px] px-2.5 py-1 rounded-lg border transition ${cronExpr === sc.cron
                              ? 'bg-emerald-950/80 border-emerald-500 text-emerald-300 font-medium'
                              : 'bg-zinc-900 border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-300'
                            }`}
                        >
                          {sc.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* 3. 执行动作与具身指令 (What) */}
                  <div>
                    <label className="block text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-1 flex items-center space-x-1">
                      <FileText className="w-3 h-3 text-emerald-400" />
                      <span>{t('📝 具身执行指令 (AI 自动扩充规范)')}</span>
                    </label>
                    <textarea
                      value={promptTemplate}
                      onChange={(e) => setPromptTemplate(e.target.value)}
                      rows={5}
                      className="w-full text-xs font-mono p-3 bg-zinc-900 border border-zinc-700 rounded-xl text-zinc-200 focus:outline-none focus:border-emerald-500 leading-relaxed"
                    />
                  </div>

                  {/* 操作条 */}
                  <div className="flex items-center justify-between pt-2 border-t border-zinc-800/80">
                    <button
                      type="button"
                      onClick={() => {
                        setParsedData(null);
                        setUserInput('');
                      }}
                      className="flex items-center space-x-1 text-xs text-zinc-400 hover:text-zinc-200 transition"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                      <span>{t('重新输入描述')}</span>
                    </button>

                    <button
                      type="button"
                      onClick={handleConfirmCreate}
                      disabled={submitting}
                      className="flex items-center space-x-2 px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-lg shadow-emerald-950/40 transition disabled:opacity-50"
                    >
                      {submitting ? (
                        <>
                          <span className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />
                          <span>{t('正在加入调度...')}</span>
                        </>
                      ) : (
                        <>
                          <span>🚀</span>
                          <span>{t('确认加入自动计划')}</span>
                        </>
                      )}
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            /* 向导分步模式 */
            <div className="space-y-6">
              {/* 步骤 1: 什么时候执行 */}
              <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50 space-y-3">
                <div className="flex items-center space-x-2">
                  <span className="w-5 h-5 rounded-full bg-emerald-600/30 text-emerald-400 flex items-center justify-center text-xs font-bold">
                    1
                  </span>
                  <h4 className="text-xs font-semibold text-zinc-200">{t('在什么时间执行？')}</h4>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {[
                    { id: 'workday', label: '工作日 (周一至五)' },
                    { id: 'daily', label: '每天执行' },
                    { id: 'interval', label: '间隔固定周期' },
                    { id: 'weekly', label: '每周固定某天' },
                  ].map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setGuidedFreq(item.id as any)}
                      className={`p-2.5 rounded-xl border text-xs font-medium text-center transition ${guidedFreq === item.id
                          ? 'bg-emerald-950/60 border-emerald-500 text-emerald-300'
                          : 'bg-zinc-900 border-zinc-800 text-zinc-400 hover:border-zinc-700'
                        }`}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>

                {guidedFreq !== 'interval' ? (
                  <div className="flex items-center space-x-3 pt-1">
                    <label className="text-xs text-zinc-400">{t('具体时间点:')}</label>
                    <input
                      type="time"
                      value={guidedTime}
                      onChange={(e) => setGuidedTime(e.target.value)}
                      className="bg-black/60 border border-zinc-700 rounded-lg px-3 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-emerald-500 font-mono"
                    />
                  </div>
                ) : (
                  <div className="flex items-center space-x-3 pt-1">
                    <label className="text-xs text-zinc-400">{t('每隔多少小时:')}</label>
                    <select
                      value={guidedIntervalHours}
                      onChange={(e) => setGuidedIntervalHours(e.target.value)}
                      className="bg-black/60 border border-zinc-700 rounded-lg px-3 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-emerald-500"
                    >
                      <option value="1">每隔 1 小时</option>
                      <option value="2">每隔 2 小时</option>
                      <option value="4">每隔 4 小时</option>
                      <option value="8">每隔 8 小时</option>
                      <option value="12">每隔 12 小时</option>
                    </select>
                  </div>
                )}
              </div>

              {/* 步骤 2: 完成什么动作 */}
              <div className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/50 space-y-3">
                <div className="flex items-center space-x-2">
                  <span className="w-5 h-5 rounded-full bg-emerald-600/30 text-emerald-400 flex items-center justify-center text-xs font-bold">
                    2
                  </span>
                  <h4 className="text-xs font-semibold text-zinc-200">{t('需要完成什么具体动作？')}</h4>
                </div>

                <textarea
                  value={guidedAction}
                  onChange={(e) => setGuidedAction(e.target.value)}
                  rows={3}
                  placeholder={t('例如：检查邮箱中的重要未读邮件，生成今天的内容摘要并把结果写入资产库...')}
                  className="w-full text-xs p-3 bg-black/60 border border-zinc-700 rounded-xl text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-emerald-500 leading-relaxed"
                />
              </div>

              {/* 向导确认按钮 */}
              <div className="flex justify-end pt-2">
                <button
                  type="button"
                  onClick={handleApplyGuided}
                  disabled={!guidedAction.trim()}
                  className="flex items-center space-x-2 px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition disabled:opacity-50"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  <span>{t('生成规划并预览')}</span>
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

