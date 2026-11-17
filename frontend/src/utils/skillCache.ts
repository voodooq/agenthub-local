/**
 * 前端技能列表与 SOP 详情的 SWR 内存 + SessionStorage 缓存管理器
 * 核心设计目标：
 * 1. 点击 `/ 技能` 或输入 `/` 时，如有缓存立即 0ms 渲染，不再白屏等待
 * 2. 同工作区 / 扩展列表并发请求自动去重，避免重复扫盘
 * 3. SOP 明文正文仅在用户点开查看详情时按需懒加载并缓存
 */
import {
  ExtensionSkillItem,
  SkillItem,
  SkillRollbackResponse,
  SkillVersionDetail,
  SkillVersionListResponse,
} from '@/types';
import { apiFetch } from './apiClient';

export const SKILLS_UPDATED_EVENT = 'agenthub:skills-updated';

export interface SkillsUpdatedDetail {
  workspaceId?: string;
  scope: 'workspace' | 'extension' | 'all';
  reason?: 'hot_reload' | 'rollback' | 'manual' | 'mutation';
  skillName?: string;
}

const WS_STORAGE_PREFIX = 'agenthub_skills_ws_cache_v2:';
const EXT_STORAGE_KEY = 'agenthub_skills_ext_cache_v2';

// 初始化时自动清理 v1 历史废弃旧缓存
if (typeof window !== 'undefined') {
  try {
    const keysToRemove: string[] = [];
    for (let i = 0; i < window.sessionStorage.length; i += 1) {
      const k = window.sessionStorage.key(i);
      if (k && (k.includes('_v1') || k.includes('agenthub_skills_ws_cache_v1'))) {
        keysToRemove.push(k);
      }
    }
    keysToRemove.forEach((k) => window.sessionStorage.removeItem(k));
  } catch {
    // 忽略 session storage 访问异常
  }
}

const workspaceSkillsCache = new Map<string, SkillItem[]>();
const workspaceInFlight = new Map<string, Promise<SkillItem[]>>();
const workspaceSkillDetailCache = new Map<string, SkillItem>();
const workspaceDetailInFlight = new Map<string, Promise<SkillItem | null>>();

let extensionSkillsCache: ExtensionSkillItem[] | null = null;
let extensionInFlight: Promise<ExtensionSkillItem[]> | null = null;
const extensionDetailCache = new Map<string, ExtensionSkillItem>();
const extensionDetailInFlight = new Map<string, Promise<ExtensionSkillItem | null>>();

function normalizeWorkspaceKey(workspaceId?: string): string {
  return workspaceId?.trim() || 'default';
}

function readStorageJson<T>(key: string): T | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function writeStorageJson(key: string, value: unknown): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 忽略隐私模式或配额超限错误，保留内存缓存即可
  }
}

function removeStorageByPrefix(prefix: string): void {
  if (typeof window === 'undefined') return;
  try {
    const keysToDelete: string[] = [];
    for (let i = 0; i < window.sessionStorage.length; i += 1) {
      const k = window.sessionStorage.key(i);
      if (k && k.startsWith(prefix)) {
        keysToDelete.push(k);
      }
    }
    for (const k of keysToDelete) {
      window.sessionStorage.removeItem(k);
    }
  } catch {
    // 忽略 storage 异常
  }
}

/**
 * 同步读取指定工作区的技能摘要缓存（命中则立即返回，否则返回 null）
 */
export function getCachedWorkspaceSkills(workspaceId?: string): SkillItem[] | null {
  const key = normalizeWorkspaceKey(workspaceId);
  const mem = workspaceSkillsCache.get(key);
  if (mem) return mem;

  const stored = readStorageJson<SkillItem[]>(`${WS_STORAGE_PREFIX}${key}`);
  if (Array.isArray(stored)) {
    workspaceSkillsCache.set(key, stored);
    return stored;
  }
  return null;
}

/**
 * 拉取指定工作区技能摘要列表（默认带并发去重，支持 force 后台静默更新及 refreshBackend 强制重扫磁盘）
 */
export async function fetchWorkspaceSkills(
  workspaceId?: string,
  options?: { force?: boolean; refreshBackend?: boolean }
): Promise<SkillItem[]> {
  const key = normalizeWorkspaceKey(workspaceId);
  const refreshBackend = Boolean(options?.refreshBackend);

  if (!options?.force && !refreshBackend) {
    const cached = getCachedWorkspaceSkills(key);
    if (cached) return cached;
  }

  if (!refreshBackend) {
    const pending = workspaceInFlight.get(key);
    if (pending) return pending;
  }

  const requestPromise = (async (): Promise<SkillItem[]> => {
    const params = new URLSearchParams();
    if (workspaceId) params.set('workspace_id', workspaceId);
    if (refreshBackend) params.set('refresh', 'true');
    const qs = params.toString() ? `?${params.toString()}` : '';
    const res = await apiFetch(`/api/v1/skills${qs}`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    const list: SkillItem[] = Array.isArray(data.skills)
      ? data.skills.filter((s: SkillItem) => s.compatible !== false)
      : [];
    workspaceSkillsCache.set(key, list);
    writeStorageJson(`${WS_STORAGE_PREFIX}${key}`, list);
    return list;
  })();

  workspaceInFlight.set(key, requestPromise);
  try {
    return await requestPromise;
  } finally {
    if (workspaceInFlight.get(key) === requestPromise) {
      workspaceInFlight.delete(key);
    }
  }
}

/**
 * 静默预加载工作区技能摘要（若已有缓存则跳过，绝不阻断 UI）
 */
export function prefetchWorkspaceSkills(workspaceId?: string): void {
  const key = normalizeWorkspaceKey(workspaceId);
  if (workspaceSkillsCache.has(key) || workspaceInFlight.has(key)) return;
  fetchWorkspaceSkills(workspaceId, { force: true }).catch(() => {
    // 静默预热忽略网络未就绪等瞬时错误
  });
}

/**
 * 广播技能变更事件，通知所有已挂载的技能列表/抽屉/补全组件同步热更新
 */
export function emitSkillsUpdated(detail: SkillsUpdatedDetail): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<SkillsUpdatedDetail>(SKILLS_UPDATED_EVENT, { detail }));
}

/**
 * 订阅技能变更事件，返回取消订阅函数
 */
export function subscribeSkillsUpdated(
  listener: (detail: SkillsUpdatedDetail) => void
): () => void {
  if (typeof window === 'undefined') return () => {};
  const handler = (ev: Event) => {
    const customEv = ev as CustomEvent<SkillsUpdatedDetail>;
    listener(customEv.detail || { scope: 'all' });
  };
  window.addEventListener(SKILLS_UPDATED_EVENT, handler);
  return () => window.removeEventListener(SKILLS_UPDATED_EVENT, handler);
}

/**
 * 判断文件路径是否指向技能定义文件（用于工具执行完成后的热更新兜底识别）
 */
export function isSkillFilePath(filePath?: string | null): boolean {
  if (!filePath) return false;
  const normalized = filePath.replace(/\\/g, '/').trim();
  return /(?:^|\/)SKILL\.md$/i.test(normalized) || /(?:^|\/)\.?skills\/.+\.md$/i.test(normalized);
}

/**
 * 按需懒加载单个工作区技能的完整 SOP 正文，并在本地缓存（支持版本号校验与强制刷新）
 */
export async function fetchWorkspaceSkillDetail(
  skillName: string,
  workspaceId?: string,
  options?: { force?: boolean; expectedVersion?: string }
): Promise<SkillItem | null> {
  const cleanName = skillName.trim();
  if (!cleanName) return null;

  const wsKey = normalizeWorkspaceKey(workspaceId);
  const detailKey = `${wsKey}::${cleanName.toLowerCase()}`;
  const cached = workspaceSkillDetailCache.get(detailKey);
  const versionMatched =
    !options?.expectedVersion ||
    (cached?.version || '1.0.0') === options.expectedVersion;
  if (!options?.force && cached && versionMatched) {
    return cached;
  }

  if (!options?.force) {
    const pending = workspaceDetailInFlight.get(detailKey);
    if (pending) return pending;
  }

  const requestPromise = (async (): Promise<SkillItem | null> => {
    const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
    const res = await apiFetch(`/api/v1/skills/${encodeURIComponent(cleanName)}${qs}`);
    if (!res.ok) return null;
    const detail: SkillItem = await res.json();
    workspaceSkillDetailCache.set(detailKey, detail);
    return detail;
  })();

  workspaceDetailInFlight.set(detailKey, requestPromise);
  try {
    return await requestPromise;
  } finally {
    if (workspaceDetailInFlight.get(detailKey) === requestPromise) {
      workspaceDetailInFlight.delete(detailKey);
    }
  }
}

/**
 * 失效工作区技能缓存（增删技能或点击「扫描同步」后调用）
 */
export function invalidateWorkspaceSkills(workspaceId?: string): void {
  if (!workspaceId) {
    workspaceSkillsCache.clear();
    workspaceSkillDetailCache.clear();
    removeStorageByPrefix(WS_STORAGE_PREFIX);
    return;
  }
  const key = normalizeWorkspaceKey(workspaceId);
  workspaceSkillsCache.delete(key);
  for (const dk of Array.from(workspaceSkillDetailCache.keys())) {
    if (dk.startsWith(`${key}::`)) {
      workspaceSkillDetailCache.delete(dk);
    }
  }
  if (typeof window !== 'undefined') {
    try {
      window.sessionStorage.removeItem(`${WS_STORAGE_PREFIX}${key}`);
    } catch {
      // 忽略异常
    }
  }
}

/**
 * 同步读取本地扩展技能 & MCP 摘要缓存
 */
export function getCachedExtensionSkills(): ExtensionSkillItem[] | null {
  if (extensionSkillsCache) return extensionSkillsCache;
  const stored = readStorageJson<ExtensionSkillItem[]>(EXT_STORAGE_KEY);
  if (Array.isArray(stored)) {
    extensionSkillsCache = stored;
    return stored;
  }
  return null;
}

/**
 * 直接覆写扩展技能缓存（供新建/导入/删除接口返回的最新列表使用）
 */
export function setExtensionSkillsCache(skills: ExtensionSkillItem[]): void {
  extensionSkillsCache = skills;
  extensionDetailCache.clear();
  writeStorageJson(EXT_STORAGE_KEY, skills);
}

/**
 * 拉取本地扩展技能与 MCP 封装列表摘要（带并发去重，支持 refreshBackend 强制绕过后端快照重扫）
 */
export async function fetchExtensionSkills(
  options?: { force?: boolean; refreshBackend?: boolean }
): Promise<ExtensionSkillItem[]> {
  const refreshBackend = Boolean(options?.refreshBackend);

  if (!options?.force && !refreshBackend) {
    const cached = getCachedExtensionSkills();
    if (cached) return cached;
  }

  if (!refreshBackend && extensionInFlight) return extensionInFlight;

  const requestPromise = (async (): Promise<ExtensionSkillItem[]> => {
    const qs = refreshBackend ? '?refresh=true' : '';
    const res = await apiFetch(`/api/v1/extensions/skills${qs}`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    const list: ExtensionSkillItem[] = Array.isArray(data.skills)
      ? data.skills.filter((s: ExtensionSkillItem) => s.compatible !== false)
      : [];
    extensionSkillsCache = list;
    writeStorageJson(EXT_STORAGE_KEY, list);
    return list;
  })();

  extensionInFlight = requestPromise;
  try {
    return await requestPromise;
  } finally {
    if (extensionInFlight === requestPromise) {
      extensionInFlight = null;
    }
  }
}

/**
 * 静默预热扩展技能摘要列表
 */
export function prefetchExtensionSkills(): void {
  if (extensionSkillsCache || extensionInFlight) return;
  fetchExtensionSkills({ force: true }).catch(() => {
    // 静默预热忽略错误
  });
}

/**
 * 按需懒加载单个扩展技能包的完整 SKILL.md 原文与详情（支持版本号校验与强制刷新）
 */
export async function fetchExtensionSkillDetail(
  skillId: string,
  options?: { force?: boolean; expectedVersion?: string }
): Promise<ExtensionSkillItem | null> {
  const cleanId = skillId.trim();
  if (!cleanId) return null;

  const key = cleanId.toLowerCase();
  const cached = extensionDetailCache.get(key);
  const versionMatched =
    !options?.expectedVersion ||
    (cached?.version || '1.0.0') === options.expectedVersion;
  if (!options?.force && cached && versionMatched) {
    return cached;
  }

  if (!options?.force) {
    const pending = extensionDetailInFlight.get(key);
    if (pending) return pending;
  }

  const requestPromise = (async (): Promise<ExtensionSkillItem | null> => {
    const res = await apiFetch(`/api/v1/extensions/skills/${encodeURIComponent(cleanId)}`);
    if (!res.ok) return null;
    const detail: ExtensionSkillItem = await res.json();
    extensionDetailCache.set(key, detail);
    return detail;
  })();

  extensionDetailInFlight.set(key, requestPromise);
  try {
    return await requestPromise;
  } finally {
    if (extensionDetailInFlight.get(key) === requestPromise) {
      extensionDetailInFlight.delete(key);
    }
  }
}

/**
 * 清空扩展技能缓存
 */
export function invalidateExtensionSkills(): void {
  extensionSkillsCache = null;
  extensionDetailCache.clear();
  if (typeof window !== 'undefined') {
    try {
      window.sessionStorage.removeItem(EXT_STORAGE_KEY);
    } catch {
      // 忽略异常
    }
  }
}

let hotRefreshInFlight: Promise<void> | null = null;

/**
 * 沙盒写入/回滚后全量失效前后端技能缓存并实时拉取最新列表，随后广播热更新事件
 */
export async function invalidateAndRefreshSkills(
  workspaceId?: string,
  options?: { reason?: SkillsUpdatedDetail['reason']; skillName?: string }
): Promise<void> {
  if (hotRefreshInFlight) {
    return hotRefreshInFlight;
  }

  hotRefreshInFlight = (async () => {
    invalidateWorkspaceSkills(workspaceId);
    invalidateExtensionSkills();
    await Promise.all([
      fetchWorkspaceSkills(workspaceId, { force: true, refreshBackend: true }).catch(() => []),
      fetchExtensionSkills({ force: true, refreshBackend: true }).catch(() => []),
    ]);
    emitSkillsUpdated({
      workspaceId,
      scope: 'all',
      reason: options?.reason || 'hot_reload',
      skillName: options?.skillName,
    });
  })();

  try {
    await hotRefreshInFlight;
  } finally {
    hotRefreshInFlight = null;
  }
}

/**
 * 查询技能的历史版本列表（支持工作区技能与全局扩展技能）
 */
export async function fetchSkillVersions(
  scope: 'workspace' | 'extension',
  identifier: string,
  workspaceId?: string
): Promise<SkillVersionListResponse> {
  const cleanId = encodeURIComponent(identifier.trim());
  const url =
    scope === 'extension'
      ? `/api/v1/extensions/skills/${cleanId}/versions`
      : `/api/v1/skills/${cleanId}/versions${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''}`;
  const res = await apiFetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `获取版本历史失败 (HTTP ${res.status})`);
  }
  return res.json();
}

/**
 * 获取指定历史版本的完整 SKILL.md 原文与正文用于预览
 */
export async function fetchSkillVersionDetail(
  scope: 'workspace' | 'extension',
  identifier: string,
  versionId: string,
  workspaceId?: string
): Promise<SkillVersionDetail> {
  const cleanId = encodeURIComponent(identifier.trim());
  const cleanVer = encodeURIComponent(versionId.trim());
  const url =
    scope === 'extension'
      ? `/api/v1/extensions/skills/${cleanId}/versions/${cleanVer}`
      : `/api/v1/skills/${cleanId}/versions/${cleanVer}${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''}`;
  const res = await apiFetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `获取版本详情失败 (HTTP ${res.status})`);
  }
  return res.json();
}

/**
 * 一键回滚技能到指定历史版本，并同步失效/刷新前端缓存
 */
export async function rollbackSkillVersion(
  scope: 'workspace' | 'extension',
  identifier: string,
  versionId: string,
  workspaceId?: string
): Promise<SkillRollbackResponse> {
  const cleanId = encodeURIComponent(identifier.trim());
  const isExt = scope === 'extension';
  const url = isExt
    ? `/api/v1/extensions/skills/${cleanId}/rollback`
    : `/api/v1/skills/${cleanId}/rollback`;
  const body = isExt
    ? { version_id: versionId }
    : { version_id: versionId, workspace_id: workspaceId ?? null };

  const res = await apiFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `回滚技能版本失败 (HTTP ${res.status})`);
  }
  const data: SkillRollbackResponse = await res.json();

  // 回滚后立即全量刷新缓存并广播更新事件
  await invalidateAndRefreshSkills(workspaceId, {
    reason: 'rollback',
    skillName: identifier,
  });

  return data;
}

