'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Globe, ChevronDown, Check, Laptop } from 'lucide-react';
import { useLanguage, LocaleMode } from '@/context/LanguageContext';

export function LanguageToggle() {
  const { localeMode, resolvedLocale, setLocaleMode } = useLanguage();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const options: { mode: LocaleMode; label: string; shortLabel: string; desc: string }[] = [
    {
      mode: 'system',
      label: '跟随系统 (System)',
      shortLabel: `自动 (${resolvedLocale === 'zh-CN' ? '简' : resolvedLocale === 'zh-TW' ? '繁' : resolvedLocale.toUpperCase()})`,
      desc: '自动识别操作系统/浏览器默认语言',
    },
    {
      mode: 'zh-CN',
      label: '简体中文',
      shortLabel: '简体中文',
      desc: 'Simplified Chinese (zh-CN)',
    },
    {
      mode: 'zh-TW',
      label: '繁體中文',
      shortLabel: '繁體中文',
      desc: 'Traditional Chinese (zh-TW)',
    },
    {
      mode: 'en',
      label: 'English',
      shortLabel: 'English',
      desc: 'International English (en-US)',
    },
    {
      mode: 'ja',
      label: '日本語',
      shortLabel: '日本語',
      desc: 'Japanese (ja-JP)',
    },
  ];

  const currentOption = options.find((opt) => opt.mode === localeMode) || options[0];

  return (
    <div className="relative inline-block" ref={containerRef} data-no-i18n="true">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-full border border-border/80 bg-surface-100 hover:bg-surface-50 text-foreground text-xs font-medium transition-all shadow-sm select-none"
        title={`当前语言：${currentOption.label}`}
      >
        <Globe className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
        <span className="hidden sm:inline-block text-xs font-medium">{currentOption.shortLabel}</span>
        <ChevronDown
          className={`w-3 h-3 opacity-60 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-1.5 w-56 rounded-2xl glass-modal p-1.5 shadow-2xl border border-border/80 z-50 animate-scale-in">
          <div className="px-2.5 py-1.5 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider border-b border-border/60 mb-1 flex items-center justify-between">
            <span>界面显示语言 / Language</span>
            <Laptop className="w-3 h-3 opacity-60" />
          </div>
          <div className="space-y-0.5">
            {options.map((opt) => {
              const isSelected = localeMode === opt.mode;
              return (
                <button
                  key={opt.mode}
                  type="button"
                  onClick={() => {
                    setLocaleMode(opt.mode);
                    setIsOpen(false);
                  }}
                  className={`w-full flex items-center justify-between p-2 rounded-xl text-left transition-colors ${
                    isSelected
                      ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-semibold border border-emerald-500/25'
                      : 'hover:bg-foreground/[0.05] text-foreground'
                  }`}
                >
                  <div>
                    <div className="text-xs font-medium leading-tight">{opt.label}</div>
                    <div className="text-[10px] text-muted-foreground mt-0.5">{opt.desc}</div>
                  </div>
                  {isSelected && <Check className="w-3.5 h-3.5 text-emerald-500 shrink-0 ml-1" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

