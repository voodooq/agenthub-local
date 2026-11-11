'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Send,
  Square,
  PanelRight,
  RefreshCw,
  Plus,
  Paperclip,
  X,
  Sparkles,
  Sliders,
  Brain,
  ClipboardList,
  EyeOff,
  AlertTriangle,
  CheckCircle2,
  Compass,
  UploadCloud,
  FileText,
  FileSpreadsheet,
  Info,
  PackageOpen,
  Maximize2,
  Minimize2,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';

import { Sidebar, MainNavHub } from '@/components/Sidebar';
import { LibraryView } from '@/components/LibraryView';
import { ScheduledView } from '@/components/ScheduledView';
import { SkillsView } from '@/components/SkillsView';
import { StatusPill } from '@/components/StatusPill';
import { ModelSelector, ThinkingLevel, ModelOption } from '@/components/ModelSelector';
import { ThinkingBlock } from '@/components/ThinkingBlock';
import { MessageActions } from '@/components/MessageActions';
import { PromptStarterCards } from '@/components/PromptStarterCards';
import { CanvasDrawer } from '@/components/CanvasDrawer';
import { SearchModal } from '@/components/SearchModal';
import { AccountModal } from '@/components/AccountModal';
import { SyncModal } from '@/components/SyncModal';
import { SettingsModal, ActiveModelsState } from '@/components/SettingsModal';
import { WorkMode } from '@/components/TopModeSwitcher';
import { HardwareHud } from '@/components/HardwareHud';
import { RightActionDrawer } from '@/components/RightActionDrawer';
import { CommandAutocomplete } from '@/components/CommandAutocomplete';
import { ThemeToggle } from '@/components/ThemeToggle';
import { LanguageToggle } from '@/components/LanguageToggle';
import { MemoryHubModal, MemoryHubTab } from '@/components/MemoryHubModal';
import { InlineToolCard } from '@/components/InlineToolCard';
import { ImportWizardModal } from '@/components/ImportWizardModal';
import { LoopHUD } from '@/components/LoopHUD';
import { ApprovalCard } from '@/components/ApprovalCard';
import { SkillPickerModal } from '@/components/SkillPickerModal';
import { SopDrawer } from '@/components/SopDrawer';
import { AssistantMarkdownMessage } from '@/components/AssistantMarkdownMessage';
import { TokenMetricsFooterBar } from '@/components/TokenMetricsFooterBar';
import { TokenStatsModal } from '@/components/TokenStatsModal';

import { GeneratedFileChip } from '@/components/GeneratedFileChip';
import { buildActiveSystemOverride } from '@/utils/personalityHelper';
import { generateScenarioThinking } from '@/utils/scenarioThinking';
import { sanitizeAssistantContent } from '@/utils/toolTagSanitizer';
import {
  Conversation,
  Message,
  SearchResult,
  StatusPillData,
  SceneMode,
  ToolExecutionCardItem,
  Workspace,
  TaskLoopResponse,
  SkillItem,
  ExecutionStrategy,
  InquirySubmitAction,
  PlanStepItem,
  ConfirmedFactItem,
  MemoryFactItem,
  TurnTokenMetrics,
} from '@/types';
import { apiFetch, getAccessToken } from '@/utils/apiClient';
import {
  cleanFilePathCandidate,
  extractGeneratedFilePaths,
  isNavigableLocalFilePath,
} from '@/utils/filePathDetector';
import {
  computeInquiryCardLocked,
  hasInteractiveInquiryBlock,
  normalizeInteractiveInquiryMarkdown,
  resolveMessageInquiryKey,
} from '@/utils/inquiryWorkflow';
import {
  fetchExtensionSkills,
  fetchWorkspaceSkills,
  prefetchExtensionSkills,
  prefetchWorkspaceSkills,
} from '@/utils/skillCache';
import {
  AttachmentCategory,
  FILE_INPUT_ACCEPT,
  MAX_ATTACHMENTS_PER_MESSAGE,
  ParsedUserAttachment,
  SUPPORTED_ATTACHMENT_GROUPS,
  buildMessageWithAttachmentsAnchor,
  formatFileSize,
  parseMessageAttachments,
  validateAttachmentCandidate,
} from '@/utils/attachmentPolicy';

export default function WorkbenchPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [activeLoop, setActiveLoop] = useState<TaskLoopResponse | null>(null);
  const [isSkillPickerOpen, setIsSkillPickerOpen] = useState(false);
  const [selectedSopSkill, setSelectedSopSkill] = useState<SkillItem | null>(null);
  const [inputPrompt, setInputPrompt] = useState('');
  const [isInputExpanded, setIsInputExpanded] = useState(false);
  const [inputCursorPos, setInputCursorPos] = useState<number>(0);
  const [skillCatalog, setSkillCatalog] = useState<
    Array<{ id: string; name: string; command: string; version: string }>
  >([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const isStreamingRef = useRef(false);
  type StreamAbortReason = 'none' | 'user' | 'switch' | 'connect_timeout' | 'idle_timeout';
  interface ActiveStreamMeta {
    controller: AbortController;
    convId: string | null;
    reason: StreamAbortReason;
  }
  const activeStreamMetaRef = useRef<ActiveStreamMeta | null>(null);
  const lastStreamErrorNoticeRef = useRef<{ convId: string; message: string } | null>(null);
  const lastStatusPillSigRef = useRef<string>('');
  const lastActiveLoopSigRef = useRef<string>('');
  const [statusPill, setStatusPill] = useState<StatusPillData | null>(null);

  // 当前选中模型及可用模型列表
  const [currentModel, setCurrentModel] = useState<string>('');
  const hasUserSelectedModelRef = useRef(false);
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [modelAccountInfo, setModelAccountInfo] = useState<{
    accountName: string;
    authLabel: string;
    isTested: boolean;
  }>({ accountName: '', authLabel: '', isTested: false });
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [settingsInitialTab, setSettingsInitialTab] = useState<'models' | 'appearance' | 'personality'>('models');
  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel>('high');

  // 工作模式与系统治理
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string>('default');
  const [activeNavHub, setActiveNavHub] = useState<MainNavHub>('chat');
  const [saveFileToast, setSaveFileToast] = useState<string | null>(null);
  const [saveFileToastType, setSaveFileToastType] = useState<'success' | 'warning' | 'error'>('success');
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showFloatingToast = useCallback(
    (msg: string, variant: 'success' | 'warning' | 'error' = 'success', durationMs = 3800) => {
      if (toastTimerRef.current) {
        clearTimeout(toastTimerRef.current);
      }
      setSaveFileToastType(variant);
      setSaveFileToast(msg);
      toastTimerRef.current = setTimeout(() => {
        setSaveFileToast(null);
        toastTimerRef.current = null;
      }, durationMs);
    },
    []
  );
  const stopActiveStream = useCallback(
    async (reason: 'user' | 'switch' = 'user') => {
      const meta = activeStreamMetaRef.current;
      if (!meta && !isStreamingRef.current) return;
      if (meta) {
        meta.reason = reason;
        meta.controller.abort();
      }
      const targetConvId = meta?.convId || activeConvIdRef.current;
      if (targetConvId) {
        try {
          await apiFetch('/api/v1/chat/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conversation_id: targetConvId }),
          });
        } catch {
          // 忽略停止接口的瞬时网络波动，前端已先中止读取连接
        }
      }
      if (reason === 'user') {
        showFloatingToast('已发起停止生成请求', 'warning', 2500);
      }
    },
    [showFloatingToast]
  );
  const [workMode, setWorkMode] = useState<WorkMode>('dev');
  const [isHudOpen, setIsHudOpen] = useState(false);
  const [isActionDrawerOpen, setIsActionDrawerOpen] = useState(false);
  const [pendingActionCount, setPendingActionCount] = useState(0);
  const [workspacePath, setWorkspacePath] = useState<string>('AgentHub Workspace');

  // 附件、上传限制提示与拖拽状态
  interface AttachmentItem {
    name: string;
    size: string;
    type: string; // 'image' | 'text' | 'file'
    category?: AttachmentCategory;
    dataUrl?: string;
    filePath?: string;
    relPath?: string;
    url?: string;
    mimeType?: string;
    textContent?: string;
  }
  const [attachments, setAttachments] = useState<AttachmentItem[]>([]);
  const [attachmentRejectionNotices, setAttachmentRejectionNotices] = useState<string[]>([]);
  const [showSupportedFormatsHelp, setShowSupportedFormatsHelp] = useState(false);
  const [isUploadingAttachment, setIsUploadingAttachment] = useState(false);
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const dragCounterRef = useRef(0);
  const [isListening, setIsListening] = useState(false);

  // 抽屉与模态框
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [isAccountOpen, setIsAccountOpen] = useState(false);
  const [isSyncOpen, setIsSyncOpen] = useState(false);
  const [isMemoryHubOpen, setIsMemoryHubOpen] = useState(false);
  const [memoryHubInitialTab, setMemoryHubInitialTab] = useState<MemoryHubTab>('memories');
  const [isTokenStatsModalOpen, setIsTokenStatsModalOpen] = useState(false);

  // 三层记忆与微内核 Agent 状态
  const [sceneMode, setSceneMode] = useState<SceneMode>('agent');
  const [incognito, setIncognito] = useState<boolean>(false);
  const [workingMemory, setWorkingMemory] = useState<Record<string, any>>({});
  // 控制超长 ReAct 过程步骤文字折叠/展开的局部状态映射
  const [expandedProcessMsgKeys, setExpandedProcessMsgKeys] = useState<Record<string, boolean>>({});
  const toggleProcessMsgExpanded = useCallback((msgKey: string) => {
    setExpandedProcessMsgKeys((prev) => ({ ...prev, [msgKey]: !prev[msgKey] }));
  }, []);

  // v1.1.2 执行策略与交互对齐状态
  const [executionStrategy, setExecutionStrategy] = useState<ExecutionStrategy>('direct');
  const [clarifyMode, setClarifyMode] = useState<boolean>(false);

  useEffect(() => {
    try {
      const savedStrategy = (localStorage.getItem('agenthub_exec_strategy') || localStorage.getItem('geminihub_exec_strategy')) as ExecutionStrategy;
      if (savedStrategy === 'direct' || savedStrategy === 'plan_first') {
        setExecutionStrategy(savedStrategy);
      }
      const savedClarify = localStorage.getItem('agenthub_clarify_mode') ?? localStorage.getItem('geminihub_clarify_mode');
      if (savedClarify !== null) {
        setClarifyMode(savedClarify === 'true');
      }
    } catch { }
  }, []);

  const handleSwitchExecStrategy = (strategy: ExecutionStrategy) => {
    setExecutionStrategy(strategy);
    try {
      localStorage.setItem('agenthub_exec_strategy', strategy);
    } catch { }
  };

  const handleToggleClarifyMode = () => {
    setClarifyMode((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('agenthub_clarify_mode', String(next));
      } catch { }
      return next;
    });
  };

  // 留空填空模板一键填入并高亮选中第一个占位符 {{...}}
  const handleFillTemplateToInput = (templateText: string) => {
    setInputPrompt(templateText);
    setTimeout(() => {
      if (textareaRef.current) {
        textareaRef.current.focus();
        const match = /\{\{([^}]+)\}\}/.exec(templateText);
        if (match) {
          textareaRef.current.setSelectionRange(match.index, match.index + match[0].length);
        } else {
          textareaRef.current.setSelectionRange(templateText.length, templateText.length);
        }
      }
    }, 50);
  };

  // 用户在方案卡片中确认批准步骤清单并启动自主执行
  const handleApproveAndExecutePlan = async (
    steps: PlanStepItem[],
    feedbackText: string,
    confirmedFacts?: ConfirmedFactItem[],
    loopGoalHint?: string
  ) => {
    // 联动自动将当前会话/界面模式升舱为 'agent'
    if (sceneMode !== 'agent') {
      setSceneMode('agent');
      try {
        localStorage.setItem('agenthub_scene_mode', 'agent');
      } catch { }
    }
    await handleSendMessage(feedbackText, steps, confirmedFacts, undefined, {
      inquiryConfirmed: true,
      inquiryAction: 'execute_now',
      loopGoalOverride: loopGoalHint,
    });
  };

  // 已提交确认卡片持久化锁定键集合（按会话隔离保存在 localStorage）
  const [submittedInquiryKeys, setSubmittedInquiryKeys] = useState<Set<string>>(() => new Set());
  const activeConvIdRef = useRef<string | null>(activeConvId);
  activeConvIdRef.current = activeConvId;

  useEffect(() => {
    if (!activeConvId) {
      setSubmittedInquiryKeys(new Set());
      return;
    }
    try {
      const storageKey = `agenthub_submitted_inquiries_${activeConvId}`;
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        const parsedKeys = JSON.parse(raw);
        if (Array.isArray(parsedKeys)) {
          setSubmittedInquiryKeys(new Set<string>(parsedKeys.map(String)));
          return;
        }
      }
    } catch { }
    setSubmittedInquiryKeys(new Set());
  }, [activeConvId]);

  const handleMarkInquirySubmitted = useCallback((inquiryKey: string) => {
    if (!inquiryKey) return;
    setSubmittedInquiryKeys((prev) => {
      if (prev.has(inquiryKey)) return prev;
      const next = new Set(prev);
      next.add(inquiryKey);
      const cid = activeConvIdRef.current;
      if (cid) {
        try {
          localStorage.setItem(
            `agenthub_submitted_inquiries_${cid}`,
            JSON.stringify(Array.from(next).slice(-100))
          );
        } catch { }
      }
      return next;
    });
  }, []);

  // 流式结束重载数据库消息时，保留最新一轮收到的 fact_refs 溯源状态
  const lastStreamFactRefsRef = useRef<{ convId: string; facts: MemoryFactItem[] } | null>(null);
  // 流式结束重载消息时保活最新一轮实时 token_stats 水表（兼顾无痕模式不闪灭）
  const lastStreamTokenStatsRef = useRef<{ convId: string; stats: TurnTokenMetrics } | null>(null);

  const [canvasState, setCanvasState] = useState<{
    isOpen: boolean;
    title: string;
    content: string;
    type: 'html' | 'mermaid' | 'code' | 'image';
  }>({ isOpen: false, title: '', content: '', type: 'code' });

  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollRafRef = useRef<number | null>(null);
  const scrollForceRef = useRef<boolean>(false);
  const shouldForceScrollOnLoadRef = useRef<boolean>(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pendingJumpTargetRef = useRef<{ convId: string; messageId?: number } | null>(null);
  const [highlightedMessageId, setHighlightedMessageId] = useState<number | null>(null);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scrollToBottom = useCallback((force = false) => {
    scrollForceRef.current = scrollForceRef.current || force;
    if (scrollRafRef.current !== null) return;
    scrollRafRef.current = requestAnimationFrame(() => {
      const shouldForce = scrollForceRef.current;
      scrollForceRef.current = false;
      scrollRafRef.current = null;
      const container = messagesContainerRef.current;
      if (container) {
        const distanceFromBottom =
          container.scrollHeight - container.scrollTop - container.clientHeight;
        if (!shouldForce && distanceFromBottom > 160) {
          return;
        }
        container.scrollTop = container.scrollHeight;
        return;
      }
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
    });
  }, []);

  useEffect(() => {
    return () => {
      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current);
      }
      if (scrollRafRef.current !== null) {
        cancelAnimationFrame(scrollRafRef.current);
        scrollRafRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const pendingJump = pendingJumpTargetRef.current;
    if (pendingJump && (!pendingJump.convId || pendingJump.convId === activeConvIdRef.current)) {
      if (pendingJump.messageId) {
        const matchedMsg = messages.find(
          (m) =>
            m.id === pendingJump.messageId ||
            m.merged_tool_message_ids?.includes(pendingJump.messageId as number)
        );
        if (matchedMsg && matchedMsg.id) {
          pendingJumpTargetRef.current = null;
          const hostId = matchedMsg.id;
          const timer = setTimeout(() => {
            const el = document.getElementById(`chat-msg-${hostId}`);
            if (el) {
              el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
            setHighlightedMessageId(hostId);
            if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
            highlightTimerRef.current = setTimeout(() => setHighlightedMessageId(null), 2800);
          }, 60);
          return () => clearTimeout(timer);
        }
        // 已切换到目标会话并拿到消息列表但未找到该条记录（如已删除），释放标记后回退至底部
        if (messages.length > 0 && messages[0].conversation_id === pendingJump.convId) {
          pendingJumpTargetRef.current = null;
        } else {
          return;
        }
      } else {
        pendingJumpTargetRef.current = null;
      }
    }
    const shouldForce = shouldForceScrollOnLoadRef.current;
    shouldForceScrollOnLoadRef.current = false;
    scrollToBottom(shouldForce);
  }, [messages, scrollToBottom]);

  // 实时捕获当前会话中等待用户确认授权的工具调用（无论页面滚到哪里，始终支持底部直达授权）
  const pendingApprovalEvent = React.useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === 'model' && m.tool_events) {
        const found = m.tool_events.find((ev) => ev.status === 'require_approval');
        if (found) return found;
      }
    }
    return null;
  }, [messages]);

  // 防止文件拖出输入区域外时触发 WebView/浏览器直接导航打开本地文件
  useEffect(() => {
    const preventWindowFileNavigation = (e: DragEvent) => {
      if (e.dataTransfer?.types && Array.from(e.dataTransfer.types).includes('Files')) {
        e.preventDefault();
      }
    };
    window.addEventListener('dragover', preventWindowFileNavigation);
    window.addEventListener('drop', preventWindowFileNavigation);
    return () => {
      window.removeEventListener('dragover', preventWindowFileNavigation);
      window.removeEventListener('drop', preventWindowFileNavigation);
    };
  }, []);

  // 全局 F11 全屏切换快捷键支持
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'F11') {
        e.preventDefault();
        if (!document.fullscreenElement) {
          document.documentElement.requestFullscreen().catch(() => { });
        } else {
          document.exitFullscreen().catch(() => { });
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const fetchActiveLoop = useCallback(async () => {
    if (!activeWorkspaceId || !activeConvId) {
      if (lastActiveLoopSigRef.current !== 'null') {
        lastActiveLoopSigRef.current = 'null';
        setActiveLoop(null);
      }
      return;
    }
    const reqConvId = activeConvId;
    try {
      const res = await apiFetch(
        `/api/v1/loop/workspace/${activeWorkspaceId}/active?conversation_id=${encodeURIComponent(reqConvId)}`
      );
      // 防止异步拉取竞态：若请求途中用户已切换其他对话，立即丢弃过期数据
      if (activeConvIdRef.current !== reqConvId) return;
      if (res.ok) {
        const data: TaskLoopResponse | null = await res.json();
        if (data && data.conversation_id !== reqConvId) return;
        const sig = JSON.stringify(data);
        if (sig !== lastActiveLoopSigRef.current) {
          lastActiveLoopSigRef.current = sig;
          setActiveLoop(data);
        }
      } else if (lastActiveLoopSigRef.current !== 'null') {
        lastActiveLoopSigRef.current = 'null';
        setActiveLoop(null);
      }
    } catch (e) { }
  }, [activeWorkspaceId, activeConvId]);

  useEffect(() => {
    fetchActiveLoop();
    const timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      void fetchActiveLoop();
    }, 4000);
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        void fetchActiveLoop();
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [fetchActiveLoop]);


  const fetchWorkspaces = async () => {
    try {
      const res = await apiFetch('/api/v1/workspaces');
      if (res.ok) {
        const data: Workspace[] = await res.json();
        setWorkspaces(data);
        if (data.length > 0 && !data.some((w) => w.id === activeWorkspaceId)) {
          setActiveWorkspaceId(data[0].id);
          setWorkspacePath(data[0].root_path);
        }
      }
    } catch (e) {
      console.warn('获取工作区失败:', e);
    }
  };

  const fetchConversations = async (wsId?: string) => {
    try {
      const targetWsId = wsId !== undefined ? wsId : activeWorkspaceId;
      const url = targetWsId ? `/api/v1/conversations?workspace_id=${targetWsId}` : '/api/v1/conversations';
      const res = await apiFetch(url);
      if (res.ok) {
        const data = await res.json();
        setConversations(data);
      }
    } catch (e) {
      console.warn('获取会话失败:', e);
    }
  };

  const applyActiveModelsState = (data: ActiveModelsState) => {
    const nextModels = data.models || [];
    setAvailableModels(nextModels);
    setModelAccountInfo({
      accountName: data.account_name,
      authLabel: data.auth_label,
      isTested: data.is_tested,
    });
    if (nextModels.length > 0) {
      setCurrentModel((prev) => {
        const defaultModel = nextModels.find((m) => m.is_default);
        const stillValid = Boolean(prev) && nextModels.some((m) => m.id === prev);
        // 若尚未选定模型、原模型已不在可用列表中，或用户未手动指定且存在明确默认模型，自动切换至用户拥有的默认/首个模型
        if (!stillValid) {
          return defaultModel ? defaultModel.id : nextModels[0].id;
        }
        if (defaultModel && !hasUserSelectedModelRef.current) {
          return defaultModel.id;
        }
        return prev;
      });
    } else {
      hasUserSelectedModelRef.current = false;
      setCurrentModel('');
    }
  };

  const fetchActiveModels = async () => {
    try {
      const res = await apiFetch('/api/v1/models/active');
      if (res.ok) {
        const data: ActiveModelsState = await res.json();
        applyActiveModelsState(data);
      }
    } catch (e) {
      console.warn('获取当前账号可用模型失败:', e);
    }
  };

  const fetchStatusPill = async () => {
    try {
      const res = await apiFetch('/api/v1/accounts/status-pill');
      if (res.ok) {
        const data = await res.json();
        const sig = JSON.stringify(data);
        if (sig !== lastStatusPillSigRef.current) {
          lastStatusPillSigRef.current = sig;
          setStatusPill(data);
        }
      }
    } catch (e) {
      console.warn('获取算力状态失败:', e);
    }
  };

  useEffect(() => {
    fetchWorkspaces();
    fetchStatusPill();
    fetchActiveModels();
    prefetchExtensionSkills();
    const pillTimer = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      void fetchStatusPill();
    }, 3000);
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        void fetchStatusPill();
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      clearInterval(pillTimer);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, []);

  const syncSkillCatalog = useCallback(async (wsId?: string) => {
    try {
      const [wsSkills, extSkills] = await Promise.all([
        fetchWorkspaceSkills(wsId).catch(() => []),
        fetchExtensionSkills().catch(() => []),
      ]);
      const mergedMap = new Map<
        string,
        { id: string; name: string; command: string; version: string }
      >();
      for (const sk of wsSkills) {
        const rawCmd = (sk.command || `/${sk.name}`).trim();
        const cmd = rawCmd.startsWith('/') ? rawCmd : `/${rawCmd}`;
        mergedMap.set(cmd.toLowerCase(), {
          id: sk.id || sk.name,
          name: sk.name,
          command: cmd,
          version: sk.version || '1.0.0',
        });
      }
      for (const ex of extSkills) {
        const rawCmd = (ex.command || `/${ex.id || ex.name}`).trim();
        const cmd = rawCmd.startsWith('/') ? rawCmd : `/${rawCmd}`;
        if (!mergedMap.has(cmd.toLowerCase())) {
          mergedMap.set(cmd.toLowerCase(), {
            id: ex.id || ex.name,
            name: ex.name,
            command: cmd,
            version: ex.version || '1.0.0',
          });
        }
      }
      setSkillCatalog(Array.from(mergedMap.values()));
    } catch {
      // 静默忽略技能目录预取波动
    }
  }, []);

  useEffect(() => {
    prefetchWorkspaceSkills(activeWorkspaceId);
    void syncSkillCatalog(activeWorkspaceId);
  }, [activeWorkspaceId, syncSkillCatalog]);

  // 自动适配常态输入框高度（展开态由半屏样式接管高度）
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    if (isInputExpanded) {
      el.style.height = '';
      return;
    }
    el.style.height = 'auto';
    const nextHeight = Math.min(Math.max(el.scrollHeight, 44), 160);
    el.style.height = `${nextHeight}px`;
  }, [inputPrompt, isInputExpanded]);

  // 实时提取当前消息中串联调用的所有 Skill 协同链（去重且保留输入顺序）
  const mountedChainSkills = React.useMemo(() => {
    if (!inputPrompt || skillCatalog.length === 0) return [];
    const byCmd = new Map<string, (typeof skillCatalog)[number]>();
    const byId = new Map<string, (typeof skillCatalog)[number]>();
    for (const item of skillCatalog) {
      byCmd.set(item.command.toLowerCase(), item);
      byId.set(`/${item.id.toLowerCase()}`, item);
    }
    const regex =
      /(?:^|[\s([{"'（【])(\/[a-zA-Z0-9_\u4e00-\u9fa5][a-zA-Z0-9_.\-\u4e00-\u9fa5]*)/g;
    const result: Array<(typeof skillCatalog)[number]> = [];
    const seen = new Set<string>();
    let match: RegExpExecArray | null;
    while ((match = regex.exec(inputPrompt)) !== null) {
      const token = match[1];
      const key = token.toLowerCase();
      const found = byCmd.get(key) || byId.get(key);
      if (found && !seen.has(found.command.toLowerCase())) {
        seen.add(found.command.toLowerCase());
        result.push(found);
      }
    }
    return result;
  }, [inputPrompt, skillCatalog]);

  const handleInsertCommandAtCursor = useCallback(
    (insertText: string, replaceRange?: { start: number; end: number }) => {
      const textarea = textareaRef.current;
      const fallbackPos = textarea?.selectionStart ?? inputCursorPos ?? inputPrompt.length;
      let start = replaceRange ? replaceRange.start : fallbackPos;
      let end = replaceRange ? replaceRange.end : fallbackPos;

      // 若未指定替换范围且光标紧邻孤立 '/'（例如通过底部「/ 技能」弹窗插入），替换该 '/' 避免重复
      if (!replaceRange && start > 0 && inputPrompt[start - 1] === '/' && (start === 1 || /\s/.test(inputPrompt[start - 2]))) {
        start -= 1;
      }

      const needLeadingSpace =
        start > 0 && !/\s|[([{"'（【]/.test(inputPrompt[start - 1]) && !insertText.startsWith(' ');
      const payloadText = `${needLeadingSpace ? ' ' : ''}${insertText}`;
      const nextValue = `${inputPrompt.slice(0, start)}${payloadText}${inputPrompt.slice(end)}`;
      const nextCursor = start + payloadText.length;

      setInputPrompt(nextValue);
      setInputCursorPos(nextCursor);
      requestAnimationFrame(() => {
        if (textareaRef.current) {
          textareaRef.current.focus();
          textareaRef.current.setSelectionRange(nextCursor, nextCursor);
        }
      });
    },
    [inputPrompt, inputCursorPos]
  );

  const handleRemoveSkillToken = useCallback((cmd: string) => {
    const escaped = cmd.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const removeRe = new RegExp(`(^|[\\s([{"'（【])${escaped}(?=\\s|$|[)\\]}"'）】])`, 'gi');
    setInputPrompt((prev) =>
      prev
        .replace(removeRe, (_m, prefix: string) => prefix)
        .replace(/[ \t]{2,}/g, ' ')
        .replace(/^\s+/, '')
    );
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, []);

  useEffect(() => {
    fetchConversations(activeWorkspaceId);
    const curr = workspaces.find((w) => w.id === activeWorkspaceId);
    if (curr) {
      setWorkspacePath(curr.root_path);
    }
  }, [activeWorkspaceId, workspaces]);

  useEffect(() => {
    if (statusPill?.active_account_name) {
      fetchActiveModels();
    }
  }, [statusPill?.active_account_name, statusPill?.auth_type]);

  const fetchMessagesAndState = useCallback(async (targetConvId?: string) => {
    const cid = targetConvId || activeConvId;
    if (!cid) return;

    try {
      const [msgRes, detailRes] = await Promise.all([
        apiFetch(`/api/v1/conversations/${cid}/messages`),
        apiFetch(`/api/v1/conversations/${cid}`),
      ]);

      if (detailRes.ok) {
        const detail = await detailRes.json();
        if (detail.scene_mode) setSceneMode(detail.scene_mode as SceneMode);
        setIncognito(Boolean(detail.incognito));
        setWorkingMemory(detail.working_memory || {});
      }

      if (msgRes.ok) {
        const rawList: Message[] = await msgRes.json();
        // 将历史 role==='tool' 的输出归并到上一条 model 消息的 tool_events 折叠卡片中
        const merged: Message[] = [];
        for (const m of rawList) {
          if (m.role === 'tool') {
            const prevModel = [...merged].reverse().find((x) => x.role === 'model');
            const toolItem: ToolExecutionCardItem = {
              call_id: m.tool_call_id || `hist_${m.id}`,
              name: 'tool',
              command: m.compressed_content || '历史工具执行输出',
              status: 'completed',
              output: m.content,
              compressed: m.compressed_content || undefined,
            };
            if (prevModel) {
              prevModel.tool_events = [...(prevModel.tool_events || []), toolItem];
              if (typeof m.id === 'number') {
                prevModel.merged_tool_message_ids = [
                  ...(prevModel.merged_tool_message_ids || []),
                  m.id,
                ];
              }
            }
          } else if (m.role === 'user') {
            merged.push(m);
          } else {
            // 统一清洗历史残存的 DSML / XML 工具协议、孤立围栏及正文内嵌 <think> 片段
            const sanitized = sanitizeAssistantContent(m.content || '', { isStreaming: false });
            let strippedContent = sanitized.cleanContent;
            const extractedThinking = sanitized.thinkingParts.join('\n\n').trim();
            const resolvedThinking = m.thinking || extractedThinking || undefined;

            if (!strippedContent && sanitized.hadToolMarkup) {
              strippedContent =
                sanitized.strippedToolNames.length > 0
                  ? `（已自动折叠历史工具调用协议：${sanitized.strippedToolNames.join('、')}）`
                  : '（已自动折叠历史内部工具调用协议片段）';
            }

            const cleanContent =
              strippedContent.toLowerCase().includes('interactive_inquiry') ||
              strippedContent.includes('"inquiry_id"')
                ? normalizeInteractiveInquiryMarkdown(strippedContent)
                : strippedContent;
            const hasToolCallMeta = Boolean(m.tool_calls_json && m.tool_calls_json !== '[]');
            merged.push({
              ...m,
              content: cleanContent,
              thinking: resolvedThinking,
              is_process_step: Boolean(hasToolCallMeta || sanitized.hadToolMarkup),
            });
          }
        }
        // 保活当前会话最新一轮已激活的确定性事实视图引用，防止流结束重载消息时微提示闪灭
        const preservedFacts = lastStreamFactRefsRef.current;
        if (preservedFacts && preservedFacts.convId === cid && preservedFacts.facts.length > 0) {
          for (let i = merged.length - 1; i >= 0; i--) {
            if (merged[i].role === 'model') {
              merged[i].fact_refs = preservedFacts.facts;
              break;
            }
          }
        }
        // 保活当前会话最新一轮实时 Token 水表（在无痕模式未落库时防重载闪灭）
        const preservedStats = lastStreamTokenStatsRef.current;
        if (preservedStats && preservedStats.convId === cid && preservedStats.stats) {
          for (let i = merged.length - 1; i >= 0; i--) {
            if (merged[i].role === 'model') {
              if (!merged[i].token_stats) {
                merged[i].token_stats = preservedStats.stats;
              }
              break;
            }
          }
        }
        // 若流式请求在写库前中断（HTTP 异常/超时/手动停止），保活故障或停止提示不被空记录刷没
        const pendingErrNotice = lastStreamErrorNoticeRef.current;
        if (
          pendingErrNotice &&
          pendingErrNotice.convId === cid &&
          (merged.length === 0 || merged[merged.length - 1].role === 'user')
        ) {
          merged.push({
            id: Date.now(),
            conversation_id: cid,
            role: 'model',
            content: pendingErrNotice.message,
          });
        }
        setMessages(merged);
      }
    } catch (e) {
      console.warn('获取消息失败:', e);
    }
  }, [activeConvId]);

  useEffect(() => {
    if (!activeConvId) {
      setMessages([]);
      setWorkingMemory({});
      return;
    }

    // 关键防冲刷保护：若当前正在流式接收新会话的消息，绝不能拉取未写完的后端空记录覆盖当前正在渲染的内容！
    if (isStreamingRef.current) {
      return;
    }

    shouldForceScrollOnLoadRef.current = true;
    fetchMessagesAndState();
  }, [activeConvId, fetchMessagesAndState]);

  const handleSelectSearchResult = useCallback(
    async (result: SearchResult) => {
      setActiveNavHub('chat');
      if (result.workspace_id && result.workspace_id !== activeWorkspaceId) {
        setActiveWorkspaceId(result.workspace_id);
      }

      pendingJumpTargetRef.current = {
        convId: result.conversation_id,
        messageId: result.message_id,
      };

      if (result.conversation_id === activeConvId) {
        if (!isStreamingRef.current) {
          await fetchMessagesAndState(result.conversation_id);
        } else if (result.message_id) {
          const matchedMsg = messages.find(
            (m) =>
              m.id === result.message_id ||
              m.merged_tool_message_ids?.includes(result.message_id)
          );
          if (matchedMsg?.id) {
            pendingJumpTargetRef.current = null;
            document.getElementById(`chat-msg-${matchedMsg.id}`)?.scrollIntoView({
              behavior: 'smooth',
              block: 'center',
            });
            setHighlightedMessageId(matchedMsg.id);
            if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
            highlightTimerRef.current = setTimeout(() => setHighlightedMessageId(null), 2800);
          }
        }
      } else {
        void stopActiveStream('switch');
        lastStreamErrorNoticeRef.current = null;
        activeConvIdRef.current = result.conversation_id;
        setActiveConvId(result.conversation_id);
      }
    },
    [activeWorkspaceId, activeConvId, fetchMessagesAndState, messages, stopActiveStream]
  );

  const handleSwitchSceneMode = async (nextMode: SceneMode) => {
    setSceneMode(nextMode);
    if (activeConvId) {
      try {
        await apiFetch(`/api/v1/conversations/${activeConvId}/agent-state`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scene_mode: nextMode }),
        });
      } catch (e) { }
    }
  };

  const handleToggleIncognito = async () => {
    const nextVal = !incognito;
    setIncognito(nextVal);
    if (activeConvId) {
      try {
        await apiFetch(`/api/v1/conversations/${activeConvId}/agent-state`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ incognito: nextVal }),
        });
      } catch (e) { }
    }
  };

  const handleClearScratchpad = async () => {
    setWorkingMemory({});
    if (activeConvId) {
      try {
        await apiFetch(`/api/v1/conversations/${activeConvId}/agent-state`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ clear_working_memory: true }),
        });
      } catch (e) { }
    }
  };

  const handleApproveToolCall = async (callId: string, approved: boolean) => {
    // 乐观更新本地状态为 running 或 rejected
    setMessages((prev) => {
      const updated = [...prev];
      for (const msg of updated) {
        if (msg.tool_events) {
          msg.tool_events = msg.tool_events.map((ev) =>
            ev.call_id === callId
              ? { ...ev, status: approved ? 'running' : 'rejected' }
              : ev
          );
        }
      }
      return updated;
    });

    try {
      await apiFetch('/api/v1/chat/tool-approval', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ call_id: callId, approved }),
      });
    } catch (e) {
      console.error('发送命令审批结果失败:', e);
    }
  };

  const handleInspectPortQuick = async (port: number) => {
    try {
      const res = await apiFetch('/api/v1/system/port/inspect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ port }),
      });
      if (res.ok) {
        const json = await res.json();
        setIsActionDrawerOpen(true);
        if (json.occupied) {
          setPendingActionCount((prev) => prev + 1);
        }
      }
    } catch (e) { }
  };

  const handleProposeProcessKill = async (proc: { pid: number; name: string; memory_mb: number }) => {
    try {
      const res = await apiFetch('/api/v1/system/governance/propose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scenario: workMode,
          targets: [proc],
          reason: `用户在硬件仪表盘中勾选终止进程 [${proc.name}] (PID: ${proc.pid})`,
        }),
      });
      if (res.ok) {
        setIsActionDrawerOpen(true);
        setPendingActionCount((prev) => prev + 1);
      }
    } catch (e) { }
  };

  const handleSendMessage = async (
    customPrompt?: string,
    approvedStepsForRequest?: PlanStepItem[],
    confirmedFactsForRequest?: ConfirmedFactItem[],
    overrideAttachments?: ParsedUserAttachment[],
    options?: {
      inquiryConfirmed?: boolean;
      inquiryAction?: InquirySubmitAction;
      loopGoalOverride?: string;
    }
  ) => {
    const usingOverrideAttachments = Array.isArray(overrideAttachments);
    const currentAttachments: AttachmentItem[] = usingOverrideAttachments
      ? overrideAttachments.map((att) => ({
        name: att.name,
        size: att.size_str || '',
        type: att.category === 'image' || Boolean(att.mime_type?.startsWith('image/'))
          ? 'image'
          : att.category === 'text'
            ? 'text'
            : 'file',
        category: att.category as AttachmentCategory | undefined,
        filePath: att.file_path,
        relPath: att.rel_path,
        url: att.url,
        mimeType: att.mime_type,
      }))
      : [...attachments];
    const rawPrompt = (customPrompt || inputPrompt).trim()
      || (currentAttachments.length > 0 && !customPrompt ? '请结合附带的文件或图片内容进行分析解答。' : '');
    if (!rawPrompt || isStreaming) return;

    // 静默拦截本地监控看板指令 /stats 与 /tokens，不消耗模型配额直接呼出弹窗
    const lowerSlashCmd = rawPrompt.toLowerCase();
    if (
      lowerSlashCmd === '/stats' ||
      lowerSlashCmd === '/tokens' ||
      lowerSlashCmd.startsWith('/stats ') ||
      lowerSlashCmd.startsWith('/tokens ')
    ) {
      if (!customPrompt) {
        setInputPrompt('');
        setIsInputExpanded(false);
        setInputCursorPos(0);
      }
      setIsTokenStatsModalOpen(true);
      return;
    }

    let promptToSend = rawPrompt;
    let effectiveConvId = activeConvId;
    if (!customPrompt) {
      setInputPrompt('');
      setIsInputExpanded(false);
      setInputCursorPos(0);
    }
    setAttachmentRejectionNotices([]);
    // 新一轮提问开始前重置本轮临时 fact_refs 缓存
    if (lastStreamFactRefsRef.current && lastStreamFactRefsRef.current.convId !== activeConvId) {
      lastStreamFactRefsRef.current = null;
    }

    if (!usingOverrideAttachments) {
      setAttachments([]);
    }

    // 与后端持久化保持绝对同构，确保发出瞬间与刷新历史后气泡均可展示图片与文档徽标
    const userDisplayContent = buildMessageWithAttachmentsAnchor(
      rawPrompt,
      currentAttachments.map((att) => ({
        name: att.name,
        file_path: att.filePath,
        rel_path: att.relPath,
        url: att.url || att.dataUrl,
        mime_type: att.mimeType,
        size_str: att.size,
        category: att.category || (att.type === 'image' ? 'image' : 'file'),
      }))
    );

    if (promptToSend.startsWith('/free-port')) {
      const parts = promptToSend.split(/\s+/);
      const portNum = parseInt(parts[1], 10);
      if (!isNaN(portNum)) {
        await handleInspectPortQuick(portNum);
      }
    } else if (promptToSend.startsWith('/optimize-design') || promptToSend.startsWith('/optimize-dev')) {
      const isDesign = promptToSend.startsWith('/optimize-design');
      const scenarioType = isDesign ? 'design' : 'dev';
      try {
        const snapRes = await apiFetch('/api/v1/system/snapshot');
        if (snapRes.ok) {
          const snapJson = await snapRes.json();
          const topList = (snapJson.top_processes || []).filter((p: any) =>
            isDesign
              ? p.category === 'design' || p.category === 'general'
              : p.category === 'dev' || p.category === 'general'
          );
          if (topList.length > 0) {
            await apiFetch('/api/v1/system/governance/propose', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                scenario: scenarioType,
                targets: topList.slice(0, 3),
                reason: `${isDesign ? '显存与设计专项' : '开发环境专项'}体检：识别到 ${topList.length} 个后台高占用进程`,
              }),
            });
          }
          setIsActionDrawerOpen(true);
          setPendingActionCount((prev) => prev + 1);

          const userSuffix = promptToSend.replace(/^\/optimize-(design|dev)\s*/, '');
          promptToSend = `【${isDesign ? '设计创意显存护航' : '代码开发环境优化'}体检请求】\n用户需求：${userSuffix || '请全面分析当前系统硬件指标并给出调优建议'}\n\n<system_telemetry_snapshot>\n${JSON.stringify(snapJson, null, 2)}\n</system_telemetry_snapshot>`;
        }
      } catch (e) { }
    }

    if (promptToSend.includes('@hardware') && !promptToSend.includes('<system_telemetry_snapshot>')) {
      try {
        const snapRes = await apiFetch('/api/v1/system/snapshot');
        if (snapRes.ok) {
          const snapJson = await snapRes.json();
          promptToSend += `\n\n<system_telemetry_snapshot>\n${JSON.stringify(snapJson, null, 2)}\n</system_telemetry_snapshot>`;
        }
      } catch (e) { }
    }

    setIsStreaming(true);
    isStreamingRef.current = true;
    const controller = new AbortController();
    const streamMeta: ActiveStreamMeta = {
      controller,
      convId: activeConvId,
      reason: 'none',
    };
    const getAbortReason = (): StreamAbortReason => streamMeta.reason;
    activeStreamMetaRef.current = streamMeta;
    lastStreamErrorNoticeRef.current = null;

    const tempUserMsg: Message = {
      id: Date.now(),
      conversation_id: activeConvId || '',
      role: 'user',
      content: userDisplayContent,
    };

    const tempAssistantMsg: Message = {
      id: Date.now() + 1,
      conversation_id: activeConvId || '',
      role: 'model',
      content: '',
      thinking: generateScenarioThinking(rawPrompt),
      is_placeholder_thinking: true,
      executed_by_account: statusPill?.active_account_name,
    };

    setMessages((prev) => [...prev, tempUserMsg, tempAssistantMsg]);
    scrollToBottom(true);

    const isExecuteUnlock = Boolean(
      (approvedStepsForRequest && approvedStepsForRequest.length > 0) ||
      (options?.inquiryConfirmed && options?.inquiryAction === 'execute_now')
    );
    const effectiveSceneMode = (isExecuteUnlock && sceneMode === 'chat') ? 'agent' : sceneMode;
    if (effectiveSceneMode === 'agent' && sceneMode !== 'agent') {
      setSceneMode('agent');
      try {
        localStorage.setItem('agenthub_scene_mode', 'agent');
      } catch { }
    }

    const hasPriorUnlockInView = messages.some(
      (m) =>
        (m.role === 'model' && Array.isArray(m.tool_events) && m.tool_events.length > 0) ||
        (m.role === 'user' &&
          (m.content.includes('实施方案已通过用户审批') ||
            m.content.includes('全部确认完毕')))
    );
    const shouldStartLoopHud =
      effectiveSceneMode === 'agent' &&
      options?.inquiryAction !== 'clarify_only' &&
      (executionStrategy !== 'plan_first' ||
        Boolean(approvedStepsForRequest && approvedStepsForRequest.length > 0) ||
        Boolean(options?.inquiryConfirmed && options?.inquiryAction === 'execute_now') ||
        hasPriorUnlockInView);

    const canResumePendingInquiryLoop = Boolean(
      activeLoop &&
        activeLoop.loop_status === 'waiting_approval' &&
        !activeLoop.pending_approval_call &&
        (!activeConvId || activeLoop.conversation_id === activeConvId)
    );

    if (shouldStartLoopHud && !canResumePendingInquiryLoop) {
      const cleanFallbackGoal = rawPrompt.split('\n')[0].slice(0, 120);
      const loopGoalPrompt =
        (options?.loopGoalOverride || '').trim() ||
        (cleanFallbackGoal.startsWith('已确认') || cleanFallbackGoal.startsWith('✅')
          ? '按确认决策执行任务'
          : cleanFallbackGoal);
      try {
        const r = await apiFetch('/api/v1/loop/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            workspace_id: activeWorkspaceId,
            conversation_id: activeConvId || 'default',
            goal_prompt: loopGoalPrompt,
          }),
        });
        if (r.ok) {
          const loopData = await r.json();
          setActiveLoop(loopData);
        }
      } catch {
        // 忽略启动阶段网络波动，由 chat/stream 兜底初始化
      }
    }

    let connectTimerId: ReturnType<typeof setTimeout> | null = null;
    let idleTimerId: ReturnType<typeof setTimeout> | null = null;
    let hasReceivedFirstChunk = false;

    const clearWatchdogs = () => {
      if (connectTimerId) {
        clearTimeout(connectTimerId);
        connectTimerId = null;
      }
      if (idleTimerId) {
        clearTimeout(idleTimerId);
        idleTimerId = null;
      }
    };

    const resetIdleWatchdog = () => {
      if (idleTimerId) clearTimeout(idleTimerId);
      idleTimerId = setTimeout(() => {
        if (getAbortReason() === 'none') {
          streamMeta.reason = 'idle_timeout';
          controller.abort();
        }
      }, 90000);
    };

    try {
      if (controller.signal.aborted) {
        throw new DOMException('Aborted', 'AbortError');
      }

      // 读取用户配置的多轮对话记忆上下文轮数
      let memoryRounds = 10;
      try {
        const savedRounds = localStorage.getItem('agenthub_context_rounds') || localStorage.getItem('geminihub_context_rounds');
        if (savedRounds !== null) memoryRounds = parseInt(savedRounds, 10);
      } catch (e) { }

      const systemOverride = await buildActiveSystemOverride();
      if (controller.signal.aborted) {
        throw new DOMException('Aborted', 'AbortError');
      }

      connectTimerId = setTimeout(() => {
        if (!hasReceivedFirstChunk && getAbortReason() === 'none') {
          streamMeta.reason = 'connect_timeout';
          controller.abort();
        }
      }, 45000);

      const response = await apiFetch('/api/v1/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          conversation_id: activeConvId,
          workspace_id: activeWorkspaceId,
          message: promptToSend,
          model_name: currentModel || availableModels[0]?.id || '',
          thinking_level: thinkingLevel,
          system_override: systemOverride,
          context_rounds: memoryRounds,
          scene_mode: effectiveSceneMode,
          incognito: incognito,
          execution_strategy: effectiveSceneMode === 'agent' ? executionStrategy : 'direct',
          clarify_mode: effectiveSceneMode === 'chat' ? clarifyMode : false,
          approved_plan_steps: approvedStepsForRequest || null,
          confirmed_facts: confirmedFactsForRequest && confirmedFactsForRequest.length > 0 ? confirmedFactsForRequest : null,
          inquiry_confirmed: Boolean(options?.inquiryConfirmed),
          inquiry_action: options?.inquiryAction || null,
          attachments: currentAttachments.map((a) => ({
            name: a.name,
            file_path: a.filePath,
            rel_path: a.relPath,
            url: a.url,
            mime_type: a.mimeType || 'image/png',
            size_str: a.size,
            category: a.category,
          })),
        }),
      });

      if (!response.ok) {
        let errDetail = `服务端返回错误 (HTTP ${response.status})`;
        try {
          const rawErr = await response.text();
          if (rawErr) {
            try {
              const errJson = JSON.parse(rawErr);
              errDetail = String(errJson?.detail || errJson?.message || rawErr);
            } catch {
              errDetail = rawErr;
            }
          }
        } catch { }
        throw new Error(errDetail);
      }

      if (!response.body) throw new Error('ReadableStream 不可用');

      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let partialText = '';
      let partialThinking = '';
      let rollbackTargetConvId: string | null = null;
      let currentStreamStepIndex = 0;
      let isRelayed = false;
      let executorAccount = statusPill?.active_account_name || 'AgentHub';

      const stripXmlToolTags = (txt: string) =>
        sanitizeAssistantContent(txt, { isStreaming: true }).cleanContent;

      let sseBuffer = '';

      const handleSseLine = (line: string) => {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) return;
        const rawData = trimmed.slice(6).trim();
        if (!rawData) return;

        try {
          const parsed = JSON.parse(rawData);
          if (parsed.type === 'conv_init') {
            // 若后端自适应升舱或调整了场景模式，同步刷新前端视图
            if (parsed.scene_mode && parsed.scene_mode !== sceneMode) {
              setSceneMode(parsed.scene_mode);
              try {
                localStorage.setItem('agenthub_scene_mode', parsed.scene_mode);
              } catch { }
            }
            // 关键：收到会话初始化事件，立即绑定 conversation_id，防止后续生成重复会话并保证继承上下文记忆
            if (parsed.conversation_id && getAbortReason() !== 'switch') {
              effectiveConvId = parsed.conversation_id;
              streamMeta.convId = parsed.conversation_id;
              activeConvIdRef.current = parsed.conversation_id;
              setActiveConvId(parsed.conversation_id);
              fetchConversations();
            }
          } else if (parsed.type === 'start') {
            if (parsed.account) executorAccount = parsed.account;
            if (typeof parsed.step_index === 'number' && parsed.step_index > currentStreamStepIndex) {
              // ReAct 进入新的一步：冻结上一个过程步骤气泡并实时开辟当前新步骤气泡，消除结束重载跳变
              currentStreamStepIndex = parsed.step_index;
              partialText = '';
              partialThinking = '';
              setMessages((prev) => {
                const updated = [...prev];
                const prevLast = updated[updated.length - 1];
                if (prevLast && prevLast.role === 'model' && prevLast.is_placeholder_thinking) {
                  updated[updated.length - 1] = {
                    ...prevLast,
                    thinking: undefined,
                    is_placeholder_thinking: false,
                  };
                }
                updated.push({
                  id: Date.now() + currentStreamStepIndex * 100 + Math.floor(Math.random() * 99),
                  conversation_id: effectiveConvId || activeConvId || '',
                  role: 'model',
                  content: '',
                  executed_by_account: executorAccount,
                  model_name: parsed.model,
                });
                return updated;
              });
            } else {
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                if (last && last.role === 'model') {
                  updated[updated.length - 1] = {
                    ...last,
                    executed_by_account: executorAccount,
                    model_name: parsed.model || last.model_name,
                  };
                }
                return updated;
              });
            }
          } else if (parsed.type === 'fact_refs') {
            const incomingFacts = (parsed.facts || []) as MemoryFactItem[];
            if (effectiveConvId) {
              lastStreamFactRefsRef.current = { convId: effectiveConvId, facts: incomingFacts };
            }
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                updated[updated.length - 1] = { ...last, fact_refs: incomingFacts };
              }
              return updated;
            });
          } else if (parsed.type === 'memory_refs') {
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                updated[updated.length - 1] = { ...last, memory_refs: parsed.memories || [] };
              }
              return updated;
            });
          } else if (parsed.type === 'scratchpad_update') {
            setWorkingMemory(parsed.working_memory || {});
          } else if (parsed.type === 'loop_step') {
            const evtConvId = parsed.conversation_id || effectiveConvId || activeConvId;
            if (!evtConvId || evtConvId !== activeConvIdRef.current) return;
            const nextStatus = parsed.status ?? 'executing';
            if (nextStatus === 'idle') {
              lastActiveLoopSigRef.current = 'null';
              setActiveLoop(null);
              return;
            }
            setActiveLoop((prev) => {
              const effStatus = parsed.status ?? prev?.loop_status ?? 'executing';
              const nextPendingApproval =
                'pending_approval_call' in parsed
                  ? parsed.pending_approval_call ?? null
                  : effStatus !== 'waiting_approval'
                    ? null
                    : prev?.pending_approval_call ?? null;
              if (!prev) {
                return {
                  id: parsed.loop_id,
                  workspace_id: activeWorkspaceId || 'default',
                  conversation_id: evtConvId,
                  goal_prompt: rawPrompt,
                  scratchpad_md: parsed.scratchpad_md,
                  loop_status: effStatus,
                  current_iteration: parsed.iteration || 0,
                  max_iterations: parsed.max_iterations || 10,
                  pending_approval_call: nextPendingApproval,
                  error_fingerprints: [],
                  distillable: false,
                  distill_reason: null,
                  created_at: new Date().toISOString(),
                  updated_at: new Date().toISOString(),
                };
              }
              return {
                ...prev,
                id: parsed.loop_id ?? prev.id,
                conversation_id: evtConvId,
                current_iteration: parsed.iteration ?? prev.current_iteration,
                loop_status: effStatus,
                pending_approval_call: nextPendingApproval,
                scratchpad_md: parsed.scratchpad_md ?? prev.scratchpad_md,
              };
            });
          } else if (parsed.type === 'loop_completed') {
            const evtConvId = parsed.conversation_id || effectiveConvId || activeConvId;
            if (!evtConvId || evtConvId !== activeConvIdRef.current) return;
            setActiveLoop((prev) => {
              const nextDistillable =
                typeof parsed.distillable === 'boolean'
                  ? parsed.distillable
                  : (prev?.distillable ?? false);
              const nextDistillReason =
                parsed.distill_reason !== undefined
                  ? parsed.distill_reason
                  : (prev?.distill_reason ?? null);
              if (!prev) {
                if (!parsed.loop_id) return null;
                return {
                  id: parsed.loop_id,
                  workspace_id: activeWorkspaceId || 'default',
                  conversation_id: evtConvId,
                  goal_prompt: rawPrompt.split('\n')[0].slice(0, 120),
                  scratchpad_md: parsed.scratchpad_md,
                  loop_status: 'completed',
                  current_iteration: parsed.iteration ?? 0,
                  max_iterations: parsed.max_iterations ?? 10,
                  pending_approval_call: null,
                  error_fingerprints: [],
                  distillable: nextDistillable,
                  distill_reason: nextDistillReason,
                  created_at: new Date().toISOString(),
                  updated_at: new Date().toISOString(),
                };
              }
              return {
                ...prev,
                id: parsed.loop_id ?? prev.id,
                conversation_id: evtConvId,
                loop_status: 'completed',
                current_iteration: parsed.iteration ?? prev.current_iteration,
                max_iterations: parsed.max_iterations ?? prev.max_iterations,
                scratchpad_md: parsed.scratchpad_md ?? prev.scratchpad_md,
                pending_approval_call: null,
                distillable: nextDistillable,
                distill_reason: nextDistillReason,
              };
            });
          } else if (parsed.type === 'loop_failed') {
            const evtConvId = parsed.conversation_id || effectiveConvId || activeConvId;
            if (!evtConvId || evtConvId !== activeConvIdRef.current) return;
            setActiveLoop((prev) => {
              if (!prev) {
                if (!parsed.loop_id) return null;
                return {
                  id: parsed.loop_id,
                  workspace_id: activeWorkspaceId || 'default',
                  conversation_id: evtConvId,
                  goal_prompt: rawPrompt.split('\n')[0].slice(0, 120),
                  scratchpad_md: parsed.scratchpad_md ?? null,
                  loop_status: 'failed',
                  current_iteration: parsed.iteration ?? 0,
                  max_iterations: parsed.max_iterations ?? 10,
                  pending_approval_call: null,
                  error_fingerprints: [],
                  distillable: false,
                  distill_reason: parsed.error || '流程执行报错',
                  created_at: new Date().toISOString(),
                  updated_at: new Date().toISOString(),
                };
              }
              return {
                ...prev,
                id: parsed.loop_id ?? prev.id,
                conversation_id: evtConvId,
                loop_status: 'failed',
                distillable: false,
                distill_reason: parsed.error || '流程执行报错',
              };
            });
          } else if (
            parsed.type === 'require_approval' ||
            parsed.type === 'tool_start' ||
            parsed.type === 'tool_end'
          ) {
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                const existingEvents = [...(last.tool_events || [])];
                const idx = existingEvents.findIndex((e) => e.call_id === parsed.call_id);
                const nextStatus =
                  parsed.type === 'require_approval'
                    ? 'require_approval'
                    : parsed.type === 'tool_start'
                      ? 'running'
                      : parsed.status || 'completed';

                const nextItem: ToolExecutionCardItem = {
                  call_id: parsed.call_id,
                  name: parsed.name || 'execute_local_command',
                  command: parsed.command || parsed.name || '',
                  reason: parsed.reason,
                  cwd: parsed.cwd,
                  status: nextStatus,
                  exit_code: parsed.exit_code,
                  output: parsed.output,
                  compressed: parsed.compressed,
                };

                if (idx >= 0) {
                  existingEvents[idx] = { ...existingEvents[idx], ...nextItem };
                } else {
                  existingEvents.push(nextItem);
                }
                updated[updated.length - 1] = {
                  ...last,
                  tool_events: existingEvents,
                  is_process_step: true,
                  content: stripXmlToolTags(partialText),
                };
              }
              return updated;
            });
            // 收到需要审批时立即自动滚动到底部聚焦卡片
            if (parsed.type === 'require_approval') {
              setTimeout(() => scrollToBottom(true), 50);
            }
          } else if (parsed.type === 'thinking_chunk') {
            partialThinking += parsed.content || '';
            executorAccount = parsed.account || executorAccount;
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                updated[updated.length - 1] = {
                  ...last,
                  thinking: partialThinking,
                  is_placeholder_thinking: false,
                  executed_by_account: executorAccount,
                  is_relayed: isRelayed,
                };
              }
              return updated;
            });
          } else if (parsed.type === 'chunk') {
            partialText += parsed.content;
            executorAccount = parsed.account || executorAccount;
            const sanitized = sanitizeAssistantContent(partialText, { isStreaming: true });
            const inlineThinking = sanitized.thinkingParts.join('\n\n').trim();
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                const resolvedThinking = partialThinking || inlineThinking || last.thinking;
                updated[updated.length - 1] = {
                  ...last,
                  content: sanitized.cleanContent,
                  thinking: resolvedThinking,
                  is_placeholder_thinking: Boolean(!partialThinking && !inlineThinking && last.is_placeholder_thinking),
                  is_process_step: Boolean(last.is_process_step || sanitized.hadToolMarkup),
                  executed_by_account: executorAccount,
                  is_relayed: isRelayed,
                };
              }
              return updated;
            });
          } else if (parsed.type === 'model_downgrade') {
            const origModel = parsed.original_model;
            const targetModel = parsed.current_model;
            const downgradeMsg = parsed.message || `检测到原模型「${origModel}」当前配额或容量受限，已自动无缝切换至「${targetModel}」`;

            // 1. 在当前正在生成的 model 消息上挂载降级通知条
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                updated[updated.length - 1] = {
                  ...last,
                  downgrade_info: {
                    original_model: origModel,
                    current_model: targetModel,
                    reason: parsed.reason,
                    message: downgradeMsg,
                  },
                  model_name: targetModel,
                };
              }
              return updated;
            });

            // 2. 右侧（输入框旁）的模型选择器自动联动跳到降级后的正确模型
            if (targetModel) {
              setCurrentModel(targetModel);
            }
          } else if (parsed.type === 'step_extend') {
            const extMsg = parsed.message || `任务正在深度自主推演中，步数已自动扩展至第 ${parsed.max_steps} 步`;
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                updated[updated.length - 1] = {
                  ...last,
                  step_extend_info: {
                    step: parsed.step,
                    max_steps: parsed.max_steps,
                    message: extMsg,
                  },
                };
              }
              return updated;
            });
          } else if (parsed.type === 'stream_reset') {
            partialText = '';
            partialThinking = '';
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                updated[updated.length - 1] = {
                  ...last,
                  content: '',
                  thinking: undefined,
                  is_placeholder_thinking: false,
                  is_process_step: false,
                };
              }
              return updated;
            });
          } else if (parsed.type === 'token_stats' && parsed.stats) {
            const turnStats = parsed.stats as TurnTokenMetrics;
            const targetConvId = effectiveConvId || activeConvIdRef.current || '';
            lastStreamTokenStatsRef.current = { convId: targetConvId, stats: turnStats };
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                updated[updated.length - 1] = { ...last, token_stats: turnStats };
              }
              return updated;
            });
          } else if (parsed.type === 'done') {
            const doneRawText =
              typeof parsed.final_step_content === 'string'
                ? parsed.final_step_content
                : (parsed.full_content || partialText);
            partialText = doneRawText;
            if (parsed.model) {
              setCurrentModel(parsed.model);
            }
            const sanitizedDone = sanitizeAssistantContent(doneRawText, { isStreaming: false });
            const strippedDoneText = sanitizedDone.cleanContent;
            const finalDoneText =
              strippedDoneText.toLowerCase().includes('interactive_inquiry') ||
              strippedDoneText.includes('"inquiry_id"')
                ? normalizeInteractiveInquiryMarkdown(strippedDoneText)
                : strippedDoneText;
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                const inlineDoneThinking = sanitizedDone.thinkingParts.join('\n\n').trim();
                const finalThinking = partialThinking || inlineDoneThinking || (last.is_placeholder_thinking ? undefined : last.thinking);
                updated[updated.length - 1] = {
                  ...last,
                  content: finalDoneText,
                  thinking: finalThinking,
                  is_placeholder_thinking: false,
                  model_name: parsed.model || last.model_name,
                };
              }
              return updated;
            });
          } else if (parsed.type === 'tool_request') {
            setIsActionDrawerOpen(true);
            setPendingActionCount((prev) => prev + 1);
          } else if (parsed.type === 'relay') {
            isRelayed = true;
            executorAccount = parsed.account;
          } else if (parsed.type === 'error') {
            const errTxt = String(parsed.content || '模型或算力通道返回错误，请检查配置');
            const errNotice = `⚠️ ${errTxt}`;
            if (effectiveConvId) {
              lastStreamErrorNoticeRef.current = { convId: effectiveConvId, message: errNotice };
            }
            partialText = partialText ? `${partialText}\n\n${errNotice}` : errNotice;
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                updated[updated.length - 1] = {
                  ...last,
                  content: partialText,
                  thinking: last.is_placeholder_thinking ? undefined : last.thinking,
                  is_placeholder_thinking: false,
                };
              }
              return updated;
            });
            showFloatingToast(errTxt, 'error', 4800);
          } else if (parsed.type === 'cancelled') {
            const cancelTxt = String(parsed.content || '已手动停止生成');
            const cancelNotice = `（${cancelTxt}）`;
            if (effectiveConvId) {
              lastStreamErrorNoticeRef.current = { convId: effectiveConvId, message: cancelNotice };
            }
            partialText = partialText ? `${partialText}\n\n${cancelNotice}` : cancelNotice;
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                updated[updated.length - 1] = {
                  ...last,
                  content: partialText,
                  thinking: last.is_placeholder_thinking ? undefined : last.thinking,
                  is_placeholder_thinking: false,
                };
              }
              return updated;
            });
          } else if (parsed.type === 'control' && parsed.action === 'rollback_completed') {
            const targetCid = String(parsed.conversation_id || effectiveConvId || '').trim();
            if (targetCid) {
              rollbackTargetConvId = targetCid;
            }
            const targetTurn = Number(parsed.target_turn_id ?? 0);
            showFloatingToast(`已回滚至 Turn ${targetTurn}，正在同步会话界面状态...`, 'success', 3600);
          }
        } catch (err) {
          console.warn('SSE 解析异常:', err, rawData);
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (!hasReceivedFirstChunk) {
          hasReceivedFirstChunk = true;
          if (connectTimerId) {
            clearTimeout(connectTimerId);
            connectTimerId = null;
          }
        }
        if (done) break;
        resetIdleWatchdog();

        const chunk = decoder.decode(value, { stream: true });
        sseBuffer += chunk;
        const lines = sseBuffer.split('\n');
        // 将未换行闭合的末尾字符放回 buffer 等待下个分包
        sseBuffer = lines.pop() || '';

        for (const line of lines) {
          handleSseLine(line);
        }
      }

      // 处理流结束时 buffer 中残留的最后一行数据
      if (sseBuffer.trim()) {
        handleSseLine(sseBuffer);
      }

      clearWatchdogs();
      fetchConversations();
      fetchStatusPill();
      if (rollbackTargetConvId) {
        await fetchMessagesAndState(rollbackTargetConvId);
      }
    } catch (err) {
      clearWatchdogs();
      const reason = getAbortReason();
      if (reason === 'switch') {
        return;
      }

      let notice = '';
      if (reason === 'user') {
        notice = '（已手动停止生成）';
      } else if (reason === 'connect_timeout') {
        notice = '⚠️ 连接超时（45秒内未收到后端首包响应），已自动终止等待。请检查算力池节点或网络连接后重试。';
        showFloatingToast('连接超时（45秒无响应），已自动终止', 'error', 4800);
      } else if (reason === 'idle_timeout') {
        notice = '⚠️ 响应超时（超过90秒未收到上游增量数据），已自动断开连接。您可以重试或切换模型节点。';
        showFloatingToast('响应空闲超时（90秒），已自动断开', 'error', 4800);
      } else {
        const rawErrMsg = err instanceof Error ? err.message : String(err || '未知网络错误');
        notice = `⚠️ 请求异常中断：${rawErrMsg}`;
        showFloatingToast(rawErrMsg, 'error', 4800);
        console.error(err);
      }

      const targetConvId = effectiveConvId || activeConvIdRef.current;
      if (targetConvId && notice) {
        lastStreamErrorNoticeRef.current = { convId: targetConvId, message: notice };
      }
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last && last.role === 'model') {
          const existingContent = (last.content || '').trim();
          const nextContent = existingContent
            ? existingContent.includes(notice)
              ? existingContent
              : `${existingContent}\n\n${notice}`
            : notice;
          updated[updated.length - 1] = {
            ...last,
            content: nextContent,
            thinking: last.is_placeholder_thinking ? undefined : last.thinking,
            is_placeholder_thinking: false,
          };
        }
        return updated;
      });
    } finally {
      clearWatchdogs();
      const wasSwitchedAway =
        getAbortReason() === 'switch' ||
        Boolean(effectiveConvId && activeConvIdRef.current && activeConvIdRef.current !== effectiveConvId);
      if (activeStreamMetaRef.current === streamMeta) {
        activeStreamMetaRef.current = null;
        setIsStreaming(false);
        isStreamingRef.current = false;
      }
      // 仅当未切换至其它会话时才同步后端消息、Loop 与滚动条，彻底杜绝异步并发串话冲刷
      if (!wasSwitchedAway) {
        if (effectiveConvId) {
          await fetchMessagesAndState(effectiveConvId);
        }
        await fetchActiveLoop();
        setTimeout(() => scrollToBottom(true), 60);
      }
    }
  };

  const readFileAsDataUrl = (file: File): Promise<string> =>
    new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = (ev) => resolve((ev.target?.result as string) || '');
      reader.onerror = () => resolve('');
      reader.readAsDataURL(file);
    });

  const handleProcessAttachmentFiles = async (files: File[], extraRejections: string[] = []) => {
    const rejections: string[] = [...extraRejections];
    let remainingSlots = Math.max(0, MAX_ATTACHMENTS_PER_MESSAGE - attachments.length);
    const validUploadQueue: { file: File; category: AttachmentCategory; canonicalMime: string }[] = [];

    for (const file of files) {
      if (remainingSlots <= 0) {
        rejections.push(
          `已达单条消息附件上限 (${MAX_ATTACHMENTS_PER_MESSAGE} 个)，未添加「${file.name}」。`
        );
        continue;
      }
      const check = validateAttachmentCandidate(file);
      if (!check.valid || !check.meta) {
        rejections.push(check.reason || `不支持文件「${file.name}」。`);
        continue;
      }
      remainingSlots -= 1;
      validUploadQueue.push({
        file,
        category: check.meta.category,
        canonicalMime: check.meta.canonicalMime,
      });
    }

    if (validUploadQueue.length > 0) {
      setIsUploadingAttachment(true);
      try {
        const uploadedItems: AttachmentItem[] = [];
        for (const item of validUploadQueue) {
          const { file, category, canonicalMime } = item;
          try {
            const formData = new FormData();
            formData.append('workspace_id', activeWorkspaceId || 'default');
            formData.append('file', file);
            const res = await apiFetch('/api/v1/chat/upload', {
              method: 'POST',
              body: formData,
            });
            if (!res.ok) {
              let errDetail = `文件「${file.name}」上传被服务端拒绝 (HTTP ${res.status})。`;
              try {
                const errJson = await res.json();
                if (errJson?.detail) errDetail = String(errJson.detail);
              } catch { }
              rejections.push(errDetail);
              continue;
            }
            const json = await res.json();
            const isImg = (json.category || category) === 'image';
            const dataUrl = isImg ? await readFileAsDataUrl(file) : undefined;
            uploadedItems.push({
              name: json.name || file.name,
              size: json.size_str || formatFileSize(file.size),
              type: isImg ? 'image' : category === 'text' ? 'text' : 'file',
              category: (json.category || category) as AttachmentCategory,
              dataUrl,
              filePath: json.file_path,
              relPath: json.rel_path,
              url: json.url,
              mimeType: json.mime_type || canonicalMime,
            });
          } catch (uploadErr) {
            console.warn('本地附件上传失败:', uploadErr);
            rejections.push(`文件「${file.name}」上传失败，请检查本地后端服务是否正常运行。`);
          }
        }
        if (uploadedItems.length > 0) {
          setAttachments((prev) => [...prev, ...uploadedItems].slice(0, MAX_ATTACHMENTS_PER_MESSAGE));
        }
      } finally {
        setIsUploadingAttachment(false);
      }
    }

    if (rejections.length > 0) {
      setAttachmentRejectionNotices(rejections);
    } else {
      setAttachmentRejectionNotices([]);
    }
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      void handleProcessAttachmentFiles(Array.from(files));
    }
    e.target.value = '';
  };

  // 支持从微信、截图工具或文件管理器直接复制后粘贴 (Ctrl + V)
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const pastedFiles: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) {
          // 若复制的是微信截图/无名图片，自动赋予易识别的名字；普通文件保留真实扩展名与 MIME
          const isUnnamedImg = file.name === 'image.png' || !file.name;
          const fileName = isUnnamedImg
            ? `微信截图_${new Date().toLocaleTimeString().replace(/:/g, '')}.png`
            : file.name;
          pastedFiles.push(
            new File([file], fileName, {
              type: file.type || (isUnnamedImg ? 'image/png' : ''),
            })
          );
        }
      }
    }

    if (pastedFiles.length > 0) {
      void handleProcessAttachmentFiles(pastedFiles);
    }
  };

  const isFileDragTransfer = (e: React.DragEvent<HTMLElement>): boolean => {
    const types = e.dataTransfer?.types;
    return Boolean(types && Array.from(types).includes('Files'));
  };

  const handleMainDragEnter = (e: React.DragEvent<HTMLElement>) => {
    if (activeNavHub !== 'chat' || !isFileDragTransfer(e)) return;
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current += 1;
    setIsDraggingFiles(true);
  };

  const handleMainDragOver = (e: React.DragEvent<HTMLElement>) => {
    if (activeNavHub !== 'chat' || !isFileDragTransfer(e)) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) {
      e.dataTransfer.dropEffect = 'copy';
    }
  };

  const handleMainDragLeave = (e: React.DragEvent<HTMLElement>) => {
    if (activeNavHub !== 'chat' || !isFileDragTransfer(e)) return;
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) {
      setIsDraggingFiles(false);
    }
  };

  const handleMainDrop = (e: React.DragEvent<HTMLElement>) => {
    if (activeNavHub !== 'chat') return;
    if (!isFileDragTransfer(e)) return;
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setIsDraggingFiles(false);

    const droppedFiles: File[] = [];
    const dirRejections: string[] = [];

    const items = e.dataTransfer?.items;
    if (items && items.length > 0) {
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind !== 'file') continue;
        const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null;
        if (entry && entry.isDirectory) {
          dirRejections.push(
            `暂不支持直接拖入文件夹「${entry.name}」，请拖拽具体文件，或点击左侧工作区「挂载本地目录」。`
          );
          continue;
        }
        const file = item.getAsFile();
        if (file) droppedFiles.push(file);
      }
    } else if (e.dataTransfer?.files) {
      droppedFiles.push(...Array.from(e.dataTransfer.files));
    }

    if (droppedFiles.length > 0 || dirRejections.length > 0) {
      void handleProcessAttachmentFiles(droppedFiles, dirRejections);
    }
  };

  const handleModifyTone = (tone: 'shorter' | 'longer' | 'simpler' | 'professional') => {
    const toneMap = {
      shorter: '请将上述回复精炼为更简短的核心要点。',
      longer: '请对上述内容进行更详尽的拆解，补充更多实现细节与架构分析。',
      simpler: '请用更通俗浅显的语言解释上述原理。',
      professional: '请从生产级高可用架构的专业视角重新组织上述回答。',
    };
    handleSendMessage(toneMap[tone]);
  };

  const handleOpenCanvas = (code: string, language: string, customTitle?: string) => {
    let canvasType: 'html' | 'mermaid' | 'code' | 'image' = 'code';
    let title = customTitle || 'Artifacts 预览';
    if (language === 'mermaid') {
      canvasType = 'mermaid';
      title = customTitle || 'Mermaid 架构拓扑预览';
    } else if (language === 'html' || language === 'htm') {
      canvasType = 'html';
      title = customTitle || 'HTML 交互画布预览';
    } else if (language === 'image') {
      canvasType = 'image';
      if (customTitle) {
        title = customTitle;
      } else if (code.includes('pollinations.ai') || code.includes('model=turbo')) {
        title = '生成的 AI 画作原图预览';
      } else {
        title = '图片大图预览';
      }
    }
    setCanvasState({
      isOpen: true,
      title,
      content: code,
      type: canvasType,
    });
  };

  const handleSaveCodeToWorkspace = async (code: string, language: string) => {
    const extMap: Record<string, string> = {
      python: 'py',
      javascript: 'js',
      typescript: 'ts',
      json: 'json',
      markdown: 'md',
      html: 'html',
      css: 'css',
      sql: 'sql',
      dockerfile: 'dockerfile',
      yaml: 'yaml',
      sh: 'sh',
    };
    const ext = extMap[language.toLowerCase()] || 'txt';
    const defaultRelPath = `src/snippet_${Date.now()}.${ext}`;
    const targetPath = prompt('请输入落盘到当前工作区的相对文件路径:', defaultRelPath);
    if (!targetPath) return;

    try {
      const res = await apiFetch(`/api/v1/workspaces/${activeWorkspaceId}/files`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          rel_path: targetPath.trim(),
          content: code,
          overwrite: true,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data && data.success) {
          showFloatingToast(`代码文件已成功落盘至: ${data.rel_path}`, 'success', 3500);
        }
      }
    } catch (e: any) {
      alert(e?.message || '文件落盘失败');
    }
  };

  /**
   * 在系统默认应用中直接打开生成文件（或定位到所在文件夹），高危可执行文件由后端自动降级为目录定位
   */
  const handleOpenGeneratedFile = useCallback(
    async (rawPath: string, mode: 'launch' | 'reveal' = 'launch') => {
      const cleaned = cleanFilePathCandidate(rawPath);
      if (!cleaned) return;
      try {
        const res = await apiFetch('/api/v1/artifacts/open-by-path', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            workspace_id: activeWorkspaceId || 'default',
            file_path: cleaned,
            mode,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showFloatingToast(data?.detail || `无法打开文件: ${cleaned}`, 'error', 4500);
          return;
        }
        if (data.downgraded) {
          showFloatingToast(
            data.message || data.downgraded_reason || '出于安全防护已自动打开所在文件夹定位',
            'warning',
            5200
          );
        } else {
          showFloatingToast(
            data.message || (mode === 'launch' ? `已在默认应用中打开 ${data.file_name || cleaned}` : `已定位文件 ${data.file_name || cleaned}`),
            'success',
            3500
          );
        }
      } catch (e: any) {
        showFloatingToast(e?.message || `打开文件失败: ${cleaned}`, 'error', 4500);
      }
    },
    [activeWorkspaceId, showFloatingToast]
  );

  /**
   * 按工作区文件路径在右侧画布抽屉中预览文件内容（文本/HTML/Excel摘要/大图）
   */
  const handlePreviewGeneratedFile = useCallback(
    async (rawPath: string) => {
      const cleaned = cleanFilePathCandidate(rawPath);
      if (!cleaned) return;
      try {
        const res = await apiFetch('/api/v1/artifacts/content-by-path', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            workspace_id: activeWorkspaceId || 'default',
            file_path: cleaned,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showFloatingToast(data?.detail || `预览文件失败: ${cleaned}`, 'error', 4500);
          return;
        }
        if (data.canvas_type === 'image' && data.raw_url) {
          const token = await getAccessToken();
          const finalUrl = token
            ? `${data.raw_url}${data.raw_url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
            : data.raw_url;
          handleOpenCanvas(finalUrl, 'image', data.file_name || '图片预览');
          return;
        }
        handleOpenCanvas(
          data.content || '',
          data.canvas_type || 'code',
          data.file_name || cleaned
        );
      } catch (e: any) {
        showFloatingToast(e?.message || `读取预览失败: ${cleaned}`, 'error', 4500);
      }
    },
    [activeWorkspaceId, showFloatingToast]
  );

  // 稳定抽屉与模态框的关闭/刷新回调引用，避免 3 秒状态轮询引发子组件不必要的 Effect 重跑
  const handleCloseCanvas = useCallback(() => setCanvasState((prev) => ({ ...prev, isOpen: false })), []);
  const handleCloseActionDrawer = useCallback(() => setIsActionDrawerOpen(false), []);
  const handleCloseSearch = useCallback(() => setIsSearchOpen(false), []);
  const handleCloseAccount = useCallback(() => setIsAccountOpen(false), []);
  const handleCloseSync = useCallback(() => setIsSyncOpen(false), []);
  const handleCloseSettings = useCallback(() => setIsSettingsOpen(false), []);
  const handleCloseMemoryHub = useCallback(() => setIsMemoryHubOpen(false), []);
  const handleCloseSkillPicker = useCallback(() => setIsSkillPickerOpen(false), []);
  const handleCloseSopDrawer = useCallback(() => setSelectedSopSkill(null), []);

  // 稳定 Markdown AI 消息子组件回调引用，配合 React.memo 彻底隔绝外层轮询重绘
  const latestMarkdownActionsRef = useRef({
    markInquirySubmitted: handleMarkInquirySubmitted,
    sendInquiryFeedback: (
      feedback: string,
      confirmedFacts?: ConfirmedFactItem[],
      inquiryAction?: InquirySubmitAction,
      loopGoalHint?: string
    ) => {
      const action = inquiryAction || 'execute_now';
      if (action === 'execute_now' && sceneMode !== 'agent') {
        setSceneMode('agent');
        try {
          localStorage.setItem('agenthub_scene_mode', 'agent');
        } catch { }
      }
      void handleSendMessage(feedback, undefined, confirmedFacts, undefined, {
        inquiryConfirmed: true,
        inquiryAction: action,
        loopGoalOverride: loopGoalHint,
      });
    },
    fillTemplate: handleFillTemplateToInput,
    approvePlan: handleApproveAndExecutePlan,
    openCanvas: handleOpenCanvas,
    saveToWorkspace: handleSaveCodeToWorkspace,
    openGeneratedFile: handleOpenGeneratedFile,
    previewGeneratedFile: handlePreviewGeneratedFile,
  });
  latestMarkdownActionsRef.current = {
    markInquirySubmitted: handleMarkInquirySubmitted,
    sendInquiryFeedback: (
      feedback: string,
      confirmedFacts?: ConfirmedFactItem[],
      inquiryAction?: InquirySubmitAction,
      loopGoalHint?: string
    ) => {
      const action = inquiryAction || 'execute_now';
      if (action === 'execute_now' && sceneMode !== 'agent') {
        setSceneMode('agent');
        try {
          localStorage.setItem('agenthub_scene_mode', 'agent');
        } catch { }
      }
      void handleSendMessage(feedback, undefined, confirmedFacts, undefined, {
        inquiryConfirmed: true,
        inquiryAction: action,
        loopGoalOverride: loopGoalHint,
      });
    },
    fillTemplate: handleFillTemplateToInput,
    approvePlan: handleApproveAndExecutePlan,
    openCanvas: handleOpenCanvas,
    saveToWorkspace: handleSaveCodeToWorkspace,
    openGeneratedFile: handleOpenGeneratedFile,
    previewGeneratedFile: handlePreviewGeneratedFile,
  };

  const stableMarkInquirySubmitted = useCallback((key: string) => {
    latestMarkdownActionsRef.current.markInquirySubmitted(key);
  }, []);
  const stableSendInquiryFeedback = useCallback(
    (
      feedback: string,
      confirmedFacts?: ConfirmedFactItem[],
      inquiryAction?: InquirySubmitAction,
      loopGoalHint?: string
    ) => {
      latestMarkdownActionsRef.current.sendInquiryFeedback(
        feedback,
        confirmedFacts,
        inquiryAction,
        loopGoalHint
      );
    },
    []
  );
  const stableFillTemplate = useCallback((tmpl: string) => {
    latestMarkdownActionsRef.current.fillTemplate(tmpl);
  }, []);
  const stableApprovePlan = useCallback(
    (
      steps: PlanStepItem[],
      feedback: string,
      confirmedFacts?: ConfirmedFactItem[],
      loopGoalHint?: string
    ) => {
      latestMarkdownActionsRef.current.approvePlan(
        steps,
        feedback,
        confirmedFacts,
        loopGoalHint
      );
    },
    []
  );
  const stableOpenCanvas = useCallback(
    (code: string, language: string, customTitle?: string) => {
      latestMarkdownActionsRef.current.openCanvas(code, language, customTitle);
    },
    []
  );
  const stableSaveToWorkspace = useCallback((code: string, language: string) => {
    latestMarkdownActionsRef.current.saveToWorkspace(code, language);
  }, []);
  const stableOpenGeneratedFile = useCallback(
    (rawPath: string, mode: 'launch' | 'reveal') => {
      latestMarkdownActionsRef.current.openGeneratedFile(rawPath, mode);
    },
    []
  );
  const stablePreviewGeneratedFile = useCallback((rawPath: string) => {
    latestMarkdownActionsRef.current.previewGeneratedFile(rawPath);
  }, []);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground transition-colors duration-200">
      {/* 侧边栏 */}
      <Sidebar
        conversations={conversations}
        activeConvId={activeConvId}
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        activeNavHub={activeNavHub}
        onSelectNavHub={(hub) => setActiveNavHub(hub)}
        onSelectWorkspace={(wsId) => {
          void stopActiveStream('switch');
          lastStreamFactRefsRef.current = null;
          lastStreamErrorNoticeRef.current = null;
          lastActiveLoopSigRef.current = 'null';
          activeConvIdRef.current = null;
          setActiveWorkspaceId(wsId);
          setActiveConvId(null);
          setActiveLoop(null);
          setMessages([]);
        }}
        onRefreshWorkspaces={fetchWorkspaces}
        onMoveConvWorkspace={async (convId, targetWsId) => {
          await apiFetch(`/api/v1/conversations/${convId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace_id: targetWsId }),
          });
          fetchConversations(activeWorkspaceId);
        }}
        onSelectConv={(id) => {
          if (id !== activeConvId) {
            void stopActiveStream('switch');
            lastStreamErrorNoticeRef.current = null;
            lastActiveLoopSigRef.current = 'null';
            setActiveLoop(null);
          }
          activeConvIdRef.current = id;
          setActiveConvId(id);
        }}
        onNewChat={() => {
          void stopActiveStream('switch');
          lastStreamFactRefsRef.current = null;
          lastStreamErrorNoticeRef.current = null;
          lastActiveLoopSigRef.current = 'null';
          activeConvIdRef.current = null;
          setActiveConvId(null);
          setActiveLoop(null);
          setMessages([]);
        }}
        onDeleteConv={async (id) => {
          if (activeConvId === id || activeStreamMetaRef.current?.convId === id) {
            await stopActiveStream('switch');
          }
          await apiFetch('/api/v1/conversations/' + id, { method: 'DELETE' });
          fetchConversations();
          if (activeConvId === id) {
            lastStreamErrorNoticeRef.current = null;
            lastActiveLoopSigRef.current = 'null';
            activeConvIdRef.current = null;
            setActiveConvId(null);
            setActiveLoop(null);
            setMessages([]);
          }
        }}
        onTogglePin={async (id, isPinned) => {
          await apiFetch('/api/v1/conversations/' + id, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_pinned: isPinned }),
          });
          fetchConversations();
        }}
        onRenameConv={async (id, newTitle) => {
          await apiFetch('/api/v1/conversations/' + id, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: newTitle }),
          });
          fetchConversations();
        }}
        onOpenSearch={() => setIsSearchOpen(true)}
        onOpenAccounts={() => setIsAccountOpen(true)}
        onOpenSync={() => setIsSyncOpen(true)}
        onOpenSettings={() => {
          setSettingsInitialTab('models');
          setIsSettingsOpen(true);
        }}
        onCleanupDuplicates={async () => {
          await apiFetch('/api/v1/conversations/cleanup-duplicates', { method: 'POST' });
          fetchConversations();
        }}
      />

      {/* 主对话区（支持全域文件拖拽放置上传） */}
      <main
        onDragEnter={handleMainDragEnter}
        onDragOver={handleMainDragOver}
        onDragLeave={handleMainDragLeave}
        onDrop={handleMainDrop}
        className="flex-1 flex flex-col h-full overflow-hidden relative"
      >
        {/* 拖拽放置上传视觉遮罩 */}
        {activeNavHub === 'chat' && isDraggingFiles && (
          <div className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center p-6 bg-background/70 backdrop-blur-md animate-fade-in">
            <div className="w-full max-w-xl rounded-3xl border-2 border-dashed border-emerald-500/80 bg-emerald-500/[0.06] px-8 py-10 flex flex-col items-center text-center shadow-2xl">
              <div className="w-14 h-14 rounded-2xl bg-emerald-500/15 border border-emerald-500/35 flex items-center justify-center mb-4">
                <UploadCloud className="w-7 h-7 text-emerald-500 animate-bounce" />
              </div>
              <div className="text-base font-bold text-foreground mb-1.5">
                松手即可放置上传附件
              </div>
              <div className="text-xs text-muted-foreground leading-relaxed mb-4">
                不支持的格式、压缩包、可执行程序或超限文件将自动拦截并说明原因
              </div>
              <div className="flex flex-wrap items-center justify-center gap-2 text-[11px]">
                <span className="px-2.5 py-1 rounded-full bg-surface-100 border border-border text-foreground/90">
                  图片 PNG/JPG/WebP/GIF ≤ 10 MB
                </span>
                <span className="px-2.5 py-1 rounded-full bg-surface-100 border border-border text-foreground/90">
                  文档 PDF (≤ 10 MB) · Word/Excel/PPT (≤ 5 MB)
                </span>
                <span className="px-2.5 py-1 rounded-full bg-surface-100 border border-border text-foreground/90">
                  TXT/MD/CSV/JSON/代码 ≤ 5 MB · 最多 {MAX_ATTACHMENTS_PER_MESSAGE} 个
                </span>
              </div>
            </div>
          </div>
        )}
        {/* 顶部极简状态栏 */}
        <header className="h-13 py-2 border-b border-border/80 px-4 md:px-6 flex items-center justify-between bg-surface-100/50 backdrop-blur-md z-10 select-none">
          <div className="flex items-center space-x-3">
            <StatusPill data={statusPill} onOpenAccounts={() => setIsAccountOpen(true)} />
          </div>

          <div className="flex items-center space-x-1.5">
            {/* 记忆中枢与扩展插件入口 */}
            <button
              onClick={() => {
                setMemoryHubInitialTab('memories');
                setIsMemoryHubOpen(true);
              }}
              className="flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs text-muted-foreground hover:text-foreground hover:bg-surface-200/70 transition-all"
              title="打开长期记忆画像与 Skill/MCP 扩展中枢"
            >
              <Brain className="w-3.5 h-3.5 text-emerald-500" />
              <span className="text-[12px]">记忆中枢</span>
            </button>

            {/* Canvas 画布极简入口 */}
            <button
              onClick={() => setCanvasState((prev) => ({ ...prev, isOpen: !prev.isOpen }))}
              className={`flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs transition-all ${canvasState.isOpen
                  ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-medium'
                  : 'text-muted-foreground hover:text-foreground hover:bg-surface-200/70'
                }`}
              title="呼出/隐藏 Canvas 画布"
            >
              <PanelRight className="w-3.5 h-3.5" />
              <span className="text-[12px]">画布</span>
            </button>

            {/* 快速进入设置与模型测试 */}
            <button
              onClick={() => {
                setSettingsInitialTab('models');
                setIsSettingsOpen(true);
              }}
              className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200/70 transition-colors"
              title="设置与模型一键测试"
            >
              <Sliders className="w-4 h-4" />
            </button>

            {/* 多语种切换器（默认跟随系统语言） */}
            <LanguageToggle />

            {/* 界面浅色/深色/跟随系统快速切换器 */}
            <ThemeToggle />
          </div>
        </header>

        {/* 核心主枢纽视图路由 (对齐 Gemini 库 / 已安排 / 技能与应用) */}
        {activeNavHub === 'library' && (
          <LibraryView
            workspace={workspaces.find((w) => w.id === activeWorkspaceId)}
            onOpenCanvas={(title, content, type) => handleOpenCanvas(content, type, title)}
          />
        )}

        {activeNavHub === 'scheduled' && (
          <ScheduledView
            workspace={workspaces.find((w) => w.id === activeWorkspaceId)}
          />
        )}

        {activeNavHub === 'skills' && (
          <SkillsView
            workspace={workspaces.find((w) => w.id === activeWorkspaceId)}
          />
        )}

        {activeNavHub === 'chat' && (
          <>
            {/* 顶部长程任务领航舱 (Loop HUD) 与敏感操作审批拦截卡片：仅当存在所属当前激活会话的非闲聊任务且有实际对话流时展示 */}
            {activeConvId &&
              activeLoop &&
              activeLoop.conversation_id === activeConvId &&
              activeLoop.loop_status !== 'idle' &&
              messages.length > 0 && (
                <div className="px-4 md:px-12 lg:px-24 pt-3">
                  <LoopHUD
                    loop={activeLoop}
                    onRefresh={fetchActiveLoop}
                  />
                  {activeLoop.loop_status === 'waiting_approval' &&
                    activeLoop.pending_approval_call && (
                      <ApprovalCard
                        loopId={activeLoop.id}
                        approvalCall={activeLoop.pending_approval_call}
                        onResolved={fetchActiveLoop}
                      />
                    )}
                </div>
              )}

            {/* 顶部置顶任务草稿纸条 (Sticky Scratchpad Bar)：仅当 workingMemory 非空时显示 */}
            {workingMemory && Object.keys(workingMemory).length > 0 && (
              <div className="px-4 md:px-12 lg:px-24 py-2 border-b border-amber-500/25 bg-amber-500/5 flex items-center justify-between text-xs animate-fade-in">
                <div className="flex items-center space-x-2 min-w-0 flex-1">
                  <ClipboardList className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                  <span className="font-semibold text-amber-600 dark:text-amber-400 shrink-0">
                    当前任务草稿:
                  </span>
                  <span className="text-foreground truncate font-mono text-[11px]">
                    {workingMemory.goal ? `[目标: ${workingMemory.goal}] ` : ''}
                    {workingMemory.notes ? JSON.stringify(workingMemory.notes) : JSON.stringify(workingMemory)}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={handleClearScratchpad}
                  className="ml-3 px-2 py-0.5 rounded bg-amber-500/15 hover:bg-amber-500/25 text-amber-700 dark:text-amber-300 text-[11px] font-medium shrink-0 transition-colors"
                  title="任务已结束？一键重置清空工作草稿纸"
                >
                  清空草稿
                </button>
              </div>
            )}

            {/* 聊天气泡列表 / 欢迎卡片 */}
            <div
              ref={messagesContainerRef}
              className="flex-1 overflow-y-auto px-4 md:px-12 lg:px-24 py-6 space-y-6"
            >
              {messages.length === 0 ? (
                <PromptStarterCards onSelectPrompt={(text) => handleSendMessage(text)} />
              ) : (
                messages.map((msg, index) => {
                  const isUser = msg.role === 'user';
                  const isCurrentStreamingMsg = isStreaming && index === messages.length - 1;
                  const isInteractiveCard = !isUser && hasInteractiveInquiryBlock(msg.content || '');
                  const isProcessStep =
                    !isUser &&
                    !isInteractiveCard &&
                    Boolean(
                      (msg.tool_events && msg.tool_events.length > 0) ||
                      (msg.tool_calls_json && msg.tool_calls_json !== '[]') ||
                      msg.is_process_step
                    );
                  // 检查上一条消息是否也是同一发言序列中的中间过程步骤，用于去噪聚合
                  const prevMsg = index > 0 ? messages[index - 1] : null;
                  const isPrevProcessStep =
                    prevMsg &&
                    prevMsg.role !== 'user' &&
                    !hasInteractiveInquiryBlock(prevMsg.content || '') &&
                    Boolean(
                      (prevMsg.tool_events && prevMsg.tool_events.length > 0) ||
                      (prevMsg.tool_calls_json && prevMsg.tool_calls_json !== '[]') ||
                      prevMsg.is_process_step
                    );
                  const isContinuousProcessStep = isProcessStep && Boolean(isPrevProcessStep);
                  const msgKey = String(msg.id ?? `idx_${index}`);
                  const rawBody = msg.content || '';
                  const isLongProcessText =
                    isProcessStep && (rawBody.length > 360 || rawBody.split('\n').length > 6);
                  const isProcessExpanded = Boolean(expandedProcessMsgKeys[msgKey]) || isCurrentStreamingMsg;
                  const isSearchHighlighted = Boolean(msg.id && highlightedMessageId === msg.id);
                  return (
                    <div
                      key={msg.id || index}
                      id={msg.id ? `chat-msg-${msg.id}` : undefined}
                      className={`flex flex-col group transition-all duration-300 ${
                        isContinuousProcessStep
                          ? 'space-y-1 pl-3.5 border-l-2 border-border/40 ml-1.5 mt-1'
                          : isProcessStep
                            ? 'space-y-1.5 pl-3.5 border-l-2 border-emerald-500/30 ml-1.5 mt-2.5'
                            : 'space-y-2.5'
                      } ${isUser ? 'items-end' : 'items-start'} ${
                        isSearchHighlighted
                          ? 'ring-2 ring-emerald-500/70 bg-emerald-500/5 rounded-2xl p-2 -m-2'
                          : ''
                      }`}
                    >
                      {/* 发言角色标志：连续过程步骤中隐藏重复的头像与角色头，仅在过程起点与交付答复展示 */}
                      {(!isContinuousProcessStep || isUser) && (
                        <div
                          className={`flex items-center space-x-2 px-1 ${
                            isProcessStep ? 'text-[11px] text-muted-foreground/70' : 'text-xs text-muted-foreground'
                          }`}
                        >
                          {isUser ? (
                            <span className="font-semibold text-foreground">您</span>
                          ) : (
                            <div className="flex items-center space-x-1.5 flex-wrap gap-y-1">
                              <Sparkles
                                className={
                                  isProcessStep
                                    ? 'w-3 h-3 text-emerald-500/70'
                                    : 'w-3.5 h-3.5 text-emerald-500'
                                }
                              />
                              {(() => {
                                const rawModel = msg.downgrade_info?.current_model || msg.model_name || currentModel || '';
                                const isGemini = rawModel.toLowerCase().includes('gemini');
                                const modelDisplayName = isGemini ? `Gemini (${rawModel})` : (rawModel || 'AI Assistant');
                                return (
                                  <span
                                    className={
                                      isProcessStep
                                        ? 'font-medium text-muted-foreground/80'
                                        : 'font-bold text-foreground'
                                    }
                                  >
                                    {modelDisplayName}
                                  </span>
                                );
                              })()}
                              {/* 彻底移除误导性的 过程思考 徽标 */}
                              {msg.downgrade_info && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 font-medium flex items-center space-x-1">
                                  <AlertTriangle className="w-2.5 h-2.5 text-amber-500" />
                                  <span>已自动降级</span>
                                </span>
                              )}
                              {msg.executed_by_account && (
                                <span
                                  className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                                    isProcessStep
                                      ? 'bg-surface-200/45 border border-border/45 text-muted-foreground/60'
                                      : 'bg-surface-200 border border-border'
                                  }`}
                                >
                                  调度: {msg.executed_by_account}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                      )}

                      {/* 模型自动降级提示横幅 */}
                      {!isUser && msg.downgrade_info && (
                        <div className="flex items-start space-x-2.5 px-3.5 py-2 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-800 dark:text-amber-200 text-xs animate-fade-in shadow-xs max-w-[88%] md:max-w-[82%]">
                          <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
                          <div className="flex-1 leading-snug">
                            <span className="font-bold text-amber-700 dark:text-amber-300 mr-1.5">[模型自动降级通知]:</span>
                            <span>
                              原模型 <code className="font-mono bg-amber-500/20 px-1.5 py-0.5 rounded text-[11px] font-semibold">{msg.downgrade_info.original_model}</code> 响应受限或配额耗尽，系统已自动无缝降级切换至高可用模型 <code className="font-mono bg-emerald-500/20 text-emerald-800 dark:text-emerald-300 px-1.5 py-0.5 rounded text-[11px] font-bold">{msg.downgrade_info.current_model}</code> 继续生成。右侧模型选择器已自动同步跳至该模型！
                            </span>
                          </div>
                        </div>
                      )}

                      {/* 步数自适应扩展与深度自愈横幅 */}
                      {!isUser && msg.step_extend_info && (
                        <div className="flex items-start space-x-2.5 px-3.5 py-2 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-800 dark:text-indigo-200 text-xs animate-fade-in shadow-xs max-w-[88%] md:max-w-[82%]">
                          <Sparkles className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5 animate-pulse" />
                          <div className="flex-1 leading-snug">
                            <span className="font-bold text-indigo-600 dark:text-indigo-300 mr-1.5">[自主推演与动态扩展]:</span>
                            <span>{msg.step_extend_info.message}</span>
                          </div>
                        </div>
                      )}

                      {/* 思考过程展开收起卡片 */}
                      {!isUser && msg.thinking && (
                        <ThinkingBlock content={msg.thinking} isStreaming={isCurrentStreamingMsg} />
                      )}

                      {/* 确定性事实视图与长期记忆引用溯源微提示 */}
                      {!isUser && ((msg.fact_refs && msg.fact_refs.length > 0) || (msg.memory_refs && msg.memory_refs.length > 0)) && (
                        <div className="flex flex-wrap items-center gap-2">
                          {msg.fact_refs && msg.fact_refs.length > 0 && (
                            <button
                              type="button"
                              onClick={() => {
                                setMemoryHubInitialTab('facts');
                                setIsMemoryHubOpen(true);
                              }}
                              className="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg bg-teal-500/10 hover:bg-teal-500/15 border border-teal-500/25 text-[11px] text-teal-700 dark:text-teal-300 transition-colors"
                              title={msg.fact_refs.map((f) => `• [${f.scope === 'global' ? '全局' : '工作区'}] ${f.fact_key} = ${f.fact_value}`).join('\n')}
                            >
                              <span>📌 已激活 {msg.fact_refs.length} 项工作区确定性事实（点击查看双时间轴）</span>
                            </button>
                          )}
                          {msg.memory_refs && msg.memory_refs.length > 0 && (
                            <button
                              type="button"
                              onClick={() => {
                                setMemoryHubInitialTab('memories');
                                setIsMemoryHubOpen(true);
                              }}
                              className="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/15 border border-emerald-500/20 text-[11px] text-emerald-600 dark:text-emerald-400 transition-colors"
                              title={msg.memory_refs.map((m) => `• ${m.content}`).join('\n')}
                            >
                              <Brain className="w-3 h-3 text-emerald-500" />
                              <span>已引用 {msg.memory_refs.length} 条长期个人偏好（点击查看或管理）</span>
                            </button>
                          )}
                        </div>
                      )}

                      {/* 消息正文主体：正式交付回复保留高清晰卡片，中间过程步骤降权为柔和旁白或微提示 */}
                      {(isUser || msg.content || (isCurrentStreamingMsg && (!msg.tool_events || msg.tool_events.length === 0))) && (
                        (() => {
                          const rawTrimmed = (msg.content || '').trim();
                          const isBriefProcessNarrative =
                            isProcessStep &&
                            Boolean(rawTrimmed) &&
                            rawTrimmed.length < 120 &&
                            !rawTrimmed.includes('```');

                          if (isBriefProcessNarrative) {
                            return (
                              <div className="text-[11px] text-muted-foreground/70 font-mono italic px-1 py-0.5 flex items-center space-x-1.5 animate-fade-in">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500/50 shrink-0"></span>
                                <span>{rawTrimmed}</span>
                              </div>
                            );
                          }

                          return (
                            <div
                              className={`max-w-[88%] md:max-w-[82%] break-anywhere ${
                                isUser
                                  ? 'rounded-2xl px-5 py-3.5 text-[14px] leading-[1.68] shadow-sm bg-emerald-100 dark:bg-emerald-950/70 text-emerald-950 dark:text-emerald-100 border border-emerald-300/60 dark:border-emerald-500/30 rounded-tr-none shadow-emerald-500/5'
                                  : isProcessStep
                                    ? 'ml-0.5 pl-3.5 pr-3 py-2 border-l-2 border-border/65 bg-surface-200/20 dark:bg-surface-200/10 rounded-r-xl text-[12px] leading-[1.58] text-muted-foreground/75'
                                    : 'rounded-2xl px-5 py-3.5 text-[14px] leading-[1.68] shadow-sm bg-surface-card text-foreground rounded-tl-none border border-border/80'
                              }`}
                            >
                          {isUser ? (
                            (() => {
                              const { cleanContent: userBody, parsedAttachments } = parseMessageAttachments(msg.content);
                              return (
                                <div className="text-emerald-950 dark:text-emerald-100 break-anywhere space-y-2.5">
                                  {userBody && (
                                    <ReactMarkdown
                                      remarkPlugins={[remarkGfm]}
                                      components={{
                                        img({ node, src, alt, ...props }: any) {
                                          if (!src) return null;
                                          return (
                                            <span className="block my-2">
                                              <img
                                                src={src}
                                                alt={alt || '附带图片'}
                                                className="rounded-xl border border-emerald-500/20 max-w-full h-auto object-cover max-h-[360px] cursor-pointer shadow-md hover:opacity-95 transition-opacity"
                                                onClick={() => handleOpenCanvas(src, 'image', alt ? `附带图片: ${alt}` : '上传图片原图预览')}
                                                {...props}
                                              />
                                              {alt && (
                                                <span className="block text-[11px] text-emerald-700 dark:text-emerald-300 mt-1 opacity-80">
                                                  {alt}
                                                </span>
                                              )}
                                            </span>
                                          );
                                        },
                                      }}
                                    >
                                      {userBody}
                                    </ReactMarkdown>
                                  )}

                                  {parsedAttachments.length > 0 && (
                                    <div className="flex flex-col gap-2 pt-1">
                                      {parsedAttachments.map((att, attIdx) => {
                                        const isImg =
                                          att.category === 'image' ||
                                          Boolean(att.mime_type && att.mime_type.startsWith('image/'));
                                        if (isImg && att.url) {
                                          return (
                                            <div key={attIdx} className="block">
                                              <img
                                                src={att.url}
                                                alt={att.name}
                                                className="rounded-xl border border-emerald-500/25 max-w-full h-auto object-cover max-h-[320px] cursor-pointer shadow-md hover:opacity-95 transition-opacity"
                                                onClick={() =>
                                                  handleOpenCanvas(att.url || '', 'image', `附带图片: ${att.name}`)
                                                }
                                              />
                                              <div className="text-[11px] text-emerald-800 dark:text-emerald-300 mt-1 opacity-80 flex items-center gap-1.5">
                                                <span>{att.name}</span>
                                                {att.size_str && <span>· {att.size_str}</span>}
                                              </div>
                                            </div>
                                          );
                                        }

                                        const isSheet = att.name.toLowerCase().endsWith('.xlsx');
                                        const isPdf = att.name.toLowerCase().endsWith('.pdf') || att.category === 'pdf';
                                        return (
                                          <a
                                            key={attIdx}
                                            href={att.url || '#'}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            onClick={(ev) => {
                                              if (!att.url) ev.preventDefault();
                                            }}
                                            title={
                                              att.rel_path
                                                ? `工作区沙盒路径: ${att.rel_path}${att.file_path ? `\n物理路径: ${att.file_path}` : ''}`
                                                : att.file_path
                                                  ? `本地路径: ${att.file_path}`
                                                  : att.name
                                            }
                                            className="flex items-center space-x-2.5 px-3 py-2 rounded-xl bg-emerald-500/15 hover:bg-emerald-500/20 border border-emerald-500/30 text-xs text-emerald-950 dark:text-emerald-100 transition-colors max-w-full"
                                          >
                                            {isSheet ? (
                                              <FileSpreadsheet className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
                                            ) : (
                                              <FileText
                                                className={`w-4 h-4 shrink-0 ${isPdf
                                                    ? 'text-rose-500 dark:text-rose-400'
                                                    : 'text-emerald-600 dark:text-emerald-400'
                                                  }`}
                                              />
                                            )}
                                            <span className="font-medium truncate">{att.name}</span>
                                            {att.size_str && (
                                              <span className="text-[11px] opacity-75 shrink-0 font-mono">
                                                {att.size_str}
                                              </span>
                                            )}
                                          </a>
                                        );
                                      })}
                                    </div>
                                  )}
                                </div>
                              );
                            })()
                          ) : (
                            (() => {
                              const msgInquiryKey = resolveMessageInquiryKey(
                                msg.content || '',
                                msg.id,
                                index
                              );
                              const isMsgInquiryLocked = computeInquiryCardLocked(
                                messages,
                                index,
                                msgInquiryKey,
                                submittedInquiryKeys
                              );
                              const mdNode = (
                                <AssistantMarkdownMessage
                                  content={msg.content || ''}
                                  isStreaming={isCurrentStreamingMsg}
                                  muted={isProcessStep}
                                  inquiryKey={msgInquiryKey}
                                  isInquirySubmitted={isMsgInquiryLocked}
                                  isExecutionContext={sceneMode === 'agent'}
                                  onMarkInquirySubmitted={stableMarkInquirySubmitted}
                                  onSendInquiryFeedback={stableSendInquiryFeedback}
                                  onFillTemplate={stableFillTemplate}
                                  onApprovePlan={stableApprovePlan}
                                  onOpenCanvas={stableOpenCanvas}
                                  onSaveToWorkspace={stableSaveToWorkspace}
                                  onOpenGeneratedFile={stableOpenGeneratedFile}
                                  onPreviewGeneratedFile={stablePreviewGeneratedFile}
                                />
                              );

                              if (!isLongProcessText) {
                                return mdNode;
                              }

                              return (
                                <div className="space-y-1.5">
                                  <div
                                    className={
                                      isProcessExpanded
                                        ? 'max-h-[420px] overflow-y-auto pr-1'
                                        : 'max-h-36 overflow-hidden relative'
                                    }
                                  >
                                    {mdNode}
                                    {!isProcessExpanded && (
                                      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-[var(--bg-main)]/85 to-transparent" />
                                    )}
                                  </div>
                                  <button
                                    type="button"
                                    onClick={() => toggleProcessMsgExpanded(msgKey)}
                                    className="inline-flex items-center space-x-1 text-[11px] text-muted-foreground/75 hover:text-foreground transition-colors"
                                  >
                                    {isProcessExpanded ? (
                                      <ChevronDown className="w-3 h-3" />
                                    ) : (
                                      <ChevronRight className="w-3 h-3" />
                                    )}
                                    <span>
                                      {isProcessExpanded
                                        ? '收起过程内容'
                                        : `展开完整过程内容 (${rawBody.length} 字)`}
                                    </span>
                                  </button>
                                </div>
                              );
                            })()
                          )}
                        </div>
                      );
                    })()
                  )}

                      {/* 生成文件快捷直达栏（点击默认程序直接打开 / 预览 / 所在目录定位；过程步骤与待确认卡不重复显示交付栏） */}
                      {!isUser &&
                        !isProcessStep &&
                        msg.content &&
                        !isCurrentStreamingMsg &&
                        !isInteractiveCard &&
                        (() => {
                          const deliverablePaths = extractGeneratedFilePaths(msg.content);
                          if (deliverablePaths.length === 0) return null;
                          return (
                            <div className="flex flex-wrap items-center gap-2 px-3 py-2 rounded-xl border border-emerald-500/20 bg-emerald-500/5 max-w-[92%] md:max-w-[85%] animate-fade-in">
                              <div className="inline-flex items-center space-x-1.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300 shrink-0 mr-1">
                                <PackageOpen className="w-3.5 h-3.5 text-emerald-500" />
                                <span>生成文件直达</span>
                              </div>
                              {deliverablePaths.map((fp) => (
                                <GeneratedFileChip
                                  key={fp}
                                  filePath={fp}
                                  variant="card"
                                  onLaunch={(targetPath) => handleOpenGeneratedFile(targetPath, 'launch')}
                                  onReveal={(targetPath) => handleOpenGeneratedFile(targetPath, 'reveal')}
                                  onPreview={(targetPath) => handlePreviewGeneratedFile(targetPath)}
                                />
                              ))}
                            </div>
                          );
                        })()}

                      {/* 内联终端工具调用与安全审批卡片：跟随在思考过程与正文输出之后的最底端，避免被上方长内容顶走 */}
                      {!isUser && msg.tool_events && msg.tool_events.length > 0 && (
                        <InlineToolCard
                          events={msg.tool_events}
                          onApprove={handleApproveToolCall}
                        />
                      )}

                      {/* 单行轻量 Token 与 KV Cache 水表 */}
                      {!isUser && msg.token_stats && (
                        <TokenMetricsFooterBar
                          stats={msg.token_stats}
                          onOpenStats={() => setIsTokenStatsModalOpen(true)}
                        />
                      )}

                      {/* AI 消息交互底栏：过程步骤默认半隐弱化，悬停时显露，保持主线阅读清爽 */}
                      {!isUser && msg.content && (
                        <div className={isProcessStep ? 'opacity-25 group-hover:opacity-90 transition-opacity origin-left scale-95' : ''}>
                          <MessageActions
                            content={msg.content}
                            onModifyTone={handleModifyTone}
                            onRegenerate={() => {
                              const lastUserMsg = [...messages].reverse().find((m) => m.role === 'user');
                              if (lastUserMsg) {
                                const { cleanContent, parsedAttachments } = parseMessageAttachments(lastUserMsg.content);
                                handleSendMessage(
                                  cleanContent || (parsedAttachments.length > 0 ? '请结合附带的文件或图片内容进行分析解答。' : ''),
                                  undefined,
                                  undefined,
                                  parsedAttachments.length > 0 ? parsedAttachments : undefined
                                );
                              }
                            }}
                          />
                        </div>
                      )}
                    </div>
                  );
                })
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* 底部折叠式硬件 HUD 仪表盘 */}
            <HardwareHud
              isOpen={isHudOpen}
              onToggle={() => setIsHudOpen(!isHudOpen)}
              onProposeProcessKill={handleProposeProcessKill}
            />

            {/* 底部悬浮输入中枢 */}
            <div className="p-4 md:px-12 lg:px-24 bg-gradient-to-t from-background via-background/95 to-transparent">
              <div className="max-w-4xl mx-auto space-y-2 relative">
                <CommandAutocomplete
                  input={inputPrompt}
                  cursorPosition={inputCursorPos}
                  onSelect={(text, range) => handleInsertCommandAtCursor(text, range)}
                  onClose={() => { }}
                />

                {/* 附件格式限制 / 超限 / 拒收说明横幅 */}
                {attachmentRejectionNotices.length > 0 && (
                  <div className="px-3.5 py-2.5 rounded-2xl bg-amber-500/12 border border-amber-500/40 text-xs text-amber-900 dark:text-amber-200 animate-fade-in shadow-sm space-y-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-start space-x-2 min-w-0">
                        <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
                        <div className="space-y-1 min-w-0">
                          <div className="font-semibold text-amber-700 dark:text-amber-300">
                            部分文件未加入附件列表（格式受限或超出大小限制）
                          </div>
                          <ul className="list-disc pl-4 space-y-0.5 text-[11px] leading-relaxed">
                            {attachmentRejectionNotices.map((reason, rIdx) => (
                              <li key={rIdx}>{reason}</li>
                            ))}
                          </ul>
                        </div>
                      </div>
                      <div className="flex items-center space-x-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => setShowSupportedFormatsHelp((prev) => !prev)}
                          className="flex items-center space-x-1 px-2 py-1 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 text-amber-800 dark:text-amber-200 text-[11px] font-medium transition-colors"
                        >
                          <Info className="w-3 h-3" />
                          <span>{showSupportedFormatsHelp ? '收起格式说明' : '查看支持格式'}</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setAttachmentRejectionNotices([]);
                            setShowSupportedFormatsHelp(false);
                          }}
                          className="p-1 rounded-lg hover:bg-amber-500/20 text-amber-700 dark:text-amber-300 transition-colors"
                          title="关闭提示"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>

                    {showSupportedFormatsHelp && (
                      <div className="pt-2 border-t border-amber-500/25 grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px]">
                        {SUPPORTED_ATTACHMENT_GROUPS.map((grp, gIdx) => (
                          <div key={gIdx} className="p-2 rounded-xl bg-surface-100/80 border border-border/70">
                            <div className="font-semibold text-foreground mb-0.5">{grp.title}</div>
                            <div className="text-muted-foreground leading-snug">{grp.items}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {(attachments.length > 0 || isUploadingAttachment) && (
                  <div className="flex items-center flex-wrap gap-2 px-3 pb-1">
                    {attachments.map((att, idx) => {
                      const isSheet = att.name.toLowerCase().endsWith('.xlsx');
                      const isPdf = att.category === 'pdf' || att.name.toLowerCase().endsWith('.pdf');
                      return (
                        <div
                          key={idx}
                          title={att.filePath ? `物理路径: ${att.filePath}` : att.name}
                          className="flex items-center space-x-2 px-2.5 py-1.5 rounded-xl bg-surface-100 border border-border text-xs text-foreground animate-scale-in shadow-sm max-w-[260px]"
                        >
                          {att.type === 'image' && att.dataUrl ? (
                            <img src={att.dataUrl} alt="" className="w-6 h-6 rounded object-cover border border-border shrink-0" />
                          ) : isSheet ? (
                            <FileSpreadsheet className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                          ) : isPdf ? (
                            <FileText className="w-3.5 h-3.5 text-rose-500 shrink-0" />
                          ) : (
                            <Paperclip className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                          )}
                          <span className="truncate flex-1">{att.name}</span>
                          <span className="text-[10px] text-muted-foreground shrink-0">{att.size}</span>
                          <button
                            type="button"
                            onClick={() => setAttachments((prev) => prev.filter((_, i) => i !== idx))}
                            className="hover:text-rose-500 p-0.5 rounded transition-colors shrink-0"
                            title="移除附件"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </div>
                      );
                    })}
                    {isUploadingAttachment && (
                      <div className="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-[11px] text-emerald-600 dark:text-emerald-400">
                        <RefreshCw className="w-3 h-3 animate-spin" />
                        <span>正在校验并上传附件...</span>
                      </div>
                    )}
                  </div>
                )}

                {/* 全局常驻待审批卡片：无论页面向上滚动多远或思考过程多长，始终可在输入框正上方一键点击允许或拒绝 */}
                {pendingApprovalEvent && (
                  <div className="px-4 py-3 mb-2 rounded-2xl bg-amber-500/15 border border-amber-500/40 backdrop-blur-md flex items-center justify-between shadow-lg animate-fade-in text-xs">
                    <div className="flex items-center space-x-2.5 min-w-0 mr-3">
                      <div className="w-7 h-7 rounded-lg bg-amber-500/20 border border-amber-500/40 flex items-center justify-center shrink-0">
                        <AlertTriangle className="w-4 h-4 text-amber-500 animate-pulse" />
                      </div>
                      <div className="min-w-0">
                        <div className="font-bold text-amber-600 dark:text-amber-400">
                          安全沙箱拦截：Agent 申请执行操作
                        </div>
                        <div className="text-[11px] text-foreground/80 font-mono truncate max-w-[420px]">
                          {pendingApprovalEvent.command}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center space-x-2 shrink-0">
                      <button
                        type="button"
                        onClick={() => handleApproveToolCall(pendingApprovalEvent.call_id, false)}
                        className="px-3 py-1.5 rounded-xl bg-surface-200 hover:bg-rose-500/20 text-muted-foreground hover:text-rose-500 font-semibold text-xs transition-colors"
                      >
                        拒绝
                      </button>
                      <button
                        type="button"
                        onClick={() => handleApproveToolCall(pendingApprovalEvent.call_id, true)}
                        className="px-4 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs shadow-md transition-all active:scale-95 flex items-center space-x-1"
                      >
                        <CheckCircle2 className="w-3.5 h-3.5 mr-1" />
                        <span>允许执行</span>
                      </button>
                    </div>
                  </div>
                )}

                {/* 智能防幻觉意图嗅探胶囊：当检测到写盘需求却处于对话模式时一键引导 */}
                {sceneMode !== 'agent' && /(写入|创建文件|新建文件|写到|落盘|保存到|在.*创建|新建.*目录|写代码到|写入代码)/.test(inputPrompt) && (
                  <div className="px-3.5 py-1.5 mb-2 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-between text-xs text-emerald-700 dark:text-emerald-300 animate-fade-in shadow-sm">
                    <div className="flex items-center space-x-2">
                      <Sparkles className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                      <span>
                        检测到本地写盘需求：当前处于<strong>「对话模式」</strong>（仅纯文本生成）。
                        是否一键切换至「任务模式」真实落盘执行？
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleSwitchSceneMode('agent')}
                      className="px-2.5 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-[11px] shrink-0 ml-2 shadow-sm shadow-emerald-600/20 transition-all"
                    >
                      一键切换为任务模式
                    </button>
                  </div>
                )}

                {/* 核心输入大胶囊 */}
                <div className="rounded-2xl border border-border/80 bg-surface-card p-2 focus-within:border-accent focus-within:shadow-md transition-all shadow-sm">
                  {/* 多 Skill 联合协同胶囊条 */}
                  {mountedChainSkills.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5 px-3 pt-1 pb-1.5 border-b border-border/50 mb-1 text-[11px]">
                      <span className="text-muted-foreground font-medium shrink-0">
                        {mountedChainSkills.length > 1 ? '联合协同技能:' : '已激活技能:'}
                      </span>
                      {mountedChainSkills.map((sk, idx) => (
                        <React.Fragment key={sk.command}>
                          {idx > 0 && <span className="text-muted-foreground/60">→</span>}
                          <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full bg-emerald-500/12 border border-emerald-500/30 text-emerald-700 dark:text-emerald-300 font-mono">
                            <span>{sk.command}</span>
                            <span className="text-[10px] opacity-75">v{sk.version}</span>
                            <button
                              type="button"
                              onClick={() => handleRemoveSkillToken(sk.command)}
                              className="hover:text-rose-500 transition-colors ml-0.5"
                              title={`移除 ${sk.command}`}
                            >
                              <X className="w-2.5 h-2.5" />
                            </button>
                          </span>
                        </React.Fragment>
                      ))}
                      {mountedChainSkills.length > 1 && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 border border-indigo-500/25">
                          多 Skill 联合编排 ({mountedChainSkills.length})
                        </span>
                      )}
                    </div>
                  )}

                  <div className="relative flex items-start">
                    <textarea
                      ref={textareaRef}
                      value={inputPrompt}
                      onChange={(e) => {
                        const val = e.target.value;
                        const cursor = e.target.selectionStart ?? val.length;
                        setInputPrompt(val);
                        setInputCursorPos(cursor);
                        if (val === '/') {
                          setIsSkillPickerOpen(true);
                        }
                      }}
                      onClick={(e) => {
                        setInputCursorPos(e.currentTarget.selectionStart ?? 0);
                      }}
                      onKeyUp={(e) => {
                        setInputCursorPos(e.currentTarget.selectionStart ?? 0);
                      }}
                      onSelect={(e) => {
                        setInputCursorPos(e.currentTarget.selectionStart ?? 0);
                      }}
                      onPaste={handlePaste}
                      onKeyDown={(e) => {
                        if (e.key === 'Tab') {
                          // 检测输入框中是否存在 {{参数名:默认值}} 形式的填空占位符
                          const text = inputPrompt;
                          const matches: { start: number; end: number; placeholder: string }[] = [];
                          const regex = /\{\{([^}]+)\}\}/g;
                          let m: RegExpExecArray | null;
                          while ((m = regex.exec(text)) !== null) {
                            matches.push({ start: m.index, end: m.index + m[0].length, placeholder: m[1] });
                          }

                          if (matches.length > 0) {
                            e.preventDefault();
                            const textarea = textareaRef.current;
                            if (textarea) {
                              const currentPos = e.shiftKey ? textarea.selectionStart : textarea.selectionEnd;
                              let targetMatch: typeof matches[0];
                              if (e.shiftKey) {
                                const prevMatches = matches.filter((item) => item.end < currentPos);
                                targetMatch = prevMatches.length > 0 ? prevMatches[prevMatches.length - 1] : matches[matches.length - 1];
                              } else {
                                const nextMatches = matches.filter((item) => item.start >= currentPos);
                                targetMatch = nextMatches.length > 0 ? nextMatches[0] : matches[0];
                              }
                              if (targetMatch) {
                                textarea.focus();
                                textarea.setSelectionRange(targetMatch.start, targetMatch.end);
                              }
                            }
                            return;
                          }
                        }

                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          handleSendMessage();
                        }
                      }}
                      rows={isInputExpanded ? 12 : 1}
                      placeholder="输入消息或指令...（支持多次 / 联合协同多个技能，Ctrl+V 粘贴图片，Enter 发送）"
                      className={`w-full bg-transparent border-none outline-none text-[14px] text-foreground placeholder-muted-foreground pl-3 pr-10 py-1.5 resize-none leading-relaxed break-anywhere ${
                        isInputExpanded
                          ? 'h-[46vh] min-h-[260px] max-h-[520px] overflow-y-auto'
                          : 'min-h-[44px] max-h-[160px] overflow-y-auto'
                      }`}
                    />

                    {/* 半屏展开 / 收起长文本编辑框切换按钮 */}
                    <button
                      type="button"
                      onClick={() => {
                        setIsInputExpanded((prev) => !prev);
                        requestAnimationFrame(() => textareaRef.current?.focus());
                      }}
                      title={
                        isInputExpanded
                          ? '收起输入框'
                          : '扩展至半屏高度查看与编辑长文本'
                      }
                      className={`absolute right-1.5 top-1.5 p-1.5 rounded-lg transition-colors ${
                        isInputExpanded
                          ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30'
                          : inputPrompt.length > 60 || inputPrompt.includes('\n')
                          ? 'bg-surface-200/80 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/15'
                          : 'text-muted-foreground/70 hover:text-foreground hover:bg-surface-200/80'
                      }`}
                    >
                      {isInputExpanded ? (
                        <Minimize2 className="w-3.5 h-3.5" />
                      ) : (
                        <Maximize2 className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </div>

                  <div className="flex items-center justify-between pt-1 px-1 select-none">
                    <div className="flex items-center space-x-1.5">
                      <label
                        className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-surface-200/80 rounded-lg cursor-pointer transition-colors"
                        title="添加常见图片 (PNG/JPG/WebP/GIF ≤10MB)、文档 (PDF ≤10MB · Word/Excel/PPT ≤5MB) 或代码/文本文件（≤5MB）；支持拖拽放置与 Ctrl+V 粘贴，最多 5 个"
                      >
                        <input
                          type="file"
                          accept={FILE_INPUT_ACCEPT}
                          multiple
                          onChange={handleFileUpload}
                          className="hidden"
                        />
                        <Plus className="w-4 h-4" />
                      </label>

                      {/* 模式双态药丸切换：[对话] 与 [任务] */}
                      <div className="flex items-center rounded-xl bg-surface-100 border border-border/70 p-0.5 text-xs">
                        {(
                          [
                            { id: 'chat', label: '对话', tip: '对话模式：纯思维链探讨与知识推演，不执行写盘与命令' },
                            { id: 'agent', label: '任务', tip: '任务模式：具身执行与自主自愈，真实读写当前工作区文件并运行 Shell' },
                          ] as const
                        ).map((m) => (
                          <button
                            key={m.id}
                            type="button"
                            onClick={() => handleSwitchSceneMode(m.id)}
                            title={m.tip}
                            className={`px-3 py-1 rounded-lg transition-all ${sceneMode === m.id
                                ? 'bg-emerald-600 text-white font-semibold shadow-sm'
                                : 'text-muted-foreground hover:text-foreground'
                              }`}
                          >
                            {m.label}
                          </button>
                        ))}
                      </div>

                      {/* 执行策略降维切换：任务模式下 [直接执行 | 谋定而动]，对话模式下 [需求澄清] */}
                      {sceneMode === 'agent' ? (
                        <div className="flex items-center rounded-xl bg-surface-100 border border-border/70 p-0.5 text-xs">
                          <button
                            type="button"
                            onClick={() => handleSwitchExecStrategy('direct')}
                            title="直接执行：立即执行意图与工具操作"
                            className={`px-2.5 py-1 rounded-lg transition-all flex items-center space-x-1 ${executionStrategy === 'direct'
                                ? 'bg-emerald-600 text-white font-semibold shadow-sm'
                                : 'text-muted-foreground hover:text-foreground'
                              }`}
                          >
                            <span>⚡ 直接执行</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => handleSwitchExecStrategy('plan_first')}
                            title="谋定而动：动工前先梳理方案与分步清单，确认后自愈执行，彻底杜绝返工"
                            className={`px-2.5 py-1 rounded-lg transition-all flex items-center space-x-1 ${executionStrategy === 'plan_first'
                                ? 'bg-emerald-600 text-white font-semibold shadow-sm'
                                : 'text-muted-foreground hover:text-foreground'
                              }`}
                          >
                            <span>📋 谋定而动</span>
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={handleToggleClarifyMode}
                          title={clarifyMode ? '需求澄清模式已开启：遇到模糊问题先生成交互卡片对齐' : '点击开启需求前置澄清模式'}
                          className={`flex items-center space-x-1 px-2.5 py-1 rounded-xl text-xs border transition-all ${clarifyMode
                              ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-600 dark:text-emerald-400 font-semibold'
                              : 'border-border bg-surface-100 text-muted-foreground hover:text-foreground'
                            }`}
                        >
                          <Compass className="w-3.5 h-3.5" />
                          <span>需求澄清</span>
                        </button>
                      )}

                      {/* 无痕模式开关 */}
                      <button
                        type="button"
                        onClick={handleToggleIncognito}
                        title={incognito ? '当前为无痕模式（不读取也不保存长期记忆）' : '点击开启无痕对话模式'}
                        className={`flex items-center space-x-1 px-2 py-1 rounded-lg text-[11px] border transition-all ${incognito
                            ? 'bg-purple-500/15 border-purple-500/40 text-purple-600 dark:text-purple-400 font-medium'
                            : 'border-transparent text-muted-foreground hover:text-foreground hover:bg-surface-200/60'
                          }`}
                      >
                        <EyeOff className="w-3 h-3" />
                        <span className="hidden sm:inline">无痕</span>
                      </button>

                      {/* 快捷技能呼出 */}
                      <button
                        type="button"
                        onClick={() => setIsSkillPickerOpen(true)}
                        title="浏览或呼出当前工作区专属技能与全局底座技能 (/)"
                        className="flex items-center space-x-1 px-2.5 py-1 rounded-xl text-[11px] border border-border bg-surface-100 hover:bg-surface-200 text-foreground transition-all font-mono"
                      >
                        <span>/ 技能</span>
                      </button>

                      {/* 字数提示微标（半屏展开或输入内容后展示） */}
                      {(isInputExpanded || inputPrompt.length > 0) && (
                        <span className="text-[10px] font-mono text-muted-foreground/80 px-1.5">
                          {inputPrompt.length} 字
                        </span>
                      )}
                    </div>

                    <div className="flex items-center space-x-2">
                      {/* 模型选择器：动态根据当前账号属性识别可用模型，并提供一键测试入口 */}
                      <ModelSelector
                        currentModel={currentModel}
                        thinkingLevel={thinkingLevel}
                        models={availableModels}
                        accountName={modelAccountInfo.accountName}
                        authLabel={modelAccountInfo.authLabel}
                        isTested={modelAccountInfo.isTested}
                        onSelectModel={(m) => {
                          hasUserSelectedModelRef.current = true;
                          setCurrentModel(m);
                        }}
                        onSelectThinking={(tl) => setThinkingLevel(tl)}
                        onOpenModelTest={() => {
                          setSettingsInitialTab('models');
                          setIsSettingsOpen(true);
                        }}
                        onOpenAccountModal={() => setIsAccountOpen(true)}
                      />

                      {/* 发送 / 停止生成切换按钮 */}
                      {isStreaming ? (
                        <button
                          type="button"
                          onClick={() => void stopActiveStream('user')}
                          className="px-2.5 py-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-white transition-all shadow-sm shadow-rose-600/25 flex items-center space-x-1.5"
                          title="立即停止当前生成与终端执行"
                        >
                          <Square className="w-3.5 h-3.5 fill-current" />
                          <span className="text-xs font-semibold hidden sm:inline">停止</span>
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => handleSendMessage()}
                          disabled={isUploadingAttachment || (!inputPrompt.trim() && attachments.length === 0)}
                          className="p-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white transition-all disabled:opacity-30 disabled:cursor-not-allowed shadow-sm shadow-emerald-600/20"
                          title="发送提问"
                        >
                          <Send className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </>
        )}
      </main>

      {/* 操作与文件落盘浮层通知 */}
      {saveFileToast && (
        <div
          className={`fixed bottom-6 right-6 z-50 px-4 py-2.5 rounded-2xl text-white text-xs font-semibold shadow-2xl flex items-center space-x-2 max-w-md animate-fade-in ${
            saveFileToastType === 'error'
              ? 'bg-rose-600'
              : saveFileToastType === 'warning'
              ? 'bg-amber-600'
              : 'bg-emerald-600'
          }`}
        >
          {saveFileToastType === 'error' || saveFileToastType === 'warning' ? (
            <AlertTriangle className="w-4 h-4 shrink-0" />
          ) : (
            <CheckCircle2 className="w-4 h-4 shrink-0" />
          )}
          <span className="leading-snug">{saveFileToast}</span>
        </div>
      )}

      {/* 右侧 Canvas 预览抽屉 */}
      <CanvasDrawer
        isOpen={canvasState.isOpen}
        onClose={handleCloseCanvas}
        title={canvasState.title}
        content={canvasState.content}
        type={canvasState.type}
      />

      {/* 右侧优化验收抽屉 */}
      <RightActionDrawer
        isOpen={isActionDrawerOpen}
        onClose={handleCloseActionDrawer}
        pendingCountChange={(count) => setPendingActionCount(count)}
      />

      {/* 全局 Ctrl + K 检索模态框 */}
      <SearchModal
        isOpen={isSearchOpen}
        onClose={handleCloseSearch}
        onSelectResult={handleSelectSearchResult}
      />

      {/* 账号池管理模态框 */}
      <AccountModal
        isOpen={isAccountOpen}
        onClose={handleCloseAccount}
        onRefresh={() => {
          fetchStatusPill();
          fetchActiveModels();
        }}
      />

      {/* 四步跨平台数据迁移与导入向导 (ChatGPT / Claude / GEMINI.md / .cursorrules) */}
      <ImportWizardModal
        isOpen={isSyncOpen}
        onClose={handleCloseSync}
        onSuccess={() => {
          fetchConversations();
        }}
      />

      {/* 设置中心模态框：包含模型自动识别与一键测试、浅色/深色主题外观、回复性格定制 */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={handleCloseSettings}
        initialTab={settingsInitialTab}
        onModelsUpdated={(state) => applyActiveModelsState(state)}
      />

      {/* 记忆中枢、多源大模型 API 与 Skill/MCP 扩展管理模态框 */}
      <MemoryHubModal
        isOpen={isMemoryHubOpen}
        onClose={handleCloseMemoryHub}
        onProvidersChanged={() => fetchActiveModels()}
        workspaceId={activeWorkspaceId}
        initialTab={memoryHubInitialTab}
        onSkillsUpdated={() => {
          prefetchWorkspaceSkills(activeWorkspaceId);
          void syncSkillCatalog(activeWorkspaceId);
        }}
      />

      {/* 快捷技能呼出面板与 SOP 明文规程抽屉 */}
      <SkillPickerModal
        workspaceId={activeWorkspaceId}
        isOpen={isSkillPickerOpen}
        onClose={handleCloseSkillPicker}
        onSelectSkill={(cmd) => handleInsertCommandAtCursor(cmd)}
        onViewSop={(sk) => setSelectedSopSkill(sk)}
      />
      <SopDrawer
        skill={selectedSopSkill}
        workspaceId={activeWorkspaceId}
        onClose={handleCloseSopDrawer}
      />

      {/* 会话级 Token 消耗、KV Cache 命中率与本地算力吞吐看板弹窗 */}
      <TokenStatsModal
        isOpen={isTokenStatsModalOpen}
        onClose={() => setIsTokenStatsModalOpen(false)}
        conversationId={activeConvId}
        liveTurnMetrics={messages
          .filter((m) => m.role === 'model' && m.token_stats)
          .map((m) => m.token_stats as TurnTokenMetrics)}
      />
    </div>
  );
}
