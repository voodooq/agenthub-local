'use client';

import { useCallback, useEffect, useState } from 'react';

const DEV_DEBUG_STORAGE_KEY = 'ah_dev_debug_mode';
const DEV_DEBUG_EVENT = 'ah:dev-debug-change';

/**
 * 全局开发者调试视图状态 Hook
 * 默认关闭，用于隐藏 RRF/FTS5/向量维度/切片得分等底层工程参数。
 */
export function useDevDebugMode() {
  const [isDevDebug, setIsDevDebug] = useState<boolean>(false);

  useEffect(() => {
    const syncFromStorage = () => {
      try {
        setIsDevDebug(localStorage.getItem(DEV_DEBUG_STORAGE_KEY) === '1');
      } catch {
        setIsDevDebug(false);
      }
    };
    syncFromStorage();
    window.addEventListener(DEV_DEBUG_EVENT, syncFromStorage);
    window.addEventListener('storage', syncFromStorage);
    return () => {
      window.removeEventListener(DEV_DEBUG_EVENT, syncFromStorage);
      window.removeEventListener('storage', syncFromStorage);
    };
  }, []);

  const toggleDevDebug = useCallback(() => {
    setIsDevDebug((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(DEV_DEBUG_STORAGE_KEY, next ? '1' : '0');
      } catch {}
      window.dispatchEvent(new CustomEvent(DEV_DEBUG_EVENT));
      return next;
    });
  }, []);

  return { isDevDebug, toggleDevDebug };
}

