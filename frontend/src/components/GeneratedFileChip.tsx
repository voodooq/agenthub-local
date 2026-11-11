'use client';

import React, { useState } from 'react';
import {
  Eye,
  ExternalLink,
  FileArchive,
  FileCode,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  ImageIcon,
  Loader2,
} from 'lucide-react';
import { formatFileSize } from '@/utils/attachmentPolicy';
import { extractFileBaseName } from '@/utils/filePathDetector';

export interface GeneratedFileChipProps {
  /** 原始文件引用路径（如 `./output/排期表.xlsx`） */
  filePath: string;
  /** 可选自定义展示文案（默认展示文件路径或文件名） */
  label?: string;
  /** 可选已核验磁盘字节大小 */
  fileSize?: number;
  /** 可选已核验磁盘修改时间（ISO 字符串） */
  mtime?: string;
  /** `inline` 用于 Markdown 正文行内替换；`card` 用于消息底栏“生成文件直达”胶囊条 */
  variant?: 'inline' | 'card';
  onLaunch: (filePath: string) => Promise<void> | void;
  onReveal: (filePath: string) => Promise<void> | void;
  onPreview?: (filePath: string) => Promise<void> | void;
}

/**
 * 根据文件扩展名返回图标类型与配色主题
 */
export function getFileIconMeta(filePath: string): {
  kind: 'sheet' | 'image' | 'code' | 'archive' | 'doc';
  colorClass: string;
} {
  const clean = filePath.split('#')[0].split('?')[0].toLowerCase();
  if (/\.(xlsx?|xlsm|csv|tsv)$/.test(clean)) {
    return { kind: 'sheet', colorClass: 'text-emerald-600 dark:text-emerald-400' };
  }
  if (/\.(png|jpe?g|webp|gif|svg|bmp|ico)$/.test(clean)) {
    return { kind: 'image', colorClass: 'text-purple-600 dark:text-purple-400' };
  }
  if (/\.(zip|7z|tar|gz|rar)$/.test(clean)) {
    return { kind: 'archive', colorClass: 'text-amber-600 dark:text-amber-400' };
  }
  if (/\.(py|tsx?|jsx?|mjs|cjs|json|jsonl|ya?ml|toml|xml|html?|css|scss|sql|sh|ps1|bat|cmd|java|go|rs|c|cpp|h|hpp|cs|php|rb|vue|svelte|mmd|mermaid)$/.test(clean)) {
    return { kind: 'code', colorClass: 'text-sky-600 dark:text-sky-400' };
  }
  return { kind: 'doc', colorClass: 'text-amber-600 dark:text-amber-400' };
}

function renderFileKindIcon(filePath: string, className = 'w-3.5 h-3.5 shrink-0') {
  const { kind, colorClass } = getFileIconMeta(filePath);
  const fullClass = `${className} ${colorClass}`;
  switch (kind) {
    case 'sheet':
      return <FileSpreadsheet className={fullClass} />;
    case 'image':
      return <ImageIcon className={fullClass} />;
    case 'code':
      return <FileCode className={fullClass} />;
    case 'archive':
      return <FileArchive className={fullClass} />;
    default:
      return <FileText className={fullClass} />;
  }
}

/**
 * 可交互生成文件直开芯片组件。
 * 注意：行内场景下严格仅使用 span + button（inline-flex），避免在 Markdown <p> 标签内产生非法 <div> 嵌套。
 */
export function GeneratedFileChip({
  filePath,
  label,
  fileSize,
  mtime,
  variant = 'inline',
  onLaunch,
  onReveal,
  onPreview,
}: GeneratedFileChipProps) {
  const [busyAction, setBusyAction] = useState<'launch' | 'reveal' | 'preview' | null>(null);

  const runAction = async (
    action: 'launch' | 'reveal' | 'preview',
    fn?: (fp: string) => Promise<void> | void
  ) => {
    if (!fn || busyAction) return;
    setBusyAction(action);
    try {
      await fn(filePath);
    } finally {
      setBusyAction(null);
    }
  };

  const isCard = variant === 'card';
  const baseName = extractFileBaseName(filePath);
  const displayText = label || (isCard ? baseName : filePath);
  const sizeLabel = typeof fileSize === 'number' && fileSize >= 0 ? formatFileSize(fileSize) : '';
  const metaParts = [filePath, sizeLabel, mtime ? mtime.replace('T', ' ') : ''].filter(Boolean);
  const launchTitle = `${metaParts.join(' · ')} · 点击直接在系统默认应用中打开`;

  return (
    <span
      className={
        isCard
          ? 'inline-flex items-center rounded-xl border border-emerald-500/30 bg-emerald-500/8 hover:bg-emerald-500/14 hover:border-emerald-500/50 transition-all shadow-xs max-w-full align-middle'
          : 'inline-flex items-center rounded-md border border-emerald-500/30 bg-emerald-500/10 hover:bg-emerald-500/18 hover:border-emerald-500/50 transition-all mx-0.5 my-0.5 max-w-full align-baseline'
      }
    >
      {/* 主点击区域：左键直接在系统默认应用中打开 */}
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          runAction('launch', onLaunch);
        }}
        disabled={busyAction !== null}
        title={launchTitle}
        className={
          isCard
            ? 'inline-flex items-center space-x-2 px-2.5 py-1.5 text-xs font-medium text-foreground hover:text-emerald-600 dark:hover:text-emerald-300 cursor-pointer min-w-0 disabled:opacity-60'
            : 'inline-flex items-center space-x-1.5 px-1.5 py-0.5 font-mono text-[12px] text-emerald-700 dark:text-emerald-300 hover:text-emerald-800 dark:hover:text-emerald-200 cursor-pointer min-w-0 disabled:opacity-60'
        }
      >
        {busyAction === 'launch' ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-500 shrink-0" />
        ) : (
          renderFileKindIcon(filePath)
        )}
        <span data-no-i18n="true" className="truncate max-w-[260px] sm:max-w-[360px]">{displayText}</span>
        {isCard && filePath !== displayText && (
          <span data-no-i18n="true" className="hidden md:inline font-mono text-[10px] text-muted-foreground truncate max-w-[180px]">
            ({filePath})
          </span>
        )}
        {isCard && sizeLabel && (
          <span data-no-i18n="true" className="text-[10px] font-mono text-emerald-600/80 dark:text-emerald-300/80 shrink-0">
            {sizeLabel}
          </span>
        )}
        <ExternalLink className="w-3 h-3 text-emerald-500/75 shrink-0" />
      </button>

      {/* 右侧微型辅操作组：画布预览 + 所在文件夹定位 */}
      <span className="inline-flex items-center border-l border-emerald-500/25 px-0.5 space-x-0.5 shrink-0">
        {onPreview && (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              runAction('preview', onPreview);
            }}
            disabled={busyAction !== null}
            title="在右侧画布中预览"
            className="p-1 rounded hover:bg-emerald-500/20 text-muted-foreground hover:text-emerald-600 dark:hover:text-emerald-300 transition-colors cursor-pointer disabled:opacity-50"
          >
            {busyAction === 'preview' ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Eye className="w-3 h-3" />
            )}
          </button>
        )}
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            runAction('reveal', onReveal);
          }}
          disabled={busyAction !== null}
          title="打开所在文件夹并选中"
          className="p-1 rounded hover:bg-emerald-500/20 text-muted-foreground hover:text-blue-500 transition-colors cursor-pointer disabled:opacity-50"
        >
          {busyAction === 'reveal' ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <FolderOpen className="w-3 h-3" />
          )}
        </button>
      </span>
    </span>
  );
}
