'use client';

import React, { useState, useEffect } from 'react';
import {
  X,
  Cloud,
  HardDrive,
  Database,
  Check,
  AlertCircle,
  RefreshCw,
  Trash2,
  PlugZap,
  Sparkles,
  Copy,
  Share2,
  ShieldCheck,
  Users,
  CheckCircle2,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface CloudWikiMountModalProps {
  isOpen: boolean;
  onClose: () => void;
  workspaceRoot?: string;
}

export interface CloudMountItem {
  mount_id: string;
  mount_name: string;
  provider_type: 'wecom' | 'webdav' | 'oss';
  authority_tier: number;
  config: Record<string, any>;
}

export interface SharedWeComSpace {
  space_id: string;
  space_name: string;
  corpid?: string;
  has_secret?: boolean;
  created_at?: number;
  admin_userid?: string;
  share_token?: string;
}

export function CloudWikiMountModal({ isOpen, onClose, workspaceRoot = '.' }: CloudWikiMountModalProps) {
  const [activeTab, setActiveTab] = useState<'wecom' | 'webdav' | 'oss'>('wecom');
  const [mountName, setMountName] = useState('');
  const [authorityTier, setAuthorityTier] = useState<number>(1);
  const [mountsList, setMountsList] = useState<CloudMountItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // 微盘权限与共享模式
  const [wecomMode, setWecomMode] = useState<'shared_list' | 'admin_create'>('shared_list');
  const [sharedSpaces, setSharedSpaces] = useState<SharedWeComSpace[]>([]);
  const [creatingSpace, setCreatingSpace] = useState(false);
  const [adminUserId, setAdminUserId] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // 表单状态
  const [wecomForm, setWecomForm] = useState({ corpid: '', corpsecret: '', space_id: '' });
  const [webdavForm, setWebdavForm] = useState({ base_url: '', username: '', password: '', root_path: '/' });
  const [ossForm, setOssForm] = useState({ endpoint: '', bucket: '', access_key: '', secret_key: '', prefix: '' });

  useEffect(() => {
    if (isOpen) {
      loadMounts();
      loadSharedSpaces();
      setTestResult(null);
    }
  }, [isOpen]);

  const loadMounts = async () => {
    try {
      const resp = await apiFetch('/api/v1/knowledge/mounts');
      if (resp.ok) {
        const json = await resp.json();
        setMountsList(json.data || []);
      }
    } catch (e) {
      console.error('加载云盘挂载失败:', e);
    }
  };

  const loadSharedSpaces = async () => {
    try {
      const resp = await apiFetch('/api/v1/knowledge/wecom/shared_spaces');
      if (resp.ok) {
        const json = await resp.json();
        setSharedSpaces(json.data || []);
      }
    } catch (e) {
      console.error('加载共享微盘空间失败:', e);
    }
  };

  if (!isOpen) return null;

  const getCurrentConfig = () => {
    if (activeTab === 'wecom') return wecomForm;
    if (activeTab === 'webdav') return webdavForm;
    return ossForm;
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const resp = await apiFetch('/api/v1/knowledge/mounts/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mount_id: `test_${Date.now()}`,
          mount_name: mountName || '测试源',
          provider_type: activeTab,
          authority_tier: authorityTier,
          config: getCurrentConfig(),
        }),
      });
      const data = await resp.json();
      setTestResult({ success: data.success, message: data.message });
    } catch (e: any) {
      setTestResult({ success: false, message: e.message || '网络请求失败' });
    } finally {
      setTesting(false);
    }
  };

  const handleSaveMount = async () => {
    if (!mountName.trim()) {
      alert('请输入挂载展示名称');
      return;
    }
    setLoading(true);
    try {
      const mountId = `mnt_${Date.now()}`;
      const resp = await apiFetch('/api/v1/knowledge/mounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mount_id: mountId,
          mount_name: mountName.trim(),
          provider_type: activeTab,
          authority_tier: authorityTier,
          config: getCurrentConfig(),
        }),
      });
      if (resp.ok) {
        setMountName('');
        setTestResult(null);
        await loadMounts();
      }
    } catch (e) {
      console.error('保存挂载源失败:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteMount = async (mountId: string) => {
    if (!confirm('确定要移除此云端挂载源吗？已解析的本地切片仍将保留在缓存中。')) return;
    try {
      await apiFetch(`/api/v1/knowledge/mounts/${mountId}`, { method: 'DELETE' });
      await loadMounts();
    } catch (e) {
      console.error('删除挂载失败:', e);
    }
  };

  const handleSyncAll = async () => {
    setSyncing(true);
    try {
      const resp = await apiFetch('/api/v1/knowledge/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workspace_root: workspaceRoot }),
      });
      const data = await resp.json();
      alert(data.message || '同步完成');
    } catch (e: any) {
      alert('同步失败: ' + e.message);
    } finally {
      setSyncing(false);
    }
  };

  // 管理员一键创建专属微盘空间
  const handleCreateSpace = async () => {
    if (!wecomForm.corpid.trim() || !wecomForm.corpsecret.trim()) {
      alert('创建空间需填写 CorpId 与 CorpSecret（自建应用密钥）');
      return;
    }
    setCreatingSpace(true);
    try {
      const resp = await apiFetch('/api/v1/knowledge/wecom/create_space', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          corpid: wecomForm.corpid.trim(),
          corpsecret: wecomForm.corpsecret.trim(),
          space_name: mountName.trim() || 'AgentHub-规程知识库',
          admin_userid: adminUserId.trim(),
          operator_role: 'admin',
          allow_all_departments: true,
        }),
      });
      const data = await resp.json();
      if (data.success) {
        setWecomForm((prev) => ({ ...prev, space_id: data.space_id }));
        if (!mountName.trim()) setMountName(data.space_name);
        alert(`🎉 微盘空间创建成功！\nSpaceId: ${data.space_id}\n\n该空间已自动对自建应用开放全部权限，且全公司同事在微盘中均可见。已自动加入下方共享列表！`);
        await loadSharedSpaces();
        setWecomMode('shared_list');
      } else {
        alert('创建微盘空间失败: ' + data.message);
      }
    } catch (e: any) {
      alert('调用异常: ' + e.message);
    } finally {
      setCreatingSpace(false);
    }
  };

  // 普通成员免配一键直接挂载共享空间
  const handleMountSharedSpace = async (space: SharedWeComSpace) => {
    try {
      const resp = await apiFetch('/api/v1/knowledge/wecom/mount_shared', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          space_id: space.space_id,
          mount_name: space.space_name,
          authority_tier: authorityTier,
        }),
      });
      const data = await resp.json();
      if (data.success) {
        alert(data.message);
        await loadMounts();
      } else {
        alert('挂载失败: ' + (data.message || data.detail));
      }
    } catch (e: any) {
      alert('挂载请求异常: ' + e.message);
    }
  };

  // 复制 SpaceId 与分享信息
  const handleCopy = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4">
      <div className="relative w-full max-w-3xl rounded-xl border border-border bg-surface-card p-6 shadow-2xl text-foreground max-h-[90vh] overflow-y-auto">
        {/* 顶部标题栏 */}
        <div className="flex items-center justify-between border-b border-border pb-4">
          <div className="flex items-center gap-2">
            <Cloud className="h-5 w-5 text-foreground" />
            <h2 className="text-lg font-semibold text-foreground">CloudVFS 云端虚拟知识库挂载</h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* 适配器选项卡 */}
        <div className="mt-4 flex gap-2 border-b border-border pb-2">
          <button
            onClick={() => setActiveTab('wecom')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              activeTab === 'wecom' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
            }`}
          >
            <HardDrive className="h-4 w-4" /> 企业微信微盘 / 知识库
          </button>
          <button
            onClick={() => setActiveTab('webdav')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              activeTab === 'webdav' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
            }`}
          >
            <Cloud className="h-4 w-4" /> WebDAV (Alist / NAS)
          </button>
          <button
            onClick={() => setActiveTab('oss')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              activeTab === 'oss' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
            }`}
          >
            <Database className="h-4 w-4" /> 对象存储 (S3 / OSS / MinIO)
          </button>
        </div>

        {/* 企业微信微盘特有：模式切换与共享空间展示 */}
        {activeTab === 'wecom' && (
          <div className="mt-3 flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">当前微盘模式:</span>
              <button
                type="button"
                onClick={() => setWecomMode('shared_list')}
                className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  wecomMode === 'shared_list'
                    ? 'bg-primary/20 text-primary border border-primary/30'
                    : 'text-muted-foreground hover:bg-muted'
                }`}
              >
                <Users className="h-3.5 w-3.5" /> 已有共享空间 (免密导入)
              </button>
              <button
                type="button"
                onClick={() => setWecomMode('admin_create')}
                className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  wecomMode === 'admin_create'
                    ? 'bg-primary/20 text-primary border border-primary/30'
                    : 'text-muted-foreground hover:bg-muted'
                }`}
              >
                <ShieldCheck className="h-3.5 w-3.5" /> 管理员 API 建库与配置
              </button>
            </div>
            <span className="text-[11px] text-muted-foreground">
              {wecomMode === 'shared_list' ? '全员免配置直接导入' : '仅管理员可调用 API 创建'}
            </span>
          </div>
        )}

        {/* 区域 A: 已共享微盘空间列表（供普通用户一键使用） */}
        {activeTab === 'wecom' && wecomMode === 'shared_list' && (
          <div className="mt-3 rounded-lg border border-border bg-background p-3">
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                <Share2 className="h-3.5 w-3.5 text-primary" />
                团队微盘共享空间库 ({sharedSpaces.length})
              </h4>
              <button
                type="button"
                onClick={loadSharedSpaces}
                className="text-[11px] text-muted-foreground hover:text-foreground flex items-center gap-1"
              >
                <RefreshCw className="h-3 w-3" /> 刷新
              </button>
            </div>

            {sharedSpaces.length === 0 ? (
              <div className="rounded border border-dashed border-border py-4 text-center text-xs text-muted-foreground">
                <p>暂无由管理员创建或共享的微盘空间</p>
                <button
                  type="button"
                  onClick={() => setWecomMode('admin_create')}
                  className="mt-2 text-primary hover:underline text-xs"
                >
                  点击切换至「管理员 API 建库与配置」创建第一个空间
                </button>
              </div>
            ) : (
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {sharedSpaces.map((s) => (
                  <div
                    key={s.space_id}
                    className="flex items-center justify-between rounded border border-border bg-surface-card p-2 text-xs"
                  >
                    <div>
                      <div className="flex items-center gap-2 font-medium">
                        <span>{s.space_name}</span>
                        <span className="text-[10px] text-muted-foreground">由 {s.admin_userid || '管理员'} 创建</span>
                      </div>
                      <div className="flex items-center gap-2 mt-1 font-mono text-[11px] text-muted-foreground">
                        <span>ID: {s.space_id}</span>
                        <button
                          type="button"
                          onClick={() => handleCopy(s.space_id, s.space_id)}
                          className="hover:text-foreground flex items-center gap-0.5"
                          title="复制 SpaceId"
                        >
                          {copiedId === s.space_id ? (
                            <CheckCircle2 className="h-3 w-3 text-green-500" />
                          ) : (
                            <Copy className="h-3 w-3" />
                          )}
                          {copiedId === s.space_id ? '已复制' : '复制ID'}
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            handleCopy(
                              `【微盘共享规程库】${s.space_name}\nSpaceId: ${s.space_id}\n请在 AgentHub 中直接挂载使用。`,
                              `share_${s.space_id}`
                            )
                          }
                          className="hover:text-foreground flex items-center gap-0.5 text-primary"
                          title="复制完整分享卡片"
                        >
                          {copiedId === `share_${s.space_id}` ? (
                            <CheckCircle2 className="h-3 w-3 text-green-500" />
                          ) : (
                            <Share2 className="h-3 w-3" />
                          )}
                          {copiedId === `share_${s.space_id}` ? '已复制' : '分享'}
                        </button>
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={() => handleMountSharedSpace(s)}
                      className="rounded bg-primary px-3 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
                    >
                      📥 免配一键挂载
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 区域 B: 表单输入区 */}
        <div className="mt-4 grid grid-cols-2 gap-4">
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">挂载展示名称</label>
              <input
                type="text"
                placeholder="如: 赛事管理知识库 / 财务规程"
                value={mountName}
                onChange={(e) => setMountName(e.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">权威等级仲裁</label>
              <select
                value={authorityTier}
                onChange={(e) => setAuthorityTier(Number(e.target.value))}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:border-primary"
              >
                <option value={1}>Tier 1: 正式发布库 / 定稿发布 (权重 1.25)</option>
                <option value={2}>Tier 2: 普通工作区当前态 (权重 1.00)</option>
                <option value={3}>Tier 3: 草稿与归档旧版 (权重 0.60)</option>
              </select>
            </div>
          </div>

          <div className="space-y-3 rounded-lg border border-border bg-background p-3">
            {activeTab === 'wecom' && (
              <>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">CorpId</label>
                  <input
                    type="text"
                    value={wecomForm.corpid}
                    onChange={(e) => setWecomForm({ ...wecomForm, corpid: e.target.value })}
                    className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-sm outline-none"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">CorpSecret</label>
                  <input
                    type="password"
                    value={wecomForm.corpsecret}
                    onChange={(e) => setWecomForm({ ...wecomForm, corpsecret: e.target.value })}
                    className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-sm outline-none"
                  />
                </div>

                {wecomMode === 'admin_create' && (
                  <div className="rounded border border-primary/30 bg-primary/5 p-2 text-xs space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-primary flex items-center gap-1">
                        <Sparkles className="h-3.5 w-3.5" /> API 自动建库 (绕过 640008 权限拦截)
                      </span>
                    </div>
                    <div>
                      <label className="text-[11px] text-muted-foreground">管理员企业微信 UserID (可选)</label>
                      <input
                        type="text"
                        placeholder="如 ZhangSan (不填则默认为应用自身)"
                        value={adminUserId}
                        onChange={(e) => setAdminUserId(e.target.value)}
                        className="mt-0.5 w-full rounded border border-border bg-surface-card px-2 py-1 text-xs outline-none"
                      />
                    </div>
                    <button
                      type="button"
                      onClick={handleCreateSpace}
                      disabled={creatingSpace}
                      className="w-full flex items-center justify-center gap-1 rounded bg-primary py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                    >
                      <Sparkles className="h-3 w-3" />
                      {creatingSpace ? '正在通过企微 API 创建空间...' : '立即在企业微信微盘中新建空间'}
                    </button>
                    <p className="text-[10px] text-muted-foreground leading-tight">
                      由应用 API 创建的空间将赋予应用完全管理员权限，且自动授权全公司可见，一键即可同步！
                    </p>
                  </div>
                )}

                <div>
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-muted-foreground">微盘 SpaceId (必填)</label>
                    <span className="text-[10px] text-amber-500 font-mono">必填</span>
                  </div>
                  <input
                    type="text"
                    placeholder="如 s.1970... 或直接粘贴分享链接"
                    value={wecomForm.space_id}
                    onChange={(e) => setWecomForm({ ...wecomForm, space_id: e.target.value })}
                    className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-sm outline-none"
                  />
                </div>
              </>
            )}

            {activeTab === 'webdav' && (
              <>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">WebDAV 端点 URL</label>
                  <input
                    type="text"
                    placeholder="http://127.0.0.1:5244/dav"
                    value={webdavForm.base_url}
                    onChange={(e) => setWebdavForm({ ...webdavForm, base_url: e.target.value })}
                    className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-sm outline-none"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">用户名</label>
                    <input
                      type="text"
                      value={webdavForm.username}
                      onChange={(e) => setWebdavForm({ ...webdavForm, username: e.target.value })}
                      className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-sm outline-none"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">密码</label>
                    <input
                      type="password"
                      value={webdavForm.password}
                      onChange={(e) => setWebdavForm({ ...webdavForm, password: e.target.value })}
                      className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-sm outline-none"
                    />
                  </div>
                </div>
              </>
            )}

            {activeTab === 'oss' && (
              <>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Endpoint 与 Bucket</label>
                  <div className="grid grid-cols-2 gap-2 mt-1">
                    <input
                      type="text"
                      placeholder="http://s3.amazonaws.com"
                      value={ossForm.endpoint}
                      onChange={(e) => setOssForm({ ...ossForm, endpoint: e.target.value })}
                      className="w-full rounded border border-border bg-surface-card px-2 py-1 text-sm outline-none"
                    />
                    <input
                      type="text"
                      placeholder="bucket-name"
                      value={ossForm.bucket}
                      onChange={(e) => setOssForm({ ...ossForm, bucket: e.target.value })}
                      className="w-full rounded border border-border bg-surface-card px-2 py-1 text-sm outline-none"
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">AccessKey</label>
                    <input
                      type="text"
                      value={ossForm.access_key}
                      onChange={(e) => setOssForm({ ...ossForm, access_key: e.target.value })}
                      className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-sm outline-none"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">SecretKey</label>
                    <input
                      type="password"
                      value={ossForm.secret_key}
                      onChange={(e) => setOssForm({ ...ossForm, secret_key: e.target.value })}
                      className="mt-1 w-full rounded border border-border bg-surface-card px-2 py-1 text-sm outline-none"
                    />
                  </div>
                </div>
              </>
            )}

            {/* 测试连通性回执 */}
            {testResult && (
              <div
                className={`flex items-center gap-2 rounded p-2 text-xs ${
                  testResult.success ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-500'
                }`}
              >
                {testResult.success ? <Check className="h-3.5 w-3.5" /> : <AlertCircle className="h-3.5 w-3.5" />}
                {testResult.message}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={handleTestConnection}
                disabled={testing}
                className="flex items-center gap-1 rounded border border-border px-3 py-1 text-xs hover:bg-muted"
              >
                <PlugZap className="h-3 w-3" /> {testing ? '测试中...' : '测试连通性'}
              </button>
              <button
                type="button"
                onClick={handleSaveMount}
                disabled={loading}
                className="rounded bg-primary px-3 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
              >
                保存挂载
              </button>
            </div>
          </div>
        </div>

        {/* 已挂载列表 */}
        <div className="mt-6 border-t border-border pt-4">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-foreground">已挂载的虚拟知识库列表 ({mountsList.length})</h3>
            <button
              onClick={handleSyncAll}
              disabled={syncing || mountsList.length === 0}
              className="flex items-center gap-1 rounded border border-border px-2.5 py-1 text-xs hover:bg-muted"
            >
              <RefreshCw className={`h-3 w-3 ${syncing ? 'animate-spin' : ''}`} />
              {syncing ? '增量同步中...' : '一键增量同步脑索引'}
            </button>
          </div>

          <div className="max-h-40 overflow-y-auto space-y-2">
            {mountsList.length === 0 ? (
              <div className="py-4 text-center text-xs text-muted-foreground">暂未挂载任何外部知识库</div>
            ) : (
              mountsList.map((m) => (
                <div
                  key={m.mount_id}
                  className="flex items-center justify-between rounded-lg border border-border bg-background p-2.5 text-xs"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{m.mount_name}</span>
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground uppercase">
                      {m.provider_type}
                    </span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] ${
                        m.authority_tier === 1
                          ? 'bg-primary/10 text-primary'
                          : m.authority_tier === 2
                          ? 'bg-muted text-foreground'
                          : 'bg-amber-500/10 text-amber-500'
                      }`}
                    >
                      Tier {m.authority_tier}
                    </span>
                  </div>
                  <button
                    onClick={() => handleDeleteMount(m.mount_id)}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
