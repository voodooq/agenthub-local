'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { CheckSquare, Sparkles, Square } from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface EntitySummary {
  id: string;
  table_name: string;
  display_name: string;
  category: string;
}

interface RelationSummary {
  source_entity_id: string;
  target_entity_id: string;
  relation_type: string;
}

interface MountedEntitiesSelectorProps {
  profileId: string;
  valueText: string;
  onChangeText: (next: string) => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  CORE_MAIN: '核心主表',
  DETAIL: '明细表',
  CONFIG: '配置字典',
  LOG: '日志审计',
};

export function MountedEntitiesSelector({
  profileId,
  valueText,
  onChangeText,
}: MountedEntitiesSelectorProps) {
  const [entities, setEntities] = useState<EntitySummary[]>([]);
  const [relations, setRelations] = useState<RelationSummary[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!profileId) {
      setEntities([]);
      setRelations([]);
      return;
    }
    setLoading(true);
    apiFetch(`/api/v1/profiles/${encodeURIComponent(profileId)}/graph`)
      .then(async (res) => {
        if (res.ok) {
          const json = await res.json();
          setEntities(json.data?.entities || []);
          setRelations(json.data?.relations || []);
        }
      })
      .finally(() => setLoading(false));
  }, [profileId]);

  const isAll = !valueText.trim() || valueText.trim() === '*';

  const selectedSet = useMemo(() => {
    if (isAll) return new Set(entities.map((e) => e.table_name));
    return new Set(
      valueText
        .split(/[,，\n]/)
        .map((s) => s.trim())
        .filter(Boolean)
    );
  }, [isAll, valueText, entities]);

  // 依据 Relation 自动推导已选明细表所依赖的主表
  const recommendedTables = useMemo(() => {
    if (isAll) return new Set<string>();
    const idToTable = new Map(entities.map((e) => [e.id, e.table_name]));
    const rec = new Set<string>();
    for (const rel of relations) {
      if (rel.relation_type === 'DO_NOT_JOIN') continue;
      const srcT = idToTable.get(rel.source_entity_id);
      const tgtT = idToTable.get(rel.target_entity_id);
      if (srcT && tgtT && selectedSet.has(srcT) && !selectedSet.has(tgtT)) {
        rec.add(tgtT);
      }
    }
    return rec;
  }, [isAll, entities, relations, selectedSet]);

  const grouped = useMemo(() => {
    const map: Record<string, EntitySummary[]> = {};
    for (const ent of entities) {
      const cat = ent.category || 'CORE_MAIN';
      if (!map[cat]) map[cat] = [];
      map[cat].push(ent);
    }
    return map;
  }, [entities]);

  const toggleTable = (tableName: string) => {
    const next = new Set(selectedSet);
    if (next.has(tableName)) {
      next.delete(tableName);
    } else {
      next.add(tableName);
    }
    if (next.size === entities.length && entities.length > 0) {
      onChangeText('*');
    } else {
      onChangeText(Array.from(next).join(', '));
    }
  };

  if (!profileId) return null;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10.5px] text-muted-foreground">
          可访问子集表（{isAll ? `全量 ${entities.length} 张` : `已选 ${selectedSet.size} / ${entities.length}`}）
        </span>
        <div className="flex items-center gap-2 text-[10.5px]">
          <button
            type="button"
            onClick={() => onChangeText('*')}
            className="text-emerald-500 hover:underline flex items-center gap-0.5"
          >
            <CheckSquare className="w-3 h-3" />
            <span>全选</span>
          </button>
          <button
            type="button"
            onClick={() => onChangeText('')}
            className="text-muted-foreground hover:text-foreground flex items-center gap-0.5"
          >
            <Square className="w-3 h-3" />
            <span>清空</span>
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-[11px] text-muted-foreground py-2">正在加载表结构...</div>
      ) : entities.length === 0 ? (
        <div className="text-[11px] text-muted-foreground py-1">该空间暂无实体表</div>
      ) : (
        <div className="max-h-44 overflow-y-auto rounded-lg border border-border bg-background p-2 space-y-2">
          {Object.entries(grouped).map(([cat, list]) => (
            <div key={cat} className="space-y-1">
              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                {CATEGORY_LABELS[cat] || cat}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1">
                {list.map((ent) => {
                  const checked = selectedSet.has(ent.table_name);
                  const isRecommended = recommendedTables.has(ent.table_name);
                  return (
                    <label
                      key={ent.id}
                      className={`flex items-center gap-1.5 px-2 py-1 rounded border text-[11px] cursor-pointer transition-colors ${
                        checked
                          ? 'border-emerald-500/40 bg-emerald-500/10 text-foreground'
                          : isRecommended
                          ? 'border-amber-500/50 bg-amber-500/10 text-amber-500'
                          : 'border-border/60 hover:bg-surface-100 text-muted-foreground'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleTable(ent.table_name)}
                        className="accent-emerald-500 shrink-0"
                      />
                      <span className="font-mono truncate">{ent.table_name}</span>
                      {isRecommended && (
                        <span className="ml-auto text-[9px] flex items-center gap-0.5 shrink-0" title="关联主表建议勾选">
                          <Sparkles className="w-2.5 h-2.5" />
                          依赖
                        </span>
                      )}
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

