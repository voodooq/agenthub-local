export type AttachmentCategory = 'image' | 'pdf' | 'office' | 'text';

export interface ValidatedAttachmentMeta {
  category: AttachmentCategory;
  ext: string;
  maxBytes: number;
  limitLabel: string;
  canonicalMime: string;
}

export interface AttachmentValidationResult {
  valid: boolean;
  reason?: string;
  meta?: ValidatedAttachmentMeta;
}

export interface ParsedUserAttachment {
  name: string;
  file_path?: string;
  rel_path?: string;
  url?: string;
  mime_type?: string;
  size_str?: string;
  category?: string;
}

export const MAX_ATTACHMENTS_PER_MESSAGE = 5;
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
export const MAX_PDF_BYTES = 10 * 1024 * 1024;
export const MAX_OFFICE_BYTES = 5 * 1024 * 1024;
export const MAX_TEXT_BYTES = 5 * 1024 * 1024;

export const IMAGE_EXTENSIONS: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
};

export const PDF_EXTENSIONS: Record<string, string> = {
  '.pdf': 'application/pdf',
};

export const OFFICE_EXTENSIONS: Record<string, string> = {
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
};

export const TEXT_EXTENSIONS: Record<string, string> = {
  '.txt': 'text/plain',
  '.md': 'text/markdown',
  '.markdown': 'text/markdown',
  '.log': 'text/plain',
  '.csv': 'text/csv',
  '.tsv': 'text/tab-separated-values',
  '.json': 'application/json',
  '.jsonl': 'application/x-ndjson',
  '.xml': 'application/xml',
  '.yaml': 'application/yaml',
  '.yml': 'application/yaml',
  '.toml': 'application/toml',
  '.ini': 'text/plain',
  '.cfg': 'text/plain',
  '.conf': 'text/plain',
  '.svg': 'image/svg+xml',
  '.py': 'text/x-python',
  '.js': 'text/javascript',
  '.jsx': 'text/jsx',
  '.ts': 'text/typescript',
  '.tsx': 'text/tsx',
  '.mjs': 'text/javascript',
  '.cjs': 'text/javascript',
  '.html': 'text/html',
  '.htm': 'text/html',
  '.css': 'text/css',
  '.scss': 'text/x-scss',
  '.less': 'text/x-less',
  '.sql': 'application/sql',
  '.sh': 'text/x-shellscript',
  '.bash': 'text/x-shellscript',
  '.ps1': 'text/plain',
  '.bat': 'text/plain',
  '.cmd': 'text/plain',
  '.java': 'text/x-java-source',
  '.go': 'text/x-go',
  '.rs': 'text/rust',
  '.c': 'text/x-c',
  '.cpp': 'text/x-c++',
  '.h': 'text/x-c',
  '.hpp': 'text/x-c++',
  '.cs': 'text/plain',
  '.php': 'text/x-php',
  '.rb': 'text/x-ruby',
  '.vue': 'text/plain',
  '.svelte': 'text/plain',
};

const SPECIAL_TEXT_FILENAMES = new Set(['dockerfile', 'makefile']);
const LEGACY_OFFICE_EXTS = new Set(['.doc', '.xls', '.ppt']);
const ARCHIVE_EXTS = new Set(['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.tgz']);
const EXECUTABLE_EXTS = new Set(['.exe', '.msi', '.dll', '.so', '.dylib', '.apk', '.dmg', '.iso', '.app', '.com']);
const UNSUPPORTED_IMAGE_EXTS = new Set(['.bmp', '.tiff', '.tif', '.ico', '.heic', '.heif', '.avif', '.raw', '.psd', '.ai', '.sketch', '.fig']);
const MEDIA_BINARY_EXTS = new Set(['.mp4', '.mov', '.avi', '.mkv', '.webm', '.wmv', '.mp3', '.wav', '.flac', '.aac', '.ogg', '.db', '.sqlite', '.sqlite3', '.bin', '.dat', '.pyc']);

export const FILE_INPUT_ACCEPT = [
  ...Object.keys(IMAGE_EXTENSIONS),
  ...Object.keys(PDF_EXTENSIONS),
  ...Object.keys(OFFICE_EXTENSIONS),
  ...Object.keys(TEXT_EXTENSIONS),
].join(',');

export const SUPPORTED_ATTACHMENT_GROUPS = [
  {
    title: '常见图片 (≤ 10 MB)',
    items: 'PNG / JPG / JPEG / WebP / GIF（支持 Ctrl+V 粘贴截图）',
  },
  {
    title: '办公文档 (PDF ≤ 10 MB · Office ≤ 5 MB)',
    items: 'PDF / Word (.docx) / Excel (.xlsx) / PPT (.pptx)',
  },
  {
    title: '纯文本与源码 (≤ 5 MB)',
    items: 'TXT / MD / CSV / JSON / YAML / XML / LOG / SVG / Python / JS / TS / SQL 等常见源码',
  },
];

function extractExtension(fileName: string): string {
  const clean = (fileName || '').trim();
  const dotIdx = clean.lastIndexOf('.');
  if (dotIdx <= 0) return '';
  return clean.slice(dotIdx).toLowerCase();
}

/**
 * 格式化可读文件大小
 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(2)} MB`;
}

/**
 * 根据文件名与大小执行前置合规检查，输出统一中文拦截说明
 */
export function validateAttachmentCandidate(file: File): AttachmentValidationResult {
  const name = (file.name || '').trim() || '未命名文件';
  const ext = extractExtension(name);
  const lowerName = name.toLowerCase();

  let category: AttachmentCategory | null = null;
  let canonicalMime = '';
  let maxBytes = MAX_TEXT_BYTES;
  let limitLabel = '文本/代码文件 (5 MB)';

  if (ext in IMAGE_EXTENSIONS) {
    category = 'image';
    canonicalMime = IMAGE_EXTENSIONS[ext];
    maxBytes = MAX_IMAGE_BYTES;
    limitLabel = '图片 (10 MB)';
  } else if (ext in PDF_EXTENSIONS) {
    category = 'pdf';
    canonicalMime = PDF_EXTENSIONS[ext];
    maxBytes = MAX_PDF_BYTES;
    limitLabel = 'PDF 文档 (10 MB)';
  } else if (ext in OFFICE_EXTENSIONS) {
    category = 'office';
    canonicalMime = OFFICE_EXTENSIONS[ext];
    maxBytes = MAX_OFFICE_BYTES;
    limitLabel = 'Office 文档 (5 MB)';
  } else if (ext in TEXT_EXTENSIONS || (!ext && SPECIAL_TEXT_FILENAMES.has(lowerName))) {
    category = 'text';
    canonicalMime = TEXT_EXTENSIONS[ext] || 'text/plain';
    maxBytes = MAX_TEXT_BYTES;
    limitLabel = '文本/代码文件 (5 MB)';
  }

  if (!category) {
    const extDisp = ext || '无扩展名';
    if (LEGACY_OFFICE_EXTS.has(ext)) {
      return {
        valid: false,
        reason: `「${name}」为老式二进制办公格式 (${extDisp})，暂不支持直接解析。请另存为 .docx / .xlsx / .pptx 或 .pdf 后再上传。`,
      };
    }
    if (ARCHIVE_EXTS.has(ext)) {
      return {
        valid: false,
        reason: `「${name}」为压缩归档包 (${extDisp})，对话附件暂不支持直接解析，请先解压后上传具体文件，或使用左侧「挂载本地工作区」。`,
      };
    }
    if (EXECUTABLE_EXTS.has(ext)) {
      return {
        valid: false,
        reason: `出于本地安全防护考虑，禁止上传可执行程序或系统安装包 (${extDisp})。`,
      };
    }
    if (UNSUPPORTED_IMAGE_EXTS.has(ext)) {
      return {
        valid: false,
        reason: `图片「${name}」(${extDisp}) 暂不受视觉引擎支持，请转换为 PNG / JPG / WebP / GIF 后再上传。`,
      };
    }
    if (MEDIA_BINARY_EXTS.has(ext)) {
      return {
        valid: false,
        reason: `文件「${name}」(${extDisp}) 属于音视频或大型二进制/数据库文件，当前对话附件暂不支持解析。`,
      };
    }
    return {
      valid: false,
      reason: `不支持「${name}」的文件格式 (${extDisp})。当前仅支持常见图片、PDF/Word/Excel/PPT 与纯文本/源码文件。`,
    };
  }

  if (file.size === 0) {
    return {
      valid: false,
      reason: `文件「${name}」内容为空（0 字节），无法上传。`,
    };
  }

  if (file.size > maxBytes) {
    const actualMb = (file.size / (1024 * 1024)).toFixed(2);
    return {
      valid: false,
      reason: `文件「${name}」大小为 ${actualMb} MB，超出 ${limitLabel} 上限。`,
    };
  }

  return {
    valid: true,
    meta: {
      category,
      ext,
      maxBytes,
      limitLabel,
      canonicalMime: file.type || canonicalMime,
    },
  };
}

const ATTACHMENT_ANCHOR_REGEX = /\n*<agenthub_attachments>([\s\S]*?)<\/agenthub_attachments>\s*$/i;

/**
 * 从用户消息正文中解析出附件元数据标签，返回纯净正文与结构化附件卡片数组
 */
export function parseMessageAttachments(rawContent: string): {
  cleanContent: string;
  parsedAttachments: ParsedUserAttachment[];
} {
  const source = rawContent || '';
  const match = ATTACHMENT_ANCHOR_REGEX.exec(source);
  if (!match) {
    return { cleanContent: source, parsedAttachments: [] };
  }

  const cleanContent = source.replace(ATTACHMENT_ANCHOR_REGEX, '').trim();
  try {
    const parsed = JSON.parse(match[1]);
    if (Array.isArray(parsed)) {
      return {
        cleanContent,
        parsedAttachments: parsed.filter((item) => item && typeof item.name === 'string'),
      };
    }
  } catch {
    // 忽略格式异常的尾部锚点，避免影响文本正文渲染
  }
  return { cleanContent, parsedAttachments: [] };
}

/**
 * 将附件元信息封装为与后端一致的尾部锚点标签，确保发送瞬间与刷新重载后渲染完全一致
 */
export function buildMessageWithAttachmentsAnchor(
  text: string,
  items: ParsedUserAttachment[]
): string {
  const cleanText = (text || '').trim();
  if (!items || items.length === 0) return cleanText;
  return `${cleanText}\n\n<agenthub_attachments>${JSON.stringify(items)}</agenthub_attachments>`.trim();
}

