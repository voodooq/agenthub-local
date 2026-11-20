'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Camera, Check, Copy, FileText, Loader2, ShieldCheck, X } from 'lucide-react';
import { apiFetch } from '@/utils/api';

type SnapMode = 'equipment' | 'notice' | 'whiteboard';

interface ExpertSummary {
  id: string;
  name: string;
}

interface WikiSpaceSummary {
  wiki_space_id: string;
  name: string;
}

interface SnapResult {
  success: boolean;
  mode: SnapMode;
  verdict?: string;
  summary?: string;
  key_values?: Array<{ label: string; value: string }>;
  citations?: Array<{ title?: string; source?: string }>;
  page_id?: string;
  title?: string;
  summary_text?: string;
  review_status?: string;
}

interface MobileSnapSheetProps {
  open: boolean;
  onClose: () => void;
  experts: ExpertSummary[];
  defaultExpertId?: string;
  onNavigateToWiki?: () => void;
}

async function compressImageToJpeg(file: File): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const maxEdge = 1280;
      let width = img.naturalWidth || img.width;
      let height = img.naturalHeight || img.height;
      if (width > maxEdge || height > maxEdge) {
        if (width >= height) {
          height = Math.round((height * maxEdge) / width);
          width = maxEdge;
        } else {
          width = Math.round((width * maxEdge) / height);
          height = maxEdge;
        }
      }
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        reject(new Error('无法创建图像压缩画布'));
        return;
      }
      ctx.drawImage(img, 0, 0, width, height);
      canvas.toBlob(
        (blob) => {
          if (blob) resolve(blob);
          else reject(new Error('图片压缩失败'));
        },
        'image/jpeg',
        0.82
      );
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('读取图片失败'));
    };
    img.src = url;
  });
}

/**
 * 移动端随手拍底部抽屉
 * 遵循原生 Bottom Sheet 交互习惯：顶部拖拽指示条、大触控区、圆角分段器
 */
export function MobileSnapSheet({
  open,
  onClose,
  experts,
  defaultExpertId,
  onNavigateToWiki,
}: MobileSnapSheetProps) {
  const [mode, setMode] = useState<SnapMode>('equipment');
  const [selectedExpertId, setSelectedExpertId] = useState<string>(defaultExpertId || '');
  const [spaces, setSpaces] = useState<WikiSpaceSummary[]>([]);
  const [selectedSpaceId, setSelectedSpaceId] = useState<string>('');
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [result, setResult] = useState<SnapResult | null>(null);
  const [copied, setCopied] = useState(false);

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (defaultExpertId) {
      setSelectedExpertId(defaultExpertId);
    } else if (experts.length > 0 && !selectedExpertId) {
      setSelectedExpertId(experts[0].id);
    }
  }, [defaultExpertId, experts, selectedExpertId]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    apiFetch('/api/v1/wiki/spaces')
      .then((res) => (res.ok ? res.json() : null))
      .then((payload) => {
        if (!active || !payload?.data) return;
        setSpaces(payload.data);
        if (payload.data.length > 0) {
          setSelectedSpaceId((prev) => prev || payload.data[0].wiki_space_id);
        }
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [open]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  if (!open) return null;

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;

    setErrorMsg(null);
    setResult(null);
    const localUrl = URL.createObjectURL(file);
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return localUrl;
    });

    setIsProcessing(true);
    try {
      const compressed = await compressImageToJpeg(file);
      const formData = new FormData();
      formData.append('file', compressed, 'snap.jpg');
      formData.append('mode', mode);
      if (mode === 'equipment' && selectedExpertId) {
        formData.append('expert_id', selectedExpertId);
      }
      if (mode !== 'equipment' && selectedSpaceId) {
        formData.append('wiki_space_id', selectedSpaceId);
      }

      const res = await apiFetch('/api/v1/mobile/snap-analyze', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail || '识图分析失败，请重试');
      }

      const data: SnapResult = await res.json();
      setResult(data);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '识图处理失败');
    } finally {
      setIsProcessing(false);
    }
  };

  const handleCopySummary = () => {
    const text = result?.summary || result?.summary_text || '';
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end bg-black/50 backdrop-blur-[1px]"
      onClick={onClose}
    >
      <div
        className="max-h-[88dvh] overflow-y-auto rounded-t-3xl border-t border-border bg-background px-5 pb-7 pt-2.5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 拖拽指示条 */}
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-border" />

        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Camera className="h-5 w-5 text-emerald-500" strokeWidth={1.75} />
            <h3 className="text-base font-semibold text-foreground">随手拍识图</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground active:bg-surface-1"
          >
            <X className="h-5 w-5" strokeWidth={1.75} />
          </button>
        </div>

        {/* 3 模式圆角分段器 */}
        <div className="grid grid-cols-3 gap-1.5 rounded-2xl bg-surface-1 p-1.5">
          {(
            [
              { id: 'equipment', label: '器材核验' },
              { id: 'notice', label: '通知公告' },
              { id: 'whiteboard', label: '白板纪要' },
            ] as Array<{ id: SnapMode; label: string }>
          ).map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                setMode(item.id);
                setResult(null);
                setErrorMsg(null);
              }}
              className={`h-9 rounded-xl px-2 text-xs font-semibold transition-all ${
                mode === item.id
                  ? 'bg-background text-foreground shadow-sm'
                  : 'text-muted-foreground'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="mt-4">
          {mode === 'equipment' ? (
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-muted-foreground">
                核验参照专家
              </span>
              <select
                value={selectedExpertId}
                onChange={(e) => setSelectedExpertId(e.target.value)}
                className="h-11 w-full rounded-xl border border-border/70 bg-surface-1 px-3.5 text-sm text-foreground focus:border-emerald-500 focus:outline-none"
              >
                {experts.map((ex) => (
                  <option key={ex.id} value={ex.id}>
                    {ex.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-muted-foreground">
                归档至 Wiki 空间
              </span>
              <select
                value={selectedSpaceId}
                onChange={(e) => setSelectedSpaceId(e.target.value)}
                className="h-11 w-full rounded-xl border border-border/70 bg-surface-1 px-3.5 text-sm text-foreground focus:border-emerald-500 focus:outline-none"
              >
                {spaces.length === 0 ? (
                  <option value="">默认知识空间</option>
                ) : (
                  spaces.map((sp) => (
                    <option key={sp.wiki_space_id} value={sp.wiki_space_id}>
                      {sp.name}
                    </option>
                  ))
                )}
              </select>
            </label>
          )}
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          capture="environment"
          onChange={handleFileChange}
          className="hidden"
        />

        <button
          type="button"
          disabled={isProcessing}
          onClick={() => fileInputRef.current?.click()}
          className="mt-4 flex h-12 w-full items-center justify-center gap-2 rounded-2xl bg-emerald-600 text-sm font-semibold text-white shadow-sm transition-transform active:scale-[0.98] disabled:opacity-50"
        >
          {isProcessing ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.75} />
              <span>正在压缩并分析图片...</span>
            </>
          ) : (
            <>
              <Camera className="h-4 w-4" strokeWidth={1.75} />
              <span>拍照或从相册选择</span>
            </>
          )}
        </button>

        {previewUrl && (
          <div className="mt-4 overflow-hidden rounded-2xl border border-border/70 bg-surface-1 p-2">
            <img
              src={previewUrl}
              alt="随手拍预览"
              className="max-h-44 w-full rounded-xl object-contain"
            />
          </div>
        )}

        {errorMsg && (
          <div className="mt-4 rounded-2xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-xs leading-relaxed text-rose-600 dark:text-rose-400">
            {errorMsg}
          </div>
        )}

        {result && result.mode === 'equipment' && (
          <div className="mt-4 rounded-2xl border border-border/70 bg-surface-1 p-4">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-emerald-500" strokeWidth={1.75} />
                <span className="text-sm font-semibold text-foreground">核验结果</span>
              </div>
              {result.verdict && (
                <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
                  {result.verdict}
                </span>
              )}
            </div>

            {result.key_values && result.key_values.length > 0 && (
              <div className="mt-3 grid grid-cols-2 gap-2 border-t border-border/60 pt-3">
                {result.key_values.map((kv, idx) => (
                  <div key={idx} className="rounded-xl bg-background px-3 py-2">
                    <div className="text-[11px] text-muted-foreground">{kv.label}</div>
                    <div className="mt-0.5 text-xs font-semibold text-foreground tabular-nums">
                      {kv.value}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {result.summary && (
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                {result.summary}
              </p>
            )}

            <div className="mt-3 flex justify-end border-t border-border/60 pt-3">
              <button
                type="button"
                onClick={handleCopySummary}
                className="flex h-9 items-center gap-1.5 rounded-full bg-background px-3.5 text-xs font-medium text-muted-foreground active:scale-95"
              >
                {copied ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
                    <span className="text-emerald-600 dark:text-emerald-400">已复制</span>
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" strokeWidth={1.75} />
                    <span>复制结论</span>
                  </>
                )}
              </button>
            </div>
          </div>
        )}

        {result && result.mode !== 'equipment' && (
          <div className="mt-4 rounded-2xl border border-border/70 bg-surface-1 p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 text-emerald-500" strokeWidth={1.75} />
                <span className="text-sm font-semibold text-foreground">
                  已收入口袋 Wiki 待审阅
                </span>
              </div>
              <span className="rounded-full bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400">
                待审阅
              </span>
            </div>
            {result.title && (
              <p className="mt-2.5 text-sm font-medium text-foreground">{result.title}</p>
            )}
            {result.summary_text && (
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {result.summary_text}
              </p>
            )}
            {onNavigateToWiki && (
              <button
                type="button"
                onClick={() => {
                  onClose();
                  onNavigateToWiki();
                }}
                className="mt-4 h-11 w-full rounded-xl bg-background text-sm font-medium text-foreground active:scale-[0.98]"
              >
                前往口袋 Wiki 查看并批准归档
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
