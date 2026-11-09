'use client';

import React, { createContext, useContext, useMemo } from 'react';
import ReactMarkdown, { defaultUrlTransform, Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { CodeBlock } from '@/components/CodeBlock';
import { GeneratedFileChip } from '@/components/GeneratedFileChip';
import { InteractiveInquiryCard } from '@/components/InteractiveInquiryCard';
import {
  AGENTHUB_FILE_SCHEME,
  cleanFilePathCandidate,
  isNavigableLocalFilePath,
  linkifyBareFilePathsInMarkdown,
} from '@/utils/filePathDetector';
import { normalizeInteractiveInquiryMarkdown } from '@/utils/inquiryWorkflow';
import {
  ConfirmedFactItem,
  InquirySubmitAction,
  PlanStepItem,
} from '@/types';

const REMARK_PLUGINS = [remarkGfm];

interface MarkdownMessageRuntimeContextValue {
  isStreaming: boolean;
  inquiryKey: string;
  isInquirySubmitted: boolean;
  isExecutionContext: boolean;
  onMarkInquirySubmitted: (key: string) => void;
  onSendInquiryFeedback: (
    feedback: string,
    confirmedFacts?: ConfirmedFactItem[],
    inquiryAction?: InquirySubmitAction,
    loopGoalHint?: string
  ) => void;
  onFillTemplate: (tmpl: string) => void;
  onApprovePlan: (
    steps: PlanStepItem[],
    feedback: string,
    confirmedFacts?: ConfirmedFactItem[],
    loopGoalHint?: string
  ) => void;
  onOpenCanvas: (code: string, language: string, customTitle?: string) => void;
  onSaveToWorkspace: (code: string, language: string) => void;
  onOpenGeneratedFile: (rawPath: string, mode: 'launch' | 'reveal') => void;
  onPreviewGeneratedFile: (rawPath: string) => void;
}

const MarkdownMessageRuntimeContext =
  createContext<MarkdownMessageRuntimeContextValue | null>(null);

function markdownUrlTransform(url: string): string {
  if (
    url.startsWith(AGENTHUB_FILE_SCHEME) ||
    url.toLowerCase().startsWith('file://') ||
    isNavigableLocalFilePath(url)
  ) {
    return url;
  }
  return defaultUrlTransform(url);
}

function normalizeEscapedContent(content: string): string {
  if (!content) return '';
  let res = content;
  if (
    res.includes('\\n') &&
    (res.includes('\\nimport ') ||
      res.includes('\\nfrom ') ||
      res.includes('\\ndef ') ||
      res.includes('\\nclass ') ||
      res.includes('\\nasync '))
  ) {
    res = res.replace(/\\n/g, '\n').replace(/\\t/g, '  ');
  }
  if (
    !res.trim().startsWith('```') &&
    (res.trim().startsWith('import ') ||
      res.trim().startsWith('from ') ||
      res.trim().startsWith('async def ') ||
      res.trim().startsWith('def ') ||
      res.trim().startsWith('asyncio'))
  ) {
    res = '```python\n' + res.trim() + '\n```';
  }
  return res;
}

function extractPlainText(childrenNode: React.ReactNode): string {
  if (typeof childrenNode === 'string' || typeof childrenNode === 'number') {
    return String(childrenNode);
  }
  if (Array.isArray(childrenNode)) {
    return childrenNode.map(extractPlainText).join('');
  }
  if (
    React.isValidElement<{ children?: React.ReactNode }>(childrenNode) &&
    childrenNode.props?.children
  ) {
    return extractPlainText(childrenNode.props.children);
  }
  return '';
}

/**
 * 稳定消费上下文的交互卡片插槽，彻底隔离父组件轮询重渲染对卡片子树的 unmount/remount。
 */
function InquiryCardSlot({ rawContent }: { rawContent: string }) {
  const ctx = useContext(MarkdownMessageRuntimeContext);
  return (
    <InteractiveInquiryCard
      rawContent={rawContent}
      inquiryKey={ctx?.inquiryKey}
      initialSubmitted={ctx?.isInquirySubmitted}
      isExecutionContext={ctx?.isExecutionContext ?? true}
      onMarkSubmitted={ctx?.onMarkInquirySubmitted}
      onSendFeedback={ctx?.onSendInquiryFeedback}
      onFillTemplate={ctx?.onFillTemplate}
      onApprovePlan={ctx?.onApprovePlan}
      isStreaming={ctx?.isStreaming ?? false}
    />
  );
}

function CodeRenderer({ inline, className, children, ...props }: any) {
  const ctx = useContext(MarkdownMessageRuntimeContext);
  const match = /language-(\w+)/.exec(className || '');
  const language = match ? match[1] : '';
  const rawChildrenText = String(children ?? '');
  const codeString = rawChildrenText.replace(/\n$/, '');

  if (!inline && language.toLowerCase() === 'interactive_inquiry') {
    return <InquiryCardSlot rawContent={codeString} />;
  }

  if (!inline && language) {
    return (
      <CodeBlock
        code={codeString}
        language={language}
        onSendToCanvas={(c: string, l: string) => ctx?.onOpenCanvas(c, l)}
        onSaveToWorkspace={ctx?.onSaveToWorkspace}
      />
    );
  }

  // 仅对单行行内代码做本地文件路径直开识别（严格避开多行代码块）
  if (
    inline !== false &&
    !language &&
    !rawChildrenText.includes('\n') &&
    isNavigableLocalFilePath(codeString)
  ) {
    return (
      <GeneratedFileChip
        filePath={cleanFilePathCandidate(codeString)}
        variant="inline"
        onLaunch={(fp) => ctx?.onOpenGeneratedFile(fp, 'launch')}
        onReveal={(fp) => ctx?.onOpenGeneratedFile(fp, 'reveal')}
        onPreview={(fp) => ctx?.onPreviewGeneratedFile(fp)}
      />
    );
  }

  return (
    <code
      className="bg-surface-200 border border-border px-1.5 py-0.5 rounded font-mono text-[12px] text-amber-600 dark:text-amber-300 break-anywhere"
      {...props}
    >
      {children}
    </code>
  );
}

function LinkRenderer({ href, children, ...props }: any) {
  const ctx = useContext(MarkdownMessageRuntimeContext);
  const hrefStr = String(href || '').trim();
  if (hrefStr.startsWith(AGENTHUB_FILE_SCHEME)) {
    const decodedPath = decodeURIComponent(hrefStr.slice(AGENTHUB_FILE_SCHEME.length));
    return (
      <GeneratedFileChip
        filePath={decodedPath}
        variant="inline"
        onLaunch={(fp) => ctx?.onOpenGeneratedFile(fp, 'launch')}
        onReveal={(fp) => ctx?.onOpenGeneratedFile(fp, 'reveal')}
        onPreview={(fp) => ctx?.onPreviewGeneratedFile(fp)}
      />
    );
  }
  if (hrefStr && isNavigableLocalFilePath(hrefStr)) {
    const labelText = extractPlainText(children).trim();
    const customLabel = labelText && labelText !== hrefStr ? labelText : undefined;
    return (
      <GeneratedFileChip
        filePath={hrefStr}
        label={customLabel}
        variant="inline"
        onLaunch={(fp) => ctx?.onOpenGeneratedFile(fp, 'launch')}
        onReveal={(fp) => ctx?.onOpenGeneratedFile(fp, 'reveal')}
        onPreview={(fp) => ctx?.onPreviewGeneratedFile(fp)}
      />
    );
  }
  const isAnchorOnly = hrefStr.startsWith('#');
  return (
    <a
      href={hrefStr}
      target={isAnchorOnly ? undefined : '_blank'}
      rel={isAnchorOnly ? undefined : 'noopener noreferrer'}
      className="text-emerald-600 dark:text-emerald-400 underline underline-offset-2 hover:text-emerald-500 transition-colors"
      {...props}
    >
      {children}
    </a>
  );
}

function ImageRenderer({ src, alt, ...props }: any) {
  const ctx = useContext(MarkdownMessageRuntimeContext);
  if (!src) return null;
  return (
    <span className="block my-3">
      <img
        src={src}
        alt={alt || 'AI 生成插画'}
        className="rounded-xl border border-border shadow-lg max-w-full h-auto object-cover max-h-[512px] hover:scale-[1.01] transition-transform cursor-pointer"
        onClick={() =>
          ctx?.onOpenCanvas(src, 'image', alt ? `画作预览: ${alt}` : '生成的 AI 画作原图预览')
        }
        {...props}
      />
      {alt && (
        <span className="block text-xs text-muted-foreground mt-1 italic text-center">
          {alt}（点击可在右侧画布全屏查看大图）
        </span>
      )}
    </span>
  );
}

// 模块级恒定映射：保证 ReactMarkdown 永远收到相同引用，绝不因外层重绘导致子组件卸载重建
const STABLE_MARKDOWN_COMPONENTS: Components = {
  code: CodeRenderer,
  a: LinkRenderer,
  img: ImageRenderer,
};

interface AssistantMarkdownMessageProps {
  content: string;
  isStreaming: boolean;
  inquiryKey: string;
  isInquirySubmitted: boolean;
  isExecutionContext: boolean;
  onMarkInquirySubmitted: (key: string) => void;
  onSendInquiryFeedback: (
    feedback: string,
    confirmedFacts?: ConfirmedFactItem[],
    inquiryAction?: InquirySubmitAction,
    loopGoalHint?: string
  ) => void;
  onFillTemplate: (tmpl: string) => void;
  onApprovePlan: (
    steps: PlanStepItem[],
    feedback: string,
    confirmedFacts?: ConfirmedFactItem[],
    loopGoalHint?: string
  ) => void;
  onOpenCanvas: (code: string, language: string, customTitle?: string) => void;
  onSaveToWorkspace: (code: string, language: string) => void;
  onOpenGeneratedFile: (rawPath: string, mode: 'launch' | 'reveal') => void;
  onPreviewGeneratedFile: (rawPath: string) => void;
}

function AssistantMarkdownMessageInner({
  content,
  isStreaming,
  inquiryKey,
  isInquirySubmitted,
  isExecutionContext,
  onMarkInquirySubmitted,
  onSendInquiryFeedback,
  onFillTemplate,
  onApprovePlan,
  onOpenCanvas,
  onSaveToWorkspace,
  onOpenGeneratedFile,
  onPreviewGeneratedFile,
}: AssistantMarkdownMessageProps) {
  const renderedMarkdown = useMemo(() => {
    const normalizedEscaped = normalizeEscapedContent(content);
    const normalizedInquiry = normalizeInteractiveInquiryMarkdown(normalizedEscaped);
    return isStreaming
      ? normalizedInquiry
      : linkifyBareFilePathsInMarkdown(normalizedInquiry);
  }, [content, isStreaming]);

  const contextValue = useMemo<MarkdownMessageRuntimeContextValue>(
    () => ({
      isStreaming,
      inquiryKey,
      isInquirySubmitted,
      isExecutionContext,
      onMarkInquirySubmitted,
      onSendInquiryFeedback,
      onFillTemplate,
      onApprovePlan,
      onOpenCanvas,
      onSaveToWorkspace,
      onOpenGeneratedFile,
      onPreviewGeneratedFile,
    }),
    [
      isStreaming,
      inquiryKey,
      isInquirySubmitted,
      isExecutionContext,
      onMarkInquirySubmitted,
      onSendInquiryFeedback,
      onFillTemplate,
      onApprovePlan,
      onOpenCanvas,
      onSaveToWorkspace,
      onOpenGeneratedFile,
      onPreviewGeneratedFile,
    ]
  );

  return (
    <MarkdownMessageRuntimeContext.Provider value={contextValue}>
      <div className="prose dark:prose-invert max-w-none text-foreground break-anywhere">
        {content ? (
          <ReactMarkdown
            remarkPlugins={REMARK_PLUGINS}
            urlTransform={markdownUrlTransform}
            components={STABLE_MARKDOWN_COMPONENTS}
          >
            {renderedMarkdown}
          </ReactMarkdown>
        ) : (
          <div className="flex items-center space-x-2 text-muted-foreground text-xs py-1">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-ping shrink-0" />
            <span>正在调度本地执行与安全分析...</span>
          </div>
        )}
      </div>
    </MarkdownMessageRuntimeContext.Provider>
  );
}

export const AssistantMarkdownMessage = React.memo(AssistantMarkdownMessageInner);

