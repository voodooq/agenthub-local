import React, { useState, useEffect } from 'react';
import {
  Database,
  Shield,
  Key,
  Copy,
  Plus,
  Trash2,
  Check,
  Globe,
  Sliders,
  AlertTriangle,
  Code2,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

export interface BoundKBItem {
  kb_id: string;
  name?: string;
  priority_weight: number;
  top_k_override: number;
}

interface ApiKeyItem {
  key_id: string;
  key_name: string;
  key_prefix: string;
  rate_limit_rpm: number;
  total_calls: number;
  last_used_at: string | null;
  created_at: string;
}

interface ExpertKnowledgeAndApiPanelProps {
  agentId?: string;
  kbIsolationMode: string;
  onChangeIsolationMode: (mode: string) => void;
  strictGrounding: boolean;
  onChangeStrictGrounding: (enabled: boolean) => void;
  minRelevanceScore: number;
  onChangeMinRelevanceScore: (score: number) => void;
  apiServiceEnabled: boolean;
  onChangeApiServiceEnabled: (enabled: boolean) => void;
  apiModelSlug: string;
  onChangeApiModelSlug: (slug: string) => void;
  boundKbs: BoundKBItem[];
  onChangeBoundKbs: (kbs: BoundKBItem[]) => void;
}

export function ExpertKnowledgeAndApiPanel({
  agentId,
  kbIsolationMode,
  onChangeIsolationMode,
  strictGrounding,
  onChangeStrictGrounding,
  minRelevanceScore,
  onChangeMinRelevanceScore,
  apiServiceEnabled,
  onChangeApiServiceEnabled,
  apiModelSlug,
  onChangeApiModelSlug,
  boundKbs,
  onChangeBoundKbs,
}: ExpertKnowledgeAndApiPanelProps) {
  const [availableKbs, setAvailableKbs] = useState<any[]>([]);
  const [loadingKbs, setLoadingKbs] = useState(false);
  const [kbSearch, setKbSearch] = useState('');

  // API Keys 状态
  const [apiKeys, setApiKeys] = useState<ApiKeyItem[]>([]);
  const [loadingKeys, setLoadingKeys] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [creatingKey, setCreatingKey] = useState(false);
  const [generatedPlainKey, setGeneratedPlainKey] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedCurl, setCopiedCurl] = useState(false);

  useEffect(() => {
    loadAllKbs();
    if (agentId) {
      loadAgentApiKeys();
    }
  }, [agentId]);

  const loadAllKbs = async () => {
    setLoadingKbs(true);
    try {
      const res = await apiFetch('/api/v1/kb/bases?published_only=true');
      if (res.ok) {
        const json = await res.json();
        setAvailableKbs((json.data || []).filter((k: any) => Boolean(k.is_published)));
      }
    } catch (e) {
      console.error('加载知识库列表失败:', e);
    } finally {
      setLoadingKbs(false);
    }
  };

  const loadAgentApiKeys = async () => {
    if (!agentId) return;
    setLoadingKeys(true);
    try {
      const res = await apiFetch(`/api/v1/agents/${agentId}/api-keys`);
      if (res.ok) {
        const json = await res.json();
        setApiKeys(json.data || []);
      }
    } catch (e) {
      console.error('加载 API Keys 失败:', e);
    } finally {
      setLoadingKeys(false);
    }
  };

  const handleCreateApiKey = async () => {
    if (!agentId) {
      alert('请先保存创建此专家，然后再签发对外 API 密钥。');
      return;
    }
    const name = newKeyName.trim() || '外部系统访问Key';
    setCreatingKey(true);
    try {
      const res = await apiFetch(`/api/v1/agents/${agentId}/api-keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key_name: name, rate_limit_rpm: 60 }),
      });
      if (res.ok) {
        const json = await res.json();
        setGeneratedPlainKey(json.data?.api_key || null);
        setNewKeyName('');
        await loadAgentApiKeys();
      }
    } catch (e: any) {
      alert(`创建密钥失败: ${e.message || e}`);
    } finally {
      setCreatingKey(false);
    }
  };

  const handleRevokeApiKey = async (keyId: string) => {
    if (!agentId || !confirm('确定要吊销此 API 密钥吗？外部系统将立即无法访问。')) return;
    try {
      await apiFetch(`/api/v1/agents/${agentId}/api-keys/${keyId}`, { method: 'DELETE' });
      await loadAgentApiKeys();
    } catch (e: any) {
      alert(`吊销失败: ${e.message || e}`);
    }
  };

  const toggleKb = (kbId: string, kbName: string) => {
    const exists = boundKbs.some((b) => b.kb_id === kbId);
    if (exists) {
      onChangeBoundKbs(boundKbs.filter((b) => b.kb_id !== kbId));
    } else {
      onChangeBoundKbs([
        ...boundKbs,
        { kb_id: kbId, name: kbName, priority_weight: 1.0, top_k_override: 5 },
      ]);
    }
  };

  const updateKbWeight = (kbId: string, weight: number) => {
    onChangeBoundKbs(
      boundKbs.map((b) => (b.kb_id === kbId ? { ...b, priority_weight: weight } : b))
    );
  };

  const filteredAvailableKbs = availableKbs.filter((k) =>
    (k.name || k.kb_id).toLowerCase().includes(kbSearch.toLowerCase())
  );

  const curlExample = `curl http://127.0.0.1:8000/api/v1/external/chat/completions \\
  -H "Authorization: Bearer ${generatedPlainKey || 'sk-ah-your-key-here'}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${apiModelSlug || (agentId ? `expert-${agentId}` : 'expert-default')}",
    "messages": [{"role": "user", "content": "请根据现行规程解答选手年龄与护具要求"}],
    "stream": true
  }'`;

  return (
    <div className="space-y-4 text-xs">
      {/* 1. 知识库专属挂载与权重 */}
      <div className="p-3.5 rounded-xl border border-border bg-surface-100 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Database className="w-4 h-4 text-sky-500" />
            <span className="font-semibold text-foreground">专属规程知识底座与权重</span>
          </div>
          <span className="text-[11px] text-muted-foreground">已挂载 {boundKbs.length} 个知识库</span>
        </div>

        {/* 知识底座搜索过滤 */}
        <input
          type="text"
          placeholder="搜索知识库名称或年份..."
          value={kbSearch}
          onChange={(e) => setKbSearch(e.target.value)}
          className="w-full px-3 py-1.5 rounded-lg bg-surface-200 border border-border text-foreground text-xs placeholder:text-muted-foreground focus:outline-none focus:border-foreground/40"
        />

        {/* 可选与已绑定列表 */}
        <div className="max-h-44 overflow-y-auto space-y-2 pr-1">
          {filteredAvailableKbs.length === 0 ? (
            <div className="text-center py-4 text-muted-foreground">
              {kbSearch.trim()
                ? '未找到匹配的已发布知识库'
                : '暂无已发布的知识库，请先在 LLMwiki 中完成训练并发布版本'}
            </div>
          ) : (
            filteredAvailableKbs.map((kb) => {
              const bound = boundKbs.find((b) => b.kb_id === kb.kb_id);
              const isChecked = Boolean(bound);

              return (
                <div
                  key={kb.kb_id}
                  className={`p-2.5 rounded-lg border transition-all ${
                    isChecked
                      ? 'border-sky-500/40 bg-sky-500/5'
                      : 'border-border/60 bg-surface-50 hover:border-border'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <label className="flex items-center gap-2 cursor-pointer flex-1 min-w-0">
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleKb(kb.kb_id, kb.name)}
                        className="rounded border-border text-sky-500 focus:ring-0"
                      />
                      <span className="font-medium text-foreground truncate">{kb.name}</span>
                      <span className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-surface-200 text-muted-foreground border border-border">
                        {kb.active_version}
                      </span>
                    </label>

                    {isChecked && (
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[10px] text-muted-foreground font-mono">
                          权重: {bound?.priority_weight.toFixed(1)}x
                        </span>
                        <input
                          type="range"
                          min="0.5"
                          max="2.0"
                          step="0.1"
                          value={bound?.priority_weight ?? 1.0}
                          onChange={(e) => updateKbWeight(kb.kb_id, parseFloat(e.target.value))}
                          className="w-16 h-1 accent-sky-500 cursor-pointer"
                          title="调节此知识库在专家检索时的加权倍率"
                        />
                      </div>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* 隔离模式选择 */}
        <div className="pt-2 border-t border-border/60 flex items-center justify-between gap-3">
          <div className="space-y-0.5">
            <span className="font-medium text-foreground text-[11px]">物理级知识库硬隔离</span>
            <p className="text-[10px] text-muted-foreground">严格隔离时专家仅检索挂载知识库，屏蔽全局其他无关库</p>
          </div>
          <select
            value={kbIsolationMode}
            onChange={(e) => onChangeIsolationMode(e.target.value)}
            className="px-2 py-1 rounded-lg bg-surface-200 border border-border text-xs text-foreground focus:outline-none"
          >
            <option value="strict_bound_only">严格物理隔离 (推荐)</option>
            <option value="bound_plus_at">允许对话 @ 临时外挂</option>
          </select>
        </div>
      </div>

      {/* 2. 严格依据作答与防幻觉熔断 (Strict Grounding) */}
      <div className="p-3.5 rounded-xl border border-border bg-surface-100 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Shield className="w-4 h-4 text-emerald-500" />
            <span className="font-semibold text-foreground">严格依据作答与防幻觉熔断</span>
          </div>
          <label className="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              checked={strictGrounding}
              onChange={(e) => onChangeStrictGrounding(e.target.checked)}
              className="sr-only peer"
            />
            <div className="w-9 h-5 bg-surface-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-500"></div>
          </label>
        </div>

        <p className="text-[11px] text-muted-foreground leading-relaxed">
          开启后，当检索相关度低于阈值时，直接触发安全拒答（如：“当前规程中暂未查到明确条款”），严禁大模型靠通用预训练臆想编造。
        </p>

        {strictGrounding && (
          <div className="flex items-center justify-between gap-4 p-2 rounded-lg bg-surface-200/60 border border-border/60">
            <span className="text-[11px] text-foreground font-medium">拒答语义相似度阈值:</span>
            <div className="flex items-center gap-2">
              <input
                type="range"
                min="0.25"
                max="0.75"
                step="0.01"
                value={minRelevanceScore}
                onChange={(e) => onChangeMinRelevanceScore(parseFloat(e.target.value))}
                className="w-24 h-1 accent-emerald-500 cursor-pointer"
              />
              <span className="font-mono text-emerald-600 dark:text-emerald-400 font-bold text-xs">
                {minRelevanceScore.toFixed(2)}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* 3. 对外 OpenAI 兼容 API 服务化 */}
      <div className="p-3.5 rounded-xl border border-border bg-surface-100 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Globe className="w-4 h-4 text-purple-500" />
            <span className="font-semibold text-foreground">对外 OpenAI 兼容 API 服务化</span>
          </div>
          <label className="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              checked={apiServiceEnabled}
              onChange={(e) => onChangeApiServiceEnabled(e.target.checked)}
              className="sr-only peer"
            />
            <div className="w-9 h-5 bg-surface-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-purple-600"></div>
          </label>
        </div>

        {apiServiceEnabled && (
          <div className="space-y-3 pt-2 border-t border-border/60">
            <div>
              <label className="block text-[11px] font-medium text-foreground mb-1">
                对外服务别名 (Model Slug)
              </label>
              <input
                type="text"
                placeholder={agentId ? `expert-${agentId}` : 'expert-fencing-2026'}
                value={apiModelSlug}
                onChange={(e) => onChangeApiModelSlug(e.target.value)}
                className="w-full px-3 py-1.5 rounded-lg bg-surface-200 border border-border text-foreground font-mono text-xs focus:outline-none"
              />
            </div>

            {/* 密钥生成与列表 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-foreground">API Keys ({apiKeys.length})</span>
                <div className="flex items-center gap-1.5">
                  <input
                    type="text"
                    placeholder="密钥备注名称..."
                    value={newKeyName}
                    onChange={(e) => setNewKeyName(e.target.value)}
                    className="px-2 py-1 rounded bg-surface-200 border border-border text-[11px] w-28 focus:outline-none"
                  />
                  <button
                    type="button"
                    disabled={creatingKey}
                    onClick={handleCreateApiKey}
                    className="px-2 py-1 rounded bg-purple-600 hover:bg-purple-500 text-white text-[11px] font-medium transition flex items-center gap-1"
                  >
                    <Plus className="w-3 h-3" />
                    <span>生成Key</span>
                  </button>
                </div>
              </div>

              {/* 明文展示横幅 */}
              {generatedPlainKey && (
                <div className="p-2.5 rounded-lg border border-purple-300 dark:border-purple-800 bg-purple-50 dark:bg-purple-950/30 text-purple-800 dark:text-purple-200 flex items-center justify-between gap-2">
                  <div className="truncate font-mono font-bold text-xs">{generatedPlainKey}</div>
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard.writeText(generatedPlainKey);
                      setCopiedKey(true);
                      setTimeout(() => setCopiedKey(false), 2000);
                    }}
                    className="px-2 py-1 bg-purple-600 text-white rounded text-[10px] font-medium shrink-0 flex items-center gap-1"
                  >
                    {copiedKey ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                    <span>{copiedKey ? '已复制' : '复制明文'}</span>
                  </button>
                </div>
              )}

              {/* 现有 Key 列表 */}
              <div className="space-y-1.5 max-h-32 overflow-y-auto">
                {apiKeys.length === 0 ? (
                  <div className="text-[11px] text-muted-foreground py-2 text-center">暂未签发 API 密钥</div>
                ) : (
                  apiKeys.map((k) => (
                    <div
                      key={k.key_id}
                      className="flex items-center justify-between p-2 rounded-lg bg-surface-200/80 border border-border text-[11px]"
                    >
                      <div className="space-y-0.5 truncate flex-1 min-w-0 pr-2">
                        <div className="font-mono font-medium text-foreground truncate">{k.key_prefix}</div>
                        <div className="text-[10px] text-muted-foreground">
                          {k.key_name} · 调用: {k.total_calls}次 · 限流: {k.rate_limit_rpm}RPM
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleRevokeApiKey(k.key_id)}
                        className="text-rose-500 hover:text-rose-600 p-1 rounded hover:bg-rose-500/10"
                        title="吊销删除"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* 一键复制 curl 代码 */}
            <div className="pt-2 border-t border-border/60">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] font-medium text-muted-foreground flex items-center gap-1">
                  <Code2 className="w-3 h-3" /> cURL 调用示例
                </span>
                <button
                  type="button"
                  onClick={() => {
                    navigator.clipboard.writeText(curlExample);
                    setCopiedCurl(true);
                    setTimeout(() => setCopiedCurl(false), 2000);
                  }}
                  className="text-[10px] text-purple-600 dark:text-purple-400 hover:underline flex items-center gap-1"
                >
                  {copiedCurl ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                  <span>{copiedCurl ? '已复制' : '复制指令'}</span>
                </button>
              </div>
              <pre className="p-2 rounded bg-zinc-950 text-zinc-300 font-mono text-[10px] overflow-x-auto leading-relaxed">
                {curlExample}
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
