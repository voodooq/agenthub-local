'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Send,
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
import { ExperienceProposalCard } from '@/components/ExperienceProposalCard';

import { GeneratedFileChip } from '@/components/GeneratedFileChip';
import { buildActiveSystemOverride } from '@/utils/personalityHelper';
import { generateScenarioThinking } from '@/utils/scenarioThinking';
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
  ExperienceProposalItem,
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
import { prefetchExtensionSkills, prefetchWorkspaceSkills } from '@/utils/skillCache';
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
  const [isStreaming, setIsStreaming] = useState(false);
  const isStreamingRef = useRef(false);
  const [statusPill, setStatusPill] = useState<StatusPillData | null>(null);

  // 当前选中模型及可用模型列表
  const [currentModel, setCurrentModel] = useState<string>('gemini-4-argon');
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
  const [workMode, setWorkMode] = useState<WorkMode>('dev');
  const [isHudOpen, setIsHudOpen] = useState(false);
  const [isActionDrawerOpen, setIsActionDrawerOpen] = useState(false);
  const [pendingActionCount, setPendingActionCount] = useState(0);
  const [workspacePath, setWorkspacePath] = useState<string>('AgentHub Workspace');
  const [cpuPercent, setCpuPercent] = useState(18);
  const [memPercent, setMemPercent] = useState(42);

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

  // 三层记忆与微内核 Agent 状态
  const [sceneMode, setSceneMode] = useState<SceneMode>('agent');
  const [incognito, setIncognito] = useState<boolean>(false);
  const [workingMemory, setWorkingMemory] = useState<Record<string, any>>({});

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
  const [pendingProposals, setPendingProposals] = useState<ExperienceProposalItem[]>([]);

  const upsertPendingProposal = useCallback((proposal: ExperienceProposalItem) => {
    if (!proposal || proposal.status !== 'pending') return;
    setPendingProposals((prev) => {
      const idx = prev.findIndex((p) => p.id === proposal.id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = proposal;
        return next;
      }
      return [proposal, ...prev];
    });
  }, []);

  const fetchPendingProposals = useCallback(async (targetConvId?: string) => {
    const cid = targetConvId || activeConvId;
    if (!cid) {
      setPendingProposals([]);
      return;
    }
    try {
      const res = await apiFetch(
        `/api/v1/experiences/proposals?conversation_id=${encodeURIComponent(cid)}&status=pending`
      );
      if (res.ok) {
        const data = await res.json();
        setPendingProposals(Array.isArray(data.proposals) ? data.proposals : []);
      }
    } catch (e) { }
  }, [activeConvId]);

  const [canvasState, setCanvasState] = useState<{
    isOpen: boolean;
    title: string;
    content: string;
    type: 'html' | 'mermaid' | 'code' | 'image';
  }>({ isOpen: false, title: '', content: '', type: 'code' });

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pendingJumpTargetRef = useRef<{ convId: string; messageId?: number } | null>(null);
  const [highlightedMessageId, setHighlightedMessageId] = useState<number | null>(null);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    return () => {
      if (highlightTimerRef.current) {
        clearTimeout(highlightTimerRef.current);
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
    scrollToBottom();
  }, [messages]);

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

  useEffect(() => {
    const fetchQuickMetrics = async () => {
      try {
        const res = await apiFetch('/api/v1/system/snapshot');
        if (res.ok) {
          const json = await res.json();
          setCpuPercent(json?.cpu?.usage_percent || 18);
          setMemPercent(json?.memory?.percent || 42);
        }
      } catch (e) { }
    };
    fetchQuickMetrics();
    const timer = setInterval(fetchQuickMetrics, 5000);
    return () => clearInterval(timer);
  }, []);

  const fetchActiveLoop = useCallback(async () => {
    if (!activeWorkspaceId) return;
    try {
      const res = await apiFetch(`/api/v1/loop/workspace/${activeWorkspaceId}/active`);
      if (res.ok) {
        const data = await res.json();
        setActiveLoop(data);
      } else {
        setActiveLoop(null);
      }
    } catch (e) { }
  }, [activeWorkspaceId]);

  useEffect(() => {
    fetchActiveLoop();
    const timer = setInterval(fetchActiveLoop, 4000);
    return () => clearInterval(timer);
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
        const stillValid = nextModels.some((m) => m.id === prev);
        // 若存在明确标记为默认就绪的模型（如已配置就绪的本地/自定义模型），且用户尚未手动选择过模型或原模型已失效，自动切换为默认模型
        if (
          defaultModel &&
          (!stillValid || (!hasUserSelectedModelRef.current && (prev === 'gemini-4-argon' || prev === 'gemini-2.5-pro')))
        ) {
          return defaultModel.id;
        }
        return stillValid ? prev : nextModels[0].id;
      });
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
        setStatusPill(data);
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
    const pillTimer = setInterval(fetchStatusPill, 3000);
    return () => clearInterval(pillTimer);
  }, []);

  useEffect(() => {
    prefetchWorkspaceSkills(activeWorkspaceId);
  }, [activeWorkspaceId]);

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
          } else {
            // 自动清洗历史残存的 [tool_call] 原始标签、孤立的 ```xml / xml 行与内部落盘标记
            const strippedContent = (m.content || '')
              .replace(/[<\[]tool_call(?:\s+name=["']?[^"'\]>]+["']?)?\s*[>\]][\s\S]*?[<\[]\/tool_call[>\]]/gi, '')
              .replace(/<\/?tool_call\b[^>]*>/gi, '')
              .replace(/\[\/?tool_call\b[^\]]*\]/gi, '')
              .replace(/\[调用工具:\s*[^\]]+\]/gi, '')
              .replace(/```(?:xml|json|tool_call)?\s*```/gi, '')
              .replace(/^\s*```xml\s*$/gim, '')
              .replace(/^\s*xml\s*$/gim, '')
              .trim();
            const cleanContent =
              m.role !== 'user' &&
              (strippedContent.toLowerCase().includes('interactive_inquiry') ||
                strippedContent.includes('"inquiry_id"'))
                ? normalizeInteractiveInquiryMarkdown(strippedContent)
                : strippedContent;
            merged.push({ ...m, content: cleanContent });
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
      setPendingProposals([]);
      return;
    }

    // 关键防冲刷保护：若当前正在流式接收新会话的消息，绝不能拉取未写完的后端空记录覆盖当前正在渲染的内容！
    if (isStreamingRef.current) {
      return;
    }

    fetchMessagesAndState();
    fetchPendingProposals(activeConvId);
  }, [activeConvId, fetchMessagesAndState, fetchPendingProposals]);

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
        setActiveConvId(result.conversation_id);
      }
    },
    [activeWorkspaceId, activeConvId, fetchMessagesAndState, messages]
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

    let promptToSend = rawPrompt;
    let effectiveConvId = activeConvId;
    if (!customPrompt) {
      setInputPrompt('');
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
      executed_by_account: statusPill?.active_account_name,
    };

    setMessages((prev) => [...prev, tempUserMsg, tempAssistantMsg]);

    const hasPriorUnlockInView = messages.some(
      (m) =>
        (m.role === 'model' && Array.isArray(m.tool_events) && m.tool_events.length > 0) ||
        (m.role === 'user' &&
          (m.content.includes('实施方案已通过用户审批') ||
            m.content.includes('全部确认完毕')))
    );
    const shouldStartLoopHud =
      sceneMode === 'agent' &&
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

    try {
      // 读取用户配置的多轮对话记忆上下文轮数
      let memoryRounds = 10;
      try {
        const savedRounds = localStorage.getItem('agenthub_context_rounds') || localStorage.getItem('geminihub_context_rounds');
        if (savedRounds !== null) memoryRounds = parseInt(savedRounds, 10);
      } catch (e) { }

      const response = await apiFetch('/api/v1/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          conversation_id: activeConvId,
          workspace_id: activeWorkspaceId,
          message: promptToSend,
          model_name: currentModel,
          thinking_level: thinkingLevel,
          system_override: await buildActiveSystemOverride(),
          context_rounds: memoryRounds,
          scene_mode: sceneMode,
          incognito: incognito,
          execution_strategy: sceneMode === 'agent' ? executionStrategy : 'direct',
          clarify_mode: sceneMode === 'chat' ? clarifyMode : false,
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

      if (!response.body) throw new Error('ReadableStream 不可用');

      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let partialText = '';
      let isRelayed = false;
      let executorAccount = statusPill?.active_account_name || 'AgentHub';

      const stripXmlToolTags = (txt: string) =>
        txt
          .replace(/[<\[]tool_call(?:\s+name=["']?[^"'\]>]+["']?)?\s*[>\]][\s\S]*?[<\[]\/tool_call[>\]]/gi, '')
          .replace(/<(?:execute_local_command|write_local_file|read_local_file|web_fetch|ui_action|update_working_memory|load_skill_detail)\b[^>]*?(?:\/>|>[\s\S]*?<\/(?:execute_local_command|write_local_file|read_local_file|web_fetch|ui_action|update_working_memory|load_skill_detail)>)/gi, '')
          .replace(/<(?:execute_local_command|write_local_file|read_local_file|web_fetch|ui_action|update_working_memory|load_skill_detail)\b[^>]*$/gi, '')
          .replace(/<\/?tool_call\b[^>]*>/gi, '')
          .replace(/\[\/?tool_call\b[^\]]*\]/gi, '')
          .replace(/\[调用工具:\s*[^\]]+\]/gi, '')
          .replace(/```(?:xml|json|tool_call)?\s*```/gi, '')
          .replace(/^\s*```xml\s*$/gim, '')
          .replace(/^\s*xml\s*$/gim, '')
          .trim();

      let sseBuffer = '';

      const handleSseLine = (line: string) => {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) return;
        const rawData = trimmed.slice(6).trim();
        if (!rawData) return;

        try {
          const parsed = JSON.parse(rawData);
          if (parsed.type === 'conv_init') {
            // 关键：收到会话初始化事件，立即绑定 conversation_id，防止后续生成重复会话并保证继承上下文记忆
            if (parsed.conversation_id) {
              effectiveConvId = parsed.conversation_id;
              setActiveConvId(parsed.conversation_id);
              fetchConversations();
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
                last.fact_refs = incomingFacts;
              }
              return updated;
            });
          } else if (parsed.type === 'memory_refs') {
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                last.memory_refs = parsed.memories || [];
              }
              return updated;
            });
          } else if (parsed.type === 'scratchpad_update') {
            setWorkingMemory(parsed.working_memory || {});
          } else if (parsed.type === 'loop_step') {
            setActiveLoop((prev) => {
              const nextStatus = parsed.status ?? prev?.loop_status ?? 'executing';
              const nextPendingApproval =
                'pending_approval_call' in parsed
                  ? parsed.pending_approval_call ?? null
                  : nextStatus !== 'waiting_approval'
                    ? null
                    : prev?.pending_approval_call ?? null;
              if (!prev) {
                return {
                  id: parsed.loop_id,
                  workspace_id: activeWorkspaceId || 'default',
                  conversation_id: effectiveConvId || activeConvId || 'default',
                  goal_prompt: rawPrompt,
                  scratchpad_md: parsed.scratchpad_md,
                  loop_status: nextStatus,
                  current_iteration: parsed.iteration || 0,
                  max_iterations: parsed.max_iterations || 10,
                  pending_approval_call: nextPendingApproval,
                  error_fingerprints: [],
                  created_at: new Date().toISOString(),
                  updated_at: new Date().toISOString(),
                };
              }
              return {
                ...prev,
                id: parsed.loop_id ?? prev.id,
                current_iteration: parsed.iteration ?? prev.current_iteration,
                loop_status: nextStatus,
                pending_approval_call: nextPendingApproval,
                scratchpad_md: parsed.scratchpad_md ?? prev.scratchpad_md,
              };
            });
          } else if (parsed.type === 'loop_completed') {
            setActiveLoop((prev) => {
              if (!prev) return null;
              return {
                ...prev,
                loop_status: 'completed',
                current_iteration: parsed.iteration ?? prev.current_iteration,
                scratchpad_md: parsed.scratchpad_md ?? prev.scratchpad_md,
              };
            });
          } else if (parsed.type === 'loop_failed') {
            setActiveLoop((prev) => {
              if (!prev) return null;
              return {
                ...prev,
                loop_status: 'failed',
              };
            });
          } else if (parsed.type === 'experience_proposal' && parsed.proposal) {
            upsertPendingProposal(parsed.proposal as ExperienceProposalItem);
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
                last.tool_events = existingEvents;
                last.content = stripXmlToolTags(partialText);
              }
              return updated;
            });
            // 收到需要审批时立即自动平滑滚动到底部聚焦卡片
            if (parsed.type === 'require_approval') {
              setTimeout(scrollToBottom, 50);
            }
          } else if (parsed.type === 'chunk') {
            partialText += parsed.content;
            executorAccount = parsed.account || executorAccount;
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                last.content = stripXmlToolTags(partialText);
                last.executed_by_account = executorAccount;
                last.is_relayed = isRelayed;
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
                last.downgrade_info = {
                  original_model: origModel,
                  current_model: targetModel,
                  reason: parsed.reason,
                  message: downgradeMsg,
                };
                last.model_name = targetModel;
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
                last.step_extend_info = {
                  step: parsed.step,
                  max_steps: parsed.max_steps,
                  message: extMsg,
                };
              }
              return updated;
            });
          } else if (parsed.type === 'stream_reset') {
            partialText = '';
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                last.content = '';
              }
              return updated;
            });
          } else if (parsed.type === 'done') {
            if (parsed.full_content) {
              partialText = parsed.full_content;
            }
            if (parsed.model) {
              setCurrentModel(parsed.model);
            }
            const strippedDoneText = stripXmlToolTags(partialText);
            const finalDoneText =
              strippedDoneText.toLowerCase().includes('interactive_inquiry') ||
              strippedDoneText.includes('"inquiry_id"')
                ? normalizeInteractiveInquiryMarkdown(strippedDoneText)
                : strippedDoneText;
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                last.content = finalDoneText;
                if (parsed.model) {
                  last.model_name = parsed.model;
                }
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
            partialText += '\n\n' + parsed.content;
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                last.content = partialText;
              }
              return updated;
            });
          }
        } catch (err) {
          console.warn('SSE 解析异常:', err, rawData);
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

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

      fetchConversations();
      fetchStatusPill();
    } catch (err) {
      console.error(err);
    } finally {
      setIsStreaming(false);
      isStreamingRef.current = false;
      // 关键闭环：流式结束后立即精准同步一次后端最新全量消息（包括多步工具卡片与最终回复）
      // 彻底根治新会话首轮问答因 activeConvId 转换未触发拉取而需要手动切换会话的体验问题
      if (effectiveConvId) {
        await fetchMessagesAndState(effectiveConvId);
        await fetchPendingProposals(effectiveConvId);
      }
      setTimeout(scrollToBottom, 60);
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
      void handleSendMessage(feedback, undefined, confirmedFacts, undefined, {
        inquiryConfirmed: true,
        inquiryAction: inquiryAction || 'execute_now',
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
      void handleSendMessage(feedback, undefined, confirmedFacts, undefined, {
        inquiryConfirmed: true,
        inquiryAction: inquiryAction || 'execute_now',
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
          lastStreamFactRefsRef.current = null;
          setPendingProposals([]);
          setActiveWorkspaceId(wsId);
          setActiveConvId(null);
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
        onSelectConv={(id) => setActiveConvId(id)}
        onNewChat={() => {
          lastStreamFactRefsRef.current = null;
          setPendingProposals([]);
          setActiveConvId(null);
          setMessages([]);
        }}
        onDeleteConv={async (id) => {
          await apiFetch('/api/v1/conversations/' + id, { method: 'DELETE' });
          fetchConversations();
          if (activeConvId === id) {
            setActiveConvId(null);
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
            {/* 顶部长程任务领航舱 (Loop HUD) 与敏感操作审批拦截卡片 */}
            <div className="px-4 md:px-12 lg:px-24 pt-3">
              <LoopHUD
                loop={activeLoop}
                onRefresh={fetchActiveLoop}
                onProposalCreated={upsertPendingProposal}
              />
              {activeLoop &&
                activeLoop.loop_status === 'waiting_approval' &&
                activeLoop.pending_approval_call && (
                  <ApprovalCard
                    loopId={activeLoop.id}
                    approvalCall={activeLoop.pending_approval_call}
                    onResolved={fetchActiveLoop}
                  />
                )}
            </div>

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
            <div className="flex-1 overflow-y-auto px-4 md:px-12 lg:px-24 py-6 space-y-6">
              {messages.length === 0 ? (
                <PromptStarterCards onSelectPrompt={(text) => handleSendMessage(text)} />
              ) : (
                messages.map((msg, index) => {
                  const isUser = msg.role === 'user';
                  const isSearchHighlighted = Boolean(msg.id && highlightedMessageId === msg.id);
                  return (
                    <div
                      key={msg.id || index}
                      id={msg.id ? `chat-msg-${msg.id}` : undefined}
                      className={`flex flex-col space-y-2.5 group transition-all duration-500 ${isUser ? 'items-end' : 'items-start'} ${
                        isSearchHighlighted
                          ? 'ring-2 ring-emerald-500/70 bg-emerald-500/5 rounded-2xl p-2 -m-2'
                          : ''
                      }`}
                    >
                      {/* 发言角色标志 */}
                      <div className="flex items-center space-x-2 px-1 text-xs text-muted-foreground">
                        {isUser ? (
                          <span className="font-semibold text-foreground">您</span>
                        ) : (
                          <div className="flex items-center space-x-2">
                            <Sparkles className="w-3.5 h-3.5 text-emerald-500" />
                            {(() => {
                              const rawModel = msg.downgrade_info?.current_model || msg.model_name || currentModel || '';
                              const isGemini = rawModel.toLowerCase().includes('gemini');
                              const modelDisplayName = isGemini ? `Gemini (${rawModel})` : (rawModel || 'AI Assistant');
                              return (
                                <span className="font-bold text-foreground">
                                  {modelDisplayName}
                                </span>
                              );
                            })()}
                            {msg.downgrade_info && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 font-medium flex items-center space-x-1">
                                <AlertTriangle className="w-2.5 h-2.5 text-amber-500" />
                                <span>已自动降级</span>
                              </span>
                            )}
                            {msg.executed_by_account && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-200 border border-border font-mono">
                                调度: {msg.executed_by_account}
                              </span>
                            )}
                          </div>
                        )}
                      </div>

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
                        <ThinkingBlock content={msg.thinking} isStreaming={isStreaming && index === messages.length - 1} />
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

                      {/* 消息正文主体：大字体、高清晰度、长 Token 强制自然折行（过滤纯工具执行时的无内容空白卡片） */}
                      {(isUser || msg.content || (isStreaming && index === messages.length - 1 && (!msg.tool_events || msg.tool_events.length === 0))) && (
                        <div
                          className={`max-w-[88%] md:max-w-[82%] rounded-2xl px-5 py-3.5 text-[14px] leading-[1.68] break-anywhere shadow-sm ${isUser
                              ? 'bg-emerald-100 dark:bg-emerald-950/70 text-emerald-950 dark:text-emerald-100 border border-emerald-300/60 dark:border-emerald-500/30 rounded-tr-none shadow-emerald-500/5'
                              : 'bg-surface-card text-foreground rounded-tl-none border border-border/80'
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
                              const isCurrentStreamingMsg =
                                isStreaming && msg.id === messages[messages.length - 1]?.id;
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
                              return (
                                <AssistantMarkdownMessage
                                  content={msg.content || ''}
                                  isStreaming={isCurrentStreamingMsg}
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
                            })()
                          )}
                        </div>
                      )}

                      {/* 生成文件快捷直达栏（点击默认程序直接打开 / 预览 / 所在目录定位；等待交互确认卡消息不提前显示交付栏） */}
                      {!isUser &&
                        msg.content &&
                        !(isStreaming && msg.id === messages[messages.length - 1]?.id) &&
                        !hasInteractiveInquiryBlock(msg.content) &&
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

                      {/* AI 消息交互底栏 */}
                      {!isUser && msg.content && (
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
                      )}
                    </div>
                  );
                })
              )}
              {pendingProposals.length > 0 && (
                <div className="space-y-3 pt-2">
                  {pendingProposals.map((prop) => (
                    <ExperienceProposalCard
                      key={prop.id}
                      proposal={prop}
                      onResolved={(proposalId, action, updated) => {
                        setPendingProposals((prev) => prev.filter((item) => item.id !== proposalId));
                        if (action === 'applied') {
                          const skillTag = updated?.proposed_skill_name
                            ? `/${updated.proposed_skill_name}`
                            : 'Skill 知识库';
                          showFloatingToast(`✨ 经验已确认落盘并热加载至 ${skillTag}`, 'success', 4000);
                        }
                      }}
                    />
                  ))}
                </div>
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
                  onSelect={(text) => {
                    setInputPrompt(text);
                    textareaRef.current?.focus();
                  }}
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
                  <textarea
                    ref={textareaRef}
                    value={inputPrompt}
                    onChange={(e) => {
                      const val = e.target.value;
                      setInputPrompt(val);
                      if (val === '/') {
                        setIsSkillPickerOpen(true);
                      }
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
                    rows={1}
                    placeholder="输入消息或指令...（支持拖拽放置文件、Ctrl+V 粘贴图片，Enter 发送，/ 唤起技能）"
                    className="w-full bg-transparent border-none outline-none text-[14px] text-foreground placeholder-muted-foreground px-3 py-1.5 resize-none leading-relaxed break-anywhere"
                  />

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
                      />

                      {/* 发送按钮 */}
                      <button
                        type="button"
                        onClick={() => handleSendMessage()}
                        disabled={isStreaming || isUploadingAttachment || (!inputPrompt.trim() && attachments.length === 0)}
                        className="p-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white transition-all disabled:opacity-30 disabled:cursor-not-allowed shadow-sm shadow-emerald-600/20"
                        title="发送提问"
                      >
                        {isStreaming ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                      </button>
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
        onSkillsUpdated={() => prefetchWorkspaceSkills(activeWorkspaceId)}
      />

      {/* 快捷技能呼出面板与 SOP 明文规程抽屉 */}
      <SkillPickerModal
        workspaceId={activeWorkspaceId}
        isOpen={isSkillPickerOpen}
        onClose={handleCloseSkillPicker}
        onSelectSkill={(cmd) => {
          setInputPrompt(cmd);
          textareaRef.current?.focus();
        }}
        onViewSop={(sk) => setSelectedSopSkill(sk)}
      />
      <SopDrawer
        skill={selectedSopSkill}
        workspaceId={activeWorkspaceId}
        onClose={handleCloseSopDrawer}
      />
    </div>
  );
}
