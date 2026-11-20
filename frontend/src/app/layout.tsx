import type { Metadata, Viewport } from 'next';
import './globals.css';
import { ThemeProvider } from '@/context/ThemeContext';
import { LanguageProvider } from '@/context/LanguageContext';
import { PwaRegistrar } from '@/components/mobile/PwaRegistrar';

export const metadata: Metadata = {
  title: 'AgentHub Local | 本地全场景多源算力与智能体工作台',
  description: '本地优先（Local-First）极客级多源大模型算力调度、Skill 容器与自主智能体工作台。',
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'default',
    title: 'AgentHub',
  },
  icons: {
    icon: '/icon.png',
    shortcut: '/favicon.ico',
    apple: '/icon.png',
  },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  viewportFit: 'cover',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)', color: '#09090b' },
  ],
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
            <PwaRegistrar />
            {children}
          </LanguageProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
