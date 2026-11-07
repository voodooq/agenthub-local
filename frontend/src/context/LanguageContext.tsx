'use client';

import React, { createContext, useContext, useEffect, useState, useRef } from 'react';
import { translateText } from '@/utils/i18nDictionary';

export type LocaleMode = 'system' | 'zh-CN' | 'zh-TW' | 'en' | 'ja';
export type ResolvedLocale = 'zh-CN' | 'zh-TW' | 'en' | 'ja';

interface LanguageContextType {
  localeMode: LocaleMode;
  resolvedLocale: ResolvedLocale;
  setLocaleMode: (mode: LocaleMode) => void;
  t: (zhText: string) => string;
}

const STORAGE_KEY = 'agenthub-locale-mode';

function detectSystemLocale(): ResolvedLocale {
  if (typeof window === 'undefined') return 'zh-CN';
  const nav = (window.navigator.language || 'zh-CN').toLowerCase();
  if (nav.startsWith('zh-tw') || nav.startsWith('zh-hk') || nav.startsWith('zh-mo')) {
    return 'zh-TW';
  }
  if (nav.startsWith('zh')) {
    return 'zh-CN';
  }
  if (nav.startsWith('ja')) {
    return 'ja';
  }
  return 'en';
}

function translateString(raw: string, target: ResolvedLocale): string {
  return translateText(raw, target);
}

const LanguageContext = createContext<LanguageContextType>({
  localeMode: 'system',
  resolvedLocale: 'zh-CN',
  setLocaleMode: () => {},
  t: (s) => s,
});

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [localeMode, setLocaleModeState] = useState<LocaleMode>('system');
  const [resolvedLocale, setResolvedLocale] = useState<ResolvedLocale>('zh-CN');
  const originalTextMapRef = useRef<WeakMap<Node, string>>(new WeakMap());
  const originalAttrMapRef = useRef<WeakMap<Element, { placeholder?: string; title?: string }>>(new WeakMap());

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY) as LocaleMode | null;
    if (saved && ['system', 'zh-CN', 'zh-TW', 'en', 'ja'].includes(saved)) {
      setLocaleModeState(saved);
    } else {
      setLocaleModeState('system');
    }
  }, []);

  useEffect(() => {
    const updateResolved = () => {
      const nextResolved: ResolvedLocale = localeMode === 'system' ? detectSystemLocale() : localeMode;
      setResolvedLocale(nextResolved);
      if (typeof document !== 'undefined') {
        document.documentElement.lang = nextResolved;
      }
    };
    updateResolved();
    window.addEventListener('languagechange', updateResolved);
    return () => window.removeEventListener('languagechange', updateResolved);
  }, [localeMode]);

  // 全局轻量 DOM 文本节点与属性自动多语种映射器（跳过代码块与对话气泡正文）
  useEffect(() => {
    if (typeof document === 'undefined') return;

    const shouldSkipElement = (el: Element | null): boolean => {
      if (!el) return true;
      const tag = el.tagName;
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'CODE' || tag === 'PRE' || tag === 'TEXTAREA') {
        return true;
      }
      if (el.closest('pre, code, .prose, [data-no-i18n="true"]')) {
        return true;
      }
      return false;
    };

    const applyTranslationToSubtree = (root: Node) => {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let node: Node | null = walker.nextNode();
      while (node) {
        const parent = node.parentElement;
        if (!shouldSkipElement(parent)) {
          const currentVal = node.nodeValue || '';
          if (!originalTextMapRef.current.has(node)) {
            originalTextMapRef.current.set(node, currentVal);
          }
          const orig = originalTextMapRef.current.get(node) || currentVal;
          // 若组件自身动态更新了新的中文值，同步更新基准值
          const effectiveOrig = resolvedLocale !== 'zh-CN' && currentVal !== translateString(orig, resolvedLocale) && /[\u4e00-\u9fa5]/.test(currentVal)
            ? currentVal
            : orig;
          originalTextMapRef.current.set(node, effectiveOrig);

          const translated = translateString(effectiveOrig, resolvedLocale);
          if (node.nodeValue !== translated) {
            node.nodeValue = translated;
          }
        }
        node = walker.nextNode();
      }

      if (root instanceof Element || root instanceof Document) {
        const elements = root.querySelectorAll('[placeholder], [title]');
        elements.forEach((el) => {
          if (shouldSkipElement(el) && el.tagName !== 'TEXTAREA') return;
          let savedAttrs = originalAttrMapRef.current.get(el);
          if (!savedAttrs) {
            savedAttrs = {
              placeholder: el.getAttribute('placeholder') || undefined,
              title: el.getAttribute('title') || undefined,
            };
            originalAttrMapRef.current.set(el, savedAttrs);
          }
          if (savedAttrs.placeholder) {
            const nextPh = translateString(savedAttrs.placeholder, resolvedLocale);
            if (el.getAttribute('placeholder') !== nextPh) {
              el.setAttribute('placeholder', nextPh);
            }
          }
          if (savedAttrs.title) {
            const nextTitle = translateString(savedAttrs.title, resolvedLocale);
            if (el.getAttribute('title') !== nextTitle) {
              el.setAttribute('title', nextTitle);
            }
          }
        });
      }
    };

    applyTranslationToSubtree(document.body);

    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        m.addedNodes.forEach((added) => {
          applyTranslationToSubtree(added);
        });
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [resolvedLocale]);

  const setLocaleMode = (mode: LocaleMode) => {
    setLocaleModeState(mode);
    localStorage.setItem(STORAGE_KEY, mode);
  };

  const t = (zhText: string) => translateString(zhText, resolvedLocale);

  return (
    <LanguageContext.Provider value={{ localeMode, resolvedLocale, setLocaleMode, t }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  return useContext(LanguageContext);
}
