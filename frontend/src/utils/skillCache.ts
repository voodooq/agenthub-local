/**
 * 前端技能列表与 SOP 详情的 SWR 内存 + SessionStorage 缓存管理器
 * 核心设计目标：
 * 1. 点击 `/ 技能` 或输入 `/` 时，如有缓存立即 0ms 渲染，不再白屏等待
 * 2. 同工作区 / 扩展列表并发请求自动去重，避免重复扫盘
 * 3. SOP 明文正文仅在用户点开查看详情时按需懒加载并缓存
 */
import { ExtensionSkillItem, SkillItem } from '@/types';
import { apiFetch } from './apiClient';

const WS_STORAGE_PREFIX = 'agenthub_skills_ws_cache_v1:';
const EXT_STORAGE_KEY = 'agenthub_skills_ext_cache_v1';

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
    const list: SkillItem[] = Array.isArray(data.skills) ? data.skills : [];
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
 * 按需懒加载单个工作区技能的完整 SOP 正文，并在本地缓存
 */
export async function fetchWorkspaceSkillDetail(
  skillName: string,
  workspaceId?: string
): Promise<SkillItem | null> {
  const cleanName = skillName.trim();
  if (!cleanName) return null;

  const wsKey = normalizeWorkspaceKey(workspaceId);
  const detailKey = `${wsKey}::${cleanName.toLowerCase()}`;
  const cached = workspaceSkillDetailCache.get(detailKey);
  if (cached) return cached;

  const pending = workspaceDetailInFlight.get(detailKey);
  if (pending) return pending;

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
    workspaceDetailInFlight.delete(detailKey);
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
    const list: ExtensionSkillItem[] = Array.isArray(data.skills) ? data.skills : [];
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
 * 按需懒加载单个扩展技能包的完整 SKILL.md 原文与详情
 */
export async function fetchExtensionSkillDetail(
  skillId: string
): Promise<ExtensionSkillItem | null> {
  const cleanId = skillId.trim();
  if (!cleanId) return null;

  const key = cleanId.toLowerCase();
  const cached = extensionDetailCache.get(key);
  if (cached) return cached;

  const pending = extensionDetailInFlight.get(key);
  if (pending) return pending;

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
    extensionDetailInFlight.delete(key);
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

