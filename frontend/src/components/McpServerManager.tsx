'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Terminal,
  Globe,
  Plus,
  Upload,
  RefreshCw,
  Play,
  Trash2,
  Edit3,
  Check,
  CheckCircle2,
  AlertCircle,
  Loader2,
  X,
  FileCode,
  Shield,
  Layers,
  Settings2,
  ExternalLink,
  Sliders,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';
import { invalidateWorkspaceSkills } from '@/utils/skillCache';

export type McpTransportType = 'stdio' | 'streamable-http' | 'sse';

export interface McpServerConfigItem {
  transport?: McpTransportType | 'http' | 'streamableHttp' | string;
  type?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  serverUrl?: string;
  headers?: Record<string, string>;
  timeout_seconds?: number;
  upload_endpoint?: string;
  upload_file_field?: string;
  download_origins?: string[];
  description?: string;
  enabled?: boolean;
}

export interface McpTestResult {
  ok: boolean;
  server: string;
  transport: string;
  tool_count: number;
  tools: string[];
  latency_ms: number;
  error?: string | null;
}

export function resolveMcpTransport(cfg?: McpServerConfigItem): McpTransportType {
  const raw = String(cfg?.transport || cfg?.type || '')
    .trim()
    .toLowerCase();
  if (raw === 'sse') return 'sse';
  if (raw === 'streamable-http' || raw === 'streamablehttp' || raw === 'streamable_http' || raw === 'http') {
    return 'streamable-http';
  }
  const hasUrl = Boolean((cfg?.url || cfg?.serverUrl || '').trim());
  const hasCmd = Boolean((cfg?.command || '').trim());
  if (hasUrl && !hasCmd) return 'streamable-http';
  return 'stdio';
}

interface McpServerManagerProps {
  workspaceId?: string;
  onServersUpdated?: () => void;
}

export function McpServerManager({ workspaceId, onServersUpdated }: McpServerManagerProps) {
  const [mcpServers, setMcpServers] = useState<Record<string, McpServerConfigItem>>({});
  const [configPath, setConfigPath] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ success: boolean; msg: string } | null>(null);

  // 弹窗表单状态
  const [formMode, setFormMode] = useState<'none' | 'create' | 'edit' | 'import'>('none');
  const [editingName, setEditingName] = useState<string | null>(null);
  const [connTab, setConnTab] = useState<'stdio' | 'remote'>('stdio');

  // 表单字段
  const [formName, setFormName] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formCommand, setFormCommand] = useState('npx');
  const [formArgs, setFormArgs] = useState('');
  const [formEnvJson, setFormEnvJson] = useState('');
  const [formRemoteTransport, setFormRemoteTransport] = useState<'streamable-http' | 'sse'>('streamable-http');
  const [formUrl, setFormUrl] = useState('');
  const [formHeadersJson, setFormHeadersJson] = useState('');
  const [formTimeout, setFormTimeout] = useState('60');
  const [formUploadEndpoint, setFormUploadEndpoint] = useState('');
  const [formUploadFileField, setFormUploadFileField] = useState('file');
  const [formDownloadOriginsText, setFormDownloadOriginsText] = useState('');
  const [showAdvancedBridge, setShowAdvancedBridge] = useState(false);

  // 测试状态
  const [testingServerName, setTestingServerName] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, McpTestResult>>({});
  const [formTestResult, setFormTestResult] = useState<McpTestResult | null>(null);

  // 批量导入
  const [importJson, setImportJson] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchMcpServers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/api/v1/extensions/mcp');
      if (res.ok) {
        const data = await res.json();
        setMcpServers(data.mcpServers || {});
        if (data.config_path) setConfigPath(data.config_path);
      }
    } catch {
      // 容错静默
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchMcpServers();
  }, [fetchMcpServers]);

  const resetForm = () => {
    setEditingName(null);
    setFormName('');
    setFormDesc('');
    setConnTab('stdio');
    setFormCommand('npx');
    setFormArgs('');
    setFormEnvJson('');
    setFormRemoteTransport('streamable-http');
    setFormUrl('');
    setFormHeadersJson('');
    setFormTimeout('60');
    setFormUploadEndpoint('');
    setFormUploadFileField('file');
    setFormDownloadOriginsText('');
    setShowAdvancedBridge(false);
    setFormTestResult(null);
  };

  const openCreateModal = () => {
    resetForm();
    setFormMode('create');
  };

  const openEditModal = (name: string, cfg: McpServerConfigItem) => {
    const transport = resolveMcpTransport(cfg);
    setEditingName(name);
    setFormName(name);
    setFormDesc(cfg.description || '');
    setFormTimeout(String(cfg.timeout_seconds ?? (transport === 'stdio' ? 35 : 60)));
    setFormTestResult(null);

    if (transport === 'stdio') {
      setConnTab('stdio');
      setFormCommand(cfg.command || 'npx');
      setFormArgs((cfg.args || []).join(' '));
      setFormEnvJson(cfg.env && Object.keys(cfg.env).length > 0 ? JSON.stringify(cfg.env, null, 2) : '');
      setFormUrl('');
      setFormHeadersJson('');
      setFormUploadEndpoint('');
      setFormUploadFileField('file');
      setFormDownloadOriginsText('');
      setShowAdvancedBridge(false);
    } else {
      setConnTab('remote');
      setFormRemoteTransport(transport === 'sse' ? 'sse' : 'streamable-http');
      setFormUrl(cfg.url || cfg.serverUrl || '');
      setFormHeadersJson(
        cfg.headers && Object.keys(cfg.headers).length > 0 ? JSON.stringify(cfg.headers, null, 2) : ''
      );
      const hasAdv = Boolean(cfg.upload_endpoint || (cfg.download_origins && cfg.download_origins.length > 0));
      setFormUploadEndpoint(cfg.upload_endpoint || '');
      setFormUploadFileField(cfg.upload_file_field || 'file');
      setFormDownloadOriginsText((cfg.download_origins || []).join(', '));
      setShowAdvancedBridge(hasAdv);
    }
    setFormMode('edit');
  };

  const handleToggleServer = async (name: string) => {
    const currentEnabled = Boolean(mcpServers[name]?.enabled ?? true);
    const nextEnabled = !currentEnabled;
    setMcpServers((prev) => ({
      ...prev,
      [name]: { ...prev[name], enabled: nextEnabled },
    }));

    try {
      const res = await apiFetch(`/api/v1/extensions/mcp/${encodeURIComponent(name)}/toggle`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: nextEnabled }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.mcpServers) setMcpServers(data.mcpServers);
        invalidateWorkspaceSkills(workspaceId);
        onServersUpdated?.();
      }
    } catch {
      // 网络容错
    }
  };

  const handleDeleteServer = async (name: string) => {
    const confirmed = window.confirm(`确定要从本地移除 MCP 服务【${name}】吗？移除后相关扩展工具将不再注入智能体。`);
    if (!confirmed) return;

    try {
      const res = await apiFetch(`/api/v1/extensions/mcp/${encodeURIComponent(name)}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        const data = await res.json();
        setMcpServers(data.mcpServers || {});
        setTestResults((prev) => {
          const next = { ...prev };
          delete next[name];
          return next;
        });
        invalidateWorkspaceSkills(workspaceId);
        onServersUpdated?.();
        setFeedback({ success: true, msg: `已成功移除 MCP 服务【${name}】` });
      }
    } catch (e: any) {
      setFeedback({ success: false, msg: e.message || '删除服务异常' });
    }
  };

  const handleTestServer = async (name: string) => {
    setTestingServerName(name);
    try {
      const res = await apiFetch('/api/v1/extensions/mcp/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_name: name }),
      });
      const data = await res.json();
      if (res.ok) {
        setTestResults((prev) => ({ ...prev, [name]: data }));
        setFeedback({
          success: Boolean(data.ok),
          msg: data.ok
            ? `✅ [${name}] 连接成功（${data.transport} · 发现 ${data.tool_count} 个工具 · 耗时 ${data.latency_ms}ms）`
            : `❌ [${name}] 连接失败：${data.error || '未知响应'}`,
        });
      } else {
        setFeedback({ success: false, msg: data.detail || `测试 [${name}] 失败` });
      }
    } catch (e: any) {
      setFeedback({ success: false, msg: e.message || '网络连接异常' });
    } finally {
      setTestingServerName(null);
    }
  };

  const buildPayloadFromForm = (): Record<string, any> | null => {
    const cleanName = formName.trim();
    if (!cleanName) {
      setFeedback({ success: false, msg: 'MCP 服务名称不能为空' });
      return null;
    }
    if (cleanName.includes('__')) {
      setFeedback({ success: false, msg: '服务名称不得包含双下划线 `__`（防命名空间冲突）' });
      return null;
    }

    const parsedTimeout = Number(formTimeout);
    const timeoutVal = Number.isFinite(parsedTimeout) ? Math.max(5, Math.min(600, parsedTimeout)) : undefined;

    if (connTab === 'stdio') {
      if (!formCommand.trim()) {
        setFeedback({ success: false, msg: '本地 stdio 模式必须填写启动命令（如 npx / uvx / python）' });
        return null;
      }
      let parsedEnv: Record<string, string> = {};
      if (formEnvJson.trim()) {
        try {
          const obj = JSON.parse(formEnvJson.trim());
          if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error();
          parsedEnv = obj;
        } catch {
          setFeedback({ success: false, msg: '环境变量必须为有效的 JSON 对象格式（如 {"TOKEN": "xxx"}）' });
          return null;
        }
      }
      const argsList = formArgs.split(/\s+/).map((s) => s.trim()).filter(Boolean);

      return {
        name: cleanName,
        transport: 'stdio',
        command: formCommand.trim(),
        args: argsList,
        env: parsedEnv,
        timeout_seconds: timeoutVal,
        description: formDesc.trim(),
        enabled: editingName ? Boolean(mcpServers[editingName]?.enabled ?? true) : true,
      };
    }

    const cleanUrl = formUrl.trim();
    if (!cleanUrl.startsWith('http://') && !cleanUrl.startsWith('https://')) {
      setFeedback({ success: false, msg: '远程端点 URL 必须以 http:// 或 https:// 开头' });
      return null;
    }

    let parsedHeaders: Record<string, string> = {};
    if (formHeadersJson.trim()) {
      try {
        const obj = JSON.parse(formHeadersJson.trim());
        if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error();
        parsedHeaders = obj;
      } catch {
        setFeedback({ success: false, msg: '请求头 Headers 必须为有效的 JSON 对象格式' });
        return null;
      }
    }

    const downloadOrigins = formDownloadOriginsText
      .split(/[\n,;]+/)
      .map((s) => s.trim())
      .filter(Boolean);

    return {
      name: cleanName,
      transport: formRemoteTransport,
      url: cleanUrl,
      headers: parsedHeaders,
      timeout_seconds: timeoutVal ?? 60,
      upload_endpoint: formUploadEndpoint.trim(),
      upload_file_field: formUploadFileField.trim() || 'file',
      download_origins: downloadOrigins,
      description: formDesc.trim(),
      enabled: editingName ? Boolean(mcpServers[editingName]?.enabled ?? true) : true,
    };
  };

  const handleSaveForm = async () => {
    const payload = buildPayloadFromForm();
    if (!payload) return;

    try {
      const res = await apiFetch('/api/v1/extensions/mcp/server', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (res.ok) {
        setMcpServers(data.mcpServers || {});
        if (data.config_path) setConfigPath(data.config_path);
        const isEdit = Boolean(editingName);
        resetForm();
        setFormMode('none');
        invalidateWorkspaceSkills(workspaceId);
        onServersUpdated?.();
        setFeedback({
          success: true,
          msg: isEdit ? `✅ 已成功更新 MCP 服务【${payload.name}】` : `✅ 已成功接入新 MCP 服务【${payload.name}】`,
        });
      } else {
        setFeedback({ success: false, msg: data.detail || '保存 MCP 服务失败' });
      }
    } catch (e: any) {
      setFeedback({ success: false, msg: e.message || '网络请求异常' });
    }
  };

  const handleTestFromForm = async () => {
    const payload = buildPayloadFromForm();
    if (!payload) return;
    const targetName = editingName || payload.name || 'test';
    setTestingServerName(`__form__:${targetName}`);
    setFormTestResult(null);

    try {
      const res = await apiFetch('/api/v1/extensions/mcp/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          server_name: editingName || undefined,
          transport: payload.transport,
          command: payload.command,
          args: payload.args,
          env: payload.env,
          url: payload.url,
          headers: payload.headers,
          timeout_seconds: payload.timeout_seconds,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setFormTestResult(data);
      } else {
        setFormTestResult({
          ok: false,
          server: targetName,
          transport: payload.transport,
          tool_count: 0,
          tools: [],
          latency_ms: 0,
          error: data.detail || '连接探测失败',
        });
      }
    } catch (e: any) {
      setFormTestResult({
        ok: false,
        server: targetName,
        transport: payload.transport,
        tool_count: 0,
        tools: [],
        latency_ms: 0,
        error: e.message || '网络异常',
      });
    } finally {
      setTestingServerName(null);
    }
  };

  const handleImportJsonSubmit = async () => {
    if (!importJson.trim()) {
      setFeedback({ success: false, msg: '请先粘贴 JSON 配置内容或选择配置文件' });
      return;
    }

    try {
      const res = await apiFetch('/api/v1/extensions/mcp/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_json: importJson.trim() }),
      });
      const data = await res.json();
      if (res.ok) {
        setMcpServers(data.mcpServers || {});
        if (data.config_path) setConfigPath(data.config_path);
        setImportJson('');
        setFormMode('none');
        invalidateWorkspaceSkills(workspaceId);
        onServersUpdated?.();
        setFeedback({
          success: true,
          msg: `✅ 成功批量导入 ${data.imported_count || 1} 个 MCP 服务！`,
        });
      } else {
        setFeedback({ success: false, msg: data.detail || '导入配置解析失败' });
      }
    } catch (e: any) {
      setFeedback({ success: false, msg: e.message || '网络请求异常' });
    }
  };

  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setImportJson(String(ev.target?.result || ''));
    };
    reader.readAsText(file, 'utf-8');
    e.target.value = '';
  };

  const serverEntries = Object.entries(mcpServers);

  return (
    <div className="space-y-6">
      {/* 顶部标题与操作栏 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-foreground flex items-center space-x-2">
            <span>已关联应用与 MCP 服务</span>
            <span className="text-xs font-mono font-normal px-2 py-0.5 rounded-full bg-surface-200 border border-border text-muted-foreground">
              {serverEntries.length} 个已配置
            </span>
          </h2>
          <p className="text-xs text-muted-foreground mt-1">
            连接外部开发工具、数据库与模型上下文协议（Model Context Protocol），为 AI 提供具身扩展能力。
          </p>
        </div>

        <div className="flex items-center space-x-2 shrink-0">
          <button
            type="button"
            onClick={() => void fetchMcpServers()}
            disabled={loading}
            className="p-2 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border text-muted-foreground hover:text-foreground transition-colors"
            title="刷新状态"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={() => setFormMode('import')}
            className="px-3 py-1.5 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border text-foreground text-xs font-medium flex items-center space-x-1.5 transition-colors"
          >
            <Upload className="w-3.5 h-3.5" />
            <span>导入配置 (JSON)</span>
          </button>
          <button
            type="button"
            onClick={openCreateModal}
            className="px-3.5 py-1.5 rounded-xl bg-foreground text-background hover:opacity-90 text-xs font-semibold flex items-center space-x-1.5 shadow-sm transition-all"
          >
            <Plus className="w-4 h-4" />
            <span>接入 MCP 服务</span>
          </button>
        </div>
      </div>

      {/* 提示反馈横幅 */}
      {feedback && (
        <div
          className={`px-4 py-2.5 rounded-xl border text-xs flex items-center justify-between animate-fade-in ${feedback.success
              ? 'bg-surface-200 border-border text-foreground'
              : 'bg-surface-200 border-border text-rose-500'
            }`}
        >
          <div className="flex items-center space-x-2">
            {feedback.success ? <Check className="w-4 h-4 shrink-0" /> : <AlertCircle className="w-4 h-4 shrink-0" />}
            <span>{feedback.msg}</span>
          </div>
          <button
            type="button"
            onClick={() => setFeedback(null)}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* 内置基础驱动展示区 */}
      <div className="space-y-2">
        <h3 className="text-xs font-bold text-foreground uppercase tracking-wider">内置核心环境驱动</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="p-4 rounded-xl border border-border bg-surface-card flex items-center justify-between shadow-xs">
            <div className="flex items-center space-x-3">
              <div className="p-2 rounded-lg bg-surface-200 text-foreground border border-border">
                <Terminal className="w-4 h-4" />
              </div>
              <div>
                <h4 className="text-xs font-semibold text-foreground">Local Shell / Terminal</h4>
                <p className="text-[11px] text-muted-foreground">受控物理工作区终端执行环境（PowerShell / Bash）</p>
              </div>
            </div>
            <span className="text-[11px] font-mono text-foreground bg-surface-200 px-2 py-0.5 rounded border border-border">
              已内置就绪
            </span>
          </div>

          <div className="p-4 rounded-xl border border-border bg-surface-card flex items-center justify-between shadow-xs">
            <div className="flex items-center space-x-3">
              <div className="p-2 rounded-lg bg-surface-200 text-foreground border border-border">
                <Layers className="w-4 h-4" />
              </div>
              <div>
                <h4 className="text-xs font-semibold text-foreground">SQLite FTS5 Local Memory</h4>
                <p className="text-[11px] text-muted-foreground">工作区源码与文档零拷贝分词检索引擎</p>
              </div>
            </div>
            <span className="text-[11px] font-mono text-foreground bg-surface-200 px-2 py-0.5 rounded border border-border">
              已内置就绪
            </span>
          </div>
        </div>
      </div>

      {/* 外部已接入 MCP 服务卡片列表 */}
      <div className="space-y-3 pt-2">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-bold text-foreground uppercase tracking-wider">
            外部接入服务 ({serverEntries.length})
          </h3>
          {configPath && (
            <span className="text-[10px] font-mono text-muted-foreground truncate max-w-sm" title={configPath}>
              存储路径: {configPath}
            </span>
          )}
        </div>

        {serverEntries.length === 0 ? (
          <div className="p-10 border border-dashed border-border rounded-2xl text-center bg-surface-100 space-y-3">
            <Globe className="w-8 h-8 text-foreground opacity-40 mx-auto" />
            <div>
              <p className="text-xs font-semibold text-foreground">暂无外部接入的 MCP 服务</p>
              <p className="text-[11px] text-muted-foreground mt-1">
                点击上方「接入 MCP 服务」或导入已有的 Claude Desktop / Cursor 配置文件。
              </p>
            </div>
            <button
              type="button"
              onClick={openCreateModal}
              className="px-3 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border text-foreground text-xs font-medium inline-flex items-center space-x-1 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>立即配置首个 MCP 协议</span>
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3">
            {serverEntries.map(([srvName, cfg]) => {
              const transport = resolveMcpTransport(cfg);
              const isEnabled = Boolean(cfg.enabled ?? true);
              const testRes = testResults[srvName];
              const isTesting = testingServerName === srvName;

              return (
                <div
                  key={srvName}
                  className={`p-4 rounded-2xl border transition-all shadow-xs ${isEnabled
                      ? 'border-border bg-surface-card'
                      : 'border-border/60 bg-surface-100 opacity-75'
                    }`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                    <div className="space-y-1.5 flex-1 min-w-0">
                      <div className="flex items-center flex-wrap gap-2">
                        <span className="text-sm font-bold text-foreground font-mono">
                          {srvName}
                        </span>

                        {/* 协议徽标 (单色扁平) */}
                        <span className="text-[10px] font-mono font-semibold px-2 py-0.5 rounded border bg-surface-200 border-border text-foreground">
                          {transport === 'stdio'
                            ? 'LOCAL STDIO'
                            : transport === 'sse'
                              ? 'REMOTE SSE'
                              : 'REMOTE HTTP'}
                        </span>

                        {/* 启停状态 */}
                        <span
                          className={`text-[10px] font-mono px-2 py-0.5 rounded-full border ${isEnabled
                              ? 'border-foreground/30 bg-surface-200 text-foreground font-semibold'
                              : 'border-border bg-surface-100 text-muted-foreground'
                            }`}
                        >
                          {isEnabled ? '● 已启用' : '○ 已停用'}
                        </span>
                      </div>

                      {/* 命令 / URL 概要 */}
                      <div className="font-mono text-xs text-muted-foreground bg-surface-100 px-2.5 py-1.5 rounded-lg border border-border break-all select-all">
                        {transport === 'stdio' ? (
                          <span>
                            {cfg.command} {(cfg.args || []).join(' ')}
                          </span>
                        ) : (
                          <span>{cfg.url || cfg.serverUrl}</span>
                        )}
                      </div>

                      {cfg.description && (
                        <p className="text-xs text-muted-foreground break-anywhere">
                          {cfg.description}
                        </p>
                      )}

                      {/* 测试连接结果展示 */}
                      {testRes && (
                        <div
                          className={`mt-2 p-2 rounded-lg border text-xs font-mono flex items-start space-x-2 ${testRes.ok
                              ? 'bg-surface-200 border-border text-foreground'
                              : 'bg-surface-200 border-border text-rose-500'
                            }`}
                        >
                          {testRes.ok ? (
                            <CheckCircle2 className="w-4 h-4 shrink-0 text-foreground mt-0.5" />
                          ) : (
                            <AlertCircle className="w-4 h-4 shrink-0 text-rose-500 mt-0.5" />
                          )}
                          <div className="min-w-0 flex-1">
                            {testRes.ok ? (
                              <div>
                                <span className="font-semibold">连通测试通过</span> · 延迟 {testRes.latency_ms}ms · 发现 {testRes.tool_count} 个工具
                                {testRes.tools && testRes.tools.length > 0 && (
                                  <p className="text-[10px] text-muted-foreground mt-0.5 truncate">
                                    工具列表: {testRes.tools.join(', ')}
                                  </p>
                                )}
                              </div>
                            ) : (
                              <div>
                                <span className="font-semibold">连通失败:</span> {testRes.error || '无法建立握手'}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>

                    {/* 操作按钮组 (单色扁平化) */}
                    <div className="flex items-center space-x-1.5 shrink-0 self-end sm:self-start">
                      {/* 测试按钮 */}
                      <button
                        type="button"
                        onClick={() => void handleTestServer(srvName)}
                        disabled={isTesting}
                        className="px-2.5 py-1.5 rounded-lg border border-border bg-surface-100 hover:bg-surface-200 text-foreground text-xs font-medium flex items-center space-x-1 transition-colors disabled:opacity-50"
                        title="测试此服务连通性"
                      >
                        {isTesting ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <Play className="w-3.5 h-3.5" />
                        )}
                        <span>测试</span>
                      </button>

                      {/* 编辑按钮 */}
                      <button
                        type="button"
                        onClick={() => openEditModal(srvName, cfg)}
                        className="px-2.5 py-1.5 rounded-lg border border-border bg-surface-100 hover:bg-surface-200 text-foreground text-xs font-medium flex items-center space-x-1 transition-colors"
                        title="编辑配置参数"
                      >
                        <Edit3 className="w-3.5 h-3.5" />
                        <span>编辑</span>
                      </button>

                      {/* 启停切换 */}
                      <button
                        type="button"
                        onClick={() => void handleToggleServer(srvName)}
                        className={`px-2.5 py-1.5 rounded-lg border text-xs font-medium transition-colors ${isEnabled
                            ? 'border-border bg-surface-200 text-foreground hover:bg-surface-300'
                            : 'border-border bg-surface-100 text-muted-foreground hover:text-foreground'
                          }`}
                        title={isEnabled ? '点击禁用该服务' : '点击启用该服务'}
                      >
                        {isEnabled ? '停用' : '启用'}
                      </button>

                      {/* 删除按钮 */}
                      <button
                        type="button"
                        onClick={() => void handleDeleteServer(srvName)}
                        className="p-1.5 rounded-lg border border-border bg-surface-100 hover:bg-surface-200 text-muted-foreground hover:text-rose-500 transition-colors"
                        title="删除该 MCP 服务"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ──────────────────── 接入 / 编辑 MCP 服务模态弹窗 (100% 实底纯色) ──────────────────── */}
      {(formMode === 'create' || formMode === 'edit') && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 animate-fade-in select-text"
          onClick={() => setFormMode('none')}
        >
          <div
            className="w-full max-w-2xl bg-surface-card border border-border text-foreground rounded-2xl shadow-2xl flex flex-col max-h-[90vh] overflow-hidden animate-scale-in"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 弹窗头部 */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-border bg-surface-100 shrink-0">
              <div className="flex items-center space-x-2.5">
                <div className="p-2 rounded-xl bg-surface-200 border border-border text-foreground">
                  <Globe className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-foreground">
                    {formMode === 'edit' ? `编辑 MCP 服务【${editingName}】` : '接入新 MCP 服务'}
                  </h3>
                  <p className="text-[11px] text-muted-foreground">
                    支持接入本地命令行进程（Stdio）或标准远程端点（Streamable-HTTP / SSE）。
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setFormMode('none')}
                className="p-1.5 rounded-lg hover:bg-surface-200 text-muted-foreground hover:text-foreground transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* 弹窗表单主体 */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              {/* 服务名称与简述 */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-semibold text-foreground">
                    服务唯一名称 <span className="text-rose-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={formName}
                    onChange={(e) => setFormName(e.target.value)}
                    disabled={formMode === 'edit'}
                    placeholder="例如: github / filesystem"
                    className="w-full px-3 py-2 rounded-xl bg-surface-100 border border-border text-xs text-foreground font-mono focus:outline-none focus:border-foreground/50 disabled:opacity-60"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-semibold text-foreground">
                    超时时间 (秒)
                  </label>
                  <input
                    type="number"
                    min="5"
                    max="600"
                    value={formTimeout}
                    onChange={(e) => setFormTimeout(e.target.value)}
                    placeholder="默认 60"
                    className="w-full px-3 py-2 rounded-xl bg-surface-100 border border-border text-xs text-foreground font-mono focus:outline-none focus:border-foreground/50"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-semibold text-foreground">服务功能描述</label>
                <input
                  type="text"
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  placeholder="对该 MCP 服务提供能力的简短说明"
                  className="w-full px-3 py-2 rounded-xl bg-surface-100 border border-border text-xs text-foreground focus:outline-none focus:border-foreground/50"
                />
              </div>

              {/* 协议类型切换选项卡 (单色扁平) */}
              <div className="space-y-2 pt-2">
                <label className="text-xs font-semibold text-foreground">通讯协议类型</label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setConnTab('stdio')}
                    className={`p-3 rounded-xl border text-left transition-all ${connTab === 'stdio'
                        ? 'border-foreground/40 bg-surface-200 text-foreground shadow-xs'
                        : 'border-border bg-surface-100 text-muted-foreground hover:text-foreground'
                      }`}
                  >
                    <div className="flex items-center space-x-2 font-semibold text-xs">
                      <Terminal className="w-4 h-4 text-foreground" />
                      <span>本地进程 (Stdio)</span>
                    </div>
                    <p className="text-[10px] text-muted-foreground mt-1">
                      通过 npx、uvx 或 python 拉起本地命令行独立进程
                    </p>
                  </button>

                  <button
                    type="button"
                    onClick={() => setConnTab('remote')}
                    className={`p-3 rounded-xl border text-left transition-all ${connTab === 'remote'
                        ? 'border-foreground/40 bg-surface-200 text-foreground shadow-xs'
                        : 'border-border bg-surface-100 text-muted-foreground hover:text-foreground'
                      }`}
                  >
                    <div className="flex items-center space-x-2 font-semibold text-xs">
                      <Globe className="w-4 h-4 text-foreground" />
                      <span>远程端点 (HTTP / SSE)</span>
                    </div>
                    <p className="text-[10px] text-muted-foreground mt-1">
                      连接远程网络或内网托管的 Streamable-HTTP / SSE 服务
                    </p>
                  </button>
                </div>
              </div>

              {/* stdio 表单字段 */}
              {connTab === 'stdio' ? (
                <div className="space-y-3 p-4 rounded-xl border border-border bg-surface-100">
                  <div className="space-y-1">
                    <label className="text-xs font-semibold text-foreground">
                      启动程序命令 (Command) <span className="text-rose-500">*</span>
                    </label>
                    <input
                      type="text"
                      value={formCommand}
                      onChange={(e) => setFormCommand(e.target.value)}
                      placeholder="npx / uvx / python / node"
                      className="w-full px-3 py-2 rounded-xl bg-surface-card border border-border text-xs text-foreground font-mono focus:outline-none focus:border-foreground/50"
                    />
                  </div>

                  <div className="space-y-1">
                    <label className="text-xs font-semibold text-foreground">
                      运行参数 (Args，空格分隔)
                    </label>
                    <input
                      type="text"
                      value={formArgs}
                      onChange={(e) => setFormArgs(e.target.value)}
                      placeholder="-y @modelcontextprotocol/server-memory"
                      className="w-full px-3 py-2 rounded-xl bg-surface-card border border-border text-xs text-foreground font-mono focus:outline-none focus:border-foreground/50"
                    />
                  </div>

                  <div className="space-y-1">
                    <label className="text-xs font-semibold text-foreground">
                      环境变量 (Environment JSON 对象，可选)
                    </label>
                    <textarea
                      rows={2}
                      value={formEnvJson}
                      onChange={(e) => setFormEnvJson(e.target.value)}
                      placeholder='{\n  "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx"\n}'
                      className="w-full px-3 py-2 rounded-xl bg-surface-card border border-border text-xs text-foreground font-mono focus:outline-none focus:border-foreground/50 resize-y"
                    />
                  </div>
                </div>
              ) : (
                /* remote 表单字段 */
                <div className="space-y-3 p-4 rounded-xl border border-border bg-surface-100">
                  <div className="space-y-1">
                    <label className="text-xs font-semibold text-foreground">传输协议规范</label>
                    <div className="flex items-center space-x-4 text-xs">
                      <label className="flex items-center space-x-1.5 cursor-pointer">
                        <input
                          type="radio"
                          name="mcpRemoteTrans"
                          checked={formRemoteTransport === 'streamable-http'}
                          onChange={() => setFormRemoteTransport('streamable-http')}
                          className="accent-foreground"
                        />
                        <span className="text-foreground">Streamable-HTTP (推荐)</span>
                      </label>
                      <label className="flex items-center space-x-1.5 cursor-pointer">
                        <input
                          type="radio"
                          name="mcpRemoteTrans"
                          checked={formRemoteTransport === 'sse'}
                          onChange={() => setFormRemoteTransport('sse')}
                          className="accent-foreground"
                        />
                        <span className="text-foreground">Server-Sent Events (SSE)</span>
                      </label>
                    </div>
                  </div>

                  <div className="space-y-1">
                    <label className="text-xs font-semibold text-foreground">
                      服务端点 URL <span className="text-rose-500">*</span>
                    </label>
                    <input
                      type="text"
                      value={formUrl}
                      onChange={(e) => setFormUrl(e.target.value)}
                      placeholder="http://127.0.0.1:3100/mcp 或 https://api.example.com/sse"
                      className="w-full px-3 py-2 rounded-xl bg-surface-card border border-border text-xs text-foreground font-mono focus:outline-none focus:border-foreground/50"
                    />
                  </div>

                  <div className="space-y-1">
                    <label className="text-xs font-semibold text-foreground">
                      请求头配置 (Headers JSON 对象，可选)
                    </label>
                    <textarea
                      rows={2}
                      value={formHeadersJson}
                      onChange={(e) => setFormHeadersJson(e.target.value)}
                      placeholder='{\n  "Authorization": "Bearer ${API_KEY}"\n}'
                      className="w-full px-3 py-2 rounded-xl bg-surface-card border border-border text-xs text-foreground font-mono focus:outline-none focus:border-foreground/50 resize-y"
                    />
                  </div>
                </div>
              )}

              {/* 表单内测试连通性结果反馈 */}
              {formTestResult && (
                <div
                  className={`p-3 rounded-xl border text-xs font-mono flex items-start space-x-2 ${formTestResult.ok
                      ? 'bg-surface-200 border-border text-foreground'
                      : 'bg-surface-200 border-border text-rose-500'
                    }`}
                >
                  {formTestResult.ok ? (
                    <CheckCircle2 className="w-4 h-4 shrink-0 text-foreground mt-0.5" />
                  ) : (
                    <AlertCircle className="w-4 h-4 shrink-0 text-rose-500 mt-0.5" />
                  )}
                  <div>
                    {formTestResult.ok ? (
                      <p>
                        测试成功！延迟 {formTestResult.latency_ms}ms · 发现 {formTestResult.tool_count} 个可用工具
                      </p>
                    ) : (
                      <p>探测失败: {formTestResult.error || '未响应'}</p>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* 弹窗底部操作 */}
            <div className="flex items-center justify-between px-6 py-4 border-t border-border bg-surface-100 shrink-0">
              <button
                type="button"
                onClick={handleTestFromForm}
                disabled={testingServerName !== null}
                className="px-3.5 py-1.5 rounded-xl border border-border bg-surface-200 hover:bg-surface-300 text-foreground text-xs font-medium flex items-center space-x-1.5 transition-colors disabled:opacity-50"
              >
                {testingServerName?.startsWith('__form__') ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Play className="w-3.5 h-3.5" />
                )}
                <span>测试配置连通性</span>
              </button>

              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  onClick={() => setFormMode('none')}
                  className="px-4 py-1.5 rounded-xl border border-border bg-surface-100 hover:bg-surface-200 text-foreground text-xs font-medium transition-colors"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={handleSaveForm}
                  className="px-5 py-1.5 rounded-xl bg-foreground text-background hover:opacity-90 text-xs font-semibold shadow-sm transition-all"
                >
                  {formMode === 'edit' ? '保存更改' : '确认接入'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ──────────────────── 批量导入 JSON 模态弹窗 (100% 实底纯色) ──────────────────── */}
      {formMode === 'import' && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 animate-fade-in select-text"
          onClick={() => setFormMode('none')}
        >
          <div
            className="w-full max-w-xl bg-surface-card border border-border text-foreground rounded-2xl shadow-2xl flex flex-col max-h-[85vh] overflow-hidden animate-scale-in"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-border bg-surface-100 shrink-0">
              <div className="flex items-center space-x-2.5">
                <div className="p-2 rounded-xl bg-surface-200 border border-border text-foreground">
                  <Upload className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-foreground">导入 MCP 配置文件 (JSON)</h3>
                  <p className="text-[11px] text-muted-foreground">
                    直接兼容 Claude Desktop、Cursor 或标准 mcp_servers.json 格式。
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setFormMode('none')}
                className="p-1.5 rounded-lg hover:bg-surface-200 text-muted-foreground hover:text-foreground transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-6 space-y-3 flex-1 overflow-y-auto">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground">配置文本内容</span>
                <div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".json"
                    onChange={handleFileSelected}
                    className="hidden"
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="px-3 py-1 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border text-foreground text-xs font-medium flex items-center space-x-1 transition-colors"
                  >
                    <Upload className="w-3.5 h-3.5" />
                    <span>选择本地 JSON 文件</span>
                  </button>
                </div>
              </div>

              <textarea
                rows={9}
                value={importJson}
                onChange={(e) => setImportJson(e.target.value)}
                placeholder={'{\n  "mcpServers": {\n    "memory": {\n      "command": "npx",\n      "args": ["-y", "@modelcontextprotocol/server-memory"]\n    }\n  }\n}'}
                className="w-full px-3 py-2.5 rounded-xl bg-surface-100 border border-border text-xs text-foreground font-mono focus:outline-none focus:border-foreground/50 resize-y"
              />
            </div>

            <div className="flex items-center justify-end space-x-2 px-6 py-4 border-t border-border bg-surface-100 shrink-0">
              <button
                type="button"
                onClick={() => setFormMode('none')}
                className="px-4 py-1.5 rounded-xl border border-border bg-surface-100 hover:bg-surface-200 text-foreground text-xs font-medium transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleImportJsonSubmit}
                className="px-5 py-1.5 rounded-xl bg-foreground text-background hover:opacity-90 text-xs font-semibold shadow-sm transition-all"
              >
                确认批量导入
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

