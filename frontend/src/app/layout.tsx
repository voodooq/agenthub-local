import type { Metadata } from 'next';
import './globals.css';
import { ThemeProvider } from '@/context/ThemeContext';
import { LanguageProvider } from '@/context/LanguageContext';

export const metadata: Metadata = {
  title: 'AgentHub Local | 本地全场景多源算力与智能体工作台',
  description: '本地优先（Local-First）极客级多源大模型算力调度、Skill 容器与自主智能体工作台。',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="dark h-full">
      <body className="h-full bg-background text-foreground antialiased selection:bg-accent/30 selection:text-emerald-400">
        <ThemeProvider>
          <LanguageProvider>
            {children}
          </LanguageProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
