import { useCallback, useEffect, useRef, useState } from 'react';
import { Message } from '@/types';
import { apiFetch } from '@/utils/apiClient';
import {
  extractGeneratedFilePaths,
  normalizeFileVerifyKey,
} from '@/utils/filePathDetector';

export interface VerifiedFileEntry {
  rawPath: string;
  exists: boolean;
  isFile: boolean;
  relPath: string;
  fileName: string;
  fileSize: number;
  mtime: string | null;
  snapshotCount?: number;
}

const VERIFY_BATCH_LIMIT = 50;

/**
 * 批量核验聊天记录中 AI 引用/声称生成的文件路径是否真实存在于当前工作区磁盘，
 * 防止无工具伪落盘幻觉或异常中断消息在界面误渲染成“生成文件直达”绿卡。
 */
export function useVerifiedWorkspacePaths(
  workspaceId: string,
  messages: Message[],
  isStreaming: boolean
) {
  const [verifiedMap, setVerifiedMap] = useState<Map<string, VerifiedFileEntry>>(() => new Map());
  const cacheRef = useRef<Map<string, VerifiedFileEntry>>(new Map());
  const pendingKeysRef = useRef<Set<string>>(new Set());
  const epochRef = useRef<number>(0);
  const [refreshTick, setRefreshTick] = useState<number>(0);

  // 工作区切换时彻底清空旧缓存并作废未完成的跨区核验请求
  useEffect(() => {
    epochRef.current += 1;
    cacheRef.current.clear();
    pendingKeysRef.current.clear();
    setVerifiedMap(new Map());
  }, [workspaceId]);

  const invalidateVerification = useCallback(() => {
    epochRef.current += 1;
    cacheRef.current.clear();
    pendingKeysRef.current.clear();
    setRefreshTick((prev) => prev + 1);
  }, []);

  useEffect(() => {
    const effWsId = (workspaceId || 'default').trim() || 'default';
    const pendingItems: Array<{ key: string; rawPath: string }> = [];
    const seenBatchKeys = new Set<string>();

    for (let i = 0; i < messages.length; i += 1) {
      const msg = messages[i];
      if (!msg || msg.role === 'user' || !msg.content) continue;
      // 正在流式输出的末尾气泡等待 done/error 收口后统一批量校验，避免逐字触发网络请求
      if (isStreaming && i === messages.length - 1) continue;

      const candidates = extractGeneratedFilePaths(msg.content, 40);
      for (const rawPath of candidates) {
        const key = normalizeFileVerifyKey(rawPath);
        if (!key || seenBatchKeys.has(key)) continue;
        if (cacheRef.current.has(key) || pendingKeysRef.current.has(key)) continue;
        seenBatchKeys.add(key);
        pendingItems.push({ key, rawPath });
      }
    }

    if (pendingItems.length === 0) return;

    for (const item of pendingItems) {
      pendingKeysRef.current.add(item.key);
    }

    const requestEpoch = epochRef.current;

    const runVerifyBatches = async () => {
      for (let offset = 0; offset < pendingItems.length; offset += VERIFY_BATCH_LIMIT) {
        const slice = pendingItems.slice(offset, offset + VERIFY_BATCH_LIMIT);
        try {
          const resp = await apiFetch('/api/v1/artifacts/verify-paths', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              workspace_id: effWsId,
              paths: slice.map((item) => item.rawPath),
            }),
          });
          if (!resp.ok) {
            throw new Error(`verify-paths HTTP ${resp.status}`);
          }
          const data = await resp.json();
          if (epochRef.current !== requestEpoch) {
            return;
          }

          const results: Array<Record<string, unknown>> = Array.isArray(data?.results)
            ? data.results
            : [];
          const returnedKeys = new Set<string>();

          for (const rawItem of results) {
            const rawPath = String(rawItem?.raw_path || '').trim();
            if (!rawPath) continue;
            const entry: VerifiedFileEntry = {
              rawPath,
              exists: Boolean(rawItem?.exists),
              isFile: Boolean(rawItem?.is_file),
              relPath: String(rawItem?.rel_path || ''),
              fileName: String(rawItem?.file_name || ''),
              fileSize: Number(rawItem?.file_size || 0),
              mtime: rawItem?.mtime ? String(rawItem.mtime) : null,
              snapshotCount: Number(rawItem?.snapshot_count || 0),
            };
            const primaryKey = normalizeFileVerifyKey(rawPath);
            if (primaryKey) {
              cacheRef.current.set(primaryKey, entry);
              returnedKeys.add(primaryKey);
              pendingKeysRef.current.delete(primaryKey);
            }
            if (entry.exists && entry.relPath) {
              const relKey = normalizeFileVerifyKey(entry.relPath);
              if (relKey) {
                cacheRef.current.set(relKey, entry);
              }
            }
          }

          // 若个别请求项未命中服务端响应列表，保守写回不存在状态避免死循环重复探测
          for (const item of slice) {
            pendingKeysRef.current.delete(item.key);
            if (!returnedKeys.has(item.key) && !cacheRef.current.has(item.key)) {
              cacheRef.current.set(item.key, {
                rawPath: item.rawPath,
                exists: false,
                isFile: false,
                relPath: '',
                fileName: '',
                fileSize: 0,
                mtime: null,
              });
            }
          }

          setVerifiedMap(new Map(cacheRef.current));
        } catch (err) {
          for (const item of slice) {
            pendingKeysRef.current.delete(item.key);
          }
          console.warn('[useVerifiedWorkspacePaths] 批量核验路径失败:', err);
        }
      }
    };

    void runVerifyBatches();
  }, [workspaceId, messages, isStreaming, refreshTick]);

  const getVerifiedFile = useCallback(
    (rawPath: string): VerifiedFileEntry | undefined => {
      const key = normalizeFileVerifyKey(rawPath);
      if (!key) return undefined;
      return verifiedMap.get(key);
    },
    [verifiedMap]
  );

  return {
    getVerifiedFile,
    invalidateVerification,
  };
}

