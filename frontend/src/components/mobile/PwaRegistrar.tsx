'use client';

import { useEffect } from 'react';

/**
 * 在客户端静默注册 PWA Service Worker
 */
export function PwaRegistrar() {
  useEffect(() => {
    if (typeof window === 'undefined' || !('serviceWorker' in navigator)) {
      return;
    }
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // 非安全上下文或本地开发不支持时静默忽略
    });
  }, []);

  return null;
}

