'use client';

import React, { useState, useEffect } from 'react';
import {
  X,
  Upload,
  FileArchive,
  FileCode,
  FileJson,
  MessageSquare,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  ArrowLeft,
  RotateCcw,
  Sparkles,
  Loader2,
  Database,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface ImportWizardModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

interface MemoryCandidate {
  temp_id: string;
  category: 'env_info' | 'preference' | 'rule';
  content: string;
  confidence: number;
  is_pinned: number;
  source: string;
  status: 'new' | 'conflict' | 'duplicate';
  resolution: 'add' | 'overwrite' | 'keep_both' | 'skip';
  conflict_with?: {
    id: string;
    content: string;
  } | null;
}

interface ParsePreviewResult {
  preview_id: string;
  filename: string;
  detected_source: string;
  stats: {
    conversations_count: number;
    messages_count: number;
    memories_count: number;
    new_count: number;
    conflict_count: number;
    duplicate_count: number;
  };
  conversations_preview: {
    title: string;
    source_type: string;
    message_count: number;
  }[];
  memories_comparison: MemoryCandidate[];
}

interface ImportBatchItem {
  id: string;
  source_type: string;
  filename: string;
  imported_sessions_count: number;
  imported_memories_count: number;
  created_at: string;
}

export function ImportWizardModal({ isOpen, onClose, onSuccess }: ImportWizardModalProps) {
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [selectedSourceCard, setSelectedSourceCard] = useState<
    'chatgpt' | 'claude' | 'ide_rules' | 'custom_json'
  >('chatgpt');
  const [pasteMode, setPasteMode] = useState(false);
  const [pasteText, setPasteText] = useState('');
  const [pasteFilename, setPasteFilename] = useState('GEMINI.md');

  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<ParsePreviewResult | null>(null);

  // Step 2 策略开关
  const [importConversations, setImportConversations] = useState(true);
  const [importMemories, setImportMemories] = useState(true);
  const [extractImplicit, setExtractImplicit] = useState(false);

  // Step 3 冲突消解决策
  const [candidates, setCandidates] = useState<MemoryCandidate[]>([]);

  // Step 4 事务执行状态
  const [committing, setCommitting] = useState(false);
  const [commitResult, setCommitResult] = useState<{
    batch_id: string;
    imported_sessions_count: number;
    imported_messages_count: number;
    imported_memories_count: number;
  } | null>(null);

  // 已导入批次记录（支持一键撤回）
  const [batches, setBatches] = useState<ImportBatchItem[]>([]);

  const fetchBatches = async () => {
    try {
      const res = await apiFetch('/api/v1/import/batches');
      if (res.ok) {
        const data = await res.json();
        setBatches(data.batches || []);
      }
    } catch (e) {}
  };

  useEffect(() => {
    if (isOpen) {
      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          onClose();
        }
      };
      window.addEventListener('keydown', handleKeyDown);

      setStep(1);
      setPreviewData(null);
      setCommitResult(null);
      setParseError(null);
      fetchBatches();

      return () => {
        window.removeEventListener('keydown', handleKeyDown);
      };
    }
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setParsing(true);
    setParseError(null);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('source_hint', selectedSourceCard);

      const res = await apiFetch('/api/v1/import/parse', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || '解析失败');
      }
      setPreviewData(data);
      setCandidates(data.memories_comparison || []);
    } catch (err: any) {
      setParseError(err.message || '文件解析失败，请确认格式');
    } finally {
      setParsing(false);
      e.target.value = '';
    }
  };

  const handleParseText = async () => {
    if (!pasteText.trim()) return;
    setParsing(true);
    setParseError(null);
    try {
      const res = await apiFetch('/api/v1/import/parse-text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: pasteFilename || 'GEMINI.md',
          content: pasteText,
          source_hint: selectedSourceCard,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || '解析文本失败');
      }
      setPreviewData(data);
      setCandidates(data.memories_comparison || []);
    } catch (err: any) {
      setParseError(err.message || '解析文本失败');
    } finally {
      setParsing(false);
    }
  };

  const handleResolutionChange = (
    tempId: string,
    resolution: 'add' | 'overwrite' | 'keep_both' | 'skip'
  ) => {
    setCandidates((prev) =>
      prev.map((c) => (c.temp_id === tempId ? { ...c, resolution } : c))
    );
  };

  const handleCommitImport = async () => {
    if (!previewData) return;
    setStep(4);
    setCommitting(true);
    try {
      const res = await apiFetch('/api/v1/import/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          preview_id: previewData.preview_id,
          import_conversations: importConversations,
          import_memories: importMemories,
          extract_implicit: extractImplicit,
          memory_resolutions: candidates.map((c) => ({
            temp_id: c.temp_id,
            resolution: c.resolution,
          })),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || '提交事务失败');
      }
      setCommitResult(data);
      fetchBatches();
      onSuccess();
    } catch (err: any) {
      setParseError(err.message || '导入落盘失败');
    } finally {
      setCommitting(false);
    }
  };

  const handleRollbackBatch = async (batchId: string) => {
    if (!window.confirm('确定要撤回该批次导入的所有历史会话与记忆吗？')) return;
    try {
      const res = await apiFetch(`/api/v1/import/batches/${batchId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        fetchBatches();
        onSuccess();
      }
    } catch (e) {}
  };

  const sourceLabelMap: Record<string, string> = {
    chatgpt: 'ChatGPT 官方导出包',
    claude: 'Claude 官方导出包',
    ide_rules: 'IDE 规则文件 (GEMINI.md / .cursorrules)',
    custom_json: '通用 JSON / 对话备份',
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-4xl max-h-[88vh] flex flex-col rounded-2xl bg-surface-card border border-border shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 顶部标题与 4 步进度指示条 */}
        <div className="px-6 py-4 border-b border-border bg-surface-100/60 flex items-center justify-between">
          <div>
            <h2 className="text-[15px] font-bold text-foreground">
              跨平台历史会话与长期记忆「四步导入向导」
            </h2>
            <p className="text-xs text-muted-foreground">
              支持 ChatGPT / Claude / GEMINI.md / .cursorrules · 本地 FTS5 毫秒级索引 · 冲突防污染比对
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* 步骤导航条 */}
        <div className="px-6 py-3 border-b border-border bg-surface-100/30 grid grid-cols-4 gap-2 text-xs">
          {[
            { num: 1, title: '1. 选择来源与嗅探' },
            { num: 2, title: '2. 导入与挖掘策略' },
            { num: 3, title: '3. 冲突比对预览' },
            { num: 4, title: '4. 批量事务落盘' },
          ].map((s) => (
            <div
              key={s.num}
              className={`px-3 py-1.5 rounded-lg font-medium flex items-center space-x-1.5 ${
                step === s.num
                  ? 'bg-emerald-600 text-white shadow-sm'
                  : step > s.num
                  ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                  : 'bg-surface-200/50 text-muted-foreground'
              }`}
            >
              <span>{s.title}</span>
            </div>
          ))}
        </div>

        {/* 主体内容区 */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {parseError && (
            <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-xs flex items-center space-x-2">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>{parseError}</span>
            </div>
          )}

          {/* ────────── STEP 1: 选择来源与自动格式嗅探 ────────── */}
          {step === 1 && (
            <div className="space-y-5">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  {
                    id: 'chatgpt',
                    title: 'ChatGPT 导出包',
                    desc: '.zip / conversations.json / memories.json',
                    icon: FileArchive,
                  },
                  {
                    id: 'claude',
                    title: 'Claude 导出包',
                    desc: '.zip / conversations.json 历史对话',
                    icon: MessageSquare,
                  },
                  {
                    id: 'ide_rules',
                    title: 'IDE 规则文件',
                    desc: 'GEMINI.md / CLAUDE.md / .cursorrules',
                    icon: FileCode,
                  },
                  {
                    id: 'custom_json',
                    title: '通用 JSON / 文本',
                    desc: 'Cherry Studio / NextChat / 自由粘贴',
                    icon: FileJson,
                  },
                ].map((card) => {
                  const Icon = card.icon;
                  const active = selectedSourceCard === card.id;
                  return (
                    <button
                      key={card.id}
                      type="button"
                      onClick={() => {
                        setSelectedSourceCard(card.id as any);
                        if (card.id === 'ide_rules') setPasteFilename('GEMINI.md');
                      }}
                      className={`p-3.5 rounded-xl border text-left transition-all space-y-1.5 ${
                        active
                          ? 'border-emerald-500 bg-emerald-500/10 shadow-sm'
                          : 'border-border bg-surface-100/60 hover:bg-surface-100'
                      }`}
                    >
                      <Icon className={`w-5 h-5 ${active ? 'text-emerald-500' : 'text-muted-foreground'}`} />
                      <div className="text-xs font-bold text-foreground">{card.title}</div>
                      <div className="text-[11px] text-muted-foreground leading-snug">{card.desc}</div>
                    </button>
                  );
                })}
              </div>

              {/* 切换文件上传 vs 直接粘贴文本 */}
              <div className="flex items-center space-x-3 text-xs">
                <button
                  type="button"
                  onClick={() => setPasteMode(false)}
                  className={`px-3 py-1 rounded-lg font-medium ${
                    !pasteMode ? 'bg-surface-200 text-foreground' : 'text-muted-foreground'
                  }`}
                >
                  上传文件 (.zip / .json / .md)
                </button>
                <button
                  type="button"
                  onClick={() => setPasteMode(true)}
                  className={`px-3 py-1 rounded-lg font-medium ${
                    pasteMode ? 'bg-surface-200 text-foreground' : 'text-muted-foreground'
                  }`}
                >
                  直接粘贴规则或 JSON 文本
                </button>
              </div>

              {!pasteMode ? (
                <label className="flex flex-col items-center justify-center border-2 border-dashed border-border hover:border-emerald-500 rounded-2xl p-8 cursor-pointer bg-surface-100/40 hover:bg-surface-100/80 transition-all">
                  <input
                    type="file"
                    accept=".zip,.json,.md,.txt,.cursorrules,.windsurfrules"
                    onChange={handleFileChange}
                    className="hidden"
                  />
                  {parsing ? (
                    <div className="flex items-center space-x-2 text-xs text-emerald-500">
                      <Loader2 className="w-5 h-5 animate-spin" />
                      <span>正在流式解压并嗅探数据结构...</span>
                    </div>
                  ) : (
                    <>
                      <Upload className="w-8 h-8 text-emerald-500 mb-2" />
                      <span className="text-xs font-semibold text-foreground">
                        点击选择或拖拽文件到此区域上传
                      </span>
                      <span className="text-[11px] text-muted-foreground mt-1">
                        支持 ChatGPT/Claude 官方 .zip、conversations.json、memories.json、GEMINI.md、.cursorrules
                      </span>
                    </>
                  )}
                </label>
              ) : (
                <div className="space-y-2.5">
                  <div className="flex items-center space-x-2">
                    <span className="text-xs text-muted-foreground">来源标识文件名:</span>
                    <input
                      type="text"
                      value={pasteFilename}
                      onChange={(e) => setPasteFilename(e.target.value)}
                      className="px-2.5 py-1 rounded-lg bg-surface-100 border border-border text-xs text-foreground outline-none"
                    />
                  </div>
                  <textarea
                    rows={5}
                    value={pasteText}
                    onChange={(e) => setPasteText(e.target.value)}
                    placeholder="在此粘贴 GEMINI.md / .cursorrules 编码规范，或粘贴 JSON 格式对话/记忆数组..."
                    className="w-full p-3 rounded-xl bg-surface-100 border border-border text-xs text-foreground font-mono outline-none focus:border-emerald-500"
                  />
                  <button
                    type="button"
                    onClick={handleParseText}
                    disabled={parsing || !pasteText.trim()}
                    className="px-4 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium disabled:opacity-40"
                  >
                    {parsing ? '解析中...' : '立即嗅探解析'}
                  </button>
                </div>
              )}

              {/* 嗅探成功摘要横幅 */}
              {previewData && (
                <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-between">
                  <div className="space-y-1">
                    <div className="text-xs font-bold text-emerald-600 dark:text-emerald-400 flex items-center space-x-1.5">
                      <CheckCircle2 className="w-4 h-4" />
                      <span>
                        识别成功：{sourceLabelMap[previewData.detected_source] || previewData.detected_source} ({previewData.filename})
                      </span>
                    </div>
                    <p className="text-xs text-foreground">
                      包含 <strong>{previewData.stats.conversations_count} 段历史会话</strong>（共{' '}
                      <strong>{previewData.stats.messages_count} 条消息</strong>）、
                      <strong>{previewData.stats.memories_count} 条显式记忆/规则</strong>（新增 {previewData.stats.new_count} 条，冲突 {previewData.stats.conflict_count} 条，重复 {previewData.stats.duplicate_count} 条）。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setStep(2)}
                    className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold flex items-center space-x-1 shrink-0"
                  >
                    <span>下一步</span>
                    <ArrowRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}

              {/* 历史导入批次与一键撤回列表 */}
              {batches.length > 0 && (
                <div className="pt-3 border-t border-border space-y-2">
                  <div className="text-xs font-bold text-muted-foreground flex items-center space-x-1.5">
                    <Database className="w-3.5 h-3.5" />
                    <span>历史导入批次记录（支持一键无痕回滚）</span>
                  </div>
                  <div className="space-y-1.5 max-h-36 overflow-y-auto">
                    {batches.map((b) => (
                      <div
                        key={b.id}
                        className="px-3 py-2 rounded-xl bg-surface-100/70 border border-border flex items-center justify-between text-xs"
                      >
                        <div>
                          <span className="font-semibold text-foreground">{b.filename || b.source_type}</span>
                          <span className="text-muted-foreground ml-2">
                            会话: {b.imported_sessions_count} · 记忆: {b.imported_memories_count} · {b.created_at}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleRollbackBatch(b.id)}
                          className="flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 text-[11px] font-medium"
                        >
                          <RotateCcw className="w-3 h-3" />
                          <span>撤回此批次</span>
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ────────── STEP 2: 选择导入模式与挖掘策略 ────────── */}
          {step === 2 && previewData && (
            <div className="space-y-4">
              <label className="p-4 rounded-xl border border-border bg-surface-100/60 flex items-start space-x-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={importConversations}
                  onChange={(e) => setImportConversations(e.target.checked)}
                  className="mt-1"
                />
                <div className="space-y-1">
                  <div className="text-xs font-bold text-foreground">
                    导入历史聊天记录（0 算力消耗，共 {previewData.stats.conversations_count} 段对话）
                  </div>
                  <p className="text-xs text-muted-foreground">
                    存入本地 SQLite 并立即建立 FTS5 全文索引，支持随时按 Ctrl+K 秒级检索历史代码片段，或点击任意老对话无缝接续往下聊。
                  </p>
                </div>
              </label>

              <label className="p-4 rounded-xl border border-border bg-surface-100/60 flex items-start space-x-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={importMemories}
                  onChange={(e) => setImportMemories(e.target.checked)}
                  className="mt-1"
                />
                <div className="space-y-1">
                  <div className="text-xs font-bold text-foreground">
                    导入显式规则与长期记忆（推荐，共 {previewData.stats.memories_count} 条）
                  </div>
                  <p className="text-xs text-muted-foreground">
                    导入 memories.json 或 GEMINI.md 中的规则，自动归类为本机环境 (env_info)、个人偏好 (preference) 与工程规范 (rule)。
                  </p>
                </div>
              </label>

              <label className="p-4 rounded-xl border border-border bg-surface-100/60 flex items-start space-x-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={extractImplicit}
                  onChange={(e) => setExtractImplicit(e.target.checked)}
                  className="mt-1"
                />
                <div className="space-y-1">
                  <div className="text-xs font-bold text-foreground flex items-center space-x-1.5">
                    <span>从最近 30 条高价值历史对话中自动提炼隐性偏好（可选）</span>
                    <Sparkles className="w-3.5 h-3.5 text-amber-500" />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    自动扫描导入对话中您声明过的技术栈与环境要求，沉淀为长期记忆画像。
                  </p>
                </div>
              </label>
            </div>
          )}

          {/* ────────── STEP 3: 冲突比对与可视化预览 ────────── */}
          {step === 3 && previewData && (
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-foreground">
                  长期记忆冲突比对表（共 {candidates.length} 条候选）
                </span>
                <div className="flex items-center space-x-3 text-[11px]">
                  <span className="text-emerald-600 dark:text-emerald-400">
                    🟢 新增: {previewData.stats.new_count}
                  </span>
                  <span className="text-amber-600 dark:text-amber-400">
                    🟡 冲突: {previewData.stats.conflict_count}
                  </span>
                  <span className="text-muted-foreground">
                    ⚪ 重复跳过: {previewData.stats.duplicate_count}
                  </span>
                </div>
              </div>

              {candidates.length === 0 ? (
                <div className="p-8 text-center text-xs text-muted-foreground bg-surface-100/50 rounded-xl border border-border">
                  本次导入仅包含历史聊天会话，无显式记忆条目需比对，可直接点击「开始导入」。
                </div>
              ) : (
                <div className="space-y-2 max-h-[46vh] overflow-y-auto pr-1">
                  {candidates.map((cand) => (
                    <div
                      key={cand.temp_id}
                      className={`p-3 rounded-xl border text-xs space-y-2 ${
                        cand.status === 'conflict'
                          ? 'border-amber-500/40 bg-amber-500/5'
                          : cand.status === 'duplicate'
                          ? 'border-border/60 bg-surface-100/40 opacity-70'
                          : 'border-border bg-surface-100/70'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center space-x-2">
                          {cand.status === 'new' && (
                            <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-semibold text-[11px]">
                              🟢 新增记忆
                            </span>
                          )}
                          {cand.status === 'conflict' && (
                            <span className="px-2 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400 font-semibold text-[11px]">
                              🟡 冲突覆盖提醒
                            </span>
                          )}
                          {cand.status === 'duplicate' && (
                            <span className="px-2 py-0.5 rounded bg-surface-200 text-muted-foreground font-medium text-[11px]">
                              ⚪ 重复忽略
                            </span>
                          )}
                          <span className="font-mono text-[11px] text-muted-foreground">
                            [{cand.category}] {cand.is_pinned ? '📌置顶免衰减' : ''}
                          </span>
                        </div>

                        {/* 决策按钮组 */}
                        <div className="flex items-center space-x-1">
                          {cand.status === 'conflict' ? (
                            <>
                              {[
                                { id: 'overwrite', label: '覆盖旧记忆' },
                                { id: 'keep_both', label: '保留两者' },
                                { id: 'skip', label: '跳过' },
                              ].map((opt) => (
                                <button
                                  key={opt.id}
                                  type="button"
                                  onClick={() => handleResolutionChange(cand.temp_id, opt.id as any)}
                                  className={`px-2 py-0.5 rounded text-[11px] transition-colors ${
                                    cand.resolution === opt.id
                                      ? 'bg-amber-600 text-white font-semibold'
                                      : 'bg-surface-200 text-muted-foreground hover:text-foreground'
                                  }`}
                                >
                                  {opt.label}
                                </button>
                              ))}
                            </>
                          ) : (
                            <>
                              {[
                                { id: 'add', label: '导入' },
                                { id: 'skip', label: '跳过' },
                              ].map((opt) => (
                                <button
                                  key={opt.id}
                                  type="button"
                                  onClick={() => handleResolutionChange(cand.temp_id, opt.id as any)}
                                  className={`px-2 py-0.5 rounded text-[11px] transition-colors ${
                                    cand.resolution === opt.id
                                      ? 'bg-emerald-600 text-white font-semibold'
                                      : 'bg-surface-200 text-muted-foreground hover:text-foreground'
                                  }`}
                                >
                                  {opt.label}
                                </button>
                              ))}
                            </>
                          )}
                        </div>
                      </div>

                      <div className="text-foreground break-anywhere">{cand.content}</div>

                      {cand.conflict_with && (
                        <div className="p-2 rounded-lg bg-surface-200/70 text-[11px] text-muted-foreground">
                          <span className="font-semibold">本地已有旧记忆：</span>
                          {cand.conflict_with.content}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ────────── STEP 4: 事务落盘与进度完成 ────────── */}
          {step === 4 && (
            <div className="py-10 text-center space-y-4">
              {committing ? (
                <div className="space-y-3">
                  <Loader2 className="w-10 h-10 text-emerald-500 animate-spin mx-auto" />
                  <div className="text-sm font-bold text-foreground">
                    正在执行 SQLite BEGIN IMMEDIATE 批量事务与 FTS5 索引构建...
                  </div>
                </div>
              ) : commitResult ? (
                <div className="space-y-3">
                  <CheckCircle2 className="w-12 h-12 text-emerald-500 mx-auto" />
                  <h3 className="text-base font-bold text-foreground">数据导入与索引构建已完成！</h3>
                  <p className="text-xs text-muted-foreground">
                    批次号: <code className="font-mono">{commitResult.batch_id}</code> · 成功导入{' '}
                    <strong>{commitResult.imported_sessions_count} 段会话</strong>（
                    {commitResult.imported_messages_count} 条消息）、
                    <strong>{commitResult.imported_memories_count} 条长期记忆</strong>。
                  </p>
                  <div className="pt-2">
                    <button
                      type="button"
                      onClick={onClose}
                      className="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold"
                    >
                      完成并返回工作台
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>

        {/* 底部翻页操作栏 */}
        {step > 1 && step < 4 && (
          <div className="px-6 py-3.5 border-t border-border bg-surface-100/60 flex items-center justify-between">
            <button
              type="button"
              onClick={() => setStep((prev) => (prev - 1) as any)}
              className="px-3.5 py-1.5 rounded-xl bg-surface-200 hover:bg-surface-300 text-foreground text-xs font-medium flex items-center space-x-1"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              <span>上一步</span>
            </button>

            {step === 2 ? (
              <button
                type="button"
                onClick={() => setStep(3)}
                className="px-4 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold flex items-center space-x-1"
              >
                <span>下一步：冲突比对预览</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            ) : (
              <button
                type="button"
                onClick={handleCommitImport}
                className="px-4 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold flex items-center space-x-1 shadow-sm"
              >
                <span>开始事务落盘导入</span>
                <CheckCircle2 className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

