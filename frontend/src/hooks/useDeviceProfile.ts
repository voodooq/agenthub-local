'use client';

import { useCallback, useEffect, useState } from 'react';

export type DeviceMode = 'desktop' | 'mobile';

export interface DeviceProfile {
  mode: DeviceMode;
  isStandalone: boolean;
  isTouch: boolean;
  forceDesktop: boolean;
  setForceDesktop: (force: boolean) => void;
}

const FORCE_DESKTOP_KEY = 'ah_force_desktop';
const DEVICE_CHANGE_EVENT = 'ah:device-profile-change';

function evaluateDeviceProfile(): Omit<DeviceProfile, 'setForceDesktop'> {
  if (typeof window === 'undefined') {
    return { mode: 'desktop', isStandalone: false, isTouch: false, forceDesktop: false };
  }

  const forceDesktop = window.sessionStorage.getItem(FORCE_DESKTOP_KEY) === '1';
  const narrowScreen = window.innerWidth < 768;
  const isTouch = window.matchMedia?.('(pointer: coarse)').matches ?? false;
  const mobileUa = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
  const isStandalone =
    window.matchMedia?.('(display-mode: standalone)').matches === true ||
    Boolean((navigator as Navigator & { standalone?: boolean }).standalone);

  if (forceDesktop) {
    return { mode: 'desktop', isStandalone, isTouch, forceDesktop: true };
  }

  const hitSignals = [narrowScreen, isTouch, mobileUa, isStandalone].filter(Boolean).length;
  const mode: DeviceMode = narrowScreen || hitSignals >= 2 ? 'mobile' : 'desktop';

  return { mode, isStandalone, isTouch, forceDesktop: false };
}

/**
 * 响应式设备形态与 PWA Standalone 状态探测 Hook
 * 支持桌面/移动自动切换，并支持通过 sessionStorage 临时强制切回桌面完整版
 */
export function useDeviceProfile(): DeviceProfile {
  const [state, setState] = useState<Omit<DeviceProfile, 'setForceDesktop'>>({
    mode: 'desktop',
    isStandalone: false,
    isTouch: false,
    forceDesktop: false,
  });

  useEffect(() => {
    const sync = () => setState(evaluateDeviceProfile());
    sync();

    window.addEventListener('resize', sync);
    window.addEventListener(DEVICE_CHANGE_EVENT, sync);
    return () => {
      window.removeEventListener('resize', sync);
      window.removeEventListener(DEVICE_CHANGE_EVENT, sync);
    };
  }, []);

  const setForceDesktop = useCallback((force: boolean) => {
    if (typeof window === 'undefined') return;
    if (force) {
      window.sessionStorage.setItem(FORCE_DESKTOP_KEY, '1');
    } else {
      window.sessionStorage.removeItem(FORCE_DESKTOP_KEY);
    }
    window.dispatchEvent(new Event(DEVICE_CHANGE_EVENT));
  }, []);

  return {
    ...state,
    setForceDesktop,
  };
}

