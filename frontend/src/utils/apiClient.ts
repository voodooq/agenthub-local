/**
 * 本地私有 API 安全网络请求客户端
 * 负责自动携带 X-Access-Token 请求头，支持自动向本地后端握手获取令牌与异常拦截
 */

const TOKEN_STORAGE_KEY = 'agenthub_access_token';
const LEGACY_TOKEN_STORAGE_KEY = 'geminihub_access_token';

let cachedToken: string | null = null;

/**
 * 获取当前的访问令牌
 */
export async function getAccessToken(): Promise<string> {
  if (typeof window === 'undefined') {
    return '';
  }

  // 1. 优先从内存缓存中读取
  if (cachedToken) {
    return cachedToken;
  }

  // 2. 从本地 localStorage 中读取 (兼容旧 key)
  const stored = localStorage.getItem(TOKEN_STORAGE_KEY) || localStorage.getItem(LEGACY_TOKEN_STORAGE_KEY);
  if (stored) {
    cachedToken = stored;
    return stored;
  }

  // 3. 首次加载时尝试向本地后端握手自动获取令牌（仅限本机 127.0.0.1）
  try {
    const res = await fetch('/api/v1/auth/local-token');
    if (res.ok) {
      const data = await res.json();
      if (data.token) {
        cachedToken = data.token;
        localStorage.setItem(TOKEN_STORAGE_KEY, data.token);
        return data.token;
      }
    }
  } catch (err) {
    console.warn('[apiClient] 本机自动握手令牌失败:', err);
  }

  return '';
}

/**
 * 手动更新访问令牌
 */
export function setAccessToken(token: string): void {
  cachedToken = token;
  if (typeof window !== 'undefined') {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
  }
}

/**
 * 封装带鉴权头的 fetch 请求
 */
export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const token = await getAccessToken();

  const headers = new Headers(init?.headers || {});
  if (token && !headers.has('X-Access-Token')) {
    headers.set('X-Access-Token', token);
  }

  return fetch(input, {
    ...init,
    headers,
  });
}

/**
 * 在 Web / Docker 远程环境下通过带鉴权头的 fetch 下载工作区文件到本地浏览器
 */
export async function downloadWorkspaceFile(
  workspaceId: string,
  filePath: string,
  fileName?: string
): Promise<boolean> {
  if (typeof window === 'undefined' || !filePath) return false;
  try {
    const qs = new URLSearchParams({
      workspace_id: workspaceId || 'default',
      file_path: filePath,
    });
    const res = await apiFetch(`/api/v1/artifacts/raw-by-path?${qs.toString()}`);
    if (!res.ok) return false;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const fallbackName = filePath.split(/[\\/]/).filter(Boolean).pop() || 'download';
    a.download = fileName || fallbackName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    return true;
  } catch (err) {
    console.warn('[apiClient] 远程下载工作区文件失败:', err);
    return false;
  }
}

