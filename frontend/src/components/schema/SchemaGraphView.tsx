'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import dagre from '@dagrejs/dagre';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
  type NodeProps,
  type EdgeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  AlertTriangle,
  BookOpen,
  Copy,
  Database,
  Download,
  Edit3,
  FileCode2,
  KeyRound,
  Layers,
  LayoutGrid,
  Link2,
  Plus,
  Save,
  Search,
  Settings2,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

export interface SchemaField {
  id?: string;
  field_name: string;
  field_type: string;
  display_name: string;
  description: string;
  is_nullable: boolean;
  is_primary_key: boolean;
  enum_values: string;
  sort_order?: number;
}

export interface SchemaEntity {
  id: string;
  profile_id: string;
  table_name: string;
  display_name: string;
  category: 'CORE_MAIN' | 'DETAIL' | 'CONFIG' | 'LOG' | string;
  description: string;
  primary_key: string;
  pos_x: number;
  pos_y: number;
  fields: SchemaField[];
}

export interface SchemaRelation {
  id: string;
  profile_id: string;
  source_entity_id: string;
  source_field: string;
  target_entity_id: string;
  target_field: string;
  relation_type: 'FK' | 'LOGICAL_JOIN' | 'JSON_EXTRACT' | 'DO_NOT_JOIN' | string;
  cardinality: '1:1' | '1:N' | 'N:1' | 'N:M' | string;
  join_condition: string;
  filter_rule: string;
  is_warning: boolean;
  warning_message: string;
}

export interface SchemaProfile {
  id: string;
  name: string;
  description: string;
  domain_prefix: string;
  target_db?: string;
  ontology_base_url: string;
  ontology_api_key: string;
  has_ontology_api_key: boolean;
  is_active: boolean;
  entity_count: number;
  relation_count: number;
}

const CATEGORY_BADGES: Record<string, { label: string; cls: string }> = {
  CORE_MAIN: { label: '核心主表', cls: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30' },
  DETAIL: { label: '明细表', cls: 'bg-sky-500/15 text-sky-500 border-sky-500/30' },
  CONFIG: { label: '配置字典', cls: 'bg-amber-500/15 text-amber-500 border-amber-500/30' },
  LOG: { label: '日志审计', cls: 'bg-purple-500/15 text-purple-500 border-purple-500/30' },
};

function TableEntityNode({ data, selected }: NodeProps) {
  const entity = data.entity as SchemaEntity;
  const badge = CATEGORY_BADGES[entity.category] || CATEGORY_BADGES.CORE_MAIN;
  const visibleFields = (entity.fields || []).slice(0, 14);

  return (
    <div
      className={`min-w-[270px] max-w-[320px] rounded-xl border bg-surface-card shadow-md transition-all text-xs ${
        selected ? 'border-emerald-500 ring-2 ring-emerald-500/25' : 'border-border'
      }`}
    >
      <div className="px-3 py-2 border-b border-border bg-surface-100 rounded-t-xl flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="font-mono font-semibold text-foreground truncate">{entity.table_name}</div>
          <div className="text-[11px] text-muted-foreground truncate">{entity.display_name}</div>
        </div>
        <span className={`px-1.5 py-0.5 rounded border text-[10px] shrink-0 ${badge.cls}`}>{badge.label}</span>
      </div>

      <div className="divide-y divide-border/50 max-h-[340px] overflow-y-auto">
        {visibleFields.length === 0 ? (
          <div className="px-3 py-2 text-[11px] text-muted-foreground italic">暂无字段，点击节点在右侧添加</div>
        ) : (
          visibleFields.map((f) => (
            <div
              key={f.field_name}
              className="relative px-3 py-1.5 flex items-center justify-between gap-2 hover:bg-surface-100/70 group"
            >
              <Handle
                type="target"
                position={Position.Left}
                id={`tgt:${f.field_name}`}
                className="!w-2 !h-2 !bg-sky-500 !border-background opacity-60 group-hover:opacity-100"
              />
              <div className="flex items-center gap-1.5 min-w-0">
                {f.is_primary_key && <KeyRound className="w-3 h-3 text-amber-500 shrink-0" />}
                <span className="font-mono text-[11px] text-foreground truncate">{f.field_name}</span>
                {f.enum_values && (
                  <span
                    className="px-1 py-0.2 rounded bg-amber-500/10 text-amber-500 text-[9px] shrink-0"
                    title={f.enum_values}
                  >
                    枚举
                  </span>
                )}
              </div>
              <span className="font-mono text-[10px] text-muted-foreground shrink-0">{f.field_type}</span>
              <Handle
                type="source"
                position={Position.Right}
                id={`src:${f.field_name}`}
                className="!w-2 !h-2 !bg-emerald-500 !border-background opacity-60 group-hover:opacity-100"
              />
            </div>
          ))
        )}
        {(entity.fields || []).length > 14 && (
          <div className="px-3 py-1 text-[10px] text-muted-foreground text-center bg-surface-100/50">
            还有 {entity.fields.length - 14} 个字段（右侧面板查看全部）
          </div>
        )}
      </div>
    </div>
  );
}

function RedlineRelationEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
}: EdgeProps) {
  const rel = data?.relation as SchemaRelation | undefined;
  const isDanger = Boolean(rel?.is_warning || rel?.relation_type === 'DO_NOT_JOIN');
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke: isDanger ? '#ef4444' : selected ? '#10b981' : '#64748b',
          strokeWidth: selected ? 2.5 : isDanger ? 2 : 1.5,
          strokeDasharray: isDanger ? '6 4' : undefined,
        }}
      />
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: 'all',
          }}
          className={`px-1.5 py-0.5 rounded text-[10px] font-medium border shadow-sm cursor-pointer flex items-center gap-1 ${
            isDanger
              ? 'bg-rose-950/90 text-rose-200 border-rose-500/60'
              : 'bg-surface-card/95 text-foreground border-border'
          }`}
          title={rel?.warning_message || rel?.join_condition || ''}
        >
          {isDanger && <AlertTriangle className="w-3 h-3 text-rose-400 shrink-0" />}
          <span>{rel?.relation_type === 'DO_NOT_JOIN' ? '禁连红线' : `${rel?.cardinality || 'N:1'}`}</span>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const nodeTypes = { customTableNode: TableEntityNode };
const edgeTypes = { redlineEdge: RedlineRelationEdge };

export function SchemaGraphView() {
  const [profiles, setProfiles] = useState<SchemaProfile[]>([]);
  const [activeProfileId, setActiveProfileId] = useState<string>('');
  const [entities, setEntities] = useState<SchemaEntity[]>([]);
  const [relations, setRelations] = useState<SchemaRelation[]>([]);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const [searchTerm, setSearchTerm] = useState('');
  const [onlyWarnings, setOnlyWarnings] = useState(false);
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  // 新建/编辑空间与网关弹窗
  const [showProfileModal, setShowProfileModal] = useState(false);
  const [editingProfileMode, setEditingProfileMode] = useState<'create' | 'edit'>('create');
  const [profName, setProfName] = useState('');
  const [profDesc, setProfDesc] = useState('');
  const [profPrefix, setProfPrefix] = useState('');
  const [profTargetDb, setProfTargetDb] = useState('');
  const [profUrl, setProfUrl] = useState('');
  const [profApiKey, setProfApiKey] = useState('');
  const [testingGateway, setTestingGateway] = useState(false);
  const [gatewayFeedback, setGatewayFeedback] = useState<{ ok: boolean; text: string } | null>(null);

  // DDL / Markdown 导入预览弹窗
  const [showImportModal, setShowImportModal] = useState(false);
  const [importFormat, setImportFormat] = useState<'auto' | 'sql' | 'markdown'>('auto');
  const [importContent, setImportContent] = useState('');
  const [previewResult, setPreviewResult] = useState<{
    source_format: string;
    entities: any[];
    relations: any[];
    business_rules_markdown?: string;
    warnings: string[];
  } | null>(null);
  const [replaceExisting, setReplaceExisting] = useState(false);
  const [importing, setImporting] = useState(false);

  // 关系边编辑弹窗
  const [editingRelation, setEditingRelation] = useState<Partial<SchemaRelation> | null>(null);

  // 新增实体弹窗
  const [showNewEntityModal, setShowNewEntityModal] = useState(false);
  const [newTableName, setNewTableName] = useState('');
  const [newDisplayName, setNewDisplayName] = useState('');
  const [newCategory, setNewCategory] = useState('CORE_MAIN');

  // 右侧实体表单草稿与常驻页签
  const [rightPanelTab, setRightPanelTab] = useState<'rules' | 'relations' | 'entity'>('rules');
  const [rulesDraft, setRulesDraft] = useState('');
  const [rulesEditing, setRulesEditing] = useState(false);
  const [savingRules, setSavingRules] = useState(false);
  const [draftEntity, setDraftEntity] = useState<SchemaEntity | null>(null);
  const [savingEntity, setSavingEntity] = useState(false);

  const showToast = useCallback((msg: string) => {
    setToastMsg(msg);
    setTimeout(() => setToastMsg(null), 3200);
  }, []);

  const loadProfiles = useCallback(async () => {
    const res = await apiFetch('/api/v1/profiles');
    if (res.ok) {
      const json = await res.json();
      const list: SchemaProfile[] = json.data || [];
      setProfiles(list);
      if (list.length > 0) {
        setActiveProfileId((prev) => (prev && list.some((p) => p.id === prev) ? prev : list[0].id));
      } else {
        setActiveProfileId('');
      }
    }
  }, []);

  const loadGraph = useCallback(async (profileId: string) => {
    if (!profileId) {
      setEntities([]);
      setRelations([]);
      return;
    }
    const res = await apiFetch(`/api/v1/profiles/${profileId}/graph`);
    if (res.ok) {
      const json = await res.json();
      const ents: SchemaEntity[] = json.data?.entities || [];
      const rels: SchemaRelation[] = json.data?.relations || [];
      setEntities(ents);
      setRelations(rels);
    }
  }, []);

  useEffect(() => {
    loadProfiles();
  }, [loadProfiles]);

  useEffect(() => {
    if (activeProfileId) {
      loadGraph(activeProfileId);
      setSelectedEntityId(null);
    }
  }, [activeProfileId, loadGraph]);

  // 同步实体与关系到 React Flow 画布
  useEffect(() => {
    const flowNodes: Node[] = entities.map((ent, idx) => ({
      id: ent.id,
      type: 'customTableNode',
      position: {
        x: ent.pos_x || (idx % 3) * 380 + 40,
        y: ent.pos_y || Math.floor(idx / 3) * 320 + 40,
      },
      data: { entity: ent },
      selected: ent.id === selectedEntityId,
    }));

    const filteredRels = onlyWarnings
      ? relations.filter((r) => r.is_warning || r.relation_type === 'DO_NOT_JOIN')
      : relations;

    const flowEdges: Edge[] = filteredRels.map((rel) => ({
      id: rel.id,
      source: rel.source_entity_id,
      target: rel.target_entity_id,
      sourceHandle: `src:${rel.source_field}`,
      targetHandle: `tgt:${rel.target_field}`,
      type: 'redlineEdge',
      data: { relation: rel },
    }));

    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [entities, relations, onlyWarnings, selectedEntityId, setNodes, setEdges]);

  useEffect(() => {
    if (!selectedEntityId) {
      setDraftEntity(null);
      return;
    }
    const found = entities.find((e) => e.id === selectedEntityId);
    setDraftEntity(found ? JSON.parse(JSON.stringify(found)) : null);
    if (found) setRightPanelTab('entity');
  }, [selectedEntityId, entities]);

  const activeProfile = useMemo(
    () => profiles.find((p) => p.id === activeProfileId) || null,
    [profiles, activeProfileId]
  );

  useEffect(() => {
    setRulesDraft(activeProfile?.description || '');
    setRulesEditing(false);
  }, [activeProfile?.id, activeProfile?.description]);

  const handleSaveRules = async () => {
    if (!activeProfileId) return;
    setSavingRules(true);
    try {
      const res = await apiFetch(`/api/v1/profiles/${activeProfileId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: rulesDraft }),
      });
      if (res.ok) {
        setRulesEditing(false);
        await loadProfiles();
        showToast('已保存空间业务口径与防坑红线');
      }
    } finally {
      setSavingRules(false);
    }
  };

  const handleOpenNewRelation = () => {
    if (!activeProfileId || entities.length === 0) return;
    const src = entities.find((e) => e.id === selectedEntityId) || entities[0];
    const tgt = entities.find((e) => e.id !== src.id) || src;
    setEditingRelation({
      profile_id: activeProfileId,
      source_entity_id: src.id,
      source_field: src.fields[0]?.field_name || 'id',
      target_entity_id: tgt.id,
      target_field: tgt.fields[0]?.field_name || 'id',
      relation_type: 'LEFT_JOIN',
      cardinality: 'N:1',
      join_condition: `${src.table_name}.${src.fields[0]?.field_name || 'id'} = ${tgt.table_name}.${tgt.fields[0]?.field_name || 'id'}`,
      filter_rule: '',
      is_warning: false,
      warning_message: '',
    });
  };

  const filteredEntities = useMemo(() => {
    const kw = searchTerm.trim().toLowerCase();
    if (!kw) return entities;
    return entities.filter(
      (e) =>
        e.table_name.toLowerCase().includes(kw) ||
        e.display_name.toLowerCase().includes(kw) ||
        e.fields.some((f) => f.field_name.toLowerCase().includes(kw))
    );
  }, [entities, searchTerm]);

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!profName.trim()) return;
    const isEdit = editingProfileMode === 'edit' && activeProfileId;
    const url = isEdit ? `/api/v1/profiles/${activeProfileId}` : '/api/v1/profiles';
    const method = isEdit ? 'PUT' : 'POST';
    const res = await apiFetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: profName.trim(),
        description: profDesc.trim(),
        domain_prefix: profPrefix.trim(),
        target_db: profTargetDb.trim(),
        ontology_base_url: profUrl.trim(),
        ontology_api_key: profApiKey.trim(),
      }),
    });
    if (res.ok) {
      const json = await res.json();
      setShowProfileModal(false);
      await loadProfiles();
      if (json.data?.id) setActiveProfileId(json.data.id);
      showToast(isEdit ? '空间配置已更新' : '新数据结构空间已创建');
    }
  };

  const handleSavePositions = async () => {
    if (!activeProfileId || nodes.length === 0) return;
    const positions = nodes.map((n) => ({
      id: n.id,
      pos_x: Math.round(n.position.x * 10) / 10,
      pos_y: Math.round(n.position.y * 10) / 10,
    }));
    const res = await apiFetch(`/api/v1/profiles/${activeProfileId}/positions`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ positions }),
    });
    if (res.ok) showToast('画布节点布局已保存');
  };

  const handleConnect = useCallback(
    (conn: Connection) => {
      if (!conn.source || !conn.target || !activeProfileId) return;
      const srcField = (conn.sourceHandle || '').replace(/^src:/, '') || 'id';
      const tgtField = (conn.targetHandle || '').replace(/^tgt:/, '') || 'id';
      const srcEnt = entities.find((e) => e.id === conn.source);
      const tgtEnt = entities.find((e) => e.id === conn.target);
      setEditingRelation({
        profile_id: activeProfileId,
        source_entity_id: conn.source,
        source_field: srcField,
        target_entity_id: conn.target,
        target_field: tgtField,
        relation_type: 'LOGICAL_JOIN',
        cardinality: 'N:1',
        join_condition: `${srcEnt?.table_name || conn.source}.${srcField} = ${tgtEnt?.table_name || conn.target}.${tgtField}`,
        filter_rule: '',
        is_warning: false,
        warning_message: '',
      });
    },
    [activeProfileId, entities]
  );

  const handleSaveRelation = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingRelation || !activeProfileId) return;
    const res = await apiFetch('/api/v1/relations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...editingRelation,
        profile_id: activeProfileId,
      }),
    });
    if (res.ok) {
      setEditingRelation(null);
      await loadGraph(activeProfileId);
      await loadProfiles();
      showToast('关系与红线规则已保存');
    }
  };

  const handleDeleteRelation = async () => {
    if (!editingRelation?.id) return;
    const res = await apiFetch(`/api/v1/relations/${editingRelation.id}`, { method: 'DELETE' });
    if (res.ok) {
      setEditingRelation(null);
      await loadGraph(activeProfileId);
      await loadProfiles();
      showToast('关系边已删除');
    }
  };

  const handleAutoLayout = async () => {
    if (!activeProfileId || nodes.length === 0) return;
    const dagreGraph = new dagre.graphlib.Graph();
    dagreGraph.setDefaultEdgeLabel(() => ({}));
    const nodeWidth = 290;
    const nodeHeight = 260;
    dagreGraph.setGraph({ rankdir: 'LR', nodesep: 55, ranksep: 110 });
    nodes.forEach((n) => dagreGraph.setNode(n.id, { width: nodeWidth, height: nodeHeight }));
    edges.forEach((e) => dagreGraph.setEdge(e.source, e.target));
    dagre.layout(dagreGraph);

    const layoutedNodes = nodes.map((n) => {
      const pos = dagreGraph.node(n.id);
      return {
        ...n,
        position: {
          x: Math.round((pos.x - nodeWidth / 2) * 10) / 10,
          y: Math.round((pos.y - nodeHeight / 2) * 10) / 10,
        },
      };
    });
    setNodes(layoutedNodes);
    const positions = layoutedNodes.map((n) => ({ id: n.id, pos_x: n.position.x, pos_y: n.position.y }));
    await apiFetch(`/api/v1/profiles/${activeProfileId}/positions`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ positions }),
    });
    showToast('已自动排版并持久化坐标');
  };

  const handleCloneProfile = async () => {
    if (!activeProfile) return;
    const newName = window.prompt('请输入克隆后的新空间名称：', `${activeProfile.name} (副本)`);
    if (!newName || !newName.trim()) return;
    const res = await apiFetch(`/api/v1/profiles/${activeProfile.id}/clone`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_profile_name: newName.trim() }),
    });
    if (res.ok) {
      const json = await res.json();
      await loadProfiles();
      if (json.data?.profile?.id) setActiveProfileId(json.data.profile.id);
      showToast(`已克隆 ${json.copied_entities_count ?? 0} 张表与 ${json.copied_relations_count ?? 0} 条关系`);
    }
  };

  const handleExportProfile = async () => {
    if (!activeProfile) return;
    try {
      const res = await apiFetch(`/api/v1/profiles/${activeProfile.id}/export-md-save`, {
        method: 'POST',
      });
      if (!res.ok) {
        showToast('导出失败，请稍后重试');
        return;
      }
      const json = await res.json();
      if (json.status === 'saved' && json.saved_path) {
        showToast(`已保存 Markdown 文档至：${json.saved_path}`);
        return;
      }
      if (json.status === 'cancelled') {
        return;
      }
      // 浏览器端回退：触发标准 .md 文件下载
      const mdContent = String(json.markdown || '');
      const filename = String(json.filename || `${activeProfile.name || activeProfile.id}.md`);
      const blob = new Blob([mdContent], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      showToast(`已导出 Markdown 文档：${filename}`);
    } catch {
      showToast('导出异常，请检查后端服务状态');
    }
  };

  const handleDeleteProfile = async () => {
    if (!activeProfile) return;
    const ok = window.confirm(
      `确定要删除数据结构空间「${activeProfile.name}」吗？\n删除后将清空该空间内所有实体表、关联边与专家绑定，且不可恢复。`
    );
    if (!ok) return;
    const res = await apiFetch(`/api/v1/profiles/${activeProfile.id}`, { method: 'DELETE' });
    if (res.ok) {
      setSelectedEntityId(null);
      await loadProfiles();
      showToast('数据结构空间已删除');
    }
  };

  const handleParsePreview = async () => {
    if (!activeProfileId || !importContent.trim()) return;
    const res = await apiFetch(`/api/v1/profiles/${activeProfileId}/parse-preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: importContent, format: importFormat }),
    });
    if (res.ok) {
      const json = await res.json();
      setPreviewResult(json.data);
    }
  };

  const handleConfirmImport = async () => {
    if (!activeProfileId || !previewResult) return;
    setImporting(true);
    try {
      const res = await apiFetch(`/api/v1/profiles/${activeProfileId}/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          entities: previewResult.entities,
          relations: previewResult.relations,
          business_rules_markdown: previewResult.business_rules_markdown || '',
          replace_existing: replaceExisting,
        }),
      });
      if (res.ok) {
        const json = await res.json();
        setShowImportModal(false);
        setPreviewResult(null);
        setImportContent('');
        await loadGraph(activeProfileId);
        await loadProfiles();
        showToast(
          `成功导入 ${json.data?.imported_entities ?? 0} 张表、${json.data?.imported_relations ?? 0} 条关联`
        );
      }
    } finally {
      setImporting(false);
    }
  };

  const handleCreateEntity = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeProfileId || !newTableName.trim()) return;
    const res = await apiFetch(`/api/v1/profiles/${activeProfileId}/entities`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        table_name: newTableName.trim(),
        display_name: newDisplayName.trim() || newTableName.trim(),
        category: newCategory,
        primary_key: 'id',
        pos_x: (entities.length % 3) * 380 + 40,
        pos_y: Math.floor(entities.length / 3) * 320 + 40,
        fields: [
          {
            field_name: 'id',
            field_type: 'BIGINT',
            display_name: '主键ID',
            description: '自增主键',
            is_nullable: false,
            is_primary_key: true,
            enum_values: '',
          },
        ],
      }),
    });
    if (res.ok) {
      const json = await res.json();
      setShowNewEntityModal(false);
      setNewTableName('');
      setNewDisplayName('');
      await loadGraph(activeProfileId);
      await loadProfiles();
      if (json.data?.id) setSelectedEntityId(json.data.id);
      showToast('已创建新实体表');
    }
  };

  const handleSaveDraftEntity = async () => {
    if (!draftEntity || !activeProfileId) return;
    setSavingEntity(true);
    try {
      const res = await apiFetch(`/api/v1/profiles/${activeProfileId}/entities`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draftEntity),
      });
      if (res.ok) {
        await loadGraph(activeProfileId);
        showToast(`已保存 ${draftEntity.table_name} 字段与口径`);
      }
    } finally {
      setSavingEntity(false);
    }
  };

  const handleDeleteEntity = async (entityId: string) => {
    if (!window.confirm('确认删除该实体表及所有关联连线？')) return;
    const res = await apiFetch(`/api/v1/entities/${entityId}`, { method: 'DELETE' });
    if (res.ok) {
      setSelectedEntityId(null);
      await loadGraph(activeProfileId);
      await loadProfiles();
      showToast('实体表已删除');
    }
  };

  return (
    <div className="h-full flex flex-col bg-background text-foreground">
      {/* 顶部工具栏 */}
      <div className="px-4 py-2.5 border-b border-border bg-surface-50 flex flex-wrap items-center justify-between gap-3 shrink-0">
        <div className="flex items-center gap-2.5">
          <Database className="w-4 h-4 text-emerald-500" />
          <select
            value={activeProfileId}
            onChange={(e) => setActiveProfileId(e.target.value)}
            className="px-2.5 py-1.5 rounded-lg border border-border bg-surface-100 text-xs font-medium min-w-[200px]"
          >
            {profiles.length === 0 ? (
              <option value="">暂无数据结构空间，请先新建</option>
            ) : (
              profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.entity_count} 表 / {p.relation_count} 关系)
                </option>
              ))
            )}
          </select>

          <button
            type="button"
            onClick={() => {
              setEditingProfileMode('create');
              setProfName('');
              setProfDesc('');
              setProfPrefix('');
              setProfTargetDb('');
              setProfUrl('');
              setProfApiKey('');
              setShowProfileModal(true);
            }}
            className="px-2.5 py-1.5 rounded-lg border border-border hover:bg-surface-200 text-xs flex items-center gap-1"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>新建空间</span>
          </button>

          {activeProfile && (
            <>
              <button
                type="button"
                onClick={() => {
                  setEditingProfileMode('edit');
                  setProfName(activeProfile.name);
                  setProfDesc(activeProfile.description);
                  setProfPrefix(activeProfile.domain_prefix);
                  setProfTargetDb(activeProfile.target_db || '');
                  setProfUrl(activeProfile.ontology_base_url);
                  setProfApiKey(activeProfile.ontology_api_key);
                  setGatewayFeedback(null);
                  setShowProfileModal(true);
                }}
                className="px-2 py-1.5 rounded-lg border border-border hover:bg-surface-200 text-xs flex items-center gap-1.5"
                title="空间元信息与独立 Auto Ontology 业务数据网关配置"
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    activeProfile.ontology_base_url && activeProfile.has_ontology_api_key
                      ? 'bg-emerald-500'
                      : 'bg-amber-500/70'
                  }`}
                />
                <Settings2 className="w-3.5 h-3.5" />
                <span>空间与网关设置</span>
              </button>
              <button
                type="button"
                onClick={handleCloneProfile}
                className="px-2 py-1.5 rounded-lg border border-border hover:bg-surface-200 text-xs flex items-center gap-1"
                title="深度克隆当前数据结构空间至新业务线"
              >
                <Copy className="w-3.5 h-3.5" />
                <span>克隆</span>
              </button>
              <button
                type="button"
                onClick={handleExportProfile}
                className="px-2 py-1.5 rounded-lg border border-border hover:bg-surface-200 text-xs flex items-center gap-1"
                title="导出为可再次导入的 .md 数据结构与口径文档"
              >
                <Download className="w-3.5 h-3.5" />
                <span>导出 .md</span>
              </button>
              <button
                type="button"
                onClick={handleDeleteProfile}
                className="px-2 py-1.5 rounded-lg border border-rose-500/30 text-rose-500 hover:bg-rose-500/10 text-xs flex items-center gap-1"
                title="删除当前数据结构空间"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>删除空间</span>
              </button>
            </>
          )}
        </div>

        <div className="flex items-center gap-2">
          {toastMsg && (
            <span className="px-2.5 py-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 text-emerald-500 text-xs">
              {toastMsg}
            </span>
          )}
          <label className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border bg-surface-100 text-xs cursor-pointer">
            <input
              type="checkbox"
              checked={onlyWarnings}
              onChange={(e) => setOnlyWarnings(e.target.checked)}
              className="accent-rose-500"
            />
            <span>仅看红线</span>
          </label>
          <button
            type="button"
            disabled={!activeProfileId}
            onClick={() => {
              setPreviewResult(null);
              setShowImportModal(true);
            }}
            className="px-2.5 py-1.5 rounded-lg border border-border hover:bg-surface-200 text-xs flex items-center gap-1 disabled:opacity-40"
          >
            <Upload className="w-3.5 h-3.5" />
            <span>导入 DDL / .md</span>
          </button>
          <button
            type="button"
            disabled={!activeProfileId}
            onClick={() => setShowNewEntityModal(true)}
            className="px-2.5 py-1.5 rounded-lg border border-border hover:bg-surface-200 text-xs flex items-center gap-1 disabled:opacity-40"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>新增实体表</span>
          </button>
          <button
            type="button"
            disabled={!activeProfileId || nodes.length === 0}
            onClick={handleAutoLayout}
            className="px-2.5 py-1.5 rounded-lg border border-border hover:bg-surface-200 text-xs flex items-center gap-1 disabled:opacity-40"
            title="使用 Dagre 引擎自动计算实体图谱排版"
          >
            <LayoutGrid className="w-3.5 h-3.5" />
            <span>自动排版</span>
          </button>
          <button
            type="button"
            disabled={!activeProfileId || nodes.length === 0}
            onClick={handleSavePositions}
            className="px-2.5 py-1.5 rounded-lg bg-slate-900 text-white dark:bg-zinc-100 dark:text-zinc-900 text-xs font-medium flex items-center gap-1 disabled:opacity-40"
          >
            <Save className="w-3.5 h-3.5" />
            <span>保存布局</span>
          </button>
        </div>
      </div>

      {/* 主体：左侧实体表目录 + 中央 React Flow ER 画布 + 右侧字段编辑器 */}
      <div className="flex-1 min-h-0 flex">
        {/* 左侧实体表目录 */}
        <div className="w-60 border-r border-border bg-surface-50 flex flex-col shrink-0">
          <div className="p-2.5 border-b border-border">
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-muted-foreground absolute left-2.5 top-2" />
              <input
                type="text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="筛选表名或字段..."
                className="w-full pl-8 pr-2.5 py-1 rounded-md border border-border bg-surface-100 text-xs"
              />
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {filteredEntities.map((ent) => {
              const badge = CATEGORY_BADGES[ent.category] || CATEGORY_BADGES.CORE_MAIN;
              return (
                <button
                  key={ent.id}
                  type="button"
                  onClick={() => setSelectedEntityId(ent.id)}
                  className={`w-full text-left px-2.5 py-2 rounded-lg border transition-all ${
                    selectedEntityId === ent.id
                      ? 'border-emerald-500/50 bg-emerald-500/10'
                      : 'border-transparent hover:bg-surface-200/70'
                  }`}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-mono text-xs font-medium truncate">{ent.table_name}</span>
                    <span className={`px-1 py-0.2 rounded border text-[9px] shrink-0 ${badge.cls}`}>
                      {ent.fields.length}
                    </span>
                  </div>
                  <div className="text-[11px] text-muted-foreground truncate mt-0.5">{ent.display_name}</div>
                </button>
              );
            })}
            {filteredEntities.length === 0 && (
              <div className="text-center text-xs text-muted-foreground py-8">
                {activeProfileId ? '暂无实体表，请点击上方导入 DDL / .md' : '请先创建数据结构空间'}
              </div>
            )}
          </div>
        </div>

        {/* 中央 React Flow ER 画布 */}
        <div className="flex-1 min-w-0 relative bg-surface-100/40">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={handleConnect}
            onNodeClick={(_, node) => setSelectedEntityId(node.id)}
            onEdgeClick={(_, edge) => {
              const rel = edge.data?.relation as SchemaRelation | undefined;
              if (rel) setEditingRelation({ ...rel });
            }}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            className="bg-background"
          >
            <Background gap={18} size={1} />
            <Controls />
            <MiniMap zoomable pannable className="!bg-surface-card !border !border-border" />
          </ReactFlow>
        </div>

        {/* 右侧常驻多页签面板：业务口径 (Wiki) / 关系列表 / 实体字段 */}
        {activeProfileId && (
          <div className="w-[380px] border-l border-border bg-surface-50 flex flex-col shrink-0">
            <div className="px-2 pt-2 border-b border-border flex items-center gap-1 bg-surface-100/50 shrink-0">
              <button
                type="button"
                onClick={() => setRightPanelTab('rules')}
                className={`px-2.5 py-1.5 text-xs font-medium rounded-t-lg border-b-2 flex items-center gap-1.5 transition-colors ${
                  rightPanelTab === 'rules'
                    ? 'border-emerald-500 text-foreground bg-surface-50'
                    : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
              >
                <BookOpen className="w-3.5 h-3.5 text-emerald-500" />
                <span>业务口径 (Wiki)</span>
              </button>
              <button
                type="button"
                onClick={() => setRightPanelTab('relations')}
                className={`px-2.5 py-1.5 text-xs font-medium rounded-t-lg border-b-2 flex items-center gap-1.5 transition-colors ${
                  rightPanelTab === 'relations'
                    ? 'border-emerald-500 text-foreground bg-surface-50'
                    : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
              >
                <Link2 className="w-3.5 h-3.5 text-sky-500" />
                <span>关系 ({relations.length})</span>
              </button>
              <button
                type="button"
                onClick={() => setRightPanelTab('entity')}
                className={`px-2.5 py-1.5 text-xs font-medium rounded-t-lg border-b-2 flex items-center gap-1.5 transition-colors ${
                  rightPanelTab === 'entity'
                    ? 'border-emerald-500 text-foreground bg-surface-50'
                    : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
              >
                <Database className="w-3.5 h-3.5 text-amber-500" />
                <span>{draftEntity ? draftEntity.table_name : '实体字段'}</span>
              </button>
            </div>

            {rightPanelTab === 'rules' && (
              <div className="flex-1 min-h-0 flex flex-col">
                <div className="px-3.5 py-2 border-b border-border flex items-center justify-between text-xs shrink-0">
                  <span className="text-muted-foreground">全局业务统计口径、CTE 规范与红线</span>
                  {!rulesEditing ? (
                    <button
                      type="button"
                      onClick={() => setRulesEditing(true)}
                      className="px-2.5 py-1 rounded border border-border hover:bg-surface-200 flex items-center gap-1"
                    >
                      <Edit3 className="w-3 h-3" />
                      <span>编辑口径</span>
                    </button>
                  ) : (
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => {
                          setRulesDraft(activeProfile?.description || '');
                          setRulesEditing(false);
                        }}
                        className="px-2 py-1 rounded border border-border text-[11px]"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        disabled={savingRules}
                        onClick={handleSaveRules}
                        className="px-2.5 py-1 rounded bg-emerald-600 text-white text-[11px] font-medium disabled:opacity-40"
                      >
                        {savingRules ? '保存中...' : '保存口径'}
                      </button>
                    </div>
                  )}
                </div>

                <div className="flex-1 overflow-y-auto p-3.5">
                  {rulesEditing ? (
                    <textarea
                      value={rulesDraft}
                      onChange={(e) => setRulesDraft(e.target.value)}
                      rows={22}
                      placeholder="在此编写或粘贴 Markdown 格式的「## 一、核心业务口径与防坑红线」..."
                      className="w-full h-full min-h-[360px] px-3 py-2 rounded-lg border border-border bg-surface-100 font-mono text-xs focus:outline-none focus:border-emerald-500/60"
                    />
                  ) : rulesDraft.trim() ? (
                    <div className="prose prose-sm dark:prose-invert max-w-none text-xs leading-relaxed">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{rulesDraft}</ReactMarkdown>
                    </div>
                  ) : (
                    <div className="text-center text-xs text-muted-foreground py-10 space-y-2">
                      <p>当前空间暂未配置全局业务口径说明。</p>
                      <p className="text-[11px]">
                        可点击右上角「编辑口径」直接撰写，或在导入 `.md` 时包含「## 一、核心业务口径与防坑红线」章节自动导入。
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {rightPanelTab === 'relations' && (
              <div className="flex-1 min-h-0 flex flex-col">
                <div className="px-3.5 py-2 border-b border-border flex items-center justify-between text-xs shrink-0">
                  <span className="text-muted-foreground">点击任意关系可编辑条件与红线</span>
                  <button
                    type="button"
                    disabled={entities.length === 0}
                    onClick={handleOpenNewRelation}
                    className="px-2.5 py-1 rounded bg-emerald-600 text-white text-[11px] font-medium flex items-center gap-1 disabled:opacity-40"
                  >
                    <Plus className="w-3 h-3" />
                    <span>新增关联</span>
                  </button>
                </div>
                <div className="flex-1 overflow-y-auto p-2.5 space-y-2 text-xs">
                  {relations.length === 0 ? (
                    <div className="text-center text-muted-foreground py-10">
                      暂无关系边，可在画布拖拽字段连线或点击「新增关联」
                    </div>
                  ) : (
                    relations.map((rel) => {
                      const srcT = rel.source_entity_id.split('.').pop();
                      const tgtT = rel.target_entity_id.split('.').pop();
                      const isDanger = rel.is_warning || rel.relation_type === 'DO_NOT_JOIN';
                      return (
                        <button
                          key={rel.id}
                          type="button"
                          onClick={() => setEditingRelation({ ...rel })}
                          className={`w-full text-left p-2.5 rounded-lg border transition-colors space-y-1 ${
                            isDanger
                              ? 'border-rose-500/40 bg-rose-500/5 hover:bg-rose-500/10'
                              : 'border-border bg-surface-100 hover:bg-surface-200/70'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-1.5">
                            <span className="font-mono font-semibold truncate">
                              {srcT}.{rel.source_field} ➔ {tgtT}.{rel.target_field}
                            </span>
                            <span
                              className={`px-1.5 py-0.5 rounded text-[10px] shrink-0 ${
                                isDanger
                                  ? 'bg-rose-500/20 text-rose-400'
                                  : 'bg-sky-500/15 text-sky-400'
                              }`}
                            >
                              {rel.relation_type}
                            </span>
                          </div>
                          {rel.join_condition && (
                            <div className="font-mono text-[10.5px] text-muted-foreground truncate">
                              ON: {rel.join_condition}
                            </div>
                          )}
                          {(rel.warning_message || rel.filter_rule) && (
                            <div className={`text-[11px] line-clamp-2 ${isDanger ? 'text-rose-400' : 'text-muted-foreground'}`}>
                              {isDanger ? `⚠️ ${rel.warning_message}` : rel.filter_rule}
                            </div>
                          )}
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            )}

            {rightPanelTab === 'entity' && (
              draftEntity ? (
                <>
                  <div className="px-3.5 py-2 border-b border-border flex items-center justify-between shrink-0">
                    <div className="min-w-0">
                      <div className="font-mono text-xs font-semibold truncate">{draftEntity.table_name}</div>
                      <div className="text-[11px] text-muted-foreground">实体属性、主键、可空与枚举口径编辑</div>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => handleDeleteEntity(draftEntity.id)}
                        className="p-1.5 rounded hover:bg-rose-500/10 text-rose-500"
                        title="删除实体表"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedEntityId(null);
                          setRightPanelTab('rules');
                        }}
                        className="p-1.5 rounded hover:bg-surface-200 text-muted-foreground"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  <div className="flex-1 overflow-y-auto p-3.5 space-y-3 text-xs">
                    <div className="grid grid-cols-3 gap-2">
                      <div className="col-span-2">
                        <label className="block text-[10.5px] text-muted-foreground mb-1">业务名称</label>
                        <input
                          type="text"
                          value={draftEntity.display_name}
                          onChange={(e) => setDraftEntity({ ...draftEntity, display_name: e.target.value })}
                          className="w-full px-2 py-1 rounded border border-border bg-surface-100"
                        />
                      </div>
                      <div>
                        <label className="block text-[10.5px] text-muted-foreground mb-1">分类</label>
                        <select
                          value={draftEntity.category}
                          onChange={(e) => setDraftEntity({ ...draftEntity, category: e.target.value })}
                          className="w-full px-2 py-1 rounded border border-border bg-surface-100"
                        >
                          <option value="CORE_MAIN">核心主表</option>
                          <option value="DETAIL">明细表</option>
                          <option value="CONFIG">配置字典</option>
                          <option value="LOG">日志审计</option>
                        </select>
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-2">
                      <div>
                        <label className="block text-[10.5px] text-muted-foreground mb-1">主键字段</label>
                        <input
                          type="text"
                          value={draftEntity.primary_key}
                          onChange={(e) => setDraftEntity({ ...draftEntity, primary_key: e.target.value })}
                          className="w-full px-2 py-1 rounded border border-border bg-surface-100 font-mono"
                        />
                      </div>
                      <div className="col-span-2">
                        <label className="block text-[10.5px] text-muted-foreground mb-1">表业务口径说明</label>
                        <input
                          type="text"
                          value={draftEntity.description}
                          onChange={(e) => setDraftEntity({ ...draftEntity, description: e.target.value })}
                          className="w-full px-2 py-1 rounded border border-border bg-surface-100"
                        />
                      </div>
                    </div>

                    <div className="flex items-center justify-between pt-1">
                      <span className="font-semibold">字段列表 ({draftEntity.fields.length})</span>
                      <button
                        type="button"
                        onClick={() =>
                          setDraftEntity({
                            ...draftEntity,
                            fields: [
                              ...draftEntity.fields,
                              {
                                field_name: `col_${draftEntity.fields.length + 1}`,
                                field_type: 'VARCHAR(64)',
                                display_name: '',
                                description: '',
                                is_nullable: true,
                                is_primary_key: false,
                                enum_values: '',
                              },
                            ],
                          })
                        }
                        className="px-2 py-0.5 rounded border border-border hover:bg-surface-200 text-[11px] flex items-center gap-1"
                      >
                        <Plus className="w-3 h-3" />
                        <span>加字段</span>
                      </button>
                    </div>

                    <div className="space-y-2">
                      {draftEntity.fields.map((f, idx) => (
                        <div key={idx} className="p-2 rounded-lg border border-border bg-surface-100 space-y-1.5">
                          <div className="flex items-center gap-1.5">
                            <input
                              type="text"
                              value={f.field_name}
                              onChange={(e) => {
                                const next = [...draftEntity.fields];
                                next[idx] = { ...f, field_name: e.target.value };
                                setDraftEntity({ ...draftEntity, fields: next });
                              }}
                              placeholder="字段名"
                              className="flex-1 px-1.5 py-0.5 rounded border border-border bg-background font-mono text-[11px]"
                            />
                            <input
                              type="text"
                              value={f.field_type}
                              onChange={(e) => {
                                const next = [...draftEntity.fields];
                                next[idx] = { ...f, field_type: e.target.value };
                                setDraftEntity({ ...draftEntity, fields: next });
                              }}
                              placeholder="类型"
                              className="w-24 px-1.5 py-0.5 rounded border border-border bg-background font-mono text-[11px]"
                            />
                            <button
                              type="button"
                              onClick={() => {
                                const next = draftEntity.fields.filter((_, i) => i !== idx);
                                setDraftEntity({ ...draftEntity, fields: next });
                              }}
                              className="text-muted-foreground hover:text-rose-500"
                            >
                              <Trash2 className="w-3 h-3" />
                            </button>
                          </div>
                          <div className="flex items-center gap-3 text-[10.5px] text-muted-foreground">
                            <label className="flex items-center gap-1 cursor-pointer">
                              <input
                                type="checkbox"
                                checked={Boolean(f.is_primary_key)}
                                onChange={(e) => {
                                  const next = [...draftEntity.fields];
                                  next[idx] = { ...f, is_primary_key: e.target.checked };
                                  setDraftEntity({ ...draftEntity, fields: next });
                                }}
                                className="accent-amber-500"
                              />
                              <span>主键 (PK)</span>
                            </label>
                            <label className="flex items-center gap-1 cursor-pointer">
                              <input
                                type="checkbox"
                                checked={Boolean(f.is_nullable)}
                                onChange={(e) => {
                                  const next = [...draftEntity.fields];
                                  next[idx] = { ...f, is_nullable: e.target.checked };
                                  setDraftEntity({ ...draftEntity, fields: next });
                                }}
                                className="accent-sky-500"
                              />
                              <span>可空 (NULL)</span>
                            </label>
                          </div>
                          <input
                            type="text"
                            value={f.enum_values}
                            onChange={(e) => {
                              const next = [...draftEntity.fields];
                              next[idx] = { ...f, enum_values: e.target.value };
                              setDraftEntity({ ...draftEntity, fields: next });
                            }}
                            placeholder="枚举值口径 (如: 1=已生效; 0=草稿)"
                            className="w-full px-1.5 py-0.5 rounded border border-border bg-background text-[11px]"
                          />
                          <input
                            type="text"
                            value={f.description}
                            onChange={(e) => {
                              const next = [...draftEntity.fields];
                              next[idx] = { ...f, description: e.target.value };
                              setDraftEntity({ ...draftEntity, fields: next });
                            }}
                            placeholder="字段业务说明"
                            className="w-full px-1.5 py-0.5 rounded border border-border bg-background text-[11px]"
                          />
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="p-3 border-t border-border shrink-0">
                    <button
                      type="button"
                      disabled={savingEntity}
                      onClick={handleSaveDraftEntity}
                      className="w-full py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium disabled:opacity-40"
                    >
                      {savingEntity ? '保存中...' : '保存实体与字段'}
                    </button>
                  </div>
                </>
              ) : (
                <div className="flex-1 flex items-center justify-center text-xs text-muted-foreground p-6 text-center">
                  请在左侧列表或中间画布点击任意表节点，查看并编辑字段结构、主键、可空与枚举口径。
                </div>
              )
            )}
          </div>
        )}
      </div>

      {/* 空间新建/编辑弹窗 */}
      {showProfileModal && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <form
            onSubmit={handleSaveProfile}
            className="w-full max-w-lg rounded-xl border border-border bg-surface-card p-5 shadow-2xl space-y-3 text-xs"
          >
            <h3 className="text-sm font-semibold">
              {editingProfileMode === 'edit' ? '编辑数据结构空间' : '新建数据结构空间 (Schema Profile)'}
            </h3>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] text-muted-foreground mb-1">空间名称 *</label>
                <input
                  required
                  value={profName}
                  onChange={(e) => setProfName(e.target.value)}
                  placeholder="如：核心业务数据口径"
                  className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
                />
              </div>
              <div>
                <label className="block text-[11px] text-muted-foreground mb-1">默认表前缀</label>
                <input
                  value={profPrefix}
                  onChange={(e) => setProfPrefix(e.target.value)}
                  placeholder="如：biz_, sys_"
                  className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100 font-mono"
                />
              </div>
            </div>
            <div>
              <label className="block text-[11px] text-muted-foreground mb-1">空间描述</label>
              <textarea
                rows={2}
                value={profDesc}
                onChange={(e) => setProfDesc(e.target.value)}
                placeholder="说明该业务系统的核心表与统计背景..."
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
              />
            </div>
            <div className="p-3 rounded-lg border border-border bg-surface-100/60 space-y-2">
              <div className="font-medium text-[11px] flex items-center justify-between">
                <span className="flex items-center gap-1.5">
                  <Layers className="w-3.5 h-3.5 text-sky-500" />
                  <span>业务数据网关配置（Auto Ontology 独立连接）</span>
                </span>
                <button
                  type="button"
                  disabled={testingGateway || !profUrl.trim()}
                  onClick={async () => {
                    setTestingGateway(true);
                    setGatewayFeedback(null);
                    try {
                      const res = await apiFetch('/api/v1/ontology/test', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                          profile_id: editingProfileMode === 'edit' ? activeProfileId : undefined,
                          base_url: profUrl.trim(),
                          api_key: profApiKey.trim(),
                        }),
                      });
                      const json = await res.json().catch(() => ({}));
                      if (!res.ok) {
                        setGatewayFeedback({ ok: false, text: json.detail || '连通性测试失败' });
                      } else {
                        setGatewayFeedback({
                          ok: true,
                          text: `连接成功！已发现 ${json.data?.terms_count || 0} 条术语`,
                        });
                      }
                    } catch (err: any) {
                      setGatewayFeedback({ ok: false, text: err?.message || '连通性测试失败' });
                    } finally {
                      setTestingGateway(false);
                    }
                  }}
                  className="px-2 py-0.5 rounded border border-border bg-background hover:bg-surface-200 text-[11px] disabled:opacity-40"
                >
                  {testingGateway ? '测试中...' : '连通性测试'}
                </button>
              </div>
              <div className="space-y-1.5">
                <input
                  value={profTargetDb}
                  onChange={(e) => setProfTargetDb(e.target.value)}
                  placeholder="目标数据库名 target_db（可自由输入，留空按空间/专家自动识别）"
                  className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-background font-mono text-xs"
                />
                <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                  <span>常用预设:</span>
                  {[
                    { id: 'fencinginfo', label: 'fencinginfo (击剑库)' },
                    { id: 'equestrianinfo', label: 'equestrianinfo (马术库)' },
                  ].map((preset) => (
                    <button
                      key={preset.id}
                      type="button"
                      onClick={() => setProfTargetDb(preset.id)}
                      className={`px-2 py-0.5 rounded border transition-colors ${
                        profTargetDb === preset.id
                          ? 'bg-emerald-500/15 border-emerald-500/50 text-emerald-500'
                          : 'bg-background border-border hover:bg-surface-200'
                      }`}
                    >
                      {preset.label}
                    </button>
                  ))}
                  {profTargetDb && (
                    <button
                      type="button"
                      onClick={() => setProfTargetDb('')}
                      className="px-1.5 py-0.5 rounded border border-border hover:bg-surface-200 text-[10px]"
                    >
                      清空(自动识别)
                    </button>
                  )}
                </div>
              </div>
              <input
                value={profUrl}
                onChange={(e) => setProfUrl(e.target.value)}
                placeholder="Auto Ontology 网关地址 (如 https://ontology.example.com)"
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-background font-mono"
              />
              <input
                type="password"
                value={profApiKey}
                onChange={(e) => setProfApiKey(e.target.value)}
                placeholder="x-api-key Token（留空或保留掩码则不覆盖原密钥）"
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-background font-mono"
              />
              {gatewayFeedback && (
                <div
                  className={`px-2.5 py-1.5 rounded text-[11px] border ${
                    gatewayFeedback.ok
                      ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
                      : 'bg-rose-500/10 text-rose-500 border-rose-500/30'
                  }`}
                >
                  {gatewayFeedback.text}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setShowProfileModal(false)}
                className="px-3 py-1.5 rounded-lg border border-border"
              >
                取消
              </button>
              <button type="submit" className="px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-medium">
                保存
              </button>
            </div>
          </form>
        </div>
      )}

      {/* DDL / Markdown 双通道导入与预览弹窗 */}
      {showImportModal && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <div className="w-full max-w-3xl rounded-xl border border-border bg-surface-card p-5 shadow-2xl space-y-3 text-xs max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold flex items-center gap-2">
                <FileCode2 className="w-4 h-4 text-emerald-500" />
                <span>导入 SQL DDL 或 Markdown 结构文档（预览确认后入库）</span>
              </h3>
              <button type="button" onClick={() => setShowImportModal(false)} className="text-muted-foreground">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <select
                value={importFormat}
                onChange={(e) => setImportFormat(e.target.value as 'auto' | 'sql' | 'markdown')}
                className="px-2.5 py-1 rounded-lg border border-border bg-surface-100"
              >
                <option value="auto">自动识别 (SQL DDL / Markdown)</option>
                <option value="sql">SQL CREATE TABLE DDL (.sql)</option>
                <option value="markdown">Markdown 结构文档 (.md)</option>
              </select>
              <input
                type="file"
                accept=".sql,.md,.markdown,.txt"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  setImportContent(await file.text());
                  setPreviewResult(null);
                }}
                className="text-xs file:mr-2 file:py-1 file:px-2.5 file:rounded-md file:border file:border-border file:bg-surface-200 file:text-xs"
              />
              <label className="flex items-center gap-1.5 ml-auto cursor-pointer">
                <input
                  type="checkbox"
                  checked={replaceExisting}
                  onChange={(e) => setReplaceExisting(e.target.checked)}
                  className="accent-rose-500"
                />
                <span>覆盖当前空间已有实体</span>
              </label>
            </div>

            <textarea
              rows={8}
              value={importContent}
              onChange={(e) => setImportContent(e.target.value)}
              placeholder="粘贴 CREATE TABLE ... 建表语句，或 `table_name (中文名)` 结构的 Markdown 文档..."
              className="w-full px-3 py-2 rounded-lg border border-border bg-surface-100 font-mono text-[11px]"
            />

            {previewResult && (
              <div className="flex-1 overflow-y-auto rounded-lg border border-border bg-surface-100 p-3 space-y-2">
                <div className="font-medium flex items-center justify-between">
                  <span>
                    解析预览（格式：{previewResult.source_format}）：识别出{' '}
                    <strong className="text-emerald-500">{previewResult.entities.length}</strong> 张表、{' '}
                    <strong className="text-sky-500">{previewResult.relations.length}</strong> 条外键关系
                  </span>
                </div>
                {previewResult.warnings.map((w, i) => (
                  <div key={i} className="px-2.5 py-1.5 rounded bg-amber-500/10 text-amber-500 border border-amber-500/30">
                    ⚠️ {w}
                  </div>
                ))}
                {previewResult.business_rules_markdown && (
                  <div className="p-2.5 rounded border border-emerald-500/30 bg-emerald-500/5 space-y-1">
                    <div className="font-semibold text-emerald-500 text-[11px]">
                      已识别「核心业务口径与防坑红线」（{previewResult.business_rules_markdown.length} 字，写入后可在右侧「业务口径 (Wiki)」查看编辑）：
                    </div>
                    <pre className="text-[10.5px] text-muted-foreground whitespace-pre-wrap max-h-24 overflow-y-auto font-sans">
                      {previewResult.business_rules_markdown}
                    </pre>
                  </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {previewResult.entities.map((ent, i) => (
                    <div key={i} className="p-2 rounded border border-border bg-background">
                      <div className="font-mono font-semibold">
                        {ent.table_name} <span className="font-sans text-muted-foreground">({ent.display_name})</span>
                      </div>
                      <div className="text-[10.5px] text-muted-foreground mt-0.5">
                        字段 {ent.fields?.length || 0} 个 · 主键 {ent.primary_key}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={handleParsePreview}
                disabled={!importContent.trim()}
                className="px-3 py-1.5 rounded-lg border border-border hover:bg-surface-200 disabled:opacity-40"
              >
                解析预览
              </button>
              <button
                type="button"
                onClick={handleConfirmImport}
                disabled={!previewResult || previewResult.entities.length === 0 || importing}
                className="px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-medium disabled:opacity-40"
              >
                {importing ? '导入中...' : '确认写入图谱'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 关系连线与红线编辑弹窗 */}
      {editingRelation && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <form
            onSubmit={handleSaveRelation}
            className="w-full max-w-lg rounded-xl border border-border bg-surface-card p-5 shadow-2xl space-y-3 text-xs"
          >
            <h3 className="text-sm font-semibold">编辑表关联与红线规则</h3>
            <div className="grid grid-cols-2 gap-2 p-2.5 rounded-lg bg-surface-100 border border-border">
              <div>
                <label className="block text-[10px] text-muted-foreground mb-1">源表</label>
                <select
                  value={editingRelation.source_entity_id || ''}
                  onChange={(e) => setEditingRelation({ ...editingRelation, source_entity_id: e.target.value })}
                  className="w-full px-2 py-1 rounded border border-border bg-background font-mono text-[11px]"
                >
                  {entities.map((ent) => (
                    <option key={ent.id} value={ent.id}>
                      {ent.table_name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-[10px] text-muted-foreground mb-1">源字段</label>
                <input
                  value={editingRelation.source_field || ''}
                  onChange={(e) => setEditingRelation({ ...editingRelation, source_field: e.target.value })}
                  className="w-full px-2 py-1 rounded border border-border bg-background font-mono text-[11px]"
                />
              </div>
              <div>
                <label className="block text-[10px] text-muted-foreground mb-1">目标表</label>
                <select
                  value={editingRelation.target_entity_id || ''}
                  onChange={(e) => setEditingRelation({ ...editingRelation, target_entity_id: e.target.value })}
                  className="w-full px-2 py-1 rounded border border-border bg-background font-mono text-[11px]"
                >
                  {entities.map((ent) => (
                    <option key={ent.id} value={ent.id}>
                      {ent.table_name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-[10px] text-muted-foreground mb-1">目标字段</label>
                <input
                  value={editingRelation.target_field || ''}
                  onChange={(e) => setEditingRelation({ ...editingRelation, target_field: e.target.value })}
                  className="w-full px-2 py-1 rounded border border-border bg-background font-mono text-[11px]"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] text-muted-foreground mb-1">关系类型</label>
                <select
                  value={editingRelation.relation_type || 'LOGICAL_JOIN'}
                  onChange={(e) => {
                    const nextType = e.target.value;
                    setEditingRelation({
                      ...editingRelation,
                      relation_type: nextType,
                      is_warning: nextType === 'DO_NOT_JOIN' ? true : editingRelation.is_warning,
                    });
                  }}
                  className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
                >
                  <option value="FK">FK (物理外键)</option>
                  <option value="LOGICAL_JOIN">LOGICAL_JOIN (业务逻辑关联)</option>
                  <option value="LEFT_JOIN">LEFT_JOIN (强制左连接)</option>
                  <option value="JSON_EXTRACT">JSON_EXTRACT (JSON提取关联)</option>
                  <option value="DO_NOT_JOIN">DO_NOT_JOIN (高危禁连红线)</option>
                </select>
              </div>
              <div>
                <label className="block text-[11px] text-muted-foreground mb-1">基数</label>
                <select
                  value={editingRelation.cardinality || 'N:1'}
                  onChange={(e) => setEditingRelation({ ...editingRelation, cardinality: e.target.value })}
                  className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
                >
                  <option value="1:1">1:1</option>
                  <option value="1:N">1:N</option>
                  <option value="N:1">N:1</option>
                  <option value="N:M">N:M</option>
                </select>
              </div>
            </div>
            <div>
              <label className="block text-[11px] text-muted-foreground mb-1">JOIN 条件表达式</label>
              <input
                value={editingRelation.join_condition || ''}
                onChange={(e) => setEditingRelation({ ...editingRelation, join_condition: e.target.value })}
                placeholder="如：main_orders.org_id = sys_orgs.id"
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100 font-mono"
              />
            </div>
            <div>
              <label className="block text-[11px] text-muted-foreground mb-1">默认过滤规则 / 业务口径说明</label>
              <input
                value={editingRelation.filter_rule || ''}
                onChange={(e) => setEditingRelation({ ...editingRelation, filter_rule: e.target.value })}
                placeholder="如：N:1；空归属记录需保留"
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
              />
            </div>
            <label className="flex items-center gap-2 p-2.5 rounded-lg border border-rose-500/30 bg-rose-500/5 cursor-pointer">
              <input
                type="checkbox"
                checked={Boolean(editingRelation.is_warning)}
                onChange={(e) => setEditingRelation({ ...editingRelation, is_warning: e.target.checked })}
                className="accent-rose-500"
              />
              <span className="font-medium text-rose-500">标记为高危红线警告边（注入专家上下文并参与 SQL 审计）</span>
            </label>
            <div>
              <label className="block text-[11px] text-muted-foreground mb-1">红线避坑提示文本</label>
              <textarea
                rows={2}
                value={editingRelation.warning_message || ''}
                onChange={(e) => setEditingRelation({ ...editingRelation, warning_message: e.target.value })}
                placeholder="如：存在空外键记录，严禁使用 INNER JOIN，必须使用 LEFT JOIN！"
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
              />
            </div>
            <div className="flex items-center justify-between pt-1">
              {editingRelation.id ? (
                <button
                  type="button"
                  onClick={handleDeleteRelation}
                  className="px-3 py-1.5 rounded-lg border border-rose-500/40 text-rose-500 hover:bg-rose-500/10"
                >
                  删除连线
                </button>
              ) : (
                <span />
              )}
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setEditingRelation(null)}
                  className="px-3 py-1.5 rounded-lg border border-border"
                >
                  取消
                </button>
                <button type="submit" className="px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-medium">
                  保存关系
                </button>
              </div>
            </div>
          </form>
        </div>
      )}

      {/* 新增实体表弹窗 */}
      {showNewEntityModal && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <form
            onSubmit={handleCreateEntity}
            className="w-full max-w-md rounded-xl border border-border bg-surface-card p-5 shadow-2xl space-y-3 text-xs"
          >
            <h3 className="text-sm font-semibold">新增实体数据表</h3>
            <div>
              <label className="block text-[11px] text-muted-foreground mb-1">物理表名 *</label>
              <input
                required
                value={newTableName}
                onChange={(e) => setNewTableName(e.target.value)}
                placeholder="如：main_orders"
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100 font-mono"
              />
            </div>
            <div>
              <label className="block text-[11px] text-muted-foreground mb-1">业务名称</label>
              <input
                value={newDisplayName}
                onChange={(e) => setNewDisplayName(e.target.value)}
                placeholder="如：收费订单主表"
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
              />
            </div>
            <div>
              <label className="block text-[11px] text-muted-foreground mb-1">分类</label>
              <select
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-surface-100"
              >
                <option value="CORE_MAIN">核心主表</option>
                <option value="DETAIL">明细表</option>
                <option value="CONFIG">配置字典</option>
                <option value="LOG">日志审计</option>
              </select>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setShowNewEntityModal(false)}
                className="px-3 py-1.5 rounded-lg border border-border"
              >
                取消
              </button>
              <button type="submit" className="px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-medium">
                创建表节点
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

