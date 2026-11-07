import type { Metadata } from 'next';
import './globals.css';
import { ThemeProvider } from '@/context/ThemeContext';

export const metadata: Metadata = {
  title: 'GeminiHub Local | 多账号无感接力与研发知识库终端',
  description: '专为重度研发设计的本地私有化多 Google AI 账号负载均衡、429 毫秒级故障转移与 FTS5 全量知识检索工作台。',
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
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
