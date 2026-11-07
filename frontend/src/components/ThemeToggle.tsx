'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Sun, Moon, Laptop, ChevronDown, Check } from 'lucide-react';
import { useTheme, ThemeMode } from '@/context/ThemeContext';

export function ThemeToggle() {
  const { theme, resolvedTheme, setTheme } = useTheme();
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

  const options: { mode: ThemeMode; label: string; icon: React.ReactNode; desc: string }[] = [
    {
      mode: 'light',
      label: '浅色模式',
      icon: <Sun className="w-4 h-4 text-amber-500" />,
      desc: '清晰明亮 · 舒适对比度',
    },
    {
      mode: 'dark',
      label: '深色模式',
      icon: <Moon className="w-4 h-4 text-emerald-400" />,
      desc: '护眼极客 · OLED 纯净深黑',
    },
    {
      mode: 'system',
      label: '跟随系统',
      icon: <Laptop className="w-4 h-4 text-purple-400" />,
      desc: '根据操作系统自动切换',
    },
  ];

  const currentOption = options.find((opt) => opt.mode === theme) || options[1];

  return (
    <div className="relative inline-block" ref={containerRef}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-full border border-border/80 bg-surface-100 hover:bg-surface-50 text-foreground text-xs font-medium transition-all shadow-sm select-none"
        title={`当前主题：${currentOption.label}`}
      >
        <span className="shrink-0">{currentOption.icon}</span>
        <span className="hidden sm:inline-block text-xs font-medium">{currentOption.label}</span>
        <ChevronDown
          className={`w-3 h-3 opacity-60 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-1.5 w-52 rounded-2xl glass-modal p-1.5 shadow-2xl border border-border/80 z-50 animate-scale-in">
          <div className="px-2.5 py-1.5 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider border-b border-border/60 mb-1">
            界面显示主题
          </div>
          <div className="space-y-0.5">
            {options.map((opt) => {
              const isSelected = theme === opt.mode;
              return (
                <button
                  key={opt.mode}
                  type="button"
                  onClick={() => {
                    setTheme(opt.mode);
                    setIsOpen(false);
                  }}
                  className={`w-full flex items-center justify-between p-2 rounded-xl text-left transition-colors ${
                    isSelected
                      ? 'bg-accent/15 text-accent font-semibold border border-accent/25'
                      : 'hover:bg-foreground/[0.05] text-foreground'
                  }`}
                >
                  <div className="flex items-center space-x-2.5">
                    <span className="shrink-0">{opt.icon}</span>
                    <div>
                      <div className="text-xs font-medium leading-tight">{opt.label}</div>
                      <div className="text-[10px] text-muted-foreground mt-0.5">{opt.desc}</div>
                    </div>
                  </div>
                  {isSelected && <Check className="w-3.5 h-3.5 text-accent shrink-0 ml-1" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

