'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  MessageSquare,
  Link2,
  Copy,
  Check,
  Trash2,
  Plus,
  Bot,
  Brain,
  Sparkles,
  History,
  RefreshCw,
  Clock,
  QrCode,
  Download,
  Users,
  X,
  Pencil,
  Server,
  Crown,
  Activity,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface AgentOption {
  id: string;
  name: string;
  description?: string;
}

interface DialogueLinkItem {
  link_id: string;
  expert_id: string;
  expert_name: string;
  expert_ids?: string[];
  expert_names?: string[];
  channel_type: string;
  channel_label: string;
  target_user_label: string;
  target_user_id: string;
  bound_role?: string;
  bound_role_label?: string;
  bound_scope?: Record<string, string>;
  endpoint_token: string;
  endpoint_url: string;
  memory_extract_enabled: boolean;
  vector_train_enabled: boolean;
  owner_mode?: boolean;
  status: string;
  message_count: number;
  memory_count: number;
  last_trained_at?: number | null;
  created_at: number;
}

interface DialogueRecordItem {
  record_id: string;
  link_id: string;
  expert_id: string;
  channel_type: string;
  sender_name: string;
  user_message: string;
  assistant_reply: string;
  extracted_memory: string;
  vector_status: string;
  created_at: number;
}

const CHANNEL_OPTIONS = [
  { id: 'weixin_clawbot', label: '个人微信 (ClawBot)', hint: '官方扫码长连接（Phase 3 桥接）' },
  { id: 'wecom', label: '企业微信 / 微信客服', hint: '企微客服 / 内部应用回调' },
  { id: 'feishu', label: '飞书', hint: '飞书机器人事件订阅' },
  { id: 'dingtalk', label: '钉钉', hint: '钉钉群机器人 / 应用' },
  { id: 'web_widget', label: '网页嵌入 / H5', hint: '独立对话窗口或嵌入组件' },
  { id: 'webhook', label: '自定义 Webhook', hint: '通用 HTTP 对接入口' },
];

const ROLE_OPTIONS = [
  { id: 'guest', label: '访客（仅规程知识库）' },
  { id: 'end_user', label: '个人/家长（仅本人记录）' },
  { id: 'club_admin', label: '俱乐部管理员（按团体隔离）' },
  { id: 'province_admin', label: '省级协会管理员（按省隔离）' },
  { id: 'hq_admin', label: '总部管理员（全量只读）' },
];

export function DialogueWikiView() {
  const [activeSubTab, setActiveSubTab] = useState<'links' | 'records' | 'memory_train'>('links');

  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [links, setLinks] = useState<DialogueLinkItem[]>([]);
  const [records, setRecords] = useState<DialogueRecordItem[]>([]);
  const [loadingLinks, setLoadingLinks] = useState(false);
  const [loadingRecords, setLoadingRecords] = useState(false);

  // 新建对接链接表单状态（支持多选数字专家，按问题自动在授权专家内路由）
  const [selectedExpertIds, setSelectedExpertIds] = useState<string[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<string>('wecom');
  const [targetUserLabel, setTargetUserLabel] = useState<string>('');
  const [targetUserId, setTargetUserId] = useState<string>('');
  const [boundRole, setBoundRole] = useState<string>('guest');
  const [boundScope, setBoundScope] = useState<string>('');
  const [memoryExtractEnabled, setMemoryExtractEnabled] = useState<boolean>(true);
  const [vectorTrainEnabled, setVectorTrainEnabled] = useState<boolean>(true);
  const [ownerMode, setOwnerMode] = useState<boolean>(false);
  const [creating, setCreating] = useState<boolean>(false);
  // 服务器资产池 (SSH) 管理弹窗状态
  const [showServerModal, setShowServerModal] = useState<boolean>(false);
  const [servers, setServers] = useState<Array<any>>([]);
  const [loadingServers, setLoadingServers] = useState<boolean>(false);
  const [inspectResult, setInspectResult] = useState<string>('');
  const [srvForm, setSrvForm] = useState({
    alias: '',
    host: '',
    port: 22,
    username: 'root',
    auth_type: 'key_path',
    credential: '',
    key_path: '',
    env_tier: 'prod',
    default_workdir: '',
    allow_write: false,
  });
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [copiedLinkId, setCopiedLinkId] = useState<string | null>(null);
  const [trainingLinkId, setTrainingLinkId] = useState<string | null>(null);
  // 在线模拟 IM 发消息联调面板状态
  const [simLinkId, setSimLinkId] = useState<string | null>(null);
  const [simText, setSimText] = useState<string>('');
  const [simMsgId, setSimMsgId] = useState<string>('');
  const [simSending, setSimSending] = useState<boolean>(false);
  const [simResult, setSimResult] = useState<any | null>(null);
  // 渠道配置 / 微信扫码状态抽屉
  const [cfgLinkId, setCfgLinkId] = useState<string | null>(null);
  const [cfgForm, setCfgForm] = useState<Record<string, string>>({});
  const [cfgSaving, setCfgSaving] = useState<boolean>(false);
  const [cfgStatus, setCfgStatus] = useState<any | null>(null);
  // IM 扫码自动绑定二维码弹窗状态（模式 A + 模式 B 统一）
  const [qrModalLink, setQrModalLink] = useState<DialogueLinkItem | null>(null);
  const [qrMaxUses, setQrMaxUses] = useState<number>(1);
  const [qrTicketData, setQrTicketData] = useState<any | null>(null);
  const [qrLoading, setQrLoading] = useState<boolean>(false);
  const [qrLiveStatus, setQrLiveStatus] = useState<string>('');
  const [qrBindings, setQrBindings] = useState<Array<{ binding_id: string; external_user_id: string; external_user_name: string; bound_at: number }>>([]);
  // 对话记录筛选与定向导出状态
  const [recFilterLinkId, setRecFilterLinkId] = useState<string>('');
  const [recFilterChannel, setRecFilterChannel] = useState<string>('');
  const [recFilterStatus, setRecFilterStatus] = useState<string>('');
  const [recKeyword, setRecKeyword] = useState<string>('');
  const [selectedRecordIds, setSelectedRecordIds] = useState<string[]>([]);
  const [exportingFormat, setExportingFormat] = useState<string | null>(null);
  // 编辑已创建机器人（修改授权专家、访问权限角色/RLS、名称与记忆开关）弹窗状态
  const [editingLink, setEditingLink] = useState<DialogueLinkItem | null>(null);
  const [editExpertIds, setEditExpertIds] = useState<string[]>([]);
  const [editTargetUserLabel, setEditTargetUserLabel] = useState<string>('');
  const [editTargetUserId, setEditTargetUserId] = useState<string>('');
  const [editBoundRole, setEditBoundRole] = useState<string>('guest');
  const [editBoundScope, setEditBoundScope] = useState<string>('');
  const [editMemoryExtract, setEditMemoryExtract] = useState<boolean>(true);
  const [editVectorTrain, setEditVectorTrain] = useState<boolean>(true);
  const [editOwnerMode, setEditOwnerMode] = useState<boolean>(false);
  const [savingEdit, setSavingEdit] = useState<boolean>(false);

  const loadServers = useCallback(async () => {
    setLoadingServers(true);
    try {
      const res = await apiFetch('/api/v1/personal-assistant/servers');
      if (res.ok) {
        const json = await res.json();
        setServers(Array.isArray(json.data) ? json.data : []);
      }
    } finally {
      setLoadingServers(false);
    }
  }, []);

  const handleSaveServer = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!srvForm.alias.trim() || !srvForm.host.trim()) return;
    const res = await apiFetch('/api/v1/personal-assistant/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(srvForm),
    });
    if (res.ok) {
      setSrvForm({
        alias: '',
        host: '',
        port: 22,
        username: 'root',
        auth_type: 'key_path',
        credential: '',
        key_path: '',
        env_tier: 'prod',
        default_workdir: '',
        allow_write: false,
      });
      await loadServers();
    }
  };

  const handleInspectServer = async (alias: string) => {
    setInspectResult('正在通过 SSH / 节点探针体检...');
    const res = await apiFetch(`/api/v1/personal-assistant/servers/${encodeURIComponent(alias)}/inspect`, {
      method: 'POST',
    });
    const json = await res.json();
    setInspectResult(json?.data?.summary || json?.detail || '体检完成');
  };

  const handleDeleteServer = async (alias: string) => {
    const res = await apiFetch(`/api/v1/personal-assistant/servers/${encodeURIComponent(alias)}`, {
      method: 'DELETE',
    });
    if (res.ok) {
      await loadServers();
    }
  };

  // 打开个人微信 ClawBot 二维码弹窗时，自动每 2 秒轮询扫码确认状态并自动刷新绑定列表
  useEffect(() => {
    if (!qrModalLink || qrModalLink.channel_type !== 'weixin_clawbot') {
      setQrLiveStatus('');
      return;
    }
    const timer = window.setInterval(async () => {
      try {
        const res = await apiFetch(`/api/v1/dialogue-wiki/links/${qrModalLink.link_id}/status`);
        if (res.ok) {
          const json = await res.json();
          const st = json.data || {};
          if (st.connected || st.qr_status === 'confirmed') {
            setQrLiveStatus('confirmed');
            await loadLinkBindings(qrModalLink.link_id);
            // 扫码绑定成功，通知左侧菜单栏立即浮现专属沙盒
            if (typeof window !== 'undefined') {
              window.dispatchEvent(new CustomEvent('agenthub:workspaces-updated'));
            }
          } else if (st.qr_status === 'scaned') {
            setQrLiveStatus('scaned');
          } else if (st.qr_status === 'expired') {
            setQrLiveStatus('expired');
          } else if (st.qr_status === 'wait') {
            setQrLiveStatus('wait');
          }
        }
      } catch {
        // 忽略轮询瞬时异常
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [qrModalLink]);

  const loadInitialData = useCallback(async () => {
    setLoadingLinks(true);
    try {
      const [agentsRes, linksRes] = await Promise.all([
        apiFetch('/api/v1/agents'),
        apiFetch('/api/v1/dialogue-wiki/links'),
      ]);
      if (agentsRes.ok) {
        const agentsJson = await agentsRes.json();
        const agentList: AgentOption[] =
          agentsJson.agents || agentsJson.data || (Array.isArray(agentsJson) ? agentsJson : []);
        setAgents(agentList);
        if (agentList.length > 0 && selectedExpertIds.length === 0) {
          setSelectedExpertIds([agentList[0].id]);
        }
      }
      if (linksRes.ok) {
        const linksJson = await linksRes.json();
        setLinks(linksJson.data || []);
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('agenthub:workspaces-updated'));
        }
      }
    } catch {
      // 忽略网络瞬时异常
    } finally {
      setLoadingLinks(false);
    }
  }, [selectedExpertIds.length]);

  const loadRecords = useCallback(async () => {
    setLoadingRecords(true);
    try {
      const qs = new URLSearchParams();
      if (recFilterLinkId) qs.set('link_id', recFilterLinkId);
      if (recFilterChannel) qs.set('channel_type', recFilterChannel);
      if (recFilterStatus) qs.set('vector_status', recFilterStatus);
      if (recKeyword.trim()) qs.set('keyword', recKeyword.trim());
      qs.set('limit', '200');
      const res = await apiFetch(`/api/v1/dialogue-wiki/records?${qs.toString()}`);
      if (res.ok) {
        const json = await res.json();
        setRecords(json.data || []);
      }
    } catch {
      // 忽略异常
    } finally {
      setLoadingRecords(false);
    }
  }, [recFilterLinkId, recFilterChannel, recFilterStatus, recKeyword]);

  const handleExportRecords = async (fmt: 'markdown' | 'csv' | 'jsonl') => {
    setExportingFormat(fmt);
    try {
      const qs = new URLSearchParams();
      qs.set('fmt', fmt);
      if (selectedRecordIds.length > 0) {
        qs.set('record_ids', selectedRecordIds.join(','));
      } else {
        if (recFilterLinkId) qs.set('link_id', recFilterLinkId);
        if (recFilterChannel) qs.set('channel_type', recFilterChannel);
        if (recFilterStatus) qs.set('vector_status', recFilterStatus);
        if (recKeyword.trim()) qs.set('keyword', recKeyword.trim());
      }
      const res = await apiFetch(`/api/v1/dialogue-wiki/records/export?${qs.toString()}`);
      if (!res.ok) {
        setNotice({ ok: false, text: '导出对话记录失败' });
        return;
      }
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const ext = fmt === 'markdown' ? 'md' : fmt;
      const a = document.createElement('a');
      a.href = url;
      a.download = `dialogue_records_${Date.now()}.${ext}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      setNotice({
        ok: true,
        text: `已导出 ${selectedRecordIds.length > 0 ? `勾选的 ${selectedRecordIds.length} 条` : '当前筛选全部'} 对话记录为 .${ext} 文件`,
      });
    } catch {
      setNotice({ ok: false, text: '导出对话记录发生网络异常' });
    } finally {
      setExportingFormat(null);
    }
  };

  const handleDeleteRecord = async (recordId: string) => {
    try {
      const res = await apiFetch(`/api/v1/dialogue-wiki/records/${recordId}`, { method: 'DELETE' });
      if (res.ok) {
        setRecords((prev) => prev.filter((r) => r.record_id !== recordId));
        setSelectedRecordIds((prev) => prev.filter((id) => id !== recordId));
      }
    } catch {
      // 忽略异常
    }
  };

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  useEffect(() => {
    if (activeSubTab === 'records' || activeSubTab === 'memory_train') {
      loadRecords();
    }
  }, [activeSubTab, loadRecords]);

  const handleCreateLink = async (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedExpertIds.length === 0) {
      setNotice({ ok: false, text: '请至少选择一位数字专家' });
      return;
    }
    if (!targetUserLabel.trim()) {
      setNotice({ ok: false, text: '请填写目标用户或群组名称（如：李教练 / 上海会员群）' });
      return;
    }

    setCreating(true);
    setNotice(null);
    try {
      const res = await apiFetch('/api/v1/dialogue-wiki/links', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expert_id: selectedExpertIds[0],
          expert_ids: selectedExpertIds,
          channel_type: selectedChannel,
          target_user_label: targetUserLabel.trim(),
          target_user_id: targetUserId.trim(),
          bound_role: boundRole,
          bound_scope: boundScope.trim(),
          memory_extract_enabled: memoryExtractEnabled,
          vector_train_enabled: vectorTrainEnabled,
          owner_mode: boundRole === 'hq_admin' ? ownerMode : false,
        }),
      });
      const json = await res.json();
      if (!res.ok) {
        setNotice({ ok: false, text: json.detail || '生成专用对接链接失败' });
        return;
      }
      setLinks((prev) => [json.data, ...prev]);
      setTargetUserLabel('');
      setTargetUserId('');
      setBoundScope('');
      setNotice({ ok: true, text: '已生成渠道专用对接链接（已服务端锁定权限角色与作用域）。' });
      setActiveSubTab('links');
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('agenthub:workspaces-updated'));
      }
    } catch (err: any) {
      setNotice({ ok: false, text: err?.message || '生成链接时发生网络错误' });
    } finally {
      setCreating(false);
    }
  };

  const handleCopyUrl = async (link: DialogueLinkItem) => {
    try {
      await navigator.clipboard.writeText(link.endpoint_url);
      setCopiedLinkId(link.link_id);
      setTimeout(() => setCopiedLinkId(null), 2000);
    } catch {
      setNotice({ ok: false, text: '复制失败，请手动复制链接' });
    }
  };

  const handleDeleteLink = async (linkId: string) => {
    try {
      const res = await apiFetch(`/api/v1/dialogue-wiki/links/${linkId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        setLinks((prev) => prev.filter((item) => item.link_id !== linkId));
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('agenthub:workspaces-updated'));
        }
      }
    } catch {
      // 忽略异常
    }
  };

  const handleOpenEditModal = (link: DialogueLinkItem) => {
    setEditingLink(link);
    const ids =
      link.expert_ids && link.expert_ids.length > 0
        ? [...link.expert_ids]
        : link.expert_id
          ? [link.expert_id]
          : [];
    setEditExpertIds(ids);
    setEditTargetUserLabel(link.target_user_label || '');
    setEditTargetUserId(link.target_user_id || '');
    setEditBoundRole(link.bound_role || 'guest');
    const scopeStr = link.bound_scope
      ? Object.entries(link.bound_scope)
          .map(([k, v]) => `${k}=${v}`)
          .join(', ')
      : '';
    setEditBoundScope(scopeStr);
    setEditMemoryExtract(Boolean(link.memory_extract_enabled));
    setEditVectorTrain(Boolean(link.vector_train_enabled));
    setEditOwnerMode(Boolean(link.owner_mode));
  };

  const handleSaveEditLink = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingLink) return;
    if (editExpertIds.length === 0) {
      setNotice({ ok: false, text: '请至少选择一位授权数字专家' });
      return;
    }
    if (!editTargetUserLabel.trim()) {
      setNotice({ ok: false, text: '用户 / 群组名称不能为空' });
      return;
    }
    setSavingEdit(true);
    setNotice(null);
    try {
      const res = await apiFetch(`/api/v1/dialogue-wiki/links/${editingLink.link_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expert_ids: editExpertIds,
          target_user_label: editTargetUserLabel.trim(),
          target_user_id: editTargetUserId.trim(),
          bound_role: editBoundRole,
          bound_scope: editBoundScope.trim(),
          memory_extract_enabled: editMemoryExtract,
          vector_train_enabled: editVectorTrain,
          owner_mode: editBoundRole === 'hq_admin' ? editOwnerMode : false,
        }),
      });
      const json = await res.json();
      if (!res.ok) {
        setNotice({ ok: false, text: json.detail || '保存修改失败' });
        return;
      }
      if (json.data) {
        setLinks((prev) =>
          prev.map((item) => (item.link_id === editingLink.link_id ? json.data : item))
        );
      }
      setNotice({ ok: true, text: json.message || '机器人配置已更新并实时生效。' });
      setEditingLink(null);
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('agenthub:workspaces-updated'));
      }
    } catch (err: any) {
      setNotice({ ok: false, text: err?.message || '保存修改时发生网络错误' });
    } finally {
      setSavingEdit(false);
    }
  };

  const handleTriggerTrain = async (linkId: string) => {
    setTrainingLinkId(linkId);
    setNotice(null);
    try {
      const res = await apiFetch(`/api/v1/dialogue-wiki/links/${linkId}/train`, {
        method: 'POST',
      });
      const json = await res.json();
      setNotice({
        ok: res.ok,
        text: json.message || json.detail || '训练任务已完成',
      });
      if (res.ok) {
        await loadInitialData();
        await loadRecords();
      }
    } catch {
      setNotice({ ok: false, text: '触发向量化训练请求失败' });
    } finally {
      setTrainingLinkId(null);
    }
  };

  const handleSimulateInbound = async (link: DialogueLinkItem) => {
    if (!simText.trim()) return;
    setSimSending(true);
    setSimResult(null);
    try {
      const res = await apiFetch(
        `/api/v1/dialogue-wiki/ingress/${encodeURIComponent(link.channel_type)}/${encodeURIComponent(link.endpoint_token)}?mode=sync_reply`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: simText.trim(),
            sender_id: link.target_user_id || `sim_${link.link_id}`,
            sender_name: link.target_user_label,
            external_msg_id: simMsgId.trim() || undefined,
          }),
        }
      );
      const json = await res.json();
      setSimResult({ ok: res.ok, data: json.data || json });
      if (res.ok) {
        await loadInitialData();
        await loadRecords();
      }
    } catch (err: any) {
      setSimResult({ ok: false, data: { detail: err?.message || '模拟请求异常' } });
    } finally {
      setSimSending(false);
    }
  };

  const handleOpenChannelConfig = async (link: DialogueLinkItem) => {
    if (cfgLinkId === link.link_id) {
      setCfgLinkId(null);
      setCfgStatus(null);
      return;
    }
    setCfgLinkId(link.link_id);
    setCfgForm({});
    setCfgStatus(null);
    try {
      const res = await apiFetch(`/api/v1/dialogue-wiki/links/${link.link_id}/status`);
      if (res.ok) {
        const json = await res.json();
        setCfgStatus(json.data || null);
      }
    } catch {
      // 忽略瞬时异常
    }
  };

  const handleSaveChannelConfig = async (linkId: string) => {
    setCfgSaving(true);
    setNotice(null);
    try {
      const res = await apiFetch(`/api/v1/dialogue-wiki/links/${linkId}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_config: cfgForm }),
      });
      const json = await res.json();
      setNotice({ ok: res.ok, text: json.message || json.detail || '配置已保存' });
      const stRes = await apiFetch(`/api/v1/dialogue-wiki/links/${linkId}/status`);
      if (stRes.ok) {
        const stJson = await stRes.json();
        setCfgStatus(stJson.data || null);
      }
    } catch {
      setNotice({ ok: false, text: '保存渠道配置失败' });
    } finally {
      setCfgSaving(false);
    }
  };

  const loadLinkBindings = async (linkId: string) => {
    try {
      const res = await apiFetch(`/api/v1/dialogue-wiki/links/${linkId}/bindings`);
      if (res.ok) {
        const json = await res.json();
        setQrBindings(json.data || []);
      }
    } catch {
      // 忽略异常
    }
  };

  const handleOpenQrModal = async (link: DialogueLinkItem, maxUsesOverride?: number) => {
    const effMaxUses = typeof maxUsesOverride === 'number' ? maxUsesOverride : qrMaxUses;
    setQrModalLink(link);
    setQrLoading(true);
    setQrTicketData(null);
    try {
      const [ticketRes] = await Promise.all([
        apiFetch(`/api/v1/dialogue-wiki/links/${link.link_id}/qr-ticket`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ max_uses: effMaxUses, ttl_minutes: 60 }),
        }),
        loadLinkBindings(link.link_id),
      ]);
      if (ticketRes.ok) {
        const json = await ticketRes.json();
        setQrTicketData(json.data || null);
        // 打开扫码绑定弹窗后，立即触发左侧菜单栏刷新以显示专属沙盒
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('agenthub:workspaces-updated'));
        }
      }
    } catch {
      setNotice({ ok: false, text: '生成扫码绑定二维码失败' });
    } finally {
      setQrLoading(false);
    }
  };

  const handleRemoveBinding = async (linkId: string, bindingId: string) => {
    try {
      const res = await apiFetch(`/api/v1/dialogue-wiki/links/${linkId}/bindings/${bindingId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        setQrBindings((prev) => prev.filter((b) => b.binding_id !== bindingId));
      }
    } catch {
      // 忽略异常
    }
  };

  return (
    <div className="h-full flex flex-col bg-background overflow-y-auto">
      <div className="max-w-6xl w-full mx-auto px-6 py-5 space-y-5">
        {/* 顶部功能定位卡片：选专家 + 选渠道 + 指定用户 -> 生成专用对接链接 */}
        <div className="p-4 rounded-2xl bg-surface-card border border-border shadow-sm space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <MessageSquare className="w-4 h-4 text-violet-500" />
                <span>用户对话 Wiki · IM 渠道对接与记忆沉淀工作台</span>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-violet-500/15 text-violet-400 border border-violet-500/30 font-medium">
                  能力预留
                </span>
              </h2>
              <p className="text-xs text-muted-foreground mt-1">
                为指定数字专家配置外部 IM 渠道与目标用户/群组的专用对接链接；后续接入真实 IM 消息后，自动沉淀历史对话记录、提炼记忆并执行向量化训练。
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setShowServerModal(true);
                  loadServers();
                }}
                className="px-3 py-1.5 rounded-lg text-xs border border-sky-500/40 bg-sky-500/10 hover:bg-sky-500/20 text-sky-400 flex items-center gap-1.5 transition-colors"
              >
                <Server className="w-3.5 h-3.5" />
                <span>服务器资产池 (SSH)</span>
              </button>
              <button
                type="button"
                onClick={loadInitialData}
                className="px-3 py-1.5 rounded-lg text-xs border border-border bg-background hover:bg-surface-200 text-foreground flex items-center gap-1.5 transition-colors"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loadingLinks ? 'animate-spin' : ''}`} />
                <span>刷新</span>
              </button>
            </div>
          </div>

          <form onSubmit={handleCreateLink} className="grid grid-cols-1 md:grid-cols-12 gap-3 items-end">
            <div className="md:col-span-12">
              <label className="block text-[11px] font-medium text-muted-foreground mb-1.5">
                1. 授权对话数字专家（可多选：单选则严格锁定单一专家，多选则按问题自动在已授权专家间跨库路由）
                <span className="ml-2 text-violet-400 font-mono">已选 {selectedExpertIds.length} 位</span>
              </label>
              {agents.length === 0 ? (
                <div className="text-xs text-muted-foreground py-1">暂无可用专家</div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {agents.map((ag) => {
                    const active = selectedExpertIds.includes(ag.id);
                    return (
                      <button
                        key={ag.id}
                        type="button"
                        onClick={() => {
                          setSelectedExpertIds((prev) =>
                            prev.includes(ag.id) ? prev.filter((id) => id !== ag.id) : [...prev, ag.id]
                          );
                        }}
                        className={`px-2.5 py-1.5 rounded-xl border text-xs flex items-center gap-1.5 transition-colors ${
                          active
                            ? 'bg-violet-500/15 border-violet-500/50 text-foreground font-medium'
                            : 'bg-background border-border text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        <Bot className={`w-3.5 h-3.5 ${active ? 'text-violet-400' : 'opacity-60'}`} />
                        <span>{ag.name}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="md:col-span-4">
              <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                2. 对应接入渠道 (IM)
              </label>
              <select
                value={selectedChannel}
                onChange={(e) => setSelectedChannel(e.target.value)}
                className="w-full px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground focus:outline-none focus:border-violet-500"
              >
                {CHANNEL_OPTIONS.map((ch) => (
                  <option key={ch.id} value={ch.id}>
                    {ch.label} · {ch.hint}
                  </option>
                ))}
              </select>
            </div>

            <div className="md:col-span-3">
              <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                3. 用户 / 群组名称
              </label>
              <input
                type="text"
                value={targetUserLabel}
                onChange={(e) => setTargetUserLabel(e.target.value)}
                placeholder="如：李教练 / 会员群"
                className="w-full px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground focus:outline-none focus:border-violet-500"
              />
            </div>

            <div className="md:col-span-3">
              <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                外部用户 ID（选填）
              </label>
              <input
                type="text"
                value={targetUserId}
                onChange={(e) => setTargetUserId(e.target.value)}
                placeholder="open_id / userid"
                className="w-full px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground focus:outline-none focus:border-violet-500"
              />
            </div>

            <div className="md:col-span-2">
              <button
                type="submit"
                disabled={creating || selectedExpertIds.length === 0}
                className="w-full px-3 py-2 rounded-xl bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-xs font-medium flex items-center justify-center gap-1.5 transition-colors"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>{creating ? '生成中...' : '生成专用链接'}</span>
              </button>
            </div>

            <div className="md:col-span-4">
              <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                4. 锁定数据查询角色（服务端签发，防越权）
              </label>
              <select
                value={boundRole}
                onChange={(e) => setBoundRole(e.target.value)}
                className="w-full px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground focus:outline-none focus:border-violet-500"
              >
                {ROLE_OPTIONS.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="md:col-span-8">
              <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                5. 锁定行级数据作用域 RLS（非总部角色必填，如 region_code=310000 或 delegation_id=1052）
              </label>
              <input
                type="text"
                value={boundScope}
                onChange={(e) => setBoundScope(e.target.value)}
                placeholder="region_code=310000 / delegation_id=1052 / owner=u_1001"
                className="w-full px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground focus:outline-none focus:border-violet-500"
              />
            </div>
          </form>

          <div className="flex flex-wrap items-center gap-5 pt-1 text-xs text-muted-foreground">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={memoryExtractEnabled}
                onChange={(e) => setMemoryExtractEnabled(e.target.checked)}
                className="rounded border-border accent-violet-500"
              />
              <span>自动提炼用户对话记忆并存储（预留）</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={vectorTrainEnabled}
                onChange={(e) => setVectorTrainEnabled(e.target.checked)}
                className="rounded border-border accent-violet-500"
              />
              <span>对话记录增量向量化训练（预留）</span>
            </label>
            {boundRole === 'hq_admin' && (
              <label className="flex items-center gap-2 cursor-pointer text-amber-400 font-medium">
                <input
                  type="checkbox"
                  checked={ownerMode}
                  onChange={(e) => setOwnerMode(e.target.checked)}
                  className="rounded border-border accent-amber-500"
                />
                <Crown className="w-3.5 h-3.5" />
                <span>开启主人全能模式（高危需二次确认）</span>
              </label>
            )}
          </div>

          {notice && (
            <div
              className={`px-3 py-2 rounded-xl text-xs border ${
                notice.ok
                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                  : 'bg-red-500/10 border-red-500/30 text-red-400'
              }`}
            >
              {notice.text}
            </div>
          )}
        </div>

        {/* 二级功能切换：专用对接链接 / 对话记录 / 提炼记忆与向量化训练 */}
        <div className="flex items-center gap-2 border-b border-border pb-2 text-xs">
          <button
            type="button"
            onClick={() => setActiveSubTab('links')}
            className={`px-3 py-1.5 rounded-lg font-medium flex items-center gap-1.5 transition-colors ${
              activeSubTab === 'links'
                ? 'bg-surface-200 text-foreground'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <Link2 className="w-3.5 h-3.5 text-violet-400" />
            <span>专用对接链接 ({links.length})</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveSubTab('records')}
            className={`px-3 py-1.5 rounded-lg font-medium flex items-center gap-1.5 transition-colors ${
              activeSubTab === 'records'
                ? 'bg-surface-200 text-foreground'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <History className="w-3.5 h-3.5 text-sky-400" />
            <span>用户对话记录 ({records.length})</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveSubTab('memory_train')}
            className={`px-3 py-1.5 rounded-lg font-medium flex items-center gap-1.5 transition-colors ${
              activeSubTab === 'memory_train'
                ? 'bg-surface-200 text-foreground'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            <Brain className="w-3.5 h-3.5 text-emerald-400" />
            <span>提炼记忆与向量化训练</span>
          </button>
        </div>

        {/* 1. 专用对接链接管理区 */}
        {activeSubTab === 'links' && (
          <div className="space-y-3">
            {links.length === 0 ? (
              <div className="p-10 rounded-2xl bg-surface-card border border-dashed border-border text-center space-y-2">
                <Link2 className="w-8 h-8 text-muted-foreground mx-auto opacity-60" />
                <div className="text-xs font-medium text-foreground">暂未生成渠道专用对接链接</div>
                <p className="text-[11px] text-muted-foreground max-w-md mx-auto">
                  在上方选择一位数字专家、指定 IM 渠道及目标用户/群组，即可生成一条专属对接链接，用于后续 IM 消息接入与对话沉淀。
                </p>
              </div>
            ) : (
              links.map((item) => (
                <div
                  key={item.link_id}
                  className="p-4 rounded-2xl bg-surface-card border border-border flex flex-col md:flex-row md:items-center justify-between gap-3"
                >
                  <div className="space-y-1.5 min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      {(item.expert_names && item.expert_names.length > 0 ? item.expert_names : [item.expert_name]).map((en, idx) => (
                        <span
                          key={`${item.link_id}_exp_${idx}`}
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-violet-500/15 text-violet-400 font-medium"
                        >
                          <Bot className="w-3 h-3" />
                          {en}
                        </span>
                      ))}
                      <span className="px-2 py-0.5 rounded-md bg-surface-200 text-foreground font-medium">
                        {item.channel_label}
                      </span>
                      <span className="font-semibold text-foreground">
                        {item.target_user_label}
                      </span>
                      {item.target_user_id && (
                        <span className="text-[11px] text-muted-foreground font-mono">
                          ({item.target_user_id})
                        </span>
                      )}
                      {item.bound_role && (
                        <span className="px-2 py-0.5 rounded-md bg-emerald-500/10 text-emerald-400 border border-emerald-500/25 text-[11px] font-medium">
                          {item.bound_role_label || item.bound_role}
                        </span>
                      )}
                      {item.owner_mode && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-500/15 text-amber-400 border border-amber-500/30 text-[11px] font-medium">
                          <Crown className="w-3 h-3" />
                          主人控制台
                        </span>
                      )}
                      {item.bound_scope && Object.keys(item.bound_scope).length > 0 && (
                        <span className="text-[11px] text-amber-400 font-mono">
                          RLS: {Object.entries(item.bound_scope).map(([k, v]) => `${k}=${v}`).join(', ')}
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-2">
                      <code className="text-[11px] px-2.5 py-1 rounded-lg bg-background border border-border text-muted-foreground truncate max-w-2xl">
                        {item.endpoint_url}
                      </code>
                      <button
                        type="button"
                        onClick={() => handleCopyUrl(item)}
                        className="px-2.5 py-1 rounded-lg bg-surface-200 hover:bg-surface-300 text-xs text-foreground flex items-center gap-1 shrink-0 transition-colors"
                      >
                        {copiedLinkId === item.link_id ? (
                          <>
                            <Check className="w-3 h-3 text-emerald-400" />
                            <span>已复制</span>
                          </>
                        ) : (
                          <>
                            <Copy className="w-3 h-3" />
                            <span>复制链接</span>
                          </>
                        )}
                      </button>
                    </div>

                    <div className="flex flex-wrap items-center gap-4 text-[11px] text-muted-foreground">
                      <span>对话沉淀：{item.message_count} 条</span>
                      <span>提炼记忆：{item.memory_count} 条</span>
                      <span>
                        记忆提炼：{item.memory_extract_enabled ? '已开启' : '未开启'}
                      </span>
                      <span>
                        向量化训练：{item.vector_train_enabled ? '已开启' : '未开启'}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0 flex-wrap">
                    <button
                      type="button"
                      onClick={() => handleOpenEditModal(item)}
                      className="px-3 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border text-foreground text-xs font-medium flex items-center gap-1.5 transition-colors"
                    >
                      <Pencil className="w-3.5 h-3.5 text-violet-400" />
                      <span>编辑</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleOpenQrModal(item)}
                      className="px-3 py-1.5 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/35 text-amber-400 text-xs font-medium flex items-center gap-1.5 transition-colors"
                    >
                      <QrCode className="w-3.5 h-3.5" />
                      <span>扫码绑定</span>
                    </button>
                    {(item.channel_type === 'wecom' ||
                      item.channel_type === 'weixin_clawbot' ||
                      item.channel_type === 'feishu' ||
                      item.channel_type === 'dingtalk') && (
                      <button
                        type="button"
                        onClick={() => handleOpenChannelConfig(item)}
                        className="px-3 py-1.5 rounded-lg bg-sky-500/15 hover:bg-sky-500/25 border border-sky-500/30 text-sky-400 text-xs font-medium transition-colors"
                      >
                        {cfgLinkId === item.link_id
                          ? '收起配置'
                          : item.channel_type === 'weixin_clawbot'
                            ? '扫码/桥接状态'
                            : '渠道凭据'}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        if (simLinkId === item.link_id) {
                          setSimLinkId(null);
                          setSimResult(null);
                        } else {
                          setSimLinkId(item.link_id);
                          setSimText('');
                          setSimMsgId('');
                          setSimResult(null);
                        }
                      }}
                      className="px-3 py-1.5 rounded-lg bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/30 text-violet-400 text-xs font-medium flex items-center gap-1.5 transition-colors"
                    >
                      <MessageSquare className="w-3.5 h-3.5" />
                      <span>{simLinkId === item.link_id ? '收起测试' : '模拟发消息'}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleTriggerTrain(item.link_id)}
                      disabled={trainingLinkId === item.link_id}
                      className="px-3 py-1.5 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/30 text-emerald-400 text-xs font-medium flex items-center gap-1.5 transition-colors"
                    >
                      <Sparkles className="w-3.5 h-3.5" />
                      <span>{trainingLinkId === item.link_id ? '调度中...' : '向量化训练'}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDeleteLink(item.link_id)}
                      className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors"
                      title="删除对接链接"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))
            )}
            {/* 选中链接的渠道专属配置 / 个人微信 ClawBot 状态卡片 */}
            {cfgLinkId && (() => {
              const cfgLink = links.find((l) => l.link_id === cfgLinkId);
              if (!cfgLink) return null;
              return (
                <div className="p-4 rounded-2xl bg-surface-100 border border-sky-500/40 space-y-3">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-semibold text-foreground">
                      {cfgLink.channel_type === 'weixin_clawbot'
                        ? `个人微信 ClawBot 桥接配置 · ${cfgLink.target_user_label}`
                        : cfgLink.channel_type === 'feishu'
                          ? `飞书机器人凭据配置 · ${cfgLink.target_user_label}`
                          : cfgLink.channel_type === 'dingtalk'
                            ? `钉钉机器人凭据配置 · ${cfgLink.target_user_label}`
                            : `企业微信 / 微信客服凭据配置 · ${cfgLink.target_user_label}`}
                    </span>
                    <span className={`text-[11px] font-medium ${cfgStatus?.connected ? 'text-emerald-400' : 'text-amber-400'}`}>
                      {cfgStatus?.connected ? '● 已就绪 / 在线' : '○ 待配置或待扫码'}
                    </span>
                  </div>

                  {cfgLink.channel_type === 'feishu' ? (
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                      <input
                        type="text"
                        value={cfgForm.app_id || ''}
                        onChange={(e) => setCfgForm((p) => ({ ...p, app_id: e.target.value }))}
                        placeholder="飞书 App ID (cli_...)"
                        className="px-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                      />
                      <input
                        type="password"
                        value={cfgForm.app_secret || ''}
                        onChange={(e) => setCfgForm((p) => ({ ...p, app_secret: e.target.value }))}
                        placeholder="飞书 App Secret（留空保留原值）"
                        className="px-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                      />
                      <button
                        type="button"
                        disabled={cfgSaving}
                        onClick={() => handleSaveChannelConfig(cfgLink.link_id)}
                        className="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-medium"
                      >
                        {cfgSaving ? '保存中...' : '保存飞书配置'}
                      </button>
                    </div>
                  ) : cfgLink.channel_type === 'dingtalk' ? (
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                      <input
                        type="password"
                        value={cfgForm.app_secret || ''}
                        onChange={(e) => setCfgForm((p) => ({ ...p, app_secret: e.target.value }))}
                        placeholder="钉钉机器人 AppSecret（用于 HMAC-SHA256 验签）"
                        className="md:col-span-2 px-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                      />
                      <button
                        type="button"
                        disabled={cfgSaving}
                        onClick={() => handleSaveChannelConfig(cfgLink.link_id)}
                        className="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-medium"
                      >
                        {cfgSaving ? '保存中...' : '保存钉钉配置'}
                      </button>
                    </div>
                  ) : cfgLink.channel_type === 'wecom' ? (
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                      <input
                        type="text"
                        value={cfgForm.corpid || ''}
                        onChange={(e) => setCfgForm((p) => ({ ...p, corpid: e.target.value }))}
                        placeholder="CorpID（企业 ID）"
                        className="px-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                      />
                      <input
                        type="password"
                        value={cfgForm.corpsecret || ''}
                        onChange={(e) => setCfgForm((p) => ({ ...p, corpsecret: e.target.value }))}
                        placeholder="CorpSecret（留空则保留原值）"
                        className="px-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                      />
                      <input
                        type="text"
                        value={cfgForm.agent_id || ''}
                        onChange={(e) => setCfgForm((p) => ({ ...p, agent_id: e.target.value }))}
                        placeholder="AgentID（自建应用 ID）"
                        className="px-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                      />
                      <input
                        type="text"
                        value={cfgForm.token || ''}
                        onChange={(e) => setCfgForm((p) => ({ ...p, token: e.target.value }))}
                        placeholder="回调 Token"
                        className="px-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                      />
                      <input
                        type="password"
                        value={cfgForm.encoding_aes_key || ''}
                        onChange={(e) => setCfgForm((p) => ({ ...p, encoding_aes_key: e.target.value }))}
                        placeholder="EncodingAESKey（43位，留空保留）"
                        className="px-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                      />
                      <button
                        type="button"
                        disabled={cfgSaving}
                        onClick={() => handleSaveChannelConfig(cfgLink.link_id)}
                        className="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-medium"
                      >
                        {cfgSaving ? '保存中...' : '保存企微配置'}
                      </button>
                    </div>
                  ) : (
                    <div className="space-y-2 text-xs">
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                        <input
                          type="text"
                          value={cfgForm.bridge_url || ''}
                          onChange={(e) => setCfgForm((p) => ({ ...p, bridge_url: e.target.value }))}
                          placeholder="Sidecar 地址（默认 http://127.0.0.1:18791）"
                          className="md:col-span-2 px-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground font-mono"
                        />
                        <button
                          type="button"
                          disabled={cfgSaving}
                          onClick={() => handleSaveChannelConfig(cfgLink.link_id)}
                          className="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-medium"
                        >
                          {cfgSaving ? '保存并检测...' : '保存并刷新状态'}
                        </button>
                      </div>
                      {cfgStatus?.hint && (
                        <div className="text-[11px] text-amber-400">{cfgStatus.hint}</div>
                      )}
                      {cfgStatus?.qr_ascii && (
                        <pre className="p-2 rounded-lg bg-black text-white text-[10px] leading-none overflow-x-auto font-mono">
                          {cfgStatus.qr_ascii}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              );
            })()}
            {/* 选中链接的在线模拟 IM 发消息联调卡片 */}
            {simLinkId && (() => {
              const targetLink = links.find((l) => l.link_id === simLinkId);
              if (!targetLink) return null;
              return (
                <div className="p-4 rounded-2xl bg-surface-100 border border-violet-500/40 space-y-3">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-semibold text-foreground">
                      在线模拟外部 IM 入站联调 · {targetLink.expert_name} ({targetLink.bound_role_label || targetLink.bound_role})
                    </span>
                    <span className="text-[11px] text-muted-foreground">
                      模式：sync_reply（真实走权限锁定、排版与 dialogue_wiki_records 落库）
                    </span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-12 gap-2">
                    <input
                      type="text"
                      value={simText}
                      onChange={(e) => setSimText(e.target.value)}
                      placeholder="输入模拟外部 IM 用户提问（如：俱乐部联赛报名有联系电话吗）"
                      className="md:col-span-7 px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground"
                    />
                    <input
                      type="text"
                      value={simMsgId}
                      onChange={(e) => setSimMsgId(e.target.value)}
                      placeholder="external_msg_id（选填，重发同ID可测幂等去重）"
                      className="md:col-span-3 px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground font-mono"
                    />
                    <button
                      type="button"
                      disabled={simSending || !simText.trim()}
                      onClick={() => handleSimulateInbound(targetLink)}
                      className="md:col-span-2 px-3 py-2 rounded-xl bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-xs font-medium"
                    >
                      {simSending ? '执行中...' : '发送测试'}
                    </button>
                  </div>
                  {simResult && (
                    <div className="p-3 rounded-xl bg-background border border-border text-xs space-y-1.5">
                      <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground font-mono">
                        <span>状态: {simResult.data?.status || (simResult.ok ? 'ok' : 'error')}</span>
                        {simResult.data?.caller_role && <span>锁定角色: {simResult.data.caller_role}</span>}
                        {simResult.data?.record_id && <span>落库ID: {simResult.data.record_id}</span>}
                        {Array.isArray(simResult.data?.reply_chunks) && (
                          <span>出站分片: {simResult.data.reply_chunks.length} 条</span>
                        )}
                      </div>
                      <pre className="whitespace-pre-wrap text-xs text-foreground leading-relaxed font-sans max-h-60 overflow-y-auto">
                        {simResult.data?.reply_text || simResult.data?.message || simResult.data?.detail || JSON.stringify(simResult.data, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              );
            })()}
          </div>
        )}

        {/* 2. 用户对话记录查看、清洗与定向导出区 */}
        {activeSubTab === 'records' && (
          <div className="space-y-3">
            {/* 筛选与定向导出工具栏 */}
            <div className="p-3.5 rounded-2xl bg-surface-card border border-border space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center">
                <select
                  value={recFilterLinkId}
                  onChange={(e) => setRecFilterLinkId(e.target.value)}
                  className="md:col-span-3 px-2.5 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                >
                  <option value="">全部渠道链接</option>
                  {links.map((l) => (
                    <option key={l.link_id} value={l.link_id}>
                      {l.expert_name} · {l.target_user_label}
                    </option>
                  ))}
                </select>

                <select
                  value={recFilterChannel}
                  onChange={(e) => setRecFilterChannel(e.target.value)}
                  className="md:col-span-2 px-2.5 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                >
                  <option value="">全部渠道</option>
                  {CHANNEL_OPTIONS.map((ch) => (
                    <option key={ch.id} value={ch.id}>
                      {ch.label}
                    </option>
                  ))}
                </select>

                <select
                  value={recFilterStatus}
                  onChange={(e) => setRecFilterStatus(e.target.value)}
                  className="md:col-span-2 px-2.5 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                >
                  <option value="">全部训练状态</option>
                  <option value="pending">待训练 (pending)</option>
                  <option value="trained">已训练 (trained)</option>
                </select>

                <input
                  type="text"
                  value={recKeyword}
                  onChange={(e) => setRecKeyword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && loadRecords()}
                  placeholder="搜问题/回复关键词或发送者..."
                  className="md:col-span-3 px-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground"
                />

                <button
                  type="button"
                  onClick={loadRecords}
                  className="md:col-span-2 px-3 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-xs text-foreground font-medium flex items-center justify-center gap-1"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingRecords ? 'animate-spin' : ''}`} />
                  <span>筛选刷新</span>
                </button>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-border/60 text-xs">
                <label className="flex items-center gap-2 cursor-pointer text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={records.length > 0 && selectedRecordIds.length === records.length}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setSelectedRecordIds(records.map((r) => r.record_id));
                      } else {
                        setSelectedRecordIds([]);
                      }
                    }}
                    className="rounded border-border accent-emerald-500"
                  />
                  <span>
                    全选当前 {records.length} 条
                    {selectedRecordIds.length > 0 && `（已勾选 ${selectedRecordIds.length} 条定向导出）`}
                  </span>
                </label>

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={records.length === 0 || exportingFormat !== null}
                    onClick={() => handleExportRecords('markdown')}
                    className="px-2.5 py-1 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/35 text-emerald-400 text-[11px] font-medium flex items-center gap-1 disabled:opacity-40"
                  >
                    <Download className="w-3 h-3" />
                    <span>{exportingFormat === 'markdown' ? '导出中...' : '导出 Markdown (.md 供Wiki/知识库训练)'}</span>
                  </button>
                  <button
                    type="button"
                    disabled={records.length === 0 || exportingFormat !== null}
                    onClick={() => handleExportRecords('csv')}
                    className="px-2.5 py-1 rounded-lg bg-sky-500/15 hover:bg-sky-500/25 border border-sky-500/35 text-sky-400 text-[11px] font-medium flex items-center gap-1 disabled:opacity-40"
                  >
                    <Download className="w-3 h-3" />
                    <span>{exportingFormat === 'csv' ? '导出中...' : '导出 CSV (Excel审阅)'}</span>
                  </button>
                  <button
                    type="button"
                    disabled={records.length === 0 || exportingFormat !== null}
                    onClick={() => handleExportRecords('jsonl')}
                    className="px-2.5 py-1 rounded-lg bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/35 text-violet-400 text-[11px] font-medium flex items-center gap-1 disabled:opacity-40"
                  >
                    <Download className="w-3 h-3" />
                    <span>{exportingFormat === 'jsonl' ? '导出 JSONL (微调语料)' : '导出 JSONL'}</span>
                  </button>
                </div>
              </div>
            </div>

            {/* 对话记录明细列表 */}
            <div className="p-4 rounded-2xl bg-surface-card border border-border space-y-2.5">
              {loadingRecords ? (
                <div className="py-8 text-center text-xs text-muted-foreground">加载对话记录中...</div>
              ) : records.length === 0 ? (
                <div className="py-8 text-center space-y-2">
                  <History className="w-8 h-8 text-sky-400/70 mx-auto" />
                  <div className="text-xs font-semibold text-foreground">暂无匹配的对话记录</div>
                  <p className="text-[11px] text-muted-foreground">
                    微信 / 企微 / 飞书 / 钉钉或在线模拟发送的问答会自动沉淀在此，支持筛选、剔除闲聊并导出用于再训练。
                  </p>
                </div>
              ) : (
                records.map((rec) => {
                  const checked = selectedRecordIds.includes(rec.record_id);
                  const timeStr = rec.created_at
                    ? new Date(rec.created_at * 1000).toLocaleString('zh-CN', { hour12: false })
                    : '';
                  return (
                    <div
                      key={rec.record_id}
                      className={`p-3.5 rounded-xl border text-xs space-y-2 transition-colors ${
                        checked
                          ? 'bg-emerald-500/5 border-emerald-500/40'
                          : 'bg-background border-border'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                        <div className="flex items-center gap-2 min-w-0">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSelectedRecordIds((prev) =>
                                  prev.includes(rec.record_id) ? prev : [...prev, rec.record_id]
                                );
                              } else {
                                setSelectedRecordIds((prev) => prev.filter((id) => id !== rec.record_id));
                              }
                            }}
                            className="rounded border-border accent-emerald-500"
                          />
                          <span className="px-1.5 py-0.5 rounded bg-surface-200 text-foreground font-medium">
                            {rec.channel_type}
                          </span>
                          <span className="font-medium text-foreground truncate">{rec.sender_name}</span>
                          <span>{timeStr}</span>
                          <span
                            className={`px-1.5 py-0.5 rounded font-mono text-[10px] ${
                              rec.vector_status === 'trained'
                                ? 'bg-emerald-500/15 text-emerald-400'
                                : 'bg-amber-500/15 text-amber-400'
                            }`}
                          >
                            {rec.vector_status || 'pending'}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleDeleteRecord(rec.record_id)}
                          className="text-muted-foreground hover:text-red-400 p-1 rounded"
                          title="剔除此条闲聊/无效记录"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                      <div className="pl-5 space-y-1.5">
                        <div className="text-foreground">
                          <span className="font-semibold text-sky-400 mr-1.5">问：</span>
                          <span>{rec.user_message}</span>
                        </div>
                        <div className="text-muted-foreground whitespace-pre-wrap leading-relaxed">
                          <span className="font-semibold text-emerald-400 mr-1.5">答：</span>
                          <span>{rec.assistant_reply}</span>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        {/* 3. 提炼记忆与向量化训练区 */}
        {activeSubTab === 'memory_train' && (
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="p-5 rounded-2xl bg-surface-card border border-border space-y-2">
                <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
                  <Brain className="w-4 h-4 text-emerald-400" />
                  <span>对话记忆提炼存储（已沉淀 {records.filter((r) => r.extracted_memory).length} 条）</span>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  点击任意渠道链接右侧的「向量化训练」，系统自动将未训练对话提炼为结构化记忆条目并写入 `dialogue_wiki_records.extracted_memory`。
                </p>
              </div>

              <div className="p-5 rounded-2xl bg-surface-card border border-border space-y-2">
                <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
                  <Sparkles className="w-4 h-4 text-violet-400" />
                  <span>对话记录增量向量化训练（已入库 {records.filter((r) => r.vector_status === 'trained').length} 条）</span>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  已训练记录自动以 `float16` 压缩向量与 FTS5 索引写入专家专属对话库 `kb_dialogue_&lt;expert_id&gt;`。
                </p>
              </div>
            </div>

            {records.some((r) => r.extracted_memory) && (
              <div className="p-4 rounded-2xl bg-surface-card border border-border space-y-2">
                <div className="text-xs font-semibold text-foreground">已提炼对话记忆列表</div>
                <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                  {records
                    .filter((r) => r.extracted_memory)
                    .map((r) => (
                      <div key={r.record_id} className="p-3 rounded-xl bg-background border border-border text-xs space-y-1">
                        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                          <span>渠道：{r.channel_type} · 发送者：{r.sender_name}</span>
                          <span className="text-emerald-400 font-mono">{r.vector_status}</span>
                        </div>
                        <div className="text-foreground leading-relaxed">{r.extracted_memory}</div>
                      </div>
                    ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* IM 客户端扫码自动绑定二维码弹窗（模式 A 带参认领 + 模式 B 个人微信 ClawBot 扫码统一） */}
      {qrModalLink && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg rounded-2xl bg-surface-card border border-border shadow-2xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <QrCode className="w-4 h-4 text-amber-400" />
                <span>IM 扫码自动绑定 · {qrModalLink.expert_name}</span>
              </div>
              <button
                type="button"
                onClick={() => {
                  setQrModalLink(null);
                  setQrTicketData(null);
                  if (typeof window !== 'undefined') {
                    window.dispatchEvent(new CustomEvent('agenthub:workspaces-updated'));
                  }
                }}
                className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setQrMaxUses(1);
                    handleOpenQrModal(qrModalLink, 1);
                  }}
                  className={`px-2.5 py-1 rounded-lg border transition-colors ${
                    qrMaxUses === 1
                      ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-400 font-medium'
                      : 'bg-background border-border text-muted-foreground'
                  }`}
                >
                  一次性单人码（防转发）
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setQrMaxUses(0);
                    handleOpenQrModal(qrModalLink, 0);
                  }}
                  className={`px-2.5 py-1 rounded-lg border transition-colors ${
                    qrMaxUses === 0
                      ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-400 font-medium'
                      : 'bg-background border-border text-muted-foreground'
                  }`}
                >
                  长期多人通用码
                </button>
              </div>
              <button
                type="button"
                onClick={() => handleOpenQrModal(qrModalLink, qrMaxUses)}
                className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${qrLoading ? 'animate-spin' : ''}`} />
                <span>刷新二维码</span>
              </button>
            </div>

            <div className="p-4 rounded-xl bg-background border border-border flex flex-col items-center space-y-3">
              {qrLoading ? (
                <div className="py-12 text-xs text-muted-foreground">正在生成离线高清绑定二维码...</div>
              ) : qrTicketData?.qr_svg_data_uri ? (
                <>
                  <img
                    src={qrTicketData.qr_svg_data_uri}
                    alt="IM 扫码绑定二维码"
                    className="w-48 h-48 rounded-xl bg-white p-2 border border-border"
                  />
                  <div className="text-center space-y-1">
                    <div className="text-xs font-medium text-foreground">
                      扫码后自动带入：{qrTicketData.expert_name} · {qrTicketData.bound_role_label}
                    </div>
                    {qrTicketData.bound_scope && Object.keys(qrTicketData.bound_scope).length > 0 && (
                      <div className="text-[11px] text-amber-400 font-mono">
                        自动锁定 RLS：{Object.entries(qrTicketData.bound_scope).map(([k, v]) => `${k}=${v}`).join(', ')}
                      </div>
                    )}
                    <div className="text-[11px] text-muted-foreground">
                      企微/微信客服 scene/state 参数：<code className="font-mono text-foreground">{qrTicketData.state_param}</code>
                    </div>
                  </div>
                  {qrModalLink.channel_type === 'weixin_clawbot' && (
                    <div
                      className={`w-full px-3 py-2 rounded-lg border text-xs text-center font-medium ${
                        qrLiveStatus === 'confirmed'
                          ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-400'
                          : qrLiveStatus === 'scaned'
                            ? 'bg-sky-500/15 border-sky-500/40 text-sky-400'
                            : qrLiveStatus === 'expired'
                              ? 'bg-red-500/15 border-red-500/40 text-red-400'
                              : 'bg-surface-100 border-border text-muted-foreground'
                      }`}
                    >
                      {qrLiveStatus === 'confirmed'
                        ? '✅ 微信已扫码登录并自动绑定完成！现在可直接在手机微信对话。'
                        : qrLiveStatus === 'scaned'
                          ? '📱 已扫码，请在手机微信上点击「确认登录」...'
                          : qrLiveStatus === 'expired'
                            ? '⚠️ 二维码已过期，请点击上方「刷新二维码」重新生成。'
                            : '📲 请直接用手机微信扫一扫上方微信官方二维码'}
                    </div>
                  )}
                  {qrTicketData.clawbot_bridge?.hint && (
                    <div className="w-full px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-[11px] text-amber-400">
                      {qrTicketData.clawbot_bridge.hint}
                    </div>
                  )}
                  <div className="flex items-center gap-2 pt-1">
                    <a
                      href={qrTicketData.qr_svg_data_uri}
                      download={`bind_qr_${qrModalLink.channel_type}_${qrTicketData.ticket}.svg`}
                      className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium flex items-center gap-1.5"
                    >
                      <Download className="w-3.5 h-3.5" />
                      <span>下载二维码 SVG</span>
                    </a>
                    <button
                      type="button"
                      onClick={() => navigator.clipboard.writeText(qrTicketData.bind_url || '')}
                      className="px-3 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-foreground text-xs flex items-center gap-1.5"
                    >
                      <Copy className="w-3.5 h-3.5" />
                      <span>复制扫码链接</span>
                    </button>
                  </div>
                </>
              ) : (
                <div className="py-10 text-xs text-red-400">生成二维码失败，请稍后重试</div>
              )}
            </div>

            {/* 已扫码自动绑定的外部用户列表 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-foreground flex items-center gap-1.5">
                  <Users className="w-3.5 h-3.5 text-emerald-400" />
                  <span>已扫码绑定用户 ({qrBindings.length})</span>
                </span>
                <button
                  type="button"
                  onClick={() => loadLinkBindings(qrModalLink.link_id)}
                  className="text-[11px] text-muted-foreground hover:text-foreground underline"
                >
                  刷新列表
                </button>
              </div>
              {qrBindings.length === 0 ? (
                <div className="text-[11px] text-muted-foreground py-2 text-center bg-surface-100 rounded-lg border border-border/60">
                  暂无用户扫码认领，扫码确认后将自动出现在此处
                </div>
              ) : (
                <div className="space-y-1.5 max-h-32 overflow-y-auto pr-1">
                  {qrBindings.map((b) => (
                    <div
                      key={b.binding_id}
                      className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-surface-100 border border-border text-xs"
                    >
                      <div className="truncate">
                        <span className="font-medium text-foreground">{b.external_user_name}</span>
                        <span className="ml-2 text-[11px] text-muted-foreground font-mono">({b.external_user_id})</span>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleRemoveBinding(qrModalLink.link_id, b.binding_id)}
                        className="text-[11px] text-red-400 hover:underline shrink-0 ml-2"
                      >
                        解绑
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 编辑已创建机器人（专家授权 + 访问权限角色/RLS + 基础信息）弹窗 */}
      {editingLink && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => !savingEdit && setEditingLink(null)}
        >
          <div
            className="w-full max-w-2xl rounded-2xl bg-surface-card border border-border shadow-2xl p-5 space-y-4 max-h-[90vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                  <Pencil className="w-4 h-4 text-violet-400" />
                  <span>编辑机器人配置 · {editingLink.target_user_label}</span>
                </h3>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  渠道：{editingLink.channel_label} · 保存后下一条消息立即生效，原对接链接与已绑定的微信扫码关系保持不变。
                </p>
              </div>
              <button
                type="button"
                onClick={() => !savingEdit && setEditingLink(null)}
                className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-surface-200"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSaveEditLink} className="space-y-4">
              {/* 1. 授权数字专家（可多选） */}
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground mb-1.5">
                  1. 授权对话数字专家（可多选：单选则严格锁定单一专家，多选则按问题自动在已授权专家间跨库路由）
                  <span className="ml-2 text-violet-400 font-mono">已选 {editExpertIds.length} 位</span>
                </label>
                <div className="flex flex-wrap gap-2">
                  {agents.map((ag) => {
                    const active = editExpertIds.includes(ag.id);
                    return (
                      <button
                        key={`edit_exp_${ag.id}`}
                        type="button"
                        onClick={() => {
                          setEditExpertIds((prev) =>
                            prev.includes(ag.id) ? prev.filter((id) => id !== ag.id) : [...prev, ag.id]
                          );
                        }}
                        className={`px-2.5 py-1.5 rounded-xl border text-xs flex items-center gap-1.5 transition-colors ${
                          active
                            ? 'bg-violet-500/15 border-violet-500/50 text-foreground font-medium'
                            : 'bg-background border-border text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        <Bot className={`w-3.5 h-3.5 ${active ? 'text-violet-400' : 'opacity-60'}`} />
                        <span>{ag.name}</span>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* 2. 名称与外部 ID */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                    2. 用户 / 群组名称
                  </label>
                  <input
                    type="text"
                    value={editTargetUserLabel}
                    onChange={(e) => setEditTargetUserLabel(e.target.value)}
                    placeholder="如：小虎子 / 会员群"
                    className="w-full px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground focus:outline-none focus:border-violet-500"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                    外部用户 ID（选填）
                  </label>
                  <input
                    type="text"
                    value={editTargetUserId}
                    onChange={(e) => setEditTargetUserId(e.target.value)}
                    placeholder="open_id / userid"
                    className="w-full px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground focus:outline-none focus:border-violet-500"
                  />
                </div>
              </div>

              {/* 3. 访问权限角色与 RLS 行级作用域 */}
              <div className="grid grid-cols-1 md:grid-cols-12 gap-3">
                <div className="md:col-span-5">
                  <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                    3. 锁定数据查询角色（服务端签发，防越权）
                  </label>
                  <select
                    value={editBoundRole}
                    onChange={(e) => setEditBoundRole(e.target.value)}
                    className="w-full px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground focus:outline-none focus:border-violet-500"
                  >
                    {ROLE_OPTIONS.map((r) => (
                      <option key={`edit_role_${r.id}`} value={r.id}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="md:col-span-7">
                  <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                    4. 锁定行级数据作用域 RLS（非总部角色必填）
                  </label>
                  <input
                    type="text"
                    value={editBoundScope}
                    onChange={(e) => setEditBoundScope(e.target.value)}
                    placeholder="region_code=310000 / delegation_id=1052 / owner=u_1001"
                    className="w-full px-3 py-2 rounded-xl bg-background border border-border text-xs text-foreground focus:outline-none focus:border-violet-500"
                  />
                </div>
              </div>

              {/* 4. 记忆提炼与训练开关 */}
              <div className="flex flex-wrap items-center gap-5 pt-1 text-xs text-muted-foreground">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={editMemoryExtract}
                    onChange={(e) => setEditMemoryExtract(e.target.checked)}
                    className="rounded border-border accent-violet-500"
                  />
                  <span>自动提炼用户对话记忆并存储</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={editVectorTrain}
                    onChange={(e) => setEditVectorTrain(e.target.checked)}
                    className="rounded border-border accent-violet-500"
                  />
                  <span>对话记录增量向量化训练</span>
                </label>
                {editBoundRole === 'hq_admin' && (
                  <label className="flex items-center gap-2 cursor-pointer text-amber-400 font-medium">
                    <input
                      type="checkbox"
                      checked={editOwnerMode}
                      onChange={(e) => setEditOwnerMode(e.target.checked)}
                      className="rounded border-border accent-amber-500"
                    />
                    <Crown className="w-3.5 h-3.5" />
                    <span>开启主人全能控制台（解锁运维与改代码，高危操作需微信二次确认）</span>
                  </label>
                )}
              </div>

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-border">
                <button
                  type="button"
                  disabled={savingEdit}
                  onClick={() => setEditingLink(null)}
                  className="px-4 py-1.5 rounded-xl bg-surface-200 hover:bg-surface-300 text-xs text-foreground transition-colors"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={savingEdit || editExpertIds.length === 0}
                  className="px-4 py-1.5 rounded-xl bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-xs font-medium transition-colors"
                >
                  {savingEdit ? '保存中...' : '保存并立即生效'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 服务器资产池 (SSH) 管理弹窗 */}
      {showServerModal && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-surface-card border border-border rounded-2xl max-w-2xl w-full p-5 space-y-4 shadow-xl max-h-[88vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b border-border pb-3">
              <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <Server className="w-4 h-4 text-sky-400" />
                <span>托管服务器资产池（SSH 密钥隔离通道）</span>
              </h3>
              <button
                type="button"
                onClick={() => setShowServerModal(false)}
                className="text-muted-foreground hover:text-foreground"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>私钥与密码均使用 Fernet 对称加密存储，前端绝不回显明文。</span>
              <button
                type="button"
                onClick={() => handleInspectServer('all')}
                className="px-2.5 py-1 rounded-lg bg-sky-500/15 text-sky-400 hover:bg-sky-500/25 flex items-center gap-1"
              >
                <Activity className="w-3.5 h-3.5" />
                <span>一键巡检全部</span>
              </button>
            </div>

            {inspectResult && (
              <pre className="p-3 rounded-xl bg-background border border-border text-[11px] whitespace-pre-wrap text-foreground">
                {inspectResult}
              </pre>
            )}

            <div className="space-y-2">
              {loadingServers ? (
                <div className="text-xs text-muted-foreground py-2">加载服务器列表中...</div>
              ) : servers.length === 0 ? (
                <div className="text-xs text-muted-foreground py-2">暂无托管 SSH 服务器，请通过下方表单添加。</div>
              ) : (
                servers.map((srv) => (
                  <div
                    key={srv.id}
                    className="p-3 rounded-xl bg-background border border-border flex items-center justify-between gap-3 text-xs"
                  >
                    <div>
                      <div className="font-semibold text-foreground flex items-center gap-2">
                        <span>{srv.alias}</span>
                        <span className="px-1.5 py-0.5 rounded bg-surface-200 text-[10px]">{srv.env_tier}</span>
                        <span className="text-[10px] text-muted-foreground">
                          {srv.allow_write ? '读写开放' : '只读白名单保护'}
                        </span>
                      </div>
                      <div className="text-[11px] text-muted-foreground font-mono mt-0.5">
                        {srv.username}@{srv.host}:{srv.port} {srv.default_workdir ? `· ${srv.default_workdir}` : ''}
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => handleInspectServer(srv.alias)}
                        className="px-2 py-1 rounded-lg bg-surface-200 hover:bg-surface-300 text-foreground"
                      >
                        体检
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteServer(srv.alias)}
                        className="p-1.5 rounded-lg text-red-400 hover:bg-red-500/10"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>

            <form onSubmit={handleSaveServer} className="grid grid-cols-2 md:grid-cols-3 gap-2.5 pt-3 border-t border-border text-xs">
              <input
                type="text"
                required
                placeholder="别名 (如 prod / host)"
                value={srvForm.alias}
                onChange={(e) => setSrvForm((p) => ({ ...p, alias: e.target.value }))}
                className="px-2.5 py-1.5 rounded-lg bg-background border border-border text-foreground"
              />
              <input
                type="text"
                required
                placeholder="主机 IP 或域名"
                value={srvForm.host}
                onChange={(e) => setSrvForm((p) => ({ ...p, host: e.target.value }))}
                className="px-2.5 py-1.5 rounded-lg bg-background border border-border text-foreground"
              />
              <input
                type="text"
                placeholder="用户名 (默认 root)"
                value={srvForm.username}
                onChange={(e) => setSrvForm((p) => ({ ...p, username: e.target.value }))}
                className="px-2.5 py-1.5 rounded-lg bg-background border border-border text-foreground"
              />
              <select
                value={srvForm.auth_type}
                onChange={(e) => setSrvForm((p) => ({ ...p, auth_type: e.target.value }))}
                className="px-2.5 py-1.5 rounded-lg bg-background border border-border text-foreground"
              >
                <option value="key_path">私钥文件路径</option>
                <option value="private_key_enc">加密存储私钥内容</option>
                <option value="password_enc">加密存储 SSH 密码</option>
              </select>
              {srvForm.auth_type === 'key_path' ? (
                <input
                  type="text"
                  placeholder="私钥路径 (/root/.ssh/id_ed25519)"
                  value={srvForm.key_path}
                  onChange={(e) => setSrvForm((p) => ({ ...p, key_path: e.target.value }))}
                  className="px-2.5 py-1.5 rounded-lg bg-background border border-border text-foreground"
                />
              ) : (
                <input
                  type="password"
                  placeholder="密码或私钥内容（加密存储）"
                  value={srvForm.credential}
                  onChange={(e) => setSrvForm((p) => ({ ...p, credential: e.target.value }))}
                  className="px-2.5 py-1.5 rounded-lg bg-background border border-border text-foreground"
                />
              )}
              <select
                value={srvForm.env_tier}
                onChange={(e) => setSrvForm((p) => ({ ...p, env_tier: e.target.value }))}
                className="px-2.5 py-1.5 rounded-lg bg-background border border-border text-foreground"
              >
                <option value="prod">prod（生产只读门禁）</option>
                <option value="host">host（宿主机）</option>
                <option value="staging">staging（预发）</option>
                <option value="dev">dev（开发）</option>
              </select>
              <input
                type="text"
                placeholder="默认项目路径（选填）"
                value={srvForm.default_workdir}
                onChange={(e) => setSrvForm((p) => ({ ...p, default_workdir: e.target.value }))}
                className="px-2.5 py-1.5 rounded-lg bg-background border border-border text-foreground col-span-2"
              />
              <button
                type="submit"
                className="px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-medium"
              >
                保存托管服务器
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

