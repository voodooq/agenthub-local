'use client';

import React, { useEffect, useState } from 'react';
import { Folder, ArrowUp, RefreshCw, AlertCircle } from 'lucide-react';
import { apiFetch } from '@/utils/apiClient';

interface DirBookmark {
  label: string;
  path: string;
}

interface DirEntry {
  name: string;
  path: string;
}

interface ServerFolderBrowserProps {
  currentPath: string;
  onSelectPath: (selectedPath: string) => void;
}

/**
 * 远程服务端目录浏览器：供 Linux / Docker 无头环境下在 Web 弹窗中点选服务器目录
 */
export function ServerFolderBrowser({ currentPath, onSelectPath }: ServerFolderBrowserProps) {
  const [browsePath, setBrowsePath] = useState<string>(currentPath || '');
  const [parentPath, setParentPath] = useState<string>('');
  const [bookmarks, setBookmarks] = useState<DirBookmark[]>([]);
  const [dirs, setDirs] = useState<DirEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');

  const loadDirectory = async (targetPath?: string) => {
    setLoading(true);
    setError('');
    try {
      const qs = targetPath ? `?path=${encodeURIComponent(targetPath)}` : '';
      const res = await apiFetch(`/api/v1/workspaces/browse-dirs${qs}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data?.detail || '读取服务器目录失败');
        return;
      }
      setBrowsePath(data.current_path || '');
      setParentPath(data.parent_path || '');
      setBookmarks(Array.isArray(data.bookmarks) ? data.bookmarks : []);
      setDirs(Array.isArray(data.dirs) ? data.dirs : []);
      if (data.current_path) {
        onSelectPath(data.current_path);
      }
    } catch (e: any) {
      setError(e?.message || '网络请求失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDirectory(currentPath || undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="rounded-xl border border-border bg-surface-100/70 p-2.5 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center flex-wrap gap-1">
          {bookmarks.map((bm) => (
            <button
              key={bm.path}
              type="button"
              onClick={() => loadDirectory(bm.path)}
              className={`px-2 py-0.5 rounded-md text-[11px] border transition ${
                browsePath === bm.path
                  ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-500 font-medium'
                  : 'bg-surface-200/70 border-border text-muted-foreground hover:text-foreground'
              }`}
            >
              {bm.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {parentPath && (
            <button
              type="button"
              onClick={() => loadDirectory(parentPath)}
              className="p-1 rounded-md hover:bg-surface-200 text-muted-foreground hover:text-foreground"
              title="返回上级目录"
            >
              <ArrowUp className="w-3.5 h-3.5" />
            </button>
          )}
          <button
            type="button"
            onClick={() => loadDirectory(browsePath)}
            className="p-1 rounded-md hover:bg-surface-200 text-muted-foreground hover:text-foreground"
            title="刷新目录"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {error ? (
        <div className="text-[11px] text-rose-400 flex items-center gap-1.5 py-1">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : (
        <div className="max-h-36 overflow-y-auto space-y-0.5 pr-1">
          {dirs.length === 0 ? (
            <div className="text-[11px] text-muted-foreground py-2 text-center">
              {loading ? '正在读取目录...' : '当前目录下无子文件夹'}
            </div>
          ) : (
            dirs.map((dir) => (
              <button
                key={dir.path}
                type="button"
                onClick={() => loadDirectory(dir.path)}
                className="w-full flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-surface-200 text-left text-xs text-foreground transition"
              >
                <Folder className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                <span className="truncate font-mono text-[11px]">{dir.name}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

