'use client';

import React, { useState, useEffect, useRef } from 'react';
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
} from 'lucide-react';

import { Sidebar } from '@/components/Sidebar';
import { StatusPill } from '@/components/StatusPill';
import { ModelSelector, ThinkingLevel, ModelOption } from '@/components/ModelSelector';
import { ThinkingBlock } from '@/components/ThinkingBlock';
import { MessageActions } from '@/components/MessageActions';
import { PromptStarterCards } from '@/components/PromptStarterCards';
import { CodeBlock } from '@/components/CodeBlock';
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
import { MemoryHubModal } from '@/components/MemoryHubModal';
import { InlineToolCard } from '@/components/InlineToolCard';
import { ImportWizardModal } from '@/components/ImportWizardModal';

import { buildActiveSystemOverride } from '@/utils/personalityHelper';
import { generateScenarioThinking } from '@/utils/scenarioThinking';
import { Conversation, Message, StatusPillData, SceneMode, ToolExecutionCardItem } from '@/types';
import { apiFetch } from '@/utils/apiClient';

export default function WorkbenchPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputPrompt, setInputPrompt] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const isStreamingRef = useRef(false);
  const [statusPill, setStatusPill] = useState<StatusPillData | null>(null);

  // 当前选中模型及可用模型列表
  const [currentModel, setCurrentModel] = useState<string>('gemini-4-argon');
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
  const [workMode, setWorkMode] = useState<WorkMode>('dev');
  const [isHudOpen, setIsHudOpen] = useState(false);
  const [isActionDrawerOpen, setIsActionDrawerOpen] = useState(false);
  const [pendingActionCount, setPendingActionCount] = useState(0);
  const [workspacePath, setWorkspacePath] = useState<string>('GeminiHub Workspace');
  const [cpuPercent, setCpuPercent] = useState(18);
  const [memPercent, setMemPercent] = useState(42);

  // 附件与语音
  interface AttachmentItem {
    name: string;
    size: string;
    type: string; // 'image' | 'text' | 'file'
    dataUrl?: string;
    filePath?: string;
    url?: string;
    mimeType?: string;
    textContent?: string;
  }
  const [attachments, setAttachments] = useState<AttachmentItem[]>([]);
  const [isListening, setIsListening] = useState(false);

  // 抽屉与模态框
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [isAccountOpen, setIsAccountOpen] = useState(false);
  const [isSyncOpen, setIsSyncOpen] = useState(false);
  const [isMemoryHubOpen, setIsMemoryHubOpen] = useState(false);

  // 三层记忆与微内核 Agent 状态
  const [sceneMode, setSceneMode] = useState<SceneMode>('agent');
  const [incognito, setIncognito] = useState<boolean>(false);
  const [workingMemory, setWorkingMemory] = useState<Record<string, any>>({});

  const [canvasState, setCanvasState] = useState<{
    isOpen: boolean;
    title: string;
    content: string;
    type: 'html' | 'mermaid' | 'code' | 'image';
  }>({ isOpen: false, title: '', content: '', type: 'code' });

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
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

  // 全局 F11 全屏切换快捷键支持
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'F11') {
        e.preventDefault();
        if (!document.fullscreenElement) {
          document.documentElement.requestFullscreen().catch(() => {});
        } else {
          document.exitFullscreen().catch(() => {});
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
      } catch (e) {}
    };
    fetchQuickMetrics();
    const timer = setInterval(fetchQuickMetrics, 5000);
    return () => clearInterval(timer);
  }, []);

  const fetchConversations = async () => {
    try {
      const res = await apiFetch('/api/v1/conversations');
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
        const stillValid = nextModels.some((m) => m.id === prev);
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
    fetchConversations();
    fetchStatusPill();
    fetchActiveModels();
    const pillTimer = setInterval(fetchStatusPill, 3000);
    return () => clearInterval(pillTimer);
  }, []);

  useEffect(() => {
    if (statusPill?.active_account_name) {
      fetchActiveModels();
    }
  }, [statusPill?.active_account_name, statusPill?.auth_type]);

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

    const fetchMessagesAndState = async () => {
      try {
        const [msgRes, detailRes] = await Promise.all([
          apiFetch(`/api/v1/conversations/${activeConvId}/messages`),
          apiFetch(`/api/v1/conversations/${activeConvId}`),
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
              }
            } else {
              merged.push(m);
            }
          }
          setMessages(merged);
        }
      } catch (e) {
        console.warn('获取消息失败:', e);
      }
    };

    fetchMessagesAndState();
  }, [activeConvId]);

  const handleSwitchSceneMode = async (nextMode: SceneMode) => {
    setSceneMode(nextMode);
    if (activeConvId) {
      try {
        await apiFetch(`/api/v1/conversations/${activeConvId}/agent-state`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scene_mode: nextMode }),
        });
      } catch (e) {}
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
      } catch (e) {}
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
      } catch (e) {}
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
    } catch (e) {}
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
    } catch (e) {}
  };

  const handleSendMessage = async (customPrompt?: string) => {
    const rawPrompt = customPrompt || inputPrompt;
    if (!rawPrompt.trim() || isStreaming) return;

    let promptToSend = rawPrompt;
    setInputPrompt('');

    const currentAttachments = [...attachments];
    setAttachments([]);

    // 将用户消息中附带的图片转换为优雅的 Markdown 预览，供用户气泡直观展示
    let userDisplayContent = rawPrompt;
    if (currentAttachments.length > 0) {
      const imgPreviews = currentAttachments
        .filter((att) => att.type === 'image' && (att.url || att.dataUrl))
        .map((att) => `\n\n![${att.name}](${att.url || att.dataUrl})`)
        .join('');
      userDisplayContent = `${rawPrompt}${imgPreviews}`;
    }

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
      } catch (e) {}
    }

    if (promptToSend.includes('@hardware') && !promptToSend.includes('<system_telemetry_snapshot>')) {
      try {
        const snapRes = await apiFetch('/api/v1/system/snapshot');
        if (snapRes.ok) {
          const snapJson = await snapRes.json();
          promptToSend += `\n\n<system_telemetry_snapshot>\n${JSON.stringify(snapJson, null, 2)}\n</system_telemetry_snapshot>`;
        }
      } catch (e) {}
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

    try {
      // 读取用户配置的多轮对话记忆上下文轮数
      let memoryRounds = 10;
      try {
        const savedRounds = localStorage.getItem('geminihub_context_rounds');
        if (savedRounds !== null) memoryRounds = parseInt(savedRounds, 10);
      } catch (e) {}

      const response = await apiFetch('/api/v1/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          conversation_id: activeConvId,
          message: promptToSend,
          model_name: currentModel,
          thinking_level: thinkingLevel,
          system_override: await buildActiveSystemOverride(),
          context_rounds: memoryRounds,
          scene_mode: sceneMode,
          incognito: incognito,
          attachments: currentAttachments.map((a) => ({
            name: a.name,
            file_path: a.filePath,
            url: a.url,
            mime_type: a.mimeType || 'image/png',
            size_str: a.size,
          })),
        }),
      });

      if (!response.body) throw new Error('ReadableStream 不可用');

      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let partialText = '';
      let isRelayed = false;
      let executorAccount = statusPill?.active_account_name || 'Gemini';

      const stripXmlToolTags = (txt: string) =>
        txt
          .replace(/<tool_call\s+name=["'][^"']+["']\s*>[\s\S]*?<\/tool_call>/gi, '')
          .replace(/```xml\s*```/gi, '')
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
              setActiveConvId(parsed.conversation_id);
              fetchConversations();
            }
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
          } else if (parsed.type === 'done') {
            if (parsed.full_content) {
              partialText = parsed.full_content;
            }
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === 'model') {
                last.content = stripXmlToolTags(partialText);
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
    }
  };

  const handleAddFileAttachment = async (file: File) => {
    const isImage = file.type.startsWith('image/');
    const isText = file.type.startsWith('text/') || /\.(txt|md|js|ts|tsx|jsx|json|py|html|css|yaml|yml|sql|csv)$/i.test(file.name);

    // 立即上传至后端本地存储，获取真实物理路径与静态 URL
    let uploadedFilePath = '';
    let uploadedUrl = '';
    let uploadedSizeStr = (file.size / 1024).toFixed(1) + ' KB';

    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await apiFetch('/api/v1/chat/upload', {
        method: 'POST',
        body: formData,
      });
      if (res.ok) {
        const json = await res.json();
        uploadedFilePath = json.file_path;
        uploadedUrl = json.url;
        uploadedSizeStr = json.size_str || uploadedSizeStr;
      }
    } catch (e) {
      console.warn('本地附件持久化失败，将使用内联兜底:', e);
    }

    if (isImage) {
      const reader = new FileReader();
      reader.onload = (e) => {
        setAttachments((prev) => [
          ...prev,
          {
            name: file.name,
            size: uploadedSizeStr,
            type: 'image',
            dataUrl: e.target?.result as string,
            filePath: uploadedFilePath,
            url: uploadedUrl,
            mimeType: file.type || 'image/png',
          },
        ]);
      };
      reader.readAsDataURL(file);
    } else if (isText) {
      const reader = new FileReader();
      reader.onload = (e) => {
        setAttachments((prev) => [
          ...prev,
          {
            name: file.name,
            size: uploadedSizeStr,
            type: 'text',
            textContent: e.target?.result as string,
            filePath: uploadedFilePath,
            url: uploadedUrl,
            mimeType: file.type || 'text/plain',
          },
        ]);
      };
      reader.readAsText(file);
    } else {
      setAttachments((prev) => [
        ...prev,
        {
          name: file.name,
          size: uploadedSizeStr,
          type: 'file',
          filePath: uploadedFilePath,
          url: uploadedUrl,
          mimeType: file.type || 'application/octet-stream',
        },
      ]);
    }
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files) {
      Array.from(files).forEach((f) => handleAddFileAttachment(f));
    }
    e.target.value = '';
  };

  // 支持从微信、截图工具或文件管理器直接复制后粘贴 (Ctrl + V)
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    let hasFile = false;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) {
          hasFile = true;
          // 若复制的是微信截图/无名图片，自动赋予易识别的名字
          const fileName = file.name === 'image.png' || !file.name
            ? `微信截图_${new Date().toLocaleTimeString().replace(/:/g, '')}.png`
            : file.name;
          const renamedFile = new File([file], fileName, { type: file.type });
          handleAddFileAttachment(renamedFile);
        }
      }
    }

    // 若粘贴的内容包含文件或图片，无需阻止普通文本粘贴（允许图文混排）
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

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground transition-colors duration-200">
      {/* 侧边栏 */}
      <Sidebar
        conversations={conversations}
        activeConvId={activeConvId}
        onSelectConv={(id) => setActiveConvId(id)}
        onNewChat={() => {
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

      {/* 主对话区 */}
      <main className="flex-1 flex flex-col h-full overflow-hidden relative">
        {/* 顶部极简状态栏 */}
        <header className="h-13 py-2 border-b border-border/80 px-4 md:px-6 flex items-center justify-between bg-surface-100/50 backdrop-blur-md z-10 select-none">
          <div className="flex items-center space-x-3">
            <StatusPill data={statusPill} onOpenAccounts={() => setIsAccountOpen(true)} />
          </div>

          <div className="flex items-center space-x-1.5">
            {/* 记忆中枢与扩展插件入口 */}
            <button
              onClick={() => setIsMemoryHubOpen(true)}
              className="flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs text-muted-foreground hover:text-foreground hover:bg-surface-200/70 transition-all"
              title="打开长期记忆画像与 Skill/MCP 扩展中枢"
            >
              <Brain className="w-3.5 h-3.5 text-blue-500" />
              <span className="text-[12px]">记忆中枢</span>
            </button>

            {/* Canvas 画布极简入口 */}
            <button
              onClick={() => setCanvasState((prev) => ({ ...prev, isOpen: !prev.isOpen }))}
              className={`flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs transition-all ${
                canvasState.isOpen
                  ? 'bg-blue-500/15 text-blue-600 dark:text-blue-400 font-medium'
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

            {/* 界面浅色/深色/跟随系统快速切换器 */}
            <ThemeToggle />
          </div>
        </header>

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
              return (
                <div key={msg.id || index} className={`flex flex-col space-y-2.5 group ${isUser ? 'items-end' : 'items-start'}`}>
                  {/* 发言角色标志 */}
                  <div className="flex items-center space-x-2 px-1 text-xs text-muted-foreground">
                    {isUser ? (
                      <span className="font-semibold text-foreground">您</span>
                    ) : (
                      <div className="flex items-center space-x-2">
                        <Sparkles className="w-3.5 h-3.5 text-blue-500" />
                        <span className="font-bold text-foreground">
                          Gemini ({msg.model_name || currentModel})
                        </span>
                        {msg.executed_by_account && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-200 border border-border font-mono">
                            调度: {msg.executed_by_account}
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* 思考过程展开收起卡片 */}
                  {!isUser && msg.thinking && (
                    <ThinkingBlock content={msg.thinking} isStreaming={isStreaming && index === messages.length - 1} />
                  )}

                  {/* 长期记忆引用溯源微提示 */}
                  {!isUser && msg.memory_refs && msg.memory_refs.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setIsMemoryHubOpen(true)}
                      className="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg bg-blue-500/10 hover:bg-blue-500/15 border border-blue-500/20 text-[11px] text-blue-600 dark:text-blue-400 transition-colors"
                      title={msg.memory_refs.map((m) => `• ${m.content}`).join('\n')}
                    >
                      <Brain className="w-3 h-3" />
                      <span>已引用 {msg.memory_refs.length} 条长期个人偏好（点击查看或管理）</span>
                    </button>
                  )}

                  {/* 消息正文主体：大字体、高清晰度、长 Token 强制自然折行（过滤纯工具执行时的无内容空白卡片） */}
                  {(isUser || msg.content || (isStreaming && index === messages.length - 1 && (!msg.tool_events || msg.tool_events.length === 0))) && (
                    <div
                      className={`max-w-[88%] md:max-w-[82%] rounded-2xl px-5 py-3.5 text-[14px] leading-[1.68] break-anywhere shadow-sm ${
                        isUser
                          ? 'bg-blue-600 text-white rounded-tr-none shadow-blue-500/10'
                          : 'bg-surface-card text-foreground rounded-tl-none border border-border'
                      }`}
                    >
                      {isUser ? (
                        <div className="text-white break-anywhere">
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
                                      className="rounded-xl border border-white/20 max-w-full h-auto object-cover max-h-[360px] cursor-pointer shadow-md hover:opacity-95 transition-opacity"
                                      onClick={() => handleOpenCanvas(src, 'image', alt ? `附带图片: ${alt}` : '上传图片原图预览')}
                                      {...props}
                                    />
                                    {alt && (
                                      <span className="block text-[11px] text-blue-100 mt-1 opacity-80">
                                        {alt}
                                      </span>
                                    )}
                                  </span>
                                );
                              },
                            }}
                          >
                            {msg.content}
                          </ReactMarkdown>
                        </div>
                      ) : (
                        <div className="prose dark:prose-invert max-w-none text-foreground break-anywhere">
                          {msg.content ? (
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              components={{
                                code({ node, inline, className, children, ...props }: any) {
                                  const match = /language-(\w+)/.exec(className || '');
                                  const language = match ? match[1] : '';
                                  const codeString = String(children).replace(/\n$/, '');

                                  if (!inline && language) {
                                    return (
                                      <CodeBlock
                                        code={codeString}
                                        language={language}
                                        onSendToCanvas={(c: string, l: string) => handleOpenCanvas(c, l)}
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
                                },
                                img({ node, src, alt, ...props }: any) {
                                  if (!src) return null;
                                  return (
                                    <span className="block my-3">
                                      <img
                                        src={src}
                                        alt={alt || 'AI 生成插画'}
                                        className="rounded-xl border border-border shadow-lg max-w-full h-auto object-cover max-h-[512px] hover:scale-[1.01] transition-transform cursor-pointer"
                                        onClick={() => handleOpenCanvas(src, 'image', alt ? `画作预览: ${alt}` : '生成的 AI 画作原图预览')}
                                        {...props}
                                      />
                                      {alt && (
                                        <span className="block text-xs text-muted-foreground mt-1 italic text-center">
                                          {alt}（点击可在右侧画布全屏查看大图）
                                        </span>
                                      )}
                                    </span>
                                  );
                                },
                              }}
                            >
                              {msg.content}
                            </ReactMarkdown>
                          ) : (
                            <div className="flex items-center space-x-2 text-muted-foreground text-xs py-1">
                              <span className="w-2 h-2 rounded-full bg-blue-500 animate-ping shrink-0" />
                              <span>正在调度本地执行与安全分析...</span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

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
                        if (lastUserMsg) handleSendMessage(lastUserMsg.content);
                      }}
                    />
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
              onSelect={(text) => {
                setInputPrompt(text);
                textareaRef.current?.focus();
              }}
              onClose={() => {}}
            />

            {attachments.length > 0 && (
              <div className="flex items-center flex-wrap gap-2 px-3 pb-1">
                {attachments.map((att, idx) => (
                  <div
                    key={idx}
                    className="flex items-center space-x-2 px-2.5 py-1.5 rounded-xl bg-surface-100 border border-border text-xs text-foreground animate-scale-in shadow-sm max-w-[240px]"
                  >
                    {att.type === 'image' && att.dataUrl ? (
                      <img src={att.dataUrl} alt="" className="w-6 h-6 rounded object-cover border border-border shrink-0" />
                    ) : (
                      <Paperclip className="w-3.5 h-3.5 text-blue-500 shrink-0" />
                    )}
                    <span className="truncate flex-1">{att.name}</span>
                    <span className="text-[10px] text-muted-foreground shrink-0">{att.size}</span>
                    <button
                      type="button"
                      onClick={() => setAttachments(attachments.filter((_, i) => i !== idx))}
                      className="hover:text-rose-500 p-0.5 rounded transition-colors shrink-0"
                      title="移除附件"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ))}
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

            {/* 智能防幻觉意图嗅探胶囊：当检测到写盘需求却处于非 Agent 模式时一键引导 */}
            {sceneMode !== 'agent' && /(写入|创建文件|新建文件|写到|落盘|保存到|在.*创建|新建.*目录|写代码到|写入代码)/.test(inputPrompt) && (
              <div className="px-3.5 py-1.5 mb-2 rounded-xl bg-blue-500/10 border border-blue-500/30 flex items-center justify-between text-xs text-blue-600 dark:text-blue-300 animate-fade-in shadow-sm">
                <div className="flex items-center space-x-2">
                  <Sparkles className="w-3.5 h-3.5 text-blue-400 shrink-0" />
                  <span>
                    检测到本地写盘需求：当前处于<strong>「{sceneMode === 'chat' ? '标准对话' : '极速单次'}」</strong>模式（仅生成文本代码）。
                    需 AI 真实操作磁盘物理创建文件？
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => handleSwitchSceneMode('agent')}
                  className="px-2.5 py-1 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium text-[11px] shrink-0 ml-2 shadow-sm transition-all"
                >
                  一键切换为 Agent 任务
                </button>
              </div>
            )}

            {/* 核心输入大胶囊 */}
            <div className="rounded-2xl border border-border/80 bg-surface-card p-2 focus-within:border-accent focus-within:shadow-md transition-all shadow-sm">
              <textarea
                ref={textareaRef}
                value={inputPrompt}
                onChange={(e) => setInputPrompt(e.target.value)}
                onPaste={handlePaste}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSendMessage();
                  }
                }}
                rows={1}
                placeholder="发送消息给 Gemini...（可直接 Ctrl+V 粘贴微信截图或文本，Enter 发送）"
                className="w-full bg-transparent border-none outline-none text-[14px] text-foreground placeholder-muted-foreground px-3 py-1.5 resize-none leading-relaxed break-anywhere"
              />

              <div className="flex items-center justify-between pt-1 px-1 select-none">
                <div className="flex items-center space-x-1.5">
                  <label
                    className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-surface-200/80 rounded-lg cursor-pointer transition-colors"
                    title="添加文件或图片（也支持直接从微信复制后粘贴）"
                  >
                    <input type="file" onChange={handleFileUpload} className="hidden" />
                    <Plus className="w-4 h-4" />
                  </label>

                  {/* 场景模式三档切换胶囊：极速单次 / 标准对话 / Agent 任务 */}
                  <div className="flex items-center rounded-lg bg-surface-100 border border-border/70 p-0.5 text-[11px]">
                    {(
                      [
                        { id: 'oneshot', label: '极速单次', tip: '极速单次：纯单次文本解答，零历史负担，不调用本地工具' },
                        { id: 'chat', label: '标准对话', tip: '标准对话：多轮上下文问答，不操作修改本地磁盘文件' },
                        { id: 'agent', label: 'Agent任务', tip: 'Agent任务：全功能模式，具备终端执行与本地代码真实物理写盘能力' },
                      ] as const
                    ).map((m) => (
                      <button
                        key={m.id}
                        type="button"
                        onClick={() => handleSwitchSceneMode(m.id)}
                        title={m.tip}
                        className={`px-2 py-0.5 rounded-md transition-all ${
                          sceneMode === m.id
                            ? 'bg-blue-600 text-white font-medium shadow-sm'
                            : 'text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        {m.label}
                      </button>
                    ))}
                  </div>

                  {/* 无痕模式开关 */}
                  <button
                    type="button"
                    onClick={handleToggleIncognito}
                    title={incognito ? '当前为无痕模式（不读取也不保存长期记忆）' : '点击开启无痕对话模式'}
                    className={`flex items-center space-x-1 px-2 py-1 rounded-lg text-[11px] border transition-all ${
                      incognito
                        ? 'bg-purple-500/15 border-purple-500/40 text-purple-600 dark:text-purple-400 font-medium'
                        : 'border-transparent text-muted-foreground hover:text-foreground hover:bg-surface-200/60'
                    }`}
                  >
                    <EyeOff className="w-3 h-3" />
                    <span className="hidden sm:inline">无痕</span>
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
                    onSelectModel={(m) => setCurrentModel(m)}
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
                    disabled={isStreaming || !inputPrompt.trim()}
                    className="p-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white transition-all disabled:opacity-30 disabled:cursor-not-allowed shadow-sm"
                    title="发送提问"
                  >
                    {isStreaming ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* 右侧 Canvas 预览抽屉 */}
      <CanvasDrawer
        isOpen={canvasState.isOpen}
        onClose={() => setCanvasState((prev) => ({ ...prev, isOpen: false }))}
        title={canvasState.title}
        content={canvasState.content}
        type={canvasState.type}
      />

      {/* 右侧优化验收抽屉 */}
      <RightActionDrawer
        isOpen={isActionDrawerOpen}
        onClose={() => setIsActionDrawerOpen(false)}
        pendingCountChange={(count) => setPendingActionCount(count)}
      />

      {/* 全局 Ctrl + K 检索模态框 */}
      <SearchModal
        isOpen={isSearchOpen}
        onClose={() => setIsSearchOpen(false)}
        onSelectResult={(convId) => {
          setActiveConvId(convId);
        }}
      />

      {/* 账号池管理模态框 */}
      <AccountModal
        isOpen={isAccountOpen}
        onClose={() => setIsAccountOpen(false)}
        onRefresh={() => {
          fetchStatusPill();
          fetchActiveModels();
        }}
      />

      {/* 四步跨平台数据迁移与导入向导 (ChatGPT / Claude / GEMINI.md / .cursorrules) */}
      <ImportWizardModal
        isOpen={isSyncOpen}
        onClose={() => setIsSyncOpen(false)}
        onSuccess={() => {
          fetchConversations();
        }}
      />

      {/* 设置中心模态框：包含模型自动识别与一键测试、浅色/深色主题外观、回复性格定制 */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        initialTab={settingsInitialTab}
        onModelsUpdated={(state) => applyActiveModelsState(state)}
      />

      {/* 记忆中枢、多源大模型 API 与 Skill/MCP 扩展管理模态框 */}
      <MemoryHubModal
        isOpen={isMemoryHubOpen}
        onClose={() => setIsMemoryHubOpen(false)}
        onProvidersChanged={() => fetchActiveModels()}
      />
    </div>
  );
}
