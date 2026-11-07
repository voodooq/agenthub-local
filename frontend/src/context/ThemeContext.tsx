'use client';

import React, { createContext, useContext, useEffect, useState } from 'react';

export type ThemeMode = 'light' | 'dark' | 'system';

interface ThemeContextType {
  theme: ThemeMode;
  resolvedTheme: 'light' | 'dark';
  setTheme: (theme: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextType>({
  theme: 'dark',
  resolvedTheme: 'dark',
  setTheme: () => {},
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeMode>('dark');
  const [resolvedTheme, setResolvedTheme] = useState<'light' | 'dark'>('dark');
  const [mounted, setMounted] = useState(false);

  // 初始化加载本地持久化偏好
  useEffect(() => {
    try {
      const saved = localStorage.getItem('geminihub_theme_mode') as ThemeMode;
      if (saved && ['light', 'dark', 'system'].includes(saved)) {
        setThemeState(saved);
      } else {
        setThemeState('dark');
      }
    } catch {
      setThemeState('dark');
    }
    setMounted(true);
  }, []);

  // 监听并计算实际生效的主题模式
  useEffect(() => {
    if (!mounted) return;

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

    const computeResolved = (mode: ThemeMode): 'light' | 'dark' => {
      if (mode === 'system') {
        return mediaQuery.matches ? 'dark' : 'light';
      }
      return mode;
    };

    const nextResolved = computeResolved(theme);
    setResolvedTheme(nextResolved);

    // 动态给 html 根节点增加/移除 dark 类
    const root = document.documentElement;
    if (nextResolved === 'dark') {
      root.classList.add('dark');
      root.classList.remove('light');
      root.style.colorScheme = 'dark';
    } else {
      root.classList.add('light');
      root.classList.remove('dark');
      root.style.colorScheme = 'light';
    }

    const handleChange = () => {
      if (theme === 'system') {
        const autoResolved = mediaQuery.matches ? 'dark' : 'light';
        setResolvedTheme(autoResolved);
        if (autoResolved === 'dark') {
          root.classList.add('dark');
          root.classList.remove('light');
          root.style.colorScheme = 'dark';
        } else {
          root.classList.add('light');
          root.classList.remove('dark');
          root.style.colorScheme = 'light';
        }
      }
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, [theme, mounted]);

  const setTheme = (newTheme: ThemeMode) => {
    setThemeState(newTheme);
    try {
      localStorage.setItem('geminihub_theme_mode', newTheme);
    } catch {}
  };

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}

