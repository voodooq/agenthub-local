'use client';

import { useState, useEffect } from 'react';

export interface AppVersionInfo {
  version: string;
  tag: string;
  edition: string;
  fullLabel: string;
}

const INITIAL_VERSION = (process.env.NEXT_PUBLIC_APP_VERSION || '0.0.0').replace(/^v/i, '').trim();
const INITIAL_EDITION = (process.env.NEXT_PUBLIC_APP_EDITION || 'Strata Edition').trim();

/**
 * 格式化版本号与版本后缀标签
 */
export function formatAppVersionInfo(version: string, edition: string = INITIAL_EDITION): AppVersionInfo {
  const cleanVersion = version.replace(/^v/i, '').trim() || INITIAL_VERSION;
  const cleanEdition = edition.trim() || INITIAL_EDITION;
  const tag = `v${cleanVersion}`;
  return {
    version: cleanVersion,
    tag,
    edition: cleanEdition,
    fullLabel: `${tag} · ${cleanEdition}`,
  };
}

export const APP_VERSION_INFO: AppVersionInfo = formatAppVersionInfo(INITIAL_VERSION, INITIAL_EDITION);

let runtimeVersionInfo: AppVersionInfo = APP_VERSION_INFO;
let runtimeSyncPromise: Promise<void> | null = null;
const versionListeners = new Set<(info: AppVersionInfo) => void>();

function ensureRuntimeVersionSynced(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();
  if (!runtimeSyncPromise) {
    runtimeSyncPromise = fetch('/api/v1/health')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && typeof data.version === 'string' && data.version.trim()) {
          const next = formatAppVersionInfo(
            data.version,
            typeof data.edition === 'string' && data.edition.trim() ? data.edition : INITIAL_EDITION
          );
          if (next.fullLabel !== runtimeVersionInfo.fullLabel) {
            runtimeVersionInfo = next;
            versionListeners.forEach((listener) => listener(next));
          }
        }
      })
      .catch(() => {
        // 离线或后端未启动时静默保留构建期注入的版本配置
      });
  }
  return runtimeSyncPromise;
}

/**
 * 全局统一获取应用版本号的自定义 Hook。
 * 首帧渲染直接采用构建期由 version.json 注入的值（零闪烁），
 * 并于客户端挂载后自动向 /api/v1/health 核对运行期配置，保持前后端严格同步。
 */
export function useAppVersion(): AppVersionInfo {
  const [info, setInfo] = useState<AppVersionInfo>(runtimeVersionInfo);

  useEffect(() => {
    versionListeners.add(setInfo);
    setInfo((prev) => (prev.fullLabel === runtimeVersionInfo.fullLabel ? prev : runtimeVersionInfo));
    void ensureRuntimeVersionSynced();
    return () => {
      versionListeners.delete(setInfo);
    };
  }, []);

  return info;
}

