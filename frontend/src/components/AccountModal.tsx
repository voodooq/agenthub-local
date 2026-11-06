import React, { useState, useEffect } from 'react';
import {
  X,
  Plus,
  Trash2,
  Activity,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Clock,
  Terminal,
  ExternalLink,
  Copy,
  Sparkles,
  Lock,
  Cpu,
  FileText,
  Shield,
  Key,
  Globe,
  Settings,
  HelpCircle,
  ArrowRight,
  Check,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';
import { AuditLogItem } from '@/types';

interface AccountItem {
  id: number;
  name: string;
  auth_type: string;
  email?: string;
  avatar_url?: string;
  priority: number;
  status: string;
  cooldown_until?: string;
  current_rpm: number;
  rpm_limit: number;
  rpm_percent: number;
  today_used: number;
  rpd_limit: number;
  rpd_percent: number;
  total_tokens: number;
  last_latency_ms: number;
  is_warning: boolean;
  is_exhausted: boolean;
  cli_home_dir?: string;
  is_estimated?: boolean;
}

interface DashboardStats {
  accounts: AccountItem[];
  summary: {
    total_accounts: number;
    active_accounts: number;
    cooldown_accounts: number;
    total_today_used: number;
    total_tokens_all: number;
    is_estimated?: boolean;
  };
}

interface AccountModalProps {
  isOpen: boolean;
  onClose: () => void;
  onRefresh?: () => void;
}

export function AccountModal({ isOpen, onClose, onRefresh }: AccountModalProps) {
  const [data, setData] = useState<DashboardStats | null>(null);
  const [activeTab, setActiveTab] = useState<'accounts' | 'add_account' | 'audit'>('accounts');
  const [addMode, setAddMode] = useState<'cli' | 'api_key' | 'standard_oauth'>('cli');
  const [loading, setLoading] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<{ id: number; success: boolean; msg: string } | null>(null);
  const [purgingAll, setPurgingAll] = useState(false);

  // 1. 本地 CLI 登录状态
  const [newAlias, setNewAlias] = useState('');
  const [newPriority, setNewPriority] = useState(1);
  const [addingCli, setAddingCli] = useState(false);
  const [pollingAlias, setPollingAlias] = useState<string | null>(null);
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [authCommand, setAuthCommand] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [cliAuthCode, setCliAuthCode] = useState('');
  const [submittingCliCode, setSubmittingCliCode] = useState(false);
  const [importingSystemCli, setImportingSystemCli] = useState(false);
  const [openingTerminal, setOpeningTerminal] = useState(false);

  // 2. Google AI Studio 官方免费 API Key 状态
  const [apiKeyName, setApiKeyName] = useState('');
  const [apiKeyValue, setApiKeyValue] = useState('');
  const [apiKeyPriority, setApiKeyPriority] = useState(1);
  const [apiKeyLoading, setApiKeyLoading] = useState(false);

  // 3. 自建 Google Cloud OAuth 配置与状态
  const [oauthConfig, setOauthConfig] = useState<{ is_configured: boolean; client_id: string; redirect_uri: string } | null>(null);
  const [clientIdInput, setClientIdInput] = useState('');
  const [clientSecretInput, setClientSecretInput] = useState('');
  const [showConfigForm, setShowConfigForm] = useState(false);
  const [oauthLoading, setOauthLoading] = useState(false);

  // 通用反馈信息
  const [feedbackMsg, setFeedbackMsg] = useState<{ success: boolean; msg: string } | null>(null);

  // 审计日志状态
  const [auditLogs, setAuditLogs] = useState<AuditLogItem[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);

  // 拉取账号列表及容量统计
  const fetchAccounts = async () => {
    try {
      setLoading(true);
      const res = await apiFetch('/api/v1/quota/stats');
      if (res.ok) {
        const stats = await res.json();
        setData(stats);
      }
    } catch (e) {
      console.error('获取账号容量统计失败:', e);
    } finally {
      setLoading(false);
    }
  };

  // 拉取 OAuth 客户端状态
  const fetchOAuthConfig = async () => {
    try {
      const res = await apiFetch('/api/v1/accounts/oauth/config');
      if (res.ok) {
        const cfg = await res.json();
        setOauthConfig(cfg);
      }
    } catch (e) {
      console.error('获取 OAuth 配置状态失败:', e);
    }
  };

  // 拉取审计日志
  const fetchAuditLogs = async () => {
    try {
      setAuditLoading(true);
      const res = await apiFetch('/api/v1/audit/logs?limit=50');
      if (res.ok) {
        const logs: AuditLogItem[] = await res.json();
        setAuditLogs(logs);
      }
    } catch (e) {
      console.error('获取审计日志失败:', e);
    } finally {
      setAuditLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchAccounts();
      fetchOAuthConfig();
      if (activeTab === 'audit') {
        fetchAuditLogs();
      }
    }
  }, [isOpen, activeTab]);

  // 监听浏览器跨窗口 OAuth 回调 postMessage
  useEffect(() => {
    const handleOAuthMessage = (event: MessageEvent) => {
      if (event.data?.type === 'GOOGLE_OAUTH_SUCCESS') {
        setFeedbackMsg({
          success: true,
          msg: `Google 账号 [${event.data.email}] 授权成功，已加入账号池！`,
        });
        fetchAccounts();
        if (onRefresh) onRefresh();
      } else if (event.data?.type === 'GOOGLE_OAUTH_FAILED') {
        setFeedbackMsg({
          success: false,
          msg: `授权失败: ${event.data.error || '用户取消授权'}`,
        });
      }
    };

    window.addEventListener('message', handleOAuthMessage);
    return () => window.removeEventListener('message', handleOAuthMessage);
  }, []);

  // ──────────────────── 1. 本地 CLI 模式添加与授权 ────────────────────
  const handleAddCliAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newAlias.trim()) {
      setFeedbackMsg({ success: false, msg: '请输入合法的账号别名（仅支持字母、数字、下划线）' });
      return;
    }

    try {
      setAddingCli(true);
      setFeedbackMsg(null);
      setAuthUrl(null);
      setCliAuthCode('');
      const res = await apiFetch('/api/v1/accounts/gemini-cli/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          alias: newAlias.trim(),
          priority: Number(newPriority),
        }),
      });

      const resData = await res.json();
      if (!res.ok) {
        setFeedbackMsg({ success: false, msg: resData.detail || '拉起官方授权失败' });
        setAddingCli(false);
        return;
      }

      setFeedbackMsg({
        success: true,
        msg: `已启动系统官方授权流程！系统浏览器已唤起，请选择账号登录，随后复制页面给出的代码并粘贴到下方提交。`,
      });
      setPollingAlias(newAlias.trim());
    } catch (err: any) {
      setFeedbackMsg({ success: false, msg: err.message || '网络请求异常' });
      setAddingCli(false);
    }
  };

  // 轮询 CLI 授权结果
  useEffect(() => {
    if (!pollingAlias) return;

    const timer = setInterval(async () => {
      try {
        const res = await apiFetch(`/api/v1/accounts/gemini-cli/login-status/${pollingAlias}`);
        if (res.ok) {
          const status = await res.json();
          if (status.auth_url) setAuthUrl(status.auth_url);
          if (status.auth_command) setAuthCommand(status.auth_command);
          if (status.logged_in) {
            clearInterval(timer);
            setPollingAlias(null);
            setAddingCli(false);
            setAuthUrl(null);
            setAuthCommand(null);
            setCliAuthCode('');
            setFeedbackMsg({
              success: true,
              msg: `Google 账号 [${pollingAlias}] 授权成功，已加入算力池！`,
            });
            setNewAlias('');
            fetchAccounts();
            if (onRefresh) onRefresh();
          }
        }
      } catch (err) {
        console.warn('轮询登录状态异常:', err);
      }
    }, 2000);

    return () => clearInterval(timer);
  }, [pollingAlias]);

  // 提交 Google 授权代码给 CLI 进程
  const handleSubmitCliCode = async () => {
    const targetAlias = pollingAlias || newAlias.trim();
    if (!targetAlias || !cliAuthCode.trim()) return;

    try {
      setSubmittingCliCode(true);
      const res = await apiFetch('/api/v1/accounts/gemini-cli/submit-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          alias: targetAlias,
          code: cliAuthCode.trim(),
        }),
      });
      const resData = await res.json();
      if (res.ok) {
        setFeedbackMsg({ success: true, msg: '授权码已提交给官方 CLI，正在完成凭证持久化...' });
        setCliAuthCode('');
      } else {
        setFeedbackMsg({ success: false, msg: resData.detail || '提交授权码失败' });
      }
    } catch (e: any) {
      setFeedbackMsg({ success: false, msg: e.message || '提交异常' });
    } finally {
      setSubmittingCliCode(false);
    }
  };

  // 一键纳管宿主机已登录的 Antigravity 原生引擎
  const handleImportSystemCli = async () => {
    try {
      setImportingSystemCli(true);
      setFeedbackMsg(null);
      const res = await apiFetch('/api/v1/accounts/gemini-cli/import-system', {
        method: 'POST',
      });
      const data = await res.json();
      if (res.ok) {
        setFeedbackMsg({
          success: true,
          msg: data.message || '已成功纳管宿主机 Antigravity 原生引擎入池！',
        });
        fetchAccounts();
        if (onRefresh) onRefresh();
      } else {
        setFeedbackMsg({ success: false, msg: data.detail || '纳管宿主机原生引擎失败' });
      }
    } catch (e: any) {
      setFeedbackMsg({ success: false, msg: e.message || '网络连接异常' });
    } finally {
      setImportingSystemCli(false);
    }
  };

  // 呼出系统交互终端
  const handleOpenTerminal = async () => {
    const targetAlias = pollingAlias || newAlias.trim();
    if (!targetAlias) {
      setFeedbackMsg({ success: false, msg: '请先填写账号别名' });
      return;
    }
    try {
      setOpeningTerminal(true);
      const res = await apiFetch('/api/v1/accounts/gemini-cli/open-terminal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alias: targetAlias }),
      });
      const data = await res.json();
      if (res.ok) {
        setFeedbackMsg({
          success: true,
          msg: data.message || '已在系统桌面呼出 PowerShell 终端，按回车即可完成！',
        });
      } else {
        setFeedbackMsg({ success: false, msg: data.detail || '呼出交互终端失败' });
      }
    } catch (e: any) {
      setFeedbackMsg({ success: false, msg: e.message || '网络异常' });
    } finally {
      setOpeningTerminal(false);
    }
  };

  // ──────────────────── 2. 添加 Google AI Studio 官方免费 API Key ────────────────────
  const handleAddApiKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKeyValue.trim() || !apiKeyName.trim()) {
      setFeedbackMsg({ success: false, msg: '请填写账号名称与 Google API Key' });
      return;
    }

    try {
      setApiKeyLoading(true);
      setFeedbackMsg(null);
      const res = await apiFetch('/api/v1/accounts/api-key/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: apiKeyName.trim(),
          api_key: apiKeyValue.trim(),
          priority: Number(apiKeyPriority),
        }),
      });
      const resData = await res.json();
      if (res.ok) {
        setFeedbackMsg({ success: true, msg: `官方免费 API Key [${apiKeyName.trim()}] 已成功加入算力池！` });
        setApiKeyName('');
        setApiKeyValue('');
        fetchAccounts();
        if (onRefresh) onRefresh();
      } else {
        setFeedbackMsg({ success: false, msg: resData.detail || '添加 API Key 失败' });
      }
    } catch (err: any) {
      setFeedbackMsg({ success: false, msg: err.message || '网络请求异常' });
    } finally {
      setApiKeyLoading(false);
    }
  };

  // ──────────────────── 3. 自建 Google Cloud OAuth ────────────────────
  const handleLaunchGoogleOAuth = async () => {
    try {
      setOauthLoading(true);
      setFeedbackMsg(null);
      const res = await apiFetch('/api/v1/accounts/oauth/google/url');
      const resData = await res.json();
      if (!res.ok) {
        setFeedbackMsg({ success: false, msg: resData.detail || '拉取授权链接失败' });
        setShowConfigForm(true);
        return;
      }

      const url = resData.authorization_url;
      const width = 600;
      const height = 750;
      const left = window.screen.width / 2 - width / 2;
      const top = window.screen.height / 2 - height / 2;
      window.open(
        url,
        'GoogleAuthWindow',
        `width=${width},height=${height},top=${top},left=${left},toolbar=no,menubar=no`
      );
      setFeedbackMsg({
        success: true,
        msg: '已弹出 Google 官方授权窗口，请在弹窗中选择任何 Gmail 账号登录并点击同意...',
      });
    } catch (err: any) {
      setFeedbackMsg({ success: false, msg: err.message || '网络请求异常' });
    } finally {
      setOauthLoading(false);
    }
  };

  const handleSaveOAuthConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!clientIdInput.trim() || !clientSecretInput.trim()) {
      setFeedbackMsg({ success: false, msg: '请输入合法的 Client ID 和 Client Secret' });
      return;
    }

    try {
      setOauthLoading(true);
      const res = await apiFetch('/api/v1/accounts/oauth/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_id: clientIdInput.trim(),
          client_secret: clientSecretInput.trim(),
        }),
      });
      if (res.ok) {
        setFeedbackMsg({ success: true, msg: 'Google OAuth 凭据配置成功保存并已持久化！' });
        setShowConfigForm(false);
        fetchOAuthConfig();
      } else {
        const err = await res.json();
        setFeedbackMsg({ success: false, msg: err.detail || '保存失败' });
      }
    } catch (err: any) {
      setFeedbackMsg({ success: false, msg: err.message || '保存配置异常' });
    } finally {
      setOauthLoading(false);
    }
  };

  // ──────────────────── 4. 账号维护与清理 ────────────────────
  const handleRecoverAccount = async (id: number) => {
    try {
      const res = await apiFetch(`/api/v1/accounts/${id}/recover`, { method: 'POST' });
      const resData = await res.json();
      if (res.ok) {
        setTestResult({
          id,
          success: true,
          msg: resData.message || '已成功恢复活跃状态',
        });
        await fetchAccounts();
        if (onRefresh) onRefresh();
      }
    } catch (e: any) {
      console.error('解除冷却失败:', e);
    }
  };

  const handleTestAccount = async (id: number) => {
    setTestingId(id);
    setTestResult(null);
    try {
      const res = await apiFetch(`/api/v1/accounts/${id}/test`, { method: 'POST' });
      const resData = await res.json();
      if (res.ok) {
        setTestResult({
          id,
          success: resData.logged_in !== false,
          msg: resData.message || '账号凭据测试正常',
        });
        await fetchAccounts();
        if (onRefresh) onRefresh();
      } else {
        setTestResult({
          id,
          success: false,
          msg: resData.detail || '测试连通性失败',
        });
      }
    } catch (e: any) {
      setTestResult({
        id,
        success: false,
        msg: e.message || '测试异常',
      });
    } finally {
      setTestingId(null);
    }
  };

  // 彻底删除账号（级联清理本地目录和缓存）
  const handleDelete = async (id: number, name: string) => {
    if (!window.confirm(`确定要从算力池中彻底移除账号 [${name}] 吗？\n该账号的本地隔离目录及所有缓存凭据将被物理删除。`)) return;
    try {
      const res = await apiFetch(`/api/v1/accounts/${id}`, { method: 'DELETE' });
      if (res.ok) {
        await fetchAccounts();
        if (onRefresh) onRefresh();
      }
    } catch (e) {
      console.error('删除账号失败:', e);
    }
  };

  // 一键彻底清空所有账号及本地文件
  const handlePurgeAll = async () => {
    if (!window.confirm('⚠️ 警告：确定要彻底清空算力池中的所有账号、本地隔离目录及所有缓存凭据吗？\n所有历史记录将完全清零，不可恢复！')) return;
    try {
      setPurgingAll(true);
      const res = await apiFetch('/api/v1/accounts/purge/all', { method: 'DELETE' });
      const resData = await res.json();
      if (res.ok) {
        setFeedbackMsg({ success: true, msg: resData.message || '算力池所有账号与本地缓存已全部彻底清空！' });
        await fetchAccounts();
        if (onRefresh) onRefresh();
      }
    } catch (e: any) {
      console.error('清空算力池失败:', e);
    } finally {
      setPurgingAll(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md animate-fadeIn">
      <div className="relative w-full max-w-4xl h-[90vh] bg-surface-100 border border-border/80 rounded-2xl shadow-2xl flex flex-col overflow-hidden text-slate-200">
        {/* 顶部标题栏 */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border/80 bg-surface-200/40">
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-600 flex items-center justify-center shadow-lg shadow-blue-500/20">
              <Shield className="w-5 h-5 text-white" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h2 className="text-base font-bold text-white tracking-wide">Google 账号池与算力中心</h2>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/30 font-medium">
                  多引擎智能避险调度
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                支持 Antigravity CLI 隔离运行与官方个人免费通道，智能容量监控与 429 秒级无感轮转
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-surface-300/40 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 导航 Tabs */}
        <div className="flex items-center space-x-2 px-6 border-b border-border/80 bg-surface-200/20">
          <button
            onClick={() => setActiveTab('accounts')}
            className={`flex items-center space-x-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all ${
              activeTab === 'accounts'
                ? 'border-blue-500 text-blue-400 font-semibold'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Cpu className="w-4 h-4" />
            <span>账号池与容量监控</span>
            {data?.summary && (
              <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-blue-500/20 text-blue-300">
                {data.summary.total_accounts}
              </span>
            )}
          </button>

          <button
            onClick={() => {
              setActiveTab('add_account');
              setFeedbackMsg(null);
            }}
            className={`flex items-center space-x-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all ${
              activeTab === 'add_account'
                ? 'border-blue-500 text-blue-400 font-semibold'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Plus className="w-4 h-4" />
            <span>添加 Google 账号 (分步向导)</span>
          </button>

          <button
            onClick={() => setActiveTab('audit')}
            className={`flex items-center space-x-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-all ${
              activeTab === 'audit'
                ? 'border-blue-500 text-blue-400 font-semibold'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <FileText className="w-4 h-4" />
            <span>安全与审计日志</span>
          </button>
        </div>

        {/* 内容主体 */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* ──────────────────── TAB 1: 账号池与容量监控 ──────────────────── */}
          {activeTab === 'accounts' && (
            <div className="space-y-5">
              {/* 配额与容量说明提示 */}
              <div className="p-3.5 rounded-xl bg-blue-950/20 border border-blue-500/20 flex items-start justify-between text-xs text-slate-300 leading-relaxed">
                <div className="flex items-start space-x-2.5">
                  <Sparkles className="w-4 h-4 text-blue-400 mt-0.5 shrink-0" />
                  <div>
                    <strong className="text-white">容量监控与避险说明：</strong>
                    系统对每个账号的 RPM 与每日请求进行滑动窗口实时监控。当单账号达到限流阈值时，调度器将
                    <strong className="text-blue-300"> 自动秒级无感切换至备用就绪账号</strong>，保障对话永不断连。
                  </div>
                </div>

                {data?.summary && data.summary.total_accounts > 0 && (
                  <button
                    type="button"
                    onClick={handlePurgeAll}
                    disabled={purgingAll}
                    className="ml-4 px-3 py-1 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 text-xs shrink-0 flex items-center space-x-1.5 transition-colors"
                    title="彻底清空算力池所有账号，并物理删除宿主机上的所有隔离文件与缓存"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>{purgingAll ? '正在清空...' : '彻底清空算力池'}</span>
                  </button>
                )}
              </div>

              {/* 顶部统计卡片 */}
              {data?.summary && (
                <div className="grid grid-cols-4 gap-4">
                  <div className="p-4 rounded-xl bg-surface-200/40 border border-border/80">
                    <div className="text-xs text-slate-400 font-medium">总算力节点</div>
                    <div className="text-2xl font-bold text-white mt-1">{data.summary.total_accounts}</div>
                  </div>
                  <div className="p-4 rounded-xl bg-surface-200/40 border border-border/80">
                    <div className="text-xs text-emerald-400 font-medium">就绪活跃节点</div>
                    <div className="text-2xl font-bold text-emerald-400 mt-1">{data.summary.active_accounts}</div>
                  </div>
                  <div className="p-4 rounded-xl bg-surface-200/40 border border-border/80">
                    <div className="text-xs text-amber-400 font-medium">冷却中节点 (429避险)</div>
                    <div className="text-2xl font-bold text-amber-400 mt-1">{data.summary.cooldown_accounts}</div>
                  </div>
                  <div className="p-4 rounded-xl bg-surface-200/40 border border-border/80">
                    <div className="text-xs text-blue-400 font-medium">今日累计调用 (估算)</div>
                    <div className="text-2xl font-bold text-blue-400 mt-1">
                      {data.summary.total_today_used} <span className="text-xs font-normal text-slate-400">次</span>
                    </div>
                  </div>
                </div>
              )}

              {/* 账号列表 */}
              <div className="space-y-3">
                {loading && (
                  <div className="p-8 text-center text-slate-400 text-xs flex items-center justify-center space-x-2">
                    <RefreshCw className="w-4 h-4 animate-spin text-blue-400" />
                    <span>正在获取实时容量指标...</span>
                  </div>
                )}

                {!loading && (!data?.accounts || data.accounts.length === 0) && (
                  <div className="p-12 text-center rounded-2xl border border-dashed border-border/80 bg-surface-200/20 space-y-3">
                    <Cpu className="w-10 h-10 text-slate-600 mx-auto" />
                    <div className="text-slate-300 font-medium text-sm">算力池当前为空</div>
                    <p className="text-xs text-slate-500 max-w-md mx-auto">
                      尚未绑定任何 Google 账号。请点击上方「添加 Google 账号 (分步向导)」，选择合适您的通道完成添加。
                    </p>
                    <button
                      onClick={() => setActiveTab('add_account')}
                      className="px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium transition-colors inline-flex items-center space-x-1.5"
                    >
                      <Plus className="w-4 h-4" />
                      <span>立即添加首个账号</span>
                    </button>
                  </div>
                )}

                {data?.accounts?.map((acc) => (
                  <div
                    key={acc.id}
                    className="p-4 rounded-xl bg-surface-200/50 border border-border/80 hover:border-slate-600 transition-all space-y-3 shadow-sm"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-3">
                        <div className="w-9 h-9 rounded-full bg-slate-800 border border-border flex items-center justify-center font-bold text-xs text-blue-400 overflow-hidden">
                          {acc.avatar_url ? (
                            <img src={acc.avatar_url} alt="" className="w-full h-full object-cover" />
                          ) : (
                            acc.name.substring(0, 2).toUpperCase()
                          )}
                        </div>
                        <div>
                          <div className="flex items-center space-x-2">
                            <span className="font-semibold text-white text-sm">{acc.name}</span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
                              Priority: {acc.priority}
                            </span>
                            <span className="text-[10px] px-2 py-0.5 rounded bg-purple-500/10 text-purple-300 border border-purple-500/20">
                              {acc.auth_type === 'api_key' ? '官方 API Key' : (acc.auth_type || '').startsWith('oauth') ? '标准 OAuth' : 'CLI 模式'}
                            </span>
                            {acc.status === 'active' ? (
                              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center space-x-1">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                                <span>就绪</span>
                              </span>
                            ) : (
                              <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 flex items-center space-x-1">
                                <Clock className="w-2.5 h-2.5" />
                                <span>冷却中 ({acc.cooldown_until || '倒计时中'})</span>
                              </span>
                            )}
                          </div>
                          <div className="text-xs text-slate-400 mt-0.5 flex items-center space-x-3">
                            <span>{acc.email || 'Google 账号'}</span>
                            <span className="text-slate-600">·</span>
                            <span className="font-mono text-[11px] text-slate-500">
                              {acc.cli_home_dir ? `目录: ${acc.cli_home_dir.split('\\').slice(-2).join('\\')}` : '官方直连通道'}
                            </span>
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center space-x-2">
                        {acc.status !== 'active' && (
                          <button
                            onClick={() => handleRecoverAccount(acc.id)}
                            className="px-2.5 py-1 text-xs rounded-lg border border-amber-500/40 text-amber-300 hover:text-amber-200 hover:bg-amber-500/10 transition-colors flex items-center space-x-1"
                            title="解除冷却状态，立即重新投入算力池调度"
                          >
                            <span>⚡ 解除冷却</span>
                          </button>
                        )}
                        <button
                          onClick={() => handleTestAccount(acc.id)}
                          disabled={testingId === acc.id}
                          className="px-2.5 py-1 text-xs rounded-lg border border-border/80 text-slate-300 hover:text-white hover:bg-surface-300/40 transition-colors flex items-center space-x-1.5 disabled:opacity-50"
                        >
                          <Activity className="w-3.5 h-3.5 text-blue-400" />
                          <span>{testingId === acc.id ? '测试中...' : '探测连通性'}</span>
                        </button>
                        <button
                          onClick={() => handleDelete(acc.id, acc.name)}
                          className="p-1.5 text-slate-500 hover:text-rose-400 rounded-lg hover:bg-rose-500/10 transition-colors"
                          title="彻底删除该账号及其本地隔离文件"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>

                    {/* 容量水位条 */}
                    <div className="grid grid-cols-2 gap-4 pt-2 border-t border-border/40 text-xs">
                      <div>
                        <div className="flex justify-between text-slate-400 mb-1">
                          <span>每分钟频次 (RPM): <strong className="text-slate-200">{acc.current_rpm}</strong> / {acc.rpm_limit}</span>
                          <span className={acc.rpm_percent >= 90 ? 'text-amber-400 font-bold' : 'text-slate-400'}>
                            {acc.rpm_percent}%
                          </span>
                        </div>
                        <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all duration-300 ${
                              acc.rpm_percent >= 90 ? 'bg-amber-400' : 'bg-blue-500'
                            }`}
                            style={{ width: `${Math.min(100, acc.rpm_percent)}%` }}
                          ></div>
                        </div>
                      </div>

                      <div>
                        <div className="flex justify-between text-slate-400 mb-1">
                          <span>今日估算调用: <strong className="text-slate-200">{acc.today_used}</strong> / {acc.rpd_limit}</span>
                          <span className="text-slate-400">消耗 Token: {acc.total_tokens.toLocaleString()}</span>
                        </div>
                        <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                          <div
                            className="h-full rounded-full bg-indigo-500 transition-all duration-300"
                            style={{ width: `${Math.min(100, acc.rpd_percent)}%` }}
                          ></div>
                        </div>
                      </div>
                    </div>

                    {testResult && testResult.id === acc.id && (
                      <div
                        className={`p-2.5 rounded-lg text-xs flex items-center space-x-2 animate-fadeIn ${
                          testResult.success
                            ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20'
                            : 'bg-rose-500/10 text-rose-300 border border-rose-500/20'
                        }`}
                      >
                        {testResult.success ? (
                          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                        ) : (
                          <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
                        )}
                        <span>{testResult.msg}</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ──────────────────── TAB 2: 添加 Google 账号 (分步向导) ──────────────────── */}
          {activeTab === 'add_account' && (
            <div className="max-w-2xl mx-auto space-y-6 py-2">
              {/* 子通道选择卡 */}
              <div className="grid grid-cols-3 gap-2.5 p-1.5 bg-surface-200/50 rounded-2xl border border-border/60">
                <button
                  type="button"
                  onClick={() => {
                    setAddMode('cli');
                    setFeedbackMsg(null);
                  }}
                  className={`py-2.5 px-3 rounded-xl text-xs font-semibold transition-all flex flex-col items-center space-y-1 ${
                    addMode === 'cli'
                      ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-surface-200/60'
                  }`}
                >
                  <div className="flex items-center space-x-1.5">
                    <Terminal className="w-3.5 h-3.5" />
                    <span>本地 CLI 官方通道</span>
                  </div>
                  <span className="text-[10px] font-normal opacity-80">免 API Key · 走 Antigravity 原生引擎</span>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setAddMode('api_key');
                    setFeedbackMsg(null);
                  }}
                  className={`py-2.5 px-3 rounded-xl text-xs font-semibold transition-all flex flex-col items-center space-y-1 ${
                    addMode === 'api_key'
                      ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-surface-200/60'
                  }`}
                >
                  <div className="flex items-center space-x-1.5">
                    <Key className="w-3.5 h-3.5" />
                    <span>官方免费 API Key</span>
                  </div>
                  <span className="text-[10px] font-normal opacity-80">个人全免费 · 免客户端 · 秒级可用</span>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setAddMode('standard_oauth');
                    setFeedbackMsg(null);
                  }}
                  className={`py-2.5 px-3 rounded-xl text-xs font-semibold transition-all flex flex-col items-center space-y-1 ${
                    addMode === 'standard_oauth'
                      ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-surface-200/60'
                  }`}
                >
                  <div className="flex items-center space-x-1.5">
                    <Globe className="w-3.5 h-3.5" />
                    <span>自建 Google OAuth</span>
                  </div>
                  <span className="text-[10px] font-normal opacity-80">Google Cloud 开发者 · 单点登录</span>
                </button>
              </div>

              {/* 实时反馈提示 */}
              {feedbackMsg && (
                <div
                  className={`p-4 rounded-xl text-xs flex items-start space-x-3 animate-fadeIn ${
                    feedbackMsg.success
                      ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20'
                      : 'bg-rose-500/10 text-rose-300 border border-rose-500/20'
                  }`}
                >
                  {feedbackMsg.success ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 mt-0.5 shrink-0" />
                  ) : (
                    <AlertTriangle className="w-4 h-4 text-rose-400 mt-0.5 shrink-0" />
                  )}
                  <span className="leading-relaxed">{feedbackMsg.msg}</span>
                </div>
              )}

              {/* ──────────────────── 模式 1: 本地 CLI 官方通道 (Antigravity) ──────────────────── */}
              {addMode === 'cli' && (
                <div className="space-y-5">
                  {/* ⚡ 宿主机原生引擎一键秒纳管（免登录） */}
                  <div className="p-4 rounded-2xl bg-gradient-to-r from-emerald-950/40 via-surface-200/50 to-blue-950/40 border border-emerald-500/40 space-y-2.5 animate-fadeIn">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2 text-xs font-bold text-emerald-300">
                        <Sparkles className="w-4 h-4 text-emerald-400" />
                        <span>检测到本机已拥有 Antigravity 官方环境</span>
                      </div>
                      <span className="text-[10px] bg-emerald-500/20 text-emerald-300 px-2 py-0.5 rounded-full border border-emerald-500/30">
                        免认证 · 零配置 · 秒级可用
                      </span>
                    </div>
                    <p className="text-[11px] text-slate-300 leading-relaxed">
                      系统已自动探测到本机官方 Antigravity 引擎与环境。无需重新打开浏览器授权或复制代码，可直接一键将宿主机原生算力纳管入池：
                    </p>
                    <button
                      type="button"
                      onClick={handleImportSystemCli}
                      disabled={importingSystemCli}
                      className="w-full py-2.5 px-4 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-semibold text-xs transition-all shadow-md shadow-emerald-600/20 flex items-center justify-center space-x-2 disabled:opacity-50"
                    >
                      {importingSystemCli ? (
                        <>
                          <RefreshCw className="w-4 h-4 animate-spin" />
                          <span>正在秒级纳管本机原生引擎...</span>
                        </>
                      ) : (
                        <>
                          <Sparkles className="w-4 h-4 text-amber-300" />
                          <span>⚡ 一键纳管本机 Antigravity 原生引擎（强烈推荐）</span>
                        </>
                      )}
                    </button>
                  </div>
                  {/* 四步引导流程条 */}
                  <div className="p-4 rounded-2xl bg-surface-200/40 border border-border/80 space-y-3">
                    <div className="flex items-center justify-between text-xs font-semibold text-white">
                      <span className="flex items-center space-x-1.5">
                        <Terminal className="w-4 h-4 text-blue-400" />
                        <span>操作指引：本地 CLI 官方通道完整 4 步向导</span>
                      </span>
                      <span className="text-[11px] text-blue-300 bg-blue-500/10 px-2 py-0.5 rounded-full border border-blue-500/20">
                        原生 Antigravity 引擎
                      </span>
                    </div>

                    <div className="grid grid-cols-4 gap-2 text-[11px]">
                      <div className="p-2.5 rounded-xl bg-surface-100 border border-border/60 space-y-1">
                        <div className="font-bold text-blue-400 flex items-center space-x-1">
                          <span className="w-4 h-4 rounded-full bg-blue-500/20 text-blue-300 flex items-center justify-center text-[10px]">1</span>
                          <span>填写别名</span>
                        </div>
                        <p className="text-slate-400 leading-tight">输入账号别名并点击下方蓝色拉起按钮</p>
                      </div>

                      <div className="p-2.5 rounded-xl bg-surface-100 border border-border/60 space-y-1">
                        <div className="font-bold text-indigo-400 flex items-center space-x-1">
                          <span className="w-4 h-4 rounded-full bg-indigo-500/20 text-indigo-300 flex items-center justify-center text-[10px]">2</span>
                          <span>浏览器授权</span>
                        </div>
                        <p className="text-slate-400 leading-tight">在系统弹出的 Google 窗口选择账号并同意</p>
                      </div>

                      <div className="p-2.5 rounded-xl bg-surface-100 border border-border/60 space-y-1">
                        <div className="font-bold text-purple-400 flex items-center space-x-1">
                          <span className="w-4 h-4 rounded-full bg-purple-500/20 text-purple-300 flex items-center justify-center text-[10px]">3</span>
                          <span>复制授权码</span>
                        </div>
                        <p className="text-slate-400 leading-tight">复制页面上以 4/0A... 开头的代码</p>
                      </div>

                      <div className="p-2.5 rounded-xl bg-surface-100 border border-border/60 space-y-1">
                        <div className="font-bold text-emerald-400 flex items-center space-x-1">
                          <span className="w-4 h-4 rounded-full bg-emerald-500/20 text-emerald-300 flex items-center justify-center text-[10px]">4</span>
                          <span>提交秒入池</span>
                        </div>
                        <p className="text-slate-400 leading-tight">粘贴代码至下方输入框提交，自动完成</p>
                      </div>
                    </div>
                  </div>

                  {/* 发起表单 */}
                  <form onSubmit={handleAddCliAccount} className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-xs font-medium text-slate-300 mb-1.5">
                          第 1 步：账号别名 (用于调度区分)
                        </label>
                        <input
                          type="text"
                          required
                          placeholder="例如：my-account 或 work-gmail"
                          value={newAlias}
                          onChange={(e) => setNewAlias(e.target.value)}
                          disabled={addingCli}
                          className="w-full px-3.5 py-2.5 rounded-xl bg-surface-200/50 border border-border/80 text-white placeholder-slate-500 text-xs focus:outline-none focus:border-blue-500 disabled:opacity-50"
                        />
                      </div>

                      <div>
                        <label className="block text-xs font-medium text-slate-300 mb-1.5">
                          调度优先级 (数值越小越优先)
                        </label>
                        <input
                          type="number"
                          min={1}
                          max={100}
                          value={newPriority}
                          onChange={(e) => setNewPriority(Number(e.target.value))}
                          disabled={addingCli}
                          className="w-full px-3.5 py-2.5 rounded-xl bg-surface-200/50 border border-border/80 text-white text-xs focus:outline-none focus:border-blue-500 disabled:opacity-50"
                        />
                      </div>
                    </div>

                    <button
                      type="submit"
                      disabled={addingCli}
                      className="w-full py-3 px-4 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-medium text-xs transition-all shadow-lg shadow-blue-500/20 flex items-center justify-center space-x-2 disabled:opacity-50"
                    >
                      {addingCli ? (
                        <>
                          <RefreshCw className="w-4 h-4 animate-spin" />
                          <span>正在拉起官方授权进程...</span>
                        </>
                      ) : (
                        <>
                          <Terminal className="w-4 h-4" />
                          <span>👉 点击拉起 Google 官方客户端授权 (打开系统浏览器)</span>
                        </>
                      )}
                    </button>
                  </form>

                  {/* 步骤 2 补充按钮：手动点击打开链接 */}
                  {authUrl && (
                    <div className="p-4 rounded-xl bg-blue-950/40 border border-blue-500/40 space-y-2.5 animate-fadeIn">
                      <div className="text-xs font-semibold text-white flex items-center space-x-2">
                        <Sparkles className="w-4 h-4 text-amber-300" />
                        <span>浏览器未自动打开？请点击下方直达链接</span>
                      </div>
                      <a
                        href={authUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center space-x-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium transition-colors"
                      >
                        <span>👉 手动在新窗口中打开 Google 授权登录</span>
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    </div>
                  )}

                  {/* 步骤 3 & 4：核心提交授权码输入区 */}
                  {(authUrl || addingCli || pollingAlias) && (
                    <div className="p-4 rounded-xl bg-purple-950/40 border border-purple-500/50 space-y-3 animate-fadeIn">
                      <div className="flex items-center space-x-2 text-xs font-bold text-purple-200">
                        <Key className="w-4 h-4 text-purple-400" />
                        <span>第 4 步：提交 Google 授权码（形如 4/0A... 开头）</span>
                      </div>
                      <p className="text-[11px] text-slate-300 leading-relaxed">
                        在刚才弹出的 Google Antigravity 网页中，点击【复制到剪贴板】，将该授权代码完整粘贴到下方并点击提交：
                      </p>
                      <div className="flex space-x-2">
                        <input
                          type="text"
                          placeholder="在此粘贴形如 4/0AUn6Hd9o1Zix4jBGT3... 的授权代码"
                          value={cliAuthCode}
                          onChange={(e) => setCliAuthCode(e.target.value)}
                          disabled={submittingCliCode}
                          className="flex-1 px-3.5 py-2.5 rounded-lg bg-surface-100 border border-border/80 text-white font-mono text-xs focus:outline-none focus:border-purple-400"
                        />
                        <button
                          type="button"
                          onClick={handleSubmitCliCode}
                          disabled={submittingCliCode || !cliAuthCode.trim()}
                          className="px-5 py-2.5 rounded-lg bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white font-semibold text-xs transition-colors shrink-0 flex items-center space-x-1.5 shadow-md shadow-purple-600/30"
                        >
                          {submittingCliCode ? (
                            <>
                              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                              <span>验证中...</span>
                            </>
                          ) : (
                            <>
                              <Check className="w-3.5 h-3.5" />
                              <span>确认提交授权码</span>
                            </>
                          )}
                        </button>
                      </div>

                      {/* 呼出终端备用入口 */}
                      <div className="pt-2.5 border-t border-purple-500/30 flex items-center justify-between text-[11px]">
                        <span className="text-slate-300">💡 若网页提交后长时间等待无响应：</span>
                        <button
                          type="button"
                          onClick={handleOpenTerminal}
                          disabled={openingTerminal}
                          className="px-3 py-1.5 rounded-lg border border-purple-400/50 bg-purple-900/30 text-purple-200 hover:bg-purple-800/40 transition-all flex items-center space-x-1"
                        >
                          <Terminal className="w-3.5 h-3.5 text-purple-300" />
                          <span>{openingTerminal ? '正在呼出...' : '💻 一键呼出系统终端窗口按回车完成'}</span>
                        </button>
                      </div>
                    </div>
                  )}

                  {/* 状态轮询指示 */}
                  {pollingAlias && (
                    <div className="p-3.5 rounded-xl bg-blue-950/30 border border-blue-500/30 flex items-center space-x-3 text-xs text-blue-300">
                      <RefreshCw className="w-4 h-4 animate-spin text-blue-400 shrink-0" />
                      <span>正在实时监听认证状态... 提交授权码后将自动完成绑定入池。</span>
                    </div>
                  )}
                </div>
              )}

              {/* ──────────────────── 模式 2: Google AI Studio 官方免费 API Key ──────────────────── */}
              {addMode === 'api_key' && (
                <div className="space-y-5">
                  {/* 三步极速引导卡 */}
                  <div className="p-4 rounded-2xl bg-surface-200/40 border border-border/80 space-y-3">
                    <div className="flex items-center justify-between text-xs font-semibold text-white">
                      <span className="flex items-center space-x-1.5">
                        <Key className="w-4 h-4 text-emerald-400" />
                        <span>操作指引：官方免费个人 API Key 极速 3 步（推荐所有个人用户）</span>
                      </span>
                      <span className="text-[11px] text-emerald-300 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20">
                        零客户端依赖 · 秒级可用
                      </span>
                    </div>

                    <div className="grid grid-cols-3 gap-2.5 text-[11px]">
                      <div className="p-2.5 rounded-xl bg-surface-100 border border-border/60 space-y-1">
                        <div className="font-bold text-emerald-400 flex items-center space-x-1">
                          <span className="w-4 h-4 rounded-full bg-emerald-500/20 text-emerald-300 flex items-center justify-center text-[10px]">1</span>
                          <span>直达官网</span>
                        </div>
                        <p className="text-slate-400 leading-tight">点击下方直达按钮进入 Google AI Studio 控制台</p>
                      </div>

                      <div className="p-2.5 rounded-xl bg-surface-100 border border-border/60 space-y-1">
                        <div className="font-bold text-teal-400 flex items-center space-x-1">
                          <span className="w-4 h-4 rounded-full bg-teal-500/20 text-teal-300 flex items-center justify-center text-[10px]">2</span>
                          <span>创建 Key</span>
                        </div>
                        <p className="text-slate-400 leading-tight">点击 Create API key 复制生成的 AIzaSy... 密钥</p>
                      </div>

                      <div className="p-2.5 rounded-xl bg-surface-100 border border-border/60 space-y-1">
                        <div className="font-bold text-blue-400 flex items-center space-x-1">
                          <span className="w-4 h-4 rounded-full bg-blue-500/20 text-blue-300 flex items-center justify-center text-[10px]">3</span>
                          <span>粘贴保存</span>
                        </div>
                        <p className="text-slate-400 leading-tight">粘贴至下方保存，即刻享受每日 1500 次调用</p>
                      </div>
                    </div>
                  </div>

                  {/* 官网直达卡片 */}
                  <div className="p-4 rounded-xl bg-emerald-950/20 border border-emerald-500/30 flex items-center justify-between">
                    <div>
                      <div className="text-xs font-semibold text-emerald-200">Google AI Studio 官方密钥生成页</div>
                      <p className="text-[11px] text-slate-400 mt-0.5">任何普通个人 Gmail 账号登录即用，免信用卡，永久免费</p>
                    </div>
                    <a
                      href="https://aistudio.google.com/apikey"
                      target="_blank"
                      rel="noreferrer"
                      className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium flex items-center space-x-1.5 transition-colors shadow-md shadow-emerald-600/20"
                    >
                      <span>👉 直达官网创建免费 Key</span>
                      <ExternalLink className="w-3.5 h-3.5" />
                    </a>
                  </div>

                  {/* 录入表单 */}
                  <form onSubmit={handleAddApiKey} className="space-y-4">
                    <div>
                      <label className="block text-xs font-medium text-slate-300 mb-1.5">
                        账号标识名称 (选填)
                      </label>
                      <input
                        type="text"
                        required
                        placeholder="例如: gmail-free-key 或 personal-1"
                        value={apiKeyName}
                        onChange={(e) => setApiKeyName(e.target.value)}
                        disabled={apiKeyLoading}
                        className="w-full px-3.5 py-2.5 rounded-xl bg-surface-200/50 border border-border/80 text-white placeholder-slate-500 text-xs focus:outline-none focus:border-emerald-500"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-medium text-slate-300 mb-1.5">
                        Google API Key (以 AIzaSy 开头)
                      </label>
                      <input
                        type="text"
                        required
                        placeholder="粘贴以 AIzaSy 开头的密钥"
                        value={apiKeyValue}
                        onChange={(e) => setApiKeyValue(e.target.value)}
                        disabled={apiKeyLoading}
                        className="w-full px-3.5 py-2.5 rounded-xl bg-surface-200/50 border border-border/80 text-white placeholder-slate-500 font-mono text-xs focus:outline-none focus:border-emerald-500"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-medium text-slate-300 mb-1.5">
                        调度优先级 (数值越小越优先调用)
                      </label>
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={apiKeyPriority}
                        onChange={(e) => setApiKeyPriority(Number(e.target.value))}
                        disabled={apiKeyLoading}
                        className="w-full px-3.5 py-2.5 rounded-xl bg-surface-200/50 border border-border/80 text-white text-xs focus:outline-none focus:border-emerald-500"
                      />
                    </div>

                    <button
                      type="submit"
                      disabled={apiKeyLoading}
                      className="w-full py-3 px-4 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-medium text-xs transition-all shadow-lg shadow-emerald-500/20 flex items-center justify-center space-x-2 disabled:opacity-50"
                    >
                      {apiKeyLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                      <span>确认添加此 API Key 入池</span>
                    </button>
                  </form>
                </div>
              )}

              {/* ──────────────────── 模式 3: 自建 Google Cloud OAuth ──────────────────── */}
              {addMode === 'standard_oauth' && (
                <div className="space-y-5">
                  <div className="p-4 rounded-2xl bg-surface-200/40 border border-border/80 space-y-2">
                    <div className="flex items-center space-x-2 text-sm font-semibold text-white">
                      <Globe className="w-4 h-4 text-blue-400" />
                      <span>自建 Google Cloud OAuth 2.0 通道</span>
                    </div>
                    <p className="text-xs text-slate-400 leading-relaxed">
                      适合拥有 Google Cloud 开发者账号的用户。通过在您的 GCP 控制台创建专属 Web 客户端，实现多用户一键网页弹窗单点登录与自动续期。
                    </p>
                  </div>

                  {oauthConfig?.is_configured ? (
                    <div className="space-y-4 text-center py-4">
                      <button
                        type="button"
                        onClick={handleLaunchGoogleOAuth}
                        disabled={oauthLoading}
                        className="w-full py-3.5 px-6 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-medium text-sm transition-all shadow-lg shadow-blue-500/20 flex items-center justify-center space-x-2 disabled:opacity-50"
                      >
                        {oauthLoading ? (
                          <RefreshCw className="w-4 h-4 animate-spin" />
                        ) : (
                          <ExternalLink className="w-4 h-4" />
                        )}
                        <span>👉 一键拉起 Google 官方授权窗口</span>
                      </button>
                      <p className="text-[11px] text-slate-500">
                        将在新弹窗中打开 accounts.google.com，授权完成后本窗口将自动捕获并加入账号池
                      </p>

                      <div className="pt-4 border-t border-border/40 text-left">
                        <button
                          type="button"
                          onClick={() => setShowConfigForm(!showConfigForm)}
                          className="text-xs text-slate-400 hover:text-slate-200 flex items-center space-x-1"
                        >
                          <Settings className="w-3.5 h-3.5" />
                          <span>{showConfigForm ? '收起客户端设置' : '修改自建 Google Cloud OAuth 凭据'}</span>
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 space-y-3">
                      <div className="flex items-center space-x-2 text-xs font-semibold text-amber-300">
                        <AlertTriangle className="w-4 h-4" />
                        <span>尚未配置 Google Cloud OAuth 客户端</span>
                      </div>
                      <p className="text-xs text-slate-300 leading-relaxed">
                        在 Google Cloud Console 中创建自己的 OAuth 客户端步骤：
                      </p>
                      <ol className="text-[11px] text-slate-400 list-decimal list-inside space-y-1.5 pl-1">
                        <li>打开 <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer" className="text-blue-400 underline">Google Cloud 控制台凭据页</a>，新建一个项目并启用 Generative Language API。</li>
                        <li>点击「创建凭据」-&gt;「OAuth 客户端 ID」，应用类型选择「Web 应用」。</li>
                        <li>在「已获授权的重定向 URI」中添加：<code className="text-emerald-300 font-mono">http://localhost:8686/api/v1/accounts/oauth/callback</code></li>
                        <li>将生成的客户端 ID 和密钥复制粘贴至下方并保存即可！</li>
                      </ol>
                    </div>
                  )}

                  {(showConfigForm || !oauthConfig?.is_configured) && (
                    <form onSubmit={handleSaveOAuthConfig} className="p-4 rounded-xl bg-surface-200/50 border border-border/80 space-y-3">
                      <div className="text-xs font-semibold text-white flex items-center space-x-2">
                        <Key className="w-3.5 h-3.5 text-blue-400" />
                        <span>配置自建 Google Cloud OAuth 客户端</span>
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">
                          Client ID (客户端 ID)
                        </label>
                        <input
                          type="text"
                          required
                          placeholder="例如: 123456789-xxx.apps.googleusercontent.com"
                          value={clientIdInput}
                          onChange={(e) => setClientIdInput(e.target.value)}
                          className="w-full px-3 py-2 rounded-lg bg-surface-100 border border-border/80 text-white text-xs font-mono focus:outline-none focus:border-blue-500"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-400 mb-1">
                          Client Secret (客户端密钥)
                        </label>
                        <input
                          type="password"
                          required
                          placeholder="例如: GOCSPX-xxxxxx"
                          value={clientSecretInput}
                          onChange={(e) => setClientSecretInput(e.target.value)}
                          className="w-full px-3 py-2 rounded-lg bg-surface-100 border border-border/80 text-white text-xs font-mono focus:outline-none focus:border-blue-500"
                        />
                      </div>
                      <div className="pt-2">
                        <button
                          type="submit"
                          disabled={oauthLoading}
                          className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium text-xs transition-colors flex items-center justify-center space-x-1.5"
                        >
                          {oauthLoading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                          <span>保存并更新客户端凭据</span>
                        </button>
                      </div>
                    </form>
                  )}
                </div>
              )}
            </div>
          )}

          {/* ──────────────────── TAB 3: 安全与审计日志 ──────────────────── */}
          {activeTab === 'audit' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-white">操作审计日志</h3>
                  <p className="text-xs text-slate-400">记录所有敏感管理操作与执行结果</p>
                </div>
                <button
                  onClick={fetchAuditLogs}
                  disabled={auditLoading}
                  className="px-3 py-1.5 text-xs rounded-lg border border-border text-slate-300 hover:text-white hover:bg-surface-200 transition-colors flex items-center space-x-1.5"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${auditLoading ? 'animate-spin' : ''}`} />
                  <span>刷新日志</span>
                </button>
              </div>

              <div className="rounded-xl border border-border/60 overflow-hidden bg-surface-200/20">
                <table className="w-full text-xs text-left">
                  <thead className="bg-surface-200/60 text-slate-400 border-b border-border/60">
                    <tr>
                      <th className="py-2.5 px-4 font-medium">时间</th>
                      <th className="py-2.5 px-4 font-medium">事件类型</th>
                      <th className="py-2.5 px-4 font-medium">关联账号</th>
                      <th className="py-2.5 px-4 font-medium">详情</th>
                      <th className="py-2.5 px-4 font-medium">执行者</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40 text-slate-300">
                    {auditLogs.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="py-8 text-center text-slate-500">
                          暂无审计日志记录
                        </td>
                      </tr>
                    ) : (
                      auditLogs.map((log) => (
                        <tr key={log.id} className="hover:bg-surface-200/40">
                          <td className="py-2 px-4 font-mono text-[11px] text-slate-400">{log.created_at}</td>
                          <td className="py-2 px-4 font-semibold text-white">{log.event_type}</td>
                          <td className="py-2 px-4 text-slate-300 font-mono text-[11px]">{log.account_name || '-'}</td>
                          <td className="py-2 px-4 text-slate-300 max-w-xs truncate">{log.detail}</td>
                          <td className="py-2 px-4 text-slate-400">{log.actor}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* 底部安全状态脚标 */}
        <div className="px-6 py-3 border-t border-border/80 bg-surface-200/30 flex items-center justify-between text-xs text-slate-400">
          <div className="flex items-center space-x-2">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            <span>安全基线: 127.0.0.1 专属回环绑定 · DNS 重绑定防护 · 动态令牌拦截</span>
          </div>
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg bg-surface-300/40 hover:bg-surface-300 text-slate-200 text-xs font-medium transition-colors"
          >
            完成并关闭
          </button>
        </div>
      </div>
    </div>
  );
}
