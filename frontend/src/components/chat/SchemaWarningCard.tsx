'use client';

import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Plus, X } from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface SchemaWarningCardProps {
  warningText: string;
  defaultProfileId?: string;
}

interface ProfileOption {
  id: string;
  name: string;
  description?: string;
}

interface EntityOption {
  table_name: string;
  display_name?: string;
}

export function SchemaWarningCard({ warningText, defaultProfileId = '' }: SchemaWarningCardProps) {
  const [showModal, setShowModal] = useState(false);
  const [profiles, setProfiles] = useState<ProfileOption[]>([]);
  const [entities, setEntities] = useState<EntityOption[]>([]);
  const [profileId, setProfileId] = useState(defaultProfileId);
  // 沉淀方式：global_rule（全局口径红线，无需填表名） | table_relation（表间关系红线）
  const [saveMode, setSaveMode] = useState<'global_rule' | 'table_relation'>('global_rule');
  const [sourceTable, setSourceTable] = useState('');
  const [sourceField, setSourceField] = useState('id');
  const [targetTable, setTargetTable] = useState('');
  const [targetField, setTargetField] = useState('id');
  const [relationType, setRelationType] = useState('DO_NOT_JOIN');
  const [warningMsg, setWarningMsg] = useState(warningText);
  const [saving, setSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState(false);

  useEffect(() => {
    if (!showModal) return;
    apiFetch('/api/v1/profiles')
      .then(async (res) => {
        if (res.ok) {
          const json = await res.json();
          const list: ProfileOption[] = json.data || [];
          setProfiles(list);
          if (!profileId && list.length > 0) {
            setProfileId(list[0].id);
          }
        }
      })
      .catch(() => {});
  }, [showModal, profileId]);

  useEffect(() => {
    if (!showModal || !profileId) return;
    apiFetch(`/api/v1/profiles/${encodeURIComponent(profileId)}/graph`)
      .then(async (res) => {
        if (res.ok) {
          const json = await res.json();
          const ents: EntityOption[] = (json.data?.entities || []).map((e: any) => ({
            table_name: String(e.table_name || ''),
            display_name: String(e.display_name || ''),
          }));
          setEntities(ents);
        }
      })
      .catch(() => {});
  }, [showModal, profileId]);

  const handleOpen = () => {
    // 尝试从告警文本中自动提取反引号包裹的表名
    const tables = Array.from(warningText.matchAll(/`([a-zA-Z0-9_]+)`/g))
      .map((m) => m[1])
      .filter((t) => t.includes('_') && !['pay_fee', 'create_time', 'pay_start_time', 'pid'].includes(t));
    const hasTables = tables.length > 0;
    setSaveMode(hasTables ? 'table_relation' : 'global_rule');
    setSourceTable(tables[0] || '');
    setTargetTable(tables[1] || tables[0] || '');
    setWarningMsg(warningText);
    setErrorMsg(null);
    setShowModal(true);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!profileId || !warningMsg.trim()) {
      setErrorMsg('请选择目标数据结构空间并填写红线避坑提示');
      return;
    }
    setSaving(true);
    setErrorMsg(null);
    try {
      if (saveMode === 'global_rule') {
        // 全局业务口径红线：直接追加沉淀到目标空间的业务口径说明（Wiki）中，无需强填表名
        const targetProf = profiles.find((p) => p.id === profileId);
        const existingDesc = (targetProf?.description || '').trim();
        const ruleLine = `- ${warningMsg.trim().replace(/^-\s*/, '')}`;
        const nextDesc = existingDesc.includes(ruleLine)
          ? existingDesc
          : existingDesc
            ? `${existingDesc}\n${ruleLine}`
            : `### 业务口径与避坑红线\n${ruleLine}`;
        const res = await apiFetch(`/api/v1/profiles/${encodeURIComponent(profileId)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ description: nextDesc }),
        });
        if (res.ok) {
          setShowModal(false);
          setSavedNotice(true);
        } else {
          const j = await res.json().catch(() => ({}));
          setErrorMsg(j.detail || '沉淀全局口径红线失败');
        }
        return;
      }

      const src = sourceTable.trim();
      const tgt = (targetTable.trim() || src);
      if (!src) {
        setErrorMsg('请填写或从下拉列表选择源表，或切换为「全局口径红线」');
        return;
      }
      const res = await apiFetch('/api/v1/relations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile_id: profileId,
          source_entity_id: `${profileId}.${src}`,
          source_field: sourceField.trim() || 'id',
          target_entity_id: `${profileId}.${tgt}`,
          target_field: targetField.trim() || 'id',
          relation_type: relationType,
          cardinality: 'N:1',
          is_warning: true,
          warning_message: warningMsg.trim(),
        }),
      });
      if (res.ok) {
        setShowModal(false);
        setSavedNotice(true);
      } else {
        const j = await res.json().catch(() => ({}));
        setErrorMsg(j.detail || '沉淀表间关系红线失败');
      }
    } catch (err: any) {
      setErrorMsg(err?.message || '网络异常，请稍后重试');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="my-2 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 font-semibold text-rose-500">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span>数据结构红线审计告警</span>
        </div>
        {savedNotice ? (
          <span className="inline-flex items-center gap-1 text-[11px] text-emerald-500 font-medium">
            <CheckCircle2 className="w-3.5 h-3.5" />
            已沉淀至图谱红线
          </span>
        ) : (
          <button
            type="button"
            onClick={handleOpen}
            className="px-2 py-1 rounded border border-rose-500/40 bg-rose-500/15 hover:bg-rose-500/25 text-rose-400 text-[11px] font-medium flex items-center gap-1"
          >
            <Plus className="w-3 h-3" />
            <span>沉淀为 Profile 红线</span>
          </button>
        )}
      </div>
      <div className="mt-1.5 text-[11.5px] text-foreground/90 whitespace-pre-wrap leading-relaxed">
        {warningText}
      </div>

      {showModal && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <form
            onSubmit={handleSave}
            className="w-full max-w-md rounded-xl border border-border bg-surface-card p-5 shadow-2xl space-y-3 text-xs text-foreground"
          >
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-semibold">沉淀为 Schema Profile 图谱红线</h4>
              <button type="button" onClick={() => setShowModal(false)} className="text-muted-foreground">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div>
              <label className="block text-[11px] text-muted-foreground mb-1">目标数据结构空间 *</label>
              <select
                required
                value={profileId}
                onChange={(e) => setProfileId(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
              >
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-[11px] text-muted-foreground mb-1">沉淀方式</label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setSaveMode('global_rule');
                    setErrorMsg(null);
                  }}
                  className={`px-2.5 py-1.5 rounded-lg border text-xs font-medium transition-colors ${
                    saveMode === 'global_rule'
                      ? 'bg-rose-500/15 border-rose-500/50 text-rose-400'
                      : 'bg-surface-100 border-border text-muted-foreground hover:text-foreground'
                  }`}
                >
                  全局口径红线（免填表名）
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setSaveMode('table_relation');
                    setErrorMsg(null);
                  }}
                  className={`px-2.5 py-1.5 rounded-lg border text-xs font-medium transition-colors ${
                    saveMode === 'table_relation'
                      ? 'bg-rose-500/15 border-rose-500/50 text-rose-400'
                      : 'bg-surface-100 border-border text-muted-foreground hover:text-foreground'
                  }`}
                >
                  表间关系红线（指定表）
                </button>
              </div>
            </div>

            {saveMode === 'table_relation' && (
              <>
                <datalist id="schema-warning-table-options">
                  {entities.map((ent) => (
                    <option key={ent.table_name} value={ent.table_name}>
                      {ent.display_name ? `${ent.table_name} (${ent.display_name})` : ent.table_name}
                    </option>
                  ))}
                </datalist>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-[11px] text-muted-foreground mb-1">源表 *</label>
                    <input
                      list="schema-warning-table-options"
                      value={sourceTable}
                      onChange={(e) => setSourceTable(e.target.value)}
                      placeholder="输入或选择表名"
                      className="w-full px-2 py-1 rounded border border-border bg-surface-100 font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] text-muted-foreground mb-1">源字段</label>
                    <input
                      value={sourceField}
                      onChange={(e) => setSourceField(e.target.value)}
                      className="w-full px-2 py-1 rounded border border-border bg-surface-100 font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] text-muted-foreground mb-1">目标表（留空则同源表）</label>
                    <input
                      list="schema-warning-table-options"
                      value={targetTable}
                      onChange={(e) => setTargetTable(e.target.value)}
                      placeholder="留空默认与源表相同"
                      className="w-full px-2 py-1 rounded border border-border bg-surface-100 font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] text-muted-foreground mb-1">目标字段</label>
                    <input
                      value={targetField}
                      onChange={(e) => setTargetField(e.target.value)}
                      className="w-full px-2 py-1 rounded border border-border bg-surface-100 font-mono"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-[11px] text-muted-foreground mb-1">关系 / 红线类型</label>
                  <select
                    value={relationType}
                    onChange={(e) => setRelationType(e.target.value)}
                    className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
                  >
                    <option value="DO_NOT_JOIN">DO_NOT_JOIN (高危禁连红线)</option>
                    <option value="LEFT_JOIN">LEFT_JOIN (强制左连接)</option>
                    <option value="LOGICAL_JOIN">LOGICAL_JOIN (业务逻辑关联)</option>
                  </select>
                </div>
              </>
            )}

            <div>
              <label className="block text-[11px] text-muted-foreground mb-1">红线避坑提示 *</label>
              <textarea
                required
                rows={3}
                value={warningMsg}
                onChange={(e) => setWarningMsg(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
              />
            </div>

            {errorMsg && (
              <div className="px-2.5 py-1.5 rounded-lg bg-red-500/10 border border-red-500/30 text-[11px] text-red-400">
                {errorMsg}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setShowModal(false)}
                className="px-3 py-1.5 rounded-lg border border-border"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={saving}
                className="px-3 py-1.5 rounded-lg bg-rose-600 text-white font-medium disabled:opacity-40"
              >
                {saving ? '保存中...' : '确认沉淀'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

